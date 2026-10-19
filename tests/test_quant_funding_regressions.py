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


def write_funding(directory, coin, observations):
    """observations: [(ts, rate)]"""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, coin + ".csv.gz")
    with gzip.open(path, "wt", newline="") as f:
        f.write("ts,rate,cap\n")
        for ts, rate in observations:
            f.write(f"{ts},{rate},0.0018\n")


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


print("\n=== 5. cycle transitions inside a holding window ===")

# MEXC reports a per-row collectCycle: the interval in force FROM that
# settlement onwards.  A coin's cycle does change (1h/2h/4h/8h all occur),
# so a window can span a transition.  The venue's observed shapes are:
#   * SHORTENING (8h -> 4h): the new grid starts one new cycle after the
#     last old settlement, and the intermediate 4h settlements EXIST and
#     must be charged.
#   * LENGTHENING (4h -> 8h): the venue skips the intermediate 4h
#     settlement; the old grid's next boundary has no row, and that is
#     the venue's stated behaviour, not a missing row.
E = 28800 * 100          # epoch-aligned 8h (and 4h) boundary
H = 3600

# --- 5a: 8h -> 4h inside the window: the 4h settlements must be charged.
sett_a = os.path.join(tempfile.mkdtemp(prefix="fund-trans-a-"), "settlements")
rows_a = [(E + k * 8 * H, 0.001, 8) for k in range(3)]        # E, E+8h, E+16h
rows_a += [(E + 20 * H, 0.002, 4), (E + 24 * H, 0.002, 4)]    # 4h regime
write_settlements(sett_a, "SHORT", sorted(rows_a))
res_a = ca.funding_cost("SHORT", E + 8 * H, E + 28 * H,
                        funding_dir=None, settlements_dir=sett_a, side=1)
check("8h->4h transition: the window is complete (not a false gap)",
      not res_a.incomplete and res_a.cost is not None, str(res_a))
check("8h->4h transition: every settlement inside the window is charged",
      res_a.observations == 3, str(res_a))
check("8h->4h transition: the intermediate 4h settlement is not omitted",
      res_a.cost is not None
      and abs(res_a.cost - (0.001 + 0.002 + 0.002)) < 1e-12, str(res_a))

# --- 5b: 4h -> 8h inside the window: the skipped 4h boundary is the
# venue's stated behaviour and must NOT make the window incomplete.
sett_b = os.path.join(tempfile.mkdtemp(prefix="fund-trans-b-"), "settlements")
rows_b = [(E + k * 4 * H, 0.003, 4) for k in range(3)]        # E, E+4h, E+8h
rows_b += [(E + 16 * H, 0.004, 8), (E + 24 * H, 0.004, 8)]    # 8h regime
write_settlements(sett_b, "LONG", sorted(rows_b))
res_b = ca.funding_cost("LONG", E, E + 24 * H,
                        funding_dir=None, settlements_dir=sett_b, side=1)
check("4h->8h transition: the window is complete",
      not res_b.incomplete and res_b.cost is not None, str(res_b))
check("4h->8h transition: both regimes are charged (3 events)",
      res_b.observations == 3, str(res_b))
check("4h->8h transition: the skipped 4h boundary is not demanded",
      res_b.cost is not None
      and abs(res_b.cost - (0.003 + 0.003 + 0.004)) < 1e-12, str(res_b))

# --- 5c: a schedule that cannot be established -> incomplete, never a
# silent omission.
# 5c-1: a missing row INSIDE a regime: the grid still demands that
# boundary, so the settlement path reports a gap (the observation fallback
# may attribute it) and the window is never undercounted.
sett_c = os.path.join(tempfile.mkdtemp(prefix="fund-trans-c-"), "settlements")
write_settlements(sett_c, "HOLE", [(E, 0.001, 8), (E + 8 * H, 0.001, 8),
                                    (E + 24 * H, 0.001, 8)])
res_c = ca.funding_cost("HOLE", E, E + 24 * H,
                        funding_dir=None, settlements_dir=sett_c, side=1)
check("a missing settlement row inside a regime -> incomplete",
      res_c.incomplete and res_c.cost is None, str(res_c))
