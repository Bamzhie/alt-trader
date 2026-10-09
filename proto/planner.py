"""
Trade plan generation.

Direction-symmetric by construction: LONG and SHORT plans are exact mirrors.
Leverage is COMPUTED from stake, stop distance and a maintenance-margin
assumption - never defaulted - then clamped to the operator's band, flagging
under-scaling instead of silently inflating.

This module places no orders and holds no keys. It emits text for review.
"""

from dataclasses import dataclass, field
from typing import Optional

# Operator parameters (spec SS5.2, SS4)
TAKER_FEE_PCT = 0.05          # per side, MEXC measured baseline
FUNDING_CYCLE_HOURS = 8
# Liquidation buffer: a perp position is liquidated when the loss consumes the
# whole margin. At leverage L a stop at distance d (fraction of notional) loses
# d*L of margin, so liquidation is at d*L = 1.0. We require the stop to fire
# with headroom, at or before HALF the margin, which is the LIQ_BUFFER.
# (A "maintenance margin" percentage is the venue's margin CALL level, not the
# liquidation point - conflating the two is what broke the first version.)
LIQ_BUFFER = 2.0              # stop must cost <= 1/2 of margin
MAX_LEVERAGE = 50
HIGH_LEVERAGE_WARN = 40     # at/above this, flag wick/slippage fragility
MIN_LEVERAGE_FLOOR = 20       # operator's stated band; planner may go BELOW and flag


@dataclass
class Plan:
    coin: str
    direction: str = "NEUTRAL"
    entry_low: float = 0.0
    entry_high: float = 0.0
    stop: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    r_multiple_tp1: float = 2.0
    r_multiple_tp2: float = 5.0
    leverage: int = 1
    notional: float = 0.0
    margin: float = 0.0
    max_loss: float = 0.0
    costs: float = 0.0
    funding_note: str = ""
    break_even_pct: float = 0.0
    reward_risk: float = 0.0
    warnings: list = field(default_factory=list)
    tradeable: bool = True

    @property
    def valid(self):
        return self.direction in ("LONG", "SHORT") and self.entry_low > 0 and self.stop > 0


def plan_levels(direction, price, stop_reference, atr_value=None):
    """
    Entry band inside the spread, structural stop, and R-multiple targets.

    stop_reference should be a swing low (LONG) or swing high (SHORT); this
    function does not choose it, it only places levels relative to it.
    """
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")

    # Entry band: a small band around current price, tighter than typical
    # spread so a limit fill is realistic.
    band = price * 0.001
    if direction == "LONG":
        entry_low, entry_high = price - band, price + band
        stop = stop_reference
        tp1 = price + 2 * (price - stop)
        tp2 = price + 5 * (price - stop)
    else:
        entry_low, entry_high = price - band, price + band
        stop = stop_reference
        tp1 = price - 2 * (stop - price)
        tp2 = price - 5 * (stop - price)

    # Targets must be on the correct side of entry, or the plan is nonsense.
    if direction == "LONG":
        if not (stop < entry_low < tp1 < tp2):
            raise ValueError("LONG levels out of order")
    else:
        if not (stop > entry_high > tp1 > tp2):
            raise ValueError("SHORT levels out of order")

    return {"entry_low": entry_low, "entry_high": entry_high, "stop": stop,
            "tp1": tp1, "tp2": tp2}


def compute_leverage(stake, risk_per_trade_pct, stop_distance_pct,
                     max_leverage=MAX_LEVERAGE):
    """
    Highest leverage at which a stop at stop_distance_pct still triggers
    BEFORE liquidation.

    Physics. A position with notional N at leverage L holds N/L of margin. A
    stop at distance d (fraction of NOTIONAL) realizes a loss of d*N, which is
    d*L as a fraction of margin. Liquidation occurs when that fraction reaches
    1.0. Requiring the stop to fire with headroom - at most half the margin:

        d * L  <=  1 / LIQ_BUFFER      =>      L  <=  1 / (LIQ_BUFFER * d)

    The returned leverage is the floor of that bound and max_leverage. It is
    never a default: a wide stop genuinely forces low leverage, and at 1x a
    stop may never trigger at all because there is no liquidation before 100%
    of margin, so the formula's bound naturally permits it.
    """
    if stop_distance_pct <= 0:
        return 1
    d = stop_distance_pct / 100.0
    safe = (1.0 / LIQ_BUFFER) / d
    lev = int(min(max_leverage, max(1.0, safe)))
    return lev


