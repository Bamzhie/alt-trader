"""Rolling Hurst / MF-DFA estimators and versioned market measures.

DESCRIPTIVE ONLY.  Every function here measures; none of them scores,
selects, ranks or signals.  A measure never becomes a decision.

INPUT CONVENTION (read this before using the estimators)
----------------------------------------------------------
``hurst_rs``, ``hurst_generalized`` and ``mfdfa`` take a LEVEL series (a price
path).  The contract names the argument ``returns`` for compatibility, but the
mathematics is the classic one: the cumulative deviation series of a LEVEL
series is formed and its scaling is measured.  Consequences:

  * white-noise levels        -> H = 0.5   (a mean-reverting / noisy path)
  * a random-walk level path  -> H = 1.0   (a persistent path)
  * the DFA exponent of the same series is alpha = H + 0.5 for a path with
    stationary increments (1.5 for a random walk), and 0.5 for white noise.

To measure the persistence of a PRICE path from bar returns, pass the
cumulated returns (:func:`level_from_returns`), not the returns themselves.
The distribution / tail / regime / dependence / breadth measures take
INCREMENTS (returns) - that is their domain.  They are implemented in
``proto/quant/measures.py`` and re-exported here, so the documented public
surface stays ``proto.quant.estimators.<name>``.

Every estimator
---------------
  * returns ``value=None`` with a ``reason`` (never a fabricated number) when
    the data cannot support an estimate: "insufficient-sample",
    "degenerate-zero-variance", "degenerate-zero-mean", "non-finite-input";
  * is deterministic: the same inputs and seed give the same value AND the
    same ``inputs_hash`` (a small self-contained PRNG is used, so the result
    does not depend on the Python build);
  * documents its assumptions in the docstring (stationarity, i.i.d.
    increments, no trend, the scale range, the finite-size caveat, and what
    the uncertainty does and does not cover);
  * carries an ``inputs_hash`` (sha256[:16] of the name, the version, the
    parameters and the input series) so a parameter change or a data change
    is detectable, and a measure-version bump changes it too.

Standard library only (hashlib, json, math).
"""
import hashlib
import json
import math
from dataclasses import dataclass

HURST_VERSION = "hurst-v1"
MFDFA_VERSION = "mfdfa-v1"
MEASURE_VERSION = "measures-v1"

EWMA_LAMBDA = 0.94        # RiskMetrics decay for the EWMA volatility
VAR_LEVEL = 0.05          # 5% tail for the historical VaR / CVaR
TAIL_LEVEL = 0.05         # 5% tail for the tail ratio
MIN_OVERLAP = 5           # minimum aligned observations for a correlation
MIN_CALIB = 4             # minimum trailing volatility observations for terciles
BOOTSTRAP_B = 100         # resamples for the seeded uncertainty cross-check

REGIME_MAPPING = {
    "0": "down/low-vol", "1": "down/mid-vol", "2": "down/high-vol",
    "3": "up/low-vol", "4": "up/mid-vol", "5": "up/high-vol",
}


# --------------------------------------------------------------------- types

@dataclass(frozen=True)
class Estimate:
    """One versioned measurement.

    name          the measure ("hurst_rs", "realized_vol", ...)
    version       the measure version (HURST_VERSION / MFDFA_VERSION / ...)
    value         the estimate, or None when it is not estimable
    uncertainty   1-sigma band, or None when it is not estimable
    n             the sample size actually used
    params        the exact parameters used
    inputs_hash   sha256[:16] of name + version + params + input series
    reason        None when estimable, else why not
    """
    name: str
    version: str
    value: float | None
    uncertainty: float | None
    n: int
    params: dict
    inputs_hash: str
    reason: str | None = None


# ------------------------------------------------------------------ numerics

class _Rng:
    """xorshift64* PRNG.

    The estimators must be reproducible across machines and Python builds, so
    the seed does not rely on the implementation details of ``random``.
    """

    __slots__ = ("s",)
    _M = (1 << 64) - 1

    def __init__(self, seed):
        s = int(seed) & self._M
        self.s = s or 0x9E3779B97F4A7C15

    def next_u64(self):
        s = self.s
        s ^= (s << 13) & self._M
        s ^= s >> 7
        s ^= (s << 17) & self._M
        self.s = s
        return (s * 0x2545F4914F6CDD1D) & self._M

    def randrange(self, n):
        return self.next_u64() % n


