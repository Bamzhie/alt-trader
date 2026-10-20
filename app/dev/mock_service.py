"""Stdlib mock service for the ALT RADAR dashboard frontend (DEV ONLY).

Serves the HTTP+SSE contract from
docs/superpowers/specs/2026-10-14-tauri-desktop-design.md §1 with static
fixtures, so the React app can be built and tested before the real
`proto/service.py` exists. When that lands, it drops in behind the identical
client with zero frontend changes.

HARD RULES (enforced here, mirroring the real service):
  * READ-ONLY. There is no order path, no keys, no execution code. POST exists
    only for the four idempotent watcher commands.
  * No confidence / probability / tier field is ever produced.
  * No 1m timeframe — 5m/15m/1H/4H/1D only; anything else is BAD_REQUEST.
  * No TOTAL row in market overview; no leverage or liquidation field anywhere.
  * Timestamps are UTC-seconds ints. Envelope is {ok, data, error:{code,message}}.
    Unavailable is an explicit null, never a zero and never a guess.
  * Binds 127.0.0.1 only. The bearer token is printed once to stdout and never
    written to the request log.

The shadow research routes (/quant-status, /quant-research) are READ-ONLY:
counts with their denominators, interval endpoints only, and an explicit
"insufficient" with a reason when the store is empty.

Dev-only affordances (clearly labelled, exempt from auth):
  GET /dev/toggle-ready      flip the readiness flag (exercises the
                             alive-but-not-ready "first sweep" state)
  GET /dev/inject-event?kind= push a transition into the SSE stream mid-session

Run:  python3 app/dev/mock_service.py --port 18741
      (or `bash app/dev/serve.sh`; PORT env var overrides)
"""

import argparse
import collections
import hmac
import json
import os
import queue
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST_DEFAULT = "127.0.0.1"
PORT_DEFAULT = 18741
TOKEN_DEFAULT = "dev-token"

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

SUPPORTED_TIMEFRAMES = ["5m", "15m", "1H", "4H", "1D"]
TIMEFRAME_SECONDS = {"5m": 300, "15m": 900, "1H": 3600, "4H": 14400, "1D": 86400}

# SSE ring buffer: last 500 events or 30 minutes, whichever is smaller.
RING_MAX_EVENTS = 500
RING_WINDOW_SECONDS = 30 * 60

# Event ids are fixed-width zero-padded so lexicographic compare == numeric
# compare. The frontend drops events whose id is <= snapshot_event_id.
EVENT_ID_WIDTH = 6

EVENT_KINDS = [
    "setup.emitted",
    "setup.invalidated",
    "setup.expired",
    "setup.resolved",
    "monitoring.unavailable",
    "monitoring.ok",
    "sweep.completed",
]

# Setup lifecycle states accepted by /setups?state=
SETUP_STATES = ["active", "emitted", "expired", "invalidated", "resolved"]

# Trigger rules the commands endpoint knows about (the watcher's rule set).
TRIGGER_RULES = [
    "range_reclaim_long",
    "range_reject_short",
    "breakout_continuation_long",
]

COMMANDS = ["pause", "resume", "disable-trigger", "enable-trigger"]

# Error codes, matching app/src/api/types.ts ErrorCode.
UNAUTH = "UNAUTH"
BAD_REQUEST = "BAD_REQUEST"
NOT_FOUND = "NOT_FOUND"
UNAVAILABLE = "UNAVAILABLE"
RATE_LIMITED = "RATE_LIMITED"

HEARTBEAT_SECONDS = 15

UNAUTH_MESSAGE = "missing or invalid bearer token"
NOT_READY_MESSAGE = "not ready — first sweep in progress"

