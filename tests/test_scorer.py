"""Scorer tests: symmetry, early-ness, vetoes, and no magnitude leakage."""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import indicators as ind
from proto import scorer

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def mk(cs, vol=None, step=300):
    out = []
    for i, c in enumerate(cs):
        v = vol[i] if vol else 1000.0
        out.append({"ts": 1700000000 + i * step,
                    "o": cs[i - 1] if i else c, "h": c * 1.004, "l": c * 0.996,
                    "c": c, "vol": v, "amount": v * c})
    return out


def mirror(bars):
    """
    Exact geometric mirror: 1/x on every price, highs/lows swapped so the
    candle body and wicks map precisely. Bars built with symmetric percentage
    bands around close mirror correctly; bars built with absolute offsets do
    not, because 1/(c*1.004) is not (1/c)*1.004. Exact mirroring requires
    deriving high/low from the inverted open/close, which is what happens here.
    """
    out = []
    for b in bars:
        inv_c = 1.0 / b["c"]
        inv_o = 1.0 / b["o"]
        # The high of the original is the low of the mirror and vice versa.
        inv_h = 1.0 / b["l"]
        inv_l = 1.0 / b["h"]
        out.append({"ts": b["ts"], "o": inv_o, "h": inv_h, "l": inv_l,
                    "c": inv_c, "vol": b["vol"], "amount": b["amount"]})
    for b in out:
        b["h"], b["l"] = max(b["h"], b["l"]), min(b["h"], b["l"])
        b["amount"] = b["vol"] * b["c"]
    return out


print("=== scorer symmetry: mirrored evidence -> mirrored direction, equal score ===")
random.seed(11)
base = [100 * (1.003 ** i) * (1 + 0.015 * random.uniform(-1, 1)) for i in range(150)]
vbars = mk(base)
# Rising base volume so volume_expansion has something to detect.
spike = [1000.0] * 146 + [9000.0] * 4
vbars = mk(base, vol=spike)
vmir = mirror(vbars)

bid_heavy = [(100.0, 50.0), (99.0, 30.0), (98.0, 20.0)]
ask_heavy = [(101.0, 50.0), (102.0, 30.0), (103.0, 20.0)]

long_sc = scorer.score_coin("TEST", vbars, bid_heavy, ask_heavy,
                            quote_vol_24h=5_000_000, spread_pct=0.2,
                            change_1h_pct=1.0, price=100.0, change_24h_pct=3.0,
                            funding_rate=0.0001, funding_cap=0.0018,
                            oi_change_pct=4.0, min_notional=0.01, tier=1)

short_sc = scorer.score_coin("TEST", vmir, ask_heavy, bid_heavy,
                             quote_vol_24h=5_000_000, spread_pct=0.2,
                             change_1h_pct=-1.0, price=0.01, change_24h_pct=-3.0,
                             funding_rate=-0.0001, funding_cap=0.0018,
                             oi_change_pct=-4.0, min_notional=0.01, tier=1)

check("long setup scores > 0", long_sc.score > 0, f"score={long_sc.score}")
check("short setup scores > 0", short_sc.score > 0, f"score={short_sc.score}")

# Score symmetry is approximate by nature, because two UNSIGNED magnitude
# inputs are ratios that are not invariant under price inversion:
#   atr_pct   = range / price   -> a 1/x mirror changes the price scale
#   macd_hist normalized by ATR -> inherits the same effect
# Measured: atr_pct 0.01381 vs 0.01520 (-9%); macd 0.440 vs 0.273.
# Both feed MAGNITUDE only. Directional lean mirrors exactly (asserted below),
# which is the property that actually matters for bias.
check("mirrored setups produce close scores (<15% apart)",
      abs(long_sc.score - short_sc.score) / max(long_sc.score, 1e-9) < 0.15,
      f"L={long_sc.score} S={short_sc.score}")
check("mirrored setups produce OPPOSITE lean", long_sc.lean * short_sc.lean < 0,
      f"L={long_sc.lean} S={short_sc.lean}")
check("lean values near-mirrored", abs(long_sc.lean + short_sc.lean) < 0.06,
      f"L={long_sc.lean} S={short_sc.lean}")
check("direction resolves LONG", long_sc.direction == "LONG", long_sc.direction)
check("direction resolves SHORT", short_sc.direction == "SHORT", short_sc.direction)
check("book lean mirrored exactly",
      abs(long_sc.lean_parts["BOOK"] + short_sc.lean_parts["BOOK"]) < 1e-9,
      f"{long_sc.lean_parts['BOOK']} vs {short_sc.lean_parts['BOOK']}")

print("\n=== OI/funding component symmetry ===")
for pc, oc, fr, label in [(1.0, 4.0, 0.0001, "up/oi-up/long-funding"),
                          (-1.0, -4.0, -0.0001, "down/oi-down/short-funding")]:
    pass

