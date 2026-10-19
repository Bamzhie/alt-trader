"""WS5: funding cost attribution for the quant research harness.

Attaches venue-reported funding costs to event evaluation, reusing the
append-only funding-observation archive (``proto/funding.py``) and the
venue-reported settlement archive (``proto/settlements.py``) as the two
cost data sources.

Design rules (reused from WS4 archives, proposal §3.1/§4):
  * Funding is paid/received at the venue's settle boundaries. MEXC's
    per-row cycle can vary; ``SETTLE_SECONDS`` = 8h is the fallback only
    for coins with no settlement archive. Those boundaries are the FUNDING
    EVENTS; a cost exists only at a funding event, never at an arbitrary
    observation timestamp.
  * Settlements are venue-reported settled funding
    (``proto/settlements.py``): each row's ``settle_ts`` IS a funding
    event timestamp and ``settled_rate`` is what was actually paid there.
    When the settlement archive covers every funding event inside a
    holding window, it is the cost source — no mapping involved.
  * The observation archive (``proto/funding.py``) records the MUTATING
    point-in-time rate at each ``ts`` — it does NOT record funding-event
    timestamps. Observation timestamps are therefore NEVER summed as if
    they were settle boundaries. The documented mapping from snapshots to
    funding events is:
        funding event at boundary ``b`` (b = phase + k * cycle) takes the
        LAST snapshot in ``[b - cycle, b)``.
      Each boundary is charged at most once, with the most recent
      point-in-time snapshot of its own cycle. A snapshot after ``b``
      belongs to the next cycle and never affects this one. The grid
      (``phase``, ``cycle``) is the VENUE's own: when the coin has a
      settlement archive it is inferred from that archive's rows and used
      for the observation fallback too, so snapshots map to the boundaries
      the venue actually settles on (which may be off-epoch). Only when the
      coin has NO settlement archive at all does the epoch-aligned 8h UTC
      grid apply. If a settlement archive exists but does not carry enough
      information to infer the grid, the window is INCOMPLETE — epoch
      alignment is never assumed in place of the venue grid.
  * CYCLE TRANSITIONS: MEXC reports a per-row ``collectCycle`` — the
    funding interval in force FROM that settlement onwards (verified
    against the archive: the gap to the next row equals the current row's
    cycle in 1,365,207 of the 1,365,701 rows that have a following row.
    The 494 exceptions include cycle transitions and same-cycle gaps, so
    they cannot all be identified as transition bridges. The separate audit
    found 512 cycle-label transitions; 23 bridges longer than the new cycle
    were treated as unverifiable).
    A coin's cycle does change (1h/2h/4h/8h all occur), so the schedule of
    a holding window is a SEQUENCE of constant-cycle REGIMES, not one grid:
      - a LENGTHENING transition (4h -> 8h) skips the intermediate
        settlement: the old grid's next boundary has no row, and that is
        the venue's stated behaviour, not a missing row;
      - a SHORTENING transition (8h -> 4h) ADDS settlements: the new
        grid's intermediate boundaries must be charged, or funding is
        silently undercounted.
    Each regime therefore contributes its own grid, restricted to the rows
    that regime actually covers, and the regimes are joined at the
    venue-stated transition. When the schedule across a transition cannot
    be established (a row off its regime's grid, or a transition bridge
    longer than the new cycle, i.e. the row that starts the new regime is
    missing) the window is INCOMPLETE — a settlement is never silently
    omitted. A row missing INSIDE a regime is a gap the observation
    fallback may still attribute.
  * INCOMPLETE, not estimated: if ANY funding event inside the holding
    window cannot be attributed (a venue settlement row is missing, no
    snapshot covers a boundary's cycle, or a settlement archive cannot
    yield the venue grid), the window's cost is
    ``cost=None, incomplete=True`` — the event is excluded from
    funding-adjusted metrics and counted, never zero-filled.
  * Missing data is MISSING — never estimated, interpolated, or
    zero-filled. If a coin has no funding archive at all, its funding cost
    is reported as ``None`` (unevaluated), not 0.
  * The sign follows the TRADE side supplied by the caller (derived from
    the selected rule's policy, see ``eval_harness.trade_side``): a
    positive rate is a cost for a long (side=+1) and a credit for a short
    (side=-1). Net R is computed by ``barrier.net_r`` which subtracts
    the combined (friction + funding) ``cost_frac``.

This module is research-only: it never writes to production data, never
starts the service, and never changes emission modes.
"""
import json
import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .config import QuantConfig, DEFAULT as DEFAULT_CFG

