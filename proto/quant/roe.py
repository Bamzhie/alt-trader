"""ROE (return on margin) feasibility: what a +100% trade requires and how
often the archive has actually delivered it.

This is a CALCULATOR, not a recommendation.  It answers, for a setup or a coin:

  * the price move a target ROE needs at a given leverage
  * what the stop costs on margin at that leverage, and whether it sits
    safely inside the liquidation distance
  * how often price actually reached that target before the stop, measured on
    the bar archive (unconditional base rate, by side), against the driftless
    null and the cost-adjusted breakeven
  * the average ROE per trade those base rates imply, after costs

The null (`b/(a+b)`) and the cost-adjusted breakeven (`(b+c)/(a+b)`) are both
"the target is reached FIRST" probabilities, over ALL entries; they are
compared with the TP-first rate, never with the TP-among-terminal rate, which
has a different denominator (the entries that hit a barrier).

Leverage does not create edge.  A +100% target on margin at leverage L is
just a price move of 1/L plus the round-trip cost; for a driftless price the
probability of reaching a target at distance a before a stop at distance b is
b/(a+b), so large-ROE targets are LOW-probability by construction.  Only a
rule whose hit rate beats that base rate by more than costs is worth
anything, and no registered rule has passed the promotion gate (see
docs/QUANT_MODULE.md and
docs/implementation-reports/quant-research-2026-10-20/final-report.md).

Simple (not log) returns are used for the ROE arithmetic; the labeling uses
the same conventions as proto.quant.barrier (entry at the next open,
stop-first on ties, time exit at the horizon close, one round-trip cost
fraction, funding NOT modeled).  A label path never crosses a timestamp gap:
the contiguous runs of the archive are the sampling frame.

The intervals are MOVING-BLOCK bootstraps over consecutive UTC days, not
one-day clusters: the label window is 24 h, so the rows of adjacent days
share their price paths.  The block length is a declared assumption
(BLOCK_DAYS) and the interval generally widens with it, so the sensitivity across
block lengths is reported next to every headline figure.

Nothing here writes to data/, promotes anything or enables ACTIONABLE
output.  It does not MEASURE the sealed holdout window either: the caller
decides the window, the CLI measures the pre-holdout window only, and the
guard in the CLI refuses any window whose label horizon would reach into the
holdout.  (The archive's timestamps are still read - they are what places
the split boundaries - but no holdout outcome is computed.)
"""
import math

from . import barrier as B
from . import stats as S
from .bars import load_coin, contiguous_runs
from .config import DEFAULT, BARS_PER_DAY, BAR_S
from .risk import MAINT_MARGIN

LIQ_BUFFER = 0.8        # the stop must sit inside 80% of the liquidation distance

# The label window is 24 h, so the entries of ADJACENT days share their
# price paths: an entry late on one day and an entry early the next are not
# independent.  Resampling whole UTC days independently (the repo's
# `stats.day_cluster_mean`) would therefore understate the uncertainty, so
# the intervals here resample BLOCKS of consecutive days instead
# (`stats.block_cluster_mean`).  BLOCK_DAYS is the declared block length:
# three consecutive days cover the 24 h overlap with a full day of margin.
# The interval generally widens as the block grows (up to bootstrap noise), so
# `BLOCK_SENSITIVITY` is reported
# alongside it and a reader who believes the dependence is longer can read
# the longer block.
BLOCK_DAYS = 3
BLOCK_SENSITIVITY = (1, 2, 3, 7, 14)
BOOTSTRAP_B = 600


def roe_from_move(move, leverage, cost_frac=0.0):
    """Return on margin for a price move `move` (simple, in the trade's favor)."""
    if leverage <= 0:
        raise ValueError(f"leverage must be positive, got {leverage}")
    return leverage * (move - cost_frac)


def required_move(target_roe, leverage, cost_frac=0.0):
    """Gross price move that nets `target_roe` on margin after the costs."""
    if leverage <= 0:
        raise ValueError(f"leverage must be positive, got {leverage}")
    if target_roe <= 0:
        raise ValueError(f"target_roe must be positive, got {target_roe}")
    return target_roe / leverage + cost_frac


def liquidation_distance(leverage, mm=MAINT_MARGIN):
    """Approximate isolated-margin liquidation distance (fraction of price)."""
    return max(1.0 / leverage - mm, 0.0)


def max_leverage_for_stop(stop_frac, buffer=LIQ_BUFFER, mm=MAINT_MARGIN):
    """Highest leverage whose liquidation distance still clears the stop with a
    buffer: stop <= buffer * (1/L - mm)."""
    return 1.0 / (stop_frac / buffer + mm)


