"""Rolling Hurst estimators, the shared measure record and the market measures.

DESCRIPTIVE ONLY.  Every function here measures; none of them scores,
selects, ranks or signals.  A measure never becomes a decision.

MODULE LAYOUT (the public surface is ``proto.quant.estimators.<name>`` for
everything below, whatever the file it is implemented in):
  * this module   the ``Estimate`` record, the version constants, the
                  shared numerics and the Hurst family
                  (``hurst_rs``, ``hurst_generalized``);
  * ``mfdfa.py``  the MF-DFA family (``mfdfa``, ``mfdfa_spectrum``);
  * ``measures.py`` the market measures (``volatility_measures``,
                  ``regime``, ``dependence``, ``breadth``).
The other two are re-exported here with a lazy import (PEP 562), so the
documented surface is unchanged and no import cycle exists.

INPUT CONVENTION (read this before using the estimators)
----------------------------------------------------------
``hurst_rs``, ``hurst_generalized`` and ``mfdfa`` take a LEVEL series (a price
path) - the argument is named ``levels`` for exactly that reason.  The
mathematics is the classic one: the cumulative deviation series of a LEVEL
series is formed and its scaling is measured.  Consequences:

  * white-noise levels        -> H = 0.5   (a mean-reverting / noisy path)
  * a random-walk level path  -> H = 1.0   (a persistent path)
  * the DFA structure exponent of the same series is alpha = H + 0.5 for a
    path with stationary increments (1.5 for a random walk), and 0.5 for
    white noise.  ``hurst_rs`` and ``hurst_generalized`` report H; ``mfdfa``
    reports the DFA structure exponent (``dfa_tau``) and says so in its
    ``value_def``.

To measure the persistence of a PRICE path from bar returns, pass the
cumulated returns (:func:`level_from_returns`), not the returns themselves.
The distribution / tail / regime / dependence / breadth measures take
INCREMENTS (returns) - that is their domain.

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
MIN_BLOCKS = 16           # minimum non-overlapping blocks per scale (aggregated variance)
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


def hurst_rs(levels, min_scale=8, max_scale=None, seed=7) -> Estimate:
    """Rescaled-range (Mandelbrot-Wallis) Hurst exponent of a LEVEL series.

    CONSTRUCTION
      For every scale q the series is cut into non-overlapping chunks of q
      bars.  Inside each chunk the cumulative deviation series is formed
      with the chunk's own mean, R = max - min of that series, S = the sample
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
    x, bad = _clean(levels)
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


