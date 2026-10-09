"""Bybit OI wiring tests (spec SS4): analyse_one passes a real Bybit
open-interest change for Bybit-shared coins and None (funding-only path)
for MEXC-only coins or while Bybit is down - with every failure COUNTED in
venue health and never fatal to the score. Offline: fake venue feeds only.

The symbol map is established through the real _bybit_symbol_set() helper
(the universe refresh is what fills it), so the tests drive the production
path rather than poking module internals.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import bybit as bybitmod
from proto import scan as scanmod
from proto.scorer import Scorecard

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


# 60 rising 5m bars: enough history for real scoring, no thin-history veto.
BARS = []
_px = 1.0
for _i in range(60):
    _c = _px * 1.002
    BARS.append({"ts": 1_700_000_000 + _i * 300, "o": _px, "h": _c * 1.001,
                 "l": _px * 0.999, "c": _c, "vol": 1e5, "amount": 1e5 * _c})
    _px = _c

BIDS = [(0.999, 100.0)]
ASKS = [(1.001, 100.0)]
TK = {"lastPrice": 1.0, "amount24": 5_000_000.0, "riseFallRate": 0.01,
      "fundingRate": 0.0001, "maxFundingRate": 0.0018}
DETAIL = {"minVol": 1, "contractSize": 0.05}
DET = {"SHARED_USDT": DETAIL, "MEXCONLY_USDT": DETAIL}
TICK = {"SHARED_USDT": TK, "MEXCONLY_USDT": TK}

# Fake Bybit universe: SHARED is listed on both venues, MEXCONLY is not.
BYBIT_TICKERS = {"SHAREDUSDT": {}, "OTHERUSDT": {}}


def fake_bars(sym, interval="5m", limit=200):
    return BARS


def fake_depth(sym, limit=20):
    return (BIDS, ASKS)


def install(bybit_tickers, oi_state):
    """Patch venue feeds + rate caps for offline analysis. Returns restore()."""
    orig = (scanmod.mexc.klines, scanmod.mexc.depth, scanmod.LIMITER,
            scanmod.STAGGER_S, scanmod.VENUE_STATE, bybitmod.tickers,
            bybitmod.oi_state)
    scanmod.mexc.klines = fake_bars
    scanmod.mexc.depth = fake_depth
    scanmod.LIMITER = scanmod.RateLimiter(rate=10_000)   # offline = no waits
    scanmod.STAGGER_S = 0
    scanmod.VENUE_STATE = {}
    bybitmod.tickers = bybit_tickers
    bybitmod.oi_state = oi_state

    def restore():
        (scanmod.mexc.klines, scanmod.mexc.depth, scanmod.LIMITER,
         scanmod.STAGGER_S, scanmod.VENUE_STATE, bybitmod.tickers,
         bybitmod.oi_state) = orig
    return restore


def test_shared_coin_gets_oi():
    print("=== shared coin gets real OI; MEXC-only stays funding-only ===")
    oi_calls = []
    seen = []                      # oi_change_pct exactly as score_coin got it
    seen_notion = []               # oi_notional exactly as score_coin got it

    def fake_oi(sym, *a, **k):
        oi_calls.append(sym)
        return 4.2, 1000.0

    def fake_tickers():
        return dict(BYBIT_TICKERS)

    restore = install(fake_tickers, fake_oi)
    real_score_coin = scanmod.score_coin

    def spy(*a, **k):
        seen.append(k.get("oi_change_pct"))
        seen_notion.append(k.get("oi_notional"))
        return real_score_coin(*a, **k)

    try:
        scanmod.score_coin = spy
        # The universe refresh is what fills the symbol map (one Bybit
        # ticker request per refresh, never one per coin).
        scanmod._bybit_symbol_set()

        sc_shared = scanmod.analyse_one("SHARED_USDT", "SHARED", DETAIL, TK, 0.10)
        check("shared coin scored", sc_shared is not None, str(sc_shared))
        check("oi_change called with the raw Bybit symbol",
              oi_calls == ["SHAREDUSDT"], str(oi_calls))
        check("oi_change_pct reaches the scorer", seen == [4.2], str(seen))
        check("scorecard carries the OI change",
              sc_shared is not None and sc_shared.oi_change_pct == 4.2,
              str(getattr(sc_shared, "oi_change_pct", None)))
        check("notional = units x price reaches the scorer",
              seen_notion == [1000.0], str(seen_notion))
        check("scorecard carries the OI notional",
              sc_shared is not None and sc_shared.oi_notional == 1000.0,
              str(getattr(sc_shared, "oi_notional", None)))

        sc_mexc = scanmod.analyse_one("MEXCONLY_USDT", "MEXCONLY",
                                      DETAIL, TK, 0.10)
        check("MEXC-only coin scored", sc_mexc is not None, str(sc_mexc))
        check("MEXC-only: no Bybit OI request",
              oi_calls == ["SHAREDUSDT"], str(oi_calls))
        check("MEXC-only: funding-only None reaches the scorer",
              len(seen) == 2 and seen[1] is None, str(seen))
        check("MEXC-only: scorecard OI is None",
              sc_mexc is not None and sc_mexc.oi_change_pct is None,
              str(getattr(sc_mexc, "oi_change_pct", None)))
        check("MEXC-only: scorecard notional is None",
              sc_mexc is not None and sc_mexc.oi_notional is None,
              str(getattr(sc_mexc, "oi_notional", None)))
    finally:
        scanmod.score_coin = real_score_coin
        restore()


def test_bybit_down_falls_back():
    print("=== Bybit down: OI -> None (funding-only), counted, scores returned ===")
    oi_calls = []

    def raise_oi(sym, *a, **k):
        oi_calls.append(sym)
        raise bybitmod.BybitError(f"{sym}: api error code=10001")

    def raise_tickers():
        raise bybitmod.BybitError("api error code=10001")

    def fake_tickers():
        return dict(BYBIT_TICKERS)

    ranked = [("SHARED_USDT", "SHARED"), ("MEXCONLY_USDT", "MEXCONLY")]
    restore = install(fake_tickers, raise_oi)
    try:
        # 1. Map known from the universe refresh, OI endpoint failing:
        #    the failure is COUNTED in venue health, never raised.
        scanmod._bybit_symbol_set()             # good tickers -> map known
        errors = {}
        cards = scanmod.score_universe(ranked, DET, TICK, 0.10,
                                       errors=errors, stagger=0)
        check("scores still returned", len(cards) == 2, str(len(cards)))
        check("cards are real scorecards",
              all(isinstance(c, Scorecard) for c in cards))
        check("no per-coin failures", errors == {}, str(errors))
        check("shared coin attempted the OI request",
              oi_calls == ["SHAREDUSDT"], str(oi_calls))
        check("OI failure -> funding-only None on every card",
              all(c.oi_change_pct is None for c in cards),
              str([c.oi_change_pct for c in cards]))
        h = scanmod.venue_health()["bybit"]
        check("OI failure marked degraded", h["ok"] is False, str(h))
        check("OI failure counted", h["fails"] == 1, str(h))
        check("OI last error surfaced", "BybitError" in (h["last_err"] or ""),
              str(h))

        # 2. Bybit fully down at the universe refresh: the map is not
        #    refreshed, OI stays None without hammering a dead venue.
        bybitmod.tickers = raise_tickers
        check("map refresh degrades to None",
              scanmod._bybit_symbol_set() is None)
        errors = {}
        cards = scanmod.score_universe(ranked, DET, TICK, 0.10,
                                       errors=errors, stagger=0)
        check("bybit down: scores still returned", len(cards) == 2,
              str(len(cards)))
        check("bybit down: still no per-coin failures", errors == {},
              str(errors))
        check("bybit down: no OI request against the dead venue",
              oi_calls == ["SHAREDUSDT"], str(oi_calls))
        check("bybit down: all cards funding-only",
              all(c.oi_change_pct is None for c in cards),
              str([c.oi_change_pct for c in cards]))
        h = scanmod.venue_health()["bybit"]
        check("bybit down: refresh failure counted too", h["fails"] == 2,
              str(h))
        check("bybit down: venue stays marked degraded", h["ok"] is False,
              str(h))
    finally:
        restore()


def run(fn):
    print(f"--- {fn.__name__}")
    try:
        fn()
    except Exception as e:
        print(f"  FAIL  {fn.__name__}  raised {type(e).__name__}: {e}")
        FAILURES.append(fn.__name__)


for t in (test_shared_coin_gets_oi, test_bybit_down_falls_back):
    run(t)

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
