"""Read-only baseline calibration for deterministic stride-grid pass rates.

Reusable by:
  - `proto.engine baseline` (CLI)
  - tests that compare against `proto/quant/roe.empirical_rates`

The baseline measures what a DRIFTLESS, outcome-independent entry would
achieve.  Entries are NOT random: every `stride`-th bar of every contiguous
run on a fixed GLOBAL grid (no look-ahead) is an entry, and BOTH sides are
labeled with the contract geometry; the outcome of one entry never changes
which entries are sampled.  The "pass" metric is TP-first — exactly the
benchmark's pass definition.  Pass rate = TP / total outcomes.

This table is NOT the plan's hash-based `random_baseline` (sha256(seed|coin)
time/side selection, plan §4); that is a separate Phase 6 mechanism — the
matched baseline of the plan's §8 evidence rule.  Conversely, THIS stride
grid is exactly the plan's Phase 1 baseline: the plan's §2.2 "random
entries" table and its Phase 1 command both route through
`proto.quant.roe.empirical_rates`, which is this same stride-grid,
both-sides measurement ("random entry" there means outcome-independent
no-skill entry, not hash-based selection).  No estimator choice exists at
Phase 1.

Costs are point-in-time (plan §5.1): every sampled entry is labeled at the
round-trip cost of its own trailing-24h quote volume AT THE ENTRY BAR, via
`_rates_point_in_time` — a bar-for-bar mirror of
`proto.quant.roe.empirical_rates`, which cannot itself express per-entry
costs (it is protected).  The per-coin boundary cost (`_estimate_cost` at
the window edge) is used ONLY to select the universe of tradeable coins,
never as a labeling cost; a fixed-cost `cost_frac` override remains
available for sensitivity runs and is labeled as such in the results.

This module NEVER writes to `data/` or creates any database file.  It reads
bars read-only via `proto/quant/bars.load_coin` — a documented Phase 1-only
exception to the plan's research-mirror rule (the plan's own Phase 1
command calibrates directly against the archive before any research
database exists; Phase 2+ engine code reads the mirror only).  The planned
snapshot-input migration is `proto.research.sync.load_snapshot_bars`,
which this module does not use while the research DB remains uncreated.
"""
import math
import os
import random

from proto.quant import bars as B
from proto.quant import barrier as BAR
from proto.quant import roe as R
from proto.quant.config import DEFAULT, BARS_PER_DAY, BAR_S


def _compute_window_bounds(data_dir, window):
    """Window boundaries anchored at BTC's first bar (the repo's convention,
    proto/quant/__main__.py). pre-holdout = dev + validation; the holdout
    guard is applied per (leverage, horizon) cell by baseline_random_entry.

    Returns a dict:
      {"window": window, "anchor_coin": "BTC", "anchor_first_ts": int,
       "t_min": int, "n_days": float, "t_lo": int | None,
       "val_end_ts": int | None, "holdout_start_ts": int | None,
       "boundaries_in_archive": bool}

    "pre-holdout": t_lo / val_end_ts / holdout_start_ts from
      DEFAULT.splits.bounds(n_days); boundaries_in_archive says whether the
      validation end lies within the archive (on short archives it does not,
      and the window degenerates to the whole archive).
    "full": the three timestamps are None (entire archive, holdout included).
    """
    btc = B.load_coin(data_dir, "BTC")
    if not btc or not btc["ts"]:
        raise ValueError("BTC bars required for the archive span")
    t_min = btc["ts"][0]
    n_days = (btc["ts"][-1] - t_min) / 86400.0
    anchor = {"window": window, "anchor_coin": "BTC",
              "anchor_first_ts": int(t_min), "t_min": int(t_min),
              "n_days": n_days}
    if window == "full":
        return dict(anchor, t_lo=None, val_end_ts=None, holdout_start_ts=None,
                    boundaries_in_archive=False)
    if window != "pre-holdout":
        raise ValueError(f"unknown window: {window!r}")
    bnd = DEFAULT.splits.bounds(n_days)
    t_lo = t_min + int(bnd["dev"][0] * 86400)
    val_end_ts = t_min + int(bnd["val"][1] * 86400)
    holdout_start_ts = t_min + int(bnd["holdout"][0] * 86400)
    boundaries_in_archive = val_end_ts <= btc["ts"][-1]
    return dict(anchor, t_lo=t_lo, val_end_ts=val_end_ts,
                holdout_start_ts=holdout_start_ts,
                boundaries_in_archive=boundaries_in_archive)