SETTLE_SECONDS = 8 * 3600  # 8h funding cycle (MEXC standard; see ws4-settlement-history.md)

# Tolerance when matching a venue-reported settle_ts to its grid boundary
# (one 5m bar; venue rows sit exactly on the grid, this only absorbs
# whole-second rounding).
BOUNDARY_TOL_S = 300


@dataclass(frozen=True)
class FundingCostResult:
    """Result of attributing funding cost to one event.

    cost: float | None -- accumulated funding cost as a fraction of
        notional (additive to the round-trip friction cost_frac).
        Sign follows the trade side: a positive rate is a cost for a
        long (side=+1) and a credit for a short (side=-1), and vice-versa
        for negative rates.  ``None`` means funding data was missing or
        could not be attributed for this coin over the holding window --
        the harness must surface this as insufficient evidence rather
        than treating it as zero cost.
    observations: int -- how many FUNDING EVENTS (settle boundaries)
        contributed. Never a count of raw snapshots.
    missing: bool -- True when the coin has NO funding archive at all
        (the coin was never observed by the funding collector).
    source: str -- "settlement" (venue-reported settled rates),
        "observation" (snapshot-mapped funding events), or "none".
    incomplete: bool -- True when archives exist for the coin but at
        least one funding event inside the holding window could not be
        attributed (missing venue settlement row, no snapshot covers a
        boundary's cycle, or the venue grid cannot be inferred). The cost
        is excluded, not estimated.
    reason: str | None -- machine-readable cause of incompleteness:
        "settlement-boundaries-missing" (a venue settlement row is
        missing for an expected boundary), "observation-coverage-gap"
        (no snapshot covers a boundary's cycle), "venue-grid-unknown"
        (a settlement archive exists but cannot yield the phase/cycle
        grid), "settlement-schedule-unverifiable" (the venue's schedule
        cannot be established: a missing or irregular settlement row
        inside a cycle regime, or a transition whose bridge is longer
        than the new cycle), or several joined by "+".
    """
    cost: Optional[float]
    observations: int
    missing: bool
    source: str
    incomplete: bool = False
    reason: Optional[str] = None


def _load_observations(funding_dir: str, coin: str) -> List[Tuple[int, float]]:
    """Load (ts, rate) pairs from the funding observation archive.

    Uses proto.funding.read_funding which returns [{ts, rate, cap}].
    Returns [] when the file is absent (missing, not zero).
    """
    try:
        from proto.funding import read_funding
    except ImportError:
        return []
    rows = read_funding(funding_dir, coin)
    return [(r["ts"], r["rate"]) for r in rows]


def _load_settlements(settlements_dir: str,
                      coin: str) -> List[Tuple[int, float, Optional[float]]]:
    """Load (settle_ts, settled_rate, cycle_hours) rows from the
    venue-reported settlement archive.

    Uses proto.settlements.read_settlements.  Returns [] when the file
    is absent.
    """
    try:
        from proto.settlements import read_settlements
    except ImportError:
        return []
    rows = read_settlements(settlements_dir, coin)
    return [(r["settle_ts"], r["settled_rate"], r.get("cycle_hours"))
            for r in rows]


def coin_cost_data_missing(coin: str, funding_dir: Optional[str],
                           settlements_dir: Optional[str]) -> bool:
    """True when NEITHER archive holds any row for ``coin``."""
    if funding_dir is not None and _load_observations(funding_dir, coin):
        return False
    if settlements_dir is not None and _load_settlements(settlements_dir, coin):
        return False
    return True


def _grid_boundaries(lo: int, hi: int, phase: int,
                     cycle: int) -> List[int]:
    """Funding-event boundaries strictly inside ``(lo, hi)``.

    Boundaries are ``phase + k * cycle`` (the venue's own settle grid:
    inferred from the settlement archive when present, else epoch-aligned
    UTC).  A boundary exactly at open or exactly at close is NOT charged
    (the position does not yet / no longer exists at that instant).
    """
    k = math.floor((lo - phase) / cycle) + 1
    b = phase + k * cycle
    out = []
    while b < hi:
        out.append(b)           # b > lo by construction of k
        b += cycle
    return out


