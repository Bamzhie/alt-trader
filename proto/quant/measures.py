"""Versioned MARKET MEASURES: distribution, tail, regime, dependence, breadth.

DESCRIPTIVE ONLY.  Every function here measures; none of them scores,
selects, ranks or signals.  A measure never becomes a decision.

These are the non-Hurst members of the measurement family; they are
re-exported by ``proto.quant.estimators`` (the documented public surface) and
live here so that both modules stay focused.

INPUT CONVENTION: these measures take INCREMENTS (per-bar log returns), not
a level series - that is their domain.  (The Hurst / MF-DFA estimators in
``proto.quant.estimators`` take a LEVEL series; see their docstrings.)

Every measure
-------------
  * returns ``value=None`` with a ``reason`` (never a fabricated number) when
    the data cannot support it: "insufficient-sample",
    "degenerate-zero-variance", "degenerate-zero-mean", "non-finite-input";
  * is trailing and therefore point-in-time: only the trailing window (or the
    window that ends at ``as_of_index``) is read, never anything after it;
  * is deterministic - the same inputs give the same value and the same
    ``inputs_hash``;
  * documents its assumptions in the docstring, including what the reported
    uncertainty does and does NOT cover.

Standard library only.
"""
import math

from .estimators import (Estimate, MEASURE_VERSION, EWMA_LAMBDA, MIN_CALIB,
                          MIN_OVERLAP, REGIME_MAPPING, TAIL_LEVEL, VAR_LEVEL,
                          _clean, _estimate)


MEASURE_NAMES = ("realized_vol", "ewma_vol", "downside_dev", "skew",
                 "excess_kurtosis", "hist_var_95", "hist_cvar_95", "tail_ratio")


def _quantile(sorted_vals, level):
    """Nearest-rank empirical quantile (documented, no interpolation)."""
    n = len(sorted_vals)
    if n == 0:
        return None
    k = int(math.ceil(level * n))
    if k < 1:
        k = 1
    if k > n:
        k = n
    return sorted_vals[k - 1]