def _estimate_cost(data_dir, coin, t_hi=None, min_qv24=None):
    """Compute per-coin round-trip cost from trailing-24h quote volume.

    Mirrors `proto/quant/roe.coin_report` (ro.e:288-313): takes the trailing
    24h QV at the last bar of the measurement window (or end of archive if
    t_hi is None), then applies DEFAULT.cost.round_trip(qv24).  Bars are
    SORTED by timestamp (bar files are not file-ordered: the collector
    appends backfill pages after the forward rows), and the 24h window is
    taken at the boundary of the sorted bars — never at the file tail.

    min_qv24 overrides the liquidity floor for the gate (default:
    DEFAULT.cost.min_qv24); the cost model's own lowest tier is a hard floor.

    Returns None if the coin is untradeable (QV below the floor or the 24h
    window spans a timestamp gap).
    """
    if min_qv24 is None:
        min_qv24 = DEFAULT.cost.min_qv24
    bars = B.load_coin(data_dir, coin)
    if not bars or len(bars["ts"]) < BARS_PER_DAY + 1:
        return None

    ts = bars["ts"]
    amt = bars["amt"]

    # Find the last bar index within the window
    if t_hi is not None:
        sel = [i for i, t in enumerate(ts) if t < t_hi]
        if not sel:
            return None
        last = sel[-1]
    else:
        last = len(ts) - 1

    # The 24h volume window at the end must be CONTIGUOUS: a window that
    # reaches back over a gap does not describe the coin's liquidity at
    # that time. Mirror coin_report's contiguity check (ro.e:306-309).
    k = last
    while k > 0 and ts[k] - ts[k - 1] == BAR_S and (last - k) < BARS_PER_DAY - 1:
        k -= 1
    if last - k + 1 < BARS_PER_DAY:
        return None  # untradeable: 24h window spans a gap

    qv24 = sum(amt[k:last + 1])
    if qv24 < min_qv24:
        return None  # below the caller's liquidity floor
    return DEFAULT.cost.round_trip(qv24)  # None below the cost model's own floor


def _empty_baseline(coin):
    return {"coin": coin, "n_samples": 0, "tp": 0, "sl": 0, "time": 0,
            "tp_rate": 0.0, "sl_rate": 0.0, "time_rate": 0.0,
            "pass_rate": 0.0, "pass_prob": 0.0, "mean_roe": 0.0,
            "roe_list": [], "plan": None}


_COST_APPLICATION_PIT = ("point-in-time at each sampled entry "
                         "(trailing-24h QV at the entry bar)")
_COST_APPLICATION_FIXED = "fixed (cost_frac override)"


