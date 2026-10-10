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

import math
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
LATE_MOVE_24H_PCT = 35.0           # 24h verticality: by the time we see it, it's over

# MTF alignment (spec SS4)
MTF_LEAN_THRESHOLD = 0.15          # a lean must exceed this on every timeframe
MTF_ALIGN_BONUS = 8.0              # flat points, capped by the 100 ceiling


def mtf_alignment(lean_5m, lean_1h, lean_4h):
    """
    (aligned, counter_note) for the three timeframe leans.

    aligned: all three same sign and all |lean| > MTF_LEAN_THRESHOLD.
    counter_note: label (never a score effect) when the 5m and 4H leans
    oppose, each beyond the threshold: "counter-trend: 5m X vs 4H Y ...".

    A missing lean (None) can be neither aligned nor opposed - absent
    evidence earns no bonus and no warning. Leans are the SAME
    volume_price lean per timeframe, so signs compare like for like.
    """
    leans = (lean_5m, lean_1h, lean_4h)
    if any(x is None for x in leans):
        return False, None

    signs = [1 if x > 0 else (-1 if x < 0 else 0) for x in leans]
    strong = [abs(x) > MTF_LEAN_THRESHOLD for x in leans]

    aligned = all(strong) and len(set(signs)) == 1

    counter = None
    if strong[0] and strong[2] and signs[0] != signs[2]:
        d5 = "LONG" if signs[0] > 0 else "SHORT"
        d4 = "LONG" if signs[2] > 0 else "SHORT"
        counter = f"counter-trend: 5m {d5} vs 4H {d4} — elevated risk"
    return aligned, counter


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
    oi_notional: Optional[float] = None   # ~USDT open interest behind OIΔ%
    price: float = 0.0
    change_24h_pct: float = 0.0
    quote_vol_24h: float = 0.0
    spread_pct: float = 0.0
    plan: Optional[object] = None   # proto.planner.Plan, attached at scan
                                    # time for flagged cards (not relogged)

    # Shadow score `score-v3-dis` (docs/grok-review.md). Logged beside the
    # live v2 fields above; NEVER used for the flag, ranking, plans or
    # episode membership. None = not computed (too few bars / shadow error).
    score_v3: Optional[float] = None
    direction_v3: Optional[str] = None
    initiation: Optional[float] = None
    flow_state: Optional[str] = None
    oi_change_1h_pct: Optional[float] = None
    opening_for: Optional[int] = None
    opening_against: Optional[int] = None
    book_agree: Optional[int] = None
    funding_crowd: Optional[str] = None
    score_rule: Optional[str] = None
    v3_parts: dict = field(default_factory=dict)

    @property
    def actionable(self):
        return (not self.vetoes) and self.tradeable and self.direction != "NEUTRAL"

    def is_actionable(self, stake):
        """
        Stake-aware flag gate (spec SS4). `actionable` above stays
        stake-agnostic - it drives ranking and keeps existing callers
        working. A FLAG additionally requires a CONFIRMED minimum whose
        MARGIN fits the stake: min_notional None (unknown) is fail-closed,
        so a coin we cannot size never flags even though it still ranks
        and shadow-logs. An unknown stake fails closed the same way.

        Margin, not notional: at 50x a $2.48 minimum notional costs ~$0.05
        of margin - affordable on a $0.10 stake. fits_stake() holds the
        rule; the planner enforces per-trade reality with planned leverage.
        """
        if self.min_notional is None or stake is None:
            return False
        return self.actionable and fits_stake(self.min_notional, stake)


def fits_stake(min_notional, stake, leverage=None):
    """True when the venue minimum fits the stake AS MARGIN.

    min_notional / leverage <= stake. Default leverage is the venue maximum
    (most permissive — matches leveraged trading; the planner enforces
    per-trade reality with the planned leverage). None/garbage/zero inputs
    fail closed. Shared by the flag gate, picks, and WATCH display so the
    rule cannot drift between them.
    """
    if leverage is None:
        from .planner import MAX_LEVERAGE
        leverage = MAX_LEVERAGE
    try:
        mn, st, lv = float(min_notional), float(stake), float(leverage)
    except (TypeError, ValueError):
        return False
    if not (mn > 0 and st > 0 and lv > 0):
        return False
    return mn / lv <= st


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
        # Funding-only path (MEXC has no OI history): crowding still informs
        # lean, magnitude scaled to funding share so it never outscores OI+funding.
        fund_ratio = abs(funding_rate) / funding_cap if funding_cap > 0 else 0.0
        fund_mag = min(1.0, fund_ratio)
        lean = 0.0
        if fund_ratio > 0.25:
            lean = -((1.0 if funding_rate > 0 else -1.0)
                     * min(0.35, (fund_ratio - 0.25) * 0.4))
        mag = max(0.0, min(1.0, 0.35 * fund_mag))
        return mag, lean, {"oi": None, "funding": round(funding_rate, 6),
                           "funding_pct_of_cap": round(fund_ratio, 3),
                           "oi_available": False}

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


