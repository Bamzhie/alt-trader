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

import argparse
import errno
import fcntl
import hmac
import importlib
import json
import logging
import os
from collections import OrderedDict
import queue
import re
import secrets
import signal
import sqlite3
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .collector import read_bars
from .funding import read_funding
from . import daemon as _daemon_mod
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

# Chart and metrics requests often ask for the same coin within seconds.
# Gzip files cannot be efficiently tailed, so cache only a few recently read
# contracts and invalidate each entry when the collector appends to its file.
_BAR_READ_CACHE = OrderedDict()
_BAR_READ_CACHE_LOCK = threading.Lock()
_BAR_READ_INFLIGHT = {}
_BAR_READ_CACHE_MAX = 4

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
               "sweep.completed", "quant.shadow")
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


QUANT_SHADOW_KIND = "quant.shadow"


def emit_quant_shadow(last, result):
    """Emit one quant.shadow event when a watcher step reports NEW counts.

    ``last`` is the previous ``(emitted, resolved)`` tuple (or None on the
    first step); ``result`` is the watcher's step() dict. Returns the
    updated tuple. Emits only when the step succeeded, the cumulative
    counts changed since ``last``, and at least one count is nonzero —
    so per-sweep polling never spams the ring. The frame carries counts
    only (state {"emitted", "resolved"}; coin None): no levels, no
    advice, nothing actionable.
    """
    if last is None:
        last = (0, 0)
    if not isinstance(result, dict) or not result.get("ok"):
        return last
    try:
        emitted = int(result.get("emitted", 0) or 0)
        resolved = int(result.get("resolved", 0) or 0)
    except (TypeError, ValueError):
        return last
    current = (emitted, resolved)
    if current != tuple(last) and (emitted > 0 or resolved > 0):
        emit_event(QUANT_SHADOW_KIND,
                   state={"emitted": emitted, "resolved": resolved})
    return current


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
# Safe-coin allowlist for the /funding route only: checked BEFORE any
# filesystem access so traversal names never reach the archive dir.
# (/bars joins its coin unchecked — untouched by design.)
_SAFE_FUNDING_COIN = re.compile(r"^[A-Za-z0-9_-]+$")
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


def _quant_status_for(writable, paused, watermarks, total):
    """Availability state from store meta + watermark (single source).

    unseen = watcher never ran (no watermark); healthy / paused (manual
    or auto-pause flag) / unavailable (readable but not writable).
    """
    if not writable:
        return "unavailable"
    if paused:
        return "paused"
    if watermarks == 0 and total == 0:
        return "unseen"
    return "healthy"


def _quant_db_writable(quant_db):
    """Write probe against the quant store's own meta table.

    The generic _db_writable targets the signals-db `meta` table, which
    quant.db does not have (it owns `quant_meta`); probing the wrong
    table would report every healthy quant store unavailable.
    Rolled back — nothing is kept. Call only after the read path has
    proven the file exists and carries quant tables.
    """
    try:
        conn = sqlite3.connect(quant_db, timeout=5)
    except sqlite3.Error:
        return False
    try:
        conn.execute("INSERT OR REPLACE INTO quant_meta "
                     "(key, value, updated_ts)"
                     " VALUES ('__svc_ready_probe__', '1', 0)")
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


