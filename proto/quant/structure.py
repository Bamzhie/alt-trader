"""Multi-timeframe market STRUCTURE with confirmed pivots.

DESCRIPTIVE ONLY.  Nothing in this module scores, selects or signals; it
measures the shape of a price series and says WHEN each measurement became
knowable.

Point-in-time contract
----------------------
A pivot at bar ``i`` is only knowable once the bars to its right have closed.
``Pivot.confirmed_ts`` is that instant: the CLOSE of bar ``i + right``, i.e.
``ts + (right + 1) * BAR_S``.  Every consumer must filter on it
(:func:`pivots_as_of`, :func:`multi_timeframe`); a pivot that is not yet
confirmed must not be visible, and a bar that has not closed must not be used.

Uniqueness
----------
A pivot high at ``i`` needs ``h[i] >= h[i-left .. i-1]`` (non-strict: a plateau
is allowed on the left) and ``h[i] > h[i+1 .. i+right]`` (STRICT: a plateau is
resolved by taking its LAST bar).  The test is therefore LOCAL to the window -
two equal highs inside one window never both become pivots, while the same
values in separate windows both do.  This is deliberately NOT the global
``list.count(...)`` uniqueness test used by ``proto/indicators.py`` (which
returns a price only and stays as it is for the watcher).

Input
-----
Bars are dicts with the keys used by ``proto/quant/bars.py``
(``ts, o, h, l, c, vol, amt``; ``ts`` is the bar's OPEN timestamp) or tuples
``(ts, o, h, l, c[, vol[, amt]])`` - a 5-tuple is accepted, the volume and the
amount then default to 0.0.  Both forms are accepted everywhere here.

Standard library only.
"""
import math
from dataclasses import dataclass

from .config import BAR_S

PIVOT_VERSION = "pivots-v1"


@dataclass(frozen=True)
class Pivot:
    """One confirmed swing point.

    index         index of the pivot bar in the series that was analysed
    ts            open timestamp of that bar
    confirmed_ts  when the pivot BECAME KNOWABLE (close of bar index+right)
    price         the pivot price (the bar high for "high", the low for "low")
    kind          "high" | "low"
    leg_bars      bars in the leg that ended at this pivot, measured against
                  the previous OPPOSITE pivot (0 when there is none)
    size          |log(price / previous opposite pivot price)| (0.0 when there
                  is no previous opposite pivot) - never None
    """
    index: int
    ts: int
    confirmed_ts: int
    price: float
    kind: str
    leg_bars: int
    size: float


# --------------------------------------------------------------------- input

def _is_dict(bar):
    return hasattr(bar, "get")


def _ts(bar):
    return int(bar["ts"]) if _is_dict(bar) else int(bar[0])


def _h(bar):
    return float(bar["h"]) if _is_dict(bar) else float(bar[2])


def _l(bar):
    return float(bar["l"]) if _is_dict(bar) else float(bar[3])


def _o(bar):
    return float(bar["o"]) if _is_dict(bar) else float(bar[1])


def _c(bar):
    return float(bar["c"]) if _is_dict(bar) else float(bar[4])


def _vol(bar):
    if _is_dict(bar):
        return float(bar.get("vol", 0.0))
    return float(bar[5]) if len(bar) > 5 else 0.0


def _amt(bar):
    if _is_dict(bar):
        return float(bar.get("amt", 0.0))
    return float(bar[6]) if len(bar) > 6 else 0.0


# -------------------------------------------------------------------- pivots

