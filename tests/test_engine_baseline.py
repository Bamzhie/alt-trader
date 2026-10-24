"""Tests for proto.engine baseline calibration.

Covers:
  - baseline_random_entry agrees with roe.empirical_rates on a synthetic series
  - _compute_window_bounds returns the BTC-anchored bounds dict
    (pre-holdout = dev + validation, holdout boundary recorded)
  - the per-(leverage, horizon) holdout guard: no labeled bar may start at or
    after the holdout on a synthetic 100-day archive (1-day AND 14-day
    horizons), without over-excluding in-window moves
  - horizon arithmetic: horizons_days -> horizons_bars is d * 288
  - simulate_pass_counts matches the exact binomial PMF
  - per-coin tiered cost, point-in-time _filter_liquid_coins
  - point-in-time PER-ENTRY cost tiering (R2-2): uniform-volume agreement
    with roe.empirical_rates, a two-tier step-volume fixture whose expected
    result is the per-segment sum at each segment's own tier, distinct tier
    counts + cost_application labels, and selection/measurement separation
  - baseline never writes under the real data/ tree
"""
import math
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def _attempt(fn, *args, **kwargs):
    """Run fn; return (result, None), or (None, "ExcType: msg") on exception.

    A missing/changed API surface must show up as a FAIL, not crash the whole
    script before the remaining checks run.
    """
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:  # reported as a test failure below
        return None, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Helpers: build synthetic bar archives in temp dirs
# ---------------------------------------------------------------------------

import gzip

BARS_PER_DAY = 288
BAR_S = 300
DAY_S = 86400
BASE_TS = 1700000000


def _write_synthetic_bars(data_dir, coin, n_bars, price=100.0, vol=5000.0):
    """Write a flat-price bar file (constant price, so no TP/SL triggers randomly)."""
    os.makedirs(data_dir, exist_ok=True)
    header = "ts,o,h,l,c,vol,amount"
    rows = [header]
    for i in range(n_bars):
        ts = BASE_TS + i * BAR_S
        rows.append(f"{ts},{price},{price*1.001},{price*0.999},{price},{vol},{vol*price}")
    path = os.path.join(data_dir, f"{coin}.csv.gz")
    with gzip.open(path, "wt") as fh:
        fh.write("\n".join(rows) + "\n")
    return path


def _write_trending_bars(data_dir, coin, n_bars, start_price=100.0, slope=0.001,
                         vol=5000.0):
    """Write bars with a deterministic upward trend."""
    os.makedirs(data_dir, exist_ok=True)
    header = "ts,o,h,l,c,vol,amount"
    rows = [header]
    for i in range(n_bars):
        ts = BASE_TS + i * BAR_S
        p = start_price * (1 + slope) ** i
        rows.append(f"{ts},{p},{p*1.001},{p*0.999},{p},{vol},{vol*p}")
    path = os.path.join(data_dir, f"{coin}.csv.gz")
    with gzip.open(path, "wt") as fh:
        fh.write("\n".join(rows) + "\n")
    return path


def _write_step_bars(data_dir, coin, n_bars, step_start_ts, base_price=100.0,
                     step_price=120.0, vol=5000.0):
    """Contiguous flat archive; every bar with ts >= step_start_ts is step_price.

    Constant volume high enough for the QV gate (288 * vol * price ~ 1.4e8).
    """
    os.makedirs(data_dir, exist_ok=True)
    header = "ts,o,h,l,c,vol,amount"
    rows = [header]
    for i in range(n_bars):
        ts = BASE_TS + i * BAR_S
        p = step_price if ts >= step_start_ts else base_price
        rows.append(f"{ts},{p},{p*1.001},{p*0.999},{p},{vol},{vol*p}")
    path = os.path.join(data_dir, f"{coin}.csv.gz")
    with gzip.open(path, "wt") as fh:
        fh.write("\n".join(rows) + "\n")
    return path


def _make_holdout_fixture(prefix, step_day):
    """100-day two-coin archive under DEFAULT.splits.bounds(100).

    COIN's price steps +20% from the first bar at/after `step_day` onward;
    BTC stays flat (it is only the anchor coin for the window bounds).
    """
    d = tempfile.mkdtemp(prefix=prefix)
    _write_step_bars(d, "BTC", 100 * BARS_PER_DAY, step_start_ts=BASE_TS + 10 ** 9)
    _write_step_bars(d, "COIN", 100 * BARS_PER_DAY,
                     step_start_ts=BASE_TS + step_day * DAY_S)
    return d


def _write_two_tier_fixture(data_dir, coin="STEPVOL"):
    """Point-in-time cost-tiering fixture: two gap-free runs in ONE archive.

    Run A (6 days) carries HIGH volume (qv24 = 288*2000*100 = 5.76e7 >= 30M,
    tier 0.0013), run B (6 days) LOW volume (qv24 = 288*200*100 = 5.76e6,
    tier 0.0022), separated by a 12-BAR gap.  12 bars is the smallest gap
    that PRESERVES the global stride phase across the split: run B starts at
    absolute index 1728 + 12 = 1740, which is ≡ 0 (mod 12) exactly like run
    A's start 0, so both runs' sampled entries sit at RELATIVE indices
    288 + 12m — slicing the archive into the two runs reproduces the very
    entries the combined archive samples (a one-bar gap would shift run B's
    phase by one).  The gap is also what keeps every sampled entry's
    trailing-24h window volume-pure: a within-run volume step would blend
    qv24 through the middle tier (~10M-30M) for the ~24 grid entries around
    the boundary, which no two-tier expectation can express — inside a run
    the 24h window is contiguous by construction.

    Each run steps price 100 -> 110.05 at its local day 3.  The step's high
    (110.05*1.001 = 110.16005) clears the tier-A long target
    (100*(1+0.1+0.0013) = 110.130) but NOT the tier-B one
    (100*(1+0.1+0.0022) = 110.220), so the cost tier an entry is labeled at
    CHANGES the outcome: run A's step entries are TP under their own tier and
    TIME under the boundary tier.  Returns (cost_A, cost_B, t_b0) with t_b0
    = run B's first timestamp.
    """
    os.makedirs(data_dir, exist_ok=True)
    len_run = 6 * BARS_PER_DAY
    gap = 12  # keeps run B on the global stride phase (1740 % 12 == 0)
    step_local = 3 * BARS_PER_DAY
    hi_vol, lo_vol = 2000.0, 200.0
    rows = ["ts,o,h,l,c,vol,amount"]

    def _emit(ts, p, vol):
        rows.append(f"{ts},{p},{p*1.001},{p*0.999},{p},{vol},{vol*p}")

    for k in range(len_run):
        _emit(BASE_TS + k * BAR_S, 110.05 if k >= step_local else 100.0, hi_vol)
    for k in range(len_run):  # 12-bar gap before run B (two contiguous runs)
        _emit(BASE_TS + (len_run + gap + k) * BAR_S,
              110.05 if k >= step_local else 100.0, lo_vol)
    with gzip.open(os.path.join(data_dir, f"{coin}.csv.gz"), "wt") as fh:
        fh.write("\n".join(rows) + "\n")
    cost_a = DEFAULT.cost.round_trip(BARS_PER_DAY * hi_vol * 100.0)
    cost_b = DEFAULT.cost.round_trip(BARS_PER_DAY * lo_vol * 100.0)
    return cost_a, cost_b, BASE_TS + (len_run + gap) * BAR_S


