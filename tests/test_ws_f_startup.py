"""Workstream F: startup & first-sweep optimization (regression tests).

Self-checking script (stdlib only, offline: no venue calls, no writes to
the repository's data/ archives - every fixture lives in a temp dir).

Pins:
- ``GET /readyz`` is API-SERVING readiness: 200 as soon as the HTTP
  server serves and the DB is readable - it does NOT wait for the first
  sweep.  The first-sweep state lives in ``GET /scanner-health`` ->
  ``first_sweep`` and never claims a sweep that did not complete.
- the persisted cadence (``.cadence.json`` next to the DB) makes a warm
  restart skip a stage that is not due; an expired stage runs again.
- a failed stage is not marked as run (it retries on the next iteration).
- the bar index skips the full-archive read when (size, mtime_ns) match and
  falls back to the full read (no duplicates, no lost bars) when they do
  not.
- the bounded quant rebuild produces feature values identical to a full
  rebuild.
- a stop flag set mid-cycle ends the iteration promptly and records the
  interrupted stage as not-ok.
- the daemon path writes exactly one watcher_events row and one
  dashboard_snapshot row per completed iteration; the CLI path still writes
  its own.
- the per-stage durations are published in ``GET /scanner-health``.

Run:  python3 tests/test_ws_f_startup.py   -> ALL PASS, exit 0
"""

import http.client
import inspect
import json
import math
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

FAILURES = []
CHECKS = 0
HUGE = 10 ** 12


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    cond = bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)
    return cond


