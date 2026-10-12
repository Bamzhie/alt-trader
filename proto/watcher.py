"""Watcher sidecar: stable criteria-triggered setups.

Quant section (Task 2). Pure stdlib functions, no I/O, no network.
Closed bars only: a bar counts iff ``bar["ts"] + 300 <= now``.
L5/initiation come from the existing ``proto.scorer.score_v3`` (called,
never reimplemented) via :func:`v3_snapshot`.
"""

import math

from proto import scorer

BAR_SEC = 300
HALF_HOUR_SEC = 1800
N_TOD_BUCKETS = 48
EWMA_SPAN = 24
WARMUP_BARS = 60
TOD_LOOKBACK_DAYS = 14
TOD_MIN_SAMPLES = 30
CUSUM_K = 0.5
CUSUM_H = 4.0
CUSUM_REFRACTORY = 6
BTC_DRIFT_BARS = 2016  # 7d of 5m bars

_LN2_TERM = 2.0 * math.log(2.0) - 1.0


def _closed(bars, now):
    """Bars with ``ts + 300 <= now`` (forming bar excluded)."""
    return [b for b in bars if b["ts"] + BAR_SEC <= now]


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def tod_bucket(ts):
    """Half-hour bucket key ``(ts // 1800) % 48``."""
    return (ts // HALF_HOUR_SEC) % N_TOD_BUCKETS


def gk_vol(bars):
    """Per-bar Garman-Klass variance, floored at 0.

    ``gk = 0.5*ln(H/L)^2 - (2ln2-1)*ln(C/O)^2``. Non-positive or missing
    prices -> None for that bar.
    """
    out = []
    for b in bars:
        try:
            o, h, l, c = b["o"], b["h"], b["l"], b["c"]
        except (KeyError, TypeError):
            out.append(None)
            continue
        if (o is None or h is None or l is None or c is None
                or o <= 0 or h <= 0 or l <= 0 or c <= 0 or h < l):
            out.append(None)
            continue
        gk = 0.5 * math.log(h / l) ** 2 - _LN2_TERM * math.log(c / o) ** 2
        out.append(max(0.0, gk))
    return out


def ewma(vals, span=24):
    """EWMA with ``lambda = 2/(span+1)``, seeded at the mean of the first
    ``span`` valid values. Positions before the seed (and None inputs) are
    None; Nones never enter the seed or move the average."""
    lam = 2.0 / (span + 1.0)
    out = [None] * len(vals)
    seed_vals = []
    avg = None
    for i, v in enumerate(vals):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        if avg is None:
            seed_vals.append(v)
            if len(seed_vals) == span:
                avg = sum(seed_vals) / span
                out[i] = avg
        else:
            avg = lam * v + (1.0 - lam) * avg
            out[i] = avg
    return out


def _prior_rows(keys, vals, lookback_days):
    """(bucket, value) pairs strictly before today 00:00 UTC and within
    ``lookback_days``. Today is the UTC day of max(keys); today's rows are
    never in the profile."""
    pairs = [(k, v) for k, v in zip(keys, vals)
             if k is not None and v is not None]
    if not pairs:
        return []
    today = (max(k for k, _ in pairs) // 86400) * 86400
    lo = today - lookback_days * 86400
    return [(tod_bucket(k), v) for k, v in pairs if lo <= k < today]


def _seasonal_ratio(keys, vals, lookback_days):
    """Median same-bucket / median all over prior-only rows. Buckets with
    <30 samples are absent (never a 1.0 default)."""
    rows = _prior_rows(keys, vals, lookback_days)
    if not rows:
        return {}
    overall = _median([v for _, v in rows])
    if overall is None or overall == 0:
        return {}
    by_bucket = {}
    for b, v in rows:
        by_bucket.setdefault(b, []).append(v)
    prof = {}
    for b, vs in by_bucket.items():
        if len(vs) >= TOD_MIN_SAMPLES:
            prof[b] = _median(vs) / overall
    return prof


def tod_profile(keys, vals, lookback_days=14):
    """Seasonal vol profile over bar timestamps ``keys`` and per-bar values
    ``vals`` (e.g. GK). Prior-only; buckets with <30 samples are absent."""
    return _seasonal_ratio(list(keys), list(vals), lookback_days)


def sigma_hat(bars, now):
    """TOD-adjusted EWMA vol as a per-5m SD. None when <60 closed bars, when
    the seasonal bucket for the latest bar is missing, or when the EWMA has
    no valid value at the latest bar. Forming bar excluded."""
    closed = _closed(bars, now)
    if len(closed) < WARMUP_BARS:
        return None
    gks = gk_vol(closed)
    prof = _seasonal_ratio([b["ts"] for b in closed], gks, TOD_LOOKBACK_DAYS)
    adj = []
    for b, g in zip(closed, gks):
        if g is None:
            adj.append(None)
            continue
        s = prof.get(tod_bucket(b["ts"]))
        adj.append(g / s if s else None)
    series = ewma(adj, span=EWMA_SPAN)
    last = series[-1] if series else None
    if last is None or last < 0:
        return None
    return math.sqrt(last)


def resid_vol(ts_list, e_list, lookback_days=14):
    """Residual-vol SD from residuals ``e`` with its OWN seasonal profile on
    ``e^2`` (raw ``s_vol`` is never reused). None when <60 values or the
    latest bucket lacks its own profile."""
    ts_list = list(ts_list)
    e_list = list(e_list)
    if len(ts_list) < WARMUP_BARS or len(e_list) < WARMUP_BARS:
        return None
    e2 = [x * x if x is not None else None for x in e_list]
    prof = _seasonal_ratio(ts_list, e2, lookback_days)
    adj = []
    for t, v in zip(ts_list, e2):
        if v is None:
            adj.append(None)
            continue
        s = prof.get(tod_bucket(t))
        adj.append(v / s if s else None)
    series = ewma(adj, span=EWMA_SPAN)
    last = series[-1] if series else None
    if last is None or last < 0:
        return None
    return math.sqrt(last)


def cusum(zs, k=0.5, H=4.0):
    """Symmetric two-sided CUSUM on standardized residuals.

    ``S+ = max(0, S+ + z - k)``, ``S- = max(0, S- - z - k)``; long fires on
    ``S+ > H``, short on ``S- > H``. Fire-side reset with a 6-bar refractory
    on the firing side only. Mirror ``z -> -z`` swaps long/short indices.
    """
    long_idx, short_idx = [], []
    sp = sm = 0.0
    ref_long = ref_short = 0
    for i, z in enumerate(zs):
        if z is None or (isinstance(z, float) and math.isnan(z)):
            if ref_long:
                ref_long -= 1
            if ref_short:
                ref_short -= 1
            continue
        sp = max(0.0, sp + z - k)
        sm = max(0.0, sm - z - k)
        if ref_long:
            ref_long -= 1
        elif sp > H:
            long_idx.append(i)
            sp = 0.0
            ref_long = CUSUM_REFRACTORY
        if ref_short:
            ref_short -= 1
        elif sm > H:
            short_idx.append(i)
            sm = 0.0
            ref_short = CUSUM_REFRACTORY
    return {"long_idx": long_idx, "short_idx": short_idx}


def _mad(xs, med):
    dev = sorted(abs(x - med) for x in xs)
    n = len(dev)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return dev[mid]
    return 0.5 * (dev[mid - 1] + dev[mid])


def _winsorize(xs, n_mad=5.0):
    med = _median(xs)
    if med is None:
        return list(xs)
    mad = _mad(xs, med)
    if mad == 0:
        # (Near-)constant series: any deviation is an outlier -> clip to median.
        return [med] * len(xs)
    lo, hi = med - n_mad * mad, med + n_mad * mad
    return [min(hi, max(lo, x)) for x in xs]


def beta_ols(r, r_btc):
    """OLS beta with no intercept. Current bar excluded, both legs winsorized
    at median +/- 5*MAD, zero BTC variance -> None."""
    r = list(r)
    r_btc = list(r_btc)
    n = min(len(r), len(r_btc))
    if n < 3:  # need >= 2 points after dropping the current bar
        return None
    xs = _winsorize(r_btc[:n - 1])
    ys = _winsorize(r[:n - 1])
    denom = sum(x * x for x in xs)
    if denom == 0:
        return None
    return sum(x * y for x, y in zip(xs, ys)) / denom


def beta_shrunk(r, r_btc):
    """``0.5 * beta_ols + 0.5``; None when OLS is unavailable."""
    ols = beta_ols(r, r_btc)
    if ols is None:
        return None
    return 0.5 * ols + 0.5


def rvol_adj(bars, now):
    """Raw 3-bar mean / 48-bar median volume, divided by the same 48-bucket
    prior-only seasonal profile built on volume. None when <48 closed bars
    or the latest bucket lacks a profile (never a 1.0 default)."""
    closed = _closed(bars, now)
    if len(closed) < 48:
        return None
    vols = [b.get("vol") for b in closed]
    if any(v is None or v < 0 for v in vols[-48:]):
        return None
    med48 = _median(vols[-48:])
    if med48 is None or med48 == 0:
        return None
    base = (sum(vols[-3:]) / 3.0) / med48
    prof = _seasonal_ratio([b["ts"] for b in closed], vols, TOD_LOOKBACK_DAYS)
    s = prof.get(tod_bucket(closed[-1]["ts"]))
    if not s:
        return None
    return base / s


def btc_drift_7d(btc_closes_5m):
    """Trailing-7d BTC drift ``(c[-1]-c[-2016])/c[-2016]`` on 5m closes.
    Fewer than 2016 bars -> None."""
    closes = list(btc_closes_5m)
    if len(closes) < BTC_DRIFT_BARS:
        return None
    c0 = closes[-BTC_DRIFT_BARS]
    c1 = closes[-1]
    if c0 is None or c1 is None or c0 == 0:
        return None
    return (c1 - c0) / c0


def v3_snapshot(bars, **kw):
    """L5/initiation/direction via the existing scorer (delegate, not a copy)."""
    return scorer.score_v3(bars, **kw)