def _clean(values):
    """(list_of_floats, reason).  Rejects None / NaN / inf."""
    out = []
    for v in values or ():
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None, "non-finite-input"
        if not math.isfinite(f):
            return None, "non-finite-input"
        out.append(f)
    return out, None


def _sd(xs):
    """Sample standard deviation (n-1 denominator); 0.0 for n < 2."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _ols(xs, ys):
    """(slope, intercept, slope_standard_error) of a simple linear fit."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None, None, None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0.0:
        return None, None, None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    inter = my - slope * mx
    if n > 2:
        ssr = sum((y - (slope * x + inter)) ** 2 for x, y in zip(xs, ys))
        se = math.sqrt(max(ssr, 0.0) / (n - 2) / sxx)
    else:
        se = None
    return slope, inter, se


def _inputs_hash(name, version, params, series):
    """sha256[:16] over the name, the version, the parameters and the series.

    Any change of the data or of the parameters changes the hash, so a
    measure-version bump is detectable from the record alone.  Items that are
    not plain floats (e.g. aligned pairs) are hashed through their repr,
    which is exact and deterministic for the tuples used here.
    """
    h = hashlib.sha256()
    h.update(str(name).encode())
    h.update(b"\x00")
    h.update(str(version).encode())
    h.update(b"\x00")
    h.update(json.dumps(params, sort_keys=True, default=str).encode())
    h.update(b"\x00")
    for v in series:
        try:
            f = float(v)
        except (TypeError, ValueError):
            h.update(repr(v).encode())
        else:
            h.update(repr(f).encode())
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _estimate(name, version, value, uncertainty, n, params, series, reason=None):
    return Estimate(name=name, version=version, value=value,
                    uncertainty=uncertainty, n=n, params=params,
                    inputs_hash=_inputs_hash(name, version, params, series),
                    reason=reason)


def level_from_returns(returns):
    """Cumulate a return series into a LEVEL series (a price path proxy).

    The Hurst / MF-DFA estimators expect a level series; this is the
    documented conversion.  ``sum(log returns)`` is the log price, so
    ``exp`` of it is the price if an absolute level is needed.
    """
    x, bad = _clean(returns)
    if bad:
        return None
    out = []
    acc = 0.0
    for v in x:
        acc += v
        out.append(acc)
    return out


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
    """Linear interpolation of the curve (xs, ys) at x0 (None if out of range)."""
    if not xs:
        return None
    for i in range(len(xs) - 1):
        x1, x2 = xs[i], xs[i + 1]
        if x1 <= x0 <= x2:
            if x2 == x1:
                return ys[i]
            w = (x0 - x1) / (x2 - x1)
            return ys[i] + w * (ys[i + 1] - ys[i])
    if x0 <= xs[0]:
        return ys[0]
    return ys[-1]


# --------------------------------------------------------------------- Hurst

def _rs_curve(x, min_scale, max_scale):
    """(R/S) per scale, with the per-chunk values kept for the bootstrap.

    The cumulative deviation is formed INSIDE each chunk with that chunk's
    own mean (the standard Mandelbrot-Wallis construction).  A global mean
    would break the scaling of a non-stationary series.
    """
    n = len(x)
    curve = []
    chunks = {}
    q = min_scale
    while q <= max_scale:
        vals = []
        for k in range(0, n - q + 1, q):
            seg = x[k:k + q]
            s = _sd(seg)
            if s <= 0.0:
                continue
            m = sum(seg) / q
            acc = 0.0
            lo = hi = 0.0
            first = True
            for v in seg:
                acc += v - m
                if first:
                    lo = hi = acc
                    first = False
                else:
                    if acc < lo:
                        lo = acc
                    if acc > hi:
                        hi = acc
            r = (hi - lo) / s
            # Anis-Lloyd correction for the finite-sample upward bias of R/S
            r *= (n - q) / (n - 1.5)
            vals.append(r)
        if vals:
            curve.append((q, sum(vals) / len(vals)))
            chunks[q] = vals
        q *= 2
    return curve, chunks