def _rates_point_in_time(bars, stop_roe, leverage, target_roe, horizon,
                         stride=12, t_lo=None, t_hi=None, min_qv24=None,
                         cost_model=None, cost_frac=None):
    """empirical_rates with the round-trip cost tiered PER ENTRY (plan §5.1).

    Mirrors `proto.quant.roe.empirical_rates` (roe.py:170-251) bar for bar —
    same `contiguous_runs`, same global stride phase
    (`start = run_a + BARS_PER_DAY; start += (BARS_PER_DAY - start) % stride`),
    same `[t_lo, t_hi)` entry window (the per-cell holdout cutoff is computed
    by the CALLER and passed in as t_hi), same trailing-24h QV gate on prefix
    sums (the window is contiguous inside the run by construction), same
    `make_path`/`label_path` side-exact geometry — with ONE difference: each
    entry's round-trip cost is `cost_model.round_trip(trailing-24h QV at the
    entry bar)`, so the plan geometry (a/b) and the net ROE of every outcome
    are computed at the entry's OWN cost tier, not at one window-boundary
    cost for the whole coin (which empirical_rates cannot express).

    cost_frac: when given, a FIXED cost for every entry — the sensitivity
    mode; `roe.empirical_rates` itself is the reference for that mode.
    cost_model defaults to DEFAULT.cost.

    Returns empirical_rates' dict shape (n/tp/sl/time/roe list/entries and
    the audit trails) plus:
      "cost_counts": {repr(cost): count of labeled outcomes (both sides)}
      "cost_application": the mode string, so a fixed-cost run can never be
      presented as the plan's point-in-time result

    An entry whose cost tier yields an infeasible geometry (target_plan's
    ValueError, or the a >= 1.0 / b >= 1.0 guard empirical_rates raises on)
    is SKIPPED — the per-entry mirror of that guard — never fatal.
    """
    cm = DEFAULT.cost if cost_model is None else cost_model
    min_qv24 = DEFAULT.cost.min_qv24 if min_qv24 is None else min_qv24
    if stride < 1:
        raise ValueError(f"stride must be at least 1, got {stride}")
    o, h, l, c, amt, ts = (bars[k] for k in ("o", "h", "l", "c", "amt", "ts"))
    cs = [0.0]
    for x in amt:
        cs.append(cs[-1] + x)
    fixed = cost_frac is not None
    # One geometry per DISTINCT per-entry cost tier (the plan depends on the
    # cost, so per-entry tiering means per-tier geometry).  None = infeasible
    # tier: skip its entries (the per-entry mirror of empirical_rates'
    # ValueError guard).
    plans = {}
    out = {"n": 0, "entries": 0, "tp": 0, "sl": 0, "time": 0, "roe_sum": 0.0,
           "days": [], "tp_flags": [], "term_days": [], "roe": [], "touch": 0,
           "entry_idx": [],
           "by_side": {1: [0, 0, 0], -1: [0, 0, 0]},
           "cost_counts": {},
           "cost_application": (_COST_APPLICATION_FIXED if fixed
                                else _COST_APPLICATION_PIT)}
    for (run_a, run_z) in B.contiguous_runs(ts):
        start = run_a + BARS_PER_DAY
        start += (BARS_PER_DAY - start) % stride
        for i in range(start, run_z - horizon, stride):
            if (t_lo is not None and ts[i] < t_lo) or (t_hi is not None and ts[i] >= t_hi):
                continue
            qv24_e = cs[i + 1] - cs[i + 1 - BARS_PER_DAY]
            if qv24_e < min_qv24:
                continue
            cost_e = cost_frac if fixed else cm.round_trip(qv24_e)
            if cost_e is None:
                continue  # below the cost model's own floor (belt and braces)
            if cost_e not in plans:
                try:
                    plan_e = R.target_plan(stop_roe, leverage, cost_e,
                                           target_roe)
                except ValueError:
                    plan_e = None  # e.g. stop inside the round-trip cost
                if (plan_e is None or plan_e["target_move"] >= 1.0
                        or plan_e["stop_move"] >= 1.0):
                    plans[cost_e] = None
                else:
                    a, b = plan_e["target_move"], plan_e["stop_move"]
                    # Side-exact geometry exactly as empirical_rates builds
                    # it, at THIS entry's tier.
                    dd_side = {1: -math.log(1.0 - b), -1: math.log(1.0 + b)}
                    r_tp_side = {1: math.log(1.0 + a) / dd_side[1],
                                 -1: -math.log(1.0 - a) / dd_side[-1]}
                    plans[cost_e] = (a, dd_side, r_tp_side,
                                     plan_e["leverage"])
            geom = plans[cost_e]
            if geom is None:
                continue
            a, dd_side, r_tp_side, L = geom
            path = BAR.make_path(o, h, l, c, i, horizon, run_end=run_z)
            if path is None:
                continue
            day = ts[i] // 86400
            e = path.entry
            out["entries"] += 1
            out["entry_idx"].append(i)
            for side in (1, -1):
                g, kind, _ = BAR.label_path(path, side, dd_side[side], horizon,
                                          r_tp_side[side])
                move = (math.exp(g) - 1.0) if side > 0 else (1.0 - math.exp(-g))
                roe = L * (move - cost_e)  # the ENTRY's cost, not a fixed one
                if side > 0:
                    reached = path.rmax[-1] >= e * (1.0 + a)
                else:
                    reached = path.nrmin[-1] >= -e * (1.0 - a)
                if reached:
                    out["touch"] += 1
                out["n"] += 1
                out["roe_sum"] += roe
                out["roe"].append(roe)
                out["days"].append(day)
                if kind == "TP":
                    out["tp"] += 1
                    out["by_side"][side][0] += 1
                elif kind == "SL":
                    out["sl"] += 1
                    out["by_side"][side][1] += 1
                else:
                    out["time"] += 1
                    out["by_side"][side][2] += 1
                if kind != "TIME":
                    out["tp_flags"].append(1.0 if kind == "TP" else 0.0)
                    out["term_days"].append(day)
            out["cost_counts"][repr(cost_e)] = \
                out["cost_counts"].get(repr(cost_e), 0) + 2  # both sides
    return out