def target_plan(stop_roe, leverage, cost_frac, target_roe=1.0):
    """Geometry of a "target_roe up / stop_roe down" trade at a chosen leverage.

    stop_roe: loss at the stop as a positive fraction of margin (0.5 = -50%).
    target_roe: gain at the target as a positive fraction of margin.

    `p_null` and `p_breakeven` are both probabilities of reaching the TARGET
    FIRST (before the stop): `p_null` is the driftless infinite-horizon rate,
    `p_breakeven` the rate that makes the trade break even after costs.
    """
    if leverage <= 0:
        raise ValueError(f"leverage must be positive, got {leverage}")
    if target_roe <= 0:
        raise ValueError(f"target_roe must be positive, got {target_roe}")
    if stop_roe <= 0:
        raise ValueError(f"stop_roe must be positive, got {stop_roe}")
    a = required_move(target_roe, leverage, cost_frac)          # gross target move
    b = stop_roe / leverage - cost_frac                         # gross stop move
    if b <= 0:
        raise ValueError("stop inside the round-trip cost: infeasible")
    liq_dist = liquidation_distance(leverage)
    return {"leverage": leverage, "target_move": a, "stop_move": b,
            "target_roe": target_roe, "stop_roe": stop_roe,
            "reward_risk": target_roe / stop_roe,
            "p_null": b / (a + b),                      # driftless, infinite horizon
            "p_breakeven": (b + cost_frac) / (a + b),   # net-of-cost breakeven
            "liq_distance": liq_dist,
            "liq_ok": b <= LIQ_BUFFER * liq_dist}


def event_block(tp_frac, stop_frac, cost_frac, target_roe=1.0):
    """What a +target_roe outcome would require for an event's OWN levels.

    `tp_frac` and `stop_frac` are the SIMPLE price-move fractions of the
    event's own target and stop (the same convention as
    `proto.quant.risk.plan_levels`).  The block is shown on every logged
    event so a leveraged target can never be read without also seeing what
    the stop costs at that leverage.
    """
    cost_frac = cost_frac or 0.0
    if target_roe <= 0:
        raise ValueError(f"target_roe must be positive, got {target_roe}")
    net_tp = tp_frac - cost_frac
    if net_tp <= 0:
        # "feasible" here means ONLY "the target clears the round-trip
        # cost".  It says nothing about whether the geometry is worth trading.
        return {"feasible": False, "why": "target does not clear costs"}
    lev = target_roe / net_tp
    stop_roe = lev * (stop_frac + cost_frac)
    liq_dist = liquidation_distance(lev)
    liq_ok = stop_frac <= LIQ_BUFFER * liq_dist
    warn = []
    if not liq_ok:
        warn.append("stop is not inside the liquidation distance at the leverage "
                    "needed for the target")
    if stop_roe >= 1.0:
        warn.append("the stop costs the whole margin or more at that leverage")
    return {"feasible": True, "target_roe": target_roe, "leverage_needed": lev,
            "roe_at_stop": -stop_roe,
            "reward_risk_roe": (target_roe / stop_roe) if stop_roe > 0 else float("inf"),
            "liq_distance": liq_dist, "liq_ok": liq_ok, "warnings": warn}


def _qv24_prefix(amt):
    cs = [0.0]
    for a in amt:
        cs.append(cs[-1] + a)
    return cs


