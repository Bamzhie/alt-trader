"""
Technical indicators. Pure functions over OHLCV bars; no I/O, no state.

Every function is DIRECTION-SYMMETRIC by construction where it should be:
mirrored inputs must produce mirrored outputs. The spec makes that a correctness
property, and tests/test_indicators.py asserts it.

Nothing here references a target return or desired move size. Magnitude of move
is an input to measurement, never an objective.
"""


# ---------- series helpers ----------

def closes(bars):
    return [b["c"] for b in bars]


def highs(bars):
    return [b["h"] for b in bars]


def lows(bars):
    return [b["l"] for b in bars]


def quote_volume(bars):
    """Quote (amount) volume - the comparable measure across differing prices."""
    return [b["amount"] for b in bars]


def sma(xs, n):
    if len(xs) < n:
        return []
    out, run = [], 0.0
    for x in xs[:n]:
        run += x
    out.append(run / n)
    for i in range(n, len(xs)):
        run += xs[i] - xs[i - n]
        out.append(run / n)
    return out


def ema(xs, n):
    if len(xs) < n:
        return []
    k = 2.0 / (n + 1)
    e = sum(xs[:n]) / n
    out = [e]
    for x in xs[n:]:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


# ---------- trend ----------

def ema_relationship(bars, fast=9, slow=21):
    """
    +1 strong uptrend (fast well above slow), -1 strong downtrend, 0 mixed.
    Normalized by ATR so it is comparable across coins and price scales.
    """
    c = closes(bars)
    if len(c) < slow + 2:
        return 0.0
    fast_ema, slow_ema = ema(c, fast), ema(c, slow)
    if not fast_ema or not slow_ema:
        return 0.0
    atr_series = atr(bars, 14)
    atr_val = atr_series[-1] if atr_series else 0.0
    gap = fast_ema[-1] - slow_ema[-1]
    if atr_val <= 0:
        return 0.0
    return max(-1.0, min(1.0, gap / (atr_val * 2)))


def donchian_position(bars, n=20):
    """
    Where price sits within its N-bar range: +1 at the highs, -1 at the lows.
    Symmetric by construction. 0 if insufficient history.
    """
    h, l, c = highs(bars), lows(bars), closes(bars)
    if len(c) < n + 1:
        return 0.0
    hi = max(h[-(n + 1):-1])
    lo = min(l[-(n + 1):-1])
    if hi <= lo:
        return 0.0
    return max(-1.0, min(1.0, (c[-1] - lo) / (hi - lo) * 2 - 1))


def macd_histogram(bars, fast=12, slow=26, signal=9):
    """Normalized MACD histogram. Sign carries direction."""
    c = closes(bars)
    if len(c) < slow + signal + 2:
        return 0.0
    fast_ema, slow_ema = ema(c, fast), ema(c, slow)
    if len(fast_ema) < len(slow_ema):
        fast_ema = [0.0] * (len(slow_ema) - len(fast_ema)) + fast_ema
    macd = [a - b for a, b in zip(fast_ema, slow_ema)]
    sig = ema(macd, signal)
    if not sig:
        return 0.0
    atr_series = atr(bars, 14)
    atr_val = atr_series[-1] if atr_series else 0.0
    if atr_val <= 0:
        return 0.0
    return max(-1.0, min(1.0, (macd[-1] - sig[-1]) / atr_val * 4))


# ---------- oscillators ----------

def rsi(bars, n=14):
    """Wilder RSI. Returns 0..100. Direction: >50 bullish, <50 bearish."""
    c = closes(bars)
    if len(c) < n + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


def atr(bars, n=14):
    """Average True Range series. Measures volatility, not direction."""
    if len(bars) < n + 1:
        return []
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    out = [sum(trs[:n]) / n]
    for i in range(n, len(trs)):
        out.append((out[-1] * (n - 1) + trs[i]) / n)
    return out


def atr_pct(bars, n=14):
    """ATR as a fraction of price - comparable across coins."""
    a = atr(bars, n)
    c = closes(bars)
    if not a or not c or c[-1] <= 0:
        return 0.0
    return a[-1] / c[-1]


def stochastic(bars, n=14, k_smooth=3):
    c, h, l = closes(bars), highs(bars), lows(bars)
    if len(c) < n + k_smooth:
        return 50.0
    hh = max(h[-n:])
    ll = min(l[-n:])
    if hh <= ll:
        return 50.0
    raw = [(c[-n + i] - ll) / (hh - ll) * 100 for i in range(n)]
    return sum(raw[-k_smooth:]) / k_smooth


# ---------- volume ----------