def volatility_measures(returns, window) -> list[Estimate]:
    """Return / volatility / tail measures of a trailing window of INCREMENTS.

    The window is the last ``window`` observations of the series.  The
    measures are:

      realized_vol      sample standard deviation of the window (per-bar
                        units; multiply by sqrt(bars per year) to annualize)
      ewma_vol          exponentially weighted volatility, lambda = 0.94
                        (RiskMetrics), seeded with the mean square of the
                        window
      downside_dev      target semideviation, sqrt(mean(min(r, 0)^2))
      skew              sample skewness m3 / m2^1.5 (0 for a normal series)
      excess_kurtosis   m4 / m2^2 - 3 (0 for a normal series)
      hist_var_95       historical VaR at the 5% level, reported as a
                        POSITIVE loss magnitude (-quantile)
      hist_cvar_95      mean of the worst 5% (the expected shortfall),
                        reported as a POSITIVE loss magnitude
      tail_ratio        |mean of the worst 5%| / |mean of the window|

    ASSUMPTIONS
      * The window is assumed to be representative of the recent regime; the
        measures are trailing and never look forward.
      * The tail measures use the empirical distribution of a finite window:
        with a window of 100 the 5% tail is 5 observations, so hist_cvar_95
        and tail_ratio are noisy.  No distributional assumption is made.
      * The uncertainty is the delta-method standard error of the standard
        deviation (sd / sqrt(2 (n-1))), which assumes INDEPENDENT returns.
        It does not cover autocorrelation, volatility clustering, the finite
        window, or the tail estimation error; the shape and tail measures
        therefore report no uncertainty rather than a misleading one.
      * With fewer than ``window`` observations, or a non-finite value in the
        window, every measure is None with a reason.  A zero-variance window
        is reported as "degenerate-zero-variance" for the dispersion measures.
    """
    x, bad = _clean(returns)
    W = int(window)
    base = {"window": W}
    if bad:
        return [_estimate(nm, MEASURE_VERSION, None, None, 0, dict(base, measure=nm),
                          [], bad) for nm in MEASURE_NAMES]
    if W < 2 or len(x) < W:
        return [_estimate(nm, MEASURE_VERSION, None, None, len(x),
                          dict(base, measure=nm), x, "insufficient-sample")
                for nm in MEASURE_NAMES]
    w = x[-W:]
    m = sum(w) / W
    var = sum((v - m) ** 2 for v in w) / (W - 1)
    sd = math.sqrt(var)
    m2 = sum((v - m) ** 2 for v in w) / W
    m3 = sum((v - m) ** 3 for v in w) / W
    m4 = sum((v - m) ** 4 for v in w) / W
    out = []

    # realized volatility
    unc = sd / math.sqrt(2.0 * (W - 1)) if sd > 0.0 else None
    out.append(_estimate("realized_vol", MEASURE_VERSION, sd, unc, W,
                         dict(base, measure="realized_vol", kind="sample_sd"), w))

    # EWMA volatility (lambda = 0.94), seeded with the window mean square
    lam = EWMA_LAMBDA
    ew = m2
    for v in w:
        ew = lam * ew + (1.0 - lam) * v * v
    out.append(_estimate("ewma_vol", MEASURE_VERSION, math.sqrt(max(ew, 0.0)),
                         None, W, dict(base, measure="ewma_vol", lam=lam), w))

    # downside deviation
    dd = math.sqrt(sum(min(v, 0.0) ** 2 for v in w) / W)
    out.append(_estimate("downside_dev", MEASURE_VERSION, dd, None, W,
                         dict(base, measure="downside_dev", target=0.0), w))

    # skewness and excess kurtosis
    skew = (m3 / (m2 ** 1.5)) if m2 > 0.0 else None
    kurt = (m4 / (m2 ** 2) - 3.0) if m2 > 0.0 else None
    out.append(_estimate("skew", MEASURE_VERSION, skew, None, W,
                         dict(base, measure="skew", normalization="population"),
                         w, None if skew is not None else "degenerate-zero-variance"))
    out.append(_estimate("excess_kurtosis", MEASURE_VERSION, kurt, None, W,
                         dict(base, measure="excess_kurtosis",
                              normalization="population"), w,
                         None if kurt is not None else "degenerate-zero-variance"))

    # historical VaR / CVaR at the 5% level (positive loss magnitudes)
    srt = sorted(w)
    q05 = _quantile(srt, VAR_LEVEL)
    tail_vals = [v for v in w if v <= q05] if q05 is not None else []
    var_loss = None if q05 is None else -q05
    cvar_loss = None if not tail_vals else -sum(tail_vals) / len(tail_vals)
    out.append(_estimate("hist_var_95", MEASURE_VERSION, var_loss, None, W,
                         dict(base, measure="hist_var_95", level=VAR_LEVEL,
                              convention="positive loss magnitude"), w))
    out.append(_estimate("hist_cvar_95", MEASURE_VERSION, cvar_loss, None, W,
                         dict(base, measure="hist_cvar_95", level=VAR_LEVEL,
                              convention="positive loss magnitude",
                              n_tail=len(tail_vals)), w))

    # tail ratio
    if m == 0.0:
        tr, tr_reason = None, "degenerate-zero-mean"
    else:
        tr = abs(sum(tail_vals) / len(tail_vals)) / abs(m) if tail_vals else None
        tr_reason = None if tr is not None else "insufficient-sample"
    out.append(_estimate("tail_ratio", MEASURE_VERSION, tr, None, W,
                         dict(base, measure="tail_ratio", tail_level=TAIL_LEVEL,
                              n_tail=len(tail_vals)), w, tr_reason))
    return out


def _rolling_sd(x, end, w, pre1, pre2):
    """Sample sd of x[end-w+1 .. end] using prefix sums (O(1))."""
    a = end - w + 1
    m = end + 1
    if a < 0 or m > len(x):
        return None
    s = pre1[m] - pre1[a]
    s2 = pre2[m] - pre2[a]
    mean = s / w
    if w < 2:
        return 0.0
    var = (s2 - s * mean) / (w - 1)
    return math.sqrt(max(var, 0.0))