# ---------------------------------------------------------------------------
# Shadow score: directional initiation score, rule id `score-v3-dis`.
#
# Declared prior, frozen before any outcome is used - these are not fitted
# values. A change to any of them is a NEW rule id, never an edit of this one.
# Hypothesis: rank a coin by how recently a 5-minute one-sided flow started,
# and add points only from evidence that supports that same side.
# ---------------------------------------------------------------------------
V3_RULE = "score-v3-dis"
V3_NEUTRAL_BAND = 0.15        # |L5| at or below this -> NEUTRAL, score 0
V3_CHANNEL_BARS = 48          # prior completed 5m bars forming the channel
V3_AGE_TAU = 18.0             # initiation = exp(-age / tau), age in 5m bars
V3_BUILDING_INITIATION = 0.50  # flow building, range still holding
V3_BUILDING_VOL_RATIO = 2.0   # recent volume / trailing median
V3_FLAT_1H_PCT = 0.15         # |1h price change| below this -> no flow sign
V3_BOOK_AGREE_MIN = 0.20      # |book lean| needed to confirm
V3_FUNDING_CROWD_RATIO = 0.50  # |funding| / cap beyond this is a crowd
V3_OI_GAP_MIN_S = 45 * 60     # 1h OI sample spacing accepted
V3_OI_GAP_MAX_S = 90 * 60
V3_INNER = {"activity": 0.45, "location": 0.25, "opening": 0.20, "book": 0.10}
V3_OPENING_AGAINST_MULT = 0.50
V3_HTF_OPPOSED_MULT = 0.70
V3_HTF_ALIGN_BONUS = 6.0


def _sgn(x):
    return 1 if x > 0 else (-1 if x < 0 else 0)


def l5_lean(bars):
    """
    5-minute structure lean for the v3 shadow score, -1..+1.

    The v2 volume/price lean WITHOUT the extra RSI blend: Donchian, EMA gap
    and OBV only (RSI is a third copy of "where is price" and stays a
    displayed number). ATR% is a magnitude input in v2 and never enters a lean.
    """
    don = ind.donchian_position(bars)
    ema_rel = ind.ema_relationship(bars)
    obv = ind.obv_slope(bars)
    return max(-1.0, min(1.0, 0.35 * don + 0.35 * ema_rel + 0.30 * obv))


def flow_state_v3(change_1h_pct, oi_change_1h_pct, oi_gap_s=None):
    """
    (state, side) from a MATCHED 1-hour price change and 1-hour OI change.

      opening    OI rising  -> positions being opened; side = sign(price):
                               +1 new longs, -1 new shorts (OI rises for both)
      unwind     OI falling -> positions closing; side = sign(price):
                               +1 short covering, -1 long liquidation
      flat       OI unchanged, or the 1h price change is inside the flat band
                 (OI can rise because both sides opened - no flow sign)
      unavailable no 1h OI, or its two samples are not ~1h apart (45-90 min)

    side is 0 unless the state is opening/unwind.
    """
    if oi_change_1h_pct is None:
        return "unavailable", 0
    if oi_gap_s is not None and not (V3_OI_GAP_MIN_S <= oi_gap_s
                                     <= V3_OI_GAP_MAX_S):
        return "unavailable", 0
    if abs(change_1h_pct) < V3_FLAT_1H_PCT:
        return "flat", 0
    if oi_change_1h_pct > 0:
        return "opening", _sgn(change_1h_pct)
    if oi_change_1h_pct < 0:
        return "unwind", _sgn(change_1h_pct)
    return "flat", 0


