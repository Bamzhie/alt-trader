"""Task 2: snapshot + resource routes with typed envelope.

Self-checking script (stdlib only). Talks to 127.0.0.1 ONLY: an egress
tripwire wraps socket connects and fails any connect outside loopback.

Fixtures use a temp DB via REAL writers (Store.log_signal/log_plan,
watcher.emit_setup, collector.append_bars) — never hand-built JSON.

Run:  python3 tests/test_service_routes.py   -> ALL PASS, exit 0
"""

import http.client
import json
import os
import socket
import sys
import tempfile
import time
from types import SimpleNamespace

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


TOKEN = "bearer-secret-task2-routes"


def get(port, path, token=TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    return resp.status, payload


def fake_scorecard(coin, price=100.0):
    return SimpleNamespace(
        coin=coin, venue="MEXC", score=80.0, lean=1.5, direction="LONG",
        earlyness=0.5, magnitude_parts={"VOL": 1.0, "BOOK": 0.5, "OI": 0.2},
        lean_parts={"VOL": 0.5, "BOOK": 0.3, "OI": 0.1}, price=price,
        change_24h_pct=2.0, quote_vol_24h=1e6, spread_pct=0.01,
        funding_rate=0.0001, min_notional=5.0, vetoes=[], tradeable=True,
        oi_notional=None, oi_change_pct=None)


def closed_bars(base_ts, n, step=300, price0=100.0):
    """n fully-closed 5m bars ending well before now (deterministic)."""
    bars = []
    for i in range(n):
        c = price0 + (i % 7) * 0.31 - (i % 5) * 0.17
        bars.append({"ts": base_ts + i * step, "o": c - 0.05,
                     "h": c + 0.12, "l": c - 0.11, "c": c,
                     "vol": 1000.0 + i, "amount": 100000.0 + i * 10})
    return bars


def main():
    from proto import service as svc
    from proto.store import Store
    from proto import collector as colmod
    from proto import watcher as watchmod

    tmp = tempfile.mkdtemp(prefix="svc-routes-")
    db_path = os.path.join(tmp, "signals.db")
    data_dir = os.path.join(tmp, "bars")
    store = Store(db_path)

    # -- fixture: one planned signal (tradeplan source: pre-cut plan_log) --
    sid = store.log_signal(fake_scorecard("RICH"), True)
    store.log_plan(sid, SimpleNamespace(
        direction="LONG", entry_low=99.9, entry_high=100.1, stop=98.0,
        tp1=104.0, tp2=110.0, leverage=5, notional=100.0, max_loss=2.0))

    # -- fixture: bars via the real collector writer -----------------------
    base = 1700000000 - (1700000000 % 300)  # aligned, long closed
    colmod.append_bars(data_dir, "RICH", closed_bars(base - 250 * 300, 250))
    colmod.append_bars(data_dir, "THIN", closed_bars(base - 3 * 300, 3))

    # -- fixture: watcher setups via the real emitter ----------------------
    # 510 active setups on distinct coins (cooldown is per coin+rule) to
    # prove the 500 pagination cap, plus one resolved setup.
    first_id = watchmod.emit_setup(store.conn, {
        "rule_id": "t1-break-v1", "coin": "RICH", "src_bar_ts": base,
        "entry": 100.0, "direction": "LONG", "src": "t1_break"})
    for i in range(509):
        watchmod.emit_setup(store.conn, {
            "rule_id": "t1-break-v1", "coin": f"C{i:04d}",
            "src_bar_ts": base - i * 300, "entry": 10.0 + i * 0.01,
            "direction": "LONG", "src": "t1_break"})
    store.conn.commit()

    ready_state = {"ok": True}
    server = svc.serve(0, TOKEN, lambda: {},
                       store_path=db_path, data_dir=data_dir,
                       is_ready=lambda: ready_state["ok"])
    port = server.server_address[1]
    time.sleep(0.15)
    try:
        # -- envelope + nulls ------------------------------------------------
        st, body = get(port, "/snapshot")
        check("snapshot 200", st == 200, f"got {st}")
        check("snapshot envelope {ok,data,error}",
              isinstance(body, dict) and body.get("ok") is True
              and isinstance(body.get("data"), dict)
              and body.get("error") is None, str(body)[:160])
        check("snapshot ts is UTC-seconds int",
              isinstance((body.get("data") or {}).get("ts"), int),
              str(body)[:160])

        st, body = get(port, "/metrics?coin=NOPE")
        check("metrics unknown coin -> 404", st == 404, f"got {st}")
        check("metrics 404 code NOT_FOUND",
              isinstance(body, dict)
              and (body.get("error") or {}).get("code") == "NOT_FOUND",
              str(body)[:160])

        st, body = get(port, "/metrics?coin=THIN")
        check("metrics thin bars -> 200 with explicit nulls",
              st == 200 and isinstance(body, dict)
              and body.get("ok") is True
              and (body.get("data") or {}).get("rsi") is None,
              f"got {st} {str(body)[:160]}")

        st, body = get(port, "/metrics?coin=RICH")
        check("metrics rich bars -> computed rsi",
              st == 200 and (body.get("data") or {}).get("rsi") is not None,
              f"got {st} {str(body)[:160]}")

        # -- pagination caps ---------------------------------------------------
        st, body = get(port, "/setups?limit=9999")
        check("setups limit=9999 capped at 500",
              st == 200 and isinstance(body.get("data"), list)
              and len(body["data"]) <= 500, f"got {st}")
        check("setups cap is exactly 500",
              st == 200 and len(body["data"]) == 500,
              f"got {len(body['data']) if st == 200 else st}")

        st, dflt = get(port, "/setups")
        check("setups default page <= 100",
              st == 200 and len(dflt["data"]) <= 100,
              f"got {st} len={len(dflt['data']) if st == 200 else '?'}")

        st, p1 = get(port, "/setups?limit=10&cursor=0")
        st2, p2 = get(port, "/setups?limit=10&cursor=10")
        ids1 = [r["id"] for r in p1["data"]] if st == 200 else []
        ids2 = [r["id"] for r in p2["data"]] if st2 == 200 else []
        check("cursor pages do not overlap",
              st == 200 and st2 == 200 and len(ids1) == 10
              and not set(ids1) & set(ids2), f"{st}/{st2}")

        st, body = get(port, "/setups?state=bogus")
        check("setups bad state -> 400 BAD_REQUEST",
              st == 400 and (body.get("error") or {}).get("code")
              == "BAD_REQUEST", f"got {st}")

        # -- setup ids carry rule:coin:src_bar_ts shape ------------------------
        st, body = get(port, f"/setup/{first_id}")
        check("setup by id -> 200",
              st == 200 and body.get("ok") is True, f"got {st}")
        got_id = (body.get("data") or {}).get("id", "")
        check("setup id is rule:coin:src_bar_ts",
              len(str(got_id).split(":")) == 3
              and str(got_id).split(":")[1] == "RICH", got_id)
        st, body = get(port, "/setup/t1-break-v1:NOPE:1")
        check("setup unknown -> 404 NOT_FOUND",
              st == 404 and (body.get("error") or {}).get("code")
              == "NOT_FOUND", f"got {st}")

        # -- bars: closed only, 1m rejected -------------------------------------
        st, body = get(port, "/bars?coin=RICH&tf=1m")
        check("bars 1m -> 400 BAD_REQUEST",
              st == 400 and (body.get("error") or {}).get("code")
              == "BAD_REQUEST", f"got {st}")
        st, body = get(port, "/bars?coin=RICH&tf=5m")
        bars = (body.get("data") or {}).get("bars", []) if st == 200 else []
        now = int(time.time())
        check("bars 5m -> 200 closed only",
              st == 200 and len(bars) > 0
              and all(b["ts"] + 300 <= now for b in bars),
              f"got {st} n={len(bars)}")
        st, body = get(port, "/bars?coin=RICH&tf=1H")
        bars1h = (body.get("data") or {}).get("bars", []) if st == 200 \
            else []
        check("bars 1H resampled closed only",
              st == 200 and len(bars1h) > 0
              and all(b["ts"] + 3600 <= now for b in bars1h),
              f"got {st} n={len(bars1h)}")
        st, _ = get(port, "/bars?coin=NOPE&tf=5m")
        check("bars unknown coin -> 404", st == 404, f"got {st}")

        # -- tradeplan: 100U reference, no leverage ------------------------------
        st, body = get(port, f"/tradeplan/{sid}")
        data = body.get("data") or {} if isinstance(body, dict) else {}
        check("tradeplan -> 200 reference",
              st == 200 and data.get("notional") == 100.0,
              f"got {st} {str(body)[:160]}")
        check("tradeplan has no leverage key",
              st == 200 and "leverage" not in data, str(data)[:160])
        st, _ = get(port, "/tradeplan/999999")
        check("tradeplan unknown -> 404", st == 404, f"got {st}")

        # -- readyz ---------------------------------------------------------------
        st, body = get(port, "/readyz")
        check("readyz ready -> 200",
              st == 200 and body.get("ok") is True, f"got {st}")
        ready_state["ok"] = False
        st, body = get(port, "/readyz")
        check("readyz not-ready -> 503 UNAVAILABLE",
              st == 503 and (body.get("error") or {}).get("code")
              == "UNAVAILABLE", f"got {st}")
        ready_state["ok"] = True
        st, _ = get(port, "/readyz", token=None)
        check("readyz no token -> 401", st == 401, f"got {st}")
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
        print(f"FAIL  tests/test_service_routes.py  "
              f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
        sys.exit(1)
    print(f"ALL PASS  tests/test_service_routes.py  ({CHECKS} checks)")


if __name__ == "__main__":
    main()