# The shadow research surface, as an empty store answers it: an explicit
# "insufficient" with a reason and null/zero denominators — never a guess
# and never a figure the store cannot back.
EMPTY_QUANT_STATUS = {
    "mode": "shadow",
    "status": "unseen",
    "paused": False,
    "pause_reason": None,
    "config_hash": None,
    "event_counts": {"total": 0, "emitted": 0, "resolved": 0},
    "actionable_events": 0,
    "suppression_top": [],
    "promotions": [],
    "reason": "insufficient-evidence",
}

EMPTY_QUANT_RESEARCH = {
    "version": "research-surface-v1",
    "status": "insufficient",
    "reasons": ["no shadow events recorded yet"],
    "generated_ts": 0,
    "provenance": {
        "config_hash": None,
        "code_rev": None,
        "rule_ids": [],
        "feature_versions": [],
    },
    "sample": {
        "events": 0, "resolved": 0, "open": 0,
        "days": 0, "coins": 0, "rules": 0,
    },
    "rules": [],
    "calibration": {
        "available": False,
        "buckets": [],
        "count": 0,
        "reason": ("the shadow store records outcomes, not predicted outcomes "
                   "- calibration is not computed"),
    },
    "coverage": {
        "attributed": 0, "eligible": 0, "ratio": None,
        "reason": "no recorded opportunities yet - coverage is not computable",
    },
    "validation": {"validated": 0, "rules": 0},
}


# ---------------------------------------------------------------------------
# Service state
# ---------------------------------------------------------------------------


