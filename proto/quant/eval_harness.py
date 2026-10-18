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
  * Venue-reported settlement timestamps are the funding events;
    observation snapshots are mapped to funding events through the
    documented last-snapshot-of-the-cycle rule (never summed per
    snapshot). See the `cost_attribution` module docstring.
  * Missing funding data for a coin, or funding events that cannot be
    attributed inside a holding window, are surfaced as insufficient
    evidence (the event is EXCLUDED from the funding-adjusted metric and
    counted, not zero-filled).

Metrics and denominators -- every window reports two clearly separated
views (report fields):
  * ``dev`` / ``val`` / ``holdout`` / ``holdout_slip2x`` are
    FRICTION-ONLY (fees + slippage), exactly as the research harness
    computes them. Selection (which config, which window keeps it) uses
    only these friction numbers.
  * ``dev_funding_adjusted`` / ``val_funding_adjusted`` /
    ``holdout_funding_adjusted`` / ``holdout_slip2x_funding_adjusted``
    re-evaluate the SAME selected config with funding costs added to the
    round-trip friction. These are REPORTING ONLY (no tuning on val /
    holdout) and carry explicit denominators: ``n_events`` (selected,
    tradeable events in the window), ``n_funding_adjusted`` (events with
    a fully attributed funding cost -- the metric's denominator),
    ``n_excluded_missing_coin`` and ``n_excluded_incomplete``. When
    ``n_funding_adjusted`` is 0 the metric is ``None`` -- a
    funding-adjusted label never hides a friction-only number.

Position side: the trade side comes from the SELECTED RULE's policy
(``trade_side``: `fade` inverts the break direction, `cont` keeps it) and
is used consistently for funding signs, exit-offset selection, and the
coverage accounting.

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
                     RuleParams, grid_geometries)
from .cost_attribution import (config_hash as cost_hash,
                               coin_cost_data_missing, funding_cost)

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
    """Full evaluation report with reproduction metadata.

    ``dev`` / ``val`` / ``holdout`` / ``holdout_slip2x`` are friction-only
    (fees + slippage).  ``*_funding_adjusted`` re-evaluates the selected
    config with venue-attributed funding costs added, and carries explicit
    denominators (n_events, n_funding_adjusted, n_excluded_*).
    """
    config_hash: str
    cost_hash: str
    splits: Dict[str, Any]
    dev: Dict[str, Any]
    val: Dict[str, Any]
    holdout: Optional[Dict[str, Any]] = None
    holdout_slip2x: Optional[Dict[str, Any]] = None
    dev_funding_adjusted: Optional[Dict[str, Any]] = None
    val_funding_adjusted: Optional[Dict[str, Any]] = None
    holdout_funding_adjusted: Optional[Dict[str, Any]] = None
    holdout_slip2x_funding_adjusted: Optional[Dict[str, Any]] = None
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


def trade_side(ev, rule: RuleParams) -> int:
    """The actual POSITION side for an event under a rule's policy.

    The candidate event carries the break direction (``ev.side``); the
    rule's policy maps it to a position: `cont` trades with the break,
    `fade` trades against it (same convention as
    ``research.eval_config``: ``side = ev.side if rule.policy == "cont"
    else -ev.side``).  Funding signs, exit-offset slot selection, and
    coverage accounting all use THIS side.
    """
    return ev.side if rule.policy == "cont" else -ev.side


def _event_exit_ts(ev, geom_idx: int, rule: RuleParams) -> Optional[int]:
    """Compute the actual exit timestamp for an event's selected geometry.

    The event stores ``exit_offset`` per (geometry, side) slot, where
    exit_offset is the 0-based bar offset from the entry bar (the open of
    bar i+1).  The side slot is chosen from the TRADE side (rule policy),
    matching how the gross return was labeled.  The entry bar timestamp
    is ``ev.ts + BAR_S`` (signal bar open + one bar), so:

        exit_ts = ev.ts + BAR_S + exit_offset[k] * BAR_S

    For TP/SL exits the offset is < horizon_bars (early exit); for TIME
    exits the offset is horizon_bars - 1 (full horizon).  Funding is
    charged only through this actual exit timestamp.
    """
    if not hasattr(ev, "exit_offset"):
        return None
    side = trade_side(ev, rule)
    k = geom_idx * 2 + (0 if side > 0 else 1)
    try:
        off = ev.exit_offset[k]
    except (IndexError, AttributeError):
        return None
    # Entry is at the open of bar i+1 = ev.ts + BAR_S; exit is at bar i+1+off
    return ev.ts + (1 + off) * BAR_S


