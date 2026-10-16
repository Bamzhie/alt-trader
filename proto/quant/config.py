"""Frozen, versioned configuration for the quant module.

Every number here is a DECLARED PRIOR or an explicit assumption, not a fitted
value.  Changing any field changes `config_hash()`; results are only
comparable within one hash.  Edit through a new rule id, never in place.
"""
import hashlib
import json
from dataclasses import dataclass, field, asdict

RULE_VERSION = "quant-v1"
BAR_S = 300                    # 5-minute bars
BARS_PER_DAY = 288


@dataclass(frozen=True)
class CostModel:
    """Round-trip friction as a fraction of notional.

    fee_per_side: taker fee (0.05% is the planner's constant; the operator has
    not confirmed their MEXC tier - set it before trusting any net number).
    slip tiers: per-side slippage by trailing-24h quote volume (USDT), an
    ASSUMPTION about spread + impact for a small account, not a measurement.
    Funding is NOT modeled (disclosed wherever net figures are shown).
    """
    fee_per_side: float = 0.0005
    # (min 24h quote volume, per-side slippage), checked top-down
    slip_tiers: tuple = ((30e6, 0.00015), (10e6, 0.0003),
                         (3e6, 0.0006), (1e6, 0.0012))
    min_qv24: float = 1e6      # below this: not tradeable, no cost model

    def slip(self, qv24):
        for floor, s in self.slip_tiers:
            if qv24 >= floor:
                return s
        return None            # untradeable

    def round_trip(self, qv24, slip_mult=1.0, fee_mult=1.0):
        s = self.slip(qv24)
        if s is None:
            return None
        return 2.0 * (self.fee_per_side * fee_mult + s * slip_mult)


@dataclass(frozen=True)
class FeatureParams:
    lam_sigma: float = 0.985       # EWMA of Garman-Klass variance (~46-bar half-life)
    lam_beta: float = 0.999        # EW BTC beta (~2.4 day half-life)
    beta_cap: float = 3.0
    channel_bars: int = 48         # prior bars forming the breakout channel
    rvol_lags_days: int = 14       # same-slot history for time-of-day RVOL
    rvol_min_lags: int = 5
    rvol_bars: int = 3             # volume summed over the latest N bars
    warmup_bars: int = 6 * BARS_PER_DAY   # no features before this much history
    cusum_k: float = 0.5
    cusum_cooldown: int = 12


@dataclass(frozen=True)
class RuleParams:
    """One candidate rule + side policy. `policy` is part of the identity."""
    family: str = "breakout_start"      # or "residual_shock"
    policy: str = "fade"                # "fade" | "cont" (cont = control)
    rvol_min: float = 4.0               # breakout_start only
    cusum_h: float = 5.0                # residual_shock only


@dataclass(frozen=True)
class Geometry:
    """Stop/target distance in units of sigma_bar * sqrt(T)."""
    horizon_bars: int = 144             # T: time barrier (bars from entry)
    k_stop: float = 3.0
    r_tp: float = 1.0                   # target distance = r_tp * stop distance

    def key(self):
        return f"T{self.horizon_bars}_ks{self.k_stop:g}_R{self.r_tp:g}"


# Pre-declared search grid (kept small on purpose; its size feeds the
# multiplicity test).  Never extended after seeing validation/holdout.
GRID_HORIZONS = (48, 144, 288)
GRID_K_STOP = (1.0, 1.5, 2.0, 3.0)
GRID_R_TP = (1.0, 2.0, 3.0)
GRID_RVOL = (2.0, 4.0)


def grid_geometries():
    return [Geometry(t, ks, r) for t in GRID_HORIZONS for ks in GRID_K_STOP
            for r in GRID_R_TP]


@dataclass(frozen=True)
class Splits:
    """Chronological splits in whole days from the archive start.

    Embargo >= longest label horizon (288 bars = 1 day) + entry bar, so no
    label window crosses a boundary.  Holdout is evaluated ONCE, last.
    """
    first_event_day: float = 6.0
    dev_end_day: float = 61.0
    embargo_days: float = 2.0
    val_len_days: float = 14.0
    # holdout starts after another embargo and runs to the end of the data

    def bounds(self, n_days):
        dev = (self.first_event_day, self.dev_end_day)
        v0 = self.dev_end_day + self.embargo_days
        val = (v0, v0 + self.val_len_days)
        h0 = val[1] + self.embargo_days
        hold = (h0, float(n_days))
        return {"dev": dev, "val": val, "holdout": hold}


@dataclass(frozen=True)
class Selector:
    """Emission budget and hurdle (pre-declared; a ceiling, never a quota)."""
    max_per_day: int = 25
    min_ev_r: float = 0.10             # net expected R hurdle after costs
    max_cost_r: float = 0.10           # friction ceiling: cost / stop <= 0.10R
    max_per_coin_per_day: int = 1
    shadow_max_per_day: int = 400      # per rule; keeps the research log bounded
    max_signal_age_bars: int = 3       # older candidates are history, not alerts
    stale_coin_bars: int = 3           # coin lagging BTC by more than this is stale
    pause_stale_fraction: float = 0.30  # global pause if this share of coins is stale
    cooldown_bars: int = 144           # per coin, per rule
    min_gap_bars_pause: int = 3        # global pause if > this many bars stale


@dataclass(frozen=True)
class RiskParams:
    """Sizing assumptions (operator answers: 5% of portfolio risk per trade,
    cross margin).  Sizing is informational: this app never trades."""
    risk_frac: float = 0.05
    max_leverage: float = 10.0         # conservative cap; cross margin shares equity
    max_open_risk_frac: float = 0.20   # aggregate open risk across signals
    max_corr_cluster_open: int = 3     # open signals per correlation cluster


@dataclass(frozen=True)
class QuantConfig:
    version: str = RULE_VERSION
    cost: CostModel = field(default_factory=CostModel)
    feat: FeatureParams = field(default_factory=FeatureParams)
    splits: Splits = field(default_factory=Splits)
    selector: Selector = field(default_factory=Selector)
    risk: RiskParams = field(default_factory=RiskParams)

    def to_json(self):
        return json.dumps(asdict(self), sort_keys=True, default=list)

    def config_hash(self):
        return hashlib.sha256(self.to_json().encode()).hexdigest()[:12]


DEFAULT = QuantConfig()
