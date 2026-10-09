"""Stake-aware universe rotation tests: disjoint groups, shortfall spill,
rotation pointer persistence. Offline: fake tickers/details fixtures only.

Fixture naming is deliberately INVERTED against volume (COIN199 is the most
liquid coin), so an implementation that sorts by symbol instead of 24h volume
produces visibly wrong groups.
"""

import contextlib
import inspect
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
    The first `tradeable_count` coins by volume have min_notional $0.05
    (tradeable at $0.10 stake); the rest are $5.00 (not tradeable)."""
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


def test_groups_disjoint_and_fill_150():
    print("=== 80 tradeable + 40 tail + 30 rotation fill 150, disjoint ===")
    tk, det = make_fixture(tradeable_count=100)
    restore = install(tk, det, bybit_set=None)
    try:
        uni = scanmod.build_universe(STAKE)
    finally:
        restore()

    coins = [c for _, c in uni]
    check("len == 150", len(uni) == 150, str(len(uni)))
    check("no duplicate coins", len(coins) == len(set(coins)),
          f"{len(coins)} vs {len(set(coins))}")

    # Group 1: 80 stake-tradeable coins, ranked by 24h volume (rank 0 first).
    check("group1 = 80 tradeable, vol-ranked",
          coins[:80] == [C(i) for i in range(199, 119, -1)], str(coins[:80]))
    ok = True
    for sym, coin in uni[:80]:
        n = scanmod.mexc.min_notional(sym, det[sym], tk[sym]["lastPrice"])
        ok = ok and n is not None and n <= STAKE
    check("group1 min_notional <= stake", ok)

    # Group 2: tail = the 40 lowest-volume coins (no Bybit map available).
    check("group2 = tail, 40 lowest-vol slice",
          coins[80:120] == [C(i) for i in range(40)], str(coins[80:120]))

    # Group 3: rotation = 30 from the remainder at pointer 0, disjoint.
    check("group3 = rotation 30 from remainder",
          coins[120:150] == [C(i) for i in range(119, 89, -1)],
          str(coins[120:150]))
    check("groups pairwise disjoint",
          len(set(coins[:80]) & set(coins[80:120])) == 0
          and len(set(coins[:80]) & set(coins[120:150])) == 0
          and len(set(coins[80:120]) & set(coins[120:150])) == 0)


def test_shortfall_spills():
    print("=== only 10 tradeable -> shortfall spills, still 150 ===")
    tk, det = make_fixture(tradeable_count=10)
    restore = install(tk, det, bybit_set=None)
    try:
        uni = scanmod.build_universe(STAKE)
    finally:
        restore()

    coins = [c for _, c in uni]
    check("len == 150", len(uni) == 150, str(len(uni)))
    check("no duplicate coins", len(coins) == len(set(coins)),
          f"{len(coins)} vs {len(set(coins))}")
    check("group1 = all 10 tradeable",
          coins[:10] == [C(i) for i in range(199, 189, -1)], str(coins[:10]))
    ok = True
    for sym, coin in uni[:10]:
        n = scanmod.mexc.min_notional(sym, det[sym], tk[sym]["lastPrice"])
        ok = ok and n is not None and n <= STAKE
    check("group1 min_notional <= stake", ok)
    check("group2 = tail 40 lowest-vol",
          coins[10:50] == [C(i) for i in range(40)], str(coins[10:50]))
    check("group3 = rotation 30",
          coins[50:80] == [C(i) for i in range(189, 159, -1)],
          str(coins[50:80]))
    check("spill = 70 more from remainder",
          coins[80:150] == [C(i) for i in range(159, 89, -1)],
          str(coins[80:150]))


def test_rotation_pointer_persists_and_advances():
    print("=== rotation pointer advances and survives reopen ===")
    db = os.path.join(tempfile.mkdtemp(), "uni.db")
    st = Store(db)
    check("fresh pointer == 0", scanmod.rotation_pointer(st) == 0,
          str(scanmod.rotation_pointer(st)))

    tk, det = make_fixture(tradeable_count=100)
    restore = install(tk, det, bybit_set=None)
    try:
        uni1 = scanmod.build_universe(STAKE, store=st)
        check("cycle1 fills 150", len(uni1) == 150, str(len(uni1)))
        check("cycle1 pointer == 30 (rotation window consumed)",
              scanmod.rotation_pointer(st) == 30,
              str(scanmod.rotation_pointer(st)))
        check("cycle1 rotation window",
              [c for _, c in uni1[120:150]] == [C(i) for i in range(119, 89, -1)])

        uni2 = scanmod.build_universe(STAKE, store=st)
        check("cycle2 pointer == 60", scanmod.rotation_pointer(st) == 60,
              str(scanmod.rotation_pointer(st)))
        check("cycle2 rotation window moved",
              [c for _, c in uni2[120:150]] == [C(i) for i in range(89, 59, -1)],
              str([c for _, c in uni2[120:150]]))
        check("cycle2 differs from cycle1", uni2 != uni1)
        check("cycle2 still disjoint, 150",
              len(uni2) == 150 and len({c for _, c in uni2}) == 150)
    finally:
        restore()

    check("advance_rotation adds consumed",
          scanmod.advance_rotation(st, 5) == 65
          and scanmod.rotation_pointer(st) == 65,
          str(scanmod.rotation_pointer(st)))
    st.close()

    st2 = Store(db)
    check("pointer survives reopen", scanmod.rotation_pointer(st2) == 65,
          str(scanmod.rotation_pointer(st2)))
    st2.close()


def test_tail_prefers_mexc_only_when_bybit_map_available():
    print("=== tail = MEXC-only coins when the Bybit map answers ===")
    tk, det = make_fixture(tradeable_count=100)
    # Bybit lists everything EXCEPT COIN040..COIN079 -> those 40 are MEXC-only.
    bybit_set = {C(i) for i in range(N)} - {C(i) for i in range(40, 80)}
    restore = install(tk, det, bybit_set=bybit_set)
    try:
        uni = scanmod.build_universe(STAKE)
    finally:
        restore()

    coins = [c for _, c in uni]
    check("len == 150", len(uni) == 150, str(len(uni)))
    check("no duplicate coins", len(coins) == len(set(coins)))
    check("tail = the 40 MEXC-only coins",
          coins[80:120] == [C(i) for i in range(40, 80)],
          str(coins[80:120]))
    # Fallback would have picked the lowest-volume coin (COIN000, Bybit-listed).
    check("lowest-vol Bybit coin NOT in tail", C(0) not in coins[80:120],
          str(coins[80:120]))
    check("groups disjoint",
          len(set(coins[:80]) & set(coins[80:120])) == 0
          and len(set(coins[:80]) & set(coins[120:150])) == 0
          and len(set(coins[80:120]) & set(coins[120:150])) == 0)


def test_missing_detail_row_fails_closed():
    print("=== coin with no detail row: unknown min_notional, not tradeable ===")
    tk, det = make_fixture(tradeable_count=100)
    del det[C(199) + "_USDT"]           # most liquid coin has no detail row
    restore = install(tk, det, bybit_set=None)
    try:
        uni = scanmod.build_universe(STAKE)
    finally:
        restore()

    coins = [c for _, c in uni]
    check("no crash, len == 150", len(uni) == 150, str(len(uni)))
    check("no duplicate coins", len(coins) == len(set(coins)))
    check("unknown-minimum coin excluded from tradeable group",
          C(199) not in coins[:80], str(coins[:80]))
    check("but still covered by a group", C(199) in coins)


def test_cli_scan_advances_pointer():
    """Review finding 2: main() must open a Store and pass it to
    build_universe, so two successive CLI runs against the same --db
    advance the rotation pointer (and --no-logs stays in-memory)."""
    print("=== CLI main(): two runs with the same --db advance the pointer ===")
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "cli.db")
    tk, det = make_fixture(tradeable_count=100)
    restore = install(tk, det, bybit_set=None)
    orig = (scanmod.analyse_one, sys.argv, sys.stdout)
    scanmod.analyse_one = lambda *a, **k: None   # scoring out of scope, no network

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
        st = Store(db)
        ptr1 = scanmod.rotation_pointer(st)
        st.close()
        check("run1 exits 0", code1 == 0, f"exit={code1} {out1[-200:]}")
        check("run1 advances pointer 0 -> 30", ptr1 == 30, str(ptr1))

        code2, out2 = run_cli("--db", db)
        st = Store(db)
        ptr2 = scanmod.rotation_pointer(st)
        st.close()
        check("run2 exits 0", code2 == 0, f"exit={code2} {out2[-200:]}")
        check("run2 advances pointer 30 -> 60", ptr2 == 60, str(ptr2))

        db2 = os.path.join(tmp, "nologs.db")
        code3, out3 = run_cli("--db", db2, "--no-logs")
        check("--no-logs run exits 0", code3 == 0, f"exit={code3} {out3[-200:]}")
        check("--no-logs uses in-memory store (no db file created)",
              not os.path.exists(db2), str(os.path.exists(db2)))
    finally:
        scanmod.analyse_one, sys.argv, sys.stdout = orig
        restore()


def test_degraded_venue_continues():
    """Task 6: Bybit down -> the tail falls back, MEXC-only scoring
    continues, and the venue failure is COUNTED and MARKED, never silent.
    Also covers the loop's error contracts: TimeoutError/ValueError shapes
    must count, not crash (deferred Task 1/2 notes); per-coin failures land
    in `failed N/M` with a last-error per coin; garbage venue fields count
    instead of raising; the universe feed is cached 10 minutes."""
    print("=== degraded venue: counted, marked, never silent ===")
    from proto import app as appmod
    from proto import bybit as bybitmod
    from proto.scorer import Scorecard

    tk, det = make_fixture(tradeable_count=100)

    def fake_bars(sym, interval, limit=200):
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
        # 1. Bybit raises -> tail falls back to the lowest-volume slice and
        #    the universe still fills its 150 budget.
        uni = scanmod.build_universe(STAKE)
        coins = [c for _, c in uni]
        check("bybit down: universe still fills 150", len(uni) == 150, str(len(uni)))
        check("bybit down: tail falls back to lowest-vol 40",
              coins[80:120] == [C(i) for i in range(40)], str(coins[80:120]))

        # 2. The venue failure is COUNTED and MARKED, not swallowed.
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
                uni = scanmod.build_universe(STAKE)
                check(f"{type(exc).__name__}: universe still fills",
                      len(uni) == 150, str(len(uni)))
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
        a = appmod.App(Args())
        a.refresh_universe()
        a.scan_once()
        check("app scans while bybit down", len(a.cards) == 10, str(len(a.cards)))
        check("header: failed 0/10", "failed 0/10" in a.status, a.status)
        check("header: degraded venue marked", "DEGRADED" in a.status, a.status)

        # 6. Per-coin venue failure: counted in failed N/M, last error kept,
        #    the loop never crashes.
        def raise_klines(sym, interval, limit=200):
            raise scanmod.mexc.MexcError("api error 429")
        scanmod.mexc.klines = raise_klines
        a.scan_once()
        check("failed coins excluded from cards", a.cards == [], str(len(a.cards)))
        check("header: failed 10/10", "failed 10/10" in a.status, a.status)
        check("per-coin last error kept",
              len(a.last_errors) == 10
              and all("MexcError" in m for m in a.last_errors.values()),
              str(a.last_errors))

        # 7. Garbage venue field: counted per-coin failure, never a crash
        #    (Task 1 deferred: unguarded float() on price strings). The
        #    garbage coin is a TAIL coin - guaranteed in the universe every
        #    cycle - and coins=150 so it is actually ranked and scored.
        scanmod.mexc.klines = fake_bars
        tk_bad = dict(tk)
        tk_bad[C(1) + "_USDT"] = dict(tk[C(1) + "_USDT"], lastPrice="garbage")
        scanmod.mexc.tickers = lambda: tk_bad
        a.args.coins = 150
        a.refresh_universe()
        check("garbage price: universe still builds", len(a.uni) == 150,
              str(len(a.uni)))
        check("garbage-price coin still covered", C(1) in [c for _, c in a.uni])
        a.scan_once()
        check("garbage price: counted, not crashed",
              a.failed == 1 and len(a.cards) == 149,
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
    """Review finding (Important): `main()` defaulted --coins to 120 while the
    universe budget is 150, so every CLI scan silently dropped the guaranteed
    40-coin tail. Pin: the parser default EQUALS the budget constant - the
    test imports the constant instead of restating 150."""
    print("=== CLI --coins default == universe budget ===")
    budget = getattr(scanmod, "UNIVERSE_BUDGET", None)
    check("UNIVERSE_BUDGET constant exported", isinstance(budget, int), str(budget))

    # End to end: a default CLI run must rank and analyse the whole budget.
    tk, det = make_fixture(tradeable_count=100)
    restore = install(tk, det, bybit_set=None)
    orig = (scanmod.analyse_one, sys.argv, sys.stdout)
    scanmod.analyse_one = lambda *a, **k: None     # scoring out of scope
    out = ""
    try:
        sys.argv = ["scan", "--db", os.path.join(tempfile.mkdtemp(), "cli.db")]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            scanmod.main()
        out = buf.getvalue()
    finally:
        scanmod.analyse_one, sys.argv, sys.stdout = orig
        restore()
    m = re.search(r"analysing top (\d+)", out)
    check("default run analyses the full budget (tail not dropped)",
          m is not None and budget is not None and int(m.group(1)) == budget,
          m.group(0) if m else out[-200:])

    parser = getattr(scanmod, "cli_parser", None)
    default = parser().get_default("coins") if parser else None
    check("--coins parser default == UNIVERSE_BUDGET",
          default is not None and budget is not None and default == budget,
          f"default={default} budget={budget}")
    bdef = inspect.signature(scanmod.build_universe).parameters["budget"].default
    check("build_universe default budget == UNIVERSE_BUDGET",
          budget is not None and bdef == budget,
          f"budget={bdef} constant={budget}")


def run(fn):
    print(f"--- {fn.__name__}")
    try:
        fn()
    except Exception as e:
        print(f"  FAIL  {fn.__name__}  raised {type(e).__name__}: {e}")
        FAILURES.append(fn.__name__)


for t in (test_groups_disjoint_and_fill_150,
          test_shortfall_spills,
          test_rotation_pointer_persists_and_advances,
          test_tail_prefers_mexc_only_when_bybit_map_available,
          test_missing_detail_row_fails_closed,
          test_cli_scan_advances_pointer,
          test_find_symbol,
          test_cli_default_coins_equals_universe_budget,
          test_degraded_venue_continues):
    run(t)

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
