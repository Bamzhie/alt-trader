"""WS-B: competing outcomes for the research harness.

A trade has more than two possible endings, and a measurement layer that
only knows "target / stop / time" silently answers a different question
than the one a reader asks.  This module makes the endings EXPLICIT and
mutually exclusive:

    TP          the target barrier was hit first
    SL          the stop barrier was hit first
    TIME        the time barrier expired
    INVALIDATED the thesis was invalidated before any barrier
    CENSORED    the outcome is NOT observable (sample ended / data lost)

Design rules (inherited, never re-implemented here):

  * **Barrier labelling is reused, not rewritten.**  The triple-barrier
    labels (gross return, kind, exit offset) were produced by
    ``proto.quant.barrier.label_path`` when the events were collected; this
    module only READS those per-(geometry, side) slots.  The conservative
    ordering therefore survives untouched: a bar that touches both the stop
    and the target resolves as **SL**, and the fill bar's stop is terminal
    (a target touch on that bar is ignored).
  * **Censoring is real.**  An event whose horizon extends past the declared
    ``sample_end_ts`` is CENSORED, never TIME.  An event whose label slot
    cannot be resolved (a missing bar run) is CENSORED with a data-loss
    reason.  Censored events are COUNTED and excluded from the resolved
    denominator; they are never dropped and never guessed.
  * **Invalidation precedes the barriers.**  When the caller's
    ``invalidation(ev)`` callable says the thesis broke, the cause is
    INVALIDATED -- not TP/SL/TIME.  The return is measured to the
    invalidation point, which the caller supplies on the event
    (``invalid_exit_offset`` for the exit bar and ``invalid_gross`` /
    ``invalid_exit_price`` for the return).  Without those inputs the
    outcome carries ``gross_log=None`` and a reason -- a return is NEVER
    fabricated.
  * **Friction, funding and other costs stay separately attributable.**
    ``gross_log``, ``friction``, ``funding`` and ``net_r`` are four
    separate fields.  ``net_r = (gross_log - friction - funding) / dd`` and
    it is None whenever any input is None -- in particular when the funding
    cost cannot be attributed (missing archive / unattributable funding
    event).  A friction-only number is never presented as net: the
    friction-only view is available under the explicit key
    ``"friction_net_r"``, and every distribution reports its denominator.
  * **The static shares are not the only view.**  ``cause_incidence``
    turns the same taxonomy into the time-to-event view (cumulative
    incidence by cause, via ``stats.aalen_johansen``): a cause that
    resolves early and one that resolves late have the same share but a
    very different incidence curve.
  * **No promotion.**  This module measures; it never promotes a rule,
    never enables ACTIONABLE output and never touches the one-shot
    holdout.

Stdlib only.
"""
import math
from dataclasses import dataclass

from . import barrier as B
from . import research as R
from . import stats as S
from .config import BAR_S, DEFAULT, grid_geometries
from .cost_attribution import funding_cost
from .eval_harness import trade_side

OUTCOME_VERSION = "outcomes-v1"

#: The mutually exclusive, exhaustive cause taxonomy.
CAUSES = ("TP", "SL", "TIME", "INVALIDATED", "CENSORED")

#: ``research.Event.kind`` encodes the barrier label as 0/1/2.  The string
#: names are accepted too, so a hand-built event does not have to encode
#: the numeric slots.
_KIND_TO_CAUSE = {0: "TP", 1: "SL", 2: "TIME",
                  "TP": "TP", "SL": "SL", "TIME": "TIME"}


@dataclass(frozen=True)
class Outcome:
    """One resolved (or censored) outcome of one event.

    event_id:   stable synthetic id of the source event
    coin:       coin of the event
    ts:         signal bar open ts
    side:       the TRADE side (+1 long / -1 short)
    cause:      one of ``CAUSES``
    exit_ts:    exit timestamp (None when the outcome is not observable)
    gross_log:  log return BEFORE costs (None when censored or unmeasured)
    friction:   round-trip friction as a fraction of notional
    funding:    funding cost as a fraction of notional (None when it cannot
                be attributed -- never zero-filled)
    net_r:      (gross_log - friction - funding) / dd; None when any input
                is None
    dd:         stop distance in log units (the R unit)
    reason:     machine-readable cause, e.g. "censored:end-of-sample",
                "funding:incomplete:settlement-boundaries-missing",
                "invalidation:no-exit"
    """
    event_id: str
    coin: str
    ts: int
    side: int
    cause: str
    exit_ts: int | None
    gross_log: float | None
    friction: float | None
    funding: float | None
    net_r: float | None
    dd: float | None
    reason: str | None


