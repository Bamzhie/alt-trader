"""WS5: funding cost attribution for the quant research harness.

Attaches venue-reported funding costs to event evaluation, reusing the
append-only funding-observation archive (``proto/funding.py``) and the
venue-reported settlement archive (``proto/settlements.py``) as the two
cost data sources.

Design rules (reused from WS4 archives, proposal §3.1/§4):
  * Funding is paid/received at the venue's settle boundaries (every
    ``SETTLE_SECONDS`` = 8h on MEXC). Those boundaries are the FUNDING
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
        funding event at boundary ``b`` (b = phase + k * cycle, phase
        inferred from the coin's settlement archive when present, else
        epoch-aligned UTC) takes the LAST snapshot in ``[b - cycle, b)``.
      Each boundary is charged at most once, with the most recent
      point-in-time snapshot of its own cycle. A snapshot after ``b``
      belongs to the next cycle and never affects this one.
  * INCOMPLETE, not estimated: if ANY funding event inside the holding
    window cannot be attributed (a venue settlement row is missing, or no
    snapshot covers a boundary's cycle), the window's cost is
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
from bisect import bisect_left
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
        attributed (missing venue settlement row, or no snapshot covers
        a boundary's cycle). The cost is excluded, not estimated.
    reason: str | None -- machine-readable cause of incompleteness:
        "settlement-boundaries-missing", "observation-coverage-gap", or
        both joined by "+".
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


def _cycle_seconds(settle_rows: List[Tuple[int, float, Optional[float]]]
                   ) -> int:
    """The venue's funding cycle from its own reported cycle_hours."""
    cycles = [c for _, _, c in settle_rows if c and c > 0]
    if not cycles:
        return SETTLE_SECONDS
    return int(round(max(set(cycles), key=cycles.count))) * 3600


def _settlement_attribution(settle_rows, open_ts, close_ts, side):
    """Cost from venue-reported settlements over ``(open_ts, close_ts)``.

    The venue's own settle timestamps define both the funding events and
    the grid phase.  EVERY expected boundary inside the window must have
    a venue-reported row (within BOUNDARY_TOL_S); if any row is missing
    the window is a GAP -- the caller reports it as incomplete instead of
    silently undercounting.

    Returns (cost, n_events, gap) with cost/n_events None on a gap.
    """
    tss = [t for t, _, _ in settle_rows]
    cycle = _cycle_seconds(settle_rows)
    phase = tss[0] % cycle
    expected = _grid_boundaries(open_ts, close_ts, phase, cycle)
    if not expected:
        return 0.0, 0, False
    total = 0.0
    j = 0
    for b in expected:
        # advance to the first row at/after b - tol
        while j < len(tss) and tss[j] < b - BOUNDARY_TOL_S:
            j += 1
        if j >= len(tss) or abs(tss[j] - b) > BOUNDARY_TOL_S:
            return None, None, True     # venue settlement row missing
        total += settle_rows[j][1] * side
    return total, len(expected), False


def _observation_attribution(obs_rows, open_ts, close_ts, side,
                             cycle=SETTLE_SECONDS, phase=0):
    """Cost from observation snapshots via the documented funding-event
    mapping (see module docstring): the boundary ``b`` takes the LAST
    snapshot in ``[b - cycle, b)``; each boundary is charged at most
    once; a boundary with no covering snapshot makes the window a gap.

    Returns (cost, n_events, gap) with cost/n_events None on a gap.
    """
    tss = [t for t, _ in obs_rows]
    expected = _grid_boundaries(open_ts, close_ts, phase, cycle)
    if not expected:
        return 0.0, 0, False
    total = 0.0
    for b in expected:
        # last snapshot in [b - cycle, b): first ts >= b-cycle, then the
        # one before the first ts >= b.
        lo = bisect_left(tss, b - cycle)
        hi = bisect_left(tss, b)
        if hi <= lo:
            return None, None, True     # no snapshot covers this cycle
        total += obs_rows[hi - 1][1] * side
    return total, len(expected), False


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
        rates are used as-is.
      * Otherwise the observation archive is consulted through the
        documented snapshot -> funding-event mapping (last snapshot of
        each boundary's cycle, each boundary charged once).
      * When neither source can attribute EVERY funding event in the
        window, the result is ``incomplete=True, cost=None``: excluded
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

    # Venue-reported settlements first (settled facts, no mapping).
    if settles:
        cost, n, gap = _settlement_attribution(
            settles, open_bar_ts, close_bar_ts, side)
        if not gap:
            return FundingCostResult(
                cost=cost, observations=n, missing=False,
                source="settlement")
        reasons.append("settlement-boundaries-missing")

    # Observation-only fallback: snapshot -> funding-event mapping.
    if obs:
        cost, n, gap = _observation_attribution(
            obs, open_bar_ts, close_bar_ts, side)
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