def funding_adjusted_config(events, rule: RuleParams, geom_idx: int,
                            geom: Geometry, window, cfg: QuantConfig = DEFAULT,
                            funding_dir: Optional[str] = None,
                            settlements_dir: Optional[str] = None,
                            slip_mult: float = 1.0,
                            fee_mult: float = 1.0) -> Dict[str, Any]:
    """Friction + funding net R for one (rule, geometry) in a day window.

    Mirrors ``research.eval_config`` (same event selection, same friction
    model, same triple-barrier slots keyed by the TRADE side from the
    rule's policy) and adds the venue-attributed funding cost for each
    event's [entry, actual exit) window:

        net_r = (gross - round_trip_friction - funding_cost) / stop_distance

    Events whose funding cost cannot be fully attributed (coin archive
    missing, or a funding event inside the window unattributable) are
    EXCLUDED from the metric and counted -- never zero-filled.

    Returns a summary dict with EXPLICIT DENOMINATORS:
      n_events               selected + tradeable events in the window
      n_funding_adjusted     events with a fully attributed funding cost
                             (= the metric's denominator)
      n_excluded_missing_coin events on coins with no cost archive rows
      n_excluded_incomplete  events with unattributable funding events
      coverage               n_funding_adjusted / n_events
      missing_coins          sorted coins counted as missing
    and the funding-adjusted metric itself (mean_r / ci_lo / ci_hi / t /
    days / coins / tp / sl / time) over the attributed events.  When
    n_funding_adjusted is 0 every metric field is None -- a
    funding-adjusted label never carries a friction-only number.
    """
    lo, hi = window
    sel = R._select(events, rule, lo, hi)
    values, days, kinds = [], [], [0, 0, 0]
    coins = set()
    missing_coins = set()
    n_events = n_missing = n_incomplete = 0

    for ev in sel:
        side = trade_side(ev, rule)
        k = geom_idx * 2 + (0 if side > 0 else 1)
        friction = cfg.cost.round_trip(ev.qv24, slip_mult, fee_mult)
        if friction is None:
            continue                       # untradeable: not an event
        exit_ts = _event_exit_ts(ev, geom_idx, rule)
        if exit_ts is None:
            # Fall back to the full horizon for this geometry
            exit_ts = ev.ts + geom.horizon_bars * BAR_S
        open_ts = ev.ts + BAR_S
        fc = funding_cost(ev.coin, open_ts, exit_ts,
                          funding_dir=funding_dir,
                          settlements_dir=settlements_dir, side=side)
        n_events += 1
        if fc.missing:
            n_missing += 1
            missing_coins.add(ev.coin)
            continue
        if fc.incomplete or fc.cost is None:
            n_incomplete += 1
            continue
        dd = B.stop_distance(ev.sigma, geom.horizon_bars, geom.k_stop)
        values.append(B.net_r(ev.gross[k], dd, friction + fc.cost))
        days.append(int(ev.day))
        kinds[ev.kind[k]] += 1
        coins.add(ev.coin)

    n = len(values)
    out: Dict[str, Any] = {
        "n_events": n_events,
        "n_funding_adjusted": n,
        "n_excluded_missing_coin": n_missing,
        "n_excluded_incomplete": n_incomplete,
        "coverage": (n / n_events) if n_events else 0.0,
        "missing_coins": sorted(missing_coins),
    }
    if n:
        m, c_lo, c_hi, nd = S.day_cluster_mean(values, days, B=1500)
        out.update({
            "mean_r": m, "ci_lo": c_lo, "ci_hi": c_hi, "days": nd,
            "coins": len(coins),
            "tp": kinds[0] / n, "sl": kinds[1] / n, "time": kinds[2] / n,
            "t": S.day_cluster_t(values, days),
        })
    else:
        out.update({
            "mean_r": None, "ci_lo": None, "ci_hi": None, "days": 0,
            "coins": 0, "tp": None, "sl": None, "time": None, "t": None,
        })
    return out


