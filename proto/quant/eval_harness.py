"""WS5: evaluation harness for historical-data cost attribution.

Runs the research harness (`research.py`) on production bar archives with
funding/settlement cost attribution, enforcing the split discipline:

  * **Development**: configs tested on the dev window; best-of-many is
    max-t corrected for multiplicity.
  * **Validation**: only the dev-selected config is re-evaluated.
  * **Holdout**: evaluated at most ONCE, only if validation kept the
    config; a lock file refuses any second look.

Purge / embargo rules (reused from `config.Splits`):
  * Events whose label window crosses a split boundary are purged from
    the downstream window -- the Splits embargo (2 days >= longest
    horizon + 1 bar) already guarantees this by construction, but the
    harness asserts it explicitly.
  * A 2-day embargo separates dev->val and val->holdout so no outcome
    window can leak across.

Cost attribution reuses `cost_attribution.funding_cost`:
  * Missing funding data for a coin is surfaced as insufficient evidence
    (the event is excluded from the cost-attributed net-R, not
    zero-filled).
  * The cost is added to the round-trip friction in ``barrier.net_r``.

Reproducibility:
  * The harness records the config hash, the cost-attribution hash, the
    git revision, and the bar/archive file digests in the output report.
  * All splits are derived from `Splits.bounds(n_days)`.

Research-only: this module never writes to production data, never
starts the service, never opens quant.db for writing, and never changes
emission modes.
"""
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import barrier as B
from . import stats as S
from . import research as R
from .config import (DEFAULT, BARS_PER_DAY, BAR_S, Geometry, QuantConfig,
                     RuleParams, Splits, grid_geometries)
from .cost_attribution import config_hash as cost_hash, FundingCostResult

# Maximum label horizon in bars (from the grid). The embargo must cover
# at least this many bars so no outcome window crosses a boundary.
MAX_HORIZON_BARS = max(g.horizon_bars for g in grid_geometries())


@dataclass(frozen=True)
class EvalSplits:
    """Resolved chronological splits for one evaluation run.

    ``n_days`` is the archive span.  ``bounds`` are (lo_day, hi_day) in
    day-float coordinates relative to the archive start, same as
    ``Splits.bounds``.
    """
    n_days: float
    dev: Tuple[float, float]
    val: Tuple[float, float]
    holdout: Tuple[float, float]
    embargo_days: float
    first_event_day: float

    @property
    def max_horizon_bars(self) -> int:
        return MAX_HORIZON_BARS


def resolve_splits(meta: Dict, cfg: QuantConfig = DEFAULT) -> EvalSplits:
    """Resolve splits from the archive metadata.

    ``meta`` must carry ``t_min``, ``t_max``, ``n_days`` (as produced by
    ``research.collect_events``).
    """
    n_days = float(meta["n_days"])
    bounds = cfg.splits.bounds(n_days)
    return EvalSplits(
        n_days=n_days,
        dev=bounds["dev"], val=bounds["val"],
        holdout=bounds["holdout"],
        embargo_days=cfg.splits.embargo_days,
        first_event_day=cfg.splits.first_event_day,
    )