_MAX_CYCLE_S = 8 * 3600      # longest documented venue funding cycle

# Rows within this margin of the window are kept as context, so a cycle
# transition just before or just after the window is still visible.
_REGIME_MARGIN_S = 2 * _MAX_CYCLE_S


@dataclass(frozen=True)
class _Regime:
    """One constant-cycle stretch of venue settlements.

    phase: boundary phase (``settle_ts % cycle``) of this regime.
    cycle: settlement interval in seconds.
    first_ts: first venue settlement row of the regime.
    last_ts: last venue settlement row of the regime.
    """
    phase: int
    cycle: int
    first_ts: int
    last_ts: int


def _settlement_regimes(settle_rows: List[Tuple[int, float, Optional[float]]],
                        open_ts: int, close_ts: int):
    """Group the venue's settlement rows around a window into regimes.

    ``settle_rows`` must be sorted by ``settle_ts`` (the archive reader
    sorts on read).  Rows within ``_REGIME_MARGIN_S`` of the window are
    the context; consecutive rows carrying the same ``cycle_hours`` form
    one regime.

    Establishability (the schedule must be provable, never assumed):
      * every row of a regime must sit on that regime's grid, and the grid
        must be a single phase/cycle (an off-grid row means the venue
        schedule here is not a simple cycle);
      * at a transition the bridge (last old row -> first new row) must be
        no longer than the NEW cycle — a longer bridge means the row that
        starts the new regime is missing, so the new grid's phase cannot
        be trusted. A shorter bridge is the venue's stated behaviour (a
        lengthening transition skips the intermediate settlement; a
        shortening transition starts the new grid early).

    A settlement row missing INSIDE a regime does not make the schedule
    unknown: the grid still demands that boundary, so the settlement path
    reports a gap and the observation fallback may attribute it. Only an
    unprovable schedule (above) is reported as unverifiable.

    Returns ``(regimes, reason)``.  ``regimes`` is None when the schedule
    cannot be established; ``reason`` is then one of
    "venue-grid-unknown" (no rows near the window, or a row without a
    usable cycle) or "settlement-schedule-unverifiable".
    """
    if not settle_rows:
        return None, "venue-grid-unknown"
    tss = [r[0] for r in settle_rows]
    lo = bisect_left(tss, open_ts - _REGIME_MARGIN_S)
    hi = bisect_right(tss, close_ts + _REGIME_MARGIN_S)
    ctx = settle_rows[lo:hi]
    if not ctx:
        return None, "venue-grid-unknown"

    groups = []                       # [(cycle_seconds, [rows])]
    for r in ctx:
        c = r[2]
        if not c or int(c) <= 0:
            return None, "venue-grid-unknown"
        cs = int(c) * 3600
        if groups and groups[-1][0] == cs:
            groups[-1][1].append(r)
        else:
            groups.append((cs, [r]))

    for cs, rs in groups:
        phase = rs[0][0] % cs
        for r in rs:
            if r[0] % cs != phase:
                # a row off this regime's grid: the venue schedule here is
                # not a single cycle, so it cannot be established
                return None, "settlement-schedule-unverifiable"
    for (_c1, rs1), (c2, rs2) in zip(groups, groups[1:]):
        if rs2[0][0] - rs1[-1][0] > c2:
            # the venue-stated cycle change cannot explain the bridge: the
            # row that starts the new regime is missing, so the new grid's
            # phase cannot be trusted
            return None, "settlement-schedule-unverifiable"

    regimes = [_Regime(phase=rs[0][0] % cs, cycle=cs,
                       first_ts=rs[0][0], last_ts=rs[-1][0])
               for cs, rs in groups]
    return regimes, None


