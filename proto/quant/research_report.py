"""Sliced research results with explicit denominators (workstream C).

``slice_results`` takes the events of one (rule, geometry) over one day
window and returns a report that can be read without re-deriving anything:

  * the **overall** block: ``n``, the mean net R, its day-cluster bootstrap
    interval and the coin / day counts;
  * a **coin** slice: the best and the worst coins by mean, each with its
    own ``n``;
  * a **period** slice: the window cut into five equal chronological
    sub-periods;
  * a **volatility / regime** slice: the events grouped by their trailing
    volatility tercile.  THESE LABELS ARE NOT COMPARABLE with the six-state
    codes of ``proto.quant.estimators.regime``: that measure needs the
    market-factor return series, which the event stream does not carry, so
    this report slices by VOLATILITY only and labels the groups
    ``low-vol`` / ``mid-vol`` / ``high-vol`` / ``unclassified``;
  * a **liquidity** slice: the events grouped by the ``CostModel``
    slippage tiers, using each event's trailing-24h quote volume.

DENOMINATORS ARE NOT OPTIONAL
------------------------------
Every cell reports ``n`` (the metric's denominator), ``n_days`` and
``n_coins``.  A cell with fewer than ``min_cell`` observations is labelled
``{"status": "insufficient", "n": n, "min_cell": min_cell}`` and reports NO
mean at all -- a mean from a tiny cell is noise dressed as a finding.

THE SEALED HOLDOUT
------------------
The holdout is never evaluated here.  A window that reaches into the
holdout range is REFUSED with a reason; nothing is computed for it.  The
holdout is spent exactly once, by ``proto.quant.research.holdout_once``,
under its own one-shot lock, after validation kept a config.  This module
has no write path at all: it never writes ``holdout_evaluated.json`` and
never touches ``data/``.

The leakage check is reused from the evaluation harness
(``proto.quant.eval_harness.check_no_leakage``): a report over a window
whose events would leak across a split boundary is refused.

Standard library only.
"""

import os
from collections import deque, namedtuple

from . import barrier as B
from . import prereg as _prereg
from . import research as R
from . import stats as S
from .config import DEFAULT, QuantConfig, RuleParams, Geometry, grid_geometries
from .eval_harness import EvalSplits, check_no_leakage

RESEARCH_REPORT_VERSION = "research-report-v1"

#: Bootstrap resamples for the day-cluster intervals.
BOOTSTRAP_B = 1000

#: How many coins the top / bottom lists carry.
COIN_SLICE = 5

#: How many chronological sub-periods the window is cut into.
PERIODS = 5

#: Trailing calibration window (days) of the volatility terciles and the
#: minimum number of trailing observations needed to classify an event.
REGIME_CALIB_DAYS = 7.0
REGIME_MIN_CALIB = 4

#: The code that produces a report (hashed into the report as evidence).
CODE_FILES = ("proto/quant/prereg.py",
              "proto/quant/research_report.py",
              "proto/quant/research.py",
              "proto/quant/config.py",
              "proto/quant/barrier.py",
              "proto/quant/stats.py",
              "proto/quant/eval_harness.py",
              "proto/quant/cost_attribution.py",
              "proto/quant/feature_store.py",
              "proto/quant/stream.py",
              "proto/quant/bars.py")

Row = namedtuple("Row", "value day day_f coin sigma qv24")


# --------------------------------------------------------------------- utils


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def _fmt_qv(value):
    """30000000.0 -> '30M'."""
    v = float(value)
    if v >= 1e9:
        return f"{v / 1e9:g}B"
    if v >= 1e6:
        return f"{v / 1e6:g}M"
    if v >= 1e3:
        return f"{v / 1e3:g}k"
    return f"{v:g}"


def _geom_index(geom):
    """The index of ``geom`` in the declared grid (the events' slot order)."""
    for i, g in enumerate(grid_geometries()):
        if (g.horizon_bars, g.k_stop, g.r_tp) == \
                (geom.horizon_bars, geom.k_stop, geom.r_tp):
            return i
    raise ValueError(
        "geometry "
        f"{getattr(geom, 'key', lambda: geom)()} is not in the declared grid; "
        "the stored outcomes only exist for grid geometries")


