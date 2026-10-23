"""Collection-only, read-only service: bars plus a neutral status API.

One process owns the archive collectors and serves loopback-only read
routes for those archives. It does not scan, score, plan, resolve outcomes,
or open ``signals.db``/``quant.db``.
"""

import argparse
import errno
import fcntl
import hmac
import json
import logging
import os
import re
import secrets
import signal
import sys
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import collection
from .collector import read_bars
from .funding import read_funding
from .prices import read_prices
from .settlements import read_settlements

LOG = logging.getLogger("proto.service")

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
TAURI_ORIGINS = frozenset({"tauri://localhost", "https://tauri.localhost"})
_LOCALHOST_ORIGIN = re.compile(
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$", re.IGNORECASE)

DEFAULT_PORT = 18741
PORT_SCAN_ATTEMPTS = 16
DEFAULT_TOKEN_FILE = os.path.join("~", ".config", "altradar",
                                  "service.token")
COLLECTOR_LOCK_NAME = "collector.lock"

ALLOWED_TFS = {"5m": 300, "15m": 900, "1H": 3600, "4H": 14400, "1D": 86400}
_SAFE_COIN = re.compile(r"^[A-Za-z0-9_-]+$")
_PRICE_SERIES = frozenset({"mark", "index"})

_BAR_READ_CACHE = OrderedDict()
_BAR_READ_CACHE_LOCK = threading.Lock()
_BAR_READ_INFLIGHT = {}
_BAR_READ_CACHE_MAX = 4


class _RedactingFilter(logging.Filter):
    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "bearer" in msg.lower() or "authorization" in msg.lower():
            record.msg = re.sub(r"(?i)bearer\s+[^\s'\"]+", "Bearer [redacted]",
                                msg)
            record.args = ()
        return True


LOG.addFilter(_RedactingFilter())


def is_allowlisted_origin(origin):
    if not origin:
        return False
    origin = origin.strip()
    return origin in TAURI_ORIGINS or _LOCALHOST_ORIGIN.match(origin) is not None


def is_loopback_host(host):
    if not host:
        return False
    host = host.strip().lower()
    if host.startswith("["):
        host = host.split("]", 1)[0][1:]
    else:
        host = host.split(":", 1)[0]
    return host in LOOPBACK_HOSTS


def envelope(ok, data=None, code=None, message=None):
    err = None if ok else {"code": code or "ERROR",
                           "message": message or "error"}
    return {"ok": ok, "data": data, "error": err}


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


def _resolve_archive_dir(custom_dir, data_dir, default_name):
    if custom_dir is not None:
        return custom_dir
    cfg_data = data_dir or os.path.join("data", "bars")
    return os.path.join(os.path.dirname(cfg_data), default_name)


def _resample_bars(bars5, tf_sec, now):
    """Bucket closed 5m bars into tf_sec candles; closed buckets only."""
    closed5 = [b for b in bars5 if int(b.get("ts", 0)) + 300 <= now]
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
            for old_key in tuple(_BAR_READ_CACHE):
                if old_key[0] == path:
                    del _BAR_READ_CACHE[old_key]
            _BAR_READ_CACHE[key] = bars
            while len(_BAR_READ_CACHE) > _BAR_READ_CACHE_MAX:
                _BAR_READ_CACHE.popitem(last=False)
            _BAR_READ_INFLIGHT.pop(key, None)
        return bars


def _make_handler(token, state, data_dir, funding_dir, settlements_dir,
                  prices_dir):
    cfg_data = data_dir or os.path.join("data", "bars")
    cfg_funding = _resolve_archive_dir(funding_dir, data_dir, "funding")
    cfg_settlements = _resolve_archive_dir(
        settlements_dir, data_dir, "funding_settlements")
    cfg_prices = _resolve_archive_dir(prices_dir, data_dir, "prices")

    class Handler(BaseHTTPRequestHandler):
        server_version = "AltRadarCollector/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            try:
                text = fmt % args if args else fmt
            except Exception:
                text = fmt
            text = re.sub(r"(?i)bearer\s+[^\s'\"]+", "Bearer [redacted]",
                          text)
            LOG.info("%s - %s", self.address_string(),
                     text.replace("\n", " "))

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
            origin = self.headers.get("Origin")
            if origin and is_allowlisted_origin(origin):
                return origin.strip()
            return None

        def _gate(self):
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
                self._deny(403, "FORBIDDEN", "origin not allowlisted")
                return False
            if not is_loopback_host(self.headers.get("Host")):
                self._deny(403, "FORBIDDEN", "host not loopback",
                           origin=origin)
                return False
            return origin

        def _route(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            qs = parse_qs(parsed.query)

            if self.command == "OPTIONS":
                origin = self.headers.get("Origin")
                if not origin or not is_allowlisted_origin(origin):
                    self._deny(403, "FORBIDDEN", "origin not allowlisted")
                    return
                origin = origin.strip()
                requested = self.headers.get(
                    "Access-Control-Request-Method", "GET")
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

            if self.command == "GET" and path in ("", "/healthz"):
                self._send_json(200, {"ok": True, "alive": True},
                                origin=self._cors_origin())
                return

            gated = self._gate()
            if gated is False:
                return

            if self.command == "GET" and path == "/readyz":
                self._handle_readyz(gated)
                return
            if self.command == "GET" and path == "/collector-health":
                self._send_json(200, envelope(True, state.snapshot()),
                                origin=gated)
                return
            if self.command == "GET" and path == "/bars":
                self._handle_bars(gated, qs, cfg_data)
                return
            if self.command == "GET" and path == "/funding":
                self._handle_funding(gated, qs, cfg_funding)
                return
            if self.command == "GET" and path == "/settlements":
                self._handle_settlements(gated, qs, cfg_settlements)
                return
            if self.command == "GET" and path == "/prices":
                self._handle_prices(gated, qs, cfg_prices)
                return

            self._deny(404, "NOT_FOUND", f"no such route: {path}",
                       origin=gated)

        def _handle_readyz(self, origin):
            snap = state.snapshot()
            ready = (snap["first_success_ts"] is not None
                     and snap["status"] in ("healthy", "degraded"))
            if not ready:
                self._send_json(503, envelope(
                    False, snap, "UNAVAILABLE",
                    "collection has not completed a successful cycle"),
                    origin=origin)
                return
            snap["ready"] = True
            self._send_json(200, envelope(True, snap), origin=origin)

        def _limit(self, qs, origin):
            try:
                limit = int((qs.get("limit") or ["1000"])[0])
            except (TypeError, ValueError):
                self._deny(400, "BAD_REQUEST", "limit must be an integer",
                           origin=origin)
                return None
            if limit < 1 or limit > 5000:
                self._deny(400, "BAD_REQUEST",
                           "limit must be between 1 and 5000", origin=origin)
                return None
            return limit

        def _handle_bars(self, origin, qs, data_dir):
            coin = (qs.get("coin") or [None])[0]
            tf = (qs.get("tf") or [None])[0]
            if not coin or not tf:
                self._deny(400, "BAD_REQUEST",
                           "coin and tf are required", origin=origin)
                return
            if not _SAFE_COIN.match(coin):
                self._deny(400, "BAD_REQUEST", "invalid coin name",
                           origin=origin)
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
            limit = self._limit(qs, origin)
            if limit is None:
                return
            try:
                bars5 = _read_service_bars(data_dir, coin)
            except Exception:
                self._deny(503, "UNAVAILABLE", "bars unavailable",
                           origin=origin)
                return
            now = int(time.time())
            bars = _resample_bars(bars5, ALLOWED_TFS[tf], now)[-limit:]
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
                self._deny(400, "BAD_REQUEST", "coin is required",
                           origin=origin)
                return
            if not _SAFE_COIN.match(coin):
                self._deny(400, "BAD_REQUEST", "invalid coin name",
                           origin=origin)
                return
            if not os.path.exists(os.path.join(funding_dir,
                                               f"{coin}.csv.gz")):
                self._deny(404, "NOT_FOUND", f"no funding for {coin}",
                           origin=origin)
                return
            limit = self._limit(qs, origin)
            if limit is None:
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
                True, {"coin": coin, "rows": rows, "count": len(rows),
                       "data_as_of": last_ts, "last_ts": last_ts}),
                origin=origin)

        def _handle_settlements(self, origin, qs, settlements_dir):
            coin = (qs.get("coin") or [None])[0]
            if not coin or not _SAFE_COIN.match(coin):
                self._deny(400, "BAD_REQUEST", "valid coin is required",
                           origin=origin)
                return
            if not os.path.exists(os.path.join(settlements_dir,
                                               f"{coin}.csv.gz")):
                self._deny(404, "NOT_FOUND", f"no settlements for {coin}",
                           origin=origin)
                return
            limit = self._limit(qs, origin)
            if limit is None:
                return
            try:
                rows = read_settlements(settlements_dir, coin)
            except Exception:
                self._deny(503, "UNAVAILABLE", "settlements unavailable",
                           origin=origin)
                return
            rows = rows[-limit:]
            last_ts = rows[-1]["settle_ts"] if rows else None
            self._send_json(200, envelope(
                True, {"coin": coin, "rows": rows, "count": len(rows),
                       "data_as_of": last_ts, "last_ts": last_ts}),
                origin=origin)

        def _handle_prices(self, origin, qs, prices_dir):
            series = (qs.get("series") or [None])[0]
            if series not in _PRICE_SERIES:
                self._deny(400, "BAD_REQUEST",
                           "series must be mark or index", origin=origin)
                return
            coin = (qs.get("coin") or [None])[0]
            if not coin or not _SAFE_COIN.match(coin):
                self._deny(400, "BAD_REQUEST", "valid coin is required",
                           origin=origin)
                return
            if not os.path.exists(os.path.join(prices_dir, series,
                                               f"{coin}.csv.gz")):
                self._deny(404, "NOT_FOUND",
                           f"no prices for {series} {coin}", origin=origin)
                return
            limit = self._limit(qs, origin)
            if limit is None:
                return
            try:
                rows = read_prices(prices_dir, series, coin)
            except Exception:
                self._deny(503, "UNAVAILABLE", "prices unavailable",
                           origin=origin)
                return
            rows = rows[-limit:]
            last_ts = rows[-1]["ts"] if rows else None
            self._send_json(200, envelope(
                True, {"series": series, "coin": coin, "rows": rows,
                       "count": len(rows), "data_as_of": last_ts,
                       "last_ts": last_ts}), origin=origin)

        def do_GET(self):
            self._route()

        def do_POST(self):
            self._route()

        def do_OPTIONS(self):
            self._route()

    return Handler