# ---------------------------------------------------------------------------
# Test 1: baseline_random_entry agrees with roe.empirical_rates
# ---------------------------------------------------------------------------

print("=== Test: baseline agrees with roe.empirical_rates ===")

from proto.engine.baseline import baseline_one_coin
from proto.quant import roe as R
from proto.quant import bars as B
from proto.quant.config import DEFAULT, BARS_PER_DAY

# Build a synthetic archive with trending BTC
tmpdir = tempfile.mkdtemp(prefix="baseline_test_")
_write_trending_bars(tmpdir, "BTC", 5 * BARS_PER_DAY, slope=0.0005)

bars = B.load_coin(tmpdir, "BTC")
cost = DEFAULT.cost.round_trip(1e9)  # high volume → lowest tier

# Contract geometry
leverage = 10.0
stop_roe = 2.5
target_roe = 1.0
horizon = BARS_PER_DAY  # 1 day

plan = R.target_plan(stop_roe, leverage, cost, target_roe)
check("target_plan returns a dict", isinstance(plan, dict))
check("target_plan p_null is finite", math.isfinite(plan["p_null"]))

# Run our baseline on one coin
bl = baseline_one_coin(tmpdir, "BTC", leverage, stop_roe, target_roe,
                        horizon, cost, stride=12, t_lo=None, t_hi=None,
                        min_qv24=1e6)
check("baseline returns dict", isinstance(bl, dict))
check("baseline has tp rate", "tp_rate" in bl)
check("baseline has sl rate", "sl_rate" in bl)
check("baseline has time rate", "time_rate" in bl)
check("baseline has mean_roe", "mean_roe" in bl)
check("baseline has n_samples", "n_samples" in bl)

# Compare with empirical_rates directly
raw = R.empirical_rates(bars, plan, cost, horizon=horizon, stride=12,
                        t_lo=None, t_hi=None, min_qv24=1e6)
total = raw["n"]

# Our baseline should report the same total outcome count
check("baseline n_samples matches empirical_rates n",
      bl["n_samples"] == total,
      f"baseline={bl['n_samples']} vs empirical={total}")

# TP rate = tp / n (among terminal outcomes)
if total > 0:
    expected_tp = raw["tp"] / total
    expected_sl = raw["sl"] / total
    expected_time = raw["time"] / total
    check("baseline tp_rate matches empirical",
          abs(bl["tp_rate"] - expected_tp) < 1e-12,
          f"baseline={bl['tp_rate']} vs empirical={expected_tp}")
    check("baseline sl_rate matches empirical",
          abs(bl["sl_rate"] - expected_sl) < 1e-12,
          f"baseline={bl['sl_rate']} vs empirical={expected_sl}")
    check("baseline time_rate matches empirical",
          abs(bl["time_rate"] - expected_time) < 1e-12,
          f"baseline={bl['time_rate']} vs empirical={expected_time}")

# Mean ROE: empirical_rates stores per-outcome ROE in raw["roe"]
if raw["roe"]:
    expected_mean_roe = sum(raw["roe"]) / len(raw["roe"])
    check("baseline mean_roe matches empirical",
          abs(bl["mean_roe"] - expected_mean_roe) < 1e-9,
          f"baseline={bl['mean_roe']} vs empirical={expected_mean_roe}")

# --- R2-2 anchor 1: with cost_frac=None the POINT-IN-TIME per-entry loop
# must reproduce empirical_rates EXACTLY on a uniform-volume archive (every
# trailing-24h window sits in the 0.0013 tier, so every entry's own cost
# equals the one-tier constant empirical_rates would use).
cost_u = DEFAULT.cost.round_trip(BARS_PER_DAY * 5000.0 * 100.0)
check("uniform fixture precondition: every 24h window is in the 0.0013 tier",
      abs(cost_u - 0.0013) < 1e-12, f"got {cost_u!r}")
bl_pit, err = _attempt(baseline_one_coin, tmpdir, "BTC", leverage,
                       stop_roe=stop_roe, target_roe=target_roe,
                       horizon_bars=horizon, cost_frac=None, stride=12,
                       t_lo=None, t_hi=None, min_qv24=1e6)
check("baseline_one_coin(cost_frac=None) per-entry mode runs", err is None,
      err or "")
if err is None:
    plan_u = R.target_plan(stop_roe, leverage, cost_u, target_roe)
    raw_u = R.empirical_rates(bars, plan_u, cost_u, horizon=horizon, stride=12,
                              t_lo=None, t_hi=None, min_qv24=1e6)
    check("per-entry mode: n_samples == empirical_rates at the uniform tier",
          bl_pit["n_samples"] == raw_u["n"],
          f"per-entry={bl_pit['n_samples']} vs empirical={raw_u['n']}")
    check("per-entry mode: tp/sl/time == empirical_rates",
          (bl_pit["tp"], bl_pit["sl"], bl_pit["time"])
          == (raw_u["tp"], raw_u["sl"], raw_u["time"]),
          f"per-entry={(bl_pit['tp'], bl_pit['sl'], bl_pit['time'])} "
          f"vs empirical={(raw_u['tp'], raw_u['sl'], raw_u['time'])}")
    if raw_u["roe"]:
        check("per-entry mode: mean_roe == empirical_rates",
              abs(bl_pit["mean_roe"] - sum(raw_u["roe"]) / len(raw_u["roe"]))
              < 1e-9,
              f"per-entry={bl_pit['mean_roe']}")
    check("per-entry mode labels the cost application",
          bl_pit.get("cost_application")
          == "point-in-time at each sampled entry (trailing-24h QV at the "
             "entry bar)",
          f"got {bl_pit.get('cost_application')!r}")
    check("per-entry mode reports exactly the uniform tier's counts",
          bl_pit.get("entry_cost_counts") == {repr(cost_u): raw_u["n"]},
          f"got {bl_pit.get('entry_cost_counts')!r}")