def _cell(rows, min_cell, seed, bootstrap):
    """One report cell: denominators plus a mean and its interval."""
    n = len(rows)
    days = sorted({r.day for r in rows})
    n_coins = len({r.coin for r in rows})
    if n < min_cell:
        return {"status": "insufficient", "n": n, "n_days": len(days),
                "n_coins": n_coins, "min_cell": min_cell}
    values = [r.value for r in rows]
    day_ids = [r.day for r in rows]
    mean, lo, hi, n_days = S.day_cluster_mean(values, day_ids, B=bootstrap,
                                              seed=seed)
    return {"status": "ok", "n": n, "n_days": n_days, "n_coins": n_coins,
            "mean": mean, "ci_lo": lo, "ci_hi": hi,
            "t": S.day_cluster_t(values, day_ids)}


# --------------------------------------------------------------- group setup


def _default_liquidity_groups(cfg: QuantConfig):
    """Liquidity groups from the ``CostModel`` slippage tiers."""
    tiers = sorted(cfg.cost.slip_tiers, key=lambda t: -float(t[0]))
    groups = []
    for i, (floor, _slip) in enumerate(tiers):
        upper = float(tiers[i - 1][0]) if i > 0 else None
        label = (f"qv>={_fmt_qv(floor)}" if upper is None else
                 f"{_fmt_qv(upper)}>qv>={_fmt_qv(floor)}")
        groups.append({"label": label, "min_qv24": float(floor),
                       "max_qv24": upper})
    if tiers:
        groups.append({"label": f"qv<{_fmt_qv(tiers[-1][0])}",
                       "min_qv24": None, "max_qv24": float(tiers[-1][0])})
    return groups


def _liquidity_groups(spec, cfg: QuantConfig):
    """Parse the ``liquidity_groups`` override.

    ``None`` uses the ``CostModel`` slip tiers.  Otherwise a mapping
    ``{label: min_qv24}`` (or a sequence of ``(label, min_qv24)`` pairs) is
    used; ``min_qv24`` may be ``None`` for the catch-all group below the
    lowest floor.
    """
    if spec is None:
        return _default_liquidity_groups(cfg)
    if isinstance(spec, dict):
        items = list(spec.items())
    else:
        items = []
        for entry in spec:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise ValueError("liquidity_groups entries must be "
                                 "(label, min_qv24) pairs")
            items.append((entry[0], entry[1]))
    groups = []
    for label, floor in items:
        groups.append({"label": str(label),
                       "min_qv24": None if floor is None else float(floor),
                       "max_qv24": None})
    ordered = sorted(
        [g for g in groups if g["min_qv24"] is not None],
        key=lambda g: -g["min_qv24"])
    catch_all = [g for g in groups if g["min_qv24"] is None]
    ordered.extend(catch_all)
    for i, g in enumerate(ordered):
        if i + 1 < len(ordered):
            g["max_qv24"] = ordered[i + 1]["min_qv24"]
    return ordered


def _default_regime_groups():
    return [{"label": "low-vol", "tercile": "low"},
            {"label": "mid-vol", "tercile": "mid"},
            {"label": "high-vol", "tercile": "high"}]


def _regime_groups(spec):
    """Parse the ``regime_groups`` override.

    ``None`` uses the three trailing volatility terciles.  Otherwise a
    mapping ``{label: tercile}`` (or a sequence of ``(label, tercile)``
    pairs) is used, where the tercile is ``low``, ``mid`` or ``high``.
    The ``unclassified`` bucket is always present so that no event is
    silently dropped.
    """
    if spec is None:
        groups = _default_regime_groups()
    else:
        if isinstance(spec, dict):
            items = list(spec.items())
        else:
            items = []
            for entry in spec:
                if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                    raise ValueError("regime_groups entries must be "
                                     "(label, tercile) pairs")
                items.append((entry[0], entry[1]))
        order = {"low": 0, "mid": 1, "high": 2}
        parsed = []
        for label, tercile in items:
            key = str(tercile).lower()
            if key not in order:
                raise ValueError(f"unknown regime tercile: {tercile!r}")
            parsed.append({"label": str(label), "tercile": key})
        parsed.sort(key=lambda g: order[g["tercile"]])
        groups = parsed
    groups = list(groups) + [{"label": "unclassified", "tercile": None}]
    return groups