def regime(reference_returns, window, as_of_index) -> Estimate:
    """A deterministic market-regime label, encoded as a float code 0..5.

    The regime is computed from a TRAILING window only: the window that ends
    at ``as_of_index`` (inclusive) of the reference series (the market factor
    by default).  Nothing after ``as_of_index`` is used, so the label for an
    index is stable under any change of the future.

    MAPPING (documented, fixed)
      trend sign  +1 ("up") when the cumulative return of the window is
                  positive, -1 ("down") otherwise
      volatility tercile  "low" when the window volatility is at or below the
                  1/3 quantile of the trailing distribution of window
                  volatilities, "high" when it is at or above the 2/3
                  quantile, "mid" in between.  The trailing distribution is
                  built from the PRECEDING windows only (the window that
                  ends at as_of_index-1 and the ones before it), so the
                  current window is never used to define its own thresholds.
      code = trend_index * 3 + tercile_index with trend_index 0 (down) or
      1 (up) and tercile_index 0 (low), 1 (mid), 2 (high):
        0 down/low-vol   1 down/mid-vol   2 down/high-vol
        3 up/low-vol     4 up/mid-vol     5 up/high-vol

    ASSUMPTIONS
      * The reference series is a market factor (e.g. BTC log returns); the
        label describes that factor, not a single coin.
      * The terciles are relative (self-referential) thresholds: they
        describe the CURRENT regime against the recent past, and they are
        NOT comparable across a change of the window length or of the
        reference series.
      * The label is a coarse, human-readable bucket.  It carries no
        uncertainty: it is a deterministic function of the trailing window,
        not an estimate with a sampling error.
      * With fewer than ``window`` observations up to ``as_of_index``, or
        fewer than MIN_CALIB preceding window volatilities, the label is
        None with a reason.
    """
    name = "regime"
    x, bad = _clean(reference_returns)
    W = int(window)
    idx = int(as_of_index)
    in_params = {"window": W, "as_of_index": idx, "mapping": REGIME_MAPPING}
    n = len(x) if x is not None else 0
    if bad:
        return _estimate(name, MEASURE_VERSION, None, None, 0, in_params, [], bad)
    if idx < 0 or idx >= n:
        return _estimate(name, MEASURE_VERSION, None, None, n, in_params, x,
                         "insufficient-sample")
    if W < 2 or idx + 1 < W:
        return _estimate(name, MEASURE_VERSION, None, None, n, in_params, x,
                         "insufficient-sample")
    win = x[idx - W + 1:idx + 1]
    trend = 1 if sum(win) > 0.0 else -1
    pre1 = [0.0] * (n + 1)
    pre2 = [0.0] * (n + 1)
    for i, v in enumerate(x):
        pre1[i + 1] = pre1[i] + v
        pre2[i + 1] = pre2[i] + v * v
    vol_now = _rolling_sd(x, idx, W, pre1, pre2)
    if vol_now is None:
        return _estimate(name, MEASURE_VERSION, None, None, W, in_params, x,
                         "insufficient-sample")
    calib = []
    for end in range(idx - 1, idx - W - 1, -1):
        v = _rolling_sd(x, end, W, pre1, pre2)
        if v is not None:
            calib.append(v)
        if len(calib) >= W:
            break
    if len(calib) < MIN_CALIB:
        return _estimate(name, MEASURE_VERSION, None, None, W, in_params, x,
                         "insufficient-sample")
    calib.sort()
    q_lo = calib[min(len(calib) - 1, int(len(calib) / 3.0))]
    q_hi = calib[min(len(calib) - 1, int(2.0 * len(calib) / 3.0))]
    if vol_now <= q_lo:
        terc, tname = 0, "low-vol"
    elif vol_now >= q_hi:
        terc, tname = 2, "high-vol"
    else:
        terc, tname = 1, "mid-vol"
    code = (1 if trend > 0 else 0) * 3 + terc
    params = dict(in_params)
    params.update({"trend": "up" if trend > 0 else "down",
                   "window_vol": vol_now, "tercile": tname,
                   "q_lo": q_lo, "q_hi": q_hi, "n_calib": len(calib),
                   "label": REGIME_MAPPING[str(code)]})
    return _estimate(name, MEASURE_VERSION, float(code), None, W, params, win, None)


