"""Frozen analysis protocol for HYD-0003 (manual-review signal family).

READ-ONLY with respect to the production archives: this module reads
``data/bars``, ``data/funding`` and ``data/funding_settlements`` and
writes nothing outside its caller's output location.

WHAT IS FROZEN (see ``docs/hypotheses/HYD-0003-manual-review-signal-family.md``):
the universe rule, the point-in-time membership rule, the five declared
family members, the direction, the entry/exit geometry, the horizon, the
cost model, the chronological splits, the metrics, the minimum sample
sizes, the uncertainty method, the baselines and the decision criteria
(for freezing a configuration and for spending the one-shot holdout).

Two rules the module enforces mechanically, because they are the ones that
must not be left to the analyst:

  * **the holdout is not evaluated here.**  ``run_split`` refuses any
    window that reaches into the holdout range and says why.  The holdout
    is spent by a separate, one-shot path on a FROZEN configuration.
  * **a null or incomplete result is never relabelled.**  A cell below the
    minimum sample sizes is reported as ``insufficient`` with its
    denominators and NO mean; a member whose funding cannot be attributed
    is excluded from the funding-adjusted denominator and counted.

The unit of measurement is **R (return per unit of risk taken at the
stop)**: entry at the open of the bar after the signal, stop at
``k_stop * sigma * sqrt(T)``, target at ``r_tp`` times the stop distance,
time exit at the close of the last bar of the horizon; the stop wins a
same-bar tie.  No leverage is applied and no equity-denominated return is
claimed: a futures return needs a position-sizing and liquidation model,
which this study does not provide.
"""
import math
import os
import time

from . import inference as INF
from . import family_test as FT
from . import competing_outcomes as CO
from .config import BAR_S, DEFAULT, Geometry, RuleParams
from .structure import confirmed_pivots

ANALYSIS_VERSION = "analysis-v1"

# --- the frozen parameters (HYD-0003) ------------------------------------
MIN_EVENTS = 200          # tradeable events per split
MIN_COINS = 15            # distinct coins per split
MIN_DAYS = 20             # distinct days per split
MIN_COVERAGE_DAYS = 60    # universe rule: complete days covering the window
PIVOT_WINDOW_BARS = 200   # the structure filter's lookback
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
PIVOT_KIND = "low"        # fire when the last confirmed pivot is a LOW
PLACEBO_COUNT = 200
FAMILY_ALPHA = 0.10
PLACEBO_ALPHA = 0.05
FUNDING_DELTA_FLOOR = -0.002      # R per trade
SPA_P_MAX = 0.10


# ---------------------------------------------------------------- helpers
def one_sided_p(t):
    """One-sided p-value for "the mean is > 0" from a t statistic."""
    if t is None:
        return None
    if not isinstance(t, (int, float)) or not math.isfinite(t):
        return None
    return max(0.0, min(1.0, 0.5 * (1.0 - math.erf(t / math.sqrt(2.0)))))


def coverage_rule(stats):
    """The preregistered universe rule.

    ``stats`` is the per-coin inventory entry: ``days_covered``,
    ``malformed``, ``first_ts``, ``last_ts``, ``gaps``.  Returns
    ``(eligible, reason)``.
    """
    if not stats:
        return False, "no-inventory"
    if int(stats.get("malformed") or 0) > 0:
        return False, "malformed-rows"
    days = float(stats.get("days_covered") or 0.0)
    if days < MIN_COVERAGE_DAYS:
        return False, f"coverage {days:.1f}d < {MIN_COVERAGE_DAYS}d"
    if int(stats.get("gaps") or 0) > 0:
        return False, "gaps-in-archive"
    return True, "eligible"


# ------------------------------------------------- point-in-time structure
def pivot_state_series(bars, window=PIVOT_WINDOW_BARS, left=PIVOT_LEFT,
                       right=PIVOT_RIGHT):
    """The last confirmed pivot kind at every bar, using only closed bars.

    For each bar the state is the kind of the most recent pivot whose
    ``confirmed_ts`` is at or before that bar's CLOSE (so a pivot is only
    used once it is knowable), and whose own bar lies within ``window``
    bars.  ``None`` means "no usable pivot state".

    Point-in-time: the pivot set is computed from the series, but a pivot
    is only ever attributed to a bar at or after its confirmation time, so
    a signal can never see a pivot it could not have known.
    """
    if not bars:
        return {}
    pivots = confirmed_pivots(bars, left=left, right=right)
    states = {}
    if not pivots:
        for b in bars:
            states[int(b["ts"])] = None
        return states
    j = 0
    span = int(window) * BAR_S
    for b in bars:
        ts = int(b["ts"])
        close = ts + BAR_S
        while j + 1 < len(pivots) and int(pivots[j + 1].confirmed_ts) <= close:
            j += 1
        p = pivots[j]
        if int(p.confirmed_ts) <= close and ts - int(p.ts) <= span:
            states[ts] = p.kind
        else:
            states[ts] = None
    return states


