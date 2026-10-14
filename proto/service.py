"""Headless read-only service: stdlib HTTP server with token auth.

Task 1 skeleton: loopback-only bind, bearer-token auth (all routes except
``GET /healthz``), Origin/Host allowlist policy, CORS preflight for
allowlisted origins only, and redacted request logging (no ``Authorization``
value ever reaches a log line).

Stdlib only: ``http.server`` + ``threading`` + ``hmac`` + ``secrets``.
Port fallback scan, routes, SSE, commands and daemon embedding arrive in
later tasks; this module owns ``serve`` / ``load_or_create_token`` and the
auth/origin gate every future route inherits.
"""

import hmac
import json
import logging
import os
import queue
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .collector import read_bars
from .planner import REFERENCE_NOTIONAL, reference_plan
from . import watcher as _watcher_mod
from .watcher import CUSUM_RULE_ID, T1_RULE_ID
from .watcher_feed import (
    DISABLED_PREFIX,
    PAUSED_KEY,
    get_flags as _feed_flags,
    set_paused as _feed_set_paused,
    set_rule_disabled as _feed_set_rule_disabled,
)

LOG = logging.getLogger("proto.service")

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Tauri 2 asset origins.
TAURI_ORIGINS = frozenset({"tauri://localhost", "https://tauri.localhost"})

# Dev frontends: http(s) on localhost / 127.0.0.1, any port.
_LOCALHOST_ORIGIN = re.compile(
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$", re.IGNORECASE)


def is_allowlisted_origin(origin):
    """True for the Tauri asset origin or localhost dev origins."""
    if not origin:
        return False
    origin = origin.strip()
    return origin in TAURI_ORIGINS or _LOCALHOST_ORIGIN.match(origin) is not None


def is_loopback_host(host):
    """True when a Host header value targets this machine's loopback."""
    if not host:
        return False
    host = host.strip().lower()
    if host.startswith("["):  # bracketed IPv6, e.g. [::1]:18741
        host = host.split("]", 1)[0][1:]
    else:
        host = host.split(":", 1)[0]
    return host in LOOPBACK_HOSTS


def envelope(ok, data=None, code=None, message=None):
    """Typed envelope shared by all routes: {ok, data, error:{code,message}}."""
    err = None if ok else {"code": code or "ERROR",
                           "message": message or "error"}
    return {"ok": ok, "data": data, "error": err}


class _RedactingFilter(logging.Filter):
    """Scrub bearer secrets that might ride along on a LogRecord."""

    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        lowered = msg.lower()
        if "bearer" in lowered or "authorization" in lowered:
            record.msg = re.sub(r"(?i)bearer\s+[^\s'\"]+", "Bearer [redacted]",
                                msg)
            record.args = ()
        return True


LOG.addFilter(_RedactingFilter())

# -- Task 3: transition-only SSE event bus --------------------------------
# Wire format mirrors app/dev/mock_service.py exactly (the frontend's
# subscribe-then-reconcile contract): zero-padded width-6 monotonic string
# ids, frames `id: / event: / data:`, Last-Event-ID replay, below-floor
# cursor -> `resync` first, 15s `: keepalive` comments, ring keeps the last
# 500 events / 30-minute window. Events enter ONLY via watcher lifecycle
# transition hooks (setup.emitted/invalidated/expired/resolved,
# monitoring.unavailable/ok) plus record_sweep_completed (the scan loop
# calls it once per completed sweep; Task 5 wires the loop).

EVENT_KINDS = ("setup.emitted", "setup.invalidated", "setup.expired",
               "setup.resolved", "monitoring.unavailable", "monitoring.ok",
               "sweep.completed")
EVENT_CAP = 500
EVENT_TTL_S = 30 * 60
EVENT_ID_WIDTH = 6
HEARTBEAT_S = 15
_ZERO_ID = "0" * EVENT_ID_WIDTH

_EVENT_LOCK = threading.RLock()
_EVENTS = deque()
_NEXT_SEQ = 1
_SUBSCRIBERS = []


def _next_event_id():
    global _NEXT_SEQ
    seq = _NEXT_SEQ
    _NEXT_SEQ += 1
    return f"{seq:0{EVENT_ID_WIDTH}d}"


def _prune_ring_locked():
    """Cap at 500 events and a true 30-minute window by event ts (not
    insertion order), relative to the newest event — same as the mock."""
    while len(_EVENTS) > EVENT_CAP:
        _EVENTS.popleft()
    if not _EVENTS:
        return
    newest = max(e["ts"] for e in _EVENTS)
    floor = newest - EVENT_TTL_S
    kept = deque(e for e in _EVENTS if e["ts"] >= floor)
    _EVENTS.clear()
    _EVENTS.extend(kept)


def emit_event(kind, coin=None, setup_id=None, state=None, ts=None):
    """Append one transition event; returns the event dict.

    Raises ValueError on an unknown kind. Live subscriber queues are fed
    on append (bounded; a slow subscriber drops, never blocks).
    """
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown event kind {kind!r}; "
                         f"expected one of {EVENT_KINDS}")
    event = {"id": None, "kind": kind,
             "ts": int(time.time()) if ts is None else int(ts),
             "coin": coin}
    if setup_id is not None:
        event["setup_id"] = setup_id
    if state is not None:
        event["state"] = state
    with _EVENT_LOCK:
        event["id"] = _next_event_id()
        _EVENTS.append(event)
        _prune_ring_locked()
        subs = list(_SUBSCRIBERS)
    for sub in subs:
        try:
            sub.put_nowait(dict(event))
        except queue.Full:
            pass
    return dict(event)


