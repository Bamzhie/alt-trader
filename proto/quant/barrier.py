"""Barrier math and triple-barrier labeling.

Closed-form results (all verified by Monte Carlo in tests):

  null hit probability   p0 = b / (a + b)             a = target, b = stop distance
  breakeven hit prob.    p* = (b + c) / (a + b)       c = round-trip cost (price units)
  with drift mu, vol s   P(TP first) = (1 - exp(-2*mu*b/s^2)) / (1 - exp(-2*mu*(a+b)/s^2))
  noise-only stop touch  P(touch -b within T) = 2*Phi(-b / (s*sqrt(T)))  (driftless)

TP-hit rate alone is therefore NOT an objective: with zero drift it is fixed
by geometry.  The measured quantity is net expected R (return in units of the
stop distance, after costs) and the excess of the TP-first rate over p0.

Labeling convention (conservative, no look-ahead):
  * signal on closed bar i; entry at the OPEN of bar i+1
  * bars i+1 .. i+T are evaluated (the entry bar included)
  * if the stop and target are both touched on the same bar, the STOP wins
  * time barrier: exit at the close of bar i+T
  * stop/target exits fill at the barrier price; friction is charged as one
    round-trip cost fraction (fees + tiered slippage), funding is not modeled
"""
import math
from bisect import bisect_left
from itertools import accumulate


def Phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def null_hit_prob(a, b):
    return b / (a + b)


def breakeven_hit_prob(a, b, c):
    return (b + c) / (a + b)


def p_tp_first(a, b, mu, sigma):
    if abs(mu) < 1e-14:
        return b / (a + b)
    k = 2.0 * mu / (sigma * sigma)
    return (1.0 - math.exp(-k * b)) / (1.0 - math.exp(-k * (a + b)))


def noise_touch_prob(b, sigma_bar, n_bars):
    """Driftless probability that price touches -b within n_bars (continuous
    monitoring, i.e. intrabar highs/lows)."""
    return 2.0 * Phi(-b / (sigma_bar * math.sqrt(n_bars)))


def friction_r(cost_frac, stop_frac):
    """Round-trip friction expressed in R."""
    return cost_frac / stop_frac


def stop_distance(sigma_bar, horizon_bars, k_stop):
    """Log-distance of the stop: k * sigma_bar * sqrt(T)."""
    return k_stop * sigma_bar * math.sqrt(horizon_bars)


class Path:
    """Forward path summary from the entry bar, shared by all geometries."""
    __slots__ = ("entry", "rmax", "nrmin", "close", "n")

    def __init__(self, entry, highs, lows, closes):
        self.entry = entry
        self.rmax = list(accumulate(highs, max))
        self.nrmin = list(accumulate((-x for x in lows), max))  # = -running min(low)
        self.close = closes
        self.n = len(highs)


def make_path(o, h, l, c, i, max_horizon, run_end=None):
    """Path for a signal on bar i, or None if the horizon is not available.

    The arrays are concatenated across timestamp gaps, so ``len(c)`` is not a
    safe end: a path that runs past the end of the signal's contiguous run
    would silently label on post-gap bars (different regime, and the entry
    bar itself could be the gap's first bar).  Callers that know the run
    boundary must pass ``run_end`` (exclusive index, e.g. from
    ``bars.contiguous_runs``); omitting it keeps the historical
    end-of-file contract.
    """
    s = i + 1
    e = s + max_horizon
    end = len(c) if run_end is None else min(run_end, len(c))
    if e > end:
        return None
    return Path(o[s], h[s:e], l[s:e], c[s:e])


_BIG = 10 ** 9


def label_path(path, side, dd, horizon, r_tp):
    """Triple-barrier outcome for one side and geometry.

    Returns (gross_log_return, kind, exit_offset) where kind is 'TP'/'SL'/'TIME'
    and exit_offset is the 0-based bar offset from the entry bar.
    dd = stop distance in log units.  Target distance = r_tp * dd.
    """
    e = path.entry
    if side > 0:
        stop = e * math.exp(-dd)
        tp = e * math.exp(r_tp * dd)
        si = bisect_left(path.nrmin, -stop)           # first low <= stop
        ti = bisect_left(path.rmax, tp)               # first high >= tp
    else:
        stop = e * math.exp(dd)
        tp = e * math.exp(-r_tp * dd)
        si = bisect_left(path.rmax, stop)             # first high >= stop
        ti = bisect_left(path.nrmin, -tp)             # first low <= tp
    si = si if si < horizon else _BIG
    ti = ti if ti < horizon else _BIG
    if si <= ti and si < _BIG:
        return -dd, "SL", si
    if ti < _BIG:
        return r_tp * dd, "TP", ti
    return side * math.log(path.close[horizon - 1] / e), "TIME", horizon - 1


def label_reference(o, h, l, c, i, side, dd, horizon, r_tp):
    """Plain loop implementation, used by tests to verify label_path."""
    e = o[i + 1]
    if side > 0:
        stop, tp = e * math.exp(-dd), e * math.exp(r_tp * dd)
    else:
        stop, tp = e * math.exp(dd), e * math.exp(-r_tp * dd)
    for j in range(horizon):
        b = i + 1 + j
        hit_stop = l[b] <= stop if side > 0 else h[b] >= stop
        hit_tp = h[b] >= tp if side > 0 else l[b] <= tp
        if hit_stop:                       # stop-first on ties
            return -dd, "SL", j
        if hit_tp:
            return r_tp * dd, "TP", j
    return side * math.log(c[i + horizon] / e), "TIME", horizon - 1


def net_r(gross_log, dd, cost_frac):
    """Net return in R units: (gross - round-trip cost) / stop distance."""
    return (gross_log - cost_frac) / dd


def label_stream(bars, side, dd, horizon, r_tp):
    """Incremental version of label_reference for live resolution.

    bars: sequence of (ts, o, h, l, c) starting at the ENTRY bar.
    Returns (gross_log, kind, exit_offset, entry_price) once resolved, or
    None while neither barrier is touched and fewer than `horizon` bars exist.
    Same conventions as label_reference (stop-first on ties, entry at the
    entry bar's open, time exit at the close of bar `horizon - 1`).
    """
    if not bars:
        return None
    e = bars[0][1]
    if side > 0:
        stop, tp = e * math.exp(-dd), e * math.exp(r_tp * dd)
    else:
        stop, tp = e * math.exp(dd), e * math.exp(-r_tp * dd)
    for j, (_ts, _o, h, l, c) in enumerate(bars[:horizon]):
        hit_stop = l <= stop if side > 0 else h >= stop
        hit_tp = h >= tp if side > 0 else l <= tp
        if hit_stop:
            return -dd, "SL", j, e
        if hit_tp:
            return r_tp * dd, "TP", j, e
        if j == horizon - 1:
            return side * math.log(c / e), "TIME", j, e
    return None
