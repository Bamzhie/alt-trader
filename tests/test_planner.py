"""Planner tests: direction symmetry, leverage safety, size limits, costs."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import planner as pl
from proto.scorer import Scorecard

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def sc(direction, price, min_notional=0.01, funding=0.0, coin="TEST"):
    return Scorecard(coin=coin, direction=direction, price=price,
                     min_notional=min_notional, funding_rate=funding)


print("=== plan_levels is an exact mirror ===")
price = 100.0
long_lv = pl.plan_levels("LONG", price, stop_ref := 94.0)   # stop below for LONG
short_lv = pl.plan_levels("SHORT", price, stop_ref2 := 106.0)  # stop above for SHORT

check("LONG stop below entry", long_lv["stop"] < long_lv["entry_low"], str(long_lv))
check("LONG tp1 > entry", long_lv["tp1"] > long_lv["entry_high"], str(long_lv))
check("LONG tp2 > tp1", long_lv["tp2"] > long_lv["tp1"])
check("SHORT stop above entry", short_lv["stop"] > short_lv["entry_high"], str(short_lv))
check("SHORT tp1 < entry", short_lv["tp1"] < short_lv["entry_low"], str(short_lv))
check("SHORT tp2 < tp1", short_lv["tp2"] < short_lv["tp1"])

# exact mirror: reflect a LONG plan about price -> SHORT plan.
# R-multiples are computed from PRICE, so compare against price, not entry_low
# (which is a symmetric band around price and identical in both directions).
lp = pl.plan_levels("LONG", 100.0, 94.0)
sp = pl.plan_levels("SHORT", 100.0, 106.0)
check("mirrored stop distances equal",
      abs(abs(100.0 - lp["stop"]) - abs(sp["stop"] - 100.0)) < 1e-9,
      f"L={abs(100.0-lp['stop']):.6f} S={abs(sp['stop']-100.0):.6f}")
check("mirrored tp1 distances equal",
      abs(abs(lp["tp1"] - 100.0) - abs(100.0 - sp["tp1"])) < 1e-9,
      f"L={abs(lp['tp1']-100.0):.6f} S={abs(100.0-sp['tp1']):.6f}")
check("mirrored tp2 distances equal",
      abs(abs(lp["tp2"] - 100.0) - abs(100.0 - sp["tp2"])) < 1e-9)

try:
    pl.plan_levels("LONG", 100.0, 106.0)  # stop ABOVE for LONG is invalid
    check("invalid stop side rejected for LONG", False, "no exception")
except ValueError:
    check("invalid stop side rejected for LONG", True)
try:
    pl.plan_levels("FLAT", 100.0, 94.0)
    check("non-directional plan rejected", False, "no exception")
except ValueError:
    check("non-directional plan rejected", True)

print("\n=== leverage safety: computed, never defaulted, never exceeds safe ===")
# The governing physics: at leverage L with stop distance d (fraction of
# notional), the stop loss is d*L as a fraction of MARGIN. It must stay below
# maintenance margin for the stop to fire before liquidation. Leverage is
# therefore bounded by maintenance_margin / d. Tighter stop -> higher leverage.
lev_tight = pl.compute_leverage(stake=100.0, risk_per_trade_pct=2.0, stop_distance_pct=0.05)
lev_wide = pl.compute_leverage(stake=100.0, risk_per_trade_pct=2.0, stop_distance_pct=10.0)
check("tight stop -> higher leverage than wide stop", lev_tight > lev_wide,
      f"{lev_tight} vs {lev_wide}")
check("leverage clamped to max", pl.compute_leverage(100.0, 2.0, 0.01) <= pl.MAX_LEVERAGE)
check("wide stop never exceeds max", lev_wide <= pl.MAX_LEVERAGE)

# THE invariant: liquidation happens when the stop loss equals 1.0 of margin.
# For any stop width, d*L must stay at or below 1/LIQ_BUFFER so the stop fires
# with headroom. This is the real safety property and is independent of any
# arbitrary expected-leverage value.
for sd in [0.05, 0.1, 0.5, 1.0, 2.0, 6.0, 20.0, 50.0]:
    lev = pl.compute_leverage(100.0, 2.0, sd)
    loss_frac_of_margin = (sd / 100.0) * lev
    check(f"stop {sd}% at {lev}x: loss {loss_frac_of_margin*100:.1f}% of margin "
          f"<= liq budget {100/pl.LIQ_BUFFER:.0f}%",
          loss_frac_of_margin <= (1.0 / pl.LIQ_BUFFER) + 1e-9,
          f"{loss_frac_of_margin*100:.2f}%")

# At the operator's $0.10 stake with a 6% stop, leverage must be low.
lev_micro = pl.compute_leverage(stake=0.10, risk_per_trade_pct=2.0, stop_distance_pct=6.0)
check("micro stake gives single-digit leverage", lev_micro <= 10, f"got {lev_micro}x")
check("micro stake flags below 20x floor", lev_micro < pl.MIN_LEVERAGE_FLOOR, f"{lev_micro}x")

print("\n=== full plan: LONG and SHORT mirror in every field ===")
p_long = pl.build_plan(sc("LONG", 100.0, min_notional=0.01), stake=50.0,
                       swing_ref=94.0)
p_short = pl.build_plan(sc("SHORT", 100.0, min_notional=0.01), stake=50.0,
                        swing_ref=106.0)
check("both plans valid", p_long.valid and p_short.valid)
check("long leverage == short leverage", p_long.leverage == p_short.leverage,
      f"{p_long.leverage} vs {p_short.leverage}")
check("long notional == short notional", abs(p_long.notional - p_short.notional) < 1e-9,
      f"{p_long.notional} vs {p_short.notional}")
check("long max_loss == short max_loss", abs(p_long.max_loss - p_short.max_loss) < 1e-9)
check("long R:R == short R:R", abs(p_long.reward_risk - p_short.reward_risk) < 1e-9)
check("break-even signed opposite",
      p_long.break_even_pct > 0 and p_short.break_even_pct > 0,
      f"long +{p_long.break_even_pct} short +{p_short.break_even_pct}")

print("\n=== size never exceeds stake; min notional enforced ===")
p = pl.build_plan(sc("LONG", 100.0, min_notional=0.01), stake=0.10, swing_ref=94.0)
check("margin <= stake", p.margin <= 0.10 + 1e-9, f"margin={p.margin}")
check("max_loss <= stake", p.max_loss <= 0.10 + 1e-9, f"max_loss={p.max_loss}")

p_blocked = pl.build_plan(sc("LONG", 100.0, min_notional=5.0), stake=0.10, swing_ref=94.0)
check("min notional > stake -> not tradeable", not p_blocked.tradeable)
check("min notional warning present", any("min notional" in w for w in p_blocked.warnings))

print("\n=== funding sign is direction-aware ===")
p_lf = pl.build_plan(sc("LONG", 100.0, funding=0.001), stake=50.0, swing_ref=94.0,
                     funding_rate=0.001)
p_sf = pl.build_plan(sc("SHORT", 100.0, funding=0.001), stake=50.0, swing_ref=106.0,
                     funding_rate=0.001)
check("positive funding: long pays (higher cost)",
      p_lf.costs > p_sf.costs, f"long={p_lf.costs} short={p_sf.costs}")
check("positive funding note says shorts receive",
      "RECEIVE" in p_sf.funding_note, p_sf.funding_note)

print("\n=== vetoes propagate into plan warnings ===")
sc_v = sc("LONG", 100.0)
from proto.scorer import Veto
sc_v.vetoes.append(Veto("low_volume", "24h quote volume $100 below floor"))
p_v = pl.build_plan(sc_v, stake=50.0, swing_ref=94.0)
check("veto surfaced in plan", any("vetoed" in w for w in p_v.warnings),
      str(p_v.warnings))

print("\n=== missing stop reference yields no plan, not a bad plan ===")
p_none = pl.build_plan(sc("LONG", 100.0), stake=50.0, swing_ref=None)
check("no stop ref -> invalid", not p_none.valid)
check("no stop ref -> warns", any("structural stop" in w for w in p_none.warnings))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)