def confirmed_pivots(bars, left: int = 2, right: int = 2, *,
                     frame_bars: int = 1) -> list[Pivot]:
    """Local, point-in-time swing points of a bar series.

    ``frame_bars`` is the number of BASE bars (5 m) that one analysed bar
    spans: 1 for the base series, 12 for an hourly frame, 48 for a 4 h
    frame.  It matters because a pivot of a FRAME is only knowable when the
    last confirming FRAME bar closes:

        confirmed_ts = ts + (right + 1) * frame_bars * BAR_S

    (for the base series that is the close of bar ``index+right``).

    ASSUMPTIONS / limits
      * ``left``/``right`` are in analysed bars; the last ``right`` bars of
        the series can never be a pivot because they are not confirmed yet.
      * A pivot high at ``i`` needs ``h[i] >= h[i-1 .. i-left]`` and
        ``h[i] > h[i+1 .. i+right]``; a pivot low is the mirror image
        (``<=`` on the left, ``<`` on the right).  Equality on the right is
        excluded, so a flat top is resolved by its LAST bar.
      * Uniqueness is local to the window (see the module docstring).
      * ``leg_bars`` and ``size`` are measured against the previous opposite
        pivot that is STRICTLY EARLIER; a bar that is both a high and a low
        pivot does not count as its own predecessor.  With no such pivot they
        are ``0`` and ``0.0`` (never None).
      * The function is pure: it does not know about wall-clock time.  Use
        :func:`pivots_as_of` (or :func:`multi_timeframe`) to ask what was
        knowable at a given instant.
    """
    n = len(bars)
    if n == 0 or left < 0 or right < 0 or frame_bars < 1:
        return []
    ts = [_ts(b) for b in bars]
    hs = [_h(b) for b in bars]
    ls = [_l(b) for b in bars]

    raw = []
    for i in range(n):
        if i - left < 0 or i + right >= n:
            continue                      # not enough room on either side
        hi, lo = hs[i], ls[i]
        is_high = all(hi >= hs[j] for j in range(i - left, i))
        is_high = is_high and all(hi > hs[j] for j in range(i + 1, i + right + 1))
        is_low = all(lo <= ls[j] for j in range(i - left, i))
        is_low = is_low and all(lo < ls[j] for j in range(i + 1, i + right + 1))
        if is_high:
            raw.append((i, "high", hi))
        if is_low:
            raw.append((i, "low", lo))

    out = []
    last = {"high": None, "low": None}
    for i, kind, price in raw:
        opp = last["low"] if kind == "high" else last["high"]
        if opp is not None and opp.index >= i:
            opp = None                  # the same bar is not a PREVIOUS pivot
        if opp is None:
            leg, size = 0, 0.0
        else:
            leg = i - opp.index
            size = 0.0
            if price > 0.0 and opp.price > 0.0:
                size = abs(math.log(price / opp.price))
        pv = Pivot(index=i, ts=ts[i],
                   confirmed_ts=ts[i] + (right + 1) * frame_bars * BAR_S,
                   price=price, kind=kind, leg_bars=leg, size=size)
        out.append(pv)
        last[kind] = pv
    return out


def pivots_as_of(pivots, as_of_ts):
    """The pivots that were KNOWABLE at ``as_of_ts``.

    A pivot becomes knowable at its ``confirmed_ts``; a pivot whose
    ``confirmed_ts`` is in the future is invisible here.  Never forward-fills,
    never interpolates.
    """
    if as_of_ts is None:
        return list(pivots)
    return [p for p in pivots if p.confirmed_ts <= as_of_ts]


def structure_summary(pivots):
    """A small, documented summary of a confirmed pivot list.

    ``trend``  "up" when the last confirmed high is above the last confirmed
               low AND the most recent pivot is a high; "down" for the mirror
               image; "mixed" otherwise (including "not enough pivots").
    ``last_pivot_high`` / ``last_pivot_low``  the most recent confirmed pivot
               prices, or None.
    ``swing_bars``  the length in bars of the most recent completed leg
               (the distance from the last pivot to the previous OPPOSITE
               pivot), or None when no such leg exists yet.
    """
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    last_h = highs[-1].price if highs else None
    last_l = lows[-1].price if lows else None
    recent = pivots[-1].kind if pivots else None
    if last_h is None or last_l is None:
        trend = "mixed"
    elif recent == "high" and last_h > last_l:
        trend = "up"
    elif recent == "low" and last_l < last_h:
        trend = "down"
    else:
        trend = "mixed"
    swing = None
    if pivots:
        leg = pivots[-1].leg_bars
        swing = leg if leg > 0 else None
    return {"trend": trend, "last_pivot_high": last_h, "last_pivot_low": last_l,
            "swing_bars": swing}