def baseline_one_coin(data_dir, coin, leverage, stop_roe=2.5, target_roe=1.0,
                      horizon_bars=None, cost_frac=None, stride=12,
                      t_lo=None, t_hi=None, min_qv24=None,
                      holdout_start_ts=None):
    """Stride-grid baseline for one coin (entries are NOT random).

    Entries sit on a fixed GLOBAL grid (every `stride`-th bar of every
    contiguous run); BOTH sides are labeled per entry and the outcome of one
    entry never changes the grid.  The plan's hash-based `random_baseline`
    (sha256(seed|coin), plan §4) is a separate Phase 6 mechanism.

    Returns dict with:
      n_samples, tp, sl, time, tp_rate, sl_rate, time_rate,
      pass_rate (= tp_rate), mean_roe, roe_list, pass_prob (= tp_rate).

    Reuses `roe.target_plan` and mirrors `roe.empirical_rates` exactly (via
    `_rates_point_in_time`, also for the fixed-cost mode), so the numbers are
    guaranteed to agree with the research pipeline.

    COSTS ARE POINT-IN-TIME (plan §5.1): with cost_frac=None (the default)
    every sampled entry is labeled — via `_rates_point_in_time` — at the
    round-trip cost of its own trailing-24h QV at the ENTRY BAR, so the plan
    geometry (a/b) and the net ROE are per-entry; there is no single plan
    (result "plan" is None) and "entry_cost_counts" discloses the tier mix.
    The window-boundary `_estimate_cost` value survives ONLY as the
    ELIGIBILITY gate: a coin untradeable at the boundary (QV below floor or
    a gapped 24h window) returns an empty baseline; the boundary cost is
    never a labeling cost.  An explicit cost_frac switches to a FIXED-cost
    sensitivity run (empirical_rates at that one cost), labeled
    "fixed (cost_frac override)" — never presentable as the point-in-time
    result.

    holdout_start_ts: when set, the entry cutoff becomes
    min(t_hi, holdout_start_ts − horizon_bars*BAR_S) so no labeled bar starts
    at or after the holdout (label windows cover bars i+1..i+horizon on a
    gap-free run).  That per-cell cutoff (t_hi_cell) is what the measurement
    loop receives; the eligibility gate keeps using the window t_hi.
    """
    if horizon_bars is None:
        horizon_bars = BARS_PER_DAY
    if min_qv24 is None:
        min_qv24 = DEFAULT.cost.min_qv24

    bars = B.load_coin(data_dir, coin)
    if not bars or len(bars["ts"]) < 3 * BARS_PER_DAY:
        return _empty_baseline(coin)

    # Per-cell holdout guard: an entry at ts[i] < holdout_start − T*BAR_S can
    # never open a bar at/after the holdout start.
    t_hi_cell = t_hi
    if holdout_start_ts is not None:
        limit = holdout_start_ts - horizon_bars * BAR_S
        t_hi_cell = limit if t_hi is None else min(t_hi, limit)

    if cost_frac is None:
        # ELIGIBILITY GATE ONLY (selection semantics): tradeability at the
        # window boundary decides whether the coin is measured at all; the
        # boundary cost is never used for labeling.
        if _estimate_cost(data_dir, coin, t_hi, min_qv24) is None:
            return _empty_baseline(coin)  # untradeable: below floor or gap
    else:
        # Fixed-cost sensitivity mode: validate the geometry up front so an
        # infeasible override raises exactly as it did before.
        R.target_plan(stop_roe, leverage, cost_frac, target_roe)

    raw = _rates_point_in_time(bars, stop_roe, leverage, target_roe,
                               horizon_bars, stride=stride, t_lo=t_lo,
                               t_hi=t_hi_cell, min_qv24=min_qv24,
                               cost_frac=cost_frac)

    n = raw["n"]
    if n == 0:
        return dict(_empty_baseline(coin),
                    cost_application=raw["cost_application"],
                    entry_cost_counts=raw["cost_counts"])

    tp, sl, time = raw["tp"], raw["sl"], raw["time"]
    roe_list = raw["roe"]

    plan = None
    if cost_frac is not None:
        plan = dict(R.target_plan(stop_roe, leverage, cost_frac, target_roe),
                    cost_frac=cost_frac)

    return {
        "coin": coin,
        "n_samples": n,
        "tp": tp, "sl": sl, "time": time,
        "tp_rate": tp / n,
        "sl_rate": sl / n,
        "time_rate": time / n,
        "pass_rate": tp / n,      # pass = TP-first
        "pass_prob": tp / n,     # same thing in the baseline
        "mean_roe": sum(roe_list) / len(roe_list) if roe_list else 0.0,
        "roe_list": roe_list,
        "plan": plan,  # None in per-entry mode: geometry is per cost tier
        "entry_cost_counts": raw["cost_counts"],
        "cost_application": raw["cost_application"],
    }


