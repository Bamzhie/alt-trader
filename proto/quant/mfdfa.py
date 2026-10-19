"""Multifractal DETRENDED FLUCTUATION ANALYSIS: the DFA structure exponent.

DESCRIPTIVE ONLY.  This module measures; it never scores, selects, ranks or
signals.

INPUT CONVENTION: the argument is a LEVEL series (a price path), named
``levels`` for exactly that reason.  White-noise levels give a DFA structure
exponent of 0.5, a random-walk path 1.5.  The DFA structure exponent is NOT a
Hurst exponent H (for a path with stationary increments it is H + 0.5) - use
``proto.quant.estimators.hurst_rs`` / ``hurst_generalized`` for H.

The functions live here so that ``proto.quant.estimators`` stays focused; the
public surface is unchanged: ``proto.quant.estimators.mfdfa`` and
``proto.quant.estimators.mfdfa_spectrum`` (re-exported lazily).

Standard library only.
"""
import math

from .estimators import (BOOTSTRAP_B, MFDFA_VERSION, Estimate, _Rng,
                         _clean, _estimate, _ols)

__all__ = ["mfdfa", "mfdfa_spectrum", "SPECTRUM_CONVENTION"]


def _profile(x):
    """Cumulative deviation series y(t) = sum_{s<=t} (x(s) - mean(x))."""
    n = len(x)
    m = sum(x) / n
    out = []
    acc = 0.0
    for v in x:
        acc += v - m
        out.append(acc)
    return out


def _detrended(y, s):
    """The |Y| fluctuation values of the linearly detrended profile at scale s."""
    n = len(y)
    out = []
    for k in range(0, n - s + 1, s):
        seg = y[k:k + s]
        ns = len(seg)
        mx = (ns - 1) / 2.0
        my = sum(seg) / ns
        sxx = sum((i - mx) ** 2 for i in range(ns))
        b = 0.0 if sxx <= 0.0 else sum((i - mx) * (v - my) for i, v in enumerate(seg)) / sxx
        a = my - b * mx
        for i, v in enumerate(seg):
            out.append(abs(v - (a + b * i)))
    return out


def _tau_curve(y, scales, orders):
    """tau(q) for each moment order q.

    For every scale s the profile is cut into segments of s bars, each segment
    is detrended by its own linear fit, and
    ``F_q(s) = (mean |Y|^q)^(1/q)`` is formed.  ``tau(q)`` is the ordinary
    least squares slope of ``log F_q(s)`` on ``log s`` over the scales - the
    generalized structure function exponent.
    """
    per_scale = {}
    for s in scales:
        vals = _detrended(y, s)
        if len(vals) >= 2:
            per_scale[s] = vals
    taus = {}
    points = {}
    for q in orders:
        pts = []
        for s, vals in per_scale.items():
            acc = 0.0
            for v in vals:
                acc += v ** q
            f = (acc / len(vals)) ** (1.0 / q)
            if f > 0.0 and math.isfinite(f):
                pts.append((math.log(s), math.log(f)))
        slope, _, se = _ols([p[0] for p in pts], [p[1] for p in pts])
        taus[q] = {"tau": slope, "se": se, "n_scales": len(pts)}
        points[q] = pts
    return taus, per_scale, points


def _interp(xs, ys, x0):
    """Linear interpolation of the curve (xs, ys) at x0.

    Returns None when x0 lies OUTSIDE the fitted range: an extrapolation
    would be a fabricated value, so the caller must treat None as "not
    estimable" and report a reason.
    """
    if not xs or x0 < xs[0] or x0 > xs[-1]:
        return None
    for i in range(len(xs) - 1):
        x1, x2 = xs[i], xs[i + 1]
        if x1 <= x0 <= x2:
            if x2 == x1:
                return ys[i]
            w = (x0 - x1) / (x2 - x1)
            return ys[i] + w * (ys[i + 1] - ys[i])
    return ys[-1]

def _bootstrap_tau(points, seed, b=BOOTSTRAP_B):
    """Seeded bootstrap of tau(q): resamples the (log s, log F_q(s)) points of
    the scale ladder and refits the slope.  The points of one scale ladder are
    NOT independent (they share the same series), so this is an optimistic
    band; it is reported as a cross-check, not as the uncertainty."""
    if len(points) < 3:
        return None
    rng = _Rng(seed)
    k = len(points)
    slopes = []
    for _ in range(b):
        lx, ly = [], []
        for _ in range(k):
            p = points[rng.randrange(k)]
            lx.append(p[0])
            ly.append(p[1])
        sl, _, _ = _ols(lx, ly)
        if sl is not None:
            slopes.append(sl)
    if len(slopes) < 2:
        return None
    mu = sum(slopes) / len(slopes)
    return math.sqrt(sum((s - mu) ** 2 for s in slopes) / (len(slopes) - 1))


# -------------------------------------------------------------------- MF-DFA