# --- R2-2 anchor 2: step-volume two-tier fixture (RED on boundary-cost
# code).  The coin's first run carries HIGH volume (tier 0.0013), its second
# run LOW volume >= floor (tier 0.0022).  Per-entry tiering must label each
# run's entries at that run's OWN tier: expected = empirical_rates on
# segment A at cost_A + empirical_rates on segment B at cost_B (same
# archive, same global stride grid, t bounds splitting the two runs).  The
# OLD code labels EVERY entry at the archive-end boundary tier (0.0022) and
# mislabels run A's step entries (TP at their own tier, TIME at the
# boundary tier).
tmpdir_pit = tempfile.mkdtemp(prefix="baseline_pit_")
cost_a, cost_b, t_b0 = _write_two_tier_fixture(tmpdir_pit)
check("two-tier precondition: run A tier is 0.0013", abs(cost_a - 0.0013) < 1e-12,
      f"got {cost_a!r}")
check("two-tier precondition: run B tier is 0.0022", abs(cost_b - 0.0022) < 1e-12,
      f"got {cost_b!r}")

bars_pit = B.load_coin(tmpdir_pit, "STEPVOL")
plan_a = R.target_plan(stop_roe, leverage, cost_a, target_roe)
plan_b = R.target_plan(stop_roe, leverage, cost_b, target_roe)
raw_a = R.empirical_rates(bars_pit, plan_a, cost_a, horizon=horizon, stride=12,
                          t_lo=None, t_hi=t_b0, min_qv24=1e6)
raw_b = R.empirical_rates(bars_pit, plan_b, cost_b, horizon=horizon, stride=12,
                          t_lo=t_b0, t_hi=None, min_qv24=1e6)
check("two-tier fixture yields outcomes on both sides of the split",
      raw_a["n"] > 0 and raw_b["n"] > 0, f"A={raw_a['n']}, B={raw_b['n']}")

bl_step, err_step = _attempt(baseline_one_coin, tmpdir_pit, "STEPVOL", leverage,
                             stop_roe=stop_roe, target_roe=target_roe,
                             horizon_bars=horizon, cost_frac=None, stride=12,
                             t_lo=None, t_hi=None, min_qv24=1e6)
check("per-entry mode runs on the two-tier fixture", err_step is None,
      err_step or "")
if err_step is None:
    check("two-tier: n == segment sum (A at cost_A + B at cost_B)",
          bl_step["n_samples"] == raw_a["n"] + raw_b["n"],
          f"got {bl_step['n_samples']}, expected {raw_a['n'] + raw_b['n']}")
    check("two-tier: tp/sl/time == segment sums at each segment's own tier",
          (bl_step["tp"], bl_step["sl"], bl_step["time"])
          == (raw_a["tp"] + raw_b["tp"], raw_a["sl"] + raw_b["sl"],
              raw_a["time"] + raw_b["time"]),
          f"got {(bl_step['tp'], bl_step['sl'], bl_step['time'])}, expected "
          f"{(raw_a['tp'] + raw_b['tp'], raw_a['sl'] + raw_b['sl'], raw_a['time'] + raw_b['time'])}")
    exp_roe = (sum(raw_a["roe"]) + sum(raw_b["roe"])) / (raw_a["n"] + raw_b["n"])
    check("two-tier: mean_roe == tier-weighted segment mean",
          abs(bl_step["mean_roe"] - exp_roe) < 1e-9,
          f"got {bl_step['mean_roe']}, expected {exp_roe}")
    # the heart of the anchor: run A's step entries are TP at their own
    # tier and cannot be at the archive-end tier the old code used
    check("two-tier: run A's own tier produces TPs the boundary tier cannot "
          "(the mislabeling this fix removes)",
          raw_a["tp"] > 0 and bl_step["tp"] == raw_a["tp"] + raw_b["tp"],
          f"tpA={raw_a['tp']}, tpB={raw_b['tp']}, got tp={bl_step['tp']}")

    # --- R2-2 anchor 3: the distinct tiers are actually used and labeled
    check("two-tier: cost_application is the point-in-time label",
          bl_step.get("cost_application")
          == "point-in-time at each sampled entry (trailing-24h QV at the "
             "entry bar)",
          f"got {bl_step.get('cost_application')!r}")
    cc = bl_step.get("entry_cost_counts") or {}
    check("two-tier: exactly the two expected tier keys, both nonzero",
          set(cc) == {repr(cost_a), repr(cost_b)}
          and cc.get(repr(cost_a), 0) > 0 and cc.get(repr(cost_b), 0) > 0,
          f"got {cc!r}")
    check("two-tier: tier outcome counts equal the per-segment totals",
          cc.get(repr(cost_a)) == raw_a["n"]
          and cc.get(repr(cost_b)) == raw_b["n"],
          f"got {cc!r}, A={raw_a['n']}, B={raw_b['n']}")

    # Explicit cost_frac override: fixed-cost sensitivity mode, labeled as
    # such; counts collapse to the single override key, and the numbers
    # equal empirical_rates at that one fixed cost.
    bl_fix, err_fix = _attempt(baseline_one_coin, tmpdir_pit, "STEPVOL",
                               leverage, stop_roe=stop_roe,
                               target_roe=target_roe,
                               horizon_bars=horizon, cost_frac=cost_a,
                               stride=12, t_lo=None, t_hi=None, min_qv24=1e6)
    check("fixed-cost override run does not raise", err_fix is None,
          err_fix or "")
    if err_fix is None:
        check("override: cost_application is the fixed label",
              bl_fix.get("cost_application") == "fixed (cost_frac override)",
              f"got {bl_fix.get('cost_application')!r}")
        ccf = bl_fix.get("entry_cost_counts") or {}
        check("override: counts collapse to exactly the override key",
              list(ccf) == [repr(cost_a)]
              and ccf[repr(cost_a)] == bl_fix["n_samples"],
              f"got {ccf!r}, n={bl_fix['n_samples']}")
        raw_fix = R.empirical_rates(bars_pit, plan_a, cost_a, horizon=horizon,
                                    stride=12, t_lo=None, t_hi=None,
                                    min_qv24=1e6)
        check("override: tp/sl/time == empirical_rates at the fixed cost",
              (bl_fix["tp"], bl_fix["sl"], bl_fix["time"])
              == (raw_fix["tp"], raw_fix["sl"], raw_fix["time"]),
              f"got {(bl_fix['tp'], bl_fix['sl'], bl_fix['time'])}")

# --- R2-2 anchor 4: selection/measurement separation in the grid runner.
# Universe selection (the boundary gate) is UNCHANGED and disclosed via
# selection_cost_note; every (L, T) cell measures point-in-time.
from proto.engine.baseline import baseline_random_entry as _bre

res_pit, err_grid = _attempt(_bre, tmpdir_pit, ["STEPVOL"], leverages=(10,),
                             horizons_days=(1,), stride=12,
                             t_lo=None, t_hi=None, min_qv24=1e6)
check("baseline_random_entry per-entry mode runs", err_grid is None,
      err_grid or "")