def hurst_generalized(levels, min_scale=8, max_scale=None, seed=7) -> Estimate:
    """Aggregated-variance (generalized) HURST exponent at q = 2.

    CONSTRUCTION
      The classical aggregated-variance estimator.  For every block size q the
      series is cut into NON-OVERLAPPING blocks of q points and the block
      SUMS ``A_i = sum of the q points of block i`` are formed.  The variance
      ``V(q)`` of those block sums across the blocks is regressed on log q;
      for a self-similar process ``V(q) ~ q^(2H)``, so

          H = slope( log V(q) vs log q ) / 2

    ASSUMPTIONS
      * The input is a LEVEL series (module docstring).  White-noise levels
        give H ~ 0.5, a random-walk path gives H ~ 1.0.  The value is a
        HURST EXPONENT in [0, 1] - it is NOT the DFA structure exponent
        tau (see :func:`mfdfa`, which is H + 0.5 for a path with stationary
        increments); the two must never be mixed up.
      * The blocks must be non-overlapping: the block sums of overlapping
        blocks are strongly correlated and the variance estimate is far too
        noisy.  The scale range is therefore capped so that every scale
        keeps at least MIN_BLOCKS (16) blocks - above that the variance of
        the block sums is dominated by its own sampling error.
      * The estimator measures how the variance of the AGGREGATE (block sum)
        scales.  A deterministic trend inflates H; strong mean reversion
        deflates it; the estimator assumes a self-similar process over the
        scale range.
      * The uncertainty is the standard error of the log-log slope divided
        by two (the same scaling as the value).  It covers the scatter of
        the V(q) points around the fitted line ONLY.  It does NOT cover the
        block-sampling error of V(q) itself (which dominates for a noisy
        series: the observed seed-to-seed spread on white noise is about
        +-0.1), the finite-size bias, the scale-range sensitivity or the
        model risk.
      * The seed is used only for the reported bootstrap cross-check
        (``params["bootstrap_slope_se"]``).
    """
    name = "hurst_generalized"
    x, bad = _clean(levels)
    in_params = {"min_scale": int(min_scale), "max_scale": max_scale,
                 "seed": int(seed), "q": 2.0,
                 "estimator": "aggregated variance of the q-block sums",
                 "value_def": "H = slope(log Var(q-block sums) vs log q) / 2"}
    n = len(x) if x is not None else 0
    if bad:
        return _estimate(name, HURST_VERSION, None, None, 0, in_params, [], bad)
    if n < 2 * int(min_scale):
        return _estimate(name, HURST_VERSION, None, None, n, in_params, x,
                         "insufficient-sample")
    max_s = int(max_scale) if max_scale is not None else n // 4
    max_s = min(max_s, n, max(n // MIN_BLOCKS, int(min_scale)))
    if max_s < int(min_scale):
        return _estimate(name, HURST_VERSION, None, None, n, in_params, x,
                         "insufficient-sample")
    scales = []
    q = int(min_scale)
    while q <= max_s:
        scales.append(q)
        q *= 2
    if len(scales) < 3:
        return _estimate(name, HURST_VERSION, None, None, n, in_params, x,
                         "insufficient-sample")
    prefix = [0.0] * (n + 1)
    for i, v in enumerate(x):
        prefix[i + 1] = prefix[i] + v
    blocks = {}
    pts = []
    for q in scales:
        sums = [prefix[i + q] - prefix[i] for i in range(0, n - q + 1, q)]
        if len(sums) < 2:
            continue
        mu = sum(sums) / len(sums)
        var = sum((s - mu) ** 2 for s in sums) / (len(sums) - 1)
        if var <= 0.0:
            continue
        blocks[q] = sums
        pts.append((math.log(q), math.log(var)))
    if len(pts) < 3:
        return _estimate(name, HURST_VERSION, None, None, n, in_params, x,
                         "degenerate-zero-variance")
    slope, _, se = _ols([p[0] for p in pts], [p[1] for p in pts])
    if slope is None:
        return _estimate(name, HURST_VERSION, None, None, n, in_params, x,
                         "degenerate-zero-variance")
    boot = _bootstrap_agg(blocks, int(seed))
    params = dict(in_params)
    params.update({"n_scales": len(pts), "max_scale_used": scales[-1],
                   "min_blocks": MIN_BLOCKS, "bootstrap_slope_se": boot})
    return _estimate(name, HURST_VERSION, slope / 2.0,
                     None if se is None else se / 2.0, n, params, x, None)


def _bootstrap_agg(blocks, seed, b=BOOTSTRAP_B):
    """Seeded bootstrap of the aggregated-variance exponent: resamples the
    (non-overlapping) block sums of every scale and refits the slope.
    Returns the standard deviation of the resampled H values."""
    if len(blocks) < 3:
        return None
    rng = _Rng(seed)
    scales = sorted(blocks)
    vals = []
    for _ in range(b):
        lx, ly = [], []
        for q in scales:
            sums = blocks[q]
            k = len(sums)
            acc = 0.0
            for _ in range(k):
                acc += sums[rng.randrange(k)]
            mu = acc / k
            var = sum((v - mu) ** 2 for v in sums) / (k - 1)
            if var > 0.0:
                lx.append(math.log(q))
                ly.append(math.log(var))
        sl, _, _ = _ols(lx, ly)
        if sl is not None:
            vals.append(sl / 2.0)
    if len(vals) < 2:
        return None
    mu = sum(vals) / len(vals)
    return math.sqrt(sum((v - mu) ** 2 for v in vals) / (len(vals) - 1))


# --------------------------------------------------------- public re-exports
# The market measures live in proto/quant/measures.py and the MF-DFA family
# in proto/quant/mfdfa.py (this module stays focused on the Hurst family and
# the shared core).  They are re-exported here so the documented public
# surface is unchanged: proto.quant.estimators.<name>.  The import is lazy
# (PEP 562) to keep the modules free of an import cycle; it happens on the
# first attribute access, not at import time.

_MEASURE_EXPORTS = ("breadth", "dependence", "regime", "volatility_measures")
_MFDFA_EXPORTS = ("mfdfa", "mfdfa_spectrum", "SPECTRUM_CONVENTION")

__all__ = [
    "HURST_VERSION", "MFDFA_VERSION", "MEASURE_VERSION", "Estimate",
    "hurst_rs", "hurst_generalized", "mfdfa", "mfdfa_spectrum",
    "level_from_returns", "SPECTRUM_CONVENTION",
    "volatility_measures", "regime", "dependence", "breadth",
]


def __getattr__(name):
    if name in _MEASURE_EXPORTS:
        from . import measures
        return getattr(measures, name)
    if name in _MFDFA_EXPORTS:
        from . import mfdfa
        return getattr(mfdfa, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(list(globals()) + list(_MEASURE_EXPORTS)
                      + list(_MFDFA_EXPORTS)))
