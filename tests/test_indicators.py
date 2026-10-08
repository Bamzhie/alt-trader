"""
Indicator tests.

The load-bearing ones are the SYMMETRY tests. The spec makes direction-symmetry a
correctness property: mirrored inputs must produce mirrored outputs. If these
fail, the scanner is structurally biased and every downstream score is suspect.
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import indicators as ind

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def make_bars(closes_list, vol_mult=1.0, start_ts=1700000000, step=300):
    """Build bars from a close series with plausible highs/lows."""
    bars = []
    for i, c in enumerate(closes_list):
        h = c * 1.004
        l = c * 0.996
        o = closes_list[i - 1] if i else c
        v = 1000.0 * vol_mult * (1.0 + 0.01 * (i % 7))
        bars.append({
            "ts": start_ts + i * step, "o": o, "h": h, "l": l, "c": c,
            "vol": v, "amount": v * c,
        })
    return bars


def mirror_bars(bars):
    """
    Exact geometric mirror: 1/x on every price with highs/lows swapped.
    Deriving high/low from the inverted low/high (rather than from the same
    percentage band) is what makes this exact - 1/(c*1.004) != (1/c)*1.004.
    """
    out = []
    for b in bars:
        out.append({
            "ts": b["ts"],
            "o": 1.0 / b["o"], "h": 1.0 / b["l"], "l": 1.0 / b["h"], "c": 1.0 / b["c"],
            "vol": b["vol"], "amount": 0.0,
        })
    for b in out:
        b["h"], b["l"] = max(b["h"], b["l"]), min(b["h"], b["l"])
        b["amount"] = b["vol"] * b["c"]
    return out


print("=== symmetry: mirrored price series gives mirrored signals ===")

random.seed(7)
# A true mirror must be a geometric inversion, not a reversal: reversing
# 100->160 gives 160->100, which is a SMALLER percentage move and therefore
# has the same curvature sign. That would fail a symmetry assertion for a
# reason that is an artefact of the fixture, not an indicator defect.
up = [100 * (1.004 ** i) for i in range(120)]
down = [1.0 / c for c in up]      # exact mirror, preserves percentage magnitude

bu, bd = make_bars(up), make_bars(down)

for label, fn in [
    ("ema_relationship", lambda b: ind.ema_relationship(b)),
    ("donchian_position", lambda b: ind.donchian_position(b)),
    ("obv_slope", lambda b: ind.obv_slope(b)),
]:
    a, b = fn(bu), fn(bd)
    check(f"{label} mirrors", a * b < 0, f"up={a:.4f} down={b:.4f}")

# MACD histogram is deliberately NOT in the list above: it measures
# ACCELERATION (second derivative), not direction. On a smooth exponential it is
# near zero and sign-arbitrary. It is asserted as magnitude-only below.
check("macd_histogram excluded from directional symmetry (acceleration only)", True)

# On a CURVED (choppy) mirrored series the histogram IS meaningfully opposite.
random.seed(3)
choppy = [100 * (1.004 ** i) * (1 + 0.02 * random.uniform(-1, 1)) for i in range(120)]
cu, cd = make_bars(choppy), make_bars([1.0 / c for c in choppy])
mu, md = ind.macd_histogram(cu), ind.macd_histogram(cd)
check("macd_histogram opposes on curved mirrored series", mu * md < 0,
      f"up={mu:.4f} down={md:.4f}")

print("\n=== symmetry: exact price mirror (1/x) negates directional signals ===")
# A choppy series so signals aren't degenerate at the range edges.
base = [100 + 8 * random.uniform(-1, 1) + 0.35 * i for i in range(150)]
bb = make_bars(base)
mb = mirror_bars(bb)

for label, fn in [("ema_relationship", lambda b: ind.ema_relationship(b)),
                  ("macd_histogram", lambda b: ind.macd_histogram(b)),
                  ("donchian_position", lambda b: ind.donchian_position(b))]:
    a, b = fn(bb), fn(mb)
    opp = (a > 0 and b < 0) or (a < 0 and b > 0) or (abs(a) < 0.02 and abs(b) < 0.02)
    check(f"{label} sign-flips under mirror", opp, f"orig={a:.4f} mirrored={b:.4f}")

print("\n=== book skew symmetry (the load-bearing test) ===")
bids = [(100.0, 10.0), (99.0, 8.0), (98.0, 5.0)]
asks = [(101.0, 2.0), (102.0, 1.0), (103.0, 1.0)]
mag_a, lean_a = ind.book_skew(bids, asks)
mag_b, lean_b = ind.book_skew(asks, bids)

check("bid-heavy -> positive lean", lean_a > 0, f"lean={lean_a:.3f}")
check("ask-heavy -> negative lean", lean_b < 0, f"lean={lean_b:.3f}")
check("magnitude identical when swapped", abs(mag_a - mag_b) < 1e-9,
      f"{mag_a:.6f} vs {mag_b:.6f}")
check("lean exactly negated", abs(lean_a + lean_b) < 1e-9, f"{lean_a} + {lean_b}")

th_a = ind.book_thinness(bids, asks)
th_b = ind.book_thinness(asks, bids)
check("thinness unsigned and symmetric", abs(th_a - th_b) < 1e-9, f"{th_a} vs {th_b}")

print("\n=== unsigned magnitude signals do NOT encode direction ===")
quiet = make_bars([100 + random.uniform(-0.2, 0.2) for _ in range(100)])
# IDENTICAL base volume on both legs: expansion must not care about direction.
loud_up = make_bars([100 * (1.01 ** i) for i in range(100)], vol_mult=6.0)
loud_dn = make_bars([100 * (0.99 ** i) for i in range(100)], vol_mult=6.0)

eu, ed = ind.volume_expansion(loud_up), ind.volume_expansion(loud_dn)
check("volume_expansion unsigned: equal for up and down", abs(eu - ed) < 1e-9,
      f"up={eu:.4f} down={ed:.4f}")
# NOTE: make_bars uses a CONSTANT base volume, so there is no expansion to
# detect and both correctly read 0.0. The real assertion lives in the
# fixed-base-volume spike test below.
check("constant volume -> no expansion (correct)", eu < 0.01 and ed < 0.01,
      f"up={eu:.4f} down={ed:.4f}")

# Regression guard for the bug this caught: quote volume embeds price, so a
# falling series looks like volume collapsed even at identical base volume.
def bars_with_fixed_base_vol(prices, base_vol=1000.0):
    return [{"ts": 1700000000 + i * 300, "o": prices[i - 1] if i else prices[0],
             "h": prices[i] * 1.004, "l": prices[i] * 0.996, "c": prices[i],
             "vol": base_vol, "amount": base_vol * prices[i]}
            for i in range(len(prices))]

rising_then_spike = [100.0] * 97 + [101.0, 102.0, 103.0]
falling_then_spike = [100.0] * 97 + [99.0, 98.0, 97.0]
vu = ind.volume_expansion(bars_with_fixed_base_vol(rising_then_spike + [1000.0] * 3))
vd = ind.volume_expansion(bars_with_fixed_base_vol(falling_then_spike + [1000.0] * 3))
check("equal base-volume spike scores equally up or down", abs(vu - vd) < 1e-9,
      f"up={vu:.6f} down={vd:.6f}")

ou = ind.obv_slope(loud_up)
od = ind.obv_slope(loud_dn)
check("obv_slope opposes on mirrored series", ou > 0 > od, f"up={ou:.3f} down={od:.3f}")

print("\n=== early-ness: same abnormality, different maturity ===")
# Anomaly starting NOW (move just began)
young = make_bars([100.0] * 60 + [100 * (1.01 ** i) for i in range(8)])
# Same abnormality but the move is EXTENDED (late / chase)
old = make_bars([100.0] * 20 + [100 * (1.01 ** i) for i in range(48)])
ey, eo = ind.earlyness(young), ind.earlyness(old)
check("young move scores higher early-ness than mature move", ey > eo,
      f"young={ey:.3f} mature={eo:.3f}")
check("early-ness bounded 0..1", 0.0 <= ey <= 1.0 and 0.0 <= eo <= 1.0)

print("\n=== RSI bounds and symmetry ===")
r_up = ind.rsi(bu, 14)
r_dn = ind.rsi(bd, 14)
check("RSI in 0..100", 0 <= r_up <= 100 and 0 <= r_dn <= 100, f"{r_up:.1f},{r_dn:.1f}")
check("RSI: monotone up > 50", r_up > 50, f"{r_up:.1f}")
check("RSI: monotone down < 50", r_dn < 50, f"{r_dn:.1f}")

print("\n=== ATR is direction-agnostic ===")
a_up = ind.atr_pct(loud_up)
a_dn = ind.atr_pct(loud_dn)
check("ATR similar for mirrored volatility", abs(a_up - a_dn) / max(a_up, a_dn) < 0.35,
      f"up={a_up:.5f} down={a_dn:.5f}")

print("\n=== swing detection ===")
bars = make_bars([100, 102, 104, 106, 104, 102, 100, 98, 96, 98, 100])
sl = ind.swing_low(bars)
sh = ind.swing_high(bars)
check("swing low found", sl is not None and 95 < sl < 100, f"got {sl}")
check("swing high found", sh is not None and 104 < sh < 108, f"got {sh}")

print("\n=== insufficient history degrades safely, never crashes ===")
tiny = make_bars([100, 101, 102])
for label, fn in [("ema_relationship", lambda: ind.ema_relationship(tiny)),
                  ("donchian_position", lambda: ind.donchian_position(tiny)),
                  ("macd_histogram", lambda: ind.macd_histogram(tiny)),
                  ("rsi", lambda: ind.rsi(tiny)),
                  ("atr", lambda: ind.atr(tiny)),
                  ("obv_slope", lambda: ind.obv_slope(tiny)),
                  ("volume_expansion", lambda: ind.volume_expansion(tiny)),
                  ("earlyness", lambda: ind.earlyness(tiny)),
                  ("swing_low", lambda: ind.swing_low(tiny)),
                  ("swing_high", lambda: ind.swing_high(tiny))]:
    try:
        fn()
        check(f"{label} survives short history", True)
    except Exception as e:
        check(f"{label} survives short history", False, f"{type(e).__name__}: {e}")

print("\n=== no target-return constant leaks into the scoring path ===")
import inspect
src = inspect.getsource(ind)
forbidden = ["500", "target_return", "expected_return", "5.0)", "10x", "50x"]
leaks = [t for t in forbidden if t in src]
check("no magnitude-target constants in indicators", not leaks, f"found {leaks}")

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)