def _orders(q_range, n_q):
    """The moment-order grid, or (None, reason) when the range is invalid."""
    try:
        lo, hi = float(q_range[0]), float(q_range[1])
    except (TypeError, ValueError, IndexError):
        return None, "invalid-q-range"
    if not (lo > 0.0 and hi > 0.0) or hi <= lo:
        return None, "invalid-q-range"
    if n_q < 2:
        n_q = 2
    return [lo + (hi - lo) * i / (n_q - 1) for i in range(n_q)], None


def _safe_q_range(q_range):
    """A serialisable form of the q range (a broken range is kept as text so
    the record of a refused call still describes what was asked for)."""
    try:
        return tuple(float(v) for v in q_range)
    except (TypeError, ValueError):
        return repr(q_range)


SPECTRUM_CONVENTION = {
    "h": "h(q) = dtau/dq, the local slope of the DFA structure-function curve "
         "in the moment order.  It is NOT a Hurst exponent and NOT the local "
         "Hölder exponent of the price: the DFA profile carries one extra "
         "integration, so the absolute level of h is a normalization "
         "artefact.",
    "D": "D = q*h - tau(q) + 1, the Legendre transform of the tau curve, "
         "rescaled so that max(D) = 1 (the dimension of a single-scale "
         "process).  The absolute level of D is NOT a fractal dimension.",
    "width": "h_max - h_min, the spread of the local slopes.  This is the "
             "only quantity that is comparable across series: 0 for an ideal "
             "monofractal, larger for a multifractal.",
    "alpha": "tau(1), the DFA structure exponent (alpha = H + 0.5 for a path "
             "with stationary increments).  It is NOT a Hurst exponent H - "
             "use hurst_rs / hurst_generalized for H.",
}


