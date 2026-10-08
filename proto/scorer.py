"""
Scorer: turns raw market state into (score, direction, explanation).

Contract, per the design spec:
  - score measures SIGNAL QUALITY (strength of evidence of early abnormal
    activity). It never measures or targets a return.
  - direction is derived from evidence and is symmetric: mirrored evidence
    produces mirrored direction with identical score.
  - early-ness is a first-class positive input; a mature move scores lower than
    the same abnormality at onset.
  - vetoes are returned with reasons so the UI can show what was filtered.
"""

from dataclasses import dataclass, field
from typing import Optional

from . import indicators as ind

# Weights are configuration, not code constants (spec SS3.3).
WEIGHTS = {
    "volume_price": 0.40,
    "book": 0.30,
    "oi_funding": 0.30,
}

# Screener veto thresholds (spec SS3.2)
MIN_QUOTE_VOLUME_24H = 250_000.0   # USDT
MAX_SPREAD_PCT = 3.0
LATE_MOVE_PCT = 12.0               # 1h move beyond this is a chase, not an entry


@dataclass
class Veto:
    code: str
    reason: str


@dataclass
class Scorecard:
    coin: str
    venue: str = "MEXC"
    score: float = 0.0                    # 0..100 signal quality
    lean: float = 0.0                     # -1 bearish .. +1 bullish
    direction: str = "NEUTRAL"            # LONG / SHORT / NEUTRAL
    earlyness: float = 0.0                # 0..1
    magnitude_parts: dict = field(default_factory=dict)
    lean_parts: dict = field(default_factory=dict)
    vetoes: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    min_notional: Optional[float] = None
    tradeable: bool = True
    funding_rate: float = 0.0
    oi_change_pct: Optional[float] = None
    price: float = 0.0
    change_24h_pct: float = 0.0
    quote_vol_24h: float = 0.0
    spread_pct: float = 0.0

    @property
    def actionable(self):
        return (not self.vetoes) and self.tradeable and self.direction != "NEUTRAL"


def book_component(bids, asks):
    """(magnitude 0..1, lean -1..+1) from order book."""
    mag, lean = ind.book_skew(bids, asks)
    thin = ind.book_thinness(bids, asks)
    # A wide skew on a THIN opposing stack is more informative than a wide
    # skew on a deep, balanced book. Unsigned, so symmetric.
    mag = max(0.0, min(1.0, mag * (0.6 + 0.4 * thin)))
    return mag, lean


def volume_price_component(bars):
    """
    (magnitude 0..1, lean -1..+1).
    Lean is averaged across genuinely directional series; magnitude blends
    unsigned abnormality measures. MACD contributes magnitude only.
    """
    ve = ind.volume_expansion(bars)          # unsigned, base volume
    don = ind.donchian_position(bars)
    ema_rel = ind.ema_relationship(bars)
    obv = ind.obv_slope(bars)
    macd = ind.macd_histogram(bars)         # acceleration -> magnitude only

    lean = (don * 0.35 + ema_rel * 0.35 + obv * 0.30)
    lean = max(-1.0, min(1.0, lean))

    r = ind.rsi(bars)                       # 0..100 -> directional 0..1
    rsi_dir = (r - 50.0) / 50.0
    lean = max(-1.0, min(1.0, lean * 0.8 + rsi_dir * 0.2))

    # Magnitude: how abnormal is activity? All terms unsigned.
    atrp = min(1.0, ind.atr_pct(bars) / 0.05)   # 5% ATR% ~ maximal
    mag = (ve * 0.40 + abs(don) * 0.20 + abs(ema_rel) * 0.15
           + abs(macd) * 0.15 + atrp * 0.10)
    mag = max(0.0, min(1.0, mag))

    return mag, lean, {
        "volume_expansion": round(ve, 3),
        "donchian": round(don, 3),
        "ema_rel": round(ema_rel, 3),
        "obv_slope": round(obv, 3),
        "rsi": round(r, 1),
        "macd_hist": round(macd, 3),
        "atr_pct": round(ind.atr_pct(bars), 5),
    }