def dependence(returns_a, returns_b, window) -> Estimate:
    """Trailing Pearson correlation of two return series.

    ALIGNMENT: the two series are right-aligned on their TAILS - the last
    ``n = min(len(a), len(b), window)`` observations of each are paired.  The
    function takes no timestamps, so the caller is responsible for passing
    two series that cover the SAME clock time; unequal lengths are aligned at
    the end, which assumes the shorter series ends at the same instant.

    MINIMUM OVERLAP: at least MIN_OVERLAP (5) aligned observations are
    needed; below that the value is None with the reason
    "insufficient-sample".

    ASSUMPTIONS
      * The correlation is a linear co-movement measure over the window; it
        says nothing about causality or lead-lag.
      * The uncertainty is the Fisher-z approximation (1 - r^2)/sqrt(n-1),
        valid only under bivariate normality; with heavy-tailed crypto
        returns it understates the real error.
      * A zero variance on either side is reported as
        "degenerate-zero-variance" instead of a correlation of 0.
    """
    name = "dependence"
    a, bad_a = _clean(returns_a)
    b, bad_b = _clean(returns_b)
    W = int(window)
    in_params = {"window": W, "min_overlap": MIN_OVERLAP,
                 "alignment": "right-aligned tails"}
    if bad_a or bad_b:
        return _estimate(name, MEASURE_VERSION, None, None, 0, in_params, [],
                         "non-finite-input")
    n = min(len(a), len(b), W)
    if n < MIN_OVERLAP:
        return _estimate(name, MEASURE_VERSION, None, None, n, in_params, [],
                         "insufficient-sample")
    xa = a[-n:]
    xb = b[-n:]
    ma = sum(xa) / n
    mb = sum(xb) / n
    num = sum((u - ma) * (v - mb) for u, v in zip(xa, xb))
    da = math.sqrt(sum((u - ma) ** 2 for u in xa))
    db = math.sqrt(sum((v - mb) ** 2 for v in xb))
    if da <= 0.0 or db <= 0.0:
        return _estimate(name, MEASURE_VERSION, None, None, n, in_params, xa,
                         "degenerate-zero-variance")
    r = num / (da * db)
    r = max(-1.0, min(1.0, r))
    unc = (1.0 - r * r) / math.sqrt(n - 1)
    params = dict(in_params)
    params.update({"n_overlap": n, "alignment": "right-aligned tails"})
    return _estimate(name, MEASURE_VERSION, r, unc, n, params,
                     list(zip(xa, xb)), None)


def breadth(per_coin_returns, window) -> Estimate:
    """Cross-sectional breadth: the fraction of coins with a positive
    trailing-window return.

    A coin contributes only if it has at least ``window`` observations (a
    coin with a shorter history is EXCLUDED and counted, never scored as
    "not positive").  The denominator is therefore the number of coins that
    could be classified, and both the used and the skipped counts are
    reported.

    ASSUMPTIONS
      * The window is trailing: only the last ``window`` observations of each
        coin are used, so the measure is point-in-time.
      * The uncertainty is the binomial standard error sqrt(p(1-p)/n),
        which assumes INDEPENDENT coins.  Crypto coins are strongly
        correlated, so the real uncertainty is larger; the number is a lower
        bound and is reported for that reason.
      * The measure is a count of coins, not a return: it says how many of
        the coins rose, not how much.
    """
    name = "breadth"
    W = int(window)
    in_params = {"window": W}
    if isinstance(per_coin_returns, dict):
        items = list(per_coin_returns.items())
    else:
        items = [(str(i), v) for i, v in enumerate(per_coin_returns or ())]
    used = skipped = pos = neg = zero = 0
    sums = []
    for coin, rets in items:
        x, bad = _clean(rets)
        if bad or x is None or len(x) < W:
            skipped += 1
            continue
        s = sum(x[-W:])
        used += 1
        sums.append((str(coin), s))
        if s > 0.0:
            pos += 1
        elif s < 0.0:
            neg += 1
        else:
            zero += 1
    if used == 0:
        return _estimate(name, MEASURE_VERSION, None, None, 0, in_params, [],
                         "insufficient-sample")
    p = pos / used
    unc = math.sqrt(max(p * (1.0 - p), 0.0) / used)
    params = dict(in_params)
    params.update({"n_coins_total": len(items), "n_coins_used": used,
                   "n_coins_skipped": skipped, "positives": pos,
                   "negatives": neg, "zeros": zero})
    return _estimate(name, MEASURE_VERSION, p, unc, used, params, sums, None)