def mfdfa(levels, q_min=8, q_max=64, q_range=(0.5, 1.5), seed=7, n_q=5) -> Estimate:
    """Multifractal detrended fluctuation analysis: the DFA structure exponent.

    CONSTRUCTION
      The cumulative deviation profile of the LEVEL series is cut into
      segments of s bars (s from q_min to q_max), each segment is detrended
      by its own linear fit, and the generalized structure function
      ``F_q(s) = (mean |Y(s,t)|^q)^(1/q)`` is formed for every moment order
      q in q_range.  ``tau(q)`` is the log-log slope of F_q(s) on s.
      The reported value is ``dfa_tau = tau(1)`` - the DFA structure
      exponent at the first moment (for a monofractal series tau is flat in
      q, so tau(1) equals tau(q) for every q).

      THE VALUE IS NOT A HURST EXPONENT.  For a path with stationary
      increments the DFA exponent is H + 0.5 (1.5 for a random walk), while
      the Hurst exponent H of the same path is 1.0.  Use
      :func:`hurst_rs` or :func:`hurst_generalized` for H.

    ASSUMPTIONS
      * The input is a LEVEL series (module docstring).  White-noise levels
        give dfa_tau = 0.5; a random-walk path gives 1.5.
      * The measure assumes a self-similar (scale-free) process over the
        scale range [q_min, q_max]; outside it the fit is not meaningful.
      * The finite-size caveat is the dominant one: with n of this size the
        scale range must stay well below n, and the exponent has a bias of
        a few hundredths at the smallest scales.
      * The uncertainty is the standard error of the log-log slope of the
        q = 1 curve: it covers the scatter of the fluctuation points only,
        NOT the finite-size bias, the scale-range sensitivity, or the
        multifractal model risk.
      * The seed only drives the reported bootstrap cross-check
        (``params["bootstrap_slope_se"]``).
    """
    name = "dfa_tau"
    # the parameters are validated BEFORE the series is touched, so a broken
    # q_range is reported as a reason and never raises
    orders, q_reason = _orders(q_range, n_q)
    in_params = {"q_min": int(q_min), "q_max": int(q_max),
                 "q_range": _safe_q_range(q_range), "n_q": int(n_q),
                 "seed": int(seed),
                 "value_def": "dfa_tau = tau(1), the DFA structure exponent "
                             "(NOT a Hurst exponent H)"}
    x, bad = _clean(levels)
    n = len(x) if x is not None else 0
    if orders is None:
        return _estimate(name, MFDFA_VERSION, None, None, n, in_params, [],
                         q_reason)
    if bad:
        return _estimate(name, MFDFA_VERSION, None, None, 0, in_params, [], bad)
    lo, hi = int(q_min), int(q_max)
    if n < 2 * lo or hi < lo:
        return _estimate(name, MFDFA_VERSION, None, None, n, in_params, x,
                         "insufficient-sample")
    hi = min(hi, n // 2)
    if hi < lo:
        return _estimate(name, MFDFA_VERSION, None, None, n, in_params, x,
                         "insufficient-sample")
    y = _profile(x)
    taus, per_scale, points = _tau_curve(y, range(lo, hi + 1), orders)
    qs = sorted(taus)
    vals = [(q, taus[q]["tau"]) for q in qs if taus[q]["tau"] is not None]
    if len(vals) < 2:
        return _estimate(name, MFDFA_VERSION, None, None, n, in_params, x,
                         "degenerate-zero-variance")
    alpha = _interp([v[0] for v in vals], [v[1] for v in vals], 1.0)
    if alpha is None:
        return _estimate(name, MFDFA_VERSION, None, None, n, in_params, x,
                         "alpha-outside-q-range")
    near = min(qs, key=lambda q: abs(q - 1.0))
    se = taus[near]["se"]
    boot = _bootstrap_tau(points[near], int(seed))
    params = dict(in_params)
    params.update({"tau": {str(round(q, 4)): round(t, 6) for q, t in vals},
                   "n_scales": taus[qs[0]]["n_scales"],
                   "bootstrap_slope_se": boot})
    return _estimate(name, MFDFA_VERSION, alpha, se, n, params, x, None)


def mfdfa_spectrum(levels, q_min=8, q_max=64, q_range=(0.5, 1.5), seed=7,
                   n_q=5) -> dict:
    """The multifractal spectrum of a LEVEL series.

    Returns ``{"alpha", "spectrum", "width", "n", "reason", "convention",
    "q_grid", "tau"}``:
      alpha     tau(1), the DFA structure exponent (the same definition as
                :func:`mfdfa`; NOT a Hurst exponent H)
      spectrum  [(h, D)] over the moment orders of q_range, where
                h(q) = dtau/dq (a centred difference on the q grid) and
                D(h) = q*h - tau + 1 is the Legendre transform.  The DFA
                profile carries one extra integration, so the absolute level
                of the transform is a normalization artefact: D is reported
                with its PEAK at 1 (the dimension of a single-scale process)
                and only the shape and the width carry information.
      width     h_max - h_min, the horizontal spread of the spectrum: the
                multifractality of the series (0 for an ideal monofractal).
                This is the ONLY quantity that is comparable across series.
      n         the number of observations used
      reason    None, or why the spectrum is not computable
      convention  a dict that states, key by key, what h, D, width and alpha
                mean and what they are NOT (so no consumer can read a Hurst
                exponent or a fractal dimension into them)
      q_grid    the moment orders that were used
      tau       the tau(q) values behind the spectrum

    The seed is accepted for signature symmetry only: the spectrum involves
    no sampling step and is fully deterministic.

    ASSUMPTIONS: as for :func:`mfdfa`, plus the local slopes h(q) are
    finite-difference estimates on the q grid, so the resolution of the
    spectrum is limited by n_q - a coarse grid smooths the spectrum and can
    understate the width.
    """
    x, bad = _clean(levels)
    n = len(x) if x is not None else 0
    empty = {"alpha": None, "spectrum": [], "width": None, "n": n,
             "reason": None, "convention": dict(SPECTRUM_CONVENTION),
             "q_grid": [], "tau": {}}
    # the parameters are validated BEFORE the series is touched, so a broken
    # q_range is reported as a reason and never raises
    orders, q_reason = _orders(q_range, n_q)
    if orders is None:
        empty["reason"] = q_reason
        return empty
    if bad:
        empty["reason"] = bad
        return empty
    lo, hi = int(q_min), int(q_max)
    if n < 2 * lo or hi < lo:
        empty["reason"] = "insufficient-sample"
        return empty
    hi = min(hi, n // 2)
    if hi < lo:
        empty["reason"] = "insufficient-sample"
        return empty
    y = _profile(x)
    taus, _, _ = _tau_curve(y, range(lo, hi + 1), orders)
    qs = [q for q in sorted(taus) if taus[q]["tau"] is not None]
    if len(qs) < 2:
        empty["reason"] = "degenerate-zero-variance"
        return empty
    tvals = [taus[q]["tau"] for q in qs]
    alpha = _interp(qs, tvals, 1.0)
    if alpha is None:
        empty["reason"] = "alpha-outside-q-range"
        empty["q_grid"] = [round(q, 6) for q in qs]
        empty["tau"] = {str(round(q, 4)): round(t, 6) for q, t in zip(qs, tvals)}
        return empty
    spec = []
    for i, q in enumerate(qs):
        if i == 0:
            h = (tvals[1] - tvals[0]) / (qs[1] - qs[0])
        elif i == len(qs) - 1:
            h = (tvals[-1] - tvals[-2]) / (qs[-1] - qs[-2])
        else:
            h = (tvals[i + 1] - tvals[i - 1]) / (qs[i + 1] - qs[i - 1])
        spec.append((h, q * h - tvals[i] + 1.0))
    # the DFA profile carries one extra integration, so the absolute level of
    # the Legendre transform is a normalization artefact: report the spectrum
    # with its peak at D = 1 (the dimension of a single-scale process).
    d_max = max(d for _, d in spec)
    spec = [(h, d - d_max + 1.0) for h, d in spec]
    hs = [h for h, _ in spec]
    return {"alpha": alpha, "spectrum": spec, "width": max(hs) - min(hs),
            "n": n, "reason": None,
            "convention": dict(SPECTRUM_CONVENTION),
            "q_grid": [round(q, 6) for q in qs],
            "tau": {str(round(q, 4)): round(t, 6) for q, t in zip(qs, tvals)}}

