"""WS-B: chronological probability estimation, uncertainty and baselines.

Three questions this module answers, and nothing else:

  1. **How confident is the platform's probability, chronologically?**
     ``chronological_probability`` walks the selected events in time order,
     fits the label rate on an EXPANDING training prefix and predicts the
     next block of events.  Every prediction is then scored against what
     actually happened, and the scores are reported as a CALIBRATION TABLE
     with explicit per-bucket denominators.  The estimator never sees a
     later event than the one it predicts, so the calibration is
     out-of-sample by construction.
  2. **How uncertain is a number once the clustering is respected?**
     ``uncertainty`` reports a DAY-cluster interval (the house rule: rows
     on one UTC day share market moves) AND a COIN-cluster interval (a
     coin-block bootstrap: whole coins are resampled with replacement).
     Both are reported separately -- they answer different questions and
     they disagree when the risk lives in a different dimension.  An
     interval whose cluster count is below three is None with a reason,
     never a degenerate zero-width interval.
  3. **What would this look like WITHOUT the signal?**
     ``baselines`` computes three references on the SAME event set, each
     with its own denominator: the unconditional base rate, the rule
     without any extra feature gate, and a matched placebo (events matched
     on coin / time-of-day / liquidity but taken from a DIFFERENT date, so
     the marginal distribution is preserved and the signal is not).

Stdlib only.  Measurement only: nothing here promotes a rule, nothing here
enables ACTIONABLE output and nothing here touches the holdout.
"""
import math
import random

from . import competing_outcomes as CO
from . import research as R
from . import stats as S
from .config import DEFAULT

INFERENCE_VERSION = "inference-v1"

#: Time-of-day buckets used by the probability estimator and the placebo
#: matching: six-hour buckets of UTC day time.
TOD_BUCKET_S = 6 * 3600
N_TOD_BUCKETS = 4

#: Shrinkage constant of the bucketed rate estimator: a bucket with n_b
#: observations and h_b hits is shrunk towards the global rate with weight
#: SHRINKAGE (a bucket needs this many observations before it is trusted
#: on its own).
SHRINKAGE = 5.0

#: Number of calibration buckets in the calibration table.
CAL_BUCKETS = 5

#: Minimum number of clusters for an interval to be reported at all.
MIN_CLUSTERS = 3


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _tod_bucket(ts):
    return int((ts % 86400) // TOD_BUCKET_S)


def _liq_bucket(cfg, qv24):
    """Liquidity tier index (the cost model's own slippage tiers)."""
    for i, (floor, _slip) in enumerate(cfg.cost.slip_tiers):
        if qv24 is not None and qv24 >= floor:
            return i
    return -1


def _trade_side(ev, rule):
    """The TRADE side under a rule's policy (house convention, reused)."""
    return CO._trade_side(ev, rule)


def _percentile(sorted_values, q):
    """Linear interpolation between order statistics (see CO._percentile)."""
    return CO._percentile(sorted_values, q)


def _mean(values):
    return (sum(values) / len(values)) if values else None


def _sd(values):
    n = len(values)
    if n == 0:
        return None
    if n == 1:
        return 0.0
    m = sum(values) / n
    return math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))