def _regime_thresholds(rows, calib_days=REGIME_CALIB_DAYS,
                        min_calib=REGIME_MIN_CALIB):
    """Trailing volatility terciles, calibrated on PRECEDING days only.

    This is the trailing-window classifier used by
    ``proto.quant.estimators.regime`` (a tercile of the trailing
    distribution, calibrated on the windows before the current one, never
    on the current one), applied to the events' own trailing sigma instead
    of a market-factor return series: the event stream does not carry the
    market factor, so the regime is read off each event's own trailing
    volatility.  The trend half of the 6-state ``regime`` label needs the
    market factor and is therefore NOT reproduced here -- this report
    slices by VOLATILITY, which is what the trend-free tercile supports.

    NOT COMPARABLE with ``measures.regime``: the groups here are volatility
    terciles of the event's own sigma, while the regime codes are a
    trend x volatility-tercile label of the market factor.  A group named
    ``high-vol`` here is NOT the ``*/high-vol`` state of the regime
    measure, and the two must never be read as the same thing.

    Returns ``{day: (q_lo, q_hi)}``.
    """
    by_day = {}
    for r in rows:
        by_day.setdefault(r.day, []).append(r.sigma)
    thresholds = {}
    window = deque()
    for day in sorted(by_day):
        while window and window[0][0] < day - calib_days:
            window.popleft()
        values = []
        for _day, sigmas in window:
            values.extend(sigmas)
        if len(values) >= min_calib:
            values.sort()
            n = len(values)
            q_lo = values[min(n - 1, int(n / 3.0))]
            q_hi = values[min(n - 1, int(2.0 * n / 3.0))]
            thresholds[day] = (q_lo, q_hi)
        window.append((day, by_day[day]))
    return thresholds


def _classify_regime(rows, groups):
    """Label every row with a regime group label (same order as ``rows``)."""
    thresholds = _regime_thresholds(rows)
    by_tercile = {g["tercile"]: g["label"] for g in groups if g["tercile"]}
    unclassified = next((g["label"] for g in groups if not g["tercile"]),
                        "unclassified")
    labels = []
    for r in rows:
        th = thresholds.get(r.day)
        if th is None:
            labels.append(unclassified)
        elif r.sigma <= th[0]:
            labels.append(by_tercile.get("low", unclassified))
        elif r.sigma >= th[1]:
            labels.append(by_tercile.get("high", unclassified))
        else:
            labels.append(by_tercile.get("mid", unclassified))
    return labels


def _liquidity_label(qv24, groups):
    for g in groups:
        lo, hi = g["min_qv24"], g["max_qv24"]
        if lo is not None and qv24 < lo:
            continue
        if hi is not None and qv24 >= hi:
            continue
        return g["label"]
    return groups[-1]["label"] if groups else "unknown"


# ------------------------------------------------------------------- report


def attach_evidence(report: dict, *, data_hash=None, code_hash=None) -> dict:
    """Return a copy of ``report`` carrying the evidence hashes.

    ``slice_results`` fills in the config hash and the code hash of the
    reporting code; the data hash is the caller's (it depends on which
    archives the events came from), so it is attached here.
    """
    out = dict(report)
    if data_hash is not None:
        out["data_hash"] = data_hash
    if code_hash is not None:
        out["code_hash"] = code_hash
    return out


def _refused(reason, window, cfg, min_cell=None, seed=None, extra=None):
    report = {
        "report_version": RESEARCH_REPORT_VERSION,
        "status": "refused",
        "reason": reason,
        "window": {"lo": window[0], "hi": window[1]},
        "config_hash": cfg.config_hash(),
        "data_hash": None,
        "code_hash": _code_hash(),
        "min_cell": min_cell,
        "seed": seed,
        "overall": None,
        "slices": {},
        "n_events_selected": 0,
        "n_untradeable": 0,
        "leakage_violations": [],
    }
    if extra:
        report.update(extra)
    return report


