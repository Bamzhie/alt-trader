"""Task 4: idempotent pause/resume/trigger commands, meta-persisted.

Self-checking script (stdlib only). Talks to 127.0.0.1 ONLY: an egress
tripwire wraps socket connects and fails any connect outside loopback.
Fixture is a temp DB via REAL writers (watcher.emit_setup) — never
hand-built JSON. Flags are read back from the SAME meta keys the Qt
panels read (watcher_feed.PAUSED_KEY / DISABLED_PREFIX).

Run:  python3 tests/test_service_commands.py   -> ALL PASS, exit 0
"""

import http.client
import json
import os
import socket
import sqlite3
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


TOKEN = "bearer-secret-task4-commands"


def post(port, path, body=None, token=TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    raw = json.dumps(body if body is not None else {}).encode()
    conn.request("POST", path, body=raw, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        payload = json.loads(data)
    except ValueError:
        payload = None
    return resp.status, payload


def flags(db_path):
    """Paused/disabled straight from the meta keys the Qt panels read."""
    from proto import watcher_feed as feed
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        paused, disabled = feed.get_flags(conn)
    finally:
        conn.close()
    return paused, disabled


def main():
    from proto import service as svc
    from proto.store import Store
    from proto import watcher as watchmod
    from proto import watcher_feed as feed

    tmp = tempfile.mkdtemp(prefix="svc-commands-")
    db_path = os.path.join(tmp, "signals.db")
    store = Store(db_path)

    # Fixture via the REAL emitter: rule t1-break-v1 is a known trigger.
    base = 1700000000 - (1700000000 % 300)
    watchmod.emit_setup(store.conn, {
        "rule_id": "t1-break-v1", "coin": "RICH", "src_bar_ts": base,
        "entry": 100.0, "direction": "LONG", "src": "t1_break"})
    store.conn.commit()
    store.conn.close()

    server = svc.serve(0, TOKEN, lambda: {}, store_path=db_path,
                       data_dir=os.path.join(tmp, "bars"))
    port = server.server_address[1]
    time.sleep(0.15)
    try:
        # -- idempotent pause -------------------------------------------
        st1, b1 = post(port, "/commands/pause")
        st2, b2 = post(port, "/commands/pause")
        check("pause -> 200", st1 == 200, f"got {st1}")
        check("pause repeated -> 200 same state", st2 == 200
              and (b1.get("data") if b1 else None)
              == (b2.get("data") if b2 else None),
              f"{st2} {str(b2)[:160]}")
        paused, _ = flags(db_path)
        check("pause persists watcher_paused=1",
              paused is True, f"paused={paused!r}")
        check("pause body carries paused True",
              isinstance(b2, dict) and (b2.get("data") or {}).get("paused")
              is True, str(b2)[:160])

        # -- idempotent resume ------------------------------------------
        st1, _ = post(port, "/commands/resume")
        st2, b2 = post(port, "/commands/resume")
        check("resume x2 -> 200", st1 == 200 and st2 == 200,
              f"got {st1}/{st2}")
        paused, _ = flags(db_path)
        check("resume persists watcher_paused=0",
              paused is False, f"paused={paused!r}")

        # -- trigger disable/enable --------------------------------------
        st1, b1 = post(port, "/commands/disable-trigger",
                       {"rule": "t1-break-v1"})
        st2, b2 = post(port, "/commands/disable-trigger",
                       {"rule": "t1-break-v1"})
        check("disable-trigger -> 200", st1 == 200, f"got {st1}")
        check("disable-trigger idempotent (same state)",
              st2 == 200 and (b1.get("data") if b1 else None)
              == (b2.get("data") if b2 else None),
              f"{st2} {str(b2)[:160]}")
        _, disabled = flags(db_path)
        check("disable persists watcher_disabled:t1-break-v1",
              tuple(disabled) == ("t1-break-v1",), repr(disabled))
        st1, _ = post(port, "/commands/enable-trigger",
                      {"rule": "t1-break-v1"})
        st2, _ = post(port, "/commands/enable-trigger",
                      {"rule": "t1-break-v1"})
        check("enable-trigger x2 -> 200", st1 == 200 and st2 == 200,
              f"got {st1}/{st2}")
        _, disabled = flags(db_path)
        check("enable clears the disable key",
              tuple(disabled) == (), repr(disabled))

        # -- validation ----------------------------------------------------
        st, body = post(port, "/commands/disable-trigger",
                        {"rule": "no_such_rule"})
        check("disable unknown rule -> 400 BAD_REQUEST",
              st == 400 and isinstance(body, dict)
              and (body.get("error") or {}).get("code") == "BAD_REQUEST",
              f"got {st} {str(body)[:120]}")
        st, body = post(port, "/commands/disable-trigger", {})
        check("disable missing rule -> 400 BAD_REQUEST",
              st == 400 and isinstance(body, dict)
              and (body.get("error") or {}).get("code") == "BAD_REQUEST",
              f"got {st}")
        st, body = post(port, "/commands/nuke", {})
        check("unknown command -> 404 NOT_FOUND",
              st == 404 and isinstance(body, dict)
              and (body.get("error") or {}).get("code") == "NOT_FOUND",
              f"got {st}")
        st, _ = post(port, "/commands/pause", token=None)
        check("commands require auth -> 401", st == 401, f"got {st}")
        st, _ = post(port, "/commands/pause", token="wrong")
        check("commands bad token -> 401", st == 401, f"got {st}")

        # -- reload-safe: fresh connection sees the same meta -------------
        post(port, "/commands/pause")
        post(port, "/commands/disable-trigger", {"rule": "t1-break-v1"})
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            paused_row = conn.execute(
                "SELECT value FROM meta WHERE key=?",
                (feed.PAUSED_KEY,)).fetchone()
            dis_row = conn.execute(
                "SELECT value FROM meta WHERE key=?",
                (feed.DISABLED_PREFIX + "t1-break-v1",)).fetchone()
        finally:
            conn.close()
        check("reload-safe paused key",
              paused_row is not None and paused_row[0] == "1",
              repr(paused_row))
        check("reload-safe disable key",
              dis_row is not None and dis_row[0] == "1", repr(dis_row))
    finally:
        server.shutdown()
        server.server_close()

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
        print(f"FAIL  tests/test_service_commands.py  "
              f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
        sys.exit(1)
    print(f"ALL PASS  tests/test_service_commands.py  ({CHECKS} checks)")


if __name__ == "__main__":
    main()