if err_grid is None:
    check("grid: top-level cost_application labels point-in-time measurement",
          res_pit.get("cost_application")
          == "point-in-time at each sampled entry (trailing-24h QV at the "
             "entry bar)",
          f"got {res_pit.get('cost_application')!r}")
    check("grid: selection_cost_note discloses the boundary cost as "
          "selection-only",
          res_pit.get("selection_cost_note")
          == "cost_frac_per_coin is the boundary cost used ONLY for universe "
             "selection; measurement costs are point-in-time per entry",
          f"got {res_pit.get('selection_cost_note')!r}")
    check("grid: cost_frac_per_coin kept, still the boundary selection cost",
          res_pit.get("cost_frac_per_coin") == {"STEPVOL": cost_b},
          f"got {res_pit.get('cost_frac_per_coin')!r}")
    cell_pit = res_pit["per_coin"]["STEPVOL"][10][1]
    check("grid: per-cell entry_cost_counts carries both tiers",
          set(cell_pit.get("entry_cost_counts") or {})
          == {repr(cost_a), repr(cost_b)},
          f"got {cell_pit.get('entry_cost_counts')!r}")
    if err_step is None:
        check("grid: per-cell counts equal baseline_one_coin per-entry counts",
              cell_pit.get("entry_cost_counts")
              == bl_step.get("entry_cost_counts"),
              f"grid={cell_pit.get('entry_cost_counts')!r}")
        check("grid: per-cell numbers equal baseline_one_coin per-entry numbers",
              (cell_pit["n_samples"], cell_pit["tp"], cell_pit["sl"],
               cell_pit["time"])
              == (bl_step["n_samples"], bl_step["tp"], bl_step["sl"],
                  bl_step["time"]),
              "grid/one-coin per-entry disagreement")

print("\n=== Test: simulate_pass_counts matches binomial ===")

from proto.engine.baseline import simulate_pass_counts

# Binomial params: n=20, p=0.71 (near the no-skill rate from plan §2.3)
for n, p in [(20, 0.71), (50, 0.50), (100, 0.75)]:
    sim = simulate_pass_counts(n, p, K=20000, seed=42)
    check(f"n={n} p={p}: sim has pmf", isinstance(sim["pmf"], dict))
    check(f"n={n} p={p}: sim has mean", "mean" in sim)
    check(f"n={n} p={p}: sim has lo95", "lo95" in sim)
    check(f"n={n} p={p}: sim has hi95", "hi95" in sim)

    # Exact binomial mean is n*p
    expected_mean = n * p
    check(f"n={n} p={p}: mean matches n*p",
          abs(sim["mean"] - expected_mean) < 0.5,
          f"sim={sim['mean']:.4f} vs exact={expected_mean:.4f}")

    # Exact binomial PMF check for a few values of k
    for k in range(n + 1):
        exact_pmf = math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))
        entry = sim["pmf"].get(str(k), {})
        sim_pmf = entry.get("sim_prob", 0.0) if isinstance(entry, dict) else entry
        # Simulation tolerance: ~3 sigma of binomial proportion
        tol = 3.0 * math.sqrt(exact_pmf * (1 - exact_pmf) / 20000)
        if exact_pmf > 0.001:  # only check non-tiny probabilities
            check(f"n={n} p={p} k={k}: PMF matches binomial",
                  abs(sim_pmf - exact_pmf) < max(tol, 0.005),
                  f"sim={sim_pmf:.6f} vs exact={exact_pmf:.6f}")

print("\n=== Test: baseline pass rate equals TP-first rate ===")

# The "pass" in the benchmark = TP first. So pass_rate should equal tp_rate.
check("baseline pass_rate == tp_rate",
      abs(bl["pass_rate"] - bl["tp_rate"]) < 1e-12,
      f"pass={bl['pass_rate']} vs tp={bl['tp_rate']}")

print("\n=== Test: baseline does not write under data/ ===")

# Run baseline on synthetic data in a temp dir, verify nothing created in data/
real_data = os.path.join(REPO, "data")
before_mtime = os.stat(real_data).st_mtime_ns if os.path.exists(real_data) else 0

from proto.engine.baseline import baseline_random_entry
result = baseline_random_entry(tmpdir, ["BTC"], leverages=(10, 20),
                                horizons_days=(1, 3), seed=1)

after_mtime = os.stat(real_data).st_mtime_ns if os.path.exists(real_data) else 0
check("data/ mtime unchanged by baseline",
      before_mtime == after_mtime)

# Verify no DB was created or modified by baseline
# (signals.db and quant.db may pre-exist as the live scanner's DBs —
#  we only check they were not touched by the baseline command)
import hashlib

def _fingerprint(path):
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    return None

sig_path = os.path.join(REPO, "data", "signals.db")
quant_path = os.path.join(REPO, "data", "quant.db")
sig_before = _fingerprint(sig_path)
quant_before = _fingerprint(quant_path)

# Run baseline again
result = baseline_random_entry(tmpdir, ["BTC"], leverages=(10, 20),
                                horizons_days=(1, 3), seed=1)

sig_after = _fingerprint(sig_path)
quant_after = _fingerprint(quant_path)
check("signals.db unchanged by baseline", sig_before == sig_after)
check("quant.db unchanged by baseline", quant_before == quant_after)
check("no research DB created in data/",
      not os.path.exists(os.path.join(REPO, "data", "research",
                                       "altradar-research.db")))


# ---------------------------------------------------------------------------
# Test: _compute_window_bounds returns the BTC-anchored bounds dict
# ---------------------------------------------------------------------------

print("\n=== Test: _compute_window_bounds returns the anchored bounds dict ===")

from proto.engine.baseline import _compute_window_bounds

tmpdir_bounds = tempfile.mkdtemp(prefix="baseline_bounds_")
_write_step_bars(tmpdir_bounds, "BTC", 100 * BARS_PER_DAY,
                 step_start_ts=BASE_TS + 10 ** 9)
_write_step_bars(tmpdir_bounds, "COIN", 100 * BARS_PER_DAY,
                 step_start_ts=BASE_TS + 79 * DAY_S)

wb, err = _attempt(_compute_window_bounds, tmpdir_bounds, "pre-holdout")
check("_compute_window_bounds('pre-holdout') does not raise", err is None,
      err or "")
if err is None:
    check("returns a dict (not a tuple)", isinstance(wb, dict),
          f"got {type(wb).__name__}: {wb!r}")