def simulate_pass_counts(n, p_pass, K=10000, seed=1):
    """Simulate K draws from Binomial(n, p_pass), return summary statistics.

    Uses exact binomial sampling.  Returns:
      mean, lo95, hi95, pmf: {k: {"sim_prob": float, "exact_pmf": float, ...}}
    """
    if not (0.0 <= p_pass <= 1.0):
        raise ValueError(f"p_pass must be in [0, 1], got {p_pass}")
    if not isinstance(n, int) or n < 1:
        raise ValueError(f"n must be a positive integer, got {n!r}")

    rng = random.Random(seed)
    counts = {}
    total = 0
    for _ in range(K):
        k = sum(1 for _ in range(n) if rng.random() < p_pass)
        counts[k] = counts.get(k, 0) + 1
        total += 1

    # Exact binomial PMF for comparison
    pmf = {}
    for k in range(n + 1):
        sim_prob = counts.get(k, 0) / total if total else 0.0
        exact_pmf = math.comb(n, k) * (p_pass ** k) * ((1 - p_pass) ** (n - k))
        pmf[k] = {
            "sim_prob": sim_prob,
            "exact_pmf": exact_pmf,
            "exact_count": exact_pmf * total,
        }

    # Percentiles from the simulation
    k_vals = sorted(counts.keys())
    cumulative = 0
    lo95 = 0
    hi95 = n
    for k in k_vals:
        cumulative += counts[k]
        if cumulative >= 0.025 * total and lo95 == 0:
            lo95 = k
        if cumulative >= 0.975 * total:
            hi95 = k
            break

    mean = sum(k * c for k, c in counts.items()) / total if total else 0.0

    return {
        "n": n,
        "p_pass": p_pass,
        "simulations": K,
        "seed": seed,
        "mean": mean,
        "lo95": lo95,
        "hi95": hi95,
        "pmf": {str(k): v for k, v in sorted(pmf.items())},
    }


def _filter_liquid_coins(data_dir, min_qv24, max_coins=None, t_hi=None):
    """Return coins whose trailing-24h quote volume clears the floor.

    The gate is EXACTLY `_estimate_cost(data_dir, coin, t_hi) is not None`:
    the trailing-24h QV of the SORTED bars at the t_hi boundary (or archive
    end), with the contiguous-24h tradeable gate.  Bar files are not
    file-ordered (the collector appends backfill pages after the forward
    rows), so the boundary window must come from the timestamp-sorted bars,
    never from the last rows of the file.

    If t_hi is given (pre-holdout window), the gate reads the trailing 24h
    ending at the last bar before t_hi — NOT at the end of the archive. This
    prevents holdout-period data from influencing coin selection.

    `min_qv24` is the liquidity floor (defaulting to DEFAULT.cost.min_qv24);
    `max_coins` caps the result and is applied AFTER the gate, in sorted coin
    order.
    """
    if min_qv24 is None:
        min_qv24 = DEFAULT.cost.min_qv24
    all_coins = sorted(f[:-7] for f in os.listdir(data_dir)
                       if f.endswith(".csv.gz"))
    eligible = []
    for coin in all_coins:
        if _estimate_cost(data_dir, coin, t_hi, min_qv24) is None:
            continue  # untradeable at the boundary (floor or 24h-window gap)
        eligible.append(coin)
        if max_coins and len(eligible) >= max_coins:
            break
    return eligible


