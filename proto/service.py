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
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


def _make_handler(token, ready):
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
            path = self.path.split("?", 1)[0].rstrip("/") or "/"

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
                try:
                    data = ready() if callable(ready) else {}
                except Exception as e:  # ready must never 500 the gate
                    LOG.warning("ready callback failed: %s",
                                type(e).__name__)
                    data = {}
                self._send_json(200, envelope(True, data), origin=gated)
                return

            if self.command == "POST" and path == "/commands/pause":
                # Stub: real pause/resume semantics arrive with Task 4.
                self._send_json(200, envelope(True, {"paused": False}),
                                origin=gated)
                return

            self._deny(404, "NOT_FOUND",
                       f"no such route: {path}", origin=gated)

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


def serve(port, token, ready):
    """Bind 127.0.0.1:port and serve in a daemon thread; return the server."""
    handler = _make_handler(token, ready)
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