def hurst_rs(returns, min_scale=8, max_scale=None, seed=7) -> Estimate:
    """Rescaled-range (Mandelbrot-Wallis) Hurst exponent of a LEVEL series.

    CONSTRUCTION
      For every scale q the series is cut into non-overlapping chunks of q
      bars.  Inside each chunk the cumulative deviation series is formed with
      the chunk's own mean, R = max - min of that series, S = the sample
      standard deviation of the chunk, and the Anis-Lloyd finite-sample
      correction (n-q)/(n-1.5) is applied.  The mean R/S over the chunks of a
      scale is regressed on log(q); the slope is H.

    ASSUMPTIONS
      * The input is a LEVEL series (see the module docstring).  White-noise
        levels give H ~ 0.5, a random-walk path gives H ~ 1.0.
      * The R/S statistic is defined for a series with a bounded (or slowly
        growing) range; a strong deterministic trend biases H upwards and a
        mean-reverting series biases it towards 0.
      * The estimate is only meaningful for n >> max_scale; the finite-size
        bias of R/S is the reason for the Anis-Lloyd correction, and the
        residual bias at these sample sizes is a few hundredths.
      * The scale range is [min_scale, max_scale] (default max_scale =
        n // 4) and the scales form a geometric ladder (q, 2q, 4q, ...).
      * The uncertainty is the STANDARD ERROR OF THE SLOPE of the log-log
        regression: it covers the scatter of the R/S points around the fitted
        line only.  It does NOT cover the finite-size bias, the effect of the
        scale range, or the (much larger) uncertainty of applying a
        stationary-process H to a real price series.
      * A seeded block bootstrap of the per-chunk R/S values is reported in
        ``params["bootstrap_slope_se"]`` as an independent cross-check of that
        band; it does not change the value.
    """
    name = "hurst_rs"
    x, bad = _clean(returns)
    in_params = {"min_scale": int(min_scale), "max_scale": max_scale,
                 "seed": int(seed), "anis_lloyd": True,
                 "estimator": "R/S, chunk-local cumulative deviation"}
    n = len(x) if x is not None else 0
    if bad:
        return _estimate(name, HURST_VERSION, None, None, 0, in_params, [], bad)
    if n < 2 * int(min_scale):
        return _estimate(name, HURST_VERSION, None, None, n, in_params, x,
                         "insufficient-sample")
    max_s = int(max_scale) if max_scale is not None else n // 4
    max_s = min(max_s, n)
    if max_s < int(min_scale):
        return _estimate(name, HURST_VERSION, None, None, n, in_params, x,
                         "insufficient-sample")
    curve, chunks = _rs_curve(x, int(min_scale), max_s)
    if len(curve) < 3:
        return _estimate(name, HURST_VERSION, None, None, n, in_params, x,
                         "insufficient-sample")
    lx = [math.log(q) for q, _ in curve]
    ly = [math.log(v) for _, v in curve]
    slope, _, se = _ols(lx, ly)
    if slope is None:
        return _estimate(name, HURST_VERSION, None, None, n, in_params, x,
                         "degenerate-zero-variance")
    boot = _bootstrap_slope(chunks, int(seed))
    params = dict(in_params)
    params.update({"max_scale_used": curve[-1][0], "n_scales": len(curve),
                   "bootstrap_slope_se": boot})
    return _estimate(name, HURST_VERSION, slope, se, n, params, x, None)


def _bootstrap_slope(chunks, seed, b=BOOTSTRAP_B):
    """Seeded block bootstrap of the log-log slope (resamples the per-chunk
    R/S values of every scale).  Returns the standard deviation of the
    resampled slopes, or None when it cannot be computed."""
    if len(chunks) < 3:
        return None
    rng = _Rng(seed)
    scales = sorted(chunks)
    slopes = []
    for _ in range(b):
        pts = []
        for q in scales:
            vals = chunks[q]
            k = len(vals)
            acc = 0.0
            for _ in range(k):
                acc += vals[rng.randrange(k)]
            m = acc / k
            if m > 0.0:
                pts.append((math.log(q), math.log(m)))
        sl, _, _ = _ols([p[0] for p in pts], [p[1] for p in pts])
        if sl is not None:
            slopes.append(sl)
    if len(slopes) < 2:
        return None
    mu = sum(slopes) / len(slopes)
    var = sum((s - mu) ** 2 for s in slopes) / (len(slopes) - 1)
    return math.sqrt(var)