def baseline_random_entry(data_dir, coins=None, leverages=(10, 20, 25),
                          horizons_days=(1, 3, 7, 14), seed=1,
                          stride=12, t_lo=None, t_hi=None,
                          holdout_start_ts=None, min_qv24=None,
                          stop_roe=2.5, target_roe=1.0, max_coins=None,
                          cost_frac=None):
    """Full baseline table across leverages and horizons (entries NOT random).

    Entries sit on a fixed GLOBAL stride grid; BOTH sides are labeled per
    entry, outcome-independent.  The plan's hash-based `random_baseline`
    (sha256(seed|coin), plan §4) is a separate Phase 6 mechanism, not this
    table.

    COSTS (plan §5.1): measurement is POINT-IN-TIME per entry — every
    sampled entry is labeled at the round-trip cost of its own trailing-24h
    QV at the entry bar (`_rates_point_in_time`), for every (L, T) cell.
    Universe SELECTION is unchanged and stays at the window boundary:
    `_filter_liquid_coins` / `_estimate_cost` at t_hi pick the coins, and
    `cost_frac_per_coin` reports those BOUNDARY costs — they are selection
    costs only (see `selection_cost_note` in the result), never labeling
    costs.  An explicit `cost_frac` switches every cell to a FIXED-cost
    sensitivity run, labeled "fixed (cost_frac override)".

    holdout_start_ts: when set, each (leverage, horizon) cell gets the
    effective entry cutoff min(t_hi, holdout_start_ts − T_bars*BAR_S), so no
    labeled bar starts at or after the holdout.  Universe selection keeps
    using the WINDOW t_hi, never the per-cell cutoff.

    Returns a JSON-serializable dict with:
      - per_coin: {coin: {L: {T_days: baseline_one_coin(...)}}}
      - pooled:   {L: {T_days: {n, tp, sl, time, pass_rate, mean_roe, ...}}}
      - sim_n20/50/100: binomial pass-count distributions at each pooled rate
      - cost_frac_per_coin: BOUNDARY costs (selection only) + the
        selection_cost_note disclosure; cost_application: the measurement
        mode; per-coin entry_cost_counts live in each measured cell

    Does NOT write anything — pure read of data_dir.
    """
    if min_qv24 is None:
        min_qv24 = DEFAULT.cost.min_qv24
    horizons_bars = [d * BARS_PER_DAY for d in horizons_days]

    # Universe SELECTION (unchanged): the boundary trailing-24h gate picks
    # the coins and their boundary costs.  These costs never label an entry.
    coin_costs = {}
    if coins is None:
        coins = _filter_liquid_coins(data_dir, min_qv24, max_coins, t_hi)
    for coin in coins:
        coin_costs[coin] = _estimate_cost(data_dir, coin, t_hi)
    # Filter out coins that are untradeable (QV below floor or gap in 24h window)
    coins = [c for c in coins if coin_costs.get(c) is not None]

    # Load each coin's bars once, then measure all (L, T) combinations.
    per_coin = {}
    for coin in coins:
        bars = B.load_coin(data_dir, coin)
        per_coin[coin] = {}
        if not bars or len(bars["ts"]) < 3 * BARS_PER_DAY:
            for L in leverages:
                per_coin[coin][L] = {}
                for T_days in horizons_days:
                    per_coin[coin][L][T_days] = _empty_baseline(coin)
            continue

        for L in leverages:
            per_coin[coin][L] = {}
            for T_bars, T_days in zip(horizons_bars, horizons_days):
                # Per-cell holdout guard: an entry at ts[i] <
                # holdout_start − T_bars*BAR_S never opens a bar at/after the
                # holdout start (label windows cover bars i+1..i+T on a
                # gap-free run).  min() ignores None entries; if both are
                # None the cutoff stays None.
                t_hi_cell = t_hi
                if holdout_start_ts is not None:
                    limit = holdout_start_ts - T_bars * BAR_S
                    t_hi_cell = limit if t_hi is None else min(t_hi, limit)
                try:
                    raw = _rates_point_in_time(
                        bars, stop_roe, float(L), target_roe, T_bars,
                        stride=stride, t_lo=t_lo, t_hi=t_hi_cell,
                        min_qv24=min_qv24, cost_frac=cost_frac)
                except ValueError:
                    per_coin[coin][L][T_days] = _empty_baseline(coin)
                    continue
                n = raw["n"]
                if n == 0:
                    per_coin[coin][L][T_days] = dict(
                        _empty_baseline(coin),
                        cost_application=raw["cost_application"],
                        entry_cost_counts=raw["cost_counts"])
                    continue
                tp, sl, tm = raw["tp"], raw["sl"], raw["time"]
                roe_list = raw["roe"]
                plan = None
                if cost_frac is not None:
                    plan = dict(R.target_plan(stop_roe, float(L), cost_frac,
                                              target_roe),
                                cost_frac=cost_frac)
                per_coin[coin][L][T_days] = {
                    "coin": coin,
                    "n_samples": n,
                    "tp": tp, "sl": sl, "time": tm,
                    "tp_rate": tp / n,
                    "sl_rate": sl / n,
                    "time_rate": tm / n,
                    "pass_rate": tp / n,
                    "pass_prob": tp / n,
                    "mean_roe": sum(roe_list) / len(roe_list) if roe_list else 0.0,
                    "roe_list": roe_list,
                    "plan": plan,  # None in per-entry mode (per-tier geometry)
                    "entry_cost_counts": raw["cost_counts"],
                    "cost_application": raw["cost_application"],
                }

    # Pooled results (sample-weighted across coins)
    pooled = {}
    for L in leverages:
        pooled[L] = {}
        for T_bars, T_days in zip(horizons_bars, horizons_days):
            tp_total = sl_total = time_total = n_total = 0
            roe_sum = 0.0
            for coin in coins:
                r = per_coin[coin][L][T_days]
                if r["n_samples"] > 0:
                    tp_total += r["tp"]
                    sl_total += r["sl"]
                    time_total += r["time"]
                    n_total += r["n_samples"]
                    roe_sum += r["mean_roe"] * r["n_samples"]
            p_pass = tp_total / n_total if n_total > 0 else 0.0
            pooled[L][T_days] = {
                "n": n_total,
                "tp": tp_total, "sl": sl_total, "time": time_total,
                "pass_rate": p_pass,
                "mean_roe": roe_sum / n_total if n_total else 0.0,
            }

    # Simulated pass-count distributions for each pooled (L, T)
    for L in leverages:
        for T_days in horizons_days:
            p = pooled[L][T_days]["pass_rate"]
            for n in (20, 50, 100):
                if p > 0 and p < 1:
                    sim = simulate_pass_counts(n, p, K=10000, seed=seed)
                    threshold = math.ceil(0.75 * n)
                    p_above = sum(sim["pmf"][str(k)]["exact_pmf"]
                                  for k in range(threshold, n + 1))
                    pooled[L][T_days][f"sim_n{n}"] = {
                        "threshold": threshold,
                        "p_reach_threshold": p_above,
                        "mean": sim["mean"],
                        "lo95": sim["lo95"],
                        "hi95": sim["hi95"],
                    }

    return {
        "coins": coins,
        "leverages": list(leverages),
        "horizons_days": list(horizons_days),
        "horizons_bars": horizons_bars,
        "stride": stride,
        "seed": seed,
        "cost_frac": coin_costs.get(coins[0]) if coins else None,
        "cost_frac_per_coin": coin_costs,
        "selection_cost_note": ("cost_frac_per_coin is the boundary cost used "
                                "ONLY for universe selection; measurement "
                                "costs are point-in-time per entry"),
        "cost_application": (_COST_APPLICATION_FIXED if cost_frac is not None
                             else _COST_APPLICATION_PIT),
        "per_coin": per_coin,
        "pooled": pooled,
    }