def oi_funding_component(price_change_pct, oi_change_pct, funding_rate, funding_cap):
    """
    (magnitude 0..1, lean -1..+1).

    OI and price agreeing -> new money in that direction (continuation).
    OI and price diverging -> the move is being driven by position closing,
    which is weaker evidence and leans against the price direction.
    Extreme funding marks crowding on the opposite side of the flow.
    """
    if oi_change_pct is None:
        return 0.0, 0.0, {"oi": None, "funding": round(funding_rate, 6), "oi_available": False}

    oi_mag = min(1.0, abs(oi_change_pct) / 15.0)
    fund_ratio = abs(funding_rate) / funding_cap if funding_cap > 0 else 0.0
    fund_mag = min(1.0, fund_ratio)

    sign_oi = 1.0 if oi_change_pct > 0 else (-1.0 if oi_change_pct < 0 else 0.0)
    sign_px = 1.0 if price_change_pct > 0 else (-1.0 if price_change_pct < 0 else 0.0)

    if sign_oi == 0 or sign_px == 0:
        lean = 0.0
        agreement = 0.0
    elif sign_oi == sign_px:
        lean = sign_px                       # new longs or new shorts, aligned
        agreement = 1.0
    else:
        # Divergent: OI up while price down = shorts adding, lean bearish but
        # with reduced conviction than agreement.
        lean = sign_px * 0.5
        agreement = -1.0

    # Crowded funding leans AGAINST the crowded side: longs crowded (positive
    # funding) is a bearish tell, and the mirror for shorts.
    if fund_ratio > 0.25:
        lean -= (1.0 if funding_rate > 0 else -1.0) * min(0.35, (fund_ratio - 0.25) * 0.4)

    lean = max(-1.0, min(1.0, lean))
    mag = max(0.0, min(1.0, 0.65 * oi_mag + 0.35 * fund_mag))
    return mag, lean, {
        "oi_change_pct": round(oi_change_pct, 2),
        "funding": round(funding_rate, 6),
        "funding_pct_of_cap": round(fund_ratio, 3),
        "agreement": agreement,
        "oi_available": True,
    }


def apply_vetoes(sc, bars, quote_vol_24h, spread_pct, change_1h_pct):
    """Hard disqualifiers. Each records a machine code and a human reason."""
    if quote_vol_24h < MIN_QUOTE_VOLUME_24H:
        sc.vetoes.append(Veto("low_volume",
                              f"24h quote volume ${quote_vol_24h:,.0f} below floor"))
    if spread_pct > MAX_SPREAD_PCT:
        sc.vetoes.append(Veto("wide_spread", f"spread {spread_pct:.2f}% too wide"))
    if abs(change_1h_pct) >= LATE_MOVE_PCT:
        sc.vetoes.append(Veto("late_move",
                              f"already moved {change_1h_pct:+.1f}% in 1h — late signal, not an entry"))
    if not bars or len(bars) < 30:
        sc.vetoes.append(Veto("thin_history",
                              f"only {len(bars) if bars else 0} bars — insufficient warm-up"))
    return sc


def score_coin(coin, bars, bids, asks, *,
               quote_vol_24h=0.0, spread_pct=0.0, change_1h_pct=0.0,
               price=0.0, change_24h_pct=0.0,
               funding_rate=0.0, funding_cap=0.0018,
               oi_change_pct=None,
               min_notional=None, venue="MEXC", tier=2):
    """Full scoring pass for one coin. Returns a Scorecard."""
    sc = Scorecard(coin=coin, venue=venue, price=price,
                   change_24h_pct=change_24h_pct,
                   quote_vol_24h=quote_vol_24h, spread_pct=spread_pct,
                   funding_rate=funding_rate, oi_change_pct=oi_change_pct,
                   min_notional=min_notional)

    apply_vetoes(sc, bars, quote_vol_24h, spread_pct, change_1h_pct)

    if sc.min_notional is not None:
        sc.tradeable = True  # planner compares against stake later

    if bars and len(bars) >= 30:
        vp_mag, vp_lean, vp_detail = volume_price_component(bars)
        bk_mag, bk_lean = book_component(bids, asks)
        oi_mag, oi_lean, oi_detail = oi_funding_component(
            change_1h_pct, oi_change_pct, funding_rate, funding_cap)

        sc.magnitude_parts = {"VOL": vp_mag, "BOOK": bk_mag, "OI": oi_mag}
        sc.lean_parts = {"VOL": vp_lean, "BOOK": bk_lean, "OI": oi_lean}
        sc.notes.append(f"vol/price: {vp_detail}")
        sc.notes.append(f"book: mag={bk_mag:.3f} lean={bk_lean:+.3f}")
        sc.notes.append(f"oi/funding: {oi_detail}")

        base = (WEIGHTS["volume_price"] * vp_mag
                + WEIGHTS["book"] * bk_mag
                + WEIGHTS["oi_funding"] * oi_mag)
        sc.earlyness = ind.earlyness(bars)
        sc.score = round(100.0 * base * (0.55 + 0.45 * sc.earlyness), 1)

        # Composite lean: weight by each signal's magnitude so a strong
        # directional reading outweighs a weak one.
        num = (WEIGHTS["volume_price"] * vp_lean * vp_mag
               + WEIGHTS["book"] * bk_lean * bk_mag
               + WEIGHTS["oi_funding"] * oi_lean * oi_mag)
        den = (WEIGHTS["volume_price"] * vp_mag
               + WEIGHTS["book"] * bk_mag
               + WEIGHTS["oi_funding"] * oi_mag)
        sc.lean = round(num / den, 3) if den > 1e-9 else 0.0
        sc.direction = "LONG" if sc.lean > 0.15 else ("SHORT" if sc.lean < -0.15 else "NEUTRAL")

        if tier == 2:
            sc.notes.append("TIER 2: UNVALIDATED — no deep history exists for this coin")

    return sc