class MockService:
    """All mutable state behind the routes. One instance per server."""

    def __init__(self, fixtures, token):
        self.fixtures = fixtures
        self.token = token
        self.ready = True
        self.lock = threading.RLock()

        # Rolling SSE history. Entries are dicts:
        #   {"id": "000001", "kind": ..., "ts": int, "coin": str|None, ...}
        self.events = collections.deque()
        self._next_seq = 1

        # Live SSE subscribers: one bounded queue each, fed on append.
        self._subscribers = []

        # Idempotent command state.
        self.paused = False
        self.disabled_triggers = set()

        # Request log — METHOD + path + status only. Never headers, never the
        # Authorization value (there is nothing in here that could leak it).
        self.request_log = collections.deque(maxlen=200)

        meta = fixtures.get("meta", {})
        self.data_as_of = int(meta.get("data_as_of", 0))
        self._load_seed_events()

    # -- events ------------------------------------------------------------

    def _load_seed_events(self):
        for seed in self.fixtures.get("seed_events", []):
            self._append_event(
                seed["kind"], int(seed.get("ts", self.data_as_of)), seed.get("coin")
            )

    def _append_event(self, kind, ts, coin):
        with self.lock:
            seq = self._next_seq
            self._next_seq += 1
            event = {
                "id": f"{seq:0{EVENT_ID_WIDTH}d}",
                "kind": kind,
                "ts": int(ts),
                "coin": coin,
            }
            self.events.append(event)
            self._prune_ring()
            subscribers = list(self._subscribers)
        for sub in subscribers:
            try:
                sub.put_nowait(event)
            except queue.Full:
                pass
        return event

    def _prune_ring(self):
        """Cap at 500 events and a true 30-minute window, filtered by ts
        (not insertion order)."""
        while len(self.events) > RING_MAX_EVENTS:
            self.events.popleft()
        if not self.events:
            return
        newest = max(e["ts"] for e in self.events)
        floor = newest - RING_WINDOW_SECONDS
        kept = collections.deque(e for e in self.events if e["ts"] >= floor)
        self.events = kept

    def inject_event(self, kind, coin=None):
        if kind not in EVENT_KINDS:
            return None
        with self.lock:
            # The mock clock is frozen at the fixture's data_as_of; injected
            # events advance from the fixture clock only. Using the host
            # time.time() would jump ahead of data_as_of once the fixture date
            # is in the past, and the 30-minute prune would then evict the
            # entire seeded buffer.
            base = self.events[-1]["ts"] if self.events else self.data_as_of
            ts = base + 1
            return self._append_event(kind, ts, coin)

    def ring_floor(self):
        with self.lock:
            return self.events[0]["id"] if self.events else None

    def head_event_id(self):
        with self.lock:
            return self.events[-1]["id"] if self.events else None

    def subscribe(self):
        q = queue.Queue(maxsize=256)
        with self.lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    # -- commands ----------------------------------------------------------

    def run_command(self, name, body):
        """Returns (ok, payload, error_code, message). Never raises."""
        if name == "pause":
            self.paused = True
            return True, {"command": name, "idempotent": True, "paused": True}, None, None
        if name == "resume":
            self.paused = False
            return True, {"command": name, "idempotent": True, "paused": False}, None, None
        if name in ("disable-trigger", "enable-trigger"):
            rule = (body or {}).get("rule")
            if not isinstance(rule, str) or not rule:
                return False, None, BAD_REQUEST, "missing 'rule' in request body"
            if rule not in TRIGGER_RULES:
                return (
                    False,
                    None,
                    BAD_REQUEST,
                    "unknown trigger rule; known: " + ", ".join(TRIGGER_RULES),
                )
            if name == "disable-trigger":
                self.disabled_triggers.add(rule)
                enabled = False
            else:
                self.disabled_triggers.discard(rule)
                enabled = True
            return (
                True,
                {
                    "command": name,
                    "rule": rule,
                    "idempotent": True,
                    "enabled": enabled,
                    "disabled_triggers": sorted(self.disabled_triggers),
                },
                None,
                None,
            )
        return False, None, NOT_FOUND, "unknown command"


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class MockHandler(BaseHTTPRequestHandler):
    server_version = "AltRadarMock/1.0"
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------------

    @property
    def svc(self) -> MockService:
        return self.server.service  # type: ignore[attr-defined]

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        """Route access logging through the service log.

        Only the request line (METHOD path HTTP/x.y) and status are recorded —
        BaseHTTPRequestHandler never logs headers, and we keep it that way so
        the Authorization value cannot reach disk.
        """
        try:
            self.svc.request_log.append(f"{self.address_string()} {format % args}")
        except Exception:
            pass
        sys.stderr.write(f"{self.address_string()} - {format % args}\n")

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def respond(self, status, data):
        self._send_json(status, {"ok": True, "data": data, "error": None})

    def fail(self, status, code, message):
        self._send_json(
            status, {"ok": False, "data": None, "error": {"code": code, "message": message}}
        )

    def _query(self):
        return urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

    def _param(self, params, name, default=None):
        values = params.get(name)
        return values[0] if values else default

    # -- auth --------------------------------------------------------------

    def _authorized(self):
        header = self.headers.get("Authorization", "")
        scheme, _, presented = header.partition(" ")
        expected = "Bearer " + self.svc.token
        # compare_digest on the whole "Bearer <token>" string so a missing or
        # malformed scheme fails the same way as a wrong token (no oracle).
        # compare_digest raises TypeError on non-ASCII input — a bad header
        # must be a 401, never an unhandled exception that drops the socket.
        try:
            digest_ok = hmac.compare_digest(header.strip(), expected)
        except TypeError:
            return False
        if not digest_ok:
            return False
        if scheme.strip().lower() != "bearer":
            return False
        return True

    def _require_auth(self):
        if not self._authorized():
            self.fail(401, UNAUTH, UNAUTH_MESSAGE)
            return False
        return True

    # -- verbs -------------------------------------------------------------

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        params = self._query()

        if path == "/healthz":
            # Liveness only — deliberately NOT the envelope, and auth-free.
            self._send_json(200, {"ok": True, "alive": True})
            return
        if path.startswith("/dev/"):
            self._dev_route(path, params)
            return
        if path == "/events":
            self._events_route()
            return
        if not self._require_auth():
            return

        if path == "/readyz":
            if self.svc.ready:
                self.respond(200, {"ready": True, "data_as_of": self.svc.data_as_of})
            else:
                self.fail(503, UNAVAILABLE, NOT_READY_MESSAGE)
            return
        if path == "/snapshot":
            self.respond(200, self._snapshot())
            return
        if path == "/setups":
            self._setups_route(params)
            return
        if path.startswith("/setup/"):
            self._setup_route(path[len("/setup/"):])
            return
        if path == "/bars":
            self._bars_route(params)
            return
        if path == "/metrics":
            self._metrics_route(params)
            return
        if path.startswith("/tradeplan/"):
            self._tradeplan_route(path[len("/tradeplan/"):])
            return
        if path == "/quant-status":
            self._quant_status_route()
            return
        if path == "/quant-research":
            self._quant_research_route()
            return
        self.fail(404, NOT_FOUND, f"no route for {path}")

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/dev/"):
            self._dev_route(path, self._query())
            return
        if not self._require_auth():
            return
        if path.startswith("/commands/"):
            self._command_route(path[len("/commands/"):])
            return
        self.fail(404, NOT_FOUND, f"no route for {path}")

    # -- route bodies ------------------------------------------------------

    def _snapshot(self):
        fx = self.svc.fixtures
        setups = list(fx.get("setups", []))
        snapshots = list(fx.get("snapshots", []))
        latest = snapshots[0] if snapshots else {}
        data_as_of = int(latest.get("ts", self.svc.data_as_of))

        # The 24h comparison point is the nearest snapshot at or before
        # now-24h. If the history does not reach back that far, the delta is
        # reported unavailable — never inferred from the nearest row.
        cutoff = data_as_of - 86400
        prior = None
        for row in snapshots[1:]:
            if int(row["ts"]) <= cutoff:
                prior = row
                break
        if prior is None:
            comparison = {
                "available": False,
                "reason": (
                    "no dashboard snapshot at or before now-24h — "
                    "comparison unavailable, not inferred"
                ),
                "prior_ts": None,
                "delta_active_setups": None,
                "delta_new_setups_24h": None,
            }
        else:
            comparison = {
                "available": True,
                "reason": None,
                "prior_ts": int(prior["ts"]),
                "delta_active_setups": int(latest.get("active_setups", 0))
                - int(prior.get("active_setups", 0)),
                "delta_new_setups_24h": int(latest.get("new_setups_24h", 0))
                - int(prior.get("new_setups_24h", 0)),
            }

        history = []
        for row in snapshots:
            history.append(
                {
                    "ts": int(row["ts"]),
                    "eligible_count": int(row["eligible_count"]),
                    "evaluated_count": int(row["evaluated_count"]),
                    "active_setups": int(row["active_setups"]),
                    "new_setups_24h": int(row["new_setups_24h"]),
                    "monitoring_coverage": row["monitoring_coverage"],
                    # True only for rows that can serve as a 24h comparison point.
                    "covers_24h": int(row["ts"]) <= cutoff,
                }
            )

        overview = []
        for row in fx.get("market_overview", [])[:2]:
            overview.append(dict(row))
        # Fall back to the movers list if the fixture has no explicit overview.
        if not overview:
            for row in fx.get("movers", [])[:2]:
                overview.append(
                    {
                        "coin": row["coin"],
                        "name": row["name"],
                        "price": None,
                        "change_24h_pct": row["change_24h_pct"],
                        "last_bar_ts": None,
                    }
                )

        plans_by_coin = {p["coin"]: p["id"] for p in fx.get("tradeplans", [])}
        enriched = []
        for s in setups:
            row = dict(s)
            row["name"] = (fx.get("coins") or {}).get(s["coin"], {}).get(
                "name", s["coin"]
            )
            row["tradeplan_id"] = plans_by_coin.get(s["coin"])
            enriched.append(row)

        return {
            "snapshot_event_id": self.svc.head_event_id(),
            "ts": data_as_of,
            "data_as_of": data_as_of,
            "eligible_count": int(latest.get("eligible_count", 0)),
            "evaluated_count": int(latest.get("evaluated_count", 0)),
            "active_setups": int(latest.get("active_setups", len(setups))),
            "monitoring_coverage": latest.get("monitoring_coverage"),
            "new_setups_24h": int(latest.get("new_setups_24h", 0)),
            "setups": enriched,
            "comparison_24h": comparison,
            "snapshot_history": history,
            "coins_without_setup": list(fx.get("coins_without_setup", [])),
            "market_overview": overview,
            "movers": list(fx.get("movers", [])),
            "recent_analysis": list(fx.get("recent_analysis", [])),
            "activity": list(fx.get("activity", [])),
            "supported_timeframes": list(SUPPORTED_TIMEFRAMES),
            "event_kinds": list(EVENT_KINDS),
            # Section-level unavailability. null = everything answered.
            "unavailable": None,
        }

    def _setups_route(self, params):
        state = self._param(params, "state")
        if state is not None and state not in SETUP_STATES:
            self.fail(
                400,
                BAD_REQUEST,
                "unknown state; known: " + ", ".join(SETUP_STATES),
            )
            return

        limit_raw = self._param(params, "limit")
        if limit_raw is None:
            limit = 100
        else:
            try:
                limit = int(limit_raw)
            except ValueError:
                self.fail(400, BAD_REQUEST, "limit must be an integer")
                return
            if limit < 1:
                self.fail(400, BAD_REQUEST, "limit must be >= 1")
                return
            limit = min(limit, 500)  # hard max

        cursor_raw = self._param(params, "cursor")
        offset = 0
        if cursor_raw is not None:
            if not cursor_raw.startswith("o") or not cursor_raw[1:].isdigit():
                self.fail(400, BAD_REQUEST, "malformed cursor")
                return
            offset = int(cursor_raw[1:])

        rows = list(self.svc.fixtures.get("setups", []))
        if state is not None:
            rows = [r for r in rows if r.get("state") == state]
        page = rows[offset : offset + limit]
        next_offset = offset + len(page)
        next_cursor = f"o{next_offset}" if next_offset < len(rows) else None

        plans_by_coin = {p["coin"]: p["id"] for p in self.svc.fixtures.get("tradeplans", [])}
        out = []
        for s in page:
            row = dict(s)
            row["tradeplan_id"] = plans_by_coin.get(s["coin"])
            out.append(row)
        self.respond(
            200,
            {
                "setups": out,
                "next_cursor": next_cursor,
                "limit": limit,
                "state": state,
                "total_returned": len(out),
            },
        )

    def _find_setup(self, key):
        for s in self.svc.fixtures.get("setups", []):
            if key in (s.get("id"), s.get("coin")):
                return s
        return None

    def _setup_route(self, key):
        setup = self._find_setup(key)
        if setup is None:
            self.fail(404, NOT_FOUND, f"no setup for {key!r}")
            return
        row = dict(setup)
        plans = {
            p["coin"]: p["id"] for p in self.svc.fixtures.get("tradeplans", [])
        }
        row["tradeplan_id"] = plans.get(setup["coin"])
        self.respond(200, row)

    def _bars_route(self, params):
        coin = self._param(params, "coin")
        tf = self._param(params, "tf")
        if not coin:
            self.fail(400, BAD_REQUEST, "missing required query parameter 'coin'")
            return
        if not tf:
            self.fail(400, BAD_REQUEST, "missing required query parameter 'tf'")
            return
        if tf not in SUPPORTED_TIMEFRAMES:
            self.fail(
                400,
                BAD_REQUEST,
                f"unsupported timeframe {tf!r}; supported: "
                + ", ".join(SUPPORTED_TIMEFRAMES),
            )
            return
        series = self.svc.fixtures.get("bars", {}).get(coin)
        if series is None or tf not in series:
            self.fail(404, NOT_FOUND, f"no bars for coin {coin!r}")
            return
        bars = list(series[tf])
        self.respond(
            200,
            {
                "coin": coin,
                "tf": tf,
                "bars": bars,
                "closed_only": True,
                "count": len(bars),
                "data_as_of": self.svc.data_as_of,
                # null = the feed answered; a reason string = section-level
                # unavailability. Never a zero-filled array.
                "unavailable": None,
            },
        )

    def _metrics_route(self, params):
        coin = self._param(params, "coin")
        if not coin:
            self.fail(400, BAD_REQUEST, "missing required query parameter 'coin'")
            return
        metrics = self.svc.fixtures.get("metrics", {}).get(coin)
        if metrics is None:
            self.fail(404, NOT_FOUND, f"no metrics for coin {coin!r}")
            return
        self.respond(200, dict(metrics))

    def _find_tradeplan(self, key):
        for p in self.svc.fixtures.get("tradeplans", []):
            if key in (p.get("id"), p.get("coin")):
                return p
        return None

    def _tradeplan_route(self, key):
        plan = self._find_tradeplan(key)
        if plan is None:
            self.fail(404, NOT_FOUND, f"no trade plan for {key!r}")
            return
        row = dict(plan)
        # Defence in depth: strip anything the contract forbids even if a
        # fixture ever grows one.
        for forbidden in ("leverage", "max_leverage", "liquidation", "liq_price"):
            row.pop(forbidden, None)
        self.respond(200, row)

    def _quant_status_route(self):
        """The shadow-watcher status (read-only, counts only)."""
        data = self.svc.fixtures.get("quant_status")
        if not isinstance(data, dict):
            data = EMPTY_QUANT_STATUS
        self.respond(200, dict(data))

    def _quant_research_route(self):
        """The research surface: counts with denominators, interval
        endpoints only, status and provenance. Read-only."""
        data = self.svc.fixtures.get("quant_research")
        if not isinstance(data, dict):
            data = EMPTY_QUANT_RESEARCH
        self.respond(200, dict(data))

    def _command_route(self, name):
        if name not in COMMANDS:
            self.fail(
                404,
                NOT_FOUND,
                f"unknown command {name!r}; known: " + ", ".join(COMMANDS),
            )
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except ValueError:
            self.fail(400, BAD_REQUEST, "request body must be a JSON object")
            return
        if not isinstance(body, dict):
            self.fail(400, BAD_REQUEST, "request body must be a JSON object")
            return
        ok, payload, code, message = self.svc.run_command(name, body)
        if ok:
            self.respond(200, payload)
        else:
            self.fail(400, code, message)

    # -- SSE ---------------------------------------------------------------

    def _sse_write(self, text):
        self.wfile.write(text.encode())
        self.wfile.flush()

    def _frame(self, event):
        payload = json.dumps(
            {
                "id": event["id"],
                "kind": event["kind"],
                "ts": event["ts"],
                "coin": event["coin"],
            }
        )
        return f"id: {event['id']}\nevent: {event['kind']}\ndata: {payload}\n\n"

    def _events_route(self):
        if not self._require_auth():
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        svc = self.svc
        cursor = self.headers.get("Last-Event-ID")
        if cursor is not None:
            cursor = cursor.strip()
        replay_from = 0
        # Subscribe BEFORE copying the ring. Copying first and subscribing
        # afterwards opens a window where an event appended in between lands
        # in neither the replay nor the subscriber queue. Any event the queue
        # delivers that was already replayed from the snapshot is skipped
        # below by id.
        q = svc.subscribe()
        try:
            with svc.lock:
                snapshot = list(svc.events)
                floor = svc.events[0]["id"] if svc.events else None
            if cursor:
                if not cursor.isdigit():
                    replay_from = 0
                    floor = None  # unparseable cursor behaves like a stale one
                else:
                    replay_from = int(cursor)
                    if floor is not None and cursor < floor:
                        # Below the ring-buffer floor: the client cannot
                        # reconcile from replay, so it must re-snapshot.
                        try:
                            self._sse_write(
                                "id: " + floor + "\nevent: resync\ndata: "
                                + json.dumps(
                                    {
                                        "id": floor,
                                        "kind": "resync",
                                        "ts": svc.data_as_of,
                                        "coin": None,
                                        "reason": "cursor below ring-buffer floor — re-snapshot",
                                    }
                                )
                                + "\n\n"
                            )
                        except (BrokenPipeError, ConnectionResetError):
                            return
            last_replayed = -1
            for event in snapshot:
                if int(event["id"]) > replay_from:
                    self._sse_write(self._frame(event))
                    last_replayed = max(last_replayed, int(event["id"]))
            while True:
                try:
                    event = q.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    try:
                        self._sse_write(f": keepalive {svc.data_as_of}\n\n")
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    continue
                # Skip anything the snapshot already covered.
                if int(event["id"]) <= last_replayed:
                    continue
                self._sse_write(self._frame(event))
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            svc.unsubscribe(q)

    # -- dev-only ----------------------------------------------------------

    def _dev_route(self, path, params):
        """DEV-ONLY. Exempt from auth on purpose so the dashboard's starting
        state can be exercised without a token in a script."""
        if path == "/dev/toggle-ready":
            self.svc.ready = not self.svc.ready
            self._send_json(200, {"ok": True, "dev": True, "ready": self.svc.ready})
            return
        if path == "/dev/inject-event":
            kind = self._param(params, "kind")
            if not kind:
                self.fail(400, BAD_REQUEST, "missing required query parameter 'kind'")
                return
            if kind not in EVENT_KINDS:
                self.fail(
                    400,
                    BAD_REQUEST,
                    f"unknown kind {kind!r}; known: " + ", ".join(EVENT_KINDS),
                )
                return
            coin = self._param(params, "coin")
            event = self.svc.inject_event(kind, coin)
            self.respond(200, dict(event))
            return
        self.fail(404, NOT_FOUND, f"no dev route for {path}")


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


