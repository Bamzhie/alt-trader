"""WS5: funding cost attribution for the quant research harness.

Attaches venue-reported funding costs to event evaluation, reusing the
append-only funding-observation archive (``proto/funding.py``) and the
venue-reported settlement archive (``proto/settlements.py``) as the two
cost data sources.

Design rules (reused from WS4 archives, proposal §3.1/§4):
  * Funding is observed point-in-time (``proto/funding.py``): the `rate`
    field is the rate OBSERVED at `ts`, not a settled fact. A position
    opened at time `t0` accrues the rate observed on the next settle
    boundary after t0.
  * Settlements are venue-reported settled rates (``proto/settlements.py``):
    the `settled_rate` field is the rate that was actually paid on
    `settle_ts`.
  * Missing data is MISSING — never estimated, interpolated, or
    zero-filled. If a coin has no funding observations, its funding cost
    is reported as ``None`` (unevaluated), not 0.
  * The evaluation harness calls ``funding_cost`` to get the cost of
    holding a position for the event horizon; the returned cost is in the
    same units as the gross log-return (net R is computed by
    ``barrier.net_r`` which subtracts `cost_frac`).

This module is research-only: it never writes to production data, never
starts the service, and never changes emission modes.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .config import BAR_S, BARS_PER_DAY, QuantConfig, DEFAULT as DEFAULT_CFG

SETTLE_SECONDS = 8 * 3600  # 8h funding cycle (MEXC standard; see ws4-settlement-history.md)


@dataclass(frozen=True)
class FundingCostResult:
    """Result of attributing funding cost to one event.

    cost: float | None -- accumulated funding cost as a fraction of
        notional (additive to the round-trip friction cost_frac).
        Sign follows the trade side: a positive rate is a cost for a
        long (side=+1) and a credit for a short (side=-1), and vice-versa
        for negative rates.  ``None`` means funding data was missing for
        this coin over the holding window -- the harness must surface
        this as insufficient evidence rather than treating it as zero
        cost.
    observations: int -- how many funding observations contributed.
    missing: bool -- True when the coin has NO funding archive at all
        (the coin was never observed by the funding collector).
    source: str -- "observation" or "settlement" depending on which
        source provided the cost values.
    """
    cost: Optional[float]
    observations: int
    missing: bool
    source: str


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


def _load_settlements(settlements_dir: str, coin: str) -> List[Tuple[int, float]]:
    """Load (settle_ts, settled_rate) pairs from the settlement archive.

    Uses proto.settlements.read_settlements.
    Returns [] when the file is absent.
    """
    try:
        from proto.settlements import read_settlements
    except ImportError:
        return []
    rows = read_settlements(settlements_dir, coin)
    return [(r["settle_ts"], r["settled_rate"]) for r in rows]


def _interpolate_rate_at(obs_tss: List[int], obs_rates: List[float],
                         target_ts: int) -> Optional[float]:
    """Find the funding rate in effect at ``target_ts``.

    Funding rates are piecewise-constant between observation/settle times:
    the rate observed at time T applies until the next observation.  This
    is NOT estimation -- it is the venue's own piecewise-constant rate
    schedule.  Returns None if no observation precedes target_ts.
    """
    # Find the last observation at or before target_ts
    rate = None
    for ts, rt in zip(obs_tss, obs_rates):
        if ts <= target_ts:
            rate = rt
        else:
            break
    return rate


def _accumulate_funding(obs_tss: List[int], obs_rates: List[float],
                        open_ts: int, close_ts: int,
                        side: int = 1) -> Tuple[Optional[float], int]:
    """Accumulate funding cost over [open_ts, close_ts) using the
    piecewise-constant observation series.

    Funding on MEXC is paid/received at settle boundaries.  The
    observation timestamps **are** the settle boundaries (each observation
    records the rate in effect at that settlement).  A position opened at
    ``open_ts`` and closed at ``close_ts`` accrues the rate in effect at
    each settle boundary (observation timestamp) in (open_ts, close_ts).

    The ``side`` parameter determines the sign of the cost:
      * Positive rates are a cost for longs (side=+1) – you pay the rate.
      * Positive rates are a credit for shorts (side=-1) – you receive the
        rate (your P&L improves).
      * Negative rates reverse the effect.

    Returns (total_cost_or_None, n_observations_used).  Total is None
    when no observation covers the open time (the coin wasn't being
    observed when the position opened -- missing, not zero).  Total is
    0.0 with n=0 when the position closes before the first settle boundary
    after open_ts.
    """
    if not obs_tss:
        return None, 0
    # Rate in effect at open time (last obs <= open_ts)
    open_rate = _interpolate_rate_at(obs_tss, obs_rates, open_ts)
    if open_rate is None:
        return None, 0
    total = 0.0
    n = 0
    # Walk observation timestamps (settle boundaries) strictly after open_ts
    # and before close_ts.  The rate in effect at each boundary is the
    # piecewise-constant rate (last obs <= that boundary).
    for ts, rate in zip(obs_tss, obs_rates):
        if open_ts < ts < close_ts:
            r = _interpolate_rate_at(obs_tss, obs_rates, ts)
            if r is not None:
                total += r * side
                n += 1
    return total, n


def _next_settle_after(ts: int) -> Optional[int]:
    """The next 8h settle boundary strictly after timestamp ``ts``.

    MEXC settles funding every 8 hours. The exact grid phase depends on
    the coin's listing; we align to the next 8h boundary strictly after
    ``ts``.  Returns None on arithmetic failure.

    Note: this is a grid-alignment heuristic, not an estimation of the
    rate itself. The rate at each boundary is still the venue-reported
    value (observation or settlement). Coins with a different grid phase
    may accumulate costs at slightly offset boundaries -- this is a
    known limitation surfaced in the report, not a silent fill.
    """
    if ts < 0:
        return None
    return ts + SETTLE_SECONDS - (ts % SETTLE_SECONDS)


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

    Cost is the sum of venue-reported rates at each settle boundary in
    [open, close), multiplied by ``side``:
      * side=+1 (long): positive rates are costs (paid).
      * side=-1 (short): positive rates are credits (received).

    Uses settlements first (venue-reported settled rates) if available,
    then falls back to observations (point-in-time).

    ``close_bar_ts`` should be the event's ACTUAL exit timestamp (TP/SL
    exit early, only TIME exits go to the full horizon).  Funding is
    charged only through that exit.

    Returns a FundingCostResult. ``missing=True`` means no funding data
    exists for this coin at all. ``cost=None`` (with missing=False) means
    data exists but does not cover the position window.
    """
    obs_tss, obs_rates = [], []
    if funding_dir is not None:
        obs = _load_observations(funding_dir, coin)
        obs_tss = [t for t, _ in obs]
        obs_rates = [r for _, r in obs]

    settle_tss, settle_rates = [], []
    if settlements_dir is not None:
        settles = _load_settlements(settlements_dir, coin)
        settle_tss = [t for t, _ in settles]
        settle_rates = [r for _, r in settles]

    # If neither source has data for this coin, it is genuinely missing.
    if not obs_tss and not settle_tss:
        return FundingCostResult(
            cost=None, observations=0, missing=True, source="none")

    # Try settlements first (venue-reported settled rates are more precise)
    if settle_tss:
        cost, n = _accumulate_funding(settle_tss, settle_rates,
                                      open_bar_ts, close_bar_ts, side)
        if cost is not None:
            return FundingCostResult(
                cost=cost, observations=n, missing=False,
                source="settlement")

    # Fall back to observations
    if obs_tss:
        cost, n = _accumulate_funding(obs_tss, obs_rates,
                                      open_bar_ts, close_bar_ts, side)
        if cost is not None:
            return FundingCostResult(
                cost=cost, observations=n, missing=False,
                source="observation")

    # Data exists for the coin but doesn't cover the window
    return FundingCostResult(
        cost=None, observations=0, missing=False,
        source="settlement" if settle_tss else "observation")


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
    }, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
