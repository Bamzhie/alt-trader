"""Regression tests for latent pipeline defects found by the 2026-10-19 audit.

These do NOT change the 2026-10-19 evaluation result (the current archive has
no gaps and no invalid bars, and the BTC-max span only trims the sealed
holdout tail).  They lock in the corrected contracts:

  1. make_path must not consume bars from a DIFFERENT contiguous run.  The
     bar array is concatenated across timestamp gaps; a forward label path
     that crosses a gap silently uses post-gap bars.
  2. collect_events must use the UNION archive max (all coins), not BTC's
     max, for meta["t_max"] / n_days.  Otherwise events after BTC's last bar
     are assigned day > n_days and silently dropped from every window.
  3. collect_events must recreate the CUSUM state when an invalid bar
     restarts the FeatureStream.  Keeping the old CUSUM carries a stale
     `last` event index into the new (n-restarted) stream, suppressing
     residual-shock events for thousands of bars.

Self-checking script: python3 tests/test_quant_pipeline_regressions.py
"""
import gzip
import math
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.quant import barrier as B
from proto.quant import research as R
from proto.quant.config import DEFAULT, BAR_S

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def series(n, seed=1, sigma=0.002, amt=20000.0, t0=1_700_000_100):
    rng = random.Random(seed)
    px, out = 100.0, []
    for i in range(n):
        o = px
        px *= math.exp(rng.gauss(0, sigma))
        c = px
        h = max(o, c) * (1 + abs(rng.gauss(0, sigma / 3)))
        l = min(o, c) * (1 - abs(rng.gauss(0, sigma / 3)))
        out.append((t0 + i * BAR_S, o, h, l, c, amt / c, amt))
    return out


def write(dirp, coin, rows):
    with gzip.open(os.path.join(dirp, coin + ".csv.gz"), "wt", newline="") as f:
        f.write("ts,o,h,l,c,vol,amount\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


print("=== 1. make_path must not cross a contiguous-run boundary ===")
o = [100.0] * 700
h = [100.5] * 700
l = [99.5] * 700
c = [100.0] * 700
# i=149 is the last bar of run [0,150); a 288-bar path would start at 150.
p = B.make_path(o, h, l, c, 149, 288, run_end=150)
check("path at a run boundary is refused (None), not filled from the next run",
      p is None, repr(p))
p2 = B.make_path(o, h, l, c, 149, 288, run_end=700)
check("same bar with a long enough run does get a path", p2 is not None)
p3 = B.make_path(o, h, l, c, 0, 288)
check("run_end omitted keeps the old end-of-file contract", p3 is not None)

print("\n=== 2. collect_events uses the union archive span (not BTC only) ===")
d = tempfile.mkdtemp()
bars = os.path.join(d, "bars")
os.makedirs(bars)
btc = series(12 * 288, seed=1, sigma=0.001, amt=5e6)
alt = series(12 * 288, seed=2)
# ALT continues one hour past BTC's last bar.
alt = alt + [(alt[-1][0] + BAR_S * (k + 1), alt[-1][1], alt[-1][2],
              alt[-1][3], alt[-1][4], alt[-1][5], alt[-1][6]) for k in range(12)]
write(bars, "BTC", btc)
write(bars, "ALT", alt)
ev, meta = R.collect_events(bars, DEFAULT, ["ALT"])
union_max = max(btc[-1][0], alt[-1][0])
check("meta t_max is the union max across coins",
      meta["t_max"] == union_max, f"{meta['t_max']} != {union_max}")
check("meta n_days covers the union span",
      abs(meta["n_days"] - (union_max - min(btc[0][0], alt[0][0])) / 86400.0) < 1e-9,
      str(meta["n_days"]))
check("ALT's late events are inside the resolved span (day <= n_days)",
      all(e.day <= meta["n_days"] + 1e-9 for e in ev),
      f"max day {max((e.day for e in ev), default=0):.4f} vs {meta['n_days']:.4f}")

print("\n=== 3. invalid-bar restart recreates the CUSUM state ===")
d2 = tempfile.mkdtemp()
bars2 = os.path.join(d2, "bars")
os.makedirs(bars2)
write(bars2, "BTC", series(10 * 288, seed=1, sigma=0.001, amt=5e6))
rows = series(10 * 288, seed=3)
# Corrupt one bar well after warmup (h < l is invalid) inside the same run.
bad = 7 * 288
rows[bad] = (rows[bad][0], 100.0, 90.0, 95.0, 100.0, rows[bad][5], rows[bad][6])
write(bars2, "ALT2", rows)

created = []
_real_cusum = R.Cusum


class CountingCusum(_real_cusum):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        created.append(self)


R.Cusum = CountingCusum
try:
    R.collect_events(bars2, DEFAULT, ["ALT2"])
finally:
    R.Cusum = _real_cusum
check("a fresh CUSUM set is created after the invalid-bar restart",
      len(created) == 2 * len(R.SHOCK_HS),
      f"created {len(created)} CUSUMs, expected {2 * len(R.SHOCK_HS)}")

print("\n=== 4. a sparse-day config cannot win max-t selection ===")
from proto.quant import stats as S
# 30 events on only 2 distinct days: day_cluster_t() returns 0.0 today.
vals0 = [-1.0] * 30
days0 = [0] * 15 + [1] * 15
# 30 events over 10 days with a genuinely negative mean/t.
vals1 = [(-0.2 if i % 5 == 0 else -1.0) for i in range(30)]
days1 = [i // 3 for i in range(30)]
best, best_t, _ = S.spa_max_t([vals0, vals1], [days0, days1], B=100)
check("a D<3 config is excluded from max-t selection",
      best == 1 and best_t < 0, f"best={best} best_t={best_t}")
check("day_cluster_t itself still degrades to 0.0 for D<3 (documented)",
      S.day_cluster_t(vals0, days0) == 0.0)

print()
if FAILURES:
    print(f"FAILURES: {len(FAILURES)}")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
print("ALL PASS (pipeline regressions)")