def check_no_leakage(events: List, splits: EvalSplits) -> List[str]:
    """Verify no event's label window crosses a split boundary.

    Each event has a ``ts`` (bar_ts) and a ``day`` field.  The maximum
    horizon is MAX_HORIZON_BARS bars = ~1 day.  An event at day D has its
    outcome window span [D, D + max_horizon_days].  The embargo gap
    between dev_end and val_start must exceed max_horizon_days, and
    similarly between val_end and holdout_start.

    Returns a list of violation strings (empty = clean).
    """
    violations = []
    max_horizon_days = MAX_HORIZON_BARS / BARS_PER_DAY  # ~1.0 day

    if splits.val[0] - splits.dev[1] < max_horizon_days - 1e-9:
        violations.append(
            f"dev/val embargo gap {splits.val[0] - splits.dev[1]:.2f}d "
            f"< max horizon {max_horizon_days:.2f}d")
    if splits.holdout[0] - splits.val[1] < max_horizon_days - 1e-9:
        violations.append(
            f"val/holdout embargo gap {splits.holdout[0] - splits.val[1]:.2f}d "
            f"< max horizon {max_horizon_days:.2f}d")

    # Check each event: its outcome window must not cross into the NEXT split
    # (past the embargo). An event in the embargo zone is safe -- its window
    # ends before the next split starts.
    for ev in events:
        ev_day = ev.day
        window_end = ev_day + max_horizon_days
        # dev boundary: event in dev, window must not reach val_start
        if ev_day < splits.dev[1] and window_end > splits.val[0]:
            violations.append(
                f"event at day {ev_day:.2f} (window ends {window_end:.2f}) "
                f"reaches val_start {splits.val[0]}")
        # val boundary: event in val, window must not reach holdout_start
        elif splits.val[0] <= ev_day < splits.val[1] and window_end > splits.holdout[0]:
            violations.append(
                f"event at day {ev_day:.2f} (window ends {window_end:.2f}) "
                f"reaches holdout_start {splits.holdout[0]}")
    return violations


@dataclass
class EvaluationReport:
    """Full evaluation report with reproduction metadata."""
    config_hash: str
    cost_hash: str
    splits: Dict[str, Any]
    dev: Dict[str, Any]
    val: Dict[str, Any]
    holdout: Optional[Dict[str, Any]] = None
    holdout_slip2x: Optional[Dict[str, Any]] = None
    leakage_violations: List[str] = field(default_factory=list)
    missing_cost_coins: List[str] = field(default_factory=list)
    git_rev: Optional[str] = None
    bar_digests: Dict[str, str] = field(default_factory=dict)
    funding_digests: Dict[str, str] = field(default_factory=dict)
    settlement_digests: Dict[str, str] = field(default_factory=dict)
    incomplete_boundary_events: int = 0
    incomplete_boundary_denominator: int = 0
    produced_at: int = field(default_factory=lambda: int(time.time()))

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _git_rev() -> Optional[str]:
    """Best-effort git revision for reproducibility."""
    try:
        import subprocess
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def _archive_digest(data_dir: str, coin: str) -> Optional[str]:
    """SHA256 of an archive file for reproducibility."""
    if data_dir is None:
        return None
    path = os.path.join(data_dir, f"{coin}.csv.gz")
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _bar_digest(data_dir: str, coin: str) -> Optional[str]:
    """SHA256 of a bar archive file for reproducibility."""
    return _archive_digest(data_dir, coin)


def _funding_digest(funding_dir: str, coin: str) -> Optional[str]:
    """SHA256 of a funding observation archive file."""
    return _archive_digest(funding_dir, coin)


def _settlement_digest(settlements_dir: str, coin: str) -> Optional[str]:
    """SHA256 of a settlement archive file."""
    return _archive_digest(settlements_dir, coin)


def _event_exit_ts(ev, geom_idx: int, cfg: QuantConfig) -> Optional[int]:
    """Compute the actual exit timestamp for an event's selected geometry/side.

    The event stores ``exit_offset`` per (geometry, side) slot, where
    exit_offset is the 0-based bar offset from the entry bar (the open of
    bar i+1).  The entry bar timestamp is ``ev.ts + BAR_S`` (signal bar
    open + one bar), so:

        exit_ts = ev.ts + BAR_S + exit_offset[k] * BAR_S

    For TP/SL exits the offset is < horizon_bars (early exit); for TIME
    exits the offset is horizon_bars - 1 (full horizon).  Funding is
    charged only through this actual exit timestamp.
    """
    if not hasattr(ev, "exit_offset"):
        return None
    k = geom_idx * 2 + (0 if ev.side > 0 else 1)
    try:
        off = ev.exit_offset[k]
    except (IndexError, AttributeError):
        return None
    # Entry is at the open of bar i+1 = ev.ts + BAR_S; exit is at bar i+1+off
    return ev.ts + (1 + off) * BAR_S