# ---------------------------------------------------------------------------
# slot access (the events carry per-(geometry, side) label arrays)
# ---------------------------------------------------------------------------
def _geom_index(geom) -> int:
    """Index of ``geom`` in the pre-declared geometry grid.

    ``research.Event`` stores the label of every (geometry, side) slot in
    the order of ``config.grid_geometries()``, so the slot can only be
    addressed through that grid.  A geometry outside the grid has no
    addressable slot and is refused loudly rather than mislabelled.
    """
    for i, g in enumerate(grid_geometries()):
        if (g.horizon_bars == geom.horizon_bars
                and g.k_stop == geom.k_stop and g.r_tp == geom.r_tp):
            return i
    raise ValueError(
        f"geometry {geom!r} is not in the pre-declared grid; the per-slot "
        "label arrays cannot be addressed")


def slot_index(geom, side) -> int:
    """Index of the (geometry, side) label slot inside an event."""
    gi = _geom_index(geom)
    return gi * 2 + (0 if side > 0 else 1)


def _slot_get(arr, k):
    """Value of slot ``k``; None when the array or the slot is missing.

    A list/tuple of per-slot values is indexed; an ``array.array`` is
    indexed; a SCALAR (a hand-built event that carries one gross/kind
    instead of a slot array) is taken as the value of the trade side.
    """
    if arr is None:
        return None
    if isinstance(arr, str):
        return arr               # a scalar label ("TP" / "SL" / "TIME")
    if isinstance(arr, (list, tuple)):
        try:
            return arr[k]
        except (IndexError, TypeError):
            return None
    try:
        return arr[k]
    except (IndexError, KeyError, TypeError):
        pass
    if isinstance(arr, (int, float, str)):
        return arr
    return None


def _trade_side(ev, rule):
    """The TRADE side of an event under a rule's policy.

    Reuses ``eval_harness.trade_side`` (the house convention: ``fade``
    inverts the break direction, ``cont`` keeps it) so the funding sign,
    the exit-offset slot and the outcome layer can never disagree.
    """
    if getattr(ev, "side", None) is None:
        return 1
    return trade_side(ev, rule)


def label_slot(ev, geom, side):
    """(cause, gross_log, exit_offset) from an event's barrier labels.

    The labels were produced by ``barrier.label_path`` (stop wins a
    same-bar tie; the fill bar's stop is terminal).  Returns
    ``(None, None, None)`` when the slot is unusable.
    """
    k = slot_index(geom, side)
    kind = _slot_get(getattr(ev, "kind", None), k)
    cause = _KIND_TO_CAUSE.get(kind)
    return cause, _slot_get(getattr(ev, "gross", None), k), \
        _slot_get(getattr(ev, "exit_offset", None), k)


def _event_id(ev) -> str:
    parts = [str(getattr(ev, "coin", "?")),
             str(getattr(ev, "ts", "?")),
             str(getattr(ev, "fam", "")),
             str(getattr(ev, "param", "")),
             str(getattr(ev, "side", ""))]
    return ":".join(parts)


def _join_reason(existing, extra):
    if not existing:
        return extra
    return existing + "+" + extra


def _data_loss_reason(ev, k, horizon_bars):
    """Reason string when the event's outcome cannot be observed.

    Channels (in priority order):
      * ``ev.censored`` (truthy) with an optional ``ev.censored_reason``
      * ``ev.data_loss`` (truthy; a string is used as the reason)
      * ``ev.bars_available`` smaller than the horizon -> a short run
      * an unresolvable label slot (kind / gross / exit_offset missing)
    """
    marker = getattr(ev, "censored", None)
    if marker:
        return "censored:data-loss:" + str(
            getattr(ev, "censored_reason", None) or "marked")
    marker = getattr(ev, "data_loss", None)
    if marker:
        return "censored:data-loss:" + (marker if isinstance(marker, str)
                                        else "marked")
    navail = getattr(ev, "bars_available", None)
    if navail is not None and int(navail) < horizon_bars:
        return "censored:data-loss:short-run"
    if (_slot_get(getattr(ev, "kind", None), k) is None
            or _slot_get(getattr(ev, "gross", None), k) is None):
        return "censored:data-loss:unlabelled"
    return None