def pivot_state_at(pivots, ts, window=PIVOT_WINDOW_BARS):
    """The structure state at ONE bar, from a precomputed pivot list.

    The state is the kind of the most recent pivot that is knowable by the
    bar's close (``confirmed_ts <= ts + BAR_S``) and whose own bar lies
    within ``window`` bars; ``None`` when there is no such pivot.
    """
    close = int(ts) + BAR_S
    best = None
    for p in pivots:                       # pivots are ordered by ts
        if int(p.confirmed_ts) > close:
            break
        best = p
    if best is None:
        return None
    if int(ts) - int(best.ts) > int(window) * BAR_S:
        return None
    return best.kind


def pivot_states_for(bars, ts_list, window=PIVOT_WINDOW_BARS,
                     left=PIVOT_LEFT, right=PIVOT_RIGHT):
    """The structure state at each timestamp in ``ts_list``.

    Point-in-time: the pivot set is computed from the series, but a pivot
    is only attributed to a bar at or after its confirmation time, so a
    signal can never see a pivot it could not have known.  Only the
    requested timestamps are materialised.
    """
    pivots = confirmed_pivots(bars, left=left, right=right) if bars else []
    return {int(ts): pivot_state_at(pivots, ts, window=window)
            for ts in ts_list}


def filter_by_pivot_state(events, states, kind=PIVOT_KIND):
    """Keep the events whose signal bar carries the preregistered state."""
    kept, dropped = [], 0
    for ev in events:
        if states.get(int(ev.ts)) == kind:
            kept.append(ev)
        else:
            dropped += 1
    return kept, dropped


# ------------------------------------------------------------- evaluation
def _friction_only_r(outcome, dd):
    """R per trade before funding: (gross_log - friction) / dd."""
    if outcome.gross_log is None or outcome.friction is None or not dd:
        return None
    return (outcome.gross_log - outcome.friction) / dd