def _expected_boundaries(regimes: List[_Regime], open_ts: int,
                         close_ts: int) -> List[Tuple[int, int]]:
    """Funding boundaries the venue must have settled inside the window.

    Each regime contributes its OWN grid, restricted to the rows that
    regime covers: a lengthening transition's skipped boundary is never
    demanded, and a shortening transition's extra boundaries are always
    demanded (so funding is never silently undercounted).  The first
    regime's grid is additionally required BEFORE its first row and the
    last regime's grid AFTER its last row: that is how an archive that
    does not cover the whole window is detected instead of silently
    undercounting its tail.

    Returns a list of ``(ts, cycle)`` sorted by ts; ``cycle`` is the
    interval of the regime that owns the boundary (the observation
    fallback needs it to pick the snapshot window).
    """
    out = {}
    last_idx = len(regimes) - 1
    for i, reg in enumerate(regimes):
        for b in _grid_boundaries(reg.first_ts - 1, reg.last_ts + 1,
                                  reg.phase, reg.cycle):
            out[b] = reg.cycle
        if i == 0:
            for b in _grid_boundaries(open_ts - 1, reg.first_ts,
                                      reg.phase, reg.cycle):
                out[b] = reg.cycle
        if i == last_idx:
            for b in _grid_boundaries(reg.last_ts, close_ts,
                                      reg.phase, reg.cycle):
                out[b] = reg.cycle
    return [(b, out[b]) for b in sorted(out) if open_ts < b < close_ts]


def _settlement_attribution(settle_rows, open_ts, close_ts, side,
                            bounds):
    """Cost from venue-reported settlements over ``(open_ts, close_ts)``.

    ``bounds`` is the expected-boundary list from ``_expected_boundaries``
    (one entry per funding event the venue must have settled inside the
    window, across every cycle regime the window spans).  EVERY expected
    boundary must have a venue-reported row (within BOUNDARY_TOL_S); if
    any row is missing the window is a GAP -- the caller reports it as
    incomplete instead of silently undercounting.

    Returns (cost, n_events, gap) with cost/n_events None on a gap.
    """
    if not bounds:
        return 0.0, 0, False
    tss = [t for t, _, _ in settle_rows]
    total = 0.0
    for b, _cycle in bounds:
        j = bisect_left(tss, b - BOUNDARY_TOL_S)
        if j >= len(tss) or tss[j] > b + BOUNDARY_TOL_S:
            return None, None, True     # venue settlement row missing
        total += settle_rows[j][1] * side
    return total, len(bounds), False


def _observation_attribution(obs_rows, open_ts, close_ts, side, bounds):
    """Cost from observation snapshots via the documented funding-event
    mapping (see module docstring): the boundary ``b`` takes the LAST
    snapshot in ``[b - cycle, b)``; each boundary is charged at most
    once; a boundary with no covering snapshot makes the window a gap.

    ``bounds`` is the expected-boundary list from
    ``_expected_boundaries``; the ``cycle`` attached to each boundary is
    the interval of the regime that owns it, so a window that spans a
    cycle transition maps each side of the transition to its own snapshot
    window.  Only when the coin has no settlement archive at all does the
    epoch-aligned default grid apply (see ``funding_cost``).

    Returns (cost, n_events, gap) with cost/n_events None on a gap.
    """
    if not bounds:
        return 0.0, 0, False
    tss = [t for t, _ in obs_rows]
    total = 0.0
    for b, cycle in bounds:
        # last snapshot in [b - cycle, b): first ts >= b-cycle, then the
        # one before the first ts >= b.
        lo = bisect_left(tss, b - cycle)
        hi = bisect_left(tss, b)
        if hi <= lo:
            return None, None, True     # no snapshot covers this cycle
        total += obs_rows[hi - 1][1] * side
    return total, len(bounds), False