def hurst_generalized(returns, min_scale=8, max_scale=None, seed=7) -> Estimate:
    """Generalized (aggregated-variance) Hurst exponent at q = 2.

    CONSTRUCTION
      The cumulative deviation profile y(t) = sum(x - mean(x)) of the LEVEL
      series is cut into segments of s bars; every segment is detrended by
      its own linear fit; the aggregated variance
      ``F_2(s) = sqrt(mean |Y(s,t)|^2)`` is the RMS fluctuation of the
      detrended profile.  The ordinary least squares slope of log F_2(s) on
      log s over the scale range is the exponent tau(2) - the q = 2 member
      of the generalized structure function (the aggregated-variance
      estimator).

    ASSUMPTIONS
      * The input is a LEVEL series (module docstring).  For a path with
        stationary increments (a random walk) tau(2) = H + 0.5 (1.5); for
        white-noise levels tau(2) = 0.5.
      * A deterministic trend in the series inflates the exponent; the
        linear detrend removes a linear trend per segment only.
      * The scale range is [min_scale, max_scale] (default max_scale =
        n // 4), a geometric ladder; the estimate needs n >> max_scale.
      * The uncertainty is the standard error of the log-log slope: it covers
        the scatter of the fluctuation points only, NOT the finite-size bias,
        the scale-range sensitivity, or the model risk of assuming a
        self-similar process.
      * The seed is used only for the reported bootstrap cross-check
        (``params["bootstrap_slope_se"]``).
    """
    name = "hurst_generalized"
    x, bad = _clean(returns)
    in_params = {"min_scale": int(min_scale), "max_scale": max_scale,
                 "seed": int(seed), "q": 2.0,
                 "estimator": "aggregated variance of the detrended profile"}
    n = len(x) if x is not None else 0
    if bad:
        return _estimate(name, HURST_VERSION, None, None, 0, in_params, [], bad)
    if n < 2 * int(min_scale):
        return _estimate(name, HURST_VERSION, None, None, n, in_params, x,
                         "insufficient-sample")
    max_s = int(max_scale) if max_scale is not None else n // 4
    max_s = min(max_s, n)
    if max_s < int(min_scale):
        return _estimate(name, HURST_VERSION, None, None, n, in_params, x,
                         "insufficient-sample")
    scales = []
    q = int(min_scale)
    while q <= max_s:
        scales.append(q)
        q *= 2
    y = _profile(x)
    taus, per_scale, points = _tau_curve(y, scales, [2.0])
    info = taus[2.0]
    if info["tau"] is None:
        return _estimate(name, HURST_VERSION, None, None, n, in_params, x,
                         "degenerate-zero-variance")
    boot = _bootstrap_tau(points[2.0], int(seed))
    params = dict(in_params)
    params.update({"n_scales": info["n_scales"], "max_scale_used": scales[-1],
                   "bootstrap_slope_se": boot})
    return _estimate(name, HURST_VERSION, info["tau"], info["se"], n, params, x, None)


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
    lo, hi = float(q_range[0]), float(q_range[1])
    if n_q < 2:
        n_q = 2
    if hi <= lo:
        hi = lo + 1.0
    return [lo + (hi - lo) * i / (n_q - 1) for i in range(n_q)]