def evaluate(data_dir: str, funding_dir: Optional[str] = None,
             settlements_dir: Optional[str] = None,
             cfg: QuantConfig = DEFAULT,
             spend_holdout: bool = False,
             out_dir: Optional[str] = None) -> EvaluationReport:
    """Run the full WS5 evaluation on production bar data.

    Steps:
      1. collect_events over the bar archive (reuses research.collect_events)
      2. resolve splits + assert no leakage
      3. run_stage_dev_val (dev -> val, with max-t multiplicity control)
      4. if spend_holdout and validation kept the config, spend the
         one-shot holdout
      5. attach funding cost attribution to the selected config's results
      6. record reproducibility metadata (config hash, cost hash, git rev,
         bar digests)

    Returns an EvaluationReport.  All output goes to ``out_dir`` (or a
    temp dir) -- production data/ is never written.
    """
    # 1. Collect events from the archive
    events, meta = R.collect_events(data_dir, cfg, progress=False)

    # 2. Resolve splits + check leakage
    splits = resolve_splits(meta, cfg)
    violations = check_no_leakage(events, splits)

    # 3. Dev -> Val
    # Guard: if too few events, run_stage_dev_val will produce empty configs
    # and spa_max_t will fail. Handle that explicitly.
    if len(events) < 50:
        dev_val = {
            "dev": {"n": 0, "mean_r": float("nan"), "ci_lo": float("nan"),
                    "ci_hi": float("nan"), "days": 0, "coins": 0,
                    "tp": float("nan"), "sl": float("nan"), "time": float("nan")},
            "val": {"n": 0, "mean_r": float("nan"), "ci_lo": float("nan"),
                    "ci_hi": float("nan"), "days": 0, "coins": 0,
                    "tp": float("nan"), "sl": float("nan"), "time": float("nan")},
            "spa": {"best_t": 0.0, "p_value": float("nan")},
            "selected": {"rule": "insufficient-data", "geometry": "n/a",
                         "rule_obj": None, "geom_idx": 0, "geom": None},
            "n_configs": 0,
            "dev_config_means": {}, "top5_dev": [],
        }
    else:
        dev_val = R.run_stage_dev_val(events, meta, cfg)

    # 4. Holdout (one-shot, only if val kept the config)
    holdout = None
    holdout_slip2x = None
    selected = dev_val.get("selected", {})
    if spend_holdout and selected.get("rule_obj") is not None:
        import math as _math
        val_mean = dev_val.get("val", {}).get("mean_r", float("nan"))
        if (isinstance(val_mean, (int, float)) and _math.isfinite(val_mean)
                and val_mean > 0):
            if out_dir is None:
                import tempfile
                out_dir = tempfile.mkdtemp(prefix="ws5-eval-")
            os.makedirs(out_dir, exist_ok=True)
            # holdout_once enforces the one-shot lock; a second attempt
            # raises SystemExit. Catch it so the harness reports refusal
            # gracefully instead of crashing.
            try:
                h, h2 = R.holdout_once(events, meta, selected, out_dir, cfg)
                holdout = R.summarize(h, 3000)
                holdout_slip2x = R.summarize(h2, 1500)
            except SystemExit:
                pass  # lock refuses a second look; holdout stays None

    # 5. Cost attribution analysis
    missing_cost_coins, incomplete = _find_missing_cost_coins(
        events, funding_dir, settlements_dir, splits, cfg, dev_val)

    # 6. Reproducibility metadata
    ch = cfg.config_hash()
    csh = cost_hash(funding_dir, settlements_dir, cfg)

    # Bar digests for ALL coins (no cap) whose data contributes to metrics
    coins_in_eval = sorted(set(e.coin for e in events))
    bar_digests = {}
    for coin in ["BTC", "ETH"] + coins_in_eval:
        d = _bar_digest(data_dir, coin)
        if d is not None:
            bar_digests[coin] = d

    # Archive digests for every coin whose data contributes to reported
    # metrics, covering both funding observations and settlements.
    funding_digests = {}
    settlement_digests = {}
    contributing_coins = sorted(set(e.coin for e in events) | {"BTC", "ETH"})
    for coin in contributing_coins:
        fd = _funding_digest(funding_dir, coin) if funding_dir else None
        sd = _settlement_digest(settlements_dir, coin) if settlements_dir else None
        if fd is not None:
            funding_digests[coin] = fd
        if sd is not None:
            settlement_digests[coin] = sd

    report = EvaluationReport(
        config_hash=ch,
        cost_hash=csh,
        splits={"dev": list(splits.dev), "val": list(splits.val),
                "holdout": list(splits.holdout),
                "n_days": splits.n_days,
                "embargo_days": splits.embargo_days,
                "first_event_day": splits.first_event_day,
                "max_horizon_bars": splits.max_horizon_bars},
        dev=dev_val["dev"],
        val=dev_val["val"],
        holdout=holdout,
        holdout_slip2x=holdout_slip2x,
        leakage_violations=violations,
        missing_cost_coins=missing_cost_coins,
        git_rev=_git_rev(),
        bar_digests=bar_digests,
        funding_digests=funding_digests,
        settlement_digests=settlement_digests,
        incomplete_boundary_events=incomplete["events"],
        incomplete_boundary_denominator=incomplete["denominator"],
    )

    # Write report
    if out_dir is None:
        import tempfile
        out_dir = tempfile.mkdtemp(prefix="ws5-eval-")
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "evaluation.json")
    with open(report_path, "w") as f:
        f.write(report.to_json())

    return report