def _invalid_gross(ev, k, side):
    """Gross log return measured to the invalidation point.

    The caller supplies it: ``ev.invalid_gross[k]`` (per slot) or
    ``ev.invalid_exit_price[k]`` together with ``ev.entry_price``.  None
    when neither is available -- a return is never fabricated.
    """
    g = _slot_get(getattr(ev, "invalid_gross", None), k)
    if g is not None:
        return float(g)
    px = _slot_get(getattr(ev, "invalid_exit_price", None), k)
    entry = getattr(ev, "entry_price", None)
    if px is not None and entry:
        return side * math.log(float(px) / float(entry))
    return None


# ---------------------------------------------------------------------------
# the resolver
# ---------------------------------------------------------------------------
def resolve_competing(events, rule, geom, window, cfg=DEFAULT, *,
                      funding_dir=None, settlements_dir=None,
                      invalidation=None, sample_end_ts=None) -> dict:
    """Resolve selected events into the competing-outcome taxonomy.

    ``events``  research events (see ``research.collect_events``); each
                carries ``coin``, ``ts``, ``day``, ``side``, ``gross``,
                ``kind`` and ``exit_offset`` per (geometry, side) slot,
                plus ``sigma`` and ``qv24``.
    ``rule``    the rule whose events are selected (same selection as
                ``research.eval_config``).
    ``geom``    the geometry (must be in the pre-declared grid).
    ``window``  (lo_day, hi_day) in the archive's day coordinates.
    ``invalidation``  optional callable ``invalidation(ev) -> bool``.
    ``sample_end_ts`` optional end of sample (epoch seconds).

    Returns a dict with:
      n_selected        the denominator: rule-selected, TRADEABLE events
      n_rule_selected   how many events the rule selected (before the
                        liquidity filter)
      n_untradeable     how many selected events had no cost model
      n_resolved        non-censored outcomes (n_selected - n_censored)
      n_measured        outcomes that also carry a gross return
      n_censored        outcomes that are not observable
      n_invalidated     INVALIDATED outcomes (a subset of n_resolved)
      n_trade_resolved  TP + SL + TIME
      n_net_evaluable   outcomes with a non-None net_r -- the denominator
                        of every NET number, so a smaller sample can never
                        be mistaken for the whole one
      causes            {cause: n} summing exactly to n_selected
      outcomes          the Outcome list, in selection order
      coverage          n_resolved / n_selected
    """
    lo, hi = window
    gi = _geom_index(geom)
    horizon = geom.horizon_bars
    sel = R._select(events, rule, lo, hi)
    causes = {c: 0 for c in CAUSES}
    outcomes = []
    selected = []
    n_untradeable = 0
    n_censored = 0
    n_invalidated = 0
    n_measured = 0
    n_net = 0
    funding_configured = funding_dir is not None or settlements_dir is not None
    # Repeated holding windows are memoised: the funding archives are read
    # per call (cost_attribution owns that I/O), so the cache keeps a run
    # with many events from re-reading the same archive over and over.
    funding_cache = {}

    for ev in sel:
        side = _trade_side(ev, rule)
        k = gi * 2 + (0 if side > 0 else 1)
        friction = cfg.cost.round_trip(getattr(ev, "qv24", None))
        if friction is None:
            # No cost model -> not tradeable -> not an outcome at all.
            # Counted separately so the denominator stays honest.
            n_untradeable += 1
            continue
        selected.append(ev)

        sigma = getattr(ev, "sigma", None)
        dd = (B.stop_distance(sigma, horizon, geom.k_stop)
              if sigma else None)
        gross = _slot_get(getattr(ev, "gross", None), k)
        kind = _slot_get(getattr(ev, "kind", None), k)
        off = _slot_get(getattr(ev, "exit_offset", None), k)

        exit_ts = None
        gross_log = None
        reason = None

        # 1. End of sample: the label window is not inside the sample.
        #    Conservative by construction: it is a pure function of the
        #    declared sample end and the horizon, so no bar outside the
        #    sample can influence the outcome.
        horizon_end = ev.ts + (horizon + 1) * BAR_S
        if sample_end_ts is not None and horizon_end > sample_end_ts:
            cause = "CENSORED"
            reason = "censored:end-of-sample"
        # 2. Data loss: the label never resolved.
        else:
            loss = _data_loss_reason(ev, k, horizon)
            if loss:
                cause = "CENSORED"
                reason = loss
            # 3. Invalidation precedes every barrier.
            elif invalidation is not None and invalidation(ev):
                cause = "INVALIDATED"
                inv_off = _slot_get(getattr(ev, "invalid_exit_offset", None), k)
                if inv_off is None:
                    gross_log = None
                    reason = "invalidation:no-exit"
                else:
                    gross_log = _invalid_gross(ev, k, side)
                    if gross_log is None:
                        reason = "invalidation:no-gross"
                    exit_ts = ev.ts + (1 + inv_off) * BAR_S
            # 4. The barrier label (read, never recomputed).
            else:
                cause = _KIND_TO_CAUSE.get(kind)
                if cause is None:
                    cause = "CENSORED"
                    reason = "censored:data-loss:unlabelled"
                else:
                    gross_log = gross
                    # TP/SL fill at the open of the exit bar; a TIME exit
                    # happens at the CLOSE of the last bar (the open of the
                    # next one) -- the same convention as eval_harness.
                    # An event with no stored exit offset falls back to the
                    # full-horizon close (eval_harness does the same): the
                    # funding window is then the longest possible one.
                    if off is None:
                        exit_ts = ev.ts + (horizon + 1) * BAR_S
                    else:
                        extra = 2 if cause == "TIME" else 1
                        exit_ts = ev.ts + (extra + off) * BAR_S

        # Funding: attributed only when the holding window is known.
        funding = None
        if gross_log is not None and exit_ts is not None:
            fkey = (ev.coin, ev.ts + BAR_S, exit_ts, side)
            fc = funding_cache.get(fkey)
            if fc is None:
                fc = funding_cost(ev.coin, ev.ts + BAR_S, exit_ts,
                                  funding_dir=funding_dir,
                                  settlements_dir=settlements_dir, side=side)
                funding_cache[fkey] = fc
            if fc.missing:
                reason = _join_reason(
                    reason, "funding:missing" if funding_configured
                    else "funding:not-configured")
            elif fc.incomplete or fc.cost is None:
                reason = _join_reason(
                    reason, "funding:incomplete:" + (fc.reason or "unknown"))
            else:
                funding = fc.cost

        net_r = None
        if gross_log is not None and friction is not None and funding is not None \
                and dd:
            net_r = (gross_log - friction - funding) / dd
        elif dd is None and cause != "CENSORED":
            reason = _join_reason(reason, "stop-distance:undefined")

        causes[cause] += 1
        if cause == "CENSORED":
            n_censored += 1
        else:
            if cause == "INVALIDATED":
                n_invalidated += 1
            if gross_log is not None:
                n_measured += 1
        if net_r is not None:
            n_net += 1

        outcomes.append(Outcome(
            event_id=_event_id(ev), coin=ev.coin, ts=ev.ts, side=side,
            cause=cause, exit_ts=exit_ts, gross_log=gross_log,
            friction=friction, funding=funding, net_r=net_r, dd=dd,
            reason=reason))

    n_selected = len(outcomes)
    n_resolved = n_selected - n_censored
    return {
        "version": OUTCOME_VERSION,
        "n_selected": n_selected,
        "n_rule_selected": len(sel),
        "n_untradeable": n_untradeable,
        "n_resolved": n_resolved,
        "n_measured": n_measured,
        "n_censored": n_censored,
        "n_invalidated": n_invalidated,
        "n_trade_resolved": causes["TP"] + causes["SL"] + causes["TIME"],
        "n_net_evaluable": n_net,
        "causes": causes,
        "outcomes": outcomes,
        "coverage": (n_resolved / n_selected) if n_selected else 0.0,
        "denominator": "rule-selected, tradeable events in the window",
        "window": (lo, hi),
        "geometry": geom.key() if hasattr(geom, "key") else str(geom),
        "rule": R.rule_name(rule),
        "selected_events": selected,
    }