def _read_service_bars(data_dir, coin):
    """Read a recently requested coin's gzip bars once per file version."""
    path = os.path.abspath(os.path.join(data_dir, f"{coin}.csv.gz"))
    stat = os.stat(path)
    key = (path, stat.st_mtime_ns, stat.st_size)
    with _BAR_READ_CACHE_LOCK:
        cached = _BAR_READ_CACHE.get(key)
        if cached is not None:
            _BAR_READ_CACHE.move_to_end(key)
            return cached
        load_lock = _BAR_READ_INFLIGHT.setdefault(key, threading.Lock())

    with load_lock:
        with _BAR_READ_CACHE_LOCK:
            cached = _BAR_READ_CACHE.get(key)
            if cached is not None:
                _BAR_READ_CACHE.move_to_end(key)
                return cached
        try:
            bars = read_bars(data_dir, coin)
        except Exception:
            with _BAR_READ_CACHE_LOCK:
                _BAR_READ_INFLIGHT.pop(key, None)
            raise
        with _BAR_READ_CACHE_LOCK:
            # Drop stale versions for this coin before keeping the new snapshot.
            for old_key in tuple(_BAR_READ_CACHE):
                if old_key[0] == path:
                    del _BAR_READ_CACHE[old_key]
            _BAR_READ_CACHE[key] = bars
            while len(_BAR_READ_CACHE) > _BAR_READ_CACHE_MAX:
                _BAR_READ_CACHE.popitem(last=False)
            _BAR_READ_INFLIGHT.pop(key, None)
        return bars


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
                   funding_dir=None, is_ready=None, scanner_health=None,
                   quant_db=None):
    cfg_store = store_path or os.path.join("data", "signals.db")
    cfg_data = data_dir or os.path.join("data", "bars")
    # Resolved once here (per server), never per request. Default: the
    # funding archive sibling of the bars dir (data/funding beside
    # data/bars, matching proto.funding.FUNDING_DIR).
    cfg_funding = (funding_dir or os.path.join(
        os.path.dirname(cfg_data), "funding"))
    # Same pattern for the quant shadow store: quant.db beside the bars
    # dir unless the caller passes an explicit path (tests use tmp dirs).
    cfg_quant = (quant_db or os.path.join(
        os.path.dirname(cfg_data), "quant.db"))

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
            origin = self._cors_origin()
            auth = self.headers.get("Authorization") or ""
            scheme, _, presented = auth.partition(" ")
            good = (scheme.lower() == "bearer" and presented
                    and hmac.compare_digest(presented, token))
            if not good:
                self._deny(401, "UNAUTH",
                           "missing or invalid bearer token", origin=origin)
                return False
            request_origin = self.headers.get("Origin")
            if request_origin is not None and origin is None:
                self._deny(403, "FORBIDDEN",
                           "origin not allowlisted")
                return False
            if not is_loopback_host(self.headers.get("Host")):
                self._deny(403, "FORBIDDEN",
                           "host not loopback", origin=origin)
                return False
            return origin

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
                self._send_json(200, {"ok": True, "alive": True},
                                origin=self._cors_origin())
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

            if self.command == "GET" and path == "/scanner-health":
                self._handle_scanner_health(gated, scanner_health)
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

            if self.command == "GET" and path == "/funding":
                self._handle_funding(gated, qs, cfg_funding)
                return

            if self.command == "GET" and path == "/quant-status":
                self._handle_quant_status(gated, cfg_quant)
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
            data = {
                "ts": now, "data_as_of": None,
                "snapshot_event_id": head_event_id(),
                "eligible_count": None, "evaluated_count": None,
                "active_setups": 0, "monitoring_coverage": None,
                "new_setups_24h": 0, "setups": [],
                "comparison_24h": {
                    "available": False,
                    "reason": "no persisted dashboard history",
                    "prior_ts": None, "delta_active_setups": None,
                    "delta_new_setups_24h": None,
                },
                "snapshot_history": [], "coins_without_setup": [],
                "market_overview": [], "movers": [],
                "recent_analysis": [], "activity": [],
                "supported_timeframes": ["5m", "15m", "1H", "4H", "1D"],
                "event_kinds": list(EVENT_KINDS),
                "unavailable": None,
            }
            try:
                conn = _open_db(store_path)
            except sqlite3.Error:
                data["unavailable"] = "dashboard database unavailable"
                self._send_json(200, envelope(True, data), origin=origin)
                return
            try:
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                # Sweep counts and history are persisted independently from
                # watcher setups. Read them so a healthy scanner does not
                # look like an empty/unavailable service in the dashboard.
                if "dashboard_snapshot" in tables:
                    snap_cols = {r[1] for r in conn.execute(
                        "PRAGMA table_info(dashboard_snapshot)")}
                    required = {"ts", "eligible_count", "evaluated_count",
                                "active_setups", "new_setups_24h",
                                "monitoring_coverage"}
                    if required <= snap_cols:
                        hist = conn.execute(
                            "SELECT ts, eligible_count, evaluated_count, "
                            "active_setups, new_setups_24h, "
                            "monitoring_coverage FROM dashboard_snapshot "
                            "ORDER BY ts DESC, rowid DESC LIMIT 200"
                        ).fetchall()
                        if hist:
                            latest = hist[0]
                            data.update({
                                "data_as_of": latest[0],
                                "eligible_count": latest[1],
                                "evaluated_count": latest[2],
                                "new_setups_24h": latest[4],
                                "monitoring_coverage": latest[5],
                            })
                            data["snapshot_history"] = [
                                {"ts": r[0], "eligible_count": r[1],
                                 "evaluated_count": r[2],
                                 "active_setups": r[3],
                                 "new_setups_24h": r[4],
                                 "monitoring_coverage": r[5],
                                 "covers_24h": r[0] <= now - 86400}
                                for r in reversed(hist)]
                            prior = conn.execute(
                                "SELECT ts, active_setups, new_setups_24h "
                                "FROM dashboard_snapshot WHERE ts<=? "
                                "ORDER BY ts DESC, rowid DESC LIMIT 1",
                                (now - 86400,)).fetchone()
                            if prior:
                                data["comparison_24h"] = {
                                    "available": True, "reason": None,
                                    "prior_ts": prior[0],
                                    "delta_active_setups": (
                                        latest[3] - prior[1]),
                                    "delta_new_setups_24h": (
                                        latest[4] - prior[2]),
                                }

                watcher_available = "watcher_setup" in tables
                projected = []
                try:
                    rows = (conn.execute(
                        "SELECT * FROM watcher_setup ORDER BY src_bar_ts"
                    ).fetchall() if watcher_available else [])
                    for row in rows:
                        r = dict(row)
                        state = r.get("state") or "active"
                        n_bars = int(r.get("N_bars") or 0)
                        src_ts = int(r.get("src_bar_ts") or 0)
                        created = int(r.get("created_at") or src_ts)
                        active = state == "active"
                        if active:
                            data["active_setups"] += 1
                        if created >= now - 86400:
                            data["new_setups_24h"] += 1
                        horizon = ({48: "4h", 288: "24h"}.get(
                            n_bars, f"{n_bars * 5}m"))
                        projected.append({
                            "id": r.get("id"), "coin": r.get("coin"),
                            "direction": r.get("direction"),
                            "horizon": horizon,
                            "state": state, "emitted_ts": created,
                            "expires_ts": src_ts + n_bars * 300,
                            "price": r.get("entry"),
                            "change_24h_pct": None,
                            "rule": r.get("rule_id"),
                            "criteria": [],
                            "inputs": {"rsi": None, "macd": None,
                                       "ema50": None, "ema200": None,
                                       "volume_pct": None},
                            "unavailable_inputs": [
                                "change_24h_pct", "rsi", "macd", "ema50",
                                "ema200", "volume_pct"],
                            "analysis": [
                                f"{r.get('src') or r.get('rule_id')} watcher setup",
                                f"stop {r.get('stop_px')}; target {r.get('tgt_px')}"],
                            "tradeplan_id": None,
                        })
                    data["setups"] = projected
                    data["active_setups"] = sum(
                        1 for row in projected if row["state"] == "active")
                    latest_data_ts = max(
                        [0] + [int(r.get("last_eval_bar") or
                                   r.get("src_bar_ts") or 0)
                               for r in map(dict, rows)])
                    if latest_data_ts:
                        data["data_as_of"] = max(
                            data["data_as_of"] or 0, latest_data_ts)
                    if not watcher_available:
                        data["unavailable"] = (
                            "watcher evaluation is not running; scanner "
                            "market data is shown, but no quant setups can "
                            "be emitted yet")
                except sqlite3.Error:
                    data["unavailable"] = (
                        "watcher setup storage is unavailable; scanner "
                        "market data may still be shown")

                # The scanner's current market state is useful as universe
                # context, but it is never promoted into a watcher setup.
                if "coin_state" in tables:
                    cols = {r[1] for r in conn.execute(
                        "PRAGMA table_info(coin_state)")}
                    needed = {"coin", "price", "change_24h_pct",
                              "quote_vol_24h", "last_seen", "veto_codes"}
                    if needed <= cols:
                        fresh = conn.execute(
                            "SELECT coin, price, change_24h_pct, "
                            "quote_vol_24h, last_seen, veto_codes "
                            "FROM coin_state WHERE last_seen>=? "
                            "AND change_24h_pct IS NOT NULL "
                            "ORDER BY quote_vol_24h DESC",
                            (now - 900,)).fetchall()
                        active_coins = {s["coin"] for s in projected
                                        if s["state"] == "active"}
                        data["coins_without_setup"] = [
                            {"coin": r[0], "name": r[0],
                             "blocking_reason": (
                                 "watcher evaluation is not running"
                                 if not watcher_available else
                                 ("veto: " + r[5] if r[5] else
                                  "no qualifying watcher setup")),
                             "price": r[1], "change_24h_pct": r[2],
                             "quote_vol_24h": r[3], "last_seen": r[4]}
                            for r in fresh if r[0] not in active_coins]
                        by_coin = {r[0]: r for r in fresh}
                        names = {"BTC": "Bitcoin", "ETH": "Ethereum"}
                        data["market_overview"] = [
                            {"coin": coin, "name": names[coin],
                             "price": by_coin[coin][1],
                             "change_24h_pct": by_coin[coin][2],
                             "last_bar_ts": None}
                            for coin in ("BTC", "ETH") if coin in by_coin]
                        gainers = sorted(
                            (r for r in fresh if r[2] > 0),
                            key=lambda r: r[2], reverse=True)[:5]
                        losers = sorted(
                            (r for r in fresh if r[2] < 0),
                            key=lambda r: r[2])[:5]
                        movers = {r[0]: {"coin": r[0], "name": r[0],
                                         "change_24h_pct": r[2]}
                                  for r in gainers + losers}
                        data["movers"] = list(movers.values())

                if "watcher_events" in tables:
                    data["activity"] = [
                        {"ts": r[0], "kind": r[1],
                         "coin": r[2] or None, "text": r[3]}
                        for r in conn.execute(
                            "SELECT ts, kind, coin, text FROM watcher_events "
                            "ORDER BY ts DESC, rowid DESC LIMIT 50")]
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

        def _handle_scanner_health(self, origin, scanner_health):
            """Scanner lifecycle state — distinct from /healthz (process
            alive) and /readyz (first sweep done). 503 UNAVAILABLE when no
            health source is wired: never claim health we cannot read.

            ``status`` is healthy / restarting / failed (see run_service
            for the exact transitions); ``restart_count`` /
            ``last_restart_ts`` count restart attempts begun after crashes
            (the initial start and crash detections are not restarts).
            """
            if scanner_health is None:
                self._deny(503, "UNAVAILABLE",
                           "scanner health not exposed", origin=origin)
                return
            try:
                state = (scanner_health() if callable(scanner_health)
                         else scanner_health)
            except Exception:
                self._deny(503, "UNAVAILABLE",
                           "scanner health unavailable", origin=origin)
                return
            if not isinstance(state, dict):
                self._deny(503, "UNAVAILABLE",
                           "scanner health unavailable", origin=origin)
                return
            now = int(time.time())
            last_sweep_ts = state.get("last_sweep_ts")
            data_lag_s = (None if last_sweep_ts is None
                          else max(0, int(now - last_sweep_ts)))
            self._send_json(200, envelope(True, {
                "status": state.get("status"),
                "last_error": state.get("last_error"),
                "restart_count": state.get("restart_count", 0),
                "last_restart_ts": state.get("last_restart_ts", 0),
                # WS3 timeliness — all measured from completed sweeps,
                # None until their first real observation (never invented).
                "sweep_count": state.get("sweep_count", 0),
                "last_sweep_ts": last_sweep_ts,
                "last_sweep_duration_s": state.get("last_sweep_duration_s"),
                "observed_sweep_interval_s":
                    state.get("observed_sweep_interval_s"),
                "data_lag_s": data_lag_s,
            }), origin=origin)

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
            raw_limit = (qs.get("limit") or ["1000"])[0]
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                self._deny(400, "BAD_REQUEST", "limit must be an integer",
                           origin=origin)
                return
            if limit < 1 or limit > 5000:
                self._deny(400, "BAD_REQUEST",
                           "limit must be between 1 and 5000", origin=origin)
                return
            try:
                bars5 = _read_service_bars(data_dir, coin)
            except Exception:
                self._deny(503, "UNAVAILABLE", "bars unavailable",
                           origin=origin)
                return
            now = int(time.time())
            bars = _resample_bars(bars5, ALLOWED_TFS[tf], now)[-limit:]
            # Match the frontend's typed OHLCV contract and make the
            # closed-bar guarantee explicit for CandleChart.
            chart_bars = [{
                "ts": b["ts"], "open": b["o"], "high": b["h"],
                "low": b["l"], "close": b["c"],
                "volume": b.get("vol") or 0, "closed": True,
            } for b in bars]
            last_ts = chart_bars[-1]["ts"] if chart_bars else None
            self._send_json(200, envelope(
                True, {"coin": coin, "tf": tf, "bars": chart_bars,
                       "closed_only": True, "count": len(chart_bars),
                       "data_as_of": last_ts, "last_ts": last_ts,
                       "unavailable": None}), origin=origin)

        def _handle_funding(self, origin, qs, funding_dir):
            coin = (qs.get("coin") or [None])[0]
            if not coin:
                self._deny(400, "BAD_REQUEST",
                           "coin is required", origin=origin)
                return
            if not _SAFE_FUNDING_COIN.match(coin):
                self._deny(400, "BAD_REQUEST",
                           "invalid coin name", origin=origin)
                return
            if not os.path.exists(os.path.join(funding_dir,
                                               f"{coin}.csv.gz")):
                self._deny(404, "NOT_FOUND", f"no funding for {coin}",
                           origin=origin)
                return
            raw_limit = (qs.get("limit") or ["1000"])[0]
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                self._deny(400, "BAD_REQUEST", "limit must be an integer",
                           origin=origin)
                return
            if limit < 1 or limit > 5000:
                self._deny(400, "BAD_REQUEST",
                           "limit must be between 1 and 5000", origin=origin)
                return
            try:
                rows = read_funding(funding_dir, coin)
            except Exception:
                self._deny(503, "UNAVAILABLE", "funding unavailable",
                           origin=origin)
                return
            rows = rows[-limit:]
            last_ts = rows[-1]["ts"] if rows else None
            self._send_json(200, envelope(
                True, {"coin": coin, "rows": rows,
                       "count": len(rows),
                       "data_as_of": last_ts, "last_ts": last_ts}),
                origin=origin)

        def _handle_quant_status(self, origin, quant_db):
            """Read-only shadow-watcher status over the quant store.

            200 whenever quant.db is readable, with `status` one of
            unseen (watcher never ran — no watermark) / healthy / paused
            (manual or auto-pause flag) / unavailable (readable but not
            writable); `reason` carries insufficient-evidence while event
            counts are zero. 503 UNAVAILABLE only when quant.db itself is
            unreadable. Never 404: an empty store is explicit zeros.
            """
            try:
                conn = sqlite3.connect(
                    f"file:{os.path.abspath(quant_db)}?mode=ro", uri=True,
                    timeout=5)
                conn.row_factory = sqlite3.Row
            except sqlite3.Error:
                self._deny(503, "UNAVAILABLE",
                           "quant database unavailable", origin=origin)
                return
            try:
                try:
                    tables = {r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                except sqlite3.Error:
                    self._deny(503, "UNAVAILABLE",
                               "quant database unavailable", origin=origin)
                    return
                if "quant_event" not in tables:
                    self._deny(503, "UNAVAILABLE",
                               "quant database unavailable", origin=origin)
                    return
                try:
                    total = conn.execute(
                        "SELECT COUNT(*) FROM quant_event").fetchone()[0]
                    opened = conn.execute(
                        "SELECT COUNT(*) FROM quant_event "
                        "WHERE state='EMITTED'").fetchone()[0]
                    resolved = conn.execute(
                        "SELECT COUNT(*) FROM quant_event "
                        "WHERE state='RESOLVED'").fetchone()[0]
                    supp_rows = (conn.execute(
                        "SELECT rule_id, reason, n FROM quant_suppressed "
                        "ORDER BY n DESC LIMIT 5").fetchall()
                        if "quant_suppressed" in tables else [])
                    meta = ({r[0]: r[1] for r in conn.execute(
                        "SELECT key, value FROM quant_meta").fetchall()}
                        if "quant_meta" in tables else {})
                    watermarks = (conn.execute(
                        "SELECT COUNT(*) FROM quant_watermark").fetchone()[0]
                        if "quant_watermark" in tables else 0)
                except sqlite3.Error:
                    self._deny(503, "UNAVAILABLE",
                               "quant database unavailable", origin=origin)
                    return
            finally:
                conn.close()
            try:
                from .quant.config import DEFAULT as _quant_default
                config_hash = _quant_default.config_hash()
            except Exception:
                config_hash = None
            try:
                from .quant import gates as _gates_mod
                promo_dir = os.path.dirname(os.path.abspath(quant_db)) or "."
                promotions = [rec for rec in
                              _gates_mod.load(promo_dir).values()
                              if isinstance(rec, dict) and rec.get("passed")]
            except Exception:
                promotions = []
            paused = meta.get("pause") == "1" or meta.get("auto_pause") == "1"
            pause_reason = meta.get("auto_pause_reason") or None
            if meta.get("pause") == "1" and not pause_reason:
                pause_reason = "manual pause"
            status = _quant_status_for(_quant_db_writable(quant_db), paused,
                                       watermarks, total)
            self._send_json(200, envelope(True, {
                "mode": "shadow",
                "status": status,
                "paused": paused,
                "pause_reason": pause_reason,
                "config_hash": config_hash,
                "event_counts": {"total": total, "emitted": opened,
                                 "resolved": resolved},
                "suppression_top": [
                    {"rule_id": r[0], "reason": r[1], "n": r[2]}
                    for r in supp_rows],
                "promotions": promotions,
                "reason": "insufficient-evidence" if total == 0 else None,
            }), origin=origin)

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
                bars5 = _read_service_bars(data_dir, coin)
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
           funding_dir=None, is_ready=None, scanner_health=None,
           quant_db=None):
    """Bind 127.0.0.1:port and serve in a daemon thread; return the server.

    `ready` is the Task 1 sweep callback (also the default readiness
    signal); `store_path`/`data_dir` locate the Store DB and bar archive
    the resource routes read; `funding_dir` locates the funding archive
    the /funding route reads (default: funding sibling of `data_dir`);
    `quant_db` locates the quant shadow store the /quant-status route
    reads (default: quant.db sibling of `data_dir`); `is_ready`
    optionally overrides readiness; `scanner_health` optionally supplies
    the scanner lifecycle state for `GET /scanner-health` (a callable
    returning a dict, or a dict). All are optional so Task 1 callers keep
    working unchanged; when `scanner_health` is absent the route answers
    503 UNAVAILABLE — it never claims health it cannot read.
    """
    handler = _make_handler(token, ready, store_path=store_path,
                            data_dir=data_dir, funding_dir=funding_dir,
                            is_ready=is_ready,
                            scanner_health=scanner_health,
                            quant_db=quant_db)
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