def score_v3(bars, *, book_lean=0.0, change_1h_pct=0.0,
             oi_change_1h_pct=None, oi_gap_s=None,
             funding_rate=0.0, funding_cap=0.0018,
             lean_1H=None, lean_4H=None):
    """
    Directional initiation score `score-v3-dis`. Pure: bars + a few scalars
    in, a dict out. Needs completed 5m bars (the scanner already guarantees
    that). Direction comes from 5-minute structure only; book, funding and OI
    can confirm or mark a disagreement but never create or flip a side.
    """
    l5 = l5_lean(bars)
    direction = ("LONG" if l5 > V3_NEUTRAL_BAND
                 else "SHORT" if l5 < -V3_NEUTRAL_BAND else "NEUTRAL")
    side = _sgn(l5) if direction != "NEUTRAL" else 0

    # Flow: matched 1h windows. Logged even for a NEUTRAL card.
    state, flow_side = flow_state_v3(change_1h_pct, oi_change_1h_pct, oi_gap_s)
    opening_for = int(state == "opening" and side != 0 and flow_side == side)
    opening_against = int(state == "opening" and side != 0
                          and flow_side == -side)

    book_agree = int(side != 0 and _sgn(book_lean) == side
                     and abs(book_lean) > V3_BOOK_AGREE_MIN)

    # Funding is context, never score: continuation vs crowding-fade are
    # different hypotheses and neither has a measured weight.
    fund_ratio = abs(funding_rate) / funding_cap if funding_cap > 0 else 0.0
    funding_crowd = "neutral"
    if side != 0 and fund_ratio > V3_FUNDING_CROWD_RATIO and funding_rate != 0:
        funding_crowd = "with" if _sgn(funding_rate) == side else "against"

    # Initiation: age of the break, not distance to the extreme.
    age = None
    initiation = 0.0
    if side != 0:
        age = ind.breakout_age(bars, side, V3_CHANNEL_BARS)
        if age is not None:
            initiation = math.exp(-age / V3_AGE_TAU)
        else:
            ratio = ind.volume_ratio(bars)
            if ratio is not None and ratio >= V3_BUILDING_VOL_RATIO:
                initiation = V3_BUILDING_INITIATION

    activity = max(0.0, min(1.0, ind.volume_expansion(bars)))
    location = max(0.0, min(1.0, abs(l5)))
    inner = (V3_INNER["activity"] * activity + V3_INNER["location"] * location
             + V3_INNER["opening"] * opening_for
             + V3_INNER["book"] * book_agree)

    htf_opposed = bool(
        side != 0 and lean_4H is not None
        and abs(lean_4H) > MTF_LEAN_THRESHOLD and _sgn(lean_4H) == -side)
    htf_aligned = bool(
        side != 0 and lean_1H is not None and lean_4H is not None
        and abs(lean_1H) > MTF_LEAN_THRESHOLD
        and abs(lean_4H) > MTF_LEAN_THRESHOLD
        and _sgn(lean_1H) == side and _sgn(lean_4H) == side)

    if side == 0 or initiation <= 0.0:
        score = 0.0
    else:
        raw = 100.0 * initiation * inner
        if opening_against:
            raw *= V3_OPENING_AGAINST_MULT
        if htf_opposed:
            raw *= V3_HTF_OPPOSED_MULT   # stacks with opening_against
        if htf_aligned:
            raw = min(100.0, raw + V3_HTF_ALIGN_BONUS)
        score = round(raw, 1)

    return {
        "score_v3": score,
        "direction_v3": direction,
        "initiation": round(initiation, 4),
        "flow_state": state,
        "oi_change_1h_pct": (None if oi_change_1h_pct is None
                             else round(oi_change_1h_pct, 3)),
        "opening_for": opening_for,
        "opening_against": opening_against,
        "book_agree": book_agree,
        "funding_crowd": funding_crowd,
        "score_rule": V3_RULE,
        "parts": {"l5": round(l5, 3), "age": age,
                  "activity": round(activity, 3),
                  "location": round(location, 3), "inner": round(inner, 3),
                  "htf_opposed": htf_opposed, "htf_aligned": htf_aligned},
    }