def serve(port, token, state, data_dir=None, funding_dir=None,
          settlements_dir=None, prices_dir=None):
    handler = _make_handler(token, state, data_dir, funding_dir,
                            settlements_dir, prices_dir)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.05},
                              daemon=True, name="alt-radar-service")
    thread.start()
    LOG.info("service listening on 127.0.0.1:%d",
             server.server_address[1])
    return server


def serve_with_fallback(port, token, state, attempts=PORT_SCAN_ATTEMPTS,
                        **kw):
    last = None
    for candidate in range(port, port + attempts):
        try:
            server = serve(candidate, token, state, **kw)
            return server, server.server_address[1]
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
            last = e
    raise last


def _acquire_lock(path):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fh = open(path, "a+b")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise RuntimeError(f"another collector is already running (lock {path})")
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n".encode("utf-8"))
        fh.flush()
    except OSError:
        pass
    return fh


def install_signal_handlers(stop_event):
    def _handle(signum, _frame):
        print(f"service: caught signal {signum}; shutting down", flush=True)
        stop_event.set()

    old = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            old[sig] = signal.signal(sig, _handle)
        except (ValueError, OSError, RuntimeError):
            pass
    return old


def restore_signal_handlers(old):
    for sig, handler in old.items():
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError, RuntimeError):
            pass