def slice_results(events, rule: RuleParams, geom: Geometry, window, *,
                   cfg: QuantConfig = DEFAULT, liquidity_groups=None,
                   regime_groups=None, min_cell=30, seed=1) -> dict:
    """Slice one (rule, geometry) evaluation over one day window.

    ``events`` are the collected research events (``research.Event``),
    ``rule`` a ``RuleParams``, ``geom`` a ``Geometry`` from the declared
    grid and ``window`` a ``(lo, hi)`` pair of day coordinates.

    Returns a report dict (see the module docstring).  A window that
    reaches into the sealed holdout, or one whose events would leak across
    a split boundary, is REFUSED with a reason instead of being computed.
    """
    if not isinstance(window, (list, tuple)) or len(window) != 2:
        raise ValueError("window must be a (lo, hi) pair of day coordinates")
    lo, hi = float(window[0]), float(window[1])
    if not lo < hi:
        raise ValueError(f"window needs lo < hi, got {lo} >= {hi}")
    events = list(events or [])

    geom_idx = _geom_index(geom)

    # --- the splits (the holdout START does not depend on the archive span)
    last_day = max((float(getattr(ev, "day", 0.0)) for ev in events),
                   default=0.0)
    n_days = last_day + 1.0
    bounds = cfg.splits.bounds(n_days)
    splits = EvalSplits(n_days=n_days, dev=bounds["dev"], val=bounds["val"],
                        holdout=bounds["holdout"],
                        embargo_days=cfg.splits.embargo_days,
                        first_event_day=cfg.splits.first_event_day)
    holdout_start = bounds["holdout"][0]

    # --- the holdout is refused, never evaluated
    if hi > holdout_start:
        return _refused(
            f"holdout-sealed: the window [{lo:g}, {hi:g}) reaches into the "
            f"sealed holdout (starts at {holdout_start:g}); the one-shot "
            "holdout is never evaluated by this workflow",
            (lo, hi), cfg, min_cell, seed,
            {"holdout_start": holdout_start,
             "splits": {"dev": list(splits.dev), "val": list(splits.val),
                        "holdout": list(splits.holdout),
                        "embargo_days": splits.embargo_days}})

    # --- leakage is checked with the evaluation harness' own rule
    violations = check_no_leakage(events, splits)
    if violations:
        return _refused(
            "leakage-detected: " + "; ".join(violations[:5]),
            (lo, hi), cfg, min_cell, seed,
            {"leakage_violations": violations,
             "holdout_start": holdout_start})

    # --- select + value the events (friction only, as the harness does)
    selected = R._select(events, rule, lo, hi)
    rows = []
    kinds = [0, 0, 0]
    untradeable = 0
    for ev in selected:
        side = ev.side if rule.policy == "cont" else -ev.side
        k = geom_idx * 2 + (0 if side > 0 else 1)
        cost = cfg.cost.round_trip(ev.qv24)
        if cost is None:
            untradeable += 1              # below the tradeable volume floor
            continue
        dd = B.stop_distance(ev.sigma, geom.horizon_bars, geom.k_stop)
        rows.append(Row(value=B.net_r(ev.gross[k], dd, cost),
                         day=int(ev.day), day_f=float(ev.day), coin=ev.coin,
                         sigma=float(ev.sigma), qv24=float(ev.qv24)))
        kinds[ev.kind[k]] += 1

    overall = _cell(rows, min_cell, seed, BOOTSTRAP_B)
    if overall.get("status") == "ok":
        n = max(1, overall["n"])
        overall.update({"tp": kinds[0] / n, "sl": kinds[1] / n,
                        "time": kinds[2] / n})

    slices = {
        "coin": _coin_slice(rows, min_cell, seed, BOOTSTRAP_B),
        "period": _period_slice(rows, (lo, hi), min_cell, seed, BOOTSTRAP_B),
        "regime": _regime_slice(rows, regime_groups, min_cell, seed,
                                 BOOTSTRAP_B),
        "liquidity": _liquidity_slice(rows, liquidity_groups, cfg, min_cell,
                                      seed, BOOTSTRAP_B),
    }

    return {
        "report_version": RESEARCH_REPORT_VERSION,
        "status": "ok",
        "reason": None,
        "window": {"lo": lo, "hi": hi},
        "rule": {"rule_id": R.rule_name(rule), "family": rule.family,
                 "policy": rule.policy, "rvol_min": rule.rvol_min,
                 "cusum_h": rule.cusum_h},
        "geometry": {"key": geom.key(), "horizon_bars": geom.horizon_bars,
                     "k_stop": geom.k_stop, "r_tp": geom.r_tp,
                     "grid_index": geom_idx},
        "config_hash": cfg.config_hash(),
        "data_hash": None,
        "code_hash": _code_hash(),
        "min_cell": min_cell,
        "seed": seed,
        "bootstrap": {"method": "day-cluster block bootstrap",
                      "B": BOOTSTRAP_B, "unit": "trading day"},
        "splits": {"dev": list(splits.dev), "val": list(splits.val),
                   "holdout": list(splits.holdout),
                   "embargo_days": splits.embargo_days,
                   "holdout_start": holdout_start},
        "n_events_selected": len(selected),
        "n_untradeable": untradeable,
        "leakage_violations": [],
        "overall": overall,
        "slices": slices,
    }


