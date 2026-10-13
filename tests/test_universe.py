"""Stakeless universe tests: full eligible set, vol-ranked, synthetics out.

Dashboard spec section 2: the universe is every eligible MEXC USDT perp.
Stake groups, `tradeable` gating, the tail and the rotation pointer are
removed; `stake` is a compatibility shim and plays no role. Offline: fake
tickers/details fixtures only.

Fixture naming is deliberately INVERTED against volume (COIN199 is the most
liquid coin), so an implementation that sorts by symbol instead of 24h volume
produces visibly wrong order.
"""

import contextlib
import io
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import scan as scanmod
from proto.store import Store

FAILURES = []

N = 200
STAKE = 0.10


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def C(i):
    return f"COIN{i:03d}"


def make_fixture(tradeable_count):
    """200 fake coins. Rank 0 = highest 24h volume, named COIN199 (inverted).

    The first `tradeable_count` coins by volume have min_notional $0.05;
    the rest are $5.00. Under the stakeless model that distinction must
    change NOTHING about membership."""
    tk, det = {}, {}
    for rank in range(N):
        sym = C(N - 1 - rank) + "_USDT"
        tk[sym] = {"lastPrice": 1.0, "amount24": float((N - rank) * 1_000_000),
                   "riseFallRate": 0.01, "fundingRate": 0.0}
        det[sym] = {"minVol": 1,
                    "contractSize": 0.05 if rank < tradeable_count else 5.0}
    return tk, det


def install(tk, det, bybit_set=None):
    """Patch scan's venue feeds. Returns a restore callable."""
    orig = (scanmod.mexc.tickers, scanmod.mexc.details, scanmod._bybit_symbol_set)
    scanmod.mexc.tickers = lambda: tk
    scanmod.mexc.details = lambda: det
    scanmod._bybit_symbol_set = lambda: bybit_set

    def restore():
        (scanmod.mexc.tickers, scanmod.mexc.details,
         scanmod._bybit_symbol_set) = orig
    return restore


def test_full_eligible_set_vol_ranked():
    print("=== stakeless: full eligible set, vol-ranked, no stake groups ===")
    tk, det = make_fixture(tradeable_count=100)
    restore = install(tk, det, bybit_set=None)
    try:
        uni = scanmod.build_universe(STAKE)
    finally:
        restore()

    coins = [c for _, c in uni]
    check("len == full 200", len(uni) == 200, str(len(uni)))
    check("no duplicate coins", len(coins) == len(set(coins)),
          f"{len(coins)} vs {len(set(coins))}")
    check("vol-ranked, most liquid first",
          coins == [C(i) for i in range(199, -1, -1)], str(coins[:5]))


def test_stake_value_ignored():
    print("=== stake shim: membership identical at any stake ===")
    tk, det = make_fixture(tradeable_count=10)
    restore = install(tk, det, bybit_set=None)
    try:
        tiny = [c for _, c in scanmod.build_universe(0.01)]
        huge = [c for _, c in scanmod.build_universe(1000.0)]
        none = [c for _, c in scanmod.build_universe(stake=None)]
        bare = [c for _, c in scanmod.build_universe()]
    finally:
        restore()
    check("tiny vs huge stake identical", tiny == huge)
    check("stake=None identical", tiny == none)
    check("omitted stake identical", tiny == bare)
    check("all 200 at every stake", len(tiny) == 200, str(len(tiny)))


def test_high_min_notional_included():
    print("=== high-min-notional coins included (no tradeable gating) ===")
    tk, det = make_fixture(tradeable_count=10)
    restore = install(tk, det, bybit_set=None)
    try:
        uni = scanmod.build_universe(STAKE)
    finally:
        restore()
    coins = [c for _, c in uni]
    heavy = [c for _, c in uni
             if scanmod.mexc.min_notional(c + "_USDT", det[c + "_USDT"],
                                          1.0) is not None
             and scanmod.mexc.min_notional(c + "_USDT", det[c + "_USDT"],
                                           1.0) > STAKE]
    check("190 heavy coins all present", len(heavy) == 190, str(len(heavy)))
    check("heavy coins in universe", all(c in coins for c in heavy))