# -- Task 5: daemon embedding + single scanner -------------------------------
# One process does both jobs: the HTTP/SSE server above plus the daemon
# scan/collect/resolve loop. The loop is IMPORTED from proto.daemon
# (run_daemon_loop / build_parser / make_app / maybe_handle_measure_status
# / record_completed_sweep) — its body is never copied here. Singular
# ownership: an fcntl lockfile refuses a second scanner, and SIGTERM/SIGINT
# stops the loop, the server, then the sessions, in that order.

DEFAULT_PORT = 18741
PORT_SCAN_ATTEMPTS = 16
SCANNER_LOCK_NAME = "scanner.lock"
LOCK_REFUSED_EXIT = 1
DEFAULT_TOKEN_FILE = os.path.join("~", ".config", "altradar",
                                  "service.token")

# Scanner state for health monitoring and recovery
SCANNER_HEALTHY = "healthy"
SCANNER_FAILED = "failed"
SCANNER_RESTARTING = "restarting"

# Default exponential backoff parameters for scanner recovery
DEFAULT_BACKOFF_BASE_S = 5
DEFAULT_BACKOFF_MAX_S = 300
# Hard cap on recovery retries before giving up (server stays alive; scanner
# stays in FAILED state). A crash with --iterations=None (infinite mode) will
# retry up to this count; a finite --iterations run terminates normally.
MAX_SCANNER_RETRIES = 12