# ---------------------------------------------------------------------------
# 1. chronological probability + calibration
# ---------------------------------------------------------------------------
def chronological_probability(events, rule, geom, window, *, min_train=30,
                              step=1, cfg=DEFAULT) -> dict:
    """Expanding-window probability estimation with a calibration table.

    The events are selected with the rule, ordered chronologically and
    walked in blocks of ``step``:

      * the first ``min_train`` events form the initial training prefix;
      * the model is fitted on the prefix, the next ``step`` events are
        PREDICTED, then those events join the prefix (expanding window);
      * every prediction is scored against the realised label when the
        label is known.

    The fitted model is the LABEL RATE (the probability that an event
    ends in a target hit) estimated per bucket and shrunk towards the
    global rate:

        p_global = (hits + 1) / (n + 2)                      (Laplace)
        p_bucket = (hits_b + SHRINKAGE * p_global) / (n_b + SHRINKAGE)

    Buckets are (time-of-day bucket, liquidity tier) -- the same two
    marginals the placebo is matched on.  Events whose label cannot be
    resolved are predicted but not scored (they are counted in
    ``n_predictions`` and not in ``n_outcomes``).

    Returns:
      n_predictions  predictions made
      n_outcomes     predictions that could be scored
      bins           [{lo, hi, n_pred, n_hit, observed}] -- equal-count
                     buckets of the predicted probability, each with its
                     own denominators
      brier          mean squared error of the prediction against the
                     realised label (None when nothing could be scored)
      slope          stats.calibration_slope (None when undefined)
      records        the individual predictions, for auditing
    """
    if step < 1:
        step = 1
    lo, hi = window
    sel = R._select(events, rule, lo, hi)
    items = []
    n_untradeable = 0
    for ev in sorted(sel, key=lambda e: (e.ts, e.coin)):
        qv24 = getattr(ev, "qv24", None)
        if cfg.cost.round_trip(qv24) is None:
            n_untradeable += 1
            continue
        side = _trade_side(ev, rule)
        cause, _gross, _off = CO.label_slot(ev, geom, side)
        bucket = (_tod_bucket(ev.ts), _liq_bucket(cfg, qv24))
        y = 1 if cause == "TP" else (0 if cause in ("SL", "TIME") else None)
        items.append({"coin": ev.coin, "ts": ev.ts, "bucket": bucket, "y": y,
                      "cause": cause})

    n = len(items)
    train = []
    records = []
    start = min_train
    while start < n:
        # fit on the training prefix (every event before `start`)
        hits = sum(1 for _b, y in train if y == 1)
        p_global = (hits + 1.0) / (len(train) + 2.0)
        b_hits, b_n = {}, {}
        for b, y in train:
            if y is None:
                continue
            b_n[b] = b_n.get(b, 0) + 1
            b_hits[b] = b_hits.get(b, 0) + (1 if y == 1 else 0)
        block_end = min(start + step, n)
        for it in items[start:block_end]:
            b = it["bucket"]
            if b_n.get(b):
                p = ((b_hits[b] + SHRINKAGE * p_global)
                     / (b_n[b] + SHRINKAGE))
            else:
                p = p_global
            p = min(1.0, max(0.0, p))
            records.append({"coin": it["coin"], "ts": it["ts"],
                            "bucket": list(it["bucket"]), "p": p, "y": it["y"]})
            if it["y"] is not None:
                train.append((b, it["y"]))
        start = block_end

    scored = [r for r in records if r["y"] is not None]
    n_predictions = len(records)
    n_outcomes = len(scored)

    bins = []
    if n_outcomes:
        ordered = sorted(scored, key=lambda r: r["p"])
        k = min(CAL_BUCKETS, n_outcomes)
        for b in range(k):
            lo_i = b * n_outcomes // k
            hi_i = (b + 1) * n_outcomes // k
            seg = ordered[lo_i:hi_i]
            if not seg:
                continue
            n_hit = sum(1 for r in seg if r["y"] == 1)
            bins.append({"lo": seg[0]["p"], "hi": seg[-1]["p"],
                         "n_pred": len(seg), "n_hit": n_hit,
                         "observed": n_hit / len(seg)})

    brier = None
    slope = None
    reason = None
    if n_outcomes:
        brier = sum((r["p"] - r["y"]) ** 2 for r in scored) / n_outcomes
        raw = S.calibration_slope([r["p"] for r in scored],
                                  [r["y"] for r in scored])
        if raw is not None and math.isfinite(raw):
            slope = raw
        else:
            reason = "calibration slope undefined (fewer than 3 scored " \
                     "predictions or no variation in the predictions)"
    else:
        reason = ("no scored predictions: fewer than min_train labelled "
                  "events" if n_predictions == 0 else
                  "no scored predictions: no resolvable labels")

    return {
        "version": INFERENCE_VERSION,
        "n_events": len(sel),
        "n_usable": n,
        "n_untradeable": n_untradeable,
        "n_predictions": n_predictions,
        "n_outcomes": n_outcomes,
        "bins": bins,
        "brier": brier,
        "slope": slope,
        "reason": reason,
        "records": records,
        "min_train": min_train,
        "step": step,
        "n_train": len(train),
        "bucket_scheme": "time-of-day (6h) x liquidity tier",
        "shrinkage": SHRINKAGE,
    }


# ---------------------------------------------------------------------------
# 2. uncertainty: day cluster AND coin cluster
# ---------------------------------------------------------------------------
def _coin_blocks(values, coins):
    groups = {}
    for v, c in zip(values, coins):
        groups.setdefault(c, []).append(v)
    return groups