def empirical_rates(bars, plan, cost_frac, horizon=BARS_PER_DAY, stride=12,
                    t_lo=None, t_hi=None, min_qv24=None):
    """Unconditional base rates for `plan` on one coin's archive.

    Every `stride`-th bar of every contiguous run (liquid at the time) is an
    entry; BOTH sides are labeled with the same barrier distances.  Returns
    counts, TP-among-terminal, per-side stats and the average net ROE per
    trade.  A path never crosses a timestamp gap: the entry is dropped when
    its horizon does not fit inside the coin's contiguous run.
    """
    min_qv24 = DEFAULT.cost.min_qv24 if min_qv24 is None else min_qv24
    if stride < 1:
        raise ValueError(f"stride must be at least 1, got {stride}")
    o, h, l, c, amt, ts = (bars[k] for k in ("o", "h", "l", "c", "amt", "ts"))
    cs = _qv24_prefix(amt)
    a, b, L = plan["target_move"], plan["stop_move"], plan["leverage"]
    if a >= 1.0 or b >= 1.0:
        raise ValueError("a move of 100% of price is not modeled")
    # Side-exact geometry: each side wins EXACTLY `a` and loses EXACTLY `b` in
    # simple return.  Long: stop at e*(1-b), target at e*(1+a).  Short: stop
    # at e*(1+b), target at e*(1-a).  label_path works in log distances, so
    # each side gets its own stop distance and a target expressed as a
    # multiple of it.
    dd_side = {1: -math.log(1.0 - b), -1: math.log(1.0 + b)}
    r_tp_side = {1: math.log(1.0 + a) / dd_side[1],
                 -1: -math.log(1.0 - a) / dd_side[-1]}
    out = {"n": 0, "entries": 0, "tp": 0, "sl": 0, "time": 0, "roe_sum": 0.0,
           "days": [], "tp_flags": [], "term_days": [], "roe": [], "touch": 0,
           # the sampled entry indices: the audit trail of which bars the
           # measurement actually used (a gap-safe sampling frame is a
           # property of THESE indices, so they are kept)
           "entry_idx": [],
           "by_side": {1: [0, 0, 0], -1: [0, 0, 0]}}
    for (run_a, run_z) in contiguous_runs(ts):
        # The trailing-24h volume gate needs BARS_PER_DAY bars of history, and
        # the stride phase is aligned to the GLOBAL grid so the sampled bars
        # do not depend on where a run happens to start.  The 24 h window is
        # taken INSIDE the run: a window that reaches back over a gap does
        # not describe the coin's liquidity at that time, so such entries are
        # dropped rather than measured on an unknown volume.
        start = run_a + BARS_PER_DAY
        start += (BARS_PER_DAY - start) % stride
        for i in range(start, run_z - horizon, stride):
            if (t_lo is not None and ts[i] < t_lo) or (t_hi is not None and ts[i] >= t_hi):
                continue
            if cs[i + 1] - cs[i + 1 - BARS_PER_DAY] < min_qv24:
                continue
            path = B.make_path(o, h, l, c, i, horizon, run_end=run_z)
            if path is None:
                continue
            day = ts[i] // 86400
            e = path.entry
            out["entries"] += 1
            out["entry_idx"].append(i)
            for side in (1, -1):
                g, kind, _ = B.label_path(path, side, dd_side[side], horizon,
                                          r_tp_side[side])
                move = (math.exp(g) - 1.0) if side > 0 else (1.0 - math.exp(-g))
                roe = L * (move - cost_frac)
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
    return out


def summarize_rates(r, plan, block=BLOCK_DAYS, B=BOOTSTRAP_B):
    """Derived figures with MOVING-BLOCK day-cluster intervals.

    The interval resamples blocks of `block` consecutive UTC days (see
    `stats.block_cluster_mean`): the label window is 24 h, so the rows of
    adjacent days share their price paths and must not be resampled as if
    they were independent.  `block` and `B` are reported so a reader can
    recompute the interval exactly.
    """
    n = r["n"]
    if n == 0:
        return {"n": 0, "entries": r.get("entries", 0), "days": 0,
                "block_days": block, "bootstrap_b": B}
    term = r["tp"] + r["sl"]
    m_tp, lo_tp, hi_tp, _ = (S.block_cluster_mean(r["tp_flags"], r["term_days"],
                                                   block=block, B=B)
                             if r["tp_flags"] else (float("nan"),) * 4)
    m_roe, lo_roe, hi_roe, nd = S.block_cluster_mean(r["roe"], r["days"],
                                                      block=block, B=B)
    return {"n": n, "entries": r.get("entries", 0), "days": nd,
            "touch": r["touch"] / n,
            "tp": r["tp"] / n, "sl": r["sl"] / n,
            "time": r["time"] / n, "terminal": term / n,
            "tp_given_terminal": m_tp, "tp_ci": (lo_tp, hi_tp),
            "p_null": plan["p_null"], "p_breakeven": plan["p_breakeven"],
            "mean_roe": m_roe, "roe_ci": (lo_roe, hi_roe),
            "block_days": block, "bootstrap_b": B}


def coin_report(data_dir, coin, stop_roe=0.5, leverages=(10, 20), target_roe=1.0,
                horizon=BARS_PER_DAY, stride=12, t_lo=None, t_hi=None,
                cost_model=None):
    """Per-coin feasibility rows (one per leverage), or None if untradeable.

    The round-trip cost of a coin is taken from the trailing 24 h quote
    volume at the LAST bar of the window (the cost model is tiered by
    trailing-24h volume), so it is one constant per coin per window.
    """
    cm = cost_model or DEFAULT.cost
    bars = load_coin(data_dir, coin)
    if not bars or len(bars["ts"]) < 3 * BARS_PER_DAY:
        return None
    amt, ts = bars["amt"], bars["ts"]
    sel = [i for i, t in enumerate(ts)
           if (t_lo is None or t >= t_lo) and (t_hi is None or t < t_hi)]
    if not sel:
        return None
    last = sel[-1]
    # The 24 h volume window at the end of the window must be CONTIGUOUS: a
    # window that reaches back over a gap does not describe the coin's
    # liquidity at that time, so the volume is unknown and the coin is not
    # tradeable (the same rule the per-entry gate applies).
    k = last
    while k > 0 and ts[k] - ts[k - 1] == BAR_S and (last - k) < BARS_PER_DAY - 1:
        k -= 1
    if last - k + 1 < BARS_PER_DAY:
        return {"coin": coin, "qv24": None,
                "untradeable": "the 24h volume window spans a timestamp gap"}
    qv24 = sum(amt[k:last + 1])
    cost = cm.round_trip(qv24)
    if cost is None:
        return {"coin": coin, "qv24": qv24, "untradeable": "below liquidity floor"}
    rows = []
    for L in leverages:
        try:
            plan = target_plan(stop_roe, L, cost, target_roe)
        except ValueError:
            continue
        try:
            rates = empirical_rates(bars, plan, cost, horizon, stride, t_lo, t_hi,
                                    cm.min_qv24)
        except ValueError:
            # a geometry that cannot be measured (e.g. a move of 100% of
            # price) skips this leverage; it never aborts the whole run
            continue
        rows.append({"leverage": L, "plan": plan,
                     "rates": summarize_rates(rates, plan), "raw": rates})
    return {"coin": coin, "qv24": qv24, "cost_frac": cost, "rows": rows}