def record_sweep_completed(eligible_count=0, evaluated_count=0):
    """One sweep.completed event per completed universe sweep.

    The scan loop calls this (Task 5 wires it); tests and operators can
    call it directly. Returns the event dict.
    """
    return emit_event("sweep.completed", coin=None)


def reset_events():
    """Clear the ring and restart ids (test isolation only)."""
    global _NEXT_SEQ
    with _EVENT_LOCK:
        _EVENTS.clear()
        _NEXT_SEQ = 1


def head_event_id():
    """Ring head id ("000000" when empty) — the snapshot_event_id value."""
    with _EVENT_LOCK:
        if not _EVENTS:
            return _ZERO_ID
        return _EVENTS[-1]["id"]


def ring_floor():
    """Oldest retained id, or None when the ring is empty."""
    with _EVENT_LOCK:
        return _EVENTS[0]["id"] if _EVENTS else None


def _subscribe():
    q = queue.Queue(maxsize=256)
    with _EVENT_LOCK:
        _SUBSCRIBERS.append(q)
    return q


def _unsubscribe(q):
    with _EVENT_LOCK:
        try:
            _SUBSCRIBERS.remove(q)
        except ValueError:
            pass


def _on_watcher_transition(kind, payload):
    """Watcher hook -> ring buffer. Transition-only by construction: the
    watcher calls this solely at emission, terminal and monitoring-flip
    points, so per-refresh / per-bar traffic never reaches the ring."""
    if kind not in EVENT_KINDS:
        return
    payload = payload or {}
    emit_event(kind, coin=payload.get("coin"),
               setup_id=payload.get("setup_id"),
               state=payload.get("state"))


_watcher_mod.register_transition_listener(_on_watcher_transition)


def _frame_event(event):
    """One SSE frame: `id: / event: / data:` with the {id,kind,ts,coin}
    JSON payload (plus setup_id/state when present)."""
    payload = {"id": event["id"], "kind": event["kind"],
               "ts": event["ts"], "coin": event["coin"]}
    for key in ("setup_id", "state"):
        if key in event:
            payload[key] = event[key]
    return (f"id: {event['id']}\nevent: {event['kind']}\n"
            f"data: {json.dumps(payload)}\n\n")

# -- Task 4: idempotent commands ------------------------------------------
# POST /commands/{pause,resume,disable-trigger,enable-trigger}. Repeated
# identical calls return the same state (no duplication, no toggle); every
# write goes to the SAME meta keys the Qt panels read (watcher_feed
# PAUSED_KEY / DISABLED_PREFIX, via its setters — never re-spelled here),
# so flags survive a process restart (reload-safe via meta persistence).
# Trigger commands take a JSON body {"rule"} and answer 400 BAD_REQUEST on
# a missing or unknown rule; unknown commands answer 404 NOT_FOUND.

COMMANDS = ("pause", "resume", "disable-trigger", "enable-trigger")
MAX_COMMAND_BODY = 1 << 20