if err is None and isinstance(wb, dict):
    check("window name recorded", wb["window"] == "pre-holdout",
          f"got {wb.get('window')!r}")
    check("anchor coin is BTC", wb["anchor_coin"] == "BTC",
          f"got {wb.get('anchor_coin')!r}")
    check("anchor_first_ts is BTC's first bar", wb["anchor_first_ts"] == BASE_TS,
          f"got {wb.get('anchor_first_ts')!r}")
    check("t_min equals anchor first ts", wb["t_min"] == BASE_TS,
          f"got {wb.get('t_min')!r}")
    expected_n_days = (100 * BARS_PER_DAY - 1) * BAR_S / 86400.0
    check("n_days from BTC's span",
          abs(wb["n_days"] - expected_n_days) < 1e-9,
          f"got {wb.get('n_days')!r}, expected {expected_n_days}")
    check("t_lo = anchor + dev start day 6",
          wb["t_lo"] == BASE_TS + 6 * DAY_S,
          f"got {wb.get('t_lo')!r}, expected {BASE_TS + 6 * DAY_S}")
    check("val_end_ts = anchor + day 77 (dev+validation end)",
          wb["val_end_ts"] == BASE_TS + 77 * DAY_S,
          f"got {wb.get('val_end_ts')!r}, expected {BASE_TS + 77 * DAY_S}")
    check("holdout_start_ts = anchor + day 79",
          wb["holdout_start_ts"] == BASE_TS + 79 * DAY_S,
          f"got {wb.get('holdout_start_ts')!r}, expected {BASE_TS + 79 * DAY_S}")
    # Cross-check every boundary against DEFAULT.splits.bounds on n_days
    bnd = DEFAULT.splits.bounds(wb["n_days"])
    check("boundaries agree with DEFAULT.splits.bounds(n_days)",
          wb["t_lo"] == BASE_TS + int(bnd["dev"][0] * DAY_S)
          and wb["val_end_ts"] == BASE_TS + int(bnd["val"][1] * DAY_S)
          and wb["holdout_start_ts"] == BASE_TS + int(bnd["holdout"][0] * DAY_S),
          f"bnd={bnd}")
    check("boundaries_in_archive True on a 100-day archive",
          wb["boundaries_in_archive"] is True,
          f"got {wb.get('boundaries_in_archive')!r}")

wf, err = _attempt(_compute_window_bounds, tmpdir_bounds, "full")
check("_compute_window_bounds('full') does not raise", err is None, err or "")
if err is None and isinstance(wf, dict):
    check("full: t_lo is None", wf["t_lo"] is None, f"got {wf.get('t_lo')!r}")
    check("full: val_end_ts is None", wf["val_end_ts"] is None,
          f"got {wf.get('val_end_ts')!r}")
    check("full: holdout_start_ts is None", wf["holdout_start_ts"] is None,
          f"got {wf.get('holdout_start_ts')!r}")
    check("full: boundaries_in_archive is False",
          wf["boundaries_in_archive"] is False,
          f"got {wf.get('boundaries_in_archive')!r}")
    check("full: anchor still recorded",
          wf["window"] == "full" and wf["anchor_coin"] == "BTC"
          and wf["t_min"] == BASE_TS, f"got {wf!r}")

_, err = _attempt(_compute_window_bounds, tmpdir_bounds, "bogus-window")
check("unknown window raises ValueError",
      err is not None and err.startswith("ValueError"), err or "no error raised")

empty_dir = tempfile.mkdtemp(prefix="baseline_nobtc_")
_, err = _attempt(_compute_window_bounds, empty_dir, "pre-holdout")
check("missing BTC raises ValueError('BTC bars required ...')",
      err is not None and err.startswith("ValueError")
      and "BTC bars required" in err, err or "no error raised")


# ---------------------------------------------------------------------------
# Test: pre-holdout excludes holdout outcomes (1-day horizon) + exact count
# ---------------------------------------------------------------------------

print("\n=== Test: pre-holdout excludes holdout outcomes (1-day horizon) ===")

d79 = _make_holdout_fixture("baseline_hold79_", 79)
bounds79, err = _attempt(_compute_window_bounds, d79, "pre-holdout")
have_bounds79 = err is None and isinstance(bounds79, dict)
check("bounds computed on the day-79-step fixture", have_bounds79,
      err or f"got {type(bounds79).__name__}")
if have_bounds79:
    res1, err = _attempt(baseline_random_entry, d79, ["COIN"],
                         leverages=(10,), horizons_days=(1,), stride=12,
                         t_lo=bounds79["t_lo"], t_hi=bounds79["val_end_ts"],
                         holdout_start_ts=bounds79["holdout_start_ts"],
                         min_qv24=1e6)
    check("baseline_random_entry accepts holdout_start_ts", err is None,
          err or "")
    if err is None:
        cell1 = res1["per_coin"]["COIN"][10][1]
        n1 = cell1["n_samples"]
        check("n_samples > 0", n1 > 0, f"n={n1}")
        check("tp == 0 (a flat pre-holdout region can only produce TIME; "
              "any TP means a holdout outcome leaked)",
              cell1["tp"] == 0, f"tp={cell1['tp']}")
        check("time == n_samples", cell1["time"] == n1,
              f"time={cell1['time']} vs n={n1}")

        # Exact hand-computed grid count.  Single contiguous run with
        # run_z = 100*288 bars; entries at indices i with i ≡ 288 (mod 12)
        # (the global stride grid), i >= 288, ts[i] >= t_lo,
        # ts[i] < t_hi_cell = min(val_end_ts, holdout_start_ts − 288*BAR_S)
        # (label window covers bars i+1..i+288), i < run_z − 288; both sides
        # labeled per entry.
        run_z = 100 * BARS_PER_DAY
        h_bars = BARS_PER_DAY
        t_hi_cell = min(bounds79["val_end_ts"],
                        bounds79["holdout_start_ts"] - h_bars * BAR_S)
        expected_entries = 0
        for i in range(BARS_PER_DAY, run_z - h_bars, 12):
            if i % 12 != 288 % 12:      # global grid phase
                continue
            ts_i = BASE_TS + i * BAR_S
            if ts_i < bounds79["t_lo"] or ts_i >= t_hi_cell:
                continue
            expected_entries += 1
        expected_n = 2 * expected_entries
        check("n_samples equals the hand-computed grid count x 2 sides",
              n1 == expected_n, f"n={n1}, hand-computed={expected_n}")
        check("hand-computed count pins to 3408 (1704 entries x 2)",
              expected_n == 3408, f"hand-computed={expected_n}")

        # full is distinct on the same fixture: the holdout step IS measured
        res2, err = _attempt(baseline_random_entry, d79, ["COIN"],
                             leverages=(10,), horizons_days=(1,), stride=12,
                             t_lo=None, t_hi=None, holdout_start_ts=None,
                             min_qv24=1e6)
        check("full-window run does not raise", err is None, err or "")
        if err is None:
            cell2 = res2["per_coin"]["COIN"][10][1]
            check("full window: tp > 0 (proves the guard, not the flat price, "
                  "excluded holdout outcomes)", cell2["tp"] > 0,
                  f"tp={cell2['tp']}")
            check("full window samples more than pre-holdout",
                  cell2["n_samples"] > n1,
                  f"full={cell2['n_samples']} vs pre={n1}")