def funding_cost(
    coin: str,
    open_bar_ts: int,
    close_bar_ts: int,
    funding_dir: Optional[str] = None,
    settlements_dir: Optional[str] = None,
    side: int = 1,
) -> FundingCostResult:
    """Compute the funding cost for holding ``coin`` from
    ``open_bar_ts`` to ``close_bar_ts``.

    Cost is the sum of the rates PAID at each funding event (settle
    boundary) strictly inside ``(open_bar_ts, close_bar_ts)``,
    multiplied by ``side``:
      * side=+1 (long): positive rates are costs (paid).
      * side=-1 (short): positive rates are credits (received).

    Source discipline:
      * Venue-reported settlements first: when the coin's settlement
        archive covers every funding event in the window, the settled
        rates are used as-is; its rows also define the venue's settle
        schedule (one constant-cycle regime per stretch of rows, so a
        cycle transition inside the window is charged on both sides).
      * Otherwise the observation archive is consulted through the
        documented snapshot -> funding-event mapping (last snapshot of
        each boundary's cycle, each boundary charged once). The mapping
        uses the venue schedule inferred from the settlement archive; only a
        coin with NO settlement archive at all uses the epoch-aligned grid.
      * When neither source can attribute EVERY funding event in the
        window (including a settlement archive whose schedule cannot be
        established), the result is ``incomplete=True, cost=None``: excluded
        and counted by the harness, never estimated.

    ``close_bar_ts`` should be the event's ACTUAL exit timestamp (TP/SL
    exit early, only TIME exits go to the full horizon).  Funding is
    charged only through that exit.

    Returns a FundingCostResult. ``missing=True`` means no funding data
    exists for this coin at all. ``incomplete=True`` (with missing=False)
    means data exists but does not cover every funding event in the
    window.
    """
    obs = _load_observations(funding_dir, coin) \
        if funding_dir is not None else []
    settles = _load_settlements(settlements_dir, coin) \
        if settlements_dir is not None else []

    # If neither source has data for this coin, it is genuinely missing.
    if not obs and not settles:
        return FundingCostResult(
            cost=None, observations=0, missing=True, source="none")

    reasons = []

    # The venue's own schedule for this window: a SEQUENCE of constant-cycle
    # regimes inferred from the settlement archive rows around the window.
    # It is used for BOTH source disciplines (the settlement coverage check
    # AND the observation fallback), so a fallback maps snapshots to the
    # same boundaries the venue actually settles on -- including across a
    # cycle transition.  None => the schedule cannot be established.
    regimes = None
    if settles:
        regimes, reason = _settlement_regimes(settles, open_bar_ts,
                                               close_bar_ts)
        if regimes is None:
            reasons.append(reason)

    if regimes is not None:
        bounds = _expected_boundaries(regimes, open_bar_ts, close_bar_ts)
        # Venue-reported settlements first (settled facts, no mapping).
        cost, n, gap = _settlement_attribution(
            settles, open_bar_ts, close_bar_ts, side, bounds)
        if not gap:
            return FundingCostResult(
                cost=cost, observations=n, missing=False,
                source="settlement")
        reasons.append("settlement-boundaries-missing")
        # Observation-only fallback against the SAME venue schedule.
        if obs:
            cost, n, gap = _observation_attribution(
                obs, open_bar_ts, close_bar_ts, side, bounds)
            if not gap:
                return FundingCostResult(
                    cost=cost, observations=n, missing=False,
                    source="observation")
            reasons.append("observation-coverage-gap")
    elif obs and not settles:
        # No settlement archive for this coin: the documented epoch-aligned
        # assumption is the only available grid.
        bounds = [(b, SETTLE_SECONDS) for b in _grid_boundaries(
            open_bar_ts, close_bar_ts, 0, SETTLE_SECONDS)]
        cost, n, gap = _observation_attribution(
            obs, open_bar_ts, close_bar_ts, side, bounds)
        if not gap:
            return FundingCostResult(
                cost=cost, observations=n, missing=False,
                source="observation")
        reasons.append("observation-coverage-gap")

    # Data exists for the coin but at least one funding event in the
    # window could not be attributed: incomplete, excluded not estimated.
    return FundingCostResult(
        cost=None, observations=0, missing=False,
        source="settlement" if settles else "observation",
        incomplete=True, reason="+".join(reasons))


def config_hash(funding_dir: Optional[str], settlements_dir: Optional[str],
                cfg: QuantConfig = DEFAULT_CFG) -> str:
    """A version hash covering the cost-attribution inputs.

    Combines the quant config hash with the funding/settlements directory
    paths so that any change to cost inputs is reflected in the hash --
    results are only comparable within one hash.
    """
    import hashlib
    payload = json.dumps({
        "quant_cfg": cfg.config_hash(),
        "funding_dir": funding_dir,
        "settlements_dir": settlements_dir,
        "settle_seconds": SETTLE_SECONDS,
        "boundary_tol_s": BOUNDARY_TOL_S,
    }, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