def _coin_bootstrap(values, coins, B, seed):
    """Coin-block bootstrap: resample whole COINS with replacement.

    Every row of a resampled coin comes with it, so the resampled mean is
    the ratio estimator over the pooled rows -- the coin-cluster analogue
    of ``stats.day_cluster_mean``'s day-block bootstrap.
    """
    groups = _coin_blocks(values, coins)
    keys = sorted(groups)
    rng = random.Random(seed)
    means = []
    for _ in range(B):
        s = 0.0
        n = 0
        for _ in range(len(keys)):
            vals = groups[keys[rng.randrange(len(keys))]]
            s += sum(vals)
            n += len(vals)
        if n:
            means.append(s / n)
    means.sort()
    if not means:
        return None, None
    lo = means[int(0.025 * (len(means) - 1))]
    hi = means[int(0.975 * (len(means) - 1))]
    return lo, hi


def _coin_t(values, coins):
    """Studentized mean over COIN clusters.

    The coin-cluster analogue of ``stats.day_cluster_t``: the same
    linearized ratio estimator, applied to coin sums and counts.
    """
    groups = {}
    for v, c in zip(values, coins):
        s, n = groups.get(c, (0.0, 0))
        groups[c] = (s + v, n + 1)
    C = len(groups)
    if C < 3:
        return None
    m = sum(v for v in values) / len(values)
    resid = []
    counts = []
    for c in groups:
        s, n = groups[c]
        resid.append(s - m * n)
        counts.append(n)
    nbar = sum(counts) / C
    var = sum(r * r for r in resid) / (C - 1) / (nbar * nbar) / C
    return (m / math.sqrt(var)) if var > 0 else 0.0


def uncertainty(values, days=None, coins=None, *, B=2000, seed=1) -> dict:
    """Day-cluster AND coin-cluster uncertainty for the same numbers.

    ``values``  the per-event numbers
    ``days``    the day label of each value (None -> no day interval)
    ``coins``   the coin label of each value (None -> no coin interval)

    Both intervals are reported separately.  An interval is None (with a
    reason) when its cluster count is below three, and also when the
    bootstrap produced a degenerate zero-width interval -- a zero-width
    interval is not evidence of certainty.
    """
    n = len(values)
    reasons = []
    out = {"n": n, "mean": _mean(values),
           "ci_lo_day": None, "ci_hi_day": None,
           "ci_lo_coin": None, "ci_hi_coin": None,
           "t_day": None, "t_coin": None,
           "clusters": {"day": 0, "coin": 0},
           "reasons": reasons}
    if n == 0:
        reasons.append("no-values")
        return out

    # ---- day cluster (house rule) ----
    if days is None or len(days) != n:
        reasons.append("no-day-labels")
    else:
        D = len(set(days))
        out["clusters"]["day"] = D
        if D < MIN_CLUSTERS:
            reasons.append(f"day-clusters<{MIN_CLUSTERS}")
        else:
            _m, lo, hi, _D = S.day_cluster_mean(list(values), list(days),
                                                 B=B, seed=seed)
            out["t_day"] = S.day_cluster_t(list(values), list(days))
            if lo is not None and hi is not None and lo < hi:
                out["ci_lo_day"], out["ci_hi_day"] = lo, hi
            else:
                reasons.append("day-interval-degenerate")

    # ---- coin cluster ----
    if coins is None or len(coins) != n:
        reasons.append("no-coin-labels")
    else:
        C = len(set(coins))
        out["clusters"]["coin"] = C
        if C < MIN_CLUSTERS:
            reasons.append(f"coin-clusters<{MIN_CLUSTERS}")
        else:
            lo, hi = _coin_bootstrap(list(values), list(coins), B, seed)
            out["t_coin"] = _coin_t(list(values), list(coins))
            if lo is not None and hi is not None and lo < hi:
                out["ci_lo_coin"], out["ci_hi_coin"] = lo, hi
            else:
                reasons.append("coin-interval-degenerate")
    return out


# ---------------------------------------------------------------------------
# 3. baselines
# ---------------------------------------------------------------------------
def _label_of(ev, rule, geom):
    """Barrier label of an event under a rule's trade side."""
    side = _trade_side(ev, rule)
    cause, _gross, _off = CO.label_slot(ev, geom, side)
    return cause