def run_service(*, port=DEFAULT_PORT, token, data_dir=None,
                funding_dir=None, settlements_dir=None, prices_dir=None,
                every_s=300, lock_path=None, stop_event=None,
                install_signals=True, on_serving=None):
    stop = stop_event or threading.Event()
    data_dir = data_dir or os.path.join("data", "bars")
    funding_dir = _resolve_archive_dir(funding_dir, data_dir, "funding")
    settlements_dir = _resolve_archive_dir(
        settlements_dir, data_dir, "funding_settlements")
    prices_dir = _resolve_archive_dir(prices_dir, data_dir, "prices")
    lock_path = lock_path or os.path.join(
        os.path.dirname(os.path.abspath(data_dir)), COLLECTOR_LOCK_NAME)
    try:
        lock_fh = _acquire_lock(lock_path)
    except RuntimeError as e:
        print(f"service: refused: {e}", file=sys.stderr, flush=True)
        return {"ok": False, "refused": True, "exit_code": 1}

    old_handlers = install_signal_handlers(stop) if install_signals else {}
    state = collection.CollectionState(
        data_dir, funding_dir, settlements_dir, prices_dir, every_s)
    try:
        server, bound = serve_with_fallback(
            port, token, state, data_dir=data_dir, funding_dir=funding_dir,
            settlements_dir=settlements_dir, prices_dir=prices_dir)
        print(f"service: listening on 127.0.0.1:{bound} "
              f"data={data_dir} lock={lock_path}", flush=True)
        if on_serving is not None:
            on_serving(server, bound)

        def _loop():
            collection.run_collection_loop(
                state, stop_event=stop, data_dir=data_dir,
                funding_dir=funding_dir, settlements_dir=settlements_dir,
                prices_dir=prices_dir)

        loop_thread = threading.Thread(target=_loop, name="collection-loop",
                                       daemon=True)
        loop_thread.start()
        try:
            while loop_thread.is_alive():
                if stop.wait(0.25):
                    break
            stop.set()
        except KeyboardInterrupt:
            stop.set()
        loop_thread.join()
        server.shutdown()
        server.server_close()
        print("service: stopped cleanly", flush=True)
        return {"ok": True, "refused": False, "exit_code": 0,
                "port": bound, "shutdown_order": ["loop", "server"]}
    finally:
        if old_handlers:
            restore_signal_handlers(old_handlers)
        try:
            lock_fh.close()
        except OSError:
            pass


def build_parser():
    ap = argparse.ArgumentParser(
        description="ALT RADAR collection-only service (bars + neutral "
                    "archive API; no scanner)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="first loopback port to try (default 18741)")
    ap.add_argument("--token", default=None,
                    help="per-launch bearer token (otherwise the token file)")
    ap.add_argument("--token-file", default=DEFAULT_TOKEN_FILE)
    ap.add_argument("--lock-file", default=None,
                    help="collector lockfile (default: data/collector.lock)")
    ap.add_argument("--data-dir", default=None,
                    help="bar archive directory (default data/bars)")
    ap.add_argument("--funding-dir", default=None)
    ap.add_argument("--settlements-dir", default=None)
    ap.add_argument("--prices-dir", default=None)
    ap.add_argument("--collect-every", type=int, default=300,
                    help="seconds between collection cycles")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    token = args.token or load_or_create_token(args.token_file)
    result = run_service(
        port=args.port, token=token, data_dir=args.data_dir,
        funding_dir=args.funding_dir,
        settlements_dir=args.settlements_dir, prices_dir=args.prices_dir,
        every_s=args.collect_every, lock_path=args.lock_file)
    return int(result.get("exit_code", 0))


if __name__ == "__main__":
    raise SystemExit(main())