check("the cause is reported as a missing boundary, not a silent omission",
      "settlement-boundaries-missing" in (res_c.reason or ""), str(res_c))

# 5c-2: with covering observations the same gap falls back to the
# observation mapping and the missing boundary IS charged.
fund_c = os.path.join(tempfile.mkdtemp(prefix="fund-trans-c-"), "funding")
write_funding(fund_c, "HOLE", [(E - 1 * H, 0.0001), (E + 7 * H, 0.0002),
                               (E + 15 * H, 0.0003), (E + 23 * H, 0.0004)])
res_c2 = ca.funding_cost("HOLE", E, E + 24 * H,
                         funding_dir=fund_c, settlements_dir=sett_c, side=1)
check("a gap inside a regime falls back to the observation mapping",
      not res_c2.incomplete and res_c2.source == "observation", str(res_c2))
check("the fallback charges the missing boundary too (2 events, 0.0005)",
      res_c2.observations == 2
      and res_c2.cost is not None
      and abs(res_c2.cost - (0.0002 + 0.0003)) < 1e-12, str(res_c2))

# 5c-3: a missing row AT a transition leaves the new grid's phase
# unprovable -> the schedule cannot be established -> incomplete, even with
# covering observations (a settlement is never silently omitted).
sett_c3 = os.path.join(tempfile.mkdtemp(prefix="fund-trans-c3-"), "settlements")
write_settlements(sett_c3, "MISSINGROW", [(E, 0.001, 8), (E + 8 * H, 0.001, 8),
                                           (E + 16 * H, 0.001, 8),
                                           (E + 28 * H, 0.001, 4)])
res_c3 = ca.funding_cost("MISSINGROW", E, E + 28 * H,
                         funding_dir=fund_c, settlements_dir=sett_c3, side=1)
check("a missing row at the transition -> incomplete, not undercounted",
      res_c3.incomplete and res_c3.cost is None, str(res_c3))
check("the unverifiable transition is reported as such",
      "settlement-schedule-unverifiable" in (res_c3.reason or ""), str(res_c3))

# --- 5d: the observation fallback uses the SAME per-regime schedule.
# The settlement archive stops before the window ends (so the settlement
# path cannot cover the tail); the observation archive covers the window.
sett_d = os.path.join(tempfile.mkdtemp(prefix="fund-trans-d-"), "settlements")
fund_d = os.path.join(tempfile.mkdtemp(prefix="fund-trans-d-"), "funding")
write_settlements(sett_d, "OBS", [(E - 8 * H, 0.001, 8), (E, 0.001, 8),
                                   (E + 4 * H, 0.002, 4)])
write_funding(fund_d, "OBS", [(E - 1 * H, 0.0001), (E + 1 * H, 0.0002),
                              (E + 5 * H, 0.0003)])
res_d = ca.funding_cost("OBS", E - 4 * H, E + 12 * H,
                        funding_dir=fund_d, settlements_dir=sett_d, side=1)
check("observation fallback covers the window the settlements cannot",
      not res_d.incomplete and res_d.source == "observation", str(res_d))
check("observation fallback charges the 4h boundary too (3 events)",
      res_d.observations == 3, str(res_d))
check("observation fallback uses each regime's own snapshot window",
      res_d.cost is not None
      and abs(res_d.cost - (0.0001 + 0.0002 + 0.0003)) < 1e-12, str(res_d))

# The 4h boundary must use the 4h regime's snapshot window [E, E+4h):
# with that snapshot missing the boundary is uncovered -> incomplete (it
# must NOT be charged from the older 8h-regime snapshot).
fund_d2 = os.path.join(tempfile.mkdtemp(prefix="fund-trans-d2-"), "funding")
write_funding(fund_d2, "OBS", [(E - 1 * H, 0.0001), (E + 5 * H, 0.0003)])
res_d2 = ca.funding_cost("OBS", E - 4 * H, E + 12 * H,
                         funding_dir=fund_d2, settlements_dir=sett_d, side=1)
check("a boundary with no snapshot in its OWN regime window -> incomplete",
      res_d2.incomplete and res_d2.cost is None, str(res_d2))


print()
if FAILURES:
    print(f"FAILURES: {len(FAILURES)}")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
print("ALL PASS (funding regressions)")
