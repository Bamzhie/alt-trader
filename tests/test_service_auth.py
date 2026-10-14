"""Task 1: server skeleton — token auth, origin/host policy, CORS, redaction.

Self-checking script (stdlib only). Talks to 127.0.0.1 ONLY: an egress
tripwire wraps socket connects and fails any connect outside loopback.

Run:  python3 tests/test_service_auth.py   -> ALL PASS, exit 0
"""

import http.client
import io
import json
import os
import socket
import stat
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# ---- egress tripwire: loopback-only (allows 127.0.0.1 / localhost / ::1) --
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class NetworkTripwire(AssertionError):
    """A test tried to connect outside loopback — instant failure."""


_RealSocket = socket.socket


class _GuardedSocket(_RealSocket):
    def connect(self, address, *args, **kw):
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOOPBACK_HOSTS:
            raise NetworkTripwire(
                f"NETWORK TRIPWIRE: connect to non-loopback {host!r} blocked")
        return super().connect(address, *args, **kw)


_RealCreateConnection = socket.create_connection


def _guarded_create_connection(address, *args, **kw):
    host = address[0] if isinstance(address, tuple) else address
    if host not in LOOPBACK_HOSTS:
        raise NetworkTripwire(
            f"NETWORK TRIPWIRE: create_connection to non-loopback {host!r}")
    return _RealCreateConnection(address, *args, **kw)


socket.socket = _GuardedSocket
socket.create_connection = _guarded_create_connection

FAILURES = []
CHECKS = 0


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    cond = bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)
    return cond


TOKEN = "bearer-secret-task1-xyz"


def req(port, method, path, token=None, origin=None, host=None,
        preflight=False):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if origin is not None:
        headers["Origin"] = origin
    if host is not None:
        headers["Host"] = host
    if preflight:
        headers["Access-Control-Request-Method"] = "GET"
    conn.request(method, path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    cors = resp.getheader("Access-Control-Allow-Origin")
    conn.close()
    return resp.status, body, cors


def raw_request(port, raw):
    """Send a hand-built HTTP request (for Host-mismatch probing)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(10)
    s.connect(("127.0.0.1", port))
    s.sendall(raw)
    chunks = []
    while True:
        try:
            data = s.recv(4096)
        except socket.timeout:
            break
        if not data:
            break
        chunks.append(data)
    s.close()
    head = b"".join(chunks).split(b"\r\n\r\n", 1)[0].decode("latin1")
    status = int(head.split(" ", 2)[1])
    return status, head


def main():
    from proto import service as svc

    server = svc.serve(0, TOKEN, lambda: {})
    port = server.server_address[1]
    check("server binds loopback only",
          server.server_address[0] == "127.0.0.1",
          str(server.server_address))
    time.sleep(0.1)

    # Capture everything the server logs while we exercise it.
    logbuf = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = logbuf
    try:
        # -- auth matrix --------------------------------------------------
        st, body, _ = req(port, "GET", "/healthz")
        ok_body = False
        try:
            ok_body = json.loads(body) == {"ok": True, "alive": True}
        except ValueError:
            pass
        check("GET /healthz no-auth -> 200", st == 200, f"got {st}")
        check("GET /healthz body", ok_body, body[:120])

        st, _, cors = req(port, "GET", "/healthz",
                          origin="http://localhost:1420")
        check("GET /healthz echoes allowlisted Origin",
              st == 200 and cors == "http://localhost:1420", str(cors))

        st, _, _ = req(port, "GET", "/snapshot")
        check("GET /snapshot no token -> 401", st == 401, f"got {st}")

        st, _, _ = req(port, "GET", "/snapshot", token="wrong-token")
        check("GET /snapshot bad token -> 401", st == 401, f"got {st}")

        st, _, cors = req(port, "GET", "/snapshot", token="wrong-token",
                          origin="http://localhost:1420")
        check("401 echoes allowlisted Origin",
              st == 401 and cors == "http://localhost:1420", str(cors))

        st, _, _ = req(port, "GET", "/snapshot", token=TOKEN)
        check("GET /snapshot good token -> 200", st == 200, f"got {st}")

        # -- origin / host policy (authed routes) -------------------------
        st, _, _ = req(port, "POST", "/commands/pause", token=TOKEN,
                       origin="https://evil.example")
        check("POST /commands/pause bad Origin -> 403", st == 403,
              f"got {st}")

        st, _, _ = req(port, "GET", "/snapshot", token=TOKEN,
                       origin="http://localhost:1420")
        check("GET /snapshot allowlisted Origin -> 200", st == 200,
              f"got {st}")

        raw = (f"GET /snapshot HTTP/1.1\r\nHost: evil.example\r\n"
               f"Authorization: Bearer {TOKEN}\r\n"
               f"Connection: close\r\n\r\n").encode()
        st, _ = raw_request(port, raw)
        check("GET /snapshot mismatched Host -> 403", st == 403,
              f"got {st}")

        # -- CORS preflight -----------------------------------------------
        st, _, cors = req(port, "OPTIONS", "/snapshot",
                          origin="http://localhost:1420", preflight=True)
        check("preflight allowlisted origin -> 2xx", 200 <= st < 300,
              f"got {st}")
        check("preflight echoes allowlisted origin",
              cors == "http://localhost:1420", str(cors))

        st, _, _ = req(port, "OPTIONS", "/snapshot",
                       origin="https://evil.example", preflight=True)
        check("preflight foreign origin -> 403", st == 403, f"got {st}")

        # -- token file helper --------------------------------------------
        tmp = tempfile.mkdtemp(prefix="svc-token-")
        tpath = os.path.join(tmp, "service.token")
        t1 = svc.load_or_create_token(tpath)
        mode = stat.S_IMODE(os.stat(tpath).st_mode)
        check("token file created 0600", mode == 0o600, oct(mode))
        t2 = svc.load_or_create_token(tpath)
        check("token file reloads same token",
              t1 == t2 and len(t1) >= 32, repr(t1)[:20])
    finally:
        sys.stderr = old_stderr
        server.shutdown()
        server.server_close()

    logs = logbuf.getvalue()
    check("no token value in server logs", TOKEN not in logs,
          logs[-300:] if TOKEN in logs else "")
    check("no Authorization header value in server logs",
          "Bearer" not in logs, logs[-300:] if "Bearer" in logs else "")

    # -- tripwire self-check: non-loopback connect must raise -------------
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
        s.close()
        check("tripwire blocks non-loopback connect", False,
              "connect succeeded")
    except NetworkTripwire:
        check("tripwire blocks non-loopback connect", True)
    except Exception as e:  # pragma: no cover — must never reach the network
        check("tripwire blocks non-loopback connect", False, repr(e))

    print()
    if FAILURES:
        print(f"FAIL  tests/test_service_auth.py  "
              f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
        sys.exit(1)
    print(f"ALL PASS  tests/test_service_auth.py  ({CHECKS} checks)")


if __name__ == "__main__":
    main()
