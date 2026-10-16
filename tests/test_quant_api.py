"""Task 3: GET /quant-status + quant.shadow SSE kind.

Self-checking script (stdlib only). Talks to 127.0.0.1 ONLY: an egress
tripwire wraps socket connects and fails any connect outside loopback.

Fixtures use a temp quant.db via REAL writers (QuantStore.insert_event /
resolve / bump_suppressed / set_meta / set_watermark, gates.record) — never
hand-built JSON.

Contract (plan binding section "Availability states and watcher-to-SSE"):
- GET /quant-status needs the bearer gate (401 without token, UNAUTH).
- 200 shape on a seeded db: {mode shadow, status, paused, config_hash,
  event_counts, suppression_top, promotions}; counts match the db.
- Empty db -> 200 with explicit zeros + insufficient-evidence reason,
  status unseen, never 404.
- Unreadable quant.db path -> 503 UNAVAILABLE (explicit, never a guess).
- quant.shadow is a registered EVENT_KIND carrying counts only (no levels,
  no advice); it flows through the unchanged ring / Last-Event-ID /
  resync machinery in order with the other kinds.

Run:  python3 tests/test_quant_api.py   -> ALL PASS, exit 0
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


TOKEN = "bearer-secret-task3-quant"


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


BASE_TS = 1788000000 - (1788000000 % 300)  # aligned closed 5m bar ts


def seed_event(store, rule_id, chash, coin, bar_ts, mode="SHADOW"):
    return store.insert_event({
        "rule_id": rule_id, "config_hash": chash, "coin": coin,
        "side": 1, "bar_ts": bar_ts, "mode": mode, "role": "candidate",
        "ref_price": 100.0, "stop": 98.0, "tp": 102.0, "stop_frac": 0.02,
        "horizon": 144, "r_tp": 1.0, "cost_frac": 0.0013,
        "sigma": 0.004, "qv24": 5e6, "features": {"rvol": 4.5}})


def main():
    from proto import service as svc
    from proto.quant.qstore import QuantStore
    from proto.quant import gates as gatesmod
    from proto.quant.config import DEFAULT
    from proto.quant.registry import REGISTERED

    CHASH = DEFAULT.config_hash()
    RULE = REGISTERED[0].rule_id

    tmp = tempfile.mkdtemp(prefix="svc-quant-")
    sig_db = os.path.join(tmp, "signals.db")
    bars_dir = os.path.join(tmp, "bars")
    os.makedirs(bars_dir, exist_ok=True)
    # signals db must exist so unrelated routes keep working; the handler
    # under test only reads the quant db.
    open(sig_db, "a").close()

    # -- seeded quant db via real writers ---------------------------------
    qpath = os.path.join(tmp, "quant.db")
    store = QuantStore(qpath)
    e1 = seed_event(store, RULE, CHASH, "ETH", BASE_TS)
    e2 = seed_event(store, RULE, CHASH, "SOL", BASE_TS + 300)
    assert e1 is not None and e2 is not None, "seed inserts collided"
    store.resolve(e1, "TIME", 100.0, BASE_TS + 144 * 300, 144, 0.001, 0.05)
    store.bump_suppressed("2026-10-01", RULE, "cooldown", n=3)
    store.bump_suppressed("2026-10-01", RULE, "cost_r", n=7)
    store.set_watermark("BTC", BASE_TS + 600)
    store.commit()
    gatesmod.record(tmp, RULE, CHASH, True, [],
                    {"n": 250, "days": 30, "coins": 20,
                     "mean_r": 0.10, "ci_lo": 0.02, "ci_hi": 0.18})
    store.close()

    svc.reset_events()
    server = svc.serve(0, TOKEN, lambda: {}, store_path=sig_db,
                       data_dir=bars_dir, quant_db=qpath,
                       is_ready=lambda: True)
    port = server.server_address[1]
    time.sleep(0.15)
    try:
        # -- bearer gate --------------------------------------------------
        st, body = get(port, "/quant-status", token=None)
        check("quant-status no token -> 401",
              st == 401, f"got {st}")
        check("quant-status 401 carries UNAUTH",
              isinstance(body, dict) and (body.get("error") or {}).get(
                  "code") == "UNAUTH", str(body)[:160])

        # -- seeded shape ---------------------------------------------------
        st, body = get(port, "/quant-status")
        check("quant-status seeded -> 200", st == 200, f"got {st}")
        data = (body or {}).get("data") or {}
        check("quant-status mode is shadow",
              data.get("mode") == "shadow", str(data)[:200])
        check("quant-status carries config hash",
              data.get("config_hash") == CHASH,
              str(data.get("config_hash")))
        ec = data.get("event_counts") or {}
        check("quant-status event counts match db",
              ec.get("total") == 2 and ec.get("emitted") == 1
              and ec.get("resolved") == 1, str(ec))
        top = data.get("suppression_top") or []
        check("quant-status suppression top reasons",
              any(r.get("reason") == "cost_r" and r.get("n") == 7
                  for r in top)
              and any(r.get("reason") == "cooldown" for r in top),
              str(top)[:200])
        promos = data.get("promotions") or []
        check("quant-status promotions carry passing record",
              any(p.get("rule_id") == RULE
                  and p.get("config_hash") == CHASH for p in promos),
              str(promos)[:200])
        check("quant-status healthy when unpaused with watermark",
              data.get("status") == "healthy"
              and data.get("paused") is False, str(data)[:200])
        check("quant-status seeded reason is null",
              data.get("reason") is None, str(data.get("reason")))

        # -- paused flag surfaces -------------------------------------------
        pstore = QuantStore(qpath)
        pstore.set_meta("pause", "1")
        pstore.close()
        st, body = get(port, "/quant-status")
        pdata = (body or {}).get("data") or {}
        check("quant-status paused flag surfaces",
              st == 200 and pdata.get("paused") is True
              and pdata.get("status") == "paused",
              str(pdata)[:200])
        pstore = QuantStore(qpath)
        pstore.set_meta("pause", "0")
        pstore.close()

        # -- ACTIONABLE legacy rows are never counted as shadow -----------
        astore = QuantStore(qpath)
        aid = seed_event(astore, RULE, CHASH, "DOGE", BASE_TS + 900,
                         mode="ACTIONABLE")
        assert aid is not None, "actionable seed insert collided"
        astore.commit()
        astore.close()
        st, body = get(port, "/quant-status")
        adata = (body or {}).get("data") or {}
        aec = adata.get("event_counts") or {}
        check("ACTIONABLE rows excluded from shadow counts",
              st == 200 and aec.get("total") == 2
              and aec.get("emitted") == 1 and aec.get("resolved") == 1,
              f"{st} {str(aec)[:200]}")
        check("ACTIONABLE rows reported separately, never as shadow",
              adata.get("actionable_events") == 1
              and adata.get("mode") == "shadow",
              str({k: adata.get(k) for k in ("actionable_events", "mode")}))

        # -- empty db: zeros + insufficient-evidence, never 404 -------------
        # Own subdir: promotions.json lives beside the db (promo_dir), so
        # a shared dir would leak the seeded promotion into this case.
        empty_dir = os.path.join(tmp, "empty")
        os.makedirs(empty_dir, exist_ok=True)
        empty_path = os.path.join(empty_dir, "empty-quant.db")
        QuantStore(empty_path).close()
        empty_server = svc.serve(0, TOKEN, lambda: {}, store_path=sig_db,
                                 data_dir=bars_dir, quant_db=empty_path,
                                 is_ready=lambda: True)
        eport = empty_server.server_address[1]
        time.sleep(0.1)
        try:
            st, body = get(eport, "/quant-status")
            edata = (body or {}).get("data") or {}
            eec = edata.get("event_counts") or {}
            check("quant-status empty db -> 200 zeros",
                  st == 200 and eec.get("total") == 0
                  and (edata.get("suppression_top") in (None, []))
                  and edata.get("promotions") == [],
                  f"{st} {str(edata)[:200]}")
            check("quant-status empty db -> insufficient-evidence",
                  edata.get("reason") == "insufficient-evidence"
                  and edata.get("status") == "unseen",
                  str(edata)[:200])
        finally:
            empty_server.shutdown()
            empty_server.server_close()

        # -- unreadable path -> 503 explicit --------------------------------
        bad_server = svc.serve(0, TOKEN, lambda: {}, store_path=sig_db,
                               data_dir=bars_dir,
                               quant_db=os.path.join(tmp, "quant-dir"),
                               is_ready=lambda: True)
        os.makedirs(os.path.join(tmp, "quant-dir"), exist_ok=True)
        bport = bad_server.server_address[1]
        time.sleep(0.1)
        try:
            st, body = get(bport, "/quant-status")
            check("quant-status unreadable db -> 503",
                  st == 503, f"got {st}")
            check("quant-status 503 carries UNAVAILABLE",
                  isinstance(body, dict) and (body.get("error") or {}).get(
                      "code") == "UNAVAILABLE", str(body)[:160])
        finally:
            bad_server.shutdown()
            bad_server.server_close()

        # -- pure helpers: status mapping + emit gating ----------------------
        check("status mapping covers four states",
              svc._quant_status_for(False, False, 5, 9) == "unavailable"
              and svc._quant_status_for(True, True, 5, 9) == "paused"
              and svc._quant_status_for(True, False, 0, 0) == "unseen"
              and svc._quant_status_for(True, False, 3, 0) == "healthy"
              and svc._quant_status_for(True, False, 0, 4) == "healthy",
              "mapping deviates from the binding states")
        svc.reset_events()
        check("emit helper ignores non-ok results",
              svc.emit_quant_shadow((2, 1), {"emitted": 9}) == (2, 1)
              and svc.emit_quant_shadow((2, 1), None) == (2, 1)
              and svc.head_event_id() == "000000",
              "helper emitted without an ok step result")
        check("emit helper emits first new counts",
              svc.emit_quant_shadow(None,
                                    {"ok": True, "emitted": 1,
                                     "resolved": 0}) == (1, 0)
              and svc.head_event_id() == "000001",
              f"head={svc.head_event_id()}")
        svc.reset_events()

        # -- readable-but-unwritable file -> 200 unavailable (not 503) ----
        ro_dir = os.path.join(tmp, "readonly")
        os.makedirs(ro_dir, exist_ok=True)
        ro_path = os.path.join(ro_dir, "ro-quant.db")
        ro_store = QuantStore(ro_path)
        ro_store.set_watermark("BTC", BASE_TS)
        ro_store.close()
        os.chmod(ro_path, 0o444)
        ro_server = svc.serve(0, TOKEN, lambda: {}, store_path=sig_db,
                              data_dir=bars_dir, quant_db=ro_path,
                              is_ready=lambda: True)
        rport = ro_server.server_address[1]
        time.sleep(0.1)
        try:
            st, body = get(rport, "/quant-status")
            rdata = (body or {}).get("data") or {}
            check("quant-status unwritable file -> 200 unavailable",
                  st == 200 and rdata.get("status") == "unavailable",
                  f"{st} {str(rdata)[:200]}")
        finally:
            ro_server.shutdown()
            ro_server.server_close()
            os.chmod(ro_path, 0o644)

        # -- SSE: quant.shadow in order with the ring -----------------------
        check("quant.shadow registered in EVENT_KINDS",
              "quant.shadow" in svc.EVENT_KINDS, str(svc.EVENT_KINDS))
        stream = SSEStream(port, last_id=svc.head_event_id())
        try:
            check("quant stream opened 200", stream.status == 200,
                  f"got {stream.status}")
            last = (0, 0)
            last = svc.emit_quant_shadow(
                last, {"ok": True, "emitted": 2, "resolved": 1})
            f1 = stream.wait_for(
                lambda fr: fr["event"] == "quant.shadow")
            svc.record_sweep_completed(eligible_count=1, evaluated_count=1)
            f2 = stream.wait_for(
                lambda fr: fr["event"] == "sweep.completed")
            last = svc.emit_quant_shadow(
                last, {"ok": True, "emitted": 3, "resolved": 1})
            f3 = stream.wait_for(
                lambda fr: fr["event"] == "quant.shadow"
                and fr["data"].get("state", {}).get("emitted") == 3)
            check("quant.shadow arrives on the wire",
                  f1 is not None, "no quant.shadow frame")
            check("quant.shadow frame carries counts only",
                  f1 is not None
                  and f1["data"].get("state") == {"emitted": 2,
                                                  "resolved": 1}
                  and f1["data"].get("coin") is None
                  and all(k not in f1["data"]
                          for k in ("stop", "tp", "entry", "advice",
                                    "levels")),
                  str(f1)[:220])
            check("quant.shadow ordered with the ring",
                  None not in (f1, f2, f3)
                  and f1["id"] < f2["id"] < f3["id"],
                  str([(f or {}).get("id") if f else None
                       for f in (f1, f2, f3)]))
            # No per-bar spam: unchanged counts emit nothing.
            before = svc.head_event_id()
            unchanged = svc.emit_quant_shadow(
                last, {"ok": True, "emitted": 3, "resolved": 1})
            check("unchanged counts emit nothing",
                  unchanged == (3, 1)
                  and svc.head_event_id() == before,
                  f"{unchanged} head={svc.head_event_id()}")
        finally:
            stream.close()

        # -- stale cursor resyncs with the new kind retained -----------------
        ancient = svc.head_event_id()
        for _ in range(505):
            svc.record_sweep_completed(eligible_count=0, evaluated_count=0)
        stream = SSEStream(port, last_id=ancient)
        try:
            first = stream.wait_for(lambda fr: True, timeout=5.0)
            check("stale cursor -> resync first",
                  first is not None and first["event"] == "resync",
                  str(first)[:200])
        finally:
            stream.close()
        stream = SSEStream(port)
        try:
            svc.emit_quant_shadow(
                (0, 0), {"ok": True, "emitted": 5, "resolved": 0})
            f = stream.wait_for(
                lambda fr: fr["event"] == "quant.shadow"
                and fr["data"].get("state", {}).get("emitted") == 5)
            check("quant.shadow flows post-eviction",
                  f is not None, "no frame after ring rollover")
        finally:
            stream.close()
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
        print(f"FAIL  tests/test_quant_api.py  "
              f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
        sys.exit(1)
    print(f"ALL PASS  tests/test_quant_api.py  ({CHECKS} checks)")


if __name__ == "__main__":
    main()