def test_budget_caps_top_vol():
    print("=== budget caps to the top-N volume-ranked coins ===")
    tk, det = make_fixture(tradeable_count=100)
    restore = install(tk, det, bybit_set=None)
    try:
        uni = scanmod.build_universe(budget=50)
        full = scanmod.build_universe(budget=None)
    finally:
        restore()
    coins = [c for _, c in uni]
    check("budget=50 caps at 50", len(uni) == 50, str(len(uni)))
    check("cap keeps the most liquid",
          coins == [C(i) for i in range(199, 149, -1)], str(coins[:5]))
    check("budget=None is the full set", len(full) == 200, str(len(full)))


def test_missing_detail_row_included():
    print("=== coin with no detail row: unknown minimum still included ===")
    tk, det = make_fixture(tradeable_count=100)
    del det[C(199) + "_USDT"]           # most liquid coin has no detail row
    restore = install(tk, det, bybit_set=None)
    try:
        uni = scanmod.build_universe(STAKE)
    finally:
        restore()

    coins = [c for _, c in uni]
    check("no crash, len == 200", len(uni) == 200, str(len(uni)))
    check("no duplicate coins", len(coins) == len(set(coins)))
    check("unknown-minimum coin included (min_notional never gates)",
          C(199) in coins, str(coins[:5]))


def test_synthetic_excluded():
    print("=== synthetic plates still excluded ===")
    tk, det = make_fixture(tradeable_count=100)
    tk["STOCKX_USDT"] = {"lastPrice": 5.0, "amount24": 999_000_000.0,
                         "riseFallRate": 0.01, "fundingRate": 0.0}
    det["STOCKX_USDT"] = {"minVol": 1, "contractSize": 1.0,
                          "conceptPlate": ["mc-trade-zone-Stock"]}
    restore = install(tk, det, bybit_set=None)
    try:
        uni = scanmod.build_universe()
    finally:
        restore()
    coins = [c for _, c in uni]
    check("huge-volume synthetic excluded", "STOCKX" not in coins,
          str(coins[:5]))
    check("len == 200 eligible", len(uni) == 200, str(len(uni)))


def test_cli_scan_records_sweep():
    """CLI main(): one run sweeps the full universe and records a
    sweep-complete journal event + dashboard snapshot (no rotation pointer;
    --no-logs stays in-memory)."""
    print("=== CLI main(): full sweep recorded, pointer gone ===")
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "cli.db")
    tk, det = make_fixture(tradeable_count=100)
    restore = install(tk, det, bybit_set=None)
    orig = (scanmod.analyse_one, scanmod._bybit_symbol_set, sys.argv,
            sys.stdout)
    scanmod.analyse_one = lambda *a, **k: None   # scoring out of scope, no network
    scanmod._bybit_symbol_set = lambda: None

    def run_cli(*argv_extra):
        sys.argv = ["scan", *argv_extra]
        buf = io.StringIO()
        code = 0
        try:
            with contextlib.redirect_stdout(buf):
                scanmod.main()
        except SystemExit as e:            # argparse errors
            code = e.code or 0
        return code, buf.getvalue()

    try:
        code1, out1 = run_cli("--db", db)
        check("run1 exits 0", code1 == 0, f"exit={code1} {out1[-200:]}")
        st = Store(db)
        kinds = [r[0] for r in st.conn.execute(
            "SELECT kind FROM watcher_events ORDER BY rowid")]
        check("sweep-complete recorded", "sweep-complete" in kinds,
              str(kinds))
        snap = st.conn.execute(
            "SELECT eligible_count, evaluated_count FROM dashboard_snapshot"
            " ORDER BY rowid DESC LIMIT 1").fetchone()
        check("snapshot recorded", snap is not None, "no snapshot row")
        if snap is not None:
            check("snapshot eligible == 200", snap[0] == 200, str(snap))
            check("snapshot evaluated == analysis budget",
                  snap[1] == scanmod.UNIVERSE_BUDGET, str(snap))
        st.close()

        db2 = os.path.join(tmp, "nologs.db")
        code3, out3 = run_cli("--db", db2, "--no-logs")
        check("--no-logs run exits 0", code3 == 0, f"exit={code3} {out3[-200:]}")
        check("--no-logs uses in-memory store (no db file created)",
              not os.path.exists(db2), str(os.path.exists(db2)))
    finally:
        (scanmod.analyse_one, scanmod._bybit_symbol_set, sys.argv,
         sys.stdout) = orig
        restore()