# ---------------------------------------------------------------------------
# distributions
# ---------------------------------------------------------------------------
def friction_net_r(outcome):
    """Net-of-friction R.  EXPLICITLY friction-only -- never a net number.

    Provided so a friction-only view of a sample can be read WITHOUT it
    ever being mistaken for a funding-adjusted (net) figure: the net figure
    lives in ``Outcome.net_r`` and is None whenever funding could not be
    attributed.
    """
    g = getattr(outcome, "gross_log", None)
    f = getattr(outcome, "friction", None)
    dd = getattr(outcome, "dd", None)
    if g is None or f is None or not dd:
        return None
    return (g - f) / dd


def _outcome_value(outcome, key):
    if key == "friction_net_r":
        return friction_net_r(outcome)
    return getattr(outcome, key, None)


def _percentile(sorted_values, q):
    """Linear interpolation between order statistics.

    h = (n - 1) * q; lo = floor(h); hi = ceil(h);
    value = x[lo] + (h - lo) * (x[hi] - x[lo]).
    """
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return sorted_values[0]
    h = (n - 1) * q
    lo = int(math.floor(h))
    hi = int(math.ceil(h))
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (h - lo) * (sorted_values[hi] - sorted_values[lo])


def _mean(values):
    return (sum(values) / len(values)) if values else None


