"""Watcher geometry + costs + funding + breakeven trail (Task 3).

Self-checking script: `python3 tests/test_watcher_geometry.py`.
Covers fund_cost sign (long pays +sum, short earns -sum), c_total
composition (long funding raises, short rebates floored at 0 with the
signed leg kept), fund_stress (3x now x n), geometry (b floor binds at
tiny sigma), levels LONG/SHORT mirror with no price rounding, and the
breakeven trail (pre-trigger touches use the original stop, move defers
to the next bar open, post-move same-bar stop+tgt resolves stop-first
at the moved level).
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import watcher

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def close(a, b, tol=1e-12):
    return abs(a - b) <= tol


print("=== fund_cost sign ===")
check("long pays +sum",
      close(watcher.fund_cost("LONG", [0.0001, 0.0002]), 0.0003),
      str(watcher.fund_cost("LONG", [0.0001, 0.0002])))
check("short earns -sum (signed leg kept)",
      close(watcher.fund_cost("SHORT", [0.0001, 0.0002]), -0.0003),
      str(watcher.fund_cost("SHORT", [0.0001, 0.0002])))
check("empty rates -> 0.0",
      watcher.fund_cost("LONG", []) == 0.0 and watcher.fund_cost("SHORT", []) == 0.0)
check("negative rate flips: long leg negative, short leg positive",
      close(watcher.fund_cost("LONG", [-0.0001]), -0.0001)
      and close(watcher.fund_cost("SHORT", [-0.0001]), 0.0001))
try:
    watcher.fund_cost("SIDEWAYS", [0.0001])
    check("bad direction raises", False, "no error")
except ValueError:
    check("bad direction raises", True)

print("\n=== c_total composition (base + fund leg, floor 0) ===")
TAKER, SLIP, SPREAD = 0.0005, 0.0005, 0.0002
base = 2 * TAKER + SPREAD + SLIP
rates = [0.0001, 0.0001, 0.0001]
c_long = base + watcher.fund_cost("LONG", rates)
c_short_raw = base + watcher.fund_cost("SHORT", rates)
c_short = max(0.0, c_short_raw)
check("long positive funding raises c_total", c_long > base, f"{c_long} vs {base}")
check("short same rates rebate (below base)", c_short_raw < base, str(c_short_raw))
check("short c_total floored at 0 on huge rebate",
      max(0.0, base + watcher.fund_cost("SHORT", [0.01, 0.01, 0.01])) == 0.0)
check("signed leg kept for net column (raw short leg negative)",
      base + watcher.fund_cost("SHORT", [0.01, 0.01, 0.01]) < 0.0)

print("\n=== fund_stress ===")
check("3 * now * n", close(watcher.fund_stress(0.0001, 3), 0.0009),
      str(watcher.fund_stress(0.0001, 3)))
check("zero rate -> 0.0", watcher.fund_stress(0.0, 5) == 0.0)
check("zero settlements -> 0.0", watcher.fund_stress(0.0001, 0) == 0.0)

print("\n=== geometry ===")
sigma, N, c, ks, kt = 0.002, 48, 0.0017, 2.0, 2.0
b, a = watcher.geometry(sigma, N, c, ks, kt)
check("a_tgt = k_t * sigma * sqrt(N)",
      close(a, kt * sigma * math.sqrt(N)), str(a))
check("b_stop = k_s * sigma * sqrt(N) when above floor",
      close(b, ks * sigma * math.sqrt(N)), str(b))
b2, a2 = watcher.geometry(1e-6, 48, c, ks, kt)
check("floor binds at tiny sigma: b_stop == 10 * c_total",
      b2 == 10 * c, str(b2))
check("a_tgt has no floor (pure k_t term)",
      close(a2, kt * 1e-6 * math.sqrt(48)), str(a2))
check("fractions throughout (b, a << 1)", b < 1.0 and a < 1.0 and b2 < 1.0)

print("\n=== levels mirror, no rounding ===")
stopL, tgtL = watcher.levels(100.0, "LONG", 0.01, 0.02)
stopS, tgtS = watcher.levels(100.0, "SHORT", 0.01, 0.02)
check("long stop below entry", close(stopL, 99.0), str(stopL))
check("long target above entry", close(tgtL, 102.0), str(tgtL))
check("short stop above entry", close(stopS, 101.0), str(stopS))
check("short target below entry", close(tgtS, 98.0), str(tgtS))
check("mirror: entry-stop symmetric (E*b both sides)",
      close(stopS - 100.0, 100.0 - stopL), f"{stopS} vs {stopL}")
check("mirror: target symmetric (E*a both sides)",
      close(tgtL - 100.0, 100.0 - tgtS), f"{tgtL} vs {tgtS}")
sl, tl = watcher.levels(100.0, "LONG", 1 / 3, 1 / 7)
check("no price rounding: fractional exact",
      sl == 100.0 * (1 - 1 / 3) and tl == 100.0 * (1 + 1 / 7), f"{sl} {tl}")
try:
    watcher.levels(100.0, "SIDEWAYS", 0.01, 0.02)
    check("bad direction raises", False, "no error")
except ValueError:
    check("bad direction raises", True)

print("\n=== breakeven trail (LONG) ===")


def long_state():
    return {"entry": 100.0, "direction": "LONG", "b_stop": 0.01,
            "stop_px": 99.0, "tgt_px": 102.0,
            "moved": False, "pending": False}


s0 = long_state()
s_pre = watcher.apply_breakeven(s0, 100.5)
check("pre-trigger keeps original stop, no pending",
      s_pre["stop_px"] == 99.0 and not s_pre.get("pending")
      and not s_pre.get("moved"), str(s_pre))
check("input state not mutated",
      s0 == long_state(), str(s0))
check("pre-trigger touch uses original stop (low 99.5 no touch)",
      not (99.5 <= s_pre["stop_px"]))
s_trig = watcher.apply_breakeven(s0, 101.0)
check("trigger at close == entry + b_stop arms pending, stop still original",
      s_trig.get("pending") is True and s_trig["stop_px"] == 99.0
      and not s_trig.get("moved"), str(s_trig))
s_moved = watcher.apply_breakeven(s_trig, 100.8)
check("next bar open moves stop to entry",
      s_moved["stop_px"] == 100.0 and s_moved.get("moved") is True
      and not s_moved.get("pending"), str(s_moved))
s_after = watcher.apply_breakeven(s_moved, 90.0)
check("post-move state immutable (crash after move keeps moved stop)",
      s_after["stop_px"] == 100.0 and s_after.get("moved") is True, str(s_after))
# Same bar touches moved stop (entry=100) and target (102): stop-first.
touch_stop = 99.5 <= s_moved["stop_px"]
touch_tgt = 103.0 >= s_moved["tgt_px"]
outcome = "stop" if touch_stop else ("target" if touch_tgt else None)
check("post-move same-bar stop+tgt -> stop at moved level",
      touch_stop and touch_tgt and outcome == "stop",
      f"stop_touch={touch_stop} tgt_touch={touch_tgt} outcome={outcome}")

print("\n=== breakeven trail (SHORT mirror) ===")


def short_state():
    return {"entry": 100.0, "direction": "SHORT", "b_stop": 0.01,
            "stop_px": 101.0, "tgt_px": 98.0,
            "moved": False, "pending": False}


t0 = short_state()
t_pre = watcher.apply_breakeven(t0, 99.5)
check("short pre-trigger keeps original stop",
      t_pre["stop_px"] == 101.0 and not t_pre.get("pending"), str(t_pre))
t_trig = watcher.apply_breakeven(t0, 99.0)
check("short trigger at close == entry - b_stop arms pending",
      t_trig.get("pending") is True and t_trig["stop_px"] == 101.0, str(t_trig))
t_moved = watcher.apply_breakeven(t_trig, 99.2)
check("short next bar open moves stop to entry",
      t_moved["stop_px"] == 100.0 and t_moved.get("moved") is True, str(t_moved))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
