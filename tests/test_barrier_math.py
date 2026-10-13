"""Barrier math port tests (watcher-setup Task 1).

Ports the verified formulas from ~/Downloads/barrier_math.py into
proto/barrier_math.py (stdlib only). Offline.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import barrier_math as bm

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def Phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


print("=== p_tp_first ===")
check("null p_tp_first(5,1,0,1) == 1/6",
      abs(bm.p_tp_first(5, 1, 0, 1) - 1 / 6) < 1e-9,
      str(bm.p_tp_first(5, 1, 0, 1)))
check("null symmetric p_tp_first(2,2,0,1) == 0.5",
      abs(bm.p_tp_first(2, 2, 0, 1) - 0.5) < 1e-9)
k = 2 * 0.2 / 1.0
want_drift = (1 - math.exp(-k * 1)) / (1 - math.exp(-k * (2 + 1)))
check("drift p_tp_first(2,1,0.2,1) matches hand value",
      abs(bm.p_tp_first(2, 1, 0.2, 1) - want_drift) < 1e-9,
      str(bm.p_tp_first(2, 1, 0.2, 1)))

print("\n=== noise_touch_prob ===")
want_noise = 2 * Phi(-2 / math.sqrt(5))
check("noise_touch_prob(2,5) == 2Phi(-2/sqrt(5))",
      abs(bm.noise_touch_prob(2, 5) - want_noise) < 1e-9,
      str(bm.noise_touch_prob(2, 5)))

print("\n=== breakeven_p ===")
check("breakeven_p(4.5,0.0) == 1/5.5",
      abs(bm.breakeven_p(4.5, 0.0) - 1 / 5.5) < 1e-12,
      str(bm.breakeven_p(4.5, 0.0)))
check("breakeven_p(2,0.2) == 1.2/3",
      abs(bm.breakeven_p(2, 0.2) - 1.2 / 3) < 1e-12)

print("\n=== friction_R ===")
check("friction_R(0.10,0.5) == 0.20",
      abs(bm.friction_R(0.10, 0.5) - 0.20) < 1e-12,
      str(bm.friction_R(0.10, 0.5)))

print("\n=== cluster_z ===")
p, p0, n = 350 / 1348, 1 / 5.5, 1348
z_full = bm.cluster_z(p, p0, n, 1.0)
z_small = bm.cluster_z(p, p0, n, 0.1)
se = math.sqrt(p0 * (1 - p0) / (n * 1.0))
check("cluster_z matches hand value at frac=1.0",
      abs(z_full - (p - p0) / se) < 1e-9, str(z_full))
check("cluster_z shrinks with frac (z(1.0) > z(0.1))", z_full > z_small,
      f"{z_full} vs {z_small}")

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
