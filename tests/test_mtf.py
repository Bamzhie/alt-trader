"""MTF scoring tests: 5m/1H/4H alignment bonus (+8, capped at 100) and the
counter-trend label (notes only, never a score change). Offline: synthetic
bars plus a patched venue feed for the scan wiring.

Alignment contract (spec SS4):
  aligned  = sign(lean_5m)==sign(lean_1H)==sign(lean_4H) and all |lean|>0.15
  bonus    = min(100, score + 8), applied after base*earlyness, before round
  counter  = sign(lean_5m) != sign(lean_4H) and both |lean|>0.15 -> label only
The 5m lean is the SAME volume_price lean the 1H/4H leans are built with, so
the three are compared like for like (spec SS4: same formula per TF).
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import planner as pl
from proto import scorer
from proto import scan as scanmod

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
    """Exact geometric mirror (1/x), as in tests/test_scorer.py."""
    out = []
    for b in bars:
        out.append({"ts": b["ts"], "o": 1.0 / b["o"], "h": 1.0 / b["l"],
                    "l": 1.0 / b["h"], "c": 1.0 / b["c"],
                    "vol": b["vol"], "amount": b["amount"]})
    for b in out:
        b["h"], b["l"] = max(b["h"], b["l"]), min(b["h"], b["l"])
        b["amount"] = b["vol"] * b["c"]
    return out


BIDS = [(100.0, 50.0), (99.0, 30.0), (98.0, 20.0)]
ASKS = [(101.0, 50.0), (102.0, 30.0), (103.0, 20.0)]
BAL_BIDS = [(100.0, 25.0), (99.0, 25.0)]
BAL_ASKS = [(101.0, 25.0), (102.0, 25.0)]

KW = dict(quote_vol_24h=5_000_000, spread_pct=0.2, change_1h_pct=1.0,
          price=100.0, change_24h_pct=3.0, funding_rate=0.0001,
          funding_cap=0.0018, oi_change_pct=4.0, min_notional=0.01, tier=1)

random.seed(7)
up_cs = [100 * (1.003 ** i) * (1 + 0.01 * random.uniform(-1, 1)) for i in range(150)]
vol = [1000.0] * 146 + [9000.0] * 4
bars_up = mk(up_cs, vol=vol)
bars_dn = mirror(bars_up)

print("=== aligned 5m/1H/4H leans earn +8, capped at 100 ===")
_, lean5_up, _ = scorer.volume_price_component(bars_up)
_, lean5_dn, _ = scorer.volume_price_component(bars_dn)
check("fixture: 5m lean up is beyond +0.15", lean5_up > 0.15, f"lean5={lean5_up}")
check("fixture: 5m lean down is beyond -0.15", lean5_dn < -0.15, f"lean5={lean5_dn}")

base_up = scorer.score_coin("MTF", bars_up, BIDS, ASKS, **KW)
al_up = scorer.score_coin("MTF", bars_up, BIDS, ASKS,
                          lean_1H=0.5, lean_4H=0.5, **KW)
check("aligned long -> min(100, base+8)",
      al_up.score == min(100.0, base_up.score + 8.0),
      f"base={base_up.score} aligned={al_up.score}")
check("bonus is the full +8 while below the cap",
      base_up.score + 8.0 <= 100.0 and al_up.score == base_up.score + 8.0,
      f"base={base_up.score} aligned={al_up.score}")

base_dn = scorer.score_coin("MTF", bars_dn, ASKS, BIDS, **KW)
al_dn = scorer.score_coin("MTF", bars_dn, ASKS, BIDS,
                          lean_1H=-0.5, lean_4H=-0.5, **KW)
check("aligned short -> min(100, base+8) (mirror holds)",
      al_dn.score == min(100.0, base_dn.score + 8.0),
      f"base={base_dn.score} aligned={al_dn.score}")
check("alignment is symmetric: mirrored leans get the same +8",
      al_up.score - base_up.score == al_dn.score - base_dn.score,
      f"up={al_up.score - base_up.score} dn={al_dn.score - base_dn.score}")

print("\n=== leans barely below the 0.15 threshold -> no bonus, no crash ===")
# 5m is strong but the given 1H/4H leans sit just under the threshold.
sub = scorer.score_coin("MTF", bars_up, BIDS, ASKS,
                        lean_1H=0.14, lean_4H=0.14, **KW)
check("1H/4H at 0.14 -> no bonus", sub.score == base_up.score,
      f"sub={sub.score} base={base_up.score}")

# All three barely under: flat bars put the 5m lean at exactly 0.
flat = mk([100.0] * 150, vol=[1000.0] * 150)
_, lean5_flat, _ = scorer.volume_price_component(flat)
base_flat = scorer.score_coin("FLAT", flat, BAL_BIDS, BAL_ASKS, **KW)
all_sub = scorer.score_coin("FLAT", flat, BAL_BIDS, BAL_ASKS,
                            lean_1H=0.14, lean_4H=0.14, **KW)
check("fixture: flat 5m lean is below threshold", abs(lean5_flat) <= 0.15,
      f"lean5={lean5_flat}")
check("all three TF leans below threshold -> no bonus, no crash",
      all_sub.score == base_flat.score and all_sub.score >= 0,
      f"all_sub={all_sub.score} base={base_flat.score}")
check("no counter-trend note on sub-threshold opposition",
      not any(n.startswith("counter-trend:") for n in all_sub.notes),
      str(all_sub.notes))

print("\n=== 5m vs 4H opposition -> counter-trend note, NO score change ===")
ct = scorer.score_coin("CT", bars_up, BIDS, ASKS,
                       lean_1H=0.5, lean_4H=-0.5, **KW)
ct_notes = [n for n in ct.notes if n.startswith("counter-trend:")]
check("counter-trend note appended exactly once",
      ct_notes == ["counter-trend: 5m LONG vs 4H SHORT — elevated risk"],
      str(ct_notes))
check("counter-trend does NOT change the score", ct.score == base_up.score,
      f"ct={ct.score} base={base_up.score}")

ct_dn = scorer.score_coin("CT", bars_dn, ASKS, BIDS,
                          lean_1H=-0.5, lean_4H=0.5, **KW)
ct_dn_notes = [n for n in ct_dn.notes if n.startswith("counter-trend:")]
check("mirrored counter-trend note (symmetry preserved)",
      ct_dn_notes == ["counter-trend: 5m SHORT vs 4H LONG — elevated risk"],
      str(ct_dn_notes))
check("mirrored counter-trend does NOT change the score",
      ct_dn.score == base_dn.score, f"ct={ct_dn.score} base={base_dn.score}")

ct1 = scorer.score_coin("CT1", bars_up, BIDS, ASKS,
                        lean_1H=-0.5, lean_4H=0.5, **KW)
check("1H-only opposition is neither labeled nor bonused",
      not any(n.startswith("counter-trend:") for n in ct1.notes)
      and ct1.score == base_up.score,
      f"score={ct1.score} base={base_up.score}")

check("default call (no MTF leans) unchanged: no bonus, no note",
      base_up.score == scorer.score_coin("MTF", bars_up, BIDS, ASKS,
                                         **KW).score
      and not any(n.startswith("counter-trend:") for n in base_up.notes),
      str(base_up.notes))

print("\n=== planner copies the counter-trend note into plan warnings ===")
check("fixture: counter-trend card resolves LONG (plannable)",
      ct.direction == "LONG", ct.direction)
plan = pl.build_plan(ct, stake=0.10, swing_ref=98.0)
check("plan is valid", plan.valid, str(plan.warnings))
check("plan warnings carry the counter-trend note verbatim",
      plan.warnings == ["counter-trend: 5m LONG vs 4H SHORT — elevated risk"],
      str(plan.warnings))

print("\n=== analyse_one fetches 5m/1H/4H and passes the leans through ===")
TK = {"lastPrice": 100.0, "amount24": 5_000_000.0, "riseFallRate": 0.03,
      "fundingRate": 0.0001, "maxFundingRate": 0.0018}
DETAIL = {"minVol": 1, "contractSize": 0.05}


def run_analyse(klines_fn):
    """analyse_one against a patched venue feed. Returns the Scorecard."""
    orig = (scanmod.mexc.klines, scanmod.mexc.depth)
    scanmod.mexc.klines = klines_fn
    scanmod.mexc.depth = lambda sym, limit=20: (BIDS, ASKS)
    try:
        return scanmod.analyse_one("TEST_USDT", "TEST", DETAIL, TK, 0.10)
    finally:
        scanmod.mexc.klines, scanmod.mexc.depth = orig


def klines_all_bullish(sym, interval="5m", limit=200):
    if interval in ("5m", "1H", "4H"):
        return bars_up          # every TF bullish -> aligned bonus
    raise AssertionError(f"unexpected interval {interval!r}")


def klines_4h_bearish(sym, interval="5m", limit=200):
    if interval in ("5m", "1H"):
        return bars_up
    if interval == "4H":
        return bars_dn          # 4H opposes 5m -> counter-trend label
    raise AssertionError(f"unexpected interval {interval!r}")


def klines_hf_fails(sym, interval="5m", limit=200):
    if interval == "5m":
        return bars_up
    raise RuntimeError("venue down")   # higher TFs unavailable -> degrade


sc_none = run_analyse(klines_hf_fails)     # baseline: no HF leans at all
sc_all = run_analyse(klines_all_bullish)
sc_dn = run_analyse(klines_4h_bearish)

check("degraded higher-TF fetch still scores the coin", sc_none is not None,
      str(sc_none))
if sc_none is not None and sc_all is not None and sc_dn is not None:
    check("scan passes all three TFs through -> aligned +8",
          sc_all.score == min(100.0, sc_none.score + 8.0),
          f"aligned={sc_all.score} degraded={sc_none.score}")
    check("aligned card carries no counter-trend note",
          not any(n.startswith("counter-trend:") for n in sc_all.notes),
          str(sc_all.notes))
    check("4H opposition labeled, score unchanged",
          sc_dn.score == sc_none.score
          and any(n.startswith("counter-trend: 5m LONG vs 4H SHORT")
                  for n in sc_dn.notes),
          f"score={sc_dn.score} vs {sc_none.score}; {sc_dn.notes}")

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