def _base_rate(causes):
    """(rate, n_resolved, n_terminal) of the target-hit label.

    ``rate`` is TP over the RESOLVED labels (TP + SL + TIME); the
    terminal hit rate (TP / (TP + SL)) is reported next to it.
    """
    tp = sum(1 for c in causes if c == "TP")
    sl = sum(1 for c in causes if c == "SL")
    tm = sum(1 for c in causes if c == "TIME")
    resolved = tp + sl + tm
    terminal = tp + sl
    return {
        "rate": (tp / resolved) if resolved else None,
        "n": resolved,
        "n_terminal": terminal,
        "terminal_rate": (tp / terminal) if terminal else None,
    }


def baselines(events, rule, geom, window, *, cfg=DEFAULT, placebos=200,
              seed=7) -> dict:
    """Three baselines on the same event set, each with its denominator.

    1. ``unconditional``           the base rate over ALL labelled events
                                   in the window -- no rule, no condition.
    2. ``rule_without_features``   the base rate over the RULE's own
                                   events -- the rule's selection is the
                                   only conditioning; no extra feature
                                   gate is applied.
    3. ``matched_placebo``         ``placebos`` synthetic event sets
                                   matched on (coin, time-of-day bucket,
                                   liquidity tier) but taken from a
                                   DIFFERENT date, so the marginal
                                   distribution is preserved and the
                                   signal is not.

    The placebo is a donor resample: the platform's event objects carry
    pre-computed labels but no bar path, so a synthetic event time cannot
    be re-labelled here.  Each placebo event therefore takes the label of
    a MATCHED event from another date (same coin, same time-of-day bucket,
    same liquidity tier).  The number of same-day fallbacks (no donor on
    another date) is reported, and the placebo p-value is the fraction of
    placebo rates at least as extreme as the observed rule rate.
    """
    lo, hi = window
    sel = R._select(events, rule, lo, hi)
    all_win = [ev for ev in events if lo <= ev.day < hi]

    sel_causes = [_label_of(ev, rule, geom) for ev in sel]
    win_causes = [_label_of(ev, rule, geom) for ev in all_win]
    uncond = _base_rate(win_causes)
    rule_base = _base_rate(sel_causes)
    sel_base = _base_rate(sel_causes)

    out = {
        "version": INFERENCE_VERSION,
        "n_events": len(all_win),
        "n_selected": len(sel),
        "unconditional": dict(uncond, denominator=(
            "all labelled events in the window (no rule, no feature gate)")),
        "rule_without_features": dict(rule_base, denominator=(
            "rule-selected labelled events in the window (no feature gate)")),
        "unconditional_on_rule_events": dict(sel_base, denominator=(
            "rule-selected labelled events in the window (the rule's own "
            "selection is the only conditioning in this workstream)")),
    }

    # ---- matched placebo ----
    pools = {}
    for ev, cause in zip(all_win, win_causes):
        if cause is None:
            continue
        key = (ev.coin, _tod_bucket(ev.ts),
               _liq_bucket(cfg, getattr(ev, "qv24", None)))
        pools.setdefault(key, []).append((int(ev.day), cause))
    rng = random.Random(seed)
    rates = []
    same_day = 0
    donors_used = 0
    for _ in range(placebos):
        hits = 0
        resolved = 0
        for ev, cause in zip(sel, sel_causes):
            if cause is None:
                continue
            key = (ev.coin, _tod_bucket(ev.ts),
                   _liq_bucket(cfg, getattr(ev, "qv24", None)))
            pool = pools.get(key)
            if not pool:
                continue
            other = [p for p in pool if p[0] != int(ev.day)]
            if other:
                donors_used += 1
                pick = other[rng.randrange(len(other))]
            else:
                same_day += 1
                pick = pool[rng.randrange(len(pool))]
            resolved += 1
            if pick[1] == "TP":
                hits += 1
        if resolved:
            rates.append(hits / resolved)
    rates.sort()
    observed = rule_base["rate"]
    p_value = None
    if rates and observed is not None:
        p_value = sum(1 for r in rates if r >= observed) / len(rates)
    out["matched_placebo"] = {
        "mean": _mean(rates),
        "sd": _sd(rates),
        "p05": _percentile(rates, 0.05),
        "p95": _percentile(rates, 0.95),
        "min": rates[0] if rates else None,
        "max": rates[-1] if rates else None,
        "n_placebos": len(rates),
        "n_per_placebo": len(sel),
        "observed": observed,
        "p_value": p_value,
        "n_same_day_fallback": same_day,
        "n_cross_date_donors": donors_used,
        "denominator": ("labelled placebo events matched on (coin, "
                        "time-of-day bucket, liquidity tier) and taken "
                        "from a different date"),
    }
    return out