def load_fixtures(path):
    with open(path) as f:
        return json.load(f)


def build_server(host, port, token, fixtures_path):
    fixtures = load_fixtures(fixtures_path)
    service = MockService(fixtures, token)
    server = ThreadingHTTPServer((host, port), MockHandler)
    server.daemon_threads = True
    server.service = service
    return server


def start_server(host=HOST_DEFAULT, port=PORT_DEFAULT, token=TOKEN_DEFAULT,
                 fixtures_path=None):
    """Build and START the server in a background thread.

    Used by tests (and by anything embedding the mock). Returns the server
    object; call `.shutdown()` / `.server_close()` to stop it.
    """
    if fixtures_path is None:
        fixtures_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "fixtures.json")
    server = build_server(host, port, token, fixtures_path)
    thread = threading.Thread(target=server.serve_forever, name="mock-service",
                              daemon=True)
    thread.start()
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="ALT RADAR mock service (dev only)")
    parser.add_argument("--host", default=HOST_DEFAULT,
                        help="bind address (loopback only)")
    parser.add_argument("--port", type=int, default=PORT_DEFAULT)
    parser.add_argument("--token", default=TOKEN_DEFAULT)
    parser.add_argument("--fixtures", default=None)
    args = parser.parse_args(argv)

    fixtures_path = args.fixtures or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "fixtures.json"
    )
    server = build_server(args.host, args.port, args.token, fixtures_path)
    # Printed once, to stdout, for a human/launcher to capture. Never logged
    # per-request and never written to a file by this process.
    print(f"dev token: {args.token}")
    print(f"mock service listening on http://{args.host}:{args.port} "
          f"(DEV ONLY — fixtures, not live data)")
    print("dev affordances: /dev/toggle-ready, /dev/inject-event?kind=")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