def obv_slope(bars, n=20):
    """
    On-balance volume slope, normalized to -1..+1 by total volume.
    Positive = accumulation by buyers, negative = distribution.

    Uses BASE volume for the same symmetry reason as volume_expansion(): quote
    volume embeds price and would bias the slope toward whichever direction
    price moved.
    """
    if len(bars) < n + 1:
        return 0.0
    c = closes(bars)
    v = [b["vol"] for b in bars]
    signed = 0.0
    total = 0.0
    for i in range(len(c) - n, len(c)):
        d = c[i] - c[i - 1]
        signed += v[i] if d > 0 else (-v[i] if d < 0 else 0.0)
        total += v[i]
    if total <= 0:
        return 0.0
    return max(-1.0, min(1.0, signed / total))


def volume_expansion(bars, recent=3, baseline=48):
    """
    MAGNITUDE (unsigned, 0..1): how far recent volume exceeds its own trailing
    baseline.

    Uses BASE volume (bars['vol']), NOT quote volume. Quote volume is
    price x size, so on a falling series it shrinks purely because price fell,
    which would suppress downside expansions and introduce a bullish bias into
    what must be a direction-neutral magnitude. Base volume is price-free and
    therefore symmetric. Quote volume ('amount') is used only for
    cross-sectional liquidity ranking, never for expansion detection.

    Deliberately unsigned - direction comes from obv_slope, price direction and
    book skew, never from volume size alone.
    """
    v = [b["vol"] for b in bars]
    if len(v) < recent + 5:
        return 0.0
    baseline = min(baseline, len(v) - recent)
    if baseline < 5:
        return 0.0
    base_sorted = sorted(v[-baseline - recent:-recent])
    med = base_sorted[len(base_sorted) // 2]
    if med <= 0:
        return 0.0
    recent_avg = sum(v[-recent:]) / recent
    ratio = recent_avg / med
    # ratio 1.0 -> 0, ratio 4.0 -> ~1
    return max(0.0, min(1.0, (ratio - 1.0) / 3.0))


# ---------- order book ----------

def book_skew(bids, asks, depth=20):
    """
    Signed notional imbalance from the top `depth` levels.
    Returns (magnitude 0..1, lean -1..+1). bid-heavy -> positive lean.
    Symmetric by construction: swapping bids/asks negates the lean and leaves
    magnitude unchanged.
    """
    b = sum(p * q for p, q in bids[:depth])
    a = sum(p * q for p, q in asks[:depth])
    if b + a <= 0:
        return 0.0, 0.0
    lean = (b - a) / (b + a)
    return abs(lean), lean


def book_thinness(bids, asks, depth=20):
    """
    0..1: how thin the opposing side is. A thin ask stack with heavy bids is
    squeeze fuel; thin bids with heavy asks is the mirror case. Unsigned.
    """
    a = sum(p * q for p, q in asks[:depth])
    b = sum(p * q for p, q in bids[:depth])
    lo, hi = min(a, b), max(a, b)
    if hi <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - lo / hi))


# ---------- early-ness ----------

def earlyness(bars, lookback=None):
    """
    0..1: how YOUNG the current move is.

    1.0 = move just started (price near the middle/recent area, volume not yet
    exhausted). 0.0 = move is mature/vertical (price pinned at range extremes
    with volume far above baseline).

    This is what separates DETECTION from CHASING. A setup that has already run
    is no longer early, regardless of how abnormal it still looks.

    Not a move-size target: it measures position within the recent range and
    relative volume, both of which are self-referential to this coin's history.
    """
    if len(bars) < 20:
        return 0.0
    n = lookback or min(len(bars), 48)
    h, l, c = highs(bars)[-n:], lows(bars)[-n:], closes(bars)[-n:]
    hh, ll = max(h), min(l)
    if hh <= ll:
        return 0.0
    # Distance from the nearer extreme: pinned at a high = late/mature.
    pos = (c[-1] - ll) / (hh - ll)
    from_high = 1.0 - pos
    from_low = pos
    near_extreme = max(from_high, from_low)  # 1.0 when pinned
    # Volume exhaustion also marks maturity.
    ve = volume_expansion(bars)
    late = 0.65 * near_extreme + 0.35 * ve
    return max(0.0, min(1.0, 1.0 - late))


# ---------- swing structure ----------

def swing_low(bars, left=2, right=2):
    """Most recent confirmed swing low price, or None."""
    l = lows(bars)
    for i in range(len(l) - right - 1, left - 1, -1):
        if l[i] == min(l[i - left:i + right + 1]) and l.count(l[i]) == 1:
            return l[i]
    return None


def swing_high(bars, left=2, right=2):
    """Most recent confirmed swing high price, or None."""
    h = highs(bars)
    for i in range(len(h) - right - 1, left - 1, -1):
        if h[i] == max(h[i - left:i + right + 1]) and h.count(h[i]) == 1:
            return h[i]
    return None