def pooled_from_reports(reports):
    """Aggregate base rates across coins per leverage (sample-weighted).

    The pooled row is built from the POOLED per-sample rows, so its
    block-cluster intervals resample blocks of consecutive UTC days over
    every coin at once (all coins on a day share the market move), exactly
    like the per-coin rows.
    """
    agg = {}
    for rep in reports:
        if not rep or "rows" not in rep:
            continue
        for row in rep["rows"]:
            raw = row.get("raw")
            if not raw or not raw.get("n"):
                continue
            L = row["leverage"]
            a = agg.setdefault(L, {"n": 0, "entries": 0, "tp": 0, "sl": 0,
                                    "time": 0, "touch": 0, "roe_sum": 0.0,
                                    "days": [], "roe": [], "tp_flags": [],
                                    "term_days": [], "coins": 0, "plan": row["plan"],
                                    "pl": {}})
            a["n"] += raw["n"]
            a["entries"] += raw.get("entries", 0)
            a["tp"] += raw["tp"]
            a["sl"] += raw["sl"]
            a["time"] += raw["time"]
            a["touch"] += raw["touch"]
            a["roe_sum"] += raw["roe_sum"]
            a["days"].extend(raw["days"])
            a["roe"].extend(raw["roe"])
            a["tp_flags"].extend(raw["tp_flags"])
            a["term_days"].extend(raw["term_days"])
            for key in ("target_move", "stop_move", "p_null", "p_breakeven",
                        "liq_distance"):
                a["pl"][key] = a["pl"].get(key, 0.0) + row["plan"][key] * raw["n"]
            a["liq_all"] = a.get("liq_all", True) and row["plan"]["liq_ok"]
            a["coins"] += 1
    out = {}
    for L, a in agg.items():
        if not a["n"]:
            continue
        plan = dict(a["plan"])                  # sample-weighted over coins
        for key, v in a["pl"].items():
            plan[key] = v / a["n"]
        plan["liq_ok"] = a["liq_all"]
        pooled_raw = {"n": a["n"], "entries": a["entries"], "tp": a["tp"],
                      "sl": a["sl"], "time": a["time"], "touch": a["touch"],
                      "roe_sum": a["roe_sum"], "days": a["days"], "roe": a["roe"],
                      "tp_flags": a["tp_flags"], "term_days": a["term_days"]}
        s = summarize_rates(pooled_raw, plan, block=BLOCK_DAYS)
        # How the interval depends on the declared block length: the label
        # window is 24 h, so the rows of adjacent days are dependent and the
        # interval has to be read together with this sensitivity.
        sens = {}
        for blk in BLOCK_SENSITIVITY:
            sb = summarize_rates(pooled_raw, plan, block=blk)
            sens[str(blk)] = {"tp_given_terminal": sb["tp_given_terminal"],
                              "tp_ci": sb["tp_ci"],
                              "mean_roe": sb["mean_roe"],
                              "roe_ci": sb["roe_ci"]}
        out[L] = {"n": s["n"], "entries": s["entries"], "coins": a["coins"],
                  "tp": s["tp"], "sl": s["sl"], "touch": s["touch"],
                  "time": s["time"],
                  "tp_given_terminal": s["tp_given_terminal"], "tp_ci": s["tp_ci"],
                  "mean_roe": s["mean_roe"], "roe_ci": s["roe_ci"],
                  "block_days": s["block_days"], "bootstrap_b": s["bootstrap_b"],
                  "ci_by_block": sens, "plan": plan}
    return out


def pooled_table(data_dir, coins, **kw):
    """Aggregate base rates across coins per leverage (sample-weighted)."""
    return pooled_from_reports([coin_report(data_dir, c, **kw) for c in coins])