# ----------------------------------------------------------------- resample

def resample(bars, frame_bars: int, *, as_of_ts=None) -> list[dict]:
    """Aggregate 5-minute bars into ``frame_bars``-bar frames (3 -> 15 m,
    12 -> 1 h, 48 -> 4 h).

    RULES
      * The bars are bucketed by WALL-CLOCK period: the period of a bar is
        ``floor(ts / (frame_bars * BAR_S))``, so a 12-bar frame is the hour
        it belongs to.  A bucket becomes a frame only when it holds exactly
        ``frame_bars`` bars that are mutually CONTIGUOUS (each one ``BAR_S``
        after the previous one).  A bucket with a gap (or with missing bars)
        is DROPPED, never interpolated - and because the buckets are
        wall-clock aligned, a gap drops that frame only: the other frames
        keep their boundaries.
      * A frame is emitted only when it is CLOSED: its close timestamp
        (``last_bar_ts + BAR_S``) must be ``<= as_of_ts`` when ``as_of_ts`` is
        given.  An incomplete trailing frame is never emitted.
      * The frame dict uses the same keys as the input bars, with ``ts`` the
        open timestamp of the frame's first bar, ``o``/``c`` the first open and
        the last close, ``h``/``l`` the extremes, and ``vol``/``amt`` the sums.
    """
    if frame_bars < 1:
        return []
    step = frame_bars * BAR_S
    buckets = {}
    for b in bars:
        t = _ts(b)
        buckets.setdefault((t // step) * step, []).append(b)
    out = []
    for key in sorted(buckets):
        group = sorted(buckets[key], key=_ts)
        if len(group) != frame_bars:
            continue                      # a gap or an incomplete period
        contiguous = True
        for k in range(1, frame_bars):
            if _ts(group[k]) - _ts(group[k - 1]) != BAR_S:
                contiguous = False
                break
        if not contiguous:
            continue
        last_ts = _ts(group[-1])
        if as_of_ts is not None and last_ts + BAR_S > as_of_ts:
            continue
        out.append({
            "ts": _ts(group[0]),
            "o": _o(group[0]),
            "h": max(_h(b) for b in group),
            "l": min(_l(b) for b in group),
            "c": _c(group[-1]),
            "vol": sum(_vol(b) for b in group),
            "amt": sum(_amt(b) for b in group),
        })
    return out


# ----------------------------------------------------------- multi timeframe

def multi_timeframe(bars, frames=(3, 12, 48), left=2, right=2, as_of_ts=None) -> dict:
    """Structure of a bar series on several timeframes.

    Returns ``{frame_bars: {"pivots": [Pivot...], "structure": {...}}}``.

    POINT-IN-TIME: when ``as_of_ts`` is given, only bars whose CLOSE
    (``ts + BAR_S``) is at or before ``as_of_ts`` are used, only frames that
    are closed by then are built, and only pivots that were confirmed by then
    are reported.  Nothing from the future can leak in.

    The pivots of a frame are indexed and timestamped in the FRAME series
    (not in the original bar series), and their ``confirmed_ts`` is the close
    of the last confirming FRAME bar (``ts + (right+1)*frame_bars*BAR_S``).
    """
    closed = bars
    if as_of_ts is not None:
        closed = [b for b in bars if _ts(b) + BAR_S <= as_of_ts]
    out = {}
    for fb in frames:
        fbars = resample(closed, fb, as_of_ts=as_of_ts)
        pivots = confirmed_pivots(fbars, left=left, right=right, frame_bars=fb)
        if as_of_ts is not None:
            pivots = pivots_as_of(pivots, as_of_ts)
        out[fb] = {"pivots": pivots, "structure": structure_summary(pivots)}
    return out