# ---------------------------------------------------------------------------
# Test: pre-holdout excludes holdout outcomes (14-day horizon)
# ---------------------------------------------------------------------------

print("\n=== Test: pre-holdout excludes holdout outcomes (14-day horizon) ===")

if have_bounds79:
    res3, err = _attempt(baseline_random_entry, d79, ["COIN"],
                         leverages=(10,), horizons_days=(14,), stride=12,
                         t_lo=bounds79["t_lo"], t_hi=bounds79["val_end_ts"],
                         holdout_start_ts=bounds79["holdout_start_ts"],
                         min_qv24=1e6)
    check("14-day run does not raise", err is None, err or "")
    if err is None:
        cell3 = res3["per_coin"]["COIN"][10][14]
        check("14-day pre-holdout: n_samples > 0", cell3["n_samples"] > 0,
              f"n={cell3['n_samples']}")
        check("14-day pre-holdout: tp == 0 (per-cell guard excludes entries "
              "whose 14-day label windows reach the holdout; a t_hi-only "
              "implementation samples day-76 entries that reach the day-80 "
              "jump)", cell3["tp"] == 0, f"tp={cell3['tp']}")


# ---------------------------------------------------------------------------
# Test: no over-exclusion — in-window moves are still measured
# ---------------------------------------------------------------------------

print("\n=== Test: no over-exclusion (step at day 70 stays measurable) ===")

d70 = _make_holdout_fixture("baseline_hold70_", 70)
bounds70, err = _attempt(_compute_window_bounds, d70, "pre-holdout")
have_bounds70 = err is None and isinstance(bounds70, dict)
check("bounds computed on the day-70-step fixture", have_bounds70,
      err or f"got {type(bounds70).__name__}")
if have_bounds70:
    res4, err = _attempt(baseline_random_entry, d70, ["COIN"],
                         leverages=(10,), horizons_days=(1,), stride=12,
                         t_lo=bounds70["t_lo"], t_hi=bounds70["val_end_ts"],
                         holdout_start_ts=bounds70["holdout_start_ts"],
                         min_qv24=1e6)
    check("day-70 run does not raise", err is None, err or "")
    if err is None:
        cell4 = res4["per_coin"]["COIN"][10][1]
        # The day-70 step's 1-day label windows end by day 71 < 79 (holdout),
        # so the guard must NOT exclude it.
        check("day-70 step (label windows end well before the holdout) is "
              "measured: tp > 0", cell4["tp"] > 0, f"tp={cell4['tp']}")


# ---------------------------------------------------------------------------
# Test: horizon arithmetic — horizons_days d maps to d*288 bars everywhere
# ---------------------------------------------------------------------------

print("\n=== Test: horizon arithmetic (3 days == 864 bars) ===")

tmpX = tempfile.mkdtemp(prefix="baseline_hxbars_")
_write_trending_bars(tmpX, "X", 5 * BARS_PER_DAY, slope=0.001)

one864, err = _attempt(baseline_one_coin, tmpX, "X", 10.0,
                       horizon_bars=3 * BARS_PER_DAY, stride=12,
                       t_lo=None, t_hi=None, holdout_start_ts=None,
                       min_qv24=1e6)
check("baseline_one_coin accepts holdout_start_ts and 864-bar horizon",
      err is None, err or "")
grid3, err = _attempt(baseline_random_entry, tmpX, ["X"], leverages=(10,),
                      horizons_days=(3,), stride=12,
                      t_lo=None, t_hi=None, holdout_start_ts=None,
                      min_qv24=1e6)
check("baseline_random_entry 3-day run does not raise", err is None, err or "")
if err is None and one864 is not None:
    gcell = grid3["per_coin"]["X"][10][3]
    check("3-day random_entry n_samples == one_coin(horizon_bars=864) n_samples",
          gcell["n_samples"] == one864["n_samples"] and one864["n_samples"] > 0,
          f"grid={gcell['n_samples']} vs one={one864['n_samples']}")
    check("3-day random_entry tp == one_coin(864) tp",
          gcell["tp"] == one864["tp"],
          f"grid={gcell['tp']} vs one={one864['tp']}")
    check("3-day random_entry sl == one_coin(864) sl",
          gcell["sl"] == one864["sl"],
          f"grid={gcell['sl']} vs one={one864['sl']}")

res_meta, err = _attempt(baseline_random_entry, tmpX, ["X"], leverages=(10,),
                         horizons_days=(1, 3, 7, 14), stride=12,
                         t_lo=None, t_hi=None, holdout_start_ts=None,
                         min_qv24=1e6)
check("horizons_bars metadata run does not raise", err is None, err or "")
if err is None:
    check("result metadata horizons_bars == [288, 864, 2016, 4032] for "
          "horizons_days=(1,3,7,14)",
          res_meta["horizons_bars"] == [288, 864, 2016, 4032],
          f"got {res_meta.get('horizons_bars')!r}")


# ---------------------------------------------------------------------------
# Test: Per-coin cost model from trailing-24h QV
# ---------------------------------------------------------------------------

print("\n=== Test: baseline_one_coin uses per-coin cost from QV24 ===")

from proto.engine.baseline import baseline_one_coin, _estimate_cost

# High-volume coin: qv24 >= 30M → cost = 2*(0.0005 + 0.00015) = 0.0013
# Low-volume coin: qv24 >= 1M but < 3M → cost = 2*(0.0005 + 0.0006) = 0.0022
# Very low volume: qv24 < 1M → untradeable

# Write a high-volume coin (volume=100000, amount=8640000 = 30M/day at $300)
high_vol_dir = tempfile.mkdtemp(prefix="baseline_hv_")
_write_synthetic_bars(high_vol_dir, "HVCOIN", 5 * BARS_PER_DAY,
                       price=300.0, vol=100000.0)
bars_hv = B.load_coin(high_vol_dir, "HVCOIN")
qv24_hv = sum(bars_hv["amt"][-BARS_PER_DAY:])
cost_hv = _estimate_cost(high_vol_dir, "HVCOIN")
expected_cost_hv = DEFAULT.cost.round_trip(qv24_hv)
check("high-volume coin uses tiered cost",
      cost_hv == expected_cost_hv,
      f"got {cost_hv}, expected {expected_cost_hv}")
check("high-volume cost is 0.0013 (lowest tier)",
      abs(cost_hv - 0.0013) < 1e-12,
      f"got {cost_hv}")

# Write a low-volume coin: vol=70, price=100 → amt=7000/bar × 288 = ~2M QV24
# This falls in tier (3e6, 0.0006) → cost = 2*(0.0005 + 0.0006) = 0.0022
low_vol_dir = tempfile.mkdtemp(prefix="baseline_lv_")
_write_synthetic_bars(low_vol_dir, "LVCOIN", 5 * BARS_PER_DAY,
                       price=100.0, vol=70.0)