def mfdfa(returns, q_min=8, q_max=64, q_range=(0.5, 1.5), seed=7, n_q=5) -> Estimate:
    """Multifractal detrended fluctuation analysis: the DFA exponent alpha.

    CONSTRUCTION
      The cumulative deviation profile of the LEVEL series is cut into
      segments of s bars (s from q_min to q_max), each segment is detrended
      by its own linear fit, and the generalized structure function
      ``F_q(s) = (mean |Y(s,t)|^q)^(1/q)`` is formed for every moment order
      q in q_range.  ``tau(q)`` is the log-log slope of F_q(s) on s.
      ``alpha = tau(1)`` - the DFA exponent at the first moment (for a
      monofractal series tau is linear in q, so alpha = tau(q) for every q).

    ASSUMPTIONS
      * The input is a LEVEL series (module docstring).  White-noise levels
        give alpha = 0.5; a random-walk path gives alpha = 1.5 (the standard
        DFA relation alpha = H + 0.5 for a path with stationary increments).
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
    name = "mfdfa_alpha"
    x, bad = _clean(returns)
    in_params = {"q_min": int(q_min), "q_max": int(q_max),
                 "q_range": tuple(float(v) for v in q_range), "n_q": int(n_q),
                 "seed": int(seed), "alpha_def": "tau(1)"}
    n = len(x) if x is not None else 0
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
    orders = _orders(q_range, n_q)
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
                         "degenerate-zero-variance")
    near = min(qs, key=lambda q: abs(q - 1.0))
    se = taus[near]["se"]
    boot = _bootstrap_tau(points[near], int(seed))
    params = dict(in_params)
    params.update({"tau": {str(round(q, 4)): round(t, 6) for q, t in vals},
                   "n_scales": taus[qs[0]]["n_scales"],
                   "bootstrap_slope_se": boot})
    return _estimate(name, MFDFA_VERSION, alpha, se, n, params, x, None)


def mfdfa_spectrum(returns, q_min=8, q_max=64, q_range=(0.5, 1.5), seed=7,
                   n_q=5) -> dict:
    """The multifractal spectrum of a LEVEL series.

    Returns ``{"alpha", "spectrum", "width", "n", "reason"}``:
      alpha     tau(1), the DFA exponent (same definition as :func:`mfdfa`)
      spectrum  [(h, D)] over the moment orders of q_range, where
                h(q) = dtau/dq (a centred difference on the q grid) and
                D(h) = q*h - tau + 1 is the Legendre transform.  The DFA
                profile carries one extra integration, so the absolute level
                of the transform is a normalization artefact: D is reported
                with its PEAK at 1 (the dimension of a single-scale process)
                and only the shape and the width carry information.
      width     h_max - h_min, the horizontal spread of the spectrum: the
                multifractality of the series (0 for an ideal monofractal)
      n         the number of observations used
      reason    None, or why the spectrum is not computable

    The seed is accepted for signature symmetry only: the spectrum involves
    no sampling step and is fully deterministic.

    ASSUMPTIONS: as for :func:`mfdfa`, plus the local slopes h(q) are
    finite-difference estimates on the q grid, so the resolution of the
    spectrum is limited by n_q - a coarse grid smooths the spectrum and can
    understate the width.
    """
    x, bad = _clean(returns)
    n = len(x) if x is not None else 0
    if bad:
        return {"alpha": None, "spectrum": [], "width": None, "n": 0, "reason": bad}
    lo, hi = int(q_min), int(q_max)
    if n < 2 * lo or hi < lo:
        return {"alpha": None, "spectrum": [], "width": None, "n": n,
                "reason": "insufficient-sample"}
    hi = min(hi, n // 2)
    if hi < lo:
        return {"alpha": None, "spectrum": [], "width": None, "n": n,
                "reason": "insufficient-sample"}
    orders = _orders(q_range, n_q)
    y = _profile(x)
    taus, _, _ = _tau_curve(y, range(lo, hi + 1), orders)
    qs = [q for q in sorted(taus) if taus[q]["tau"] is not None]
    if len(qs) < 2:
        return {"alpha": None, "spectrum": [], "width": None, "n": n,
                "reason": "degenerate-zero-variance"}
    tvals = [taus[q]["tau"] for q in qs]
    alpha = _interp(qs, tvals, 1.0)
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
            "n": n, "reason": None}


# --------------------------------------------------------- public re-exports
# The market measures live in proto/quant/measures.py (this module stays
# focused on the Hurst / MF-DFA family).  They are re-exported here so the
# documented public surface is unchanged: proto.quant.estimators.<name>.
# The import is lazy (PEP 562) to keep the two modules free of an import
# cycle; it happens on the first attribute access, not at import time.

_MEASURE_EXPORTS = ("breadth", "dependence", "regime", "volatility_measures")


def __getattr__(name):
    if name in _MEASURE_EXPORTS:
        from . import measures
        return getattr(measures, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(list(globals()) + list(_MEASURE_EXPORTS)))