def copy_counter_trend(sc, plan):
    """Copy the scorer's counter-trend label into plan warnings.

    Warnings are surfaced, never swallowed (spec SS4): the scorer can only
    annotate its own Scorecard, so the planner carries the label onward.
    """
    for n in sc.notes:
        if isinstance(n, str) and n.startswith("counter-trend:"):
            plan.warnings.append(n)
    return plan


def build_plan(sc, stake, risk_per_trade_pct=2.0, swing_ref=None,
               fee_pct=TAKER_FEE_PCT, funding_rate=0.0, funding_cap=0.0018,
               hold_hours=24, max_leverage=MAX_LEVERAGE):
    """
    Assemble a full plan from a Scorecard.

    sc must carry: coin, direction, price, funding_rate, min_notional.
    swing_ref: structural stop reference (swing low for LONG, high for SHORT).
    max_leverage: operator cap threaded to compute_leverage (default the
    venue maximum, i.e. today's behaviour exactly — cap plumbing only).
    """
    if not swing_ref:
        p = Plan(coin=sc.coin, direction=sc.direction,
                 warnings=["no structural stop reference available — cannot plan safely"])
        return copy_counter_trend(sc, p)

    lv = plan_levels(sc.direction, sc.price, swing_ref)

    entry_mid = (lv["entry_low"] + lv["entry_high"]) / 2
    stop_distance_pct = abs(entry_mid - lv["stop"]) / entry_mid * 100

    lev = compute_leverage(stake, risk_per_trade_pct, stop_distance_pct,
                           max_leverage=max_leverage)
    under_scaled = lev < MIN_LEVERAGE_FLOOR

    # Notional from risk budget: risk_$ = notional * stop_distance; cap by stake
    risk_budget = stake * risk_per_trade_pct / 100.0
    stop_frac = stop_distance_pct / 100.0
    notional_by_risk = risk_budget / stop_frac if stop_frac > 0 else 0.0
    # Also cap notional so margin never exceeds the stake itself
    notional_by_stake = stake * lev
    notional = min(notional_by_risk, notional_by_stake)

    max_loss = notional * stop_frac
    margin = notional / lev if lev else notional

    # Costs: taker both sides on the notional, plus funding for the hold.
    taker_cost = notional * (fee_pct / 100.0) * 2
    settles = max(1.0, hold_hours / FUNDING_CYCLE_HOURS)
    # Funding is DIRECTIONAL. Positive funding means LONGS pay shorts receive.
    # So a long's funding cost is +notional*rate and a short's is the negative
    # of it (money paid TO the short, hence negative cost). Without this sign
    # a short would be charged the same as a long - a directional bias that
    # silently overstates the cost of every short plan.
    funding_flow = notional * funding_rate * settles
    funding_cost = funding_flow if sc.direction == "LONG" else -funding_flow
    costs = taker_cost + funding_cost

    # Break-even: the adverse move needed to cover costs (signed by direction)
    if notional > 0:
        break_even_pct = (costs / notional) * 100
    else:
        break_even_pct = 0.0

    # Reward:risk to tp1
    reward = abs(lv["tp1"] - entry_mid)
    risk = abs(entry_mid - lv["stop"])
    rr = reward / risk if risk > 0 else 0.0

    if funding_rate > 0:
        funding_note = f"funding +{funding_rate*100:.4f}% → longs PAY, shorts RECEIVE"
    elif funding_rate < 0:
        funding_note = f"funding {funding_rate*100:.4f}% → shorts PAY, longs RECEIVE"
    else:
        funding_note = "funding ~0"

    p = Plan(coin=sc.coin, direction=sc.direction,
             entry_low=lv["entry_low"], entry_high=lv["entry_high"], stop=lv["stop"],
             tp1=lv["tp1"], tp2=lv["tp2"], leverage=lev, notional=notional,
             margin=margin, max_loss=max_loss, costs=costs, funding_note=funding_note,
             break_even_pct=break_even_pct, reward_risk=rr)

    # ---- warnings (surfaced, never swallowed) ----
    if under_scaled:
        p.warnings.append(
            f"leverage {lev}x is BELOW your {MIN_LEVERAGE_FLOOR}x floor — safe value at "
            f"this stake; under-scaled, not inflated")
    if sc.min_notional is not None:
        min_margin = sc.min_notional / lev if lev else None
        if min_margin is not None and min_margin > stake:
            p.tradeable = False
            p.warnings.append(
                f"min notional ${sc.min_notional:.4f} needs ~${min_margin:.4f} "
                f"margin at {lev}x > stake ${stake:.2f} — WATCH-ONLY until "
                f"stake grows")
    if notional < (sc.min_notional or 0) and sc.min_notional:
        p.tradeable = False
        p.warnings.append(
            f"computed notional ${notional:.4f} below venue min ${sc.min_notional:.4f}")
    if max_loss > stake:
        p.warnings.append(
            f"max loss ${max_loss:.4f} EXCEEDS stake ${stake:.2f} — reduce risk_pct")
    if break_even_pct > stop_distance_pct:
        p.warnings.append(
            f"break-even move {break_even_pct:.2f}% is a large fraction of the "
            f"{stop_distance_pct:.2f}% stop — costs dominate this trade")
    if lev >= HIGH_LEVERAGE_WARN:
        p.warnings.append(
            f"leverage {lev}x is near the {MAX_LEVERAGE}x venue maximum with "
            f"only a {stop_distance_pct:.2f}% stop — almost no room for wicks "
            f"or slippage; a fill even slightly past the stop costs far more "
            f"than the ${max_loss:.4f} max loss")
    for v in sc.vetoes:
        p.warnings.append(f"vetoed: {v.reason}")
    copy_counter_trend(sc, p)
    return p