def _known_trigger_rules(store_path):
    """Rule ids a trigger command may address: the static watcher rule ids
    plus every rule_id already present in watcher_setup plus every rule
    with a disable key in meta. Unknown anything-else -> 400."""
    known = {T1_RULE_ID, CUSUM_RULE_ID}
    try:
        conn = sqlite3.connect(store_path, timeout=5)
    except sqlite3.Error:
        return known
    try:
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.Error:
            return known
        if "watcher_setup" in tables:
            try:
                for (rule_id,) in conn.execute(
                        "SELECT DISTINCT rule_id FROM watcher_setup"):
                    if rule_id:
                        known.add(str(rule_id))
            except sqlite3.Error:
                pass
        if "meta" in tables:
            try:
                for (key,) in conn.execute(
                        "SELECT key FROM meta WHERE key LIKE ?",
                        (DISABLED_PREFIX + "%",)):
                    suffix = key[len(DISABLED_PREFIX):]
                    if suffix:
                        known.add(suffix)
            except sqlite3.Error:
                pass
    finally:
        conn.close()
    return known


def _command_flags(store_path):
    """(paused, disabled) via the panel read path; DB trouble -> (False, ())."""
    try:
        conn = sqlite3.connect(store_path, timeout=5)
    except sqlite3.Error:
        return (False, ())
    try:
        try:
            return _feed_flags(conn)
        except sqlite3.Error:
            return (False, ())
    finally:
        conn.close()


def _apply_pause(store_path, paused):
    conn = sqlite3.connect(store_path, timeout=5)
    try:
        _feed_set_paused(conn, paused)
    finally:
        conn.close()


def _apply_trigger(store_path, rule, disabled):
    conn = sqlite3.connect(store_path, timeout=5)
    try:
        _feed_set_rule_disabled(conn, rule, disabled)
    finally:
        conn.close()


# -- Task 2: resource routes -----------------------------------------------
# Envelope `{ok, data, error:{code,message}}`; timestamps are UTC-seconds
# ints; ids everywhere. Error codes: UNAUTH, BAD_REQUEST, NOT_FOUND,
# UNAVAILABLE, RATE_LIMITED. Reads go through the existing Store tables
# (per-request sqlite connections — the handler runs on threads) and call
# the existing detail_metrics / reference_plan (never copied).

ALLOWED_TFS = {"5m": 300, "15m": 900, "1H": 3600, "4H": 14400, "1D": 86400}
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
SETUP_STATES = frozenset({"active", "invalidated", "expired", "resolved"})

_detail_metrics_fn = None
_detail_metrics_tried = False


def _detail_metrics_func():
    """The existing server-side indicator function (called, never copied)."""
    global _detail_metrics_fn, _detail_metrics_tried
    if not _detail_metrics_tried:
        _detail_metrics_tried = True
        try:
            from qtgui.app import detail_metrics as fn
            _detail_metrics_fn = fn
        except Exception:
            _detail_metrics_fn = None
    return _detail_metrics_fn


def _null_metrics():
    return {"rsi": None, "macd_hist": None, "ema50": None,
            "ema200": None, "vol_quote": None, "last_ts": None}