def test_degraded_venue_continues():
    """Universe build needs only MEXC tickers/details; a dead Bybit degrades
    the OI map refresh (counted, marked) while the full universe still fills.
    Also covers the loop's error contracts: TimeoutError/ValueError shapes
    must count, not crash; per-coin failures land in `failed N/M` with a
    last-error per coin; garbage venue fields count instead of raising; the
    universe feed is cached 10 minutes."""
    print("=== degraded venue: counted, marked, never silent ===")
    from proto import app as appmod
    from proto import bybit as bybitmod
    from proto.scorer import Scorecard

    tk, det = make_fixture(tradeable_count=100)

    def fake_bars(sym, interval="5m", limit=200, end=None):
        """60 rising bars - enough for real scoring, no thin_history veto."""
        base, bars, px = 1_700_000_000, [], 1.0
        for i in range(60):
            c = px * 1.002
            bars.append({"ts": base + i * 300, "o": px, "h": c * 1.001,
                         "l": px * 0.999, "c": c, "vol": 1e5, "amount": 1e5 * c})
            px = c
        return bars

    def fake_depth(sym, limit=20):
        return ([(0.999, 100.0)], [(1.001, 100.0)])

    def raise_exc(exc):
        raise exc

    class Args:
        stake = STAKE
        coins = 10
        interval = 60
        db = os.path.join(tempfile.mkdtemp(), "degraded.db")
        log_threshold = 24.0
        write_logs = False

    # Real analyse_one/score_universe/build_universe; fake venue responses.
    # Note: _bybit_symbol_set is NOT patched here - the real one must call
    # bybit.tickers (patched to raise) and degrade through venue health.
    orig = (scanmod.mexc.tickers, scanmod.mexc.details, scanmod.mexc.klines,
            scanmod.mexc.depth, scanmod.LIMITER, scanmod.STAGGER_S,
            bybitmod.tickers)
    scanmod.mexc.tickers = lambda: tk
    scanmod.mexc.details = lambda: det
    scanmod.mexc.klines = fake_bars
    scanmod.mexc.depth = fake_depth
    scanmod.LIMITER = scanmod.RateLimiter(rate=10_000)   # offline = no waits
    scanmod.STAGGER_S = 0
    scanmod.VENUE_STATE.clear()
    bybitmod.tickers = lambda: raise_exc(
        bybitmod.BybitError("api error code=10001"))

    def restore():
        (scanmod.mexc.tickers, scanmod.mexc.details, scanmod.mexc.klines,
         scanmod.mexc.depth, scanmod.LIMITER, scanmod.STAGGER_S,
         bybitmod.tickers) = orig

    try:
        # 1. Bybit raises -> the OI map refresh degrades, but the stakeless
        #    universe still fills the FULL eligible set (no tail fallback).
        uni = scanmod.build_universe(STAKE)
        coins = [c for _, c in uni]
        check("bybit down: universe still fills full 200", len(uni) == 200,
              str(len(uni)))
        check("bybit down: still vol-ranked",
              coins[:3] == [C(199), C(198), C(197)], str(coins[:3]))

        # 2. The refresh path marks the failure: run it and check health.
        a = appmod.App(Args())
        a.refresh_universe()
        h = scanmod.venue_health()["bybit"]
        check("bybit marked degraded", h["ok"] is False, str(h))
        check("bybit failure counted", h["fails"] == 1, str(h))
        check("bybit last error surfaced", "BybitError" in (h["last_err"] or ""),
              str(h))

        # 3. TimeoutError / ValueError shapes also degrade + count, never crash.
        for exc in (TimeoutError("socket read timed out"),
                    ValueError("bad literal for float()"),
                    bybitmod.BybitError("api error code=10001")):
            bybitmod.tickers = (lambda exc=exc: raise_exc(exc))
            try:
                scanmod._bybit_symbol_set()
                check(f"{type(exc).__name__} swallowed+counted", True)
            except Exception as e:
                check(f"{type(exc).__name__} swallowed+counted", False,
                      f"raised {type(e).__name__}: {e}")
        h = scanmod.venue_health()["bybit"]
        check("all failure shapes counted", h["fails"] == 4, str(h))

        # 4. With Bybit down, MEXC-only coins are still fetched and scored.
        bybitmod.tickers = lambda: raise_exc(bybitmod.BybitError("down"))
        errors = {}
        cards = scanmod.score_universe(uni[:10], det, tk, STAKE,
                                       errors=errors, stagger=0)
        check("MEXC-only scores still returned", len(cards) == 10, str(len(cards)))
        check("cards are real scorecards",
              all(isinstance(c, Scorecard) for c in cards))
        check("no per-coin failures", errors == {}, str(errors))

        # 5. App assembly: header counts failures and marks the degraded venue.
        a.scan_once()
        check("app scans while bybit down", len(a.cards) == 10, str(len(a.cards)))
        check("header: failed 0/10", "failed 0/10" in a.status, a.status)
        check("header: degraded venue marked", "DEGRADED" in a.status, a.status)
        check("header: sweep progress shown",
              "sweeping 10/200" in a.status, a.status)

        # 6. Per-coin venue failure: counted in failed N/M, last error kept,
        #    the loop never crashes.
        def raise_klines(sym, interval="5m", limit=200, end=None):
            raise scanmod.mexc.MexcError("api error 429")
        scanmod.mexc.klines = raise_klines
        a.scan_once()
        check("failed coins excluded from cards", a.cards == [], str(len(a.cards)))
        check("header: failed 10/10", "failed 10/10" in a.status, a.status)
        check("per-coin last error kept",
              len(a.last_errors) == 10
              and all("MexcError" in m for m in a.last_errors.values()),
              str(a.last_errors))

        # 7. Garbage venue field: counted per-coin failure, never a crash.
        #    Full-universe sweep means the garbage coin is covered whenever
        #    the analysis budget reaches it - use the full budget here.
        scanmod.mexc.klines = fake_bars
        tk_bad = dict(tk)
        tk_bad[C(1) + "_USDT"] = dict(tk[C(1) + "_USDT"], lastPrice="garbage")
        scanmod.mexc.tickers = lambda: tk_bad
        a.args.coins = 200
        a.refresh_universe()
        check("garbage price: universe still builds", len(a.uni) == 200,
              str(len(a.uni)))
        check("garbage-price coin still covered", C(1) in [c for _, c in a.uni])
        a.scan_once()
        check("garbage price: counted, not crashed",
              a.failed == 1 and len(a.cards) == 199,
              f"failed={a.failed} cards={len(a.cards)}")
        check("garbage price: ValueError in last error",
              "ValueError" in a.last_errors.get(C(1), ""),
              str(a.last_errors.get(C(1))))

        # 8. Universe feed is cached 10 minutes, not rebuilt every scan.
        calls = []
        orig_bu = appmod.build_universe
        appmod.build_universe = lambda *ar, **kw: (calls.append(1), [])[1]
        try:
            a.maybe_refresh_universe()          # just refreshed -> cached
            check("fresh universe not rebuilt", calls == [], str(calls))
            a._uni_at = time.time() - 601       # past the 10-minute TTL
            a.maybe_refresh_universe()
            check("stale universe rebuilt once", calls == [1], str(calls))
        finally:
            appmod.build_universe = orig_bu
    finally:
        restore()