def _sd(values):
    """Sample standard deviation (n - 1 denominator)."""
    n = len(values)
    if n == 0:
        return None
    if n == 1:
        return 0.0
    m = sum(values) / n
    return math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))


def outcome_distribution(outcomes, *, key="net_r") -> dict:
    """Gross and net distribution over a set of outcomes.

    ``key`` names the field to summarise ("net_r", "gross_log",
    "friction", "funding") plus the explicit derived key
    "friction_net_r" (friction-only, never net).

    The statistics are computed over the outcomes where the key is not
    None; ``n_missing`` is the number excluded, so the denominator is
    always explicit.  Percentiles use linear interpolation between order
    statistics (see ``_percentile``).  ``hit_rate`` is the share of TP
    among TERMINAL outcomes (TP + SL) and is reported over the whole
    outcome set, with ``n_terminal`` as its denominator.
    """
    values = []
    n_missing = 0
    for o in outcomes:
        v = _outcome_value(o, key)
        if v is None:
            n_missing += 1
        else:
            values.append(v)
    values.sort()
    n = len(values)

    gross = [o.gross_log for o in outcomes
             if getattr(o, "gross_log", None) is not None]
    friction = [o.friction for o in outcomes
                if getattr(o, "friction", None) is not None]
    funding = [o.funding for o in outcomes
               if getattr(o, "funding", None) is not None]
    tp = sum(1 for o in outcomes if getattr(o, "cause", None) == "TP")
    sl = sum(1 for o in outcomes if getattr(o, "cause", None) == "SL")

    return {
        "key": key,
        "n": n,
        "n_missing": n_missing,
        "mean": _mean(values),
        "sd": _sd(values),
        "median": _percentile(values, 0.5),
        "p05": _percentile(values, 0.05),
        "p25": _percentile(values, 0.25),
        "p75": _percentile(values, 0.75),
        "p95": _percentile(values, 0.95),
        "min": values[0] if n else None,
        "max": values[-1] if n else None,
        "hit_rate": (tp / (tp + sl)) if (tp + sl) else None,
        "n_terminal": tp + sl,
        "mean_gross": _mean(gross),
        "mean_friction": _mean(friction),
        "mean_funding": _mean(funding),
        "n_gross": len(gross),
        "n_friction": len(friction),
        "n_funding": len(funding),
    }