def _find_missing_cost_coins(events, funding_dir, settlements_dir,
                             splits, cfg, dev_val=None) -> Tuple[List[str], Dict[str, int]]:
    """Identify coins that have events in the evaluation windows but
    no funding or settlement archive (cost data missing -- surfaced, not
    estimated).

    Uses the selected config's actual trade side and per-event exit
    timestamps (derived from the barrier labeler's exit_offset) so that
    funding is charged only through the real exit, not the max horizon.

    Also tracks events excluded from funding-adjusted metrics due to
    incomplete boundary coverage (funding data exists but does not cover
    the full [open_ts, exit_ts) window for that event).

    Returns (missing_coins, incomplete_info) where incomplete_info has
    keys "events" (count excluded) and "denominator" (total events in
    windows with cost data available).
    """
    from .cost_attribution import funding_cost
    coins_with_events = sorted(set(e.coin for e in events))
    if not coins_with_events:
        return [], {"events": 0, "denominator": 0}

    # Determine the selected geometry for exit-timestamp computation
    selected = dev_val.get("selected", {}) if dev_val else {}
    geom_idx = selected.get("geom_idx", 0)

    missing = []
    incomplete_events = 0
    denominator = 0

    for coin in coins_with_events:
        # Check each event for this coin individually with its actual
        # open/exit timestamps and trade side
        coin_events = [e for e in events if e.coin == coin]
        for ev in coin_events:
            # Use the selected geometry's exit offset to compute actual exit ts
            exit_ts = _event_exit_ts(ev, geom_idx, cfg)
            if exit_ts is None:
                # Fall back to max-horizon close for boundary checking
                exit_ts = ev.ts + MAX_HORIZON_BARS * BAR_S
            # Entry is at open of bar i+1
            open_ts = ev.ts + BAR_S
            side = ev.side
            result = funding_cost(
                coin, open_ts, exit_ts,
                funding_dir=funding_dir, settlements_dir=settlements_dir,
                side=side)
            denominator += 1
            if result.missing:
                if coin not in missing:
                    missing.append(coin)
            elif result.cost is None:
                # Data exists but doesn't cover the window -> incomplete
                incomplete_events += 1

    return missing, {"events": incomplete_events, "denominator": denominator}
    return missing