class ScannerAlreadyRunning(Exception):
    """A second scanner asked to start while the lock is held."""


def default_lock_path(db_path):
    """Lockfile beside the deployment DB (overridable via --lock-file)."""
    parent = os.path.dirname(os.path.abspath(db_path))
    return os.path.join(parent, SCANNER_LOCK_NAME)


def acquire_scanner_lock(path):
    """Hold an exclusive non-blocking flock on *path*.

    Returns the open file object (the lock lives until it is closed).
    Raises ScannerAlreadyRunning when another process — or another open
    of this path — already holds it. fcntl locks vanish with the process,
    so a crashed owner never leaves a stale lock behind.
    """
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fh = open(path, "a+b")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            fh.close()
        except OSError:
            pass
        raise ScannerAlreadyRunning(
            f"another scanner is already running (lock {path})")
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n".encode("utf-8"))
        fh.flush()
    except OSError:
        pass
    return fh


def serve_with_fallback(port, token, ready, attempts=PORT_SCAN_ATTEMPTS,
                        **kw):
    """Bind port..port+attempts-1 on loopback; return (server, bound_port).

    Raises the last OSError when every candidate is taken; non-conflict
    errors (e.g. permission) raise immediately.
    """
    last = None
    for candidate in range(port, port + attempts):
        try:
            server = serve(candidate, token, ready, **kw)
            return server, server.server_address[1]
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
            last = e
    raise last