def evaluate_member(events, rule, geom, window, *, cfg=DEFAULT,
                     funding_dir=None, settlements_dir=None,
                     min_events=MIN_EVENTS, min_coins=MIN_COINS,
                     min_days=MIN_DAYS, label=None):
    """Evaluate one family member on one window.

    Returns a dict with explicit denominators, the friction-only and
    funding-adjusted results reported SEPARATELY, the cause shares, the
    day- and coin-cluster intervals and the baseline comparison.  A cell
    below the minimum sample sizes is ``insufficient`` and carries NO
    mean.
    """
    label = label or getattr(rule, "name", None) or "member"
    res = CO.resolve_competing(events, rule, geom, window, cfg,
                                funding_dir=funding_dir,
                                settlements_dir=settlements_dir)
    outcomes = res.get("outcomes") or []
    n_selected = int(res.get("n_selected") or 0)
    n_resolved = int(res.get("n_resolved") or 0)

    fric, net, days, coins = [], [], [], []
    for o in outcomes:
        if o.dd:
            fr = _friction_only_r(o, o.dd)
            if fr is not None:
                fric.append(fr)
                days.append(int(o.ts) // 86400)
                coins.append(o.coin)
                if o.net_r is not None:
                    net.append(o.net_r)
    n_friction = len(fric)
    n_net = len(net)
    distinct_coins = len(set(coins))
    distinct_days = len(set(days))

    out = {
        "label": label,
        "rule": getattr(rule, "family", None),
        "params": getattr(rule, "params", None) or {},
        "geometry": {"key": geom.key(), "horizon_bars": geom.horizon_bars,
                      "k_stop": geom.k_stop, "r_tp": geom.r_tp},
        "window": [float(window[0]), float(window[1])],
        "denominators": {
            "n_selected": n_selected,
            "n_resolved": n_resolved,
            "n_friction_evaluable": n_friction,
            "n_funding_evaluable": n_net,
            "n_censored": int(res.get("n_censored") or 0),
            "n_invalidated": int(res.get("n_invalidated") or 0),
            "n_coins": distinct_coins,
            "n_days": distinct_days,
            "funding_coverage": (n_net / n_friction) if n_friction else None,
        },
        "causes": res.get("causes") or {},
        "cause_denominator": n_resolved,
        "friction_only": None,
        "funding_adjusted": None,
        "status": "insufficient",
        "reason": None,
    }
    if n_friction < min_events or distinct_coins < min_coins \
            or distinct_days < min_days:
        out["reason"] = (f"insufficient: {n_friction} events "
                         f"(min {min_events}), {distinct_coins} coins "
                         f"(min {min_coins}), {distinct_days} days "
                         f"(min {min_days})")
        return out

    u_f = INF.uncertainty(fric, days=days, coins=coins)
    out["friction_only"] = {
        "mean_r": u_f.get("mean"),
        "ci_lo_day": u_f.get("ci_lo_day"),
        "ci_hi_day": u_f.get("ci_hi_day"),
        "ci_lo_coin": u_f.get("ci_lo_coin"),
        "ci_hi_coin": u_f.get("ci_hi_coin"),
        "t_day": u_f.get("t_day"),
        "t_coin": u_f.get("t_coin"),
        "clusters": u_f.get("clusters"),
    }
    p_f = one_sided_p(u_f.get("t_day"))
    out["friction_only"]["p_one_sided"] = p_f
    if n_net >= min_events:
        u_n = INF.uncertainty(net, days=days[:n_net], coins=coins[:n_net])
        out["funding_adjusted"] = {
            "mean_r": u_n.get("mean"),
            "ci_lo_day": u_n.get("ci_lo_day"),
            "ci_hi_day": u_n.get("ci_hi_day"),
            "ci_lo_coin": u_n.get("ci_lo_coin"),
            "ci_hi_coin": u_n.get("ci_hi_coin"),
            "t_day": u_n.get("t_day"),
            "t_coin": u_n.get("t_coin"),
            "clusters": u_n.get("clusters"),
            "delta_vs_friction_only": (u_n.get("mean") - u_f.get("mean"))
            if (u_n.get("mean") is not None and u_f.get("mean") is not None)
            else None,
        }
        out["funding_adjusted"]["p_one_sided"] = one_sided_p(u_n.get("t_day"))
    out["status"] = "evaluated"
    out["_values"] = fric
    out["_days"] = days
    return out


def evaluate_with_baselines(events, rule, geom, window, **kw):
    """The member plus the three preregistered baselines."""
    res = evaluate_member(events, rule, geom, window, **kw)
    try:
        b = INF.baselines(events, rule, geom, window, cfg=kw.get("cfg", DEFAULT),
                          placebos=PLACEBO_COUNT, seed=7)
    except Exception as exc:                     # never fail the run on a baseline
        b = {"error": f"{type(exc).__name__}: {exc}"}
    res["baselines"] = b
    return res


# ------------------------------------------------------- the holdout wall
def refuse_holdout_window(window, holdout_start_day, splits):
    """Refuse a window that reaches into the holdout.

    Returns ``(True, reason)`` when the window is refused.  The analysis
    protocol may only touch dev and validation.
    """
    lo, hi = float(window[0]), float(window[1])
    if hi > float(holdout_start_day):
        return True, (f"holdout-sealed: the window [{lo}, {hi}) reaches into "
                      f"the holdout (day {holdout_start_day}); the one-shot "
                      f"holdout may only be evaluated once, on a frozen "
                      f"configuration, through the separate spend path")
    return False, None


# -------------------------------------------------------- family + freeze
def family_report(members, *, method="bootstrap", alpha=FAMILY_ALPHA):
    """The family-level multiple-testing correction over the declared members.

    Members are the ones that produced a mean (an insufficient cell is
    excluded from the correction and reported as such).
    """
    usable = [m for m in members if m.get("status") == "evaluated"
               and m.get("friction_only")
               and m["friction_only"].get("p_one_sided") is not None]
    excluded = [{"label": m.get("label"), "reason": m.get("reason")}
                for m in members if m not in usable]
    if not usable:
        return {"status": "insufficient", "reason": "no-evaluable-member",
                "n_members": len(members), "excluded": excluded,
                "family_p": None, "adjusted": []}
    hyps = []
    for m in usable:
        hyps.append({
            "id": m["label"],
            "p": m["friction_only"]["p_one_sided"],
            "n": m["denominators"]["n_friction_evaluable"],
            "metric": "mean_net_r_per_trade",
            "values": m.get("_values"),
            "days": m.get("_days"),
        })
    corr = FT.correct(hyps, method=method, alpha=alpha)
    for m in usable:
        m.setdefault("family", {})
    return {
        "status": "evaluated",
        "method": method,
        "alpha": alpha,
        "n_members": len(members),
        "n_evaluable": len(usable),
        "excluded": excluded,
        "family_p": corr.get("family_p"),
        "best_id": corr.get("best_id"),
        "adjusted": corr.get("adjusted"),
        "reason": corr.get("reason"),
    }


def freeze_criteria(member, family, val_member, placebo=None):
    """Apply the seven preregistered criteria for freezing a configuration.

    Returns ``(frozen: bool, checks: list)``.  Every check states what it
    compared and what it found, so a reader can disagree with a specific
    criterion rather than with a verdict.
    """
    checks = []

    def add(name, ok, detail):
        checks.append({"criterion": name, "passed": bool(ok), "detail": detail})

    fo = (member or {}).get("friction_only") or {}
    fv = (val_member or {}).get("friction_only") or {}
    dm = member.get("denominators") or {}
    dv = (val_member or {}).get("denominators") or {}

    add("dev mean net R (friction-only) > 0",
        (fo.get("mean_r") or 0) > 0, f"dev mean_r={fo.get('mean_r')}")
    add("dev day-cluster 95% lower bound > 0",
        (fo.get("ci_lo_day") is not None) and (fo.get("ci_lo_day") > 0),
        f"dev ci_lo_day={fo.get('ci_lo_day')}")
    add("validation mean net R (friction-only) > 0",
        (fv.get("mean_r") or 0) > 0, f"val mean_r={fv.get('mean_r')}")
    add("validation day-cluster 95% lower bound > 0",
        (fv.get("ci_lo_day") is not None) and (fv.get("ci_lo_day") > 0),
        f"val ci_lo_day={fv.get('ci_lo_day')}")
    add("minimum sample sizes met on both splits",
        dm.get("n_friction_evaluable", 0) >= MIN_EVENTS
        and dv.get("n_friction_evaluable", 0) >= MIN_EVENTS
        and dm.get("n_coins", 0) >= MIN_COINS
        and dv.get("n_coins", 0) >= MIN_COINS
        and dm.get("n_days", 0) >= MIN_DAYS
        and dv.get("n_days", 0) >= MIN_DAYS,
        f"dev n={dm.get('n_friction_evaluable')}/{dm.get('n_coins')}c/"
        f"{dm.get('n_days')}d, val n={dv.get('n_friction_evaluable')}/"
        f"{dv.get('n_coins')}c/{dv.get('n_days')}d")
    add(f"family-corrected p <= {FAMILY_ALPHA}",
        (family or {}).get("family_p") is not None
        and family["family_p"] <= FAMILY_ALPHA,
        f"family_p={(family or {}).get('family_p')}")
    if placebo is not None:
        add(f"matched placebo p <= {PLACEBO_ALPHA}",
            placebo.get("p_value") is not None
            and placebo["p_value"] <= PLACEBO_ALPHA,
            f"placebo p={placebo.get('p_value')}")
    fa = (member or {}).get("funding_adjusted") or {}
    add("funding delta >= -0.002 R per trade",
        (fa.get("delta_vs_friction_only") is not None)
        and fa["delta_vs_friction_only"] >= FUNDING_DELTA_FLOOR,
        f"funding delta={fa.get('delta_vs_friction_only')}")
    return all(c["passed"] for c in checks), checks


def describe(result):
    """A one-line human summary of a member result (denominators included)."""
    d = result.get("denominators") or {}
    fo = result.get("friction_only") or {}
    fa = result.get("funding_adjusted") or {}
    if result.get("status") != "evaluated":
        return f"{result.get('label')}: insufficient ({result.get('reason')})"
    return (f"{result.get('label')}: friction-only mean {fo.get('mean_r'):+.5f} R "
            f"[{fo.get('ci_lo_day')}, {fo.get('ci_hi_day')}] day-cluster, "
            f"n={d.get('n_friction_evaluable')} events / {d.get('n_coins')} coins "
            f"/ {d.get('n_days')} days; funding-adjusted "
            f"{fa.get('mean_r') if fa.get('mean_r') is not None else float('nan'):+.5f} R "
            f"(n={d.get('n_funding_evaluable')}, coverage "
            f"{d.get('funding_coverage')})")