def format_plan(p, sc=None):
    """Human-readable plan. Direction-symmetric wording."""
    if p.direction == "SHORT":
        arrow, stop_desc, tgt_dir = "▼", "above structure", "▼"
    else:
        arrow, stop_desc, tgt_dir = "▲", "below structure", "▲"

    lines = []
    lines.append(f"  Direction      {arrow} {p.direction}")
    lines.append(f"  Entry          {p.entry_low:.8g} – {p.entry_high:.8g}   (limit band, hypothetical — no fill model)")
    lines.append(f"  Stop loss      {p.stop:.8g}   ({stop_desc})")
    lines.append(f"  TP1            {p.tp1:.8g}   (2R)  → cover 50%")
    lines.append(f"  TP2            {p.tp2:.8g}   (5R)  → cover 30%, trail rest")
    lines.append(f"  Leverage       {p.leverage}x" + (f"   (operator band {MIN_LEVERAGE_FLOOR}-{MAX_LEVERAGE}x)" if p.leverage < MIN_LEVERAGE_FLOOR else ""))
    lines.append(f"  Position size  {p.notional:.6g} notional → ~{p.margin:.6g} margin")
    lines.append(f"  Max loss       {p.max_loss:.6g}")
    lines.append(f"  Costs          taker {TAKER_FEE_PCT}%×2 + funding   (total {p.costs:.6g})")
    sign = "+" if p.direction == "LONG" else "-"
    lines.append(f"  Break-even     {sign}{p.break_even_pct:.3f}%  (signed by direction)")
    lines.append(f"  R:R            {p.reward_risk:.2f} : 1")
    lines.append(f"  Funding        {p.funding_note}")
    if p.warnings:
        lines.append("  ── warnings ──")
        for w in p.warnings:
            prefix = "  ⚠ " if not w.startswith("vetoed") else "  ⨯ "
            lines.append(prefix + w)
    return "\n".join(lines)