def apply_vetoes(sc, bars, quote_vol_24h, spread_pct, change_1h_pct,
                 change_24h_pct=0.0):
    """Hard disqualifiers. Each records a machine code and a human reason."""
    if quote_vol_24h < MIN_QUOTE_VOLUME_24H:
        sc.vetoes.append(Veto("low_volume",
                              f"24h quote volume ${quote_vol_24h:,.0f} below floor"))
    if spread_pct > MAX_SPREAD_PCT:
        sc.vetoes.append(Veto("wide_spread", f"spread {spread_pct:.2f}% too wide"))
    if abs(change_1h_pct) >= LATE_MOVE_PCT:
        sc.vetoes.append(Veto("late_move",
                              f"already moved {change_1h_pct:+.1f}% in 1h — late signal, not an entry"))
    # 24h verticality (spec SS3.2): a coin that has already run 35% in a day
    # is a chase even when the last hour looks calm. Same machine code as the
    # 1h rule; only one late_move reason is recorded so veto_codes stays
    # readable downstream.
    if (abs(change_24h_pct) >= LATE_MOVE_24H_PCT
            and not any(v.code == "late_move" for v in sc.vetoes)):
        sc.vetoes.append(Veto("late_move",
                              f"already moved {change_24h_pct:+.1f}% in 24h — vertical move, not an entry"))
    if not bars or len(bars) < 30:
        sc.vetoes.append(Veto("thin_history",
                              f"only {len(bars) if bars else 0} bars — insufficient warm-up"))
    return sc


def score_coin(coin, bars, bids, asks, *,
               quote_vol_24h=0.0, spread_pct=0.0, change_1h_pct=0.0,
               price=0.0, change_24h_pct=0.0,
               funding_rate=0.0, funding_cap=0.0018,
               oi_change_pct=None, oi_notional=None,
               lean_1H=None, lean_4H=None,
               oi_change_1h_pct=None, oi_gap_s=None,
               min_notional=None, venue="MEXC", tier=2):
    """Full scoring pass for one coin. Returns a Scorecard."""
    sc = Scorecard(coin=coin, venue=venue, price=price,
                   change_24h_pct=change_24h_pct,
                   quote_vol_24h=quote_vol_24h, spread_pct=spread_pct,
                   funding_rate=funding_rate, oi_change_pct=oi_change_pct,
                   oi_notional=oi_notional,
                   min_notional=min_notional)

    apply_vetoes(sc, bars, quote_vol_24h, spread_pct, change_1h_pct,
                 change_24h_pct)

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
        raw = 100.0 * base * (0.55 + 0.45 * sc.earlyness)

        # MTF: same formula per timeframe, 5m lean = the volume_price lean
        # computed above. Alignment adds its bonus AFTER base*earlyness and
        # BEFORE rounding, capped by the 0-100 ceiling. Counter-trend is a
        # label only - it never moves the score.
        aligned, counter = mtf_alignment(vp_lean, lean_1H, lean_4H)
        if aligned:
            raw = min(100.0, raw + MTF_ALIGN_BONUS)
        if counter:
            sc.notes.append(counter)
        sc.score = round(raw, 1)

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

        # Shadow score (score-v3-dis): additive, read-only with respect to
        # everything above. A shadow failure must never touch the live card.
        try:
            v3 = score_v3(bars, book_lean=bk_lean, change_1h_pct=change_1h_pct,
                          oi_change_1h_pct=oi_change_1h_pct, oi_gap_s=oi_gap_s,
                          funding_rate=funding_rate, funding_cap=funding_cap,
                          lean_1H=lean_1H, lean_4H=lean_4H)
            sc.score_v3 = v3["score_v3"]
            sc.direction_v3 = v3["direction_v3"]
            sc.initiation = v3["initiation"]
            sc.flow_state = v3["flow_state"]
            sc.oi_change_1h_pct = v3["oi_change_1h_pct"]
            sc.opening_for = v3["opening_for"]
            sc.opening_against = v3["opening_against"]
            sc.book_agree = v3["book_agree"]
            sc.funding_crowd = v3["funding_crowd"]
            sc.score_rule = v3["score_rule"]
            sc.v3_parts = v3["parts"]
        except Exception as e:   # noqa: BLE001 - shadow only, never fatal
            sc.notes.append(f"SHADOW v3 error: {type(e).__name__}: {e}")

        if tier == 2:
            sc.notes.append("TIER 2: UNVALIDATED — no deep history exists for this coin")

    return sc