def _open_db(store_path):
    conn = sqlite3.connect(store_path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _default_is_ready(ready):
    """First-sweep flag from the Task 1 `ready` callable.

    Dict results honour an explicit ready/sweep_done/first_sweep_done key;
    anything else is booleanised.
    """
    try:
        result = ready() if callable(ready) else ready
    except Exception:
        return False
    if isinstance(result, dict):
        for key in ("ready", "sweep_done", "first_sweep_done"):
            if key in result:
                return bool(result[key])
        return False
    return bool(result)


def _db_writable(store_path):
    """True when the DB accepts a write (rolled-back probe, nothing kept)."""
    if not store_path or not os.path.exists(store_path):
        return False
    try:
        conn = sqlite3.connect(store_path, timeout=5)
    except sqlite3.Error:
        return False
    try:
        conn.execute("INSERT OR REPLACE INTO meta (key, value)"
                     " VALUES ('__svc_ready_probe__', '1')")
        conn.rollback()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _resample_bars(bars5, tf_sec, now):
    """Bucket closed 5m bars into tf_sec candles; closed buckets only."""
    closed5 = [b for b in bars5
               if int(b.get("ts", 0)) + 300 <= now]
    if tf_sec == 300:
        return [dict(b) for b in closed5]
    buckets = {}
    for b in closed5:
        buckets.setdefault(int(b["ts"]) // tf_sec, []).append(b)
    out = []
    for key in sorted(buckets):
        if key * tf_sec + tf_sec > now:
            continue
        grp = buckets[key]
        out.append({
            "ts": key * tf_sec,
            "o": grp[0]["o"], "h": max(g["h"] for g in grp),
            "l": min(g["l"] for g in grp), "c": grp[-1]["c"],
            "vol": sum(g.get("vol") or 0 for g in grp),
            "amount": sum(g.get("amount") or 0 for g in grp),
        })
    return out


def _setup_dict(row):
    return {
        "id": row["id"], "rule_id": row["rule_id"], "coin": row["coin"],
        "symbol": row["symbol"] if "symbol" in row.keys() else row["coin"],
        "src": row["src"] if "src" in row.keys() else None,
        "direction": row["direction"],
        "src_bar_ts": row["src_bar_ts"], "entry": row["entry"],
        "stop_px": row["stop_px"] if "stop_px" in row.keys() else None,
        "tgt_px": row["tgt_px"] if "tgt_px" in row.keys() else None,
        "state": row["state"],
        "substate": row["substate"] if "substate" in row.keys() else None,
        "monitoring": row["monitoring"]
        if "monitoring" in row.keys() else None,
        "created_at": row["created_at"]
        if "created_at" in row.keys() else None,
        "closed_at": row["closed_at"]
        if "closed_at" in row.keys() else None,
    }


def load_or_create_token(path):
    """Return the bearer token at *path*, creating it (mode 0600) if absent."""
    path = os.path.expanduser(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token + "\n")
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def _make_handler(token, ready, store_path=None, data_dir=None,
                  is_ready=None):
    cfg_store = store_path or os.path.join("data", "signals.db")
    cfg_data = data_dir or os.path.join("data", "bars")

    def _ready_ok():
        if is_ready is not None:
            try:
                return bool(is_ready() if callable(is_ready) else is_ready)
            except Exception:
                return False
        return _default_is_ready(ready)

    class Handler(BaseHTTPRequestHandler):
        server_version = "AltRadarService/1"
        protocol_version = "HTTP/1.1"

        # -- logging: request line only, redacted ----------------------
        def log_message(self, fmt, *args):
            try:
                text = fmt % args if args else fmt
            except Exception:
                text = fmt
            text = re.sub(r"(?i)bearer\s+[^\s'\"]+", "Bearer [redacted]",
                          text)
            LOG.info("%s - %s", self.address_string(),
                     text.replace("\n", " "))

        # -- helpers ----------------------------------------------------
        def _send_json(self, status, payload, origin=None):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if origin is not None:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)

        def _deny(self, status, code, message, origin=None):
            self._send_json(status, envelope(False, None, code, message),
                            origin=origin)

        def _cors_origin(self):
            """The request Origin if allowlisted, else None."""
            origin = self.headers.get("Origin")
            if origin and is_allowlisted_origin(origin):
                return origin.strip()
            return None

        def _gate(self):
            """Auth + origin/host gate for every route but /healthz.

            Returns the echoable Origin (or None) on success, else sends
            the 401/403 response and returns False.
            """
            auth = self.headers.get("Authorization") or ""
            scheme, _, presented = auth.partition(" ")
            good = (scheme.lower() == "bearer" and presented
                    and hmac.compare_digest(presented, token))
            if not good:
                self._deny(401, "UNAUTH",
                           "missing or invalid bearer token")
                return False
            origin = self.headers.get("Origin")
            if origin is not None and not is_allowlisted_origin(origin):
                self._deny(403, "FORBIDDEN",
                           "origin not allowlisted")
                return False
            if not is_loopback_host(self.headers.get("Host")):
                self._deny(403, "FORBIDDEN",
                           "host not loopback")
                return False
            return self._cors_origin()

        # -- routing ----------------------------------------------------
        def _route(self, is_options):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            qs = parse_qs(parsed.query)

            if self.command == "OPTIONS":
                # CORS preflight: answered only for allowlisted origins.
                origin = self.headers.get("Origin")
                if not origin or not is_allowlisted_origin(origin):
                    self._deny(403, "FORBIDDEN",
                               "origin not allowlisted")
                    return
                origin = origin.strip()
                requested = self.headers.get("Access-Control-Request-Method",
                                             "GET")
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods",
                                 f"{requested}, OPTIONS")
                self.send_header("Access-Control-Allow-Headers",
                                 "Authorization, Content-Type")
                self.send_header("Access-Control-Max-Age", "600")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if self.command == "GET" and path in ("", "/healthz", "/healthz/"):
                self._send_json(200, {"ok": True, "alive": True})
                return

            gated = self._gate()
            if gated is False:
                return

            if self.command == "GET" and path == "/snapshot":
                self._handle_snapshot(gated)
                return

            if self.command == "GET" and path == "/events":
                self._handle_events(gated)
                return

            if self.command == "GET" and path == "/readyz":
                self._handle_readyz(gated, _ready_ok, cfg_store)
                return

            if self.command == "GET" and path == "/setups":
                self._handle_setups(gated, qs, cfg_store)
                return

            if self.command == "GET" and path.startswith("/setup/"):
                self._handle_setup(gated, path[len("/setup/"):], cfg_store)
                return

            if self.command == "GET" and path == "/bars":
                self._handle_bars(gated, qs, cfg_data)
                return

            if self.command == "GET" and path == "/metrics":
                self._handle_metrics(gated, qs, cfg_data)
                return

            if self.command == "GET" and path.startswith("/tradeplan/"):
                self._handle_tradeplan(gated, path[len("/tradeplan/"):],
                                       cfg_store)
                return

            if self.command == "POST" and path.startswith("/commands/"):
                self._handle_command(gated, path[len("/commands/"):],
                                     cfg_store)
                return

            self._deny(404, "NOT_FOUND",
                       f"no such route: {path}", origin=gated)

        # -- Task 2 resource handlers (typed envelope) -------------------
        def _handle_snapshot(self, origin, store_path=cfg_store):
            now = int(time.time())
            data = {"ts": now, "snapshot_event_id": head_event_id(),
                    "setups": {}, "signals": None, "unavailable": []}
            try:
                conn = _open_db(store_path)
            except sqlite3.Error:
                data["unavailable"].append("database unavailable")
                self._send_json(200, envelope(True, data), origin=origin)
                return
            try:
                try:
                    for state, n in conn.execute(
                            "SELECT state, COUNT(*) FROM watcher_setup"
                            " GROUP BY state"):
                        data["setups"][state] = n
                except sqlite3.Error:
                    data["unavailable"].append("setups unavailable")
                try:
                    total = conn.execute(
                        "SELECT COUNT(*) FROM signal_log").fetchone()[0]
                    flagged = conn.execute(
                        "SELECT COUNT(*) FROM signal_log WHERE flagged=1"
                        ).fetchone()[0]
                    data["signals"] = {"rows": total, "flagged": flagged}
                except sqlite3.Error:
                    data["unavailable"].append("signals unavailable")
            finally:
                conn.close()
            self._send_json(200, envelope(True, data), origin=origin)

        def _handle_readyz(self, origin, ready_ok, store_path):
            now = int(time.time())
            if not ready_ok():
                self._send_json(503, envelope(
                    False, None, "UNAVAILABLE",
                    "first sweep not complete"), origin=origin)
                return
            if not _db_writable(store_path):
                self._send_json(503, envelope(
                    False, None, "UNAVAILABLE",
                    "database not writable"), origin=origin)
                return
            self._send_json(200, envelope(True, {"ready": True, "ts": now}),
                            origin=origin)

        def _handle_setups(self, origin, qs, store_path):
            state = (qs.get("state") or [None])[0]
            if state is not None and state not in SETUP_STATES:
                self._deny(400, "BAD_REQUEST",
                           f"unknown state {state!r}", origin=origin)
                return
            raw_limit = (qs.get("limit") or [None])[0]
            if raw_limit is None or raw_limit == "":
                limit = DEFAULT_LIMIT
            else:
                try:
                    limit = int(raw_limit)
                except (TypeError, ValueError):
                    self._deny(400, "BAD_REQUEST",
                               "limit must be an integer", origin=origin)
                    return
                if limit < 1:
                    self._deny(400, "BAD_REQUEST",
                               "limit must be >= 1", origin=origin)
                    return
                limit = min(limit, MAX_LIMIT)
            raw_cursor = (qs.get("cursor") or ["0"])[0]
            try:
                cursor = int(raw_cursor)
            except (TypeError, ValueError):
                self._deny(400, "BAD_REQUEST",
                           "cursor must be an integer offset",
                           origin=origin)
                return
            if cursor < 0:
                self._deny(400, "BAD_REQUEST",
                           "cursor must be >= 0", origin=origin)
                return
            try:
                conn = _open_db(store_path)
            except sqlite3.Error:
                self._deny(503, "UNAVAILABLE", "database unavailable",
                           origin=origin)
                return
            try:
                try:
                    if state is None:
                        rows = conn.execute(
                            "SELECT * FROM watcher_setup ORDER BY rowid"
                            " LIMIT ? OFFSET ?", (limit, cursor)).fetchall()
                    else:
                        rows = conn.execute(
                            "SELECT * FROM watcher_setup WHERE state=?"
                            " ORDER BY rowid LIMIT ? OFFSET ?",
                            (state, limit, cursor)).fetchall()
                except sqlite3.Error:
                    self._deny(503, "UNAVAILABLE", "setups unavailable",
                               origin=origin)
                    return
                self._send_json(200, envelope(
                    True, [_setup_dict(r) for r in rows]), origin=origin)
            finally:
                conn.close()

        def _handle_setup(self, origin, setup_id, store_path):
            if not setup_id:
                self._deny(400, "BAD_REQUEST", "missing setup id",
                           origin=origin)
                return
            try:
                conn = _open_db(store_path)
            except sqlite3.Error:
                self._deny(503, "UNAVAILABLE", "database unavailable",
                           origin=origin)
                return
            try:
                try:
                    row = conn.execute(
                        "SELECT * FROM watcher_setup WHERE id=?",
                        (setup_id,)).fetchone()
                except sqlite3.Error:
                    self._deny(503, "UNAVAILABLE", "setups unavailable",
                               origin=origin)
                    return
                if row is None:
                    self._deny(404, "NOT_FOUND",
                               f"no such setup: {setup_id}", origin=origin)
                    return
                self._send_json(200, envelope(True, _setup_dict(row)),
                                origin=origin)
            finally:
                conn.close()

        def _handle_bars(self, origin, qs, data_dir):
            coin = (qs.get("coin") or [None])[0]
            tf = (qs.get("tf") or [None])[0]
            if not coin or not tf:
                self._deny(400, "BAD_REQUEST",
                           "coin and tf are required", origin=origin)
                return
            if tf not in ALLOWED_TFS:
                self._deny(400, "BAD_REQUEST",
                           f"unsupported timeframe {tf!r}: closed bars only"
                           " for 5m/15m/1H/4H/1D", origin=origin)
                return
            if not os.path.exists(os.path.join(data_dir, f"{coin}.csv.gz")):
                self._deny(404, "NOT_FOUND", f"no bars for {coin}",
                           origin=origin)
                return
            try:
                bars5 = read_bars(data_dir, coin)
            except Exception:
                self._deny(503, "UNAVAILABLE", "bars unavailable",
                           origin=origin)
                return
            now = int(time.time())
            bars = _resample_bars(bars5, ALLOWED_TFS[tf], now)
            last_ts = bars[-1]["ts"] if bars else None
            self._send_json(200, envelope(
                True, {"coin": coin, "tf": tf, "bars": bars,
                       "last_ts": last_ts}), origin=origin)

        def _handle_metrics(self, origin, qs, data_dir):
            coin = (qs.get("coin") or [None])[0]
            if not coin:
                self._deny(400, "BAD_REQUEST", "coin is required",
                           origin=origin)
                return
            if not os.path.exists(os.path.join(data_dir, f"{coin}.csv.gz")):
                self._deny(404, "NOT_FOUND", f"no metrics for {coin}",
                           origin=origin)
                return
            try:
                bars5 = read_bars(data_dir, coin)
            except Exception:
                bars5 = []
            now = int(time.time())
            closed = [b for b in bars5 if int(b.get("ts", 0)) + 300 <= now]
            fn = _detail_metrics_func()
            try:
                data = fn(closed) if fn is not None else _null_metrics()
            except Exception:
                data = _null_metrics()
            # Warm-up honesty: the indicator helpers return neutral
            # placeholders (RSI 50.0, MACD 0.0) below their documented
            # minimums (indicators.rsi needs n+1=15 closes; macd_histogram
            # needs slow+signal+2=37). A placeholder is not a measurement,
            # so short-bar coins report explicit null — never a guess.
            n = len(closed)
            if n < 15:
                data["rsi"] = None
            if n < 37:
                data["macd_hist"] = None
            self._send_json(200, envelope(True, data), origin=origin)

        def _handle_tradeplan(self, origin, raw_id, store_path):
            try:
                signal_id = int(raw_id)
            except (TypeError, ValueError):
                self._deny(400, "BAD_REQUEST",
                           "tradeplan id must be an integer signal id",
                           origin=origin)
                return
            try:
                conn = _open_db(store_path)
            except sqlite3.Error:
                self._deny(503, "UNAVAILABLE", "database unavailable",
                           origin=origin)
                return
            try:
                try:
                    row = conn.execute(
                        "SELECT p.signal_id, p.direction, p.entry_low,"
                        " p.entry_high, p.stop, p.tp1, p.tp2, p.logged_at,"
                        " s.coin, s.ts FROM plan_log p"
                        " LEFT JOIN signal_log s ON s.id = p.signal_id"
                        " WHERE p.signal_id=?", (signal_id,)).fetchone()
                except sqlite3.Error:
                    self._deny(503, "UNAVAILABLE", "plans unavailable",
                               origin=origin)
                    return
                if row is None:
                    self._deny(404, "NOT_FOUND",
                               f"no such tradeplan: {signal_id}",
                               origin=origin)
                    return
                levels = {"entry_low": row["entry_low"],
                          "entry_high": row["entry_high"],
                          "stop": row["stop"], "tp1": row["tp1"],
                          "tp2": row["tp2"]}
                if any(v is None for v in levels.values()):
                    self._deny(503, "UNAVAILABLE",
                               "plan levels incomplete", origin=origin)
                    return
                try:
                    ref = reference_plan(levels)
                except Exception:
                    self._deny(503, "UNAVAILABLE",
                               "plan levels incomplete", origin=origin)
                    return
                data = {"signal_id": row["signal_id"],
                        "coin": row["coin"], "direction": row["direction"],
                        "ts": row["ts"], "logged_at": row["logged_at"]}
                data.update(ref)
                self._send_json(200, envelope(True, data), origin=origin)
            finally:
                conn.close()

        # -- Task 4 commands (idempotent, meta-persisted) -------------------
        def _handle_command(self, origin, name, store_path):
            if name not in COMMANDS:
                self._deny(404, "NOT_FOUND",
                           f"unknown command {name!r}; known: "
                           + ", ".join(COMMANDS), origin=origin)
                return
            raw = getattr(self, "_body", b"") or b""
            if not raw.strip():
                body = {}
            else:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    self._deny(400, "BAD_REQUEST",
                               "request body must be a JSON object",
                               origin=origin)
                    return
            if not isinstance(body, dict):
                self._deny(400, "BAD_REQUEST",
                           "request body must be a JSON object",
                           origin=origin)
                return
            try:
                if name == "pause":
                    _apply_pause(store_path, True)
                    self._send_json(200, envelope(
                        True, {"command": name, "idempotent": True,
                               "paused": True}), origin=origin)
                    return
                if name == "resume":
                    _apply_pause(store_path, False)
                    self._send_json(200, envelope(
                        True, {"command": name, "idempotent": True,
                               "paused": False}), origin=origin)
                    return
                rule = body.get("rule")
                if not isinstance(rule, str) or not rule:
                    self._deny(400, "BAD_REQUEST",
                               "missing 'rule' in request body",
                               origin=origin)
                    return
                known = _known_trigger_rules(store_path)
                if rule not in known:
                    self._deny(400, "BAD_REQUEST",
                               "unknown trigger rule; known: "
                               + ", ".join(sorted(known)), origin=origin)
                    return
                _apply_trigger(store_path, rule,
                               name == "disable-trigger")
                _, disabled = _command_flags(store_path)
                self._send_json(200, envelope(
                    True, {"command": name, "rule": rule,
                           "idempotent": True,
                           "enabled": name == "enable-trigger",
                           "disabled_triggers": sorted(disabled)}),
                    origin=origin)
            except sqlite3.Error:
                self._deny(503, "UNAVAILABLE", "database unavailable",
                           origin=origin)

        # -- Task 3 SSE stream (transition-only, subscribe-then-reconcile) --
        def _sse_write(self, frame):
            self.wfile.write(frame.encode("utf-8"))
            self.wfile.flush()

        def _handle_events(self, origin):
            cursor = self.headers.get("Last-Event-ID")
            if cursor is not None:
                cursor = cursor.strip()
            # Subscribe BEFORE copying the ring: copying first and
            # subscribing afterwards opens a window where an event appended
            # in between lands in neither the replay nor the subscriber
            # queue. Anything the queue delivers that was already replayed
            # is skipped below by id.
            q = _subscribe()
            try:
                with _EVENT_LOCK:
                    _prune_ring_locked()
                    snapshot = [dict(e) for e in _EVENTS]
                    floor = snapshot[0]["id"] if snapshot else None
                replay_from = _ZERO_ID
                send_resync = False
                if cursor:
                    if cursor.isdigit():
                        # Zero-pad so short numeric cursors compare
                        # numerically against the width-6 ids.
                        replay_from = cursor.zfill(EVENT_ID_WIDTH)
                        send_resync = (floor is not None
                                       and replay_from < floor)
                    # Unparseable cursor: replay everything retained, no
                    # resync (same as the mock).
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                if origin is not None:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.end_headers()
                if send_resync:
                    # Below the ring-buffer floor: the client cannot
                    # reconcile from replay, so it must re-snapshot.
                    self._sse_write(
                        "id: " + floor + "\nevent: resync\ndata: "
                        + json.dumps({"id": floor, "kind": "resync",
                                      "ts": int(time.time()), "coin": None,
                                      "reason": "cursor below ring-buffer "
                                      "floor — re-snapshot"}) + "\n\n")
                last_replayed = _ZERO_ID
                for event in snapshot:
                    if event["id"] > replay_from:
                        self._sse_write(_frame_event(event))
                        if event["id"] > last_replayed:
                            last_replayed = event["id"]
                while True:
                    try:
                        event = q.get(timeout=HEARTBEAT_S)
                    except queue.Empty:
                        self._sse_write(f": keepalive {int(time.time())}\n\n")
                        continue
                    # Skip anything the snapshot already covered.
                    if event["id"] <= last_replayed:
                        continue
                    self._sse_write(_frame_event(event))
                    if event["id"] > last_replayed:
                        last_replayed = event["id"]
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            finally:
                _unsubscribe(q)

        def do_GET(self):
            self._route(False)

        def do_POST(self):
            # Read the full body (1 MiB cap on kept bytes, remainder drained)
            # so a large/over-long body never desyncs the keep-alive stream.
            length = self.headers.get("Content-Length")
            try:
                n = int(length) if length else 0
            except ValueError:
                n = 0
            if n < 0:
                n = 0
            body = b""
            if n > 0:
                keep = min(n, MAX_COMMAND_BODY)
                while len(body) < keep:
                    chunk = self.rfile.read(min(65536, keep - len(body)))
                    if not chunk:
                        break
                    body += chunk
                remaining = n - len(body)
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            self._body = body
            self._route(False)

        def do_OPTIONS(self):
            self._route(True)

    return Handler


def serve(port, token, ready, store_path=None, data_dir=None,
          is_ready=None):
    """Bind 127.0.0.1:port and serve in a daemon thread; return the server.

    `ready` is the Task 1 sweep callback (also the default readiness
    signal); `store_path`/`data_dir` locate the Store DB and bar archive
    the resource routes read; `is_ready` optionally overrides readiness.
    All three are optional so Task 1 callers keep working unchanged.
    """
    handler = _make_handler(token, ready, store_path=store_path,
                            data_dir=data_dir, is_ready=is_ready)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever,
                                            kwargs={"poll_interval": 0.05},
                                            daemon=True,
                                            name="alt-radar-service")
    thread.start()
    LOG.info("service listening on 127.0.0.1:%d",
             server.server_address[1])
    return server