def _code_hash():
    """The digest of the code that produces the report (evidence)."""
    try:
        return _prereg.code_hash(_repo_root(), list(CODE_FILES))
    except (FileNotFoundError, ValueError):
        return None


def _coin_slice(rows, min_cell, seed, bootstrap):
    """The best and the worst coins by mean net R (descriptive ranking)."""
    by_coin = {}
    for r in rows:
        by_coin.setdefault(r.coin, []).append(r)
    ranked = []
    for coin, coin_rows in by_coin.items():
        mean = sum(x.value for x in coin_rows) / len(coin_rows)
        ranked.append((mean, coin, coin_rows))
    ranked.sort(key=lambda t: (-t[0], t[1]))
    eligible = [t for t in ranked if len(t[2]) >= min_cell]
    top = [_coin_entry(t, min_cell, seed, bootstrap) for t in eligible[:COIN_SLICE]]
    bottom = [_coin_entry(t, min_cell, seed, bootstrap)
              for t in reversed(eligible[-COIN_SLICE:])]
    return {"top": top, "bottom": bottom,
            "n_coins": len(by_coin),
            "n_coins_ranked": len(eligible),
            "n_coins_below_min_cell": len(ranked) - len(eligible),
            "note": "ranking is descriptive; it is not a selection criterion"}


def _coin_entry(entry, min_cell, seed, bootstrap):
    mean, coin, coin_rows = entry
    cell = _cell(coin_rows, min_cell, seed, bootstrap)
    cell["coin"] = coin
    return cell


def _period_slice(rows, window, min_cell, seed, bootstrap):
    """The window cut into five equal chronological sub-periods."""
    lo, hi = window
    span = (hi - lo) / PERIODS
    out = []
    for i in range(PERIODS):
        sub_lo = lo + i * span
        sub_hi = lo + (i + 1) * span
        sub = [r for r in rows if sub_lo <= r.day_f < sub_hi]
        cell = _cell(sub, min_cell, seed, bootstrap)
        cell.update({"label": f"p{i + 1}", "lo": sub_lo, "hi": sub_hi})
        out.append(cell)
    return out


def _regime_slice(rows, regime_groups, min_cell, seed, bootstrap):
    """The events grouped by their trailing volatility tercile."""
    groups = _regime_groups(regime_groups)
    labels = _classify_regime(rows, groups)
    buckets = {g["label"]: [] for g in groups}
    for r, label in zip(rows, labels):
        buckets.setdefault(label, []).append(r)
    out = []
    for g in groups:
        sub = buckets.get(g["label"], [])
        cell = _cell(sub, min_cell, seed, bootstrap)
        cell.update({"label": g["label"], "tercile": g["tercile"],
                     "n_events": len(sub)})
        out.append(cell)
    return out


def _liquidity_slice(rows, liquidity_groups, cfg, min_cell, seed, bootstrap):
    """The events grouped by the slippage tiers (trailing-24h quote volume)."""
    groups = _liquidity_groups(liquidity_groups, cfg)
    buckets = {g["label"]: [] for g in groups}
    for r in rows:
        buckets.setdefault(_liquidity_label(r.qv24, groups), []).append(r)
    out = []
    for g in groups:
        sub = buckets.get(g["label"], [])
        cell = _cell(sub, min_cell, seed, bootstrap)
        cell.update({"label": g["label"], "min_qv24": g["min_qv24"],
                     "max_qv24": g["max_qv24"], "n_events": len(sub)})
        out.append(cell)
    return out