def expected_return(outcomes, key="net_r", days=None, *, B=1500, seed=1) -> dict:
    """Expected return of a set of outcomes with an explicit denominator.

    The reported interval follows the SAME rule as the cluster intervals
    in ``inference.uncertainty``: an interval whose cluster count is
    below three is None with a reason -- it is never a thin or degenerate
    interval presented as if it were a cluster interval.

      * ``days`` supplied, aligned and with at least three distinct days:
        the interval is the day-cluster bootstrap from
        ``stats.day_cluster_mean``, the statistic is
        ``stats.day_cluster_t`` and the reported ``se`` is the matching
        analytic cluster standard error (mean / t).  ``cluster="day"``.
      * ``days`` supplied, aligned, but fewer than three distinct days:
        NO interval is reported (``ci_lo``/``ci_hi`` are None) and the
        reason says why.  The se/t fall back to the row level
        (``cluster="row"``) and the row-level interval is reported
        separately as ``row_ci_lo``/``row_ci_hi``.
      * no day labels (or labels that do not align with the evaluated
        outcomes): the plain mean with the row-level standard error and
        its normal-approximation interval; ``cluster="row"``.

    ``row_ci_lo``/``row_ci_hi`` are always present, so the row-level view
    is never lost when the cluster interval is suppressed.
    """
    values = []
    day_list = []
    for i, o in enumerate(outcomes):
        v = _outcome_value(o, key)
        if v is None:
            continue
        values.append(v)
        day_list.append(days[i] if days is not None and i < len(days) else None)
    n = len(values)
    if n == 0:
        return {"key": key, "mean": None, "n": 0, "n_missing": len(outcomes),
                "se": None, "t": None, "ci_lo": None, "ci_hi": None,
                "row_ci_lo": None, "row_ci_hi": None, "cluster": "none",
                "n_days": 0, "reason": "no-evaluable-outcomes"}
    mean = sum(values) / n
    plain_se = (_sd(values) / math.sqrt(n)) if n > 1 else 0.0
    row_t = (mean / plain_se) if plain_se > 0 else 0.0
    row_lo, row_hi = mean - 1.96 * plain_se, mean + 1.96 * plain_se

    if days is None:
        days_supplied = False
        aligned = False
    else:
        days_supplied = True
        aligned = (len(day_list) == n
                   and all(d is not None for d in day_list))

    if not days_supplied:
        se, t, lo, hi = plain_se, row_t, row_lo, row_hi
        cluster = "row"
        reason = "no day labels supplied: row-level interval"
    elif not aligned:
        se, t, lo, hi = plain_se, row_t, row_lo, row_hi
        cluster = "row"
        reason = ("day labels do not align with the evaluated outcomes: "
                  "row-level interval")
    else:
        n_days = len(set(day_list))
        if n_days < 3:
            # Too few clusters for a day-cluster interval: report none.
            se, t, lo, hi = plain_se, row_t, None, None
            cluster = "row"
            reason = (f"day-cluster interval suppressed: only {n_days} "
                      "distinct days (< 3); the se/t are row-level and the "
                      "row-level interval is row_ci_lo..row_ci_hi")
        else:
            _m, lo, hi, _D = S.day_cluster_mean(values, day_list, B=B, seed=seed)
            t = S.day_cluster_t(values, day_list)
            se = (mean / t) if (t and math.isfinite(t)) else plain_se
            cluster = "day"
            reason = (None if t and math.isfinite(t) else
                      "day-cluster t undefined: the se falls back to the "
                      "row-level one")
    return {"key": key, "mean": mean, "n": n, "n_missing": len(outcomes) - n,
            "se": se, "t": t, "ci_lo": lo, "ci_hi": hi, "cluster": cluster,
            "row_ci_lo": row_lo, "row_ci_hi": row_hi,
            "n_days": len(set(day_list)) if aligned else 0,
            "reason": reason}


# ---------------------------------------------------------------------------
# cumulative incidence (the time-to-event view of the same taxonomy)
# ---------------------------------------------------------------------------
#: Cause codes for ``stats.aalen_johansen`` (0 = right-censored).
_CAUSE_CODE = {"TP": 1, "SL": 2, "TIME": 3, "INVALIDATED": 4}

#: The causes that can OCCUR (i.e. the competing risks); CENSORED is the
#: censoring state, not a competing risk.
INCIDENCE_CAUSES = ("TP", "SL", "TIME", "INVALIDATED")


def cause_incidence(outcomes, horizon_bars):
    """Cumulative incidence by cause -- the time-to-event view.

    The static cause shares say "how often did each cause happen".  The
    cumulative incidence says "how likely is this cause to have happened
    BY bar t", which is the right view for competing risks: a cause that
    resolves early (a stop) and one that resolves late (a time exit) have
    the same share but a very different incidence curve.

    Built on ``stats.aalen_johansen`` (reused, not re-implemented):
    TP=1, SL=2, TIME=3, INVALIDATED=4, and 0 = right-censored.  A
    CENSORED outcome is censored at the horizon, and so is an
    INVALIDATED outcome with no exit timestamp -- the event time is
    unknown, and an unknown time is a censoring, not a guess.

    The observed time is the exit bar offset: ``(exit_ts - ts) / BAR_S``
    minus one bar for a barrier fill (the fill happens at the open of the
    exit bar) and minus two for a TIME exit (the exit is the CLOSE of the
    last bar).

    Returns ``{cause: [(bar, F_cause(bar))]}``.
    """
    times, causes = [], []
    for o in outcomes:
        cause = getattr(o, "cause", None)
        exit_ts = getattr(o, "exit_ts", None)
        if cause == "CENSORED" or exit_ts is None:
            times.append(horizon_bars)
            causes.append(0)
            continue
        extra = 2 if cause == "TIME" else 1
        off = int((exit_ts - o.ts) // BAR_S) - extra
        times.append(min(max(off, 0), horizon_bars))
        causes.append(_CAUSE_CODE.get(cause, 0))
    inc = S.aalen_johansen(times, causes, len(INCIDENCE_CAUSES), horizon_bars)
    return {name: inc[code]
            for code, name in enumerate(INCIDENCE_CAUSES, start=1)}