def _format_table(result):
    """Render a readable table from the baseline result."""
    lines = []
    lines.append("Baseline pass rates — deterministic stride-grid entries (both sides)")
    wb = result.get("window_bounds")
    if isinstance(wb, dict):
        if wb.get("window") == "full":
            lines.append("FULL ARCHIVE — includes the sealed holdout; "
                         "calibration/diagnostic only, never evidence")
        else:
            t0 = wb.get("t_min")
            if wb.get("t_lo") is not None and t0 is not None:
                d_lo = (wb["t_lo"] - t0) / 86400.0
                d_val = (wb["val_end_ts"] - t0) / 86400.0
                d_hold = (wb["holdout_start_ts"] - t0) / 86400.0
                lines.append(f"Window pre-holdout: dev from day {d_lo:g}, "
                             f"validation ends day {d_val:g}, holdout starts "
                             f"day {d_hold:g}")
                if not wb.get("boundaries_in_archive", False):
                    lines.append(
                        "  note: the archive is shorter than the split "
                        "boundaries, so the window currently spans the whole "
                        "archive (no holdout bars exist yet)")
    lines.append("=" * 70)
    header = (f"{'Coin':<20} {'L':>4} {'T(d)':>5} {'n':>6} {'TP':>6} "
              f"{'SL':>6} {'TIME':>6} {'pass%':>7} {'meanROE':>9}")
    lines.append(header)
    lines.append("-" * 70)

    for coin in result["coins"]:
        for L in result["leverages"]:
            for T_days in result["horizons_days"]:
                r = result["per_coin"][coin][L][T_days]
                if r["n_samples"] > 0:
                    lines.append(f"{coin:<20} {L:>4} {T_days:>5} {r['n_samples']:>6} "
                                 f"{r['tp']:>6} {r['sl']:>6} {r['time']:>6} "
                                 f"{r['pass_rate']*100:>6.1f}% {r['mean_roe']:>9.4f}")

    lines.append("=" * 70)
    lines.append("\nPooled pass rates and simulated cycle pass-count distributions:")
    lines.append("-" * 70)
    header2 = (f"{'L':>4} {'T(d)':>5} {'n':>6} {'pass%':>7} "
               f"{'meanROE':>9}  {'P(>=thr) n=20':>14} {'n=50':>10} {'n=100':>10}")
    lines.append(header2)
    lines.append("-" * 70)

    for L in result["leverages"]:
        for T_days in result["horizons_days"]:
            pool = result["pooled"][L][T_days]
            p_str = f"{pool['pass_rate']*100:.1f}%"
            roe_str = f"{pool['mean_roe']:.4f}"
            sim_parts = []
            for n in (20, 50, 100):
                key = f"sim_n{n}"
                if key in pool:
                    sim_parts.append(
                        f"{pool[key]['p_reach_threshold']*100:.1f}% "
                        f"(thr={pool[key]['threshold']})")
                else:
                    sim_parts.append("N/A")
            lines.append(f"{L:>4} {T_days:>5} {pool['n']:>6} {p_str:>7} {roe_str:>9}  "
                         + "  ".join(f"{p:>14}" for p in sim_parts))

    lines.append("-" * 70)
    lines.append(f"thr = ceil(0.75 * n); P(>=thr) = exact binomial probability that")
    lines.append("a no-skill stride-grid engine reaches threshold by chance.")
    # Per-coin BOUNDARY costs: universe-selection gate only, never labeling
    # costs (the measurement is point-in-time per entry — disclosed below).
    pc_costs = result.get("cost_frac_per_coin", {})
    if pc_costs:
        cost_strs = [f"{c}:{v:.6f}" for c, v in sorted(pc_costs.items()) if v is not None]
        if cost_strs:
            lines.append(f"selection cost_frac (per coin, window boundary): "
                         f"{', '.join(cost_strs[:5])}{'...' if len(cost_strs) > 5 else ''}")
    ca = result.get("cost_application")
    if ca:
        lines.append(f"measurement costs: {ca}")
        if "point-in-time" in ca:
            lines.append("  (cost_frac_per_coin above is the boundary cost used "
                         "ONLY for universe selection)")
    return "\n".join(lines)


def _list_coins(data_dir):
    """List coins in the archive, sorted."""
    return B.list_coins(data_dir)