def evaluate(data_dir: str, funding_dir: Optional[str] = None,
             settlements_dir: Optional[str] = None,
             cfg: QuantConfig = DEFAULT,
             spend_holdout: bool = False,
             out_dir: Optional[str] = None,
             coins: Optional[List[str]] = None) -> EvaluationReport:
    """Run the full WS5 evaluation on production bar data.

    Steps:
      1. collect_events over the bar archive (reuses research.collect_events).
         ``coins`` restricts the traded universe (BTC is always loaded as the
         market factor); ``None`` means every archived altcoin.
      2. resolve splits + assert no leakage
      3. run_stage_dev_val (dev -> val, with max-t multiplicity control);
         selection uses friction-only metrics
      4. funding-adjusted dev/val for the SELECTED config (reporting only)
      5. if spend_holdout and validation kept the config, spend the
         one-shot holdout (friction + funding-adjusted + 2x-slip
         sensitivity, all within the same single look)
      6. record reproducibility metadata (config hash, cost hash, git rev,
         bar digests) and the explicit funding-coverage denominators

    Returns an EvaluationReport.  All output goes to ``out_dir`` (or a
    temp dir) -- production data/ is never written.
    """
    # 1. Collect events from the archive
    events, meta = R.collect_events(data_dir, cfg, coins=coins, progress=False)

    # 2. Resolve splits + check leakage
    splits = resolve_splits(meta, cfg)
    violations = check_no_leakage(events, splits)

    # 3. Dev -> Val (friction-only; this is where selection happens)
    # Guard: if too few events, run_stage_dev_val would produce empty
    # configs and spa_max_t would fail. Handle that explicitly.
    if len(events) < 50:
        dev_val = {
            "bounds": cfg.splits.bounds(meta["n_days"]),
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

    selected = dev_val.get("selected", {})
    rule_obj = selected.get("rule_obj")
    bounds = dev_val.get("bounds") or cfg.splits.bounds(meta["n_days"])

    # 4. Funding-adjusted dev/val for the selected config (REPORTING ONLY:
    # selection already happened on friction numbers; no val tuning).
    fa_dev = fa_val = None
    if rule_obj is not None:
        geom_idx, geom = selected["geom_idx"], selected["geom"]
        fa_dev = funding_adjusted_config(
            events, rule_obj, geom_idx, geom, bounds["dev"], cfg,
            funding_dir, settlements_dir)
        fa_val = funding_adjusted_config(
            events, rule_obj, geom_idx, geom, bounds["val"], cfg,
            funding_dir, settlements_dir)

    # 5. Holdout (one-shot, only if val kept the config)
    holdout = None
    holdout_slip2x = None
    fa_holdout = None
    fa_holdout_slip2x = None
    if spend_holdout and rule_obj is not None:
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
                # holdout_once returns the two summaries directly (and
                # writes the one-shot lock); it is NOT re-summarized.
                h, h2 = R.holdout_once(events, meta, selected, out_dir, cfg)
                holdout, holdout_slip2x = h, h2
                # Same single look: funding-adjusted holdout + 2x-slip
                # sensitivity for the selected config. Only computed when
                # holdout_once SUCCEEDED -- never a second look.
                geom_idx, geom = selected["geom_idx"], selected["geom"]
                fa_holdout = funding_adjusted_config(
                    events, rule_obj, geom_idx, geom, bounds["holdout"], cfg,
                    funding_dir, settlements_dir)
                fa_holdout_slip2x = funding_adjusted_config(
                    events, rule_obj, geom_idx, geom, bounds["holdout"], cfg,
                    funding_dir, settlements_dir, slip_mult=2.0)
            except SystemExit:
                pass  # lock refuses a second look; holdout stays None

    # 6. Cost-coverage accounting + reproducibility metadata
    # Coins with events but NO rows in either cost archive (surfaced,
    # never zero-filled).
    coins_in_events = sorted(set(e.coin for e in events))
    missing_cost_coins = [c for c in coins_in_events
                          if coin_cost_data_missing(c, funding_dir,
                                                    settlements_dir)]

    # Excluded-and-counted totals across every window whose
    # funding-adjusted metric was actually computed (holdout slip2x is
    # the same events as the holdout and is not double-counted).
    fa_blocks = [b for b in (fa_dev, fa_val, fa_holdout) if b is not None]
    incomplete_events = sum(b["n_excluded_incomplete"] for b in fa_blocks)
    incomplete_denominator = sum(b["n_events"] for b in fa_blocks)

    ch = cfg.config_hash()
    csh = cost_hash(funding_dir, settlements_dir, cfg)

    # Bar digests for ALL coins (no cap) whose data contributes to metrics
    bar_digests = {}
    for coin in ["BTC", "ETH"] + coins_in_events:
        d = _bar_digest(data_dir, coin)
        if d is not None:
            bar_digests[coin] = d

    # Archive digests for every coin whose data contributes to reported
    # metrics, covering both funding observations and settlements.
    funding_digests = {}
    settlement_digests = {}
    contributing_coins = sorted(set(coins_in_events) | {"BTC", "ETH"})
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
        dev_funding_adjusted=fa_dev,
        val_funding_adjusted=fa_val,
        holdout_funding_adjusted=fa_holdout,
        holdout_slip2x_funding_adjusted=fa_holdout_slip2x,
        leakage_violations=violations,
        missing_cost_coins=missing_cost_coins,
        git_rev=_git_rev(),
        bar_digests=bar_digests,
        funding_digests=funding_digests,
        settlement_digests=settlement_digests,
        incomplete_boundary_events=incomplete_events,
        incomplete_boundary_denominator=incomplete_denominator,
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