def install_signal_handlers(stop_event):
    """Install SIGTERM/SIGINT -> stop_event.set(); return the old handlers."""

    def _handle(signum, _frame):
        print(f"service: caught signal {signum}; shutting down",
              flush=True)
        stop_event.set()

    old = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            old[sig] = signal.signal(sig, _handle)
        except (ValueError, OSError, RuntimeError):
            pass
    return old


def restore_signal_handlers(old):
    """Undo install_signal_handlers (best effort)."""
    for sig, handler in old.items():
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError, RuntimeError):
            pass


def close_sessions(app=None):
    """Last shutdown step: close exchange sessions, then the Store.

    The venues use per-request connections (no persistent sessions today),
    so this calls any ``close_session`` hook the venue modules expose and
    then closes the App's Store. Never raises.
    """
    for modname in ("mexc", "bybit"):
        try:
            mod = importlib.import_module("." + modname, __package__)
        except Exception:
            continue
        closer = getattr(mod, "close_session", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
    if app is not None:
        store = getattr(app, "store", None)
        if store is not None:
            try:
                store.close()
            except Exception:
                pass


def run_service(*, port=DEFAULT_PORT, token, db, data_dir=None,
                funding_dir=None,
                lock_path=None, daemon_argv=None, stop_event=None,
                install_signals=True, on_serving=None, on_health=None,
                backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
                backoff_max_s: float = DEFAULT_BACKOFF_MAX_S,
                max_scanner_retries: int = MAX_SCANNER_RETRIES,
                quant_db=None):
    """Run the HTTP server plus the embedded daemon loop; return a result.

    ``daemon_argv`` is parsed by the daemon parser (imported, never
    re-spelled) for the loop cadences; ``db`` feeds both the loop and the
    resource routes. ``stop_event`` (or SIGTERM/SIGINT) ends the run.
    Shutdown is ordered — loop, server, sessions — and recorded in
    ``result["shutdown_order"]``. A held lockfile returns
    ``{"refused": True, "exit_code": LOCK_REFUSED_EXIT}`` without starting
    any scanning.

    Scanner resilience (WS2): a crash of the embedded loop is retried with
    capped exponential backoff (``backoff_base_s`` doubling up to
    ``backoff_max_s``, at most ``max_scanner_retries`` restart attempts)
    while the HTTP server stays reachable the whole time — /healthz keeps
    answering 200, /readyz keeps reporting first-sweep readiness, and
    /scanner-health reports the scanner lifecycle:

    - ``healthy``    — the first attempt is running, OR a restart has
                       completed an actual sweep (the explicit recovery
                       signal: ``on_sweep`` moves RESTARTING -> HEALTHY),
                       OR a run finished normally.
    - ``restarting`` — a restart attempt after a crash is in progress;
                       it stays in this state until a sweep completes.
    - ``failed``     — the last attempt crashed; persists through the
                       backoff wait and after retries are exhausted.

    ``restart_count`` / ``last_restart_ts`` count restart ATTEMPTS BEGUN
    (the initial start is not a restart; a crash is not a restart — the
    counter moves only when attempt N>1 begins), so a parked scanner
    reports exactly the retries it attempted. A finite ``--iterations``
    run that completes normally is completion, not a crash: it is never
    restarted. When retries are exhausted the loop parks in FAILED
    (server still up) until ``stop_event`` — the process is not torn
    down by a dead scanner.

    ``on_health`` is called once with a callable returning the current
    scanner health dict (for embedded callers that need to surface health
    outside the HTTP routes).

    Result semantics (explicit): ``result["ok"]`` means the service ENDED
    in a healthy state — it is NOT "no failure ever occurred". A crash
    that later recovers reports ``ok=True`` while preserving its
    diagnostics: ``loop_error`` (last crash message), ``crashed`` (any
    crash occurred), ``crash_count``, ``recovered`` (crashed and ended
    healthy), and ``final_status`` (ending lifecycle state: healthy or
    failed). Ending parked-failed reports ``ok=False``. ``exit_code`` is
    unchanged (0 unless the lock was refused).
    """
    result = {"ok": False, "refused": False, "exit_code": 0, "port": None,
              "shutdown_order": [], "loop_error": None,
              # Result semantics: ok = the service ENDED in a healthy
              # state, NOT "no failure ever occurred". A crash that later
              # recovers keeps its historical diagnostics and reports
              # ok=True; ending parked-failed reports ok=False.
              "crashed": False, "crash_count": 0, "recovered": False,
              "final_status": None}
    lock_path = lock_path or default_lock_path(db)
    try:
        lock_fh = acquire_scanner_lock(lock_path)
    except ScannerAlreadyRunning as e:
        print(f"service: refused: {e}", file=sys.stderr, flush=True)
        result["refused"] = True
        result["exit_code"] = LOCK_REFUSED_EXIT
        return result

    stop = stop_event if stop_event is not None else threading.Event()
    old_handlers = {}
    if install_signals:
        old_handlers = install_signal_handlers(stop)
    try:
        parser = _daemon_mod.build_parser()
        loop_args = parser.parse_args(daemon_argv or [])
        loop_args.db = db
        sweep_done = threading.Event()

        def _on_sweep(eligible, evaluated, duration_s=None):
            # Explicit recovery signal: a completed sweep proves the
            # scanner is working again. A retry that was RESTARTING becomes
            # HEALTHY; no other state is touched (a sweep cannot fire while
            # FAILED — on_sweep only runs inside the loop). This runs
            # before the bookkeeping so a recording failure never masks a
            # real recovery.
            now_s = time.time()
            with scanner_lock:
                if scanner_state["status"] == SCANNER_RESTARTING:
                    scanner_state["status"] = SCANNER_HEALTHY
                # WS3 timeliness: observed cadence (interval between
                # consecutive completed sweeps) and measured duration,
                # from real observations only.
                prev_ts = scanner_state["last_sweep_ts"]
                if prev_ts is not None:
                    scanner_state["observed_sweep_interval_s"] = (
                        now_s - prev_ts)
                scanner_state["sweep_count"] += 1
                scanner_state["last_sweep_ts"] = now_s
                if duration_s is not None:
                    scanner_state["last_sweep_duration_s"] = duration_s
            _daemon_mod.record_completed_sweep(db, eligible, evaluated)
            sweep_done.set()

        # Scanner state tracking - shared with the /scanner-health route
        # (readable by any API consumer) and with on_health callers.
        # WS3 timeliness fields: sweep cadence/lag/duration are measured
        # from completed sweeps (the daemon passes duration_s), never
        # invented — fields stay None until their first real observation.
        scanner_state = {"status": SCANNER_HEALTHY, "last_error": None,
                         "restart_count": 0, "last_restart_ts": 0,
                         "sweep_count": 0, "last_sweep_ts": None,
                         "last_sweep_duration_s": None,
                         "observed_sweep_interval_s": None}
        scanner_lock = threading.Lock()

        def _scanner_health():
            with scanner_lock:
                return dict(scanner_state)

        server, bound = serve_with_fallback(
            port, token, lambda: {"sweep_done": sweep_done.is_set()},
            store_path=db, data_dir=data_dir,
            funding_dir=funding_dir,
            is_ready=sweep_done.is_set,
            scanner_health=_scanner_health,
            quant_db=quant_db)
        result["port"] = bound
        print(f"service: listening on 127.0.0.1:{bound} db={db} "
              f"lock={lock_path}", flush=True)
        if on_serving is not None:
            on_serving(server, bound)
        if on_health is not None:
            on_health(_scanner_health)

        app_box = {}

        def _run_loop():
            backoff_s = backoff_base_s
            attempt = 0
            while not stop.is_set():
                attempt += 1
                with scanner_lock:
                    if attempt == 1:
                        # First run: the scanner is starting up healthy.
                        scanner_state["status"] = SCANNER_HEALTHY
                    else:
                        # This attempt is an actual restart after a crash:
                        # restart_count / last_restart_ts describe restart
                        # attempts begun, not crash detections.
                        scanner_state["status"] = SCANNER_RESTARTING
                        scanner_state["restart_count"] += 1
                        scanner_state["last_restart_ts"] = int(time.time())
                    scanner_state["last_error"] = None
                try:
                    _daemon_mod.run_daemon_loop(
                        loop_args, stop_event=stop, on_sweep=_on_sweep,
                        app_box=app_box)
                    # Normal completion: a finite --iterations run consumed
                    # its cap — completion, not a crash; never restart it.
                    # An infinite-mode loop only returns when stop is set,
                    # so returning here also means shutdown.
                    with scanner_lock:
                        scanner_state["status"] = SCANNER_HEALTHY
                    return
                except Exception as e:  # keep server alive on loop failure
                    err_msg = f"{type(e).__name__}: {e}"
                    result["loop_error"] = err_msg
                    result["crashed"] = True
                    result["crash_count"] += 1
                    with scanner_lock:
                        scanner_state["status"] = SCANNER_FAILED
                        scanner_state["last_error"] = err_msg
                    print(f"service: loop FAILED: {err_msg}", flush=True)
                    # Don't retry on finite runs (user expected a clean exit)
                    if loop_args.iterations is not None:
                        return
                    # Cap on restarts actually attempted; park in FAILED
                    # with the server up rather than tearing it down.
                    if scanner_state["restart_count"] >= max_scanner_retries:
                        print(f"service: scanner exhausted "
                              f"{max_scanner_retries} retries; parking "
                              f"in FAILED (server stays up)",
                              flush=True)
                        while not stop.is_set():
                            stop.wait(1.0)
                        return
                    # Exponential backoff before the next restart attempt
                    wait_s = min(backoff_s, backoff_max_s)
                    print(f"service: scanner backoff {wait_s}s", flush=True)
                    backoff_s = min(backoff_s * 2, backoff_max_s)
                    if stop.wait(wait_s):  # interrupted by stop event
                        return

        loop_thread = threading.Thread(target=_run_loop,
                                       name="daemon-loop", daemon=True)
        loop_thread.start()
        try:
            while loop_thread.is_alive():
                if stop.wait(0.25):
                    break
            stop.set()
        except KeyboardInterrupt:
            stop.set()

        loop_thread.join()
        result["shutdown_order"].append("loop")
        try:
            server.shutdown()
        finally:
            server.server_close()
        result["shutdown_order"].append("server")
        close_sessions(app_box.get("app"))
        result["shutdown_order"].append("sessions")
        # Result semantics (explicit): ok = the service ENDED in a healthy
        # state. loop_error/crashed/crash_count preserve the historical
        # diagnostics even when the scanner recovered; `recovered` flags
        # exactly that case, and final_status is the ending lifecycle
        # state (healthy | failed).
        final_status = _scanner_health()["status"]
        result["final_status"] = final_status
        result["recovered"] = (result["crashed"]
                               and final_status == SCANNER_HEALTHY)
        result["ok"] = final_status == SCANNER_HEALTHY
        print("service: stopped cleanly", flush=True)
        return result
    finally:
        if old_handlers:
            restore_signal_handlers(old_handlers)
        try:
            lock_fh.close()
        except OSError:
            pass


def build_service_parser():
    """Daemon flags (imported) plus the service's own --port/--token flags."""
    ap = _daemon_mod.build_parser()
    ap.description = ("ALT RADAR headless service: HTTP API plus the "
                      "embedded scan/collect/resolve loop")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="first loopback port to try; scans upward on "
                         "conflict (default 18741)")
    ap.add_argument("--token", default=None,
                    help="per-launch bearer token (otherwise the token file "
                         "is used)")
    ap.add_argument("--token-file", default=DEFAULT_TOKEN_FILE,
                    help="config-file bearer token (0600, auto-generated on "
                         "first start if absent)")
    ap.add_argument("--lock-file", default=None,
                    help="single-scanner lockfile (default: beside the DB)")
    ap.add_argument("--data-dir", default=None,
                    help="bar archive dir for /bars and /metrics")
    ap.add_argument("--funding-dir", default=None,
                    help="funding archive dir for /funding "
                         "(default: funding sibling of --data-dir)")
    # No --quant-db here: the inherited daemon parser already owns it
    # (same default data/quant.db); main() passes args.quant_db through.
    return ap