def get(port, path, token="ws-f-token"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Authorization": f"Bearer {token}"}
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError:
        payload = None
    return resp.status, payload


def start_service(tmp, db, extra_argv=(), token="ws-f-token", data_dir=None):
    """Start the service in a thread; return a box with port/stop/thread."""
    from proto import service as svc
    lock = os.path.join(tmp, "locks",
                        os.path.basename(db) + ".scanner.lock")
    box = {}
    stop = threading.Event()

    def _run():
        box["result"] = svc.run_service(
            port=0, token=token, db=db,
            data_dir=data_dir or os.path.join(tmp, "bars"),
            lock_path=lock,
            daemon_argv=["--no-scan", "--interval", "60",
                         "--collect-every", "0",
                         "--resolve-every", str(HUGE),
                         "--measure-every", str(HUGE)] + list(extra_argv),
            stop_event=stop, install_signals=False,
            on_serving=lambda s, p: box.setdefault("port", p))

    thread = threading.Thread(target=_run, name="wsf-service")
    thread.start()
    deadline = time.time() + 20
    while time.time() < deadline and not box.get("port"):
        time.sleep(0.02)
    box["thread"] = thread
    box["stop"] = stop
    return box


def stop_service(box):
    box["stop"].set()
    box["thread"].join(timeout=30)


def wait_for(predicate, timeout=20.0, step=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return None


def db_rows(db, sql):
    if not os.path.exists(db):
        return []
    conn = sqlite3.connect(db, timeout=5)
    try:
        return list(conn.execute(sql))
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------- timing ---
def test_timing_log_and_cadence():
    print("=== timing log + persisted cadence ===")
    from proto.daemon_timing import (Cadence, TimingLog, format_stages,
                                      sidecar_path)
    tmp = tempfile.mkdtemp(prefix="wsf-timing-")
    try:
        db = os.path.join(tmp, "signals.db")
        path = sidecar_path(db, ".timing.json")
        log = TimingLog(path, db=db)
        log.begin()
        log.record("scan", 1.5, "ok")
        log.record("collect", 0.0, "not_due")
        log.record("quant", 2.0, "failed")
        done = log.end(duration_s=3.5)
        check("iteration records every stage outcome",
              done["stages"]["scan"]["outcome"] == "ok"
              and done["stages"]["collect"]["outcome"] == "not_due"
              and done["stages"]["quant"]["outcome"] == "failed",
              str(done))
        check("an unknown outcome is never stored as a success",
              log.record("scan", 1.0, "bogus")["outcome"] == "failed")
        check("the timing file is persisted next to the DB",
              os.path.exists(path), path)
        again = TimingLog(path, db=db)
        check("the timing file survives a restart",
              len(again.iterations) == 1
              and again.iterations[0]["stages"]["quant"]["outcome"] == "failed",
              str(again.iterations))
        summary = format_stages(again.last_stages())
        check("the summary line names every stage",
              all(name in summary for name in
                  ("scan=", "collect=", "funding=", "settlements=",
                   "prices=", "resolve=", "measure=", "quant=")), summary)
        check("a failed stage is not printed like a success",
              "quant=2.0(failed)" in summary, summary)

        cpath = sidecar_path(db, ".cadence.json")
        cad = Cadence(cpath, intervals={"scan": 300, "collect": 600},
                      db=db)
        check("a stage with no entry is due (first boot)",
              cad.due("scan") and cad.due("collect"))
        cad.mark("scan", now=1000)
        check("a stage that just ran is not due",
              not cad.due("scan", now=1000))
        check("an expired stage is due again",
              cad.due("scan", now=1300))
        check("the cadence is persisted", os.path.exists(cpath), cpath)
        reloaded = Cadence(cpath, intervals={"scan": 300}, db=db)
        check("the cadence survives a restart",
              not reloaded.due("scan", now=1000)
              and reloaded.due("scan", now=1300), str(reloaded.entries))
        other = Cadence(cpath, intervals={"scan": 300},
                        db=os.path.join(tmp, "other.db"))
        check("a cadence file of another database is ignored",
              other.due("scan", now=1000), str(other.entries))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------- readiness + first sweep --
def test_readyz_and_first_sweep():
    print("=== /readyz is API readiness; the first sweep has its own state ===")
    from proto import daemon as dmod
    tmp = tempfile.mkdtemp(prefix="wsf-ready-")
    real_collect = dmod.colmod.collect_full_universe
    real_fund = dmod.fmod.collect_funding
    real_settle = dmod.smod.maybe_collect
    real_prices = dmod.pmod.maybe_collect
    dmod.fmod.collect_funding = lambda *a, **k: {}
    dmod.smod.maybe_collect = lambda *a, **k: {}
    dmod.pmod.maybe_collect = lambda *a, **k: {}
    try:
        # -- a slow first sweep: the API is ready long before it finishes --
        release = threading.Event()

        def _slow_collect(*a, **k):
            release.wait(timeout=20)
            return {"BTC": 1, "ETH": 2}

        dmod.colmod.collect_full_universe = _slow_collect
        db = os.path.join(tmp, "slow.db")
        box = start_service(tmp, db)
        port = box.get("port")
        check("the service binds a port", bool(port), str(box))
        st, body = get(port, "/readyz")
        check("/readyz answers 200 before the first sweep completes",
              st == 200 and body.get("ok") is True
              and (body.get("data") or {}).get("ready") is True
              and isinstance((body.get("data") or {}).get("ts"), int),
              f"got {st} {str(body)[:160]}")
        st, body = get(port, "/scanner-health")
        fs = (body.get("data") or {}).get("first_sweep") or {}
        # the loop must have started its first iteration by now (the
        # collect below is still blocked)
        got = wait_for(lambda: (
            (get(port, "/scanner-health")[1].get("data") or {})
            .get("first_sweep") or {}).get("state") == "in_progress")
        check("the first sweep is in_progress while it runs", bool(got),
              str(fs))
        st, body = get(port, "/scanner-health")
        fs = (body.get("data") or {}).get("first_sweep") or {}
        check("the first sweep is in_progress while it runs",
              fs.get("state") == "in_progress", str(fs))
        check("an unfinished first sweep has no completed_ts",
              fs.get("completed_ts") is None
              and fs.get("duration_s") is None, str(fs))
        check("the first sweep has a started_ts",
              isinstance(fs.get("started_ts"), int), str(fs))
        release.set()
        got = wait_for(lambda: (
            (get(port, "/scanner-health")[1].get("data") or {})
            .get("first_sweep") or {}).get("state") == "complete")
        check("a completed first sweep is reported as complete", bool(got))
        st, body = get(port, "/scanner-health")
        fs = (body.get("data") or {}).get("first_sweep") or {}
        check("the completed first sweep carries its measured duration",
              isinstance(fs.get("duration_s"), (int, float))
              and fs["duration_s"] >= 0, str(fs))
        check("the completed first sweep carries its stage breakdown",
              isinstance(fs.get("stages"), dict)
              and "collect" in fs["stages"], str(fs))
        stop_service(box)

        # -- a FAILED first sweep must never claim "complete" --------------
        def _boom_collect(*a, **k):
            raise RuntimeError("collect down")

        dmod.colmod.collect_full_universe = _boom_collect
        db2 = os.path.join(tmp, "failed.db")
        box2 = start_service(tmp, db2)
        port2 = box2.get("port")
        st, body = get(port2, "/scanner-health")
        fs = (body.get("data") or {}).get("first_sweep") or {}
        check("a failed first sweep is never 'complete'",
              fs.get("state") in ("pending", "in_progress"), str(fs))
        check("a failed first sweep has no completed_ts",
              fs.get("completed_ts") is None, str(fs))
        st, body = get(port2, "/readyz")
        check("/readyz still answers 200 while the first sweep fails",
              st == 200, f"got {st}")
        stop_service(box2)
    finally:
        dmod.colmod.collect_full_universe = real_collect
        dmod.fmod.collect_funding = real_fund
        dmod.smod.maybe_collect = real_settle
        dmod.pmod.maybe_collect = real_prices
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------- the cadence --
def _fake_app(counter, fail=False, sweep_writer=False):
    """An offline stand-in for proto.app.App."""
    from proto.store import Store
    from proto import watcher_journal as journalmod

    class FakeApp:
        def __init__(self, args):
            self.args = args
            self.cards = []
            self.failed = 0
            self.uni = ["AAA", "BBB"]
            self.tk = {}
            self.det = {}
            self.universe_error = None
            self.store = Store(args.db)

        def maybe_refresh_universe(self):
            return None

        def scan_once(self, record_sweep=True):
            counter["scans"] = counter.get("scans", 0) + 1
            counter["record_sweep"] = record_sweep
            if fail:
                raise RuntimeError("scan boom")
            self.cards = []
            if sweep_writer and record_sweep:
                # What the real App does on the CLI path.
                journalmod.record_sweep(self.store.conn, len(self.uni), 0)

    return FakeApp


def test_cadence_persisted_across_restarts():
    print("=== a warm restart does not repeat work that is not due ===")
    from proto import daemon as dmod
    tmp = tempfile.mkdtemp(prefix="wsf-cadence-")
    real_app = dmod.App
    real_collect = dmod.colmod.collect_full_universe
    real_fund = dmod.fmod.collect_funding
    real_settle = dmod.smod.maybe_collect
    real_prices = dmod.pmod.maybe_collect

    def _no_venue(*a, **k):
        return {}

    dmod.fmod.collect_funding = _no_venue
    dmod.smod.maybe_collect = _no_venue
    dmod.pmod.maybe_collect = _no_venue
    try:
        db = os.path.join(tmp, "signals.db")
        counter = {}
        dmod.App = _fake_app(counter)
        dmod.colmod.collect_full_universe = lambda *a, **k: {"BTC": 1}
        args = SimpleNamespace(
            stake=0.10, coins=150, interval=300, db=db, log_threshold=24.0,
            use_v3=False, collect_every=HUGE, resolve_every=HUGE,
            measure_every=HUGE, iterations=1, no_scan=False,
            quant_shadow=False, quant_db=os.path.join(tmp, "q.db"),
            quant_bars=os.path.join(tmp, "bars"), quant_equity=1000.0,
            settlements_dir=None, prices_dir=None)
        dmod.run_daemon_loop(args)
        check("the first boot runs the scan", counter.get("scans") == 1,
              str(counter))
        cad_path = os.path.join(tmp, ".cadence.json")
        check("the cadence file is written next to the DB",
              os.path.exists(cad_path), cad_path)
        with open(cad_path, encoding="utf-8") as f:
            cad = json.load(f)
        entry = (cad.get("stages") or {}).get("scan") or {}
        check("the completed stage is marked as run",
              isinstance(entry.get("last_run_ts"), int)
              and entry.get("interval_s") == 300, str(cad))

        # -- warm restart: the scan is not due yet -----------------------
        counter2 = {}
        dmod.App = _fake_app(counter2)
        dmod.run_daemon_loop(args)
        check("a warm restart does not repeat a stage that is not due",
              counter2.get("scans", 0) == 0, str(counter2))

        # -- expired cadence: the stage runs again ------------------------
        with open(cad_path, encoding="utf-8") as f:
            cad = json.load(f)
        cad["stages"]["scan"]["last_run_ts"] = int(time.time()) - 10_000
        with open(cad_path, "w", encoding="utf-8") as f:
            json.dump(cad, f)
        counter3 = {}
        dmod.App = _fake_app(counter3)
        dmod.run_daemon_loop(args)
        check("an expired stage runs again", counter3.get("scans") == 1,
              str(counter3))
    finally:
        dmod.App = real_app
        dmod.colmod.collect_full_universe = real_collect
        dmod.fmod.collect_funding = real_fund
        dmod.smod.maybe_collect = real_settle
        dmod.pmod.maybe_collect = real_prices
        shutil.rmtree(tmp, ignore_errors=True)


def test_failed_stage_is_not_marked_as_run():
    print("=== a failed stage retries (it is never marked as run) ===")
    from proto import daemon as dmod
    tmp = tempfile.mkdtemp(prefix="wsf-retry-")
    real_app = dmod.App
    real_collect = dmod.colmod.collect_full_universe
    real_fund = dmod.fmod.collect_funding
    real_settle = dmod.smod.maybe_collect
    real_prices = dmod.pmod.maybe_collect
    calls = {"n": 0}

    def _no_venue(*a, **k):
        return {}

    dmod.fmod.collect_funding = _no_venue
    dmod.smod.maybe_collect = _no_venue
    dmod.pmod.maybe_collect = _no_venue

    def _boom_collect(*a, **k):
        calls["n"] += 1
        raise RuntimeError("collect down")

    try:
        db = os.path.join(tmp, "signals.db")
        dmod.App = _fake_app({})
        dmod.colmod.collect_full_universe = _boom_collect
        args = SimpleNamespace(
            stake=0.10, coins=150, interval=5, db=db, log_threshold=24.0,
            use_v3=False, collect_every=300, resolve_every=HUGE,
            measure_every=HUGE, iterations=2, no_scan=True,
            quant_shadow=False, quant_db=os.path.join(tmp, "q.db"),
            quant_bars=os.path.join(tmp, "bars"), quant_equity=1000.0,
            settlements_dir=None, prices_dir=None)
        dmod.run_daemon_loop(args)
        check("a failed stage is retried on the next iteration",
              calls["n"] == 2, str(calls))
        cad_path = os.path.join(tmp, ".cadence.json")
        marked = False
        if os.path.exists(cad_path):
            with open(cad_path, encoding="utf-8") as f:
                cad = json.load(f)
            marked = "collect" in (cad.get("stages") or {})
        check("a failed stage is not marked as run", not marked)
    finally:
        dmod.App = real_app
        dmod.colmod.collect_full_universe = real_collect
        dmod.fmod.collect_funding = real_fund
        dmod.smod.maybe_collect = real_settle
        dmod.pmod.maybe_collect = real_prices
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------- the indexes --
def test_bar_index():
    print("=== the bar index skips the full archive read (and falls back) ===")
    from proto import collector as col
    tmp = tempfile.mkdtemp(prefix="wsf-index-")
    real_read = col.read_bars
    calls = []
    try:
        bars = [{"ts": 1_700_000_000 + i * 300, "o": 1.0, "h": 1.1,
                 "l": 0.9, "c": 1.0 + i * 0.01, "vol": 10.0,
                 "amount": 10.0 + i} for i in range(50)]

        def _spy(data_dir, coin):
            calls.append(coin)
            return real_read(data_dir, coin)

        col.read_bars = _spy
        n1 = col.append_bars(tmp, "AAA", bars)
        check("the first write stores every bar", n1 == 50, str(n1))
        check("the first write had to read the archive", calls == ["AAA"],
              str(calls))
        calls.clear()
        n2 = col.append_bars(tmp, "AAA", bars)
        check("a matching index skips the archive read", calls == [],
              str(calls))
        check("a matching index writes no duplicate bars", n2 == 0, str(n2))
        check("the index file is written next to the archive",
              os.path.exists(os.path.join(tmp, ".bar_index.json")))

        # a NEW bar is still appended through the fast path
        calls.clear()
        fresh = dict(bars[-1])
        fresh["ts"] = bars[-1]["ts"] + 300
        n3 = col.append_bars(tmp, "AAA", [fresh])
        check("a new bar is still written with the index in place",
              n3 == 1, str(n3))
        check("the fast path still does not read the archive", calls == [],
              str(calls))
        check("the archive holds every bar",
              len(real_read(tmp, "AAA")) == 51,
              str(len(real_read(tmp, "AAA"))))

        # -- a MISMATCHED index falls back to the full read ---------------
        path = os.path.join(tmp, "AAA.csv.gz")
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
        calls.clear()
        n4 = col.append_bars(tmp, "AAA", bars)
        check("a mismatched index falls back to the full read",
              calls == ["AAA"], str(calls))
        check("the fallback writes no duplicate bars", n4 == 0, str(n4))
        check("the fallback loses no bar",
              len(real_read(tmp, "AAA")) == 51,
              str(len(real_read(tmp, "AAA"))))
    finally:
        col.read_bars = real_read
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------- the bounded rebuild --
def _series(n, seed, base_ts=1_700_000_100, sigma=0.002, amount=2e4):
    rng = random.Random(seed)
    px = 100.0
    out = []
    for i in range(n):
        o = px
        px = o * math.exp(rng.gauss(0, sigma))
        a = amount * (0.7 + 0.6 * rng.random())
        out.append((base_ts + i * 300, o, max(o, px) * 1.0005,
                    min(o, px) * 0.9995, px, a / px, a))
    return out


def _write_series(path, rows):
    import gzip
    with gzip.open(path, "wt", newline="") as f:
        f.write("ts,o,h,l,c,vol,amount\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


def test_bounded_rebuild_is_identical():
    print("=== the bounded quant rebuild equals the full rebuild ===")
    from proto.quant import engine as E
    from proto.quant.config import DEFAULT, BAR_S
    from proto.quant.stream import FeatureStream
    tmp = tempfile.mkdtemp(prefix="wsf-quant-")
    try:
        bars_dir = os.path.join(tmp, "bars")
        os.makedirs(bars_dir)
        days = 30
        n = days * 288
        btc = _series(n, 1, sigma=0.001, amount=5e6)
        aaa = _series(n, 2, sigma=0.002, amount=2e4)
        _write_series(os.path.join(bars_dir, "BTC.csv.gz"), btc)
        _write_series(os.path.join(bars_dir, "AAA.csv.gz"), aaa)
        # the market factor, exactly as the watcher computes it
        btc_ret = {}
        for i in range(1, len(btc)):
            if btc[i][0] - btc[i - 1][0] == BAR_S:
                btc_ret[btc[i][0]] = math.log(btc[i][4] / btc[i - 1][4])

        full = E.load_coin(bars_dir, "AAA")
        bounded = E.load_coin_window(bars_dir, "AAA")
        check("the archive is longer than the bounded window",
              full is not None and bounded is not None
              and len(full["ts"]) == n and len(bounded["ts"]) < n,
              f"{len(full['ts']) if full else None}/"
              f"{len(bounded['ts']) if bounded else None}")
        w_full = E.rebuild_window(full)
        w_bounded = E.rebuild_window(bounded)
        check("both rebuilds replay the same number of bars",
              (w_full[1] - w_full[0]) == (w_bounded[1] - w_bounded[0]),
              f"{w_full} {w_bounded}")
        fed_full = [full["ts"][i] for i in range(w_full[0], w_full[1])]
        fed_bounded = [bounded["ts"][i] for i in range(w_bounded[0],
                                                      w_bounded[1])]
        check("both rebuilds replay exactly the same bars",
              fed_full == fed_bounded,
              f"{len(fed_full)}/{len(fed_bounded)}")

        def _snap(bars, window):
            st = FeatureStream(DEFAULT.feat)
            snap = None
            a, z = window
            for i in range(a, z):
                ts = bars["ts"][i]
                snap = st.update(ts, bars["o"][i], bars["h"][i], bars["l"][i],
                                 bars["c"][i], bars["amt"][i],
                                 btc_ret.get(ts, 0.0))
            return snap

        s_full = _snap(full, w_full)
        s_bounded = _snap(bounded, w_bounded)
        check("both rebuilds produce a warmed-up snapshot",
              s_full is not None and s_bounded is not None)
        if s_full is not None and s_bounded is not None:
            fields = ("sigma", "z", "res", "beta", "rvol", "qv24", "z1h",
                      "z4h", "up_age", "dn_age")
            worst = 0.0
            for f in fields:
                a = getattr(s_full, f)
                b = getattr(s_bounded, f)
                if a is None or b is None:
                    check(f"the bounded rebuild has the same {f}",
                          a is None and b is None, f"{a} {b}")
                    continue
                worst = max(worst, abs(a - b))
                check(f"the bounded rebuild has the same {f}",
                      abs(a - b) <= 1e-12, f"{a} vs {b}")
            check("every compared feature is identical within 1e-12",
                  worst <= 1e-12, str(worst))

        # the watcher itself: the bounded rebuild is the default and it
        # matches the full-parse rebuild state for state.
        from proto.quant import qstore
        store = qstore.QuantStore(os.path.join(tmp, "q.db"))
        try:
            w1 = E.Watcher(bars_dir, store)
            w2 = E.Watcher(bars_dir, store)
            cs1 = w1._rebuild("AAA", "start")
            cs2 = w2._rebuild("AAA", "start", full=True)
            check("the bounded rebuild reaches the same last bar",
                  cs1.last_ts == cs2.last_ts, f"{cs1.last_ts} {cs2.last_ts}")
            check("the bounded rebuild reaches the same warm-up",
                  cs1.stream.n == cs2.stream.n, f"{cs1.stream.n} "
                  f"{cs2.stream.n}")
            check("the bounded rebuild keeps the same volatility state",
                  abs(cs1.stream.s2 - cs2.stream.s2) <= 1e-18,
                  f"{cs1.stream.s2} {cs2.stream.s2}")
            check("the bounded rebuild keeps the same beta state",
                  abs(cs1.stream.cov - cs2.stream.cov) <= 1e-18
                  and abs(cs1.stream.varb - cs2.stream.varb) <= 1e-18)
            check("the bounded rebuild keeps the same liquidity state",
                  abs(cs1.stream.qv - cs2.stream.qv) <= 1e-9)
        finally:
            store.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------ the stop flag --
def test_stop_flag_interrupts_the_iteration():
    print("=== a stop flag ends the iteration promptly ===")
    from proto import daemon as dmod
    from proto.daemon_timing import TimingLog
    tmp = tempfile.mkdtemp(prefix="wsf-stop-")
    real_app = dmod.App
    real_collect = dmod.colmod.collect_full_universe
    real_fund = dmod.fmod.collect_funding
    real_settle = dmod.smod.maybe_collect
    real_prices = dmod.pmod.maybe_collect
    try:
        db = os.path.join(tmp, "signals.db")
        stop = threading.Event()

        class _StoppingCounts(dict):
            def __init__(self):
                super().__init__()
                self.errors = {}
                self.stopped = True

        def _stop_collect(*a, **k):
            # the stop arrives in the middle of the collect cycle
            stop.set()
            return _StoppingCounts()

        dmod.fmod.collect_funding = lambda *a, **k: {}
        dmod.smod.maybe_collect = lambda *a, **k: {}
        dmod.pmod.maybe_collect = lambda *a, **k: {}
        dmod.App = _fake_app({})
        dmod.colmod.collect_full_universe = _stop_collect
        timing = TimingLog(os.path.join(tmp, ".timing.json"), db=db)
        args = SimpleNamespace(
            stake=0.10, coins=150, interval=300, db=db, log_threshold=24.0,
            use_v3=False, collect_every=0, resolve_every=HUGE,
            measure_every=HUGE, iterations=5, no_scan=True,
            quant_shadow=False, quant_db=os.path.join(tmp, "q.db"),
            quant_bars=os.path.join(tmp, "bars"), quant_equity=1000.0,
            settlements_dir=None, prices_dir=None)
        t0 = time.monotonic()
        dmod.run_daemon_loop(args, stop_event=stop, timing=timing)
        elapsed = time.monotonic() - t0
        check("the interrupted iteration ends promptly", elapsed < 10.0,
              f"{elapsed:.2f}s")
        last = timing.last() or {}
        stages = last.get("stages") or {}
        check("the interrupted stage is not recorded as ok",
              (stages.get("collect") or {}).get("outcome") not in
              (None, "ok"), str(stages.get("collect")))
        check("the stages after the interruption are skipped",
              (stages.get("measure") or {}).get("outcome") == "skipped",
              str(stages.get("measure")))
    finally:
        dmod.App = real_app
        dmod.colmod.collect_full_universe = real_collect
        dmod.fmod.collect_funding = real_fund
        dmod.smod.maybe_collect = real_settle
        dmod.pmod.maybe_collect = real_prices
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------- the sweep records --
def test_one_sweep_record_per_iteration():
    print("=== exactly one sweep record per completed iteration ===")
    from proto import daemon as dmod
    from proto import app as appmod
    from proto import scan as scanmod
    tmp = tempfile.mkdtemp(prefix="wsf-sweep-")
    real_app = dmod.App
    real_collect = dmod.colmod.collect_full_universe
    real_fund = dmod.fmod.collect_funding
    real_settle = dmod.smod.maybe_collect
    real_prices = dmod.pmod.maybe_collect
    real_score = scanmod.score_universe
    try:
        dmod.fmod.collect_funding = lambda *a, **k: {}
        dmod.smod.maybe_collect = lambda *a, **k: {}
        dmod.pmod.maybe_collect = lambda *a, **k: {}
        db = os.path.join(tmp, "signals.db")
        counter = {}
        # the fake writes the app-level sweep row exactly like the real
        # App does when it is asked to.
        dmod.App = _fake_app(counter, sweep_writer=True)
        dmod.colmod.collect_full_universe = lambda *a, **k: {"BTC": 1}
        args = SimpleNamespace(
            stake=0.10, coins=150, interval=5, db=db, log_threshold=24.0,
            use_v3=False, collect_every=HUGE, resolve_every=HUGE,
            measure_every=HUGE, iterations=2, no_scan=False,
            quant_shadow=False, quant_db=os.path.join(tmp, "q.db"),
            quant_bars=os.path.join(tmp, "bars"), quant_equity=1000.0,
            settlements_dir=None, prices_dir=None)
        dmod.run_daemon_loop(args)
        check("the daemon asks the app NOT to write its own sweep row",
              counter.get("record_sweep") is False, str(counter))
        events = db_rows(db, "SELECT kind FROM watcher_events "
                             "WHERE kind='sweep-complete'")
        snaps = db_rows(db, "SELECT COUNT(*) FROM dashboard_snapshot")[0][0]
        check("exactly one watcher_events row per completed iteration",
              len(events) == 2, str(len(events)))
        check("exactly one dashboard_snapshot row per completed iteration",
              snaps == 2, str(snaps))

        # -- the CLI path keeps writing its own row -----------------------
        db2 = os.path.join(tmp, "cli.db")
        cli_args = SimpleNamespace(
            stake=0.10, coins=150, interval=5, db=db2, write_logs=True,
            use_v3=False, universe_budget=150)
        app = appmod.App(cli_args)
        app.uni = [("AAA_USDT", "AAA")]
        app.tk = {"AAA_USDT": {"amount24": 1e6}}
        app.det = {}
        app.maybe_refresh_universe = lambda: None
        scanmod.score_universe = lambda *a, **kw: ([], {})
        try:
            app.scan_once()
        finally:
            scanmod.score_universe = real_score
        ev = db_rows(db2, "SELECT kind FROM watcher_events "
                          "WHERE kind='sweep-complete'")
        sn = db_rows(db2, "SELECT COUNT(*) FROM dashboard_snapshot")[0][0]
        check("the CLI path still writes its own sweep row",
              len(ev) == 1 and sn == 1, f"{len(ev)}/{sn}")
        sig = inspect.signature(appmod.App.scan_once)
        check("the app's sweep write defaults to on (CLI path unchanged)",
              sig.parameters["record_sweep"].default is True,
              str(sig))
    finally:
        dmod.App = real_app
        dmod.colmod.collect_full_universe = real_collect
        dmod.fmod.collect_funding = real_fund
        dmod.smod.maybe_collect = real_settle
        dmod.pmod.maybe_collect = real_prices
        scanmod.score_universe = real_score
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------- the stage breakdown API --
def test_stage_durations_in_scanner_health():
    print("=== the stage durations are published in /scanner-health ===")
    from proto import daemon as dmod
    tmp = tempfile.mkdtemp(prefix="wsf-health-")
    real_collect = dmod.colmod.collect_full_universe
    real_fund = dmod.fmod.collect_funding
    real_settle = dmod.smod.maybe_collect
    real_prices = dmod.pmod.maybe_collect
    dmod.fmod.collect_funding = lambda *a, **k: {}
    dmod.smod.maybe_collect = lambda *a, **k: {}
    dmod.pmod.maybe_collect = lambda *a, **k: {}
    try:
        dmod.colmod.collect_full_universe = lambda *a, **k: {"BTC": 1}
        db = os.path.join(tmp, "signals.db")
        box = start_service(tmp, db)
        port = box.get("port")
        got = wait_for(lambda: (
            (get(port, "/scanner-health")[1].get("data") or {})
            .get("sweep_count") or 0) > 0)
        check("a completed sweep is visible", bool(got))
        st, body = get(port, "/scanner-health")
        data = body.get("data") or {}
        stages = data.get("stages") or {}
        check("the stage durations are published",
              isinstance(stages, dict) and stages, str(stages)[:200])
        check("every published stage carries a measured duration",
              all(isinstance((v or {}).get("duration_s"), (int, float))
                  for v in stages.values()), str(stages)[:200])
        check("every published stage carries its outcome",
              all((v or {}).get("outcome") in
                  ("ok", "skipped", "failed", "not_due", "partial")
                  for v in stages.values()), str(stages)[:200])
        check("the existing scanner-health keys keep their meaning",
              {"status", "sweep_count", "last_sweep_ts",
               "last_sweep_duration_s", "observed_sweep_interval_s",
               "data_lag_s", "first_sweep", "stages"} <= set(data),
              str(sorted(data)))
        check("the whole-iteration duration is still the sweep duration",
              isinstance(data.get("last_sweep_duration_s"), (int, float))
              and data["last_sweep_duration_s"] >= 0, str(data))
        stop_service(box)
    finally:
        dmod.colmod.collect_full_universe = real_collect
        dmod.fmod.collect_funding = real_fund
        dmod.smod.maybe_collect = real_settle
        dmod.pmod.maybe_collect = real_prices
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    for fn in (test_timing_log_and_cadence,
               test_readyz_and_first_sweep,
               test_cadence_persisted_across_restarts,
               test_failed_stage_is_not_marked_as_run,
               test_bar_index,
               test_bounded_rebuild_is_identical,
               test_stop_flag_interrupts_the_iteration,
               test_one_sweep_record_per_iteration,
               test_stage_durations_in_scanner_health):
        print(f"--- {fn.__name__}")
        try:
            fn()
        except Exception as e:  # noqa: BLE001 -- a crash is a failure
            import traceback
            traceback.print_exc()
            FAILURES.append(fn.__name__)
    print()
    if FAILURES:
        print(f"FAIL  tests/test_ws_f_startup.py  "
              f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
        sys.exit(1)
    print(f"ALL PASS  tests/test_ws_f_startup.py  ({CHECKS} checks)")


if __name__ == "__main__":
    main()
