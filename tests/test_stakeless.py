"""Task 3: stakeless universe + reference plans (TDD).

- build_universe() takes no required stake arg; stake plays NO role in
  membership (low-min-notional and high-min-notional coins alike included).
- min_notional stays populated on scorecards, never consulted for
  inclusion/flags (no "tradeable" gating anywhere).
- reference_plan(levels) uses REFERENCE_NOTIONAL=100.0; levels, R-multiples,
  stop-risk only. No leverage headroom, no liquidation claims.
- Every completed scan sweep writes a sweep-complete journal event plus a
  dashboard_snapshot row (proto/watcher_journal.py writers).

Offline: fake tickers/details/bars only. No network.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import scan as scanmod
from proto import planner as planmod
from proto import mexc as mexcmod
from proto.store import Store

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


LOW_COIN = "CHEAP"
HIGH_COIN = "HEAVY"
MID_COIN = "MID"


def make_fixture():
    """3 eligible coins + 1 synthetic.

    CHEAP has a dust min_notional ($0.001); HEAVY needs $500 notional.
    Stakeless membership must include both. SYNTH is a stock plate and
    must stay excluded.
    """
    tk = {
        "CHEAP_USDT": {"lastPrice": 1.0, "amount24": 30_000_000.0,
                       "riseFallRate": 0.01, "fundingRate": 0.0},
        "MID_USDT": {"lastPrice": 2.0, "amount24": 20_000_000.0,
                     "riseFallRate": 0.01, "fundingRate": 0.0},
        "HEAVY_USDT": {"lastPrice": 100.0, "amount24": 10_000_000.0,
                       "riseFallRate": 0.01, "fundingRate": 0.0},
        "STOCKX_USDT": {"lastPrice": 5.0, "amount24": 99_000_000.0,
                        "riseFallRate": 0.01, "fundingRate": 0.0},
    }
    det = {
        "CHEAP_USDT": {"minVol": 1, "contractSize": 0.001,
                       "conceptPlate": ["mc-trade-zone-MEME"]},
        "MID_USDT": {"minVol": 1, "contractSize": 1.0,
                     "conceptPlate": ["mc-trade-zone-DeFi"]},
        "HEAVY_USDT": {"minVol": 1, "contractSize": 5.0,
                       "conceptPlate": ["mc-trade-zone-Layer1"]},
        "STOCKX_USDT": {"minVol": 1, "contractSize": 1.0,
                        "conceptPlate": ["mc-trade-zone-Stock"]},
    }
    return tk, det


def install(tk, det):
    orig = (scanmod.mexc.tickers, scanmod.mexc.details)
    scanmod.mexc.tickers = lambda: tk
    scanmod.mexc.details = lambda: det

    def restore():
        (scanmod.mexc.tickers, scanmod.mexc.details) = orig
    return restore


def test_no_stake_gating():
    print("=== build_universe takes no stake; membership ignores stake ===")
    tk, det = make_fixture()
    restore = install(tk, det)
    try:
        uni = scanmod.build_universe(stake=None)  # signature must not require stake
    finally:
        restore()
    coins = [c for _, c in uni]
    check("low-min-notional coin included",
          LOW_COIN in coins, str(coins))
    check("high-min-notional coin included (no tradeable gating)",
          HIGH_COIN in coins, str(coins))
    check("all eligible included", {LOW_COIN, MID_COIN, HIGH_COIN} <= set(coins),
          str(coins))
    check("synthetic filter kept", "STOCKX" not in coins, str(coins))
    check("no tradeable gating marker",
          not any("tradeable" in str(x).lower() for x in uni), str(uni))


def test_stake_plays_no_role():
    print("=== stake value changes nothing ===")
    tk, det = make_fixture()
    restore = install(tk, det)
    try:
        a = [c for _, c in scanmod.build_universe(stake=0.01)]
        b = [c for _, c in scanmod.build_universe(stake=1000.0)]
        c = [c for _, c in scanmod.build_universe()]
    finally:
        restore()
    check("tiny vs huge stake identical", a == b, f"{a} vs {b}")
    check("omitted stake identical", a == c, f"{a} vs {c}")


def test_min_notional_populated_never_consulted():
    print("=== min_notional populated on cards, never gates ===")
    tk, det = make_fixture()
    restore = install(tk, det)
    orig_kl = mexcmod.klines
    mexcmod.klines = lambda sym, interval="5m", limit=200, end=None: [
        {"ts": 1_700_000_000 + i * 300, "o": 1.0, "h": 1.01, "l": 0.99,
         "c": 1.0 + i * 0.0001, "vol": 1e5, "amount": 1e5}
        for i in range(60)]
    orig_depth = mexcmod.depth
    mexcmod.depth = lambda sym, limit=20: ([(0.999, 100.0)], [(1.001, 100.0)])
    orig_lim, orig_stag = scanmod.LIMITER, scanmod.STAGGER_S
    scanmod.LIMITER = scanmod.RateLimiter(rate=10_000)
    scanmod.STAGGER_S = 0
    try:
        cards = scanmod.score_universe(
            [("HEAVY_USDT", HIGH_COIN)], det, tk, None, errors={}, stagger=0)
    finally:
        mexcmod.klines = orig_kl
        mexcmod.depth = orig_depth
        scanmod.LIMITER, scanmod.STAGGER_S = orig_lim, orig_stag
        restore()
    check("heavy coin still scored", len(cards) == 1, str(len(cards)))
    if cards:
        check("min_notional populated", cards[0].min_notional is not None,
              str(cards[0].min_notional))


def test_reference_plan_has_no_leverage():
    print("=== reference_plan: 100 USDT, no leverage/liquidation ===")
    check("REFERENCE_NOTIONAL == 100.0",
          getattr(planmod, "REFERENCE_NOTIONAL", None) == 100.0,
          str(getattr(planmod, "REFERENCE_NOTIONAL", "missing")))
    levels = planmod.plan_levels("LONG", 100.0, 98.0)
    rp = planmod.reference_plan(levels)
    check("notional == 100.0", rp.get("notional") == 100.0, str(rp))
    check("no leverage key", "leverage" not in rp, str(sorted(rp)))
    check("no liquidation claims",
          not any("liquidat" in k.lower() for k in rp), str(sorted(rp)))
    check("no leverage headroom",
          not any("headroom" in k.lower() for k in rp), str(sorted(rp)))
    check("levels carried",
          rp.get("entry_low") == levels["entry_low"]
          and rp.get("stop") == levels["stop"]
          and rp.get("tp1") == levels["tp1"]
          and rp.get("tp2") == levels["tp2"], str(rp))
    entry_mid = (levels["entry_low"] + levels["entry_high"]) / 2
    expect_risk = 100.0 * abs(entry_mid - levels["stop"]) / entry_mid
    check("stop-risk math",
          abs(rp.get("stop_risk", -1) - expect_risk) < 1e-9,
          f"{rp.get('stop_risk')} vs {expect_risk}")
    check("R-multiples present",
          rp.get("r_multiple_tp1") == 2.0 and rp.get("r_multiple_tp2") == 5.0,
          str(rp))


def test_sweep_writes_journal_and_snapshot():
    print("=== completed sweep writes sweep-complete + snapshot ===")
    from proto import app as appmod
    tk, det = make_fixture()

    def fake_bars(sym, interval="5m", limit=200, end=None):
        base, bars, px = 1_700_000_000, [], 1.0
        for i in range(60):
            cc = px * 1.002
            bars.append({"ts": base + i * 300, "o": px, "h": cc * 1.001,
                         "l": px * 0.999, "c": cc, "vol": 1e5,
                         "amount": 1e5 * cc})
            px = cc
        return bars

    def fake_depth(sym, limit=20):
        return ([(0.999, 100.0)], [(1.001, 100.0)])

    orig = (scanmod.mexc.tickers, scanmod.mexc.details, scanmod.mexc.klines,
            scanmod.mexc.depth, scanmod.LIMITER, scanmod.STAGGER_S,
            scanmod._bybit_symbol_set)
    scanmod.mexc.tickers = lambda: tk
    scanmod.mexc.details = lambda: det
    scanmod.mexc.klines = fake_bars
    scanmod.mexc.depth = fake_depth
    scanmod.LIMITER = scanmod.RateLimiter(rate=10_000)
    scanmod.STAGGER_S = 0
    scanmod._bybit_symbol_set = lambda: None  # OI map refresh: no network

    class Args:
        stake = 0.10
        coins = 10
        interval = 60
        db = os.path.join(tempfile.mkdtemp(), "sweep.db")
        log_threshold = 24.0
        write_logs = False

    try:
        app = appmod.App(Args())
        app.scan_once()
        uni_n = len(app.uni)
        conn = app.store.conn
        kinds = [r[0] for r in conn.execute(
            "SELECT kind FROM watcher_events ORDER BY rowid")]
        check("sweep-complete event logged", "sweep-complete" in kinds,
              str(kinds))
        snap = conn.execute(
            "SELECT eligible_count, evaluated_count FROM dashboard_snapshot"
            " ORDER BY rowid DESC LIMIT 1").fetchone()
        check("snapshot row written", snap is not None, "no snapshot row")
        if snap is not None:
            check("snapshot eligible == live universe",
                  snap[0] == uni_n, f"{snap[0]} vs {uni_n}")
            check("snapshot evaluated covers sweep",
                  snap[1] == len(app.cards) + app.failed,
                  f"{snap[1]} vs cards={len(app.cards)} failed={app.failed}")
        prog = getattr(app, "sweep_progress", None)
        check("sweep progress (done, total) tracked",
              prog is not None and prog[1] == uni_n and prog[0] == prog[1],
              str(prog))
        app.store.close()
    finally:
        (scanmod.mexc.tickers, scanmod.mexc.details, scanmod.mexc.klines,
         scanmod.mexc.depth, scanmod.LIMITER, scanmod.STAGGER_S,
         scanmod._bybit_symbol_set) = orig


def run(fn):
    print(f"--- {fn.__name__}")
    try:
        fn()
    except Exception as e:
        print(f"  FAIL  {fn.__name__}  raised {type(e).__name__}: {e}")
        FAILURES.append(fn.__name__)


for t in (test_no_stake_gating,
          test_stake_plays_no_role,
          test_min_notional_populated_never_consulted,
          test_reference_plan_has_no_leverage,
          test_sweep_writes_journal_and_snapshot):
    run(t)

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