cost_lv = _estimate_cost(low_vol_dir, "LVCOIN")
qv24_lv = sum(B.load_coin(low_vol_dir, "LVCOIN")["amt"][-BARS_PER_DAY:])
expected_cost_lv = DEFAULT.cost.round_trip(qv24_lv)
check("low-volume coin uses higher-tier cost",
      cost_lv == expected_cost_lv,
      f"got {cost_lv}, expected {expected_cost_lv}")
check("low-volume cost > high-volume cost",
      cost_lv > cost_hv,
      f"lv={cost_lv} vs hv={cost_hv}")

# cost_frac=None is the POINT-IN-TIME mode.  On this uniform-volume fixture
# every entry's trailing-24h window sits in the same tier as the boundary,
# so the per-entry counts collapse to the coin's own single tier.
bl_hv, err_hv = _attempt(baseline_one_coin, high_vol_dir, "HVCOIN", 20.0,
                         stop_roe=2.5, target_roe=1.0,
                         horizon_bars=BARS_PER_DAY, cost_frac=None,
                         stride=12, t_lo=None, t_hi=None, min_qv24=1e6)
check("cost_frac=None per-entry mode runs on the uniform fixture",
      err_hv is None, err_hv or "")
if err_hv is None:
    check("uniform fixture: per-entry counts collapse to the coin's own tier",
          bl_hv.get("entry_cost_counts")
          == {repr(cost_hv): bl_hv["n_samples"]} and bl_hv["n_samples"] > 0,
          f"got {bl_hv.get('entry_cost_counts')!r}, n={bl_hv['n_samples']}")

# The boundary cost survives ONLY as the eligibility gate: a coin whose
# trailing-24h QV at the window boundary is below the floor returns the
# empty baseline (it is never labeled at a boundary cost anymore).
tmpdir_dry = tempfile.mkdtemp(prefix="baseline_drycoin_")
_write_synthetic_bars(tmpdir_dry, "DRYCOIN", 5 * BARS_PER_DAY,
                      price=100.0, vol=1.0)
bl_dry, err_dry = _attempt(baseline_one_coin, tmpdir_dry, "DRYCOIN", 10.0,
                           stop_roe=2.5, target_roe=1.0,
                           horizon_bars=BARS_PER_DAY, cost_frac=None,
                           stride=12, t_lo=None, t_hi=None, min_qv24=1e6)
check("untradeable-at-boundary coin: no crash, empty baseline",
      err_dry is None and bl_dry["n_samples"] == 0 and bl_dry["tp"] == 0
      and bl_dry["mean_roe"] == 0.0,
      err_dry or f"got n={bl_dry['n_samples'] if bl_dry else None}")


# ---------------------------------------------------------------------------
# Test: _filter_liquid_coins is point-in-time at t_hi, not archive end
# ---------------------------------------------------------------------------

print("\n=== Test: _filter_liquid_coins uses QV24 at t_hi, not archive end ===")

from proto.engine.baseline import _filter_liquid_coins

# Create a coin that is liquid early but dries up later (or vice versa)
# We make two coins: one liquid at the end, one liquid only early
tmpdir3 = tempfile.mkdtemp(prefix="baseline_filter_")

# Coin A: high volume early, low volume at the end
n_bars_a = 5 * BARS_PER_DAY
_write_synthetic_bars(tmpdir3, "COINA", n_bars_a, price=100.0, vol=10000.0)
# Overwrite the last 2 days with zero volume to make it illiquid at the end
path_a = os.path.join(tmpdir3, "COINA.csv.gz")
with gzip.open(path_a, "wt") as fh:
    fh.write("ts,o,h,l,c,vol,amount\n")
    for i in range(n_bars_a):
        ts = BASE_TS + i * BAR_S
        price = 100.0
        # First 3 days: high vol (100000); last 2 days: zero vol
        vol = 100000.0 if i < 3 * BARS_PER_DAY else 0.0
        amt = vol * price
        fh.write(f"{ts},{price},{price*1.001},{price*0.999},{price},{vol},{amt}\n")

# Coin B: low volume early, high volume at the end
path_b = os.path.join(tmpdir3, "COINB.csv.gz")
with gzip.open(path_b, "wt") as fh:
    fh.write("ts,o,h,l,c,vol,amount\n")
    for i in range(n_bars_a):
        ts = BASE_TS + i * BAR_S
        price = 100.0
        # First 3 days: zero vol; last 2 days: high vol
        vol = 0.0 if i < 3 * BARS_PER_DAY else 100000.0
        amt = vol * price
        fh.write(f"{ts},{price},{price*1.001},{price*0.999},{price},{vol},{amt}\n")

# Without t_hi (full archive): CoinA should fail (tail has zero vol),
# CoinB should pass (tail has high vol)
coins_full = _filter_liquid_coins(tmpdir3, min_qv24=1e6)
check("no t_hi: COINB liquid at archive end", "COINB" in coins_full)
check("no t_hi: COINA not liquid at archive end", "COINA" not in coins_full,
      f"got coins={coins_full}")

# With t_hi = end of day 3 (point-in-time): CoinA should pass (liquid early),
# CoinB should fail (not liquid at that point)
t_hi_day3 = BASE_TS + 3 * BARS_PER_DAY * BAR_S
coins_pit = _filter_liquid_coins(tmpdir3, min_qv24=1e6, t_hi=t_hi_day3)
check("with t_hi: COINA passes (liquid at t_hi)", "COINA" in coins_pit,
      f"got coins={coins_pit}")
check("with t_hi: COINB excluded (not liquid at t_hi)", "COINB" not in coins_pit,
      f"got coins={coins_pit}")


# ---------------------------------------------------------------------------
# Test: _filter_liquid_coins is file-order-independent (sorted boundary gate)
# ---------------------------------------------------------------------------

print("\n=== Test: _filter_liquid_coins is file-order-independent ===")

# Bar files are NOT time-ordered: the collector appends backfill pages after
# the forward rows (load_coin sorts by ts).  The liquidity gate must evaluate
# the trailing-24h window AT THE t_hi BOUNDARY of the SORTED bars — exactly
# what _estimate_cost does — never the last rows of the file.