m1, l1, d1 = scorer.oi_funding_component(1.0, 4.0, 0.0001, 0.0018)
m2, l2, d2 = scorer.oi_funding_component(-1.0, -4.0, -0.0001, 0.0018)
check("OI+price up -> bullish lean", l1 > 0, f"lean={l1}")
check("OI+price down -> bearish lean", l2 < 0, f"lean={l2}")
check("magnitude symmetric", abs(m1 - m2) < 1e-9, f"{m1} vs {m2}")
check("lean symmetric", abs(l1 + l2) < 1e-9, f"{l1} vs {l2}")

m3, l3, _ = scorer.oi_funding_component(1.0, 4.0, 0.0015, 0.0018)   # crowded longs
check("crowded long funding reduces bullish lean", l3 < l1, f"crowded={l3} vs normal={l1}")
m4, l4, _ = scorer.oi_funding_component(-1.0, -4.0, -0.0015, 0.0018)  # crowded shorts
check("crowded short funding reduces bearish lean (mirror)", l4 > l2, f"crowded={l4} vs normal={l2}")

m5, l5, _ = scorer.oi_funding_component(1.0, None, 0.0001, 0.0018)
check("missing OI degrades gracefully (funding-only, near-zero lean)",
      l5 == 0.0 and m5 < 0.05, f"{m5},{l5}")
m5b, l5b, _ = scorer.oi_funding_component(1.0, None, 0.0015, 0.0018)
m5c, l5c, _ = scorer.oi_funding_component(-1.0, None, -0.0015, 0.0018)
check("funding-only crowded longs lean bearish", l5b < 0, f"{l5b}")
check("funding-only symmetric", abs(m5b - m5c) < 1e-9 and abs(l5b + l5c) < 1e-9,
      f"{m5b},{l5b} vs {m5c},{l5c}")

print("\n=== early-ness lowers quality for a mature move ===")
young = mk([100.0] * 130 + [100 * (1.004 ** i) for i in range(20)],
           vol=[1000.0] * 146 + [9000.0] * 4)
mature = mk([100.0] * 60 + [100 * (1.004 ** i) for i in range(90)],
            vol=[1000.0] * 146 + [9000.0] * 4)

sy = scorer.score_coin("Y", young, bid_heavy, ask_heavy, quote_vol_24h=5e6,
                       spread_pct=0.2, change_1h_pct=1.0, tier=1)
sm = scorer.score_coin("M", mature, bid_heavy, ask_heavy, quote_vol_24h=5e6,
                       spread_pct=0.2, change_1h_pct=1.0, tier=1)
check("young move scores >= mature move", sy.score >= sm.score,
      f"young={sy.score} mature={sm.score}")
check("young early-ness higher", sy.earlyness > sm.earlyness,
      f"young={sy.earlyness} mature={sm.earlyness}")

print("\n=== vetoes fire with reasons ===")
flat = mk([100.0] * 150, vol=[1000.0] * 150)
v = scorer.score_coin("V", flat, bid_heavy, ask_heavy,
                      quote_vol_24h=100.0, spread_pct=7.0, change_1h_pct=25.0, tier=1)
codes = {x.code for x in v.vetoes}
check("low volume veto", "low_volume" in codes, str(codes))
check("wide spread veto", "wide_spread" in codes, str(codes))
check("late move veto", "late_move" in codes, str(codes))
check("vetoes carry human reasons", all(x.reason for x in v.vetoes))
check("vetoed coin is not actionable", not v.actionable)

short_hist = mk([100.0] * 10)
v2 = scorer.score_coin("T", short_hist, bid_heavy, ask_heavy, quote_vol_24h=5e6,
                       spread_pct=0.2, change_1h_pct=1.0, tier=1)
check("thin history veto", "thin_history" in {x.code for x in v2.vetoes})

print("\n=== NEUTRAL when lean is weak ===")
noise = mk([100 + random.uniform(-1, 1) for _ in range(150)],
           vol=[1000.0] * 150)
balanced_bids = [(100.0, 25.0), (99.0, 25.0)]
balanced_asks = [(101.0, 25.0), (102.0, 25.0)]
n = scorer.score_coin("N", noise, balanced_bids, balanced_asks,
                      quote_vol_24h=5e6, spread_pct=0.2, change_1h_pct=0.1,
                      oi_change_pct=0.0, tier=1)
check("balanced evidence -> NEUTRAL or low |lean|",
      n.direction == "NEUTRAL" or abs(n.lean) <= 0.15, f"dir={n.direction} lean={n.lean}")

print("\n=== no return-magnitude leakage in the scorer ===")
import inspect
src = inspect.getsource(scorer)
leaks = [t for t in ["target_return", "expected_return", "take_profit_pct",
                     "500", "multiple_target"] if t in src]
check("no target-return constants", not leaks, f"found {leaks}")

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)