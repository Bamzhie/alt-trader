#!/usr/bin/env python3
"""Regression tests for the 2026-10-19 cost/split audit (audit B).

These are self-checking like the other tests in this repo: they print
"ALL PASS" and exit non-zero on the first mismatch.  Each case reproduces a
latent defect that did NOT change the canonical friction-only WS5 result but
does bias the funding-adjusted reporting:

1. `_settlement_grid` inferred the cycle from the whole archive's mode and the
   phase from the OLDEST row, so a coin whose funding cycle changed (or whose
   oldest row is on an old phase) was wrongly marked incomplete, and a coin
   whose code cycle is a subset of the real one was silently undercharged.
   The grid must be inferred from rows around the HOLDING WINDOW.
2. `funding_adjusted_config` exposed no friction-only mean over the same
   attributed events, so the report compared different denominators and
   narrated the composition difference as a funding credit.
3. A TIME exit was timestamped at the open of the final bar instead of the
   close, so at most one funding boundary was missed.
4. `check_no_leakage` used a 288-bar horizon; the actual span from the signal
   bar to the TIME close is 289 bars.
"""

import gzip
import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from proto.quant import barrier as B
from proto.quant import cost_attribution as ca
from proto.quant import eval_harness as eh
from proto.quant import research as R
from proto.quant.config import BAR_S, BARS_PER_DAY, DEFAULT, grid_geometries

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS ", name)
    else:
        print("  FAIL ", name, detail)
        FAILURES.append(name)


def write_settlements(directory, coin, rows):
    """rows: [(settle_ts, settled_rate, cycle_hours)]"""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, coin + ".csv.gz")
    with gzip.open(path, "wt", newline="") as f:
        f.write("settle_ts,settled_rate,cycle_hours\n")
        for ts, rate, cycle in rows:
            f.write(f"{ts},{rate},{cycle}\n")


T0 = 1_700_000_100
T0 -= T0 % BAR_S
# Epoch-aligned 8h anchor at/after T0.
B1 = 8 * 3600 * ((T0 // (8 * 3600)) + 1)


print("=== 1. settlement grid follows the holding window, not the whole archive ===")

sett_dir = os.path.join(tempfile.mkdtemp(prefix="fund-reg-"), "settlements")
W = B1 + 50 * 8 * 3600          # a later window, well away from old history
old = [(W - 50 * 4 * 3600 + k * 4 * 3600 + 7200, 0.0001, 4)
       for k in range(10)]      # old rows: 4h cycle, phase 7200
new = [(W + k * 4 * 3600, 0.0002, 4) for k in range(6)]  # current: phase 0
write_settlements(sett_dir, "CHG", sorted(old + new))

# Window strictly inside the current (phase-0) grid. Expected boundaries:
# W+4h, W+8h, W+12h. The old whole-archive inference uses phase=7200, so all
# three boundaries miss and the result is incomplete.
res = ca.funding_cost("CHG", W + 1, W + 3 * 4 * 3600 + 1,
                      funding_dir=None, settlements_dir=sett_dir)
check("cycle/phase change no longer causes a spurious incomplete window",
      not res.incomplete and res.cost is not None, str(res))
check("all current-grid boundaries are charged",
      res.observations == 3, str(res))
check("cost equals the three current venue settlements",
      res.cost == 3 * 0.0002, str(res))


print("\n=== 2. funding-adjusted mean is compared on the same events ===")

# 60 synthetic breakout/cont events: 30 on a coin with complete settlements
# and 30 on a coin with no funding archive.  The unfunded coin has a much
# worse gross outcome, so the unmatched friction-only mean is biased low.
geoms = grid_geometries()
geom = geoms[0]
geom_idx = 0
rule = next(r for r in R.rule_variants()
            if r.family == "breakout_start" and r.policy == "cont")
cfg = DEFAULT

sett2 = os.path.join(tempfile.mkdtemp(prefix="fund-reg2-"), "settlements")
write_settlements(sett2, "AAA", [(B1, 0.0001, 8)])

ng = len(geoms) * 2
events = []
for coin, gross, n_ev in (("AAA", 0.01, 30), ("BBB", -0.05, 30)):
    for j in range(n_ev):
        ev = R.Event()
        ev.coin = coin
        ev.ts = T0
        ev.day = 6 + (j % 10)
        ev.fam = "B"
        ev.param = 4.0
        ev.side = 1
        ev.qv24 = 50e6
        ev.sigma = 0.003
        ev.rvol = 4.0
        ev.z1h = 0.0
        ev.z4h = 0.0
        ev.btc7d = 0.0
        ev.gross = [gross] * ng
        ev.kind = [2] * ng               # TIME
        ev.exit_offset = [geom.horizon_bars - 1] * ng
        events.append(ev)

out = eh.funding_adjusted_config(
    events, rule, geom_idx, geom, (6, 61), cfg,
    funding_dir=None, settlements_dir=sett2)

check("explicit denominators: 30 of 60 attributed, 30 missing",
      out["n_events"] == 60 and out["n_funding_adjusted"] == 30
      and out["n_excluded_missing_coin"] == 30, str(out))
check("matched friction-only mean is exposed",
      "mean_r_friction_matched" in out,
      f"keys={sorted(out)}")
check("funding delta is computed on the matched set",
      "funding_delta" in out, f"keys={sorted(out)}")

k = geom_idx * 2
dd = B.stop_distance(0.003, geom.horizon_bars, geom.k_stop)
fric = cfg.cost.round_trip(50e6, 1.0, 1.0)
matched = B.net_r(0.01, dd, fric)
all_mean = (30 * matched + 30 * B.net_r(-0.05, dd, fric)) / 60.0

check("matched mean uses only the attributed events",
      abs(out.get("mean_r_friction_matched", 0.0) - matched) < 1e-12,
      f"got {out.get('mean_r_friction_matched')}, expected {matched}")
check("matched mean differs from the all-event friction mean",
      abs(out.get("mean_r_friction_matched", 0.0) - all_mean) > 0.005,
      f"matched={out.get('mean_r_friction_matched')} all={all_mean}")
check("funding_delta is not the unmatched contrast",
      abs(out.get("funding_delta", 0.0)
          - (out.get("mean_r", 0.0) - all_mean)) > 0.005,
      str(out))


print("\n=== 3. TIME exit is timestamped at the final bar's close ===")

ev = R.Event()
ev.coin = "AAA"
ev.ts = T0
ev.day = 6.0
ev.fam = "B"
ev.param = 4.0
ev.side = 1
ev.qv24 = 50e6
ev.sigma = 0.003
ev.rvol = 4.0
ev.z1h = 0.0
ev.z4h = 0.0
ev.btc7d = 0.0
ev.gross = [0.0] * ng
ev.kind = [2] * ng
ev.exit_offset = [geom.horizon_bars - 1] * ng

ts_time = eh._event_exit_ts(ev, geom_idx, rule)
check("TIME exit spans horizon+1 bars from the signal bar",
      ts_time == T0 + (geom.horizon_bars + 1) * BAR_S,
      f"got {ts_time}, expected {T0 + (geom.horizon_bars + 1) * BAR_S}")

ev.kind = [0] * ng                   # TP
ev.exit_offset = [5] * ng
ts_tp = eh._event_exit_ts(ev, geom_idx, rule)
check("TP/SL exit keeps the exit bar's open timestamp",
      ts_tp == T0 + (1 + 5) * BAR_S,
      f"got {ts_tp}, expected {T0 + 6 * BAR_S}")


print("\n=== 4. leakage bound includes the final bar's close ===")

splits = eh.EvalSplits(
    n_days=98.0, dev=(6.0, 61.0), val=(62.0, 77.0),
    holdout=(78.0, 98.0), embargo_days=1.0, first_event_day=0.0)
border = SimpleNamespace(day=60.9999, ts=0)
viol = eh.check_no_leakage([border], splits)
check("an event whose 289-bar window reaches val_start is flagged",
      any("reaches val_start" in v for v in viol),
      f"violations={viol}")


print()
if FAILURES:
    print(f"FAILURES: {len(FAILURES)}")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
print("ALL PASS (funding regressions)")
