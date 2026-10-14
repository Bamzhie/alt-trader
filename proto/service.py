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
import re
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .collector import read_bars
from .planner import REFERENCE_NOTIONAL, reference_plan

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

            if self.command == "POST" and path == "/commands/pause":
                # Stub: real pause/resume semantics arrive with Task 4.
                self._send_json(200, envelope(True, {"paused": False}),
                                origin=gated)
                return

            self._deny(404, "NOT_FOUND",
                       f"no such route: {path}", origin=gated)

        # -- Task 2 resource handlers (typed envelope) -------------------
        def _handle_snapshot(self, origin, store_path=cfg_store):
            now = int(time.time())
            data = {"ts": now, "snapshot_event_id": 0,
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

        def do_GET(self):
            self._route(False)

        def do_POST(self):
            length = self.headers.get("Content-Length")
            try:
                n = int(length) if length else 0
            except ValueError:
                n = 0
            if n > 0:
                self.rfile.read(min(n, 1 << 20))
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