def test_find_symbol():
    print("=== find_symbol: canon match, USDT-only, synthetics excluded ===")
    tk = {"QNT_USDT": {}, "BTC_USDT": {}, "QNT_USDC": {}, "ETHUSD": {}}
    det = {"QNT_USDT": {"conceptPlate": ["mc-trade-zone-RWA"]},
           "BTC_USDT": {"conceptPlate": []}}
    check("exact coin found",
          scanmod.find_symbol("QNT", tk, det) == "QNT_USDT")
    check("case-insensitive", scanmod.find_symbol("qnt", tk, det) == "QNT_USDT")
    check("non-USDT quote ignored",
          scanmod.find_symbol("QNT", {"QNT_USDC": {}}, {}) is None)
    check("unknown coin -> None",
          scanmod.find_symbol("ZZZ", tk, det) is None)
    check("blank -> None", scanmod.find_symbol("  ", tk, det) is None)
    check("synthetic excluded",
          scanmod.find_symbol(
              "AAPLSTOCK", {"AAPLSTOCK_USDT": {}},
              {"AAPLSTOCK_USDT": {"conceptPlate": ["mc-trade-zone-Stock"]}})
          is None)


def test_cli_default_coins_equals_universe_budget():
    """The CLI --coins default still equals UNIVERSE_BUDGET: the budget is
    now the per-cycle ANALYSIS cap over the full stakeless universe (not a
    group fill), so a default run must rank and analyse the whole budget
    slice of the full set."""
    print("=== CLI --coins default == universe budget ===")
    budget = getattr(scanmod, "UNIVERSE_BUDGET", None)
    check("UNIVERSE_BUDGET constant exported", isinstance(budget, int), str(budget))

    # End to end: a default CLI run analyses the full budget slice.
    tk, det = make_fixture(tradeable_count=100)
    restore = install(tk, det, bybit_set=None)
    orig = (scanmod.analyse_one, scanmod._bybit_symbol_set, sys.argv,
            sys.stdout)
    scanmod.analyse_one = lambda *a, **k: None     # scoring out of scope
    scanmod._bybit_symbol_set = lambda: None       # OI refresh: no network
    out = ""
    try:
        sys.argv = ["scan", "--db", os.path.join(tempfile.mkdtemp(), "cli.db")]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            scanmod.main()
        out = buf.getvalue()
    finally:
        (scanmod.analyse_one, scanmod._bybit_symbol_set, sys.argv,
         sys.stdout) = orig
        restore()
    m = re.search(r"analysing top (\d+)", out)
    check("default run analyses the full budget",
          m is not None and budget is not None and int(m.group(1)) == budget,
          m.group(0) if m else out[-200:])
    m2 = re.search(r"sweep complete: evaluated (\d+)/(\d+)", out)
    check("sweep record covers full eligible set",
          m2 is not None and int(m2.group(2)) == 200, m2.group(0) if m2 else out[-200:])

    parser = getattr(scanmod, "cli_parser", None)
    default = parser().get_default("coins") if parser else None
    check("--coins parser default == UNIVERSE_BUDGET",
          default is not None and budget is not None and default == budget,
          f"default={default} budget={budget}")


def run(fn):
    print(f"--- {fn.__name__}")
    try:
        fn()
    except Exception as e:
        print(f"  FAIL  {fn.__name__}  raised {type(e).__name__}: {e}")
        FAILURES.append(fn.__name__)


for t in (test_full_eligible_set_vol_ranked,
          test_stake_value_ignored,
          test_high_min_notional_included,
          test_budget_caps_top_vol,
          test_missing_detail_row_included,
          test_synthetic_excluded,
          test_cli_scan_records_sweep,
          test_find_symbol,
          test_cli_default_coins_equals_universe_budget,
          test_degraded_venue_continues):
    run(t)

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