# Loop flags forwarded from the service parser into the embedded
# daemon loop's argv (parsed by the daemon parser, never re-spelled).
# Includes the quant shadow flags: without them the embedded loop would
# parse its own defaults and never enable the watcher, while the
# /quant-status route reads the (separately plumbed) quant_db path —
# route and loop would disagree. Both derive from the same args.
_DAEMON_ARGV_NAMES = ("stake", "coins", "interval", "log_threshold",
                      "use_v3", "collect_every", "resolve_every",
                      "measure_every", "iterations", "no_scan",
                      "quant_shadow", "quant_db", "quant_bars",
                      "quant_equity")


def _daemon_argv_for(args):
    """Build the embedded loop's argv from parsed service args."""
    daemon_argv = []
    for name in _DAEMON_ARGV_NAMES:
        value = getattr(args, name)
        flag = "--" + name.replace("_", "-")
        if isinstance(value, bool):
            if value:
                daemon_argv.append(flag)
        elif value is not None:
            daemon_argv += [flag, str(value)]
    return daemon_argv


def main(argv=None):
    """Entry point: ``python3 -m proto.service``. Returns an exit code."""
    args = build_service_parser().parse_args(argv)
    if args.measure_status or args.enable_measurement_epoch:
        # Read-only operator path, owned by the daemon module (no copy).
        app = _daemon_mod.make_app(args)
        try:
            _daemon_mod.maybe_handle_measure_status(app, args)
        finally:
            try:
                app.store.close()
            except Exception:
                pass
        return 0
    token = args.token or load_or_create_token(args.token_file)
    data_dir = args.data_dir
    daemon_argv = _daemon_argv_for(args)
    result = run_service(port=args.port, token=token, db=args.db,
                         data_dir=data_dir,
                         funding_dir=args.funding_dir,
                         lock_path=args.lock_file,
                         daemon_argv=daemon_argv,
                         quant_db=getattr(args, "quant_db", None))
    return result.get("exit_code", 0)


if __name__ == "__main__":
    raise SystemExit(main())