def _write_unsorted_liquidity_fixture(data_dir):
    """Two coins whose FILE row order is: late-window rows first, then an
    appended early-window 'backfill' block.  Volumes are mirrored:
      COIN-A: late window LOW volume, early backfill HIGH volume
      COIN-B: late window HIGH volume, early backfill LOW volume
    At a t_hi inside the late window the boundary gate must therefore EXCLUDE
    COIN-A and INCLUDE COIN-B regardless of file order.  Returns t_hi."""
    os.makedirs(data_dir, exist_ok=True)
    n_early = BARS_PER_DAY               # day 0 (the appended backfill block)
    n_late = 2 * BARS_PER_DAY            # days 1-2 (the forward rows)
    t_hi = BASE_TS + 2 * DAY_S           # boundary inside the late window
    hi_vol, lo_vol = 100000.0, 1.0
    for coin, late_vol, early_vol in (("COIN-A", lo_vol, hi_vol),
                                      ("COIN-B", hi_vol, lo_vol)):
        rows = ["ts,o,h,l,c,vol,amount"]
        # Forward rows: the late window, volume per coin
        for i in range(n_early, n_early + n_late):
            ts = BASE_TS + i * BAR_S
            p = 100.0
            rows.append(f"{ts},{p},{p*1.001},{p*0.999},{p},{late_vol},{late_vol*p}")
        # Appended 'backfill' block: the early window, mirrored volume
        for i in range(n_early):
            ts = BASE_TS + i * BAR_S
            p = 100.0
            rows.append(f"{ts},{p},{p*1.001},{p*0.999},{p},{early_vol},{early_vol*p}")
        with gzip.open(os.path.join(data_dir, f"{coin}.csv.gz"), "wt") as fh:
            fh.write("\n".join(rows) + "\n")
    return t_hi


tmpdir_uns = tempfile.mkdtemp(prefix="baseline_unsorted_")
t_hi_uns = _write_unsorted_liquidity_fixture(tmpdir_uns)

coins_gate = sorted(_filter_liquid_coins(tmpdir_uns, min_qv24=1e6, t_hi=t_hi_uns))
check("unsorted file: COIN-A excluded (its SORTED boundary window is "
      "low-volume; the old file-order scan picked up the high-volume early "
      "block)", "COIN-A" not in coins_gate, f"got {coins_gate}")
check("unsorted file: COIN-B included (its SORTED boundary window is "
      "high-volume)", "COIN-B" in coins_gate, f"got {coins_gate}")

expected_gate = sorted(c for c in B.list_coins(tmpdir_uns)
                       if _estimate_cost(tmpdir_uns, c, t_hi_uns) is not None)
check("gate equivalence: _filter_liquid_coins == {_estimate_cost(c, t_hi) "
      "is not None}", coins_gate == expected_gate,
      f"filter={coins_gate} vs gate={expected_gate}")
check("gate equivalence is non-trivial (exactly COIN-B)",
      expected_gate == ["COIN-B"], f"gate={expected_gate}")

check("max_coins cap still applies after the gate",
      _filter_liquid_coins(tmpdir_uns, min_qv24=1e6, max_coins=1,
                           t_hi=t_hi_uns) == ["COIN-B"],
      f"got {_filter_liquid_coins(tmpdir_uns, min_qv24=1e6, max_coins=1, t_hi=t_hi_uns)}")


# ---------------------------------------------------------------------------
# Test: deterministic stride sampling, both sides, outcome-independent
# ---------------------------------------------------------------------------

print("\n=== Test: stride sampling is deterministic, documented ===")

# The baseline samples a fixed GLOBAL grid: every `stride`-th bar of every
# contiguous run, both sides labeled per entry, outcome-independent (which
# entries are sampled never depends on any outcome).  Entries are NOT random.
# The plan's hash-based `random_baseline` (sha256(seed|coin) time/side, plan
# §4) is a separate Phase 6 mechanism, not this table.

with tempfile.TemporaryDirectory() as tmpdir4:
    _write_trending_bars(tmpdir4, "DOC", 5 * BARS_PER_DAY)
    # Same seed, same data → same result (deterministic)
    r1 = baseline_random_entry(tmpdir4, ["DOC"], leverages=(10,),
                                horizons_days=(1,), seed=1, stride=12)
    r2 = baseline_random_entry(tmpdir4, ["DOC"], leverages=(10,),
                                horizons_days=(1,), seed=1, stride=12)
    check("stride sampling is deterministic (same seed → same result)",
          r1["per_coin"]["DOC"][10][1] == r2["per_coin"]["DOC"][10][1])
    check("pass_rate is TP-first among terminal outcomes",
          abs(r1["per_coin"]["DOC"][10][1]["pass_rate"] -
              r1["per_coin"]["DOC"][10][1]["tp_rate"]) < 1e-12)


# ---------------------------------------------------------------------------
# Test: baseline_random_entry passes t_lo/t_hi through to per-coin
# ---------------------------------------------------------------------------

print("\n=== Test: baseline_random_entry respects t_lo/t_hi boundaries ===")

with tempfile.TemporaryDirectory() as tmpdir5:
    _write_trending_bars(tmpdir5, "WINDCOIN", 80 * BARS_PER_DAY, slope=0.0001)
    bars_wind = B.load_coin(tmpdir5, "WINDCOIN")
    n_bars_wind = len(bars_wind["ts"])
    check("archive has enough bars", n_bars_wind >= 80 * BARS_PER_DAY)

    # Full window: should sample across all bars
    result_full = baseline_random_entry(tmpdir5, ["WINDCOIN"], leverages=(10,),
                                         horizons_days=(1,), stride=12,
                                         t_lo=None, t_hi=None, min_qv24=1e6)

    # Explicit t_hi at the day-61 boundary (pass-through check)
    dev_end_ts = BASE_TS + int(61 * BARS_PER_DAY * BAR_S)
    result_pre = baseline_random_entry(tmpdir5, ["WINDCOIN"], leverages=(10,),
                                        horizons_days=(1,), stride=12,
                                        t_lo=None, t_hi=dev_end_ts, min_qv24=1e6)

    check("pre-holdout has fewer or equal samples than full",
          result_pre["per_coin"]["WINDCOIN"][10][1]["n_samples"]
          <= result_full["per_coin"]["WINDCOIN"][10][1]["n_samples"],
          f"pre={result_pre['per_coin']['WINDCOIN'][10][1]['n_samples']} "
          f"vs full={result_full['per_coin']['WINDCOIN'][10][1]['n_samples']}")
    check("pre-holdout pass_rate is computed (not error)",
          "pass_rate" in result_pre["per_coin"]["WINDCOIN"][10][1])


# ---------------------------------------------------------------------------
# Summary
#
# Sampling note: the baseline samples a fixed GLOBAL stride grid (every
# `stride`-th bar, both sides labeled, outcome-independent).  It is a valid
# hindsight-free baseline.  The plan's hash-based `random_baseline`
# (sha256(seed|coin) for time, hash parity for side) is a DIFFERENT mechanism,
# scheduled for Phase 6's decision.py/evidence.py — it is not this table.
# ---------------------------------------------------------------------------

print(f"\n=== {'ALL PASS' if not FAILURES else 'FAIL: ' + str(len(FAILURES)) + ' failures'}: {len(FAILURES)} failures ===")
if FAILURES:
    print("Failures:", FAILURES)
    sys.exit(1)
print("All tests passed.")
sys.exit(0)
