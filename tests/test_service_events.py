"""Task 3: transition-only SSE with snapshot reconcile.

Self-checking script (stdlib only). Talks to 127.0.0.1 ONLY: an egress
tripwire wraps socket connects and fails any connect outside loopback.

Fixtures use a temp DB via REAL writers (watcher.emit_setup / eval_bar,
service.record_sweep_completed) — never hand-built JSON.

Contract (mirrors app/dev/mock_service.py wire format):
- GET /events SSE frames `id: / event: / data:` with zero-padded width-6
  monotonic string ids; kinds setup.emitted/invalidated/expired/resolved,
  monitoring.unavailable/ok, sweep.completed — transitions ONLY.
- Recovery is subscribe-first-then-reconcile: open the stream with
  Last-Event-ID, then fetch GET /snapshot carrying snapshot_event_id
  (the ring head); events <= snapshot id are dropped, newer applied.
- Cursor below the ring-buffer floor -> server sends `resync` first.
- Ring keeps the last 500 events / 30-minute window.

Run:  python3 tests/test_service_events.py   -> ALL PASS, exit 0
"""

import http.client
import json
import os
import socket
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


TOKEN = "bearer-secret-task3-events"


def get(port, path, token=TOKEN, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = dict(headers or {})
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    conn.request("GET", path, headers=hdrs)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    return resp.status, payload


def snapshot_id(port):
    st, body = get(port, "/snapshot")
    assert st == 200, f"/snapshot -> {st}"
    return body["data"]["snapshot_event_id"]


def parse_frame(raw):
    """One SSE frame -> {id, event, data} (None for comments/keepalives)."""
    fid, kind, data_lines = None, "message", []
    for line in raw.split("\n"):
        line = line.rstrip("\r")
        if line == "" or line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "id":
            fid = value
        elif field == "event":
            kind = value
        elif field == "data":
            data_lines.append(value)
    if fid is None:
        return None
    data = {}
    if data_lines:
        try:
            data = json.loads("\n".join(data_lines))
        except ValueError:
            data = {}
    return {"id": fid, "event": kind, "data": data}


class SSEStream:
    """Raw-socket SSE reader: status line, then incremental frame polling."""

    def __init__(self, port, last_id=None, token=TOKEN):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        lines = ["GET /events HTTP/1.1", "Host: 127.0.0.1",
                 f"Authorization: Bearer {token}",
                 "Accept: text/event-stream", "Connection: keep-alive"]
        if last_id is not None:
            lines.append(f"Last-Event-ID: {last_id}")
        if token is None:
            lines = [l for l in lines if not l.startswith("Authorization")]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        head = self._read_until(b"\r\n\r\n", timeout=10)
        self.status = int(head.split(b"\r\n")[0].split()[1])
        self.frames = []

    def _read_until(self, marker, timeout):
        if not hasattr(self, "buf"):
            self.buf = b""
        self.sock.settimeout(timeout)
        while marker not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                break
            self.buf += chunk
        head, _, rest = self.buf.partition(marker)
        self.buf = rest
        return head + marker

    def poll(self, duration=0.5):
        """Collect complete frames arriving within duration seconds."""
        if not hasattr(self, "buf"):
            self.buf = b""
        new = []
        end = time.time() + duration
        while time.time() < end:
            self.sock.settimeout(max(0.05, end - time.time()))
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            self.buf += chunk
            while b"\n\n" in self.buf:
                raw, self.buf = self.buf.split(b"\n\n", 1)
                frame = parse_frame(raw.decode("utf-8", "replace"))
                if frame is not None:
                    new.append(frame)
        self.frames.extend(new)
        return new

    def wait_for(self, pred, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            got = self.poll(min(0.3, max(0.05, end - time.time())))
            for frame in got:
                if pred(frame):
                    return frame
        return None

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


BASE = 1700000000 - (1700000000 % 300)  # aligned, long closed
NOW = BASE + 400 * 300


def live_bar(ts, entry=100.0):
    """Closed bar that touches neither stop (98) nor target (105)."""
    return {"ts": ts, "o": entry, "h": entry + 1.0, "l": entry - 1.0,
            "c": entry, "vol": 10.0, "amount": 1000.0}


def emit(store_conn, watchmod, coin, entry=100.0):
    return watchmod.emit_setup(store_conn, {
        "rule_id": "t1-break-v1", "coin": coin, "src_bar_ts": BASE,
        "entry": entry, "direction": "LONG", "src": "t1_break",
        "b_stop": 0.02, "a_tgt": 0.05})


def main():
    from proto import service as svc
    from proto.store import Store
    from proto import watcher as watchmod

    tmp = tempfile.mkdtemp(prefix="svc-events-")
    db_path = os.path.join(tmp, "signals.db")
    data_dir = os.path.join(tmp, "bars")
    os.makedirs(data_dir, exist_ok=True)
    store = Store(db_path)
    svc.reset_events()

    server = svc.serve(0, TOKEN, lambda: {},
                       store_path=db_path, data_dir=data_dir,
                       is_ready=lambda: True)
    port = server.server_address[1]
    time.sleep(0.15)
    try:
        # -- auth gate on the stream --------------------------------------
        st, _ = get(port, "/events", token=None)
        check("events no token -> 401", st == 401, f"got {st}")

        # -- head starts at zero, zero-padded ------------------------------
        head0 = snapshot_id(port)
        check("snapshot_event_id starts at 000000",
              head0 == "000000", repr(head0))

        # -- transition-only: refresh traffic emits nothing ----------------
        sid = emit(store.conn, watchmod, "STEADY")
        head_after_emit = snapshot_id(port)
        for _ in range(50):
            get(port, "/snapshot")
            get(port, "/setups")
            get(port, "/readyz")
        for i in range(20):
            state, mon = watchmod.eval_bar(
                store.conn, sid, live_bar(BASE + 300 + i * 300), {"now": NOW})
            assert (state, mon) == ("active", "ok"), (state, mon)
        check("50 refreshes + 20 live bars advance head by nothing",
              snapshot_id(port) == head_after_emit,
              f"{head_after_emit} -> {snapshot_id(port)}")
        stream = SSEStream(port, last_id=head_after_emit)
        try:
            check("stream opened 200", stream.status == 200,
                  f"got {stream.status}")
            check("no per-refresh events on the wire",
                  stream.poll(0.7) == [], "frames arrived")
        finally:
            stream.close()

        # -- full kind coverage via real writers ---------------------------
        stream = SSEStream(port, last_id=snapshot_id(port))
        try:
            k1 = emit(store.conn, watchmod, "KINDS1")
            f = stream.wait_for(lambda fr: fr["event"] == "setup.emitted"
                                and fr["data"].get("setup_id") == k1)
            check("setup.emitted on the wire", f is not None)
            check("emitted frame carries coin",
                  f is not None and f["data"].get("coin") == "KINDS1",
                  str(f)[:160])

            watchmod.eval_bar(store.conn, k1, None,
                              {"bar_ts": BASE + 900, "missing": True})
            f = stream.wait_for(
                lambda fr: fr["event"] == "monitoring.unavailable")
            check("monitoring.unavailable on gap bar", f is not None)

            watchmod.eval_bar(store.conn, k1, live_bar(BASE + 1200),
                              {"now": NOW})
            f = stream.wait_for(lambda fr: fr["event"] == "monitoring.ok")
            check("monitoring.ok on recovery bar", f is not None)

            watchmod.eval_bar(store.conn, k1, live_bar(BASE + 1500),
                              {"now": NOW, "invalidate": True})
            f = stream.wait_for(
                lambda fr: fr["event"] == "setup.invalidated"
                and fr["data"].get("setup_id") == k1)
            check("setup.invalidated on thesis break", f is not None)

            k2 = emit(store.conn, watchmod, "KINDS2")
            stream.wait_for(lambda fr: fr["event"] == "setup.emitted"
                            and fr["data"].get("setup_id") == k2)
            stop_bar = dict(live_bar(BASE + 1800))
            stop_bar.update({"l": 90.0, "h": 99.0, "c": 98.5})
            watchmod.eval_bar(store.conn, k2, stop_bar, {"now": NOW})
            f = stream.wait_for(lambda fr: fr["event"] == "setup.resolved"
                                and fr["data"].get("setup_id") == k2)
            check("setup.resolved on stop touch", f is not None)

            k3 = emit(store.conn, watchmod, "KINDS3")
            stream.wait_for(lambda fr: fr["event"] == "setup.emitted"
                            and fr["data"].get("setup_id") == k3)
            watchmod.eval_bar(store.conn, k3, live_bar(BASE + 2100),
                              {"now": NOW, "age_bars": 999})
            f = stream.wait_for(lambda fr: fr["event"] == "setup.expired"
                                and fr["data"].get("setup_id") == k3)
            check("setup.expired on time-bar", f is not None)

            svc.record_sweep_completed(eligible_count=9, evaluated_count=9)
            f = stream.wait_for(
                lambda fr: fr["event"] == "sweep.completed")
            check("sweep.completed on the wire", f is not None)

            ids = [fr["id"] for fr in stream.frames]
            check("ids monotonic zero-padded width-6",
                  ids == sorted(ids) and len(set(ids)) == len(ids)
                  and all(len(i) == 6 and i.isdigit() for i in ids), ids[:8])
        finally:
            stream.close()

        # -- mid-recovery transition is never lost --------------------------
        before = snapshot_id(port)
        stream = SSEStream(port, last_id=before)
        try:
            k4 = emit(store.conn, watchmod, "MIDRECOVERY")
            after = snapshot_id(port)
            check("snapshot advanced past subscribe point",
                  after > before, f"{before} -> {after}")
            # Reconcile: drop events <= snapshot id, apply newer ones; the
            # injected transition must be present on the stream either way.
            got = stream.wait_for(
                lambda fr: fr["data"].get("setup_id") == k4, timeout=5.0)
            check("transition between subscribe and snapshot not lost",
                  got is not None)
            check("injected event covered by the new snapshot",
                  got is not None and got["id"] <= after,
                  str(got)[:160])
            applied = [fr for fr in stream.frames if fr["id"] > after]
            check("nothing newer than snapshot to apply",
                  applied == [], str(applied)[:160])
        finally:
            stream.close()

        # -- stale cursor resyncs -------------------------------------------
        ancient = snapshot_id(port)
        for i in range(505):
            emit(store.conn, watchmod, f"FLOOD{i:04d}")
        check("ring evicted the ancient cursor",
              snapshot_id(port) > ancient)
        stream = SSEStream(port, last_id=ancient)
        try:
            first = stream.wait_for(lambda fr: True, timeout=5.0)
            check("stale cursor -> resync first",
                  first is not None and first["event"] == "resync",
                  str(first)[:200])
        finally:
            stream.close()

        # -- unparseable cursor replays retained, no resync ------------------
        stream = SSEStream(port, last_id="not-an-id")
        try:
            frames = stream.poll(1.0)
            check("unparseable cursor replays without resync",
                  len(frames) > 0
                  and all(fr["event"] != "resync" for fr in frames),
                  str(frames[:2])[:200])
        finally:
            stream.close()
    finally:
        server.shutdown()
        server.server_close()
        store.close()

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
        print(f"FAIL  tests/test_service_events.py  "
              f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
        sys.exit(1)
    print(f"ALL PASS  tests/test_service_events.py  ({CHECKS} checks)")


if __name__ == "__main__":
    main()
