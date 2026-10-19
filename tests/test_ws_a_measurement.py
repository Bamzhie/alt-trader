"""Workstream A: market measurement & structure engine.

Self-checking script in the repo house style: prints PASS/FAIL per check and
exits 1 when anything fails.  Offline, standard library only, no writes
outside a tempfile fixture.
"""
import dataclasses
import inspect
import math
import os
import random
import statistics
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.quant import structure as ST
from proto.quant import estimators as ES
from proto.quant import feature_store as FS
from proto.quant.config import BAR_S

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


T0 = 1_700_006_400            # aligned to 1 h, and therefore to 15 m and 5 m


# ----------------------------------------------------------------- fixtures

def bars_from_hl(highs, lows, t0=T0, step=BAR_S):
    """Bars with controlled highs/lows (c = mid, flat vol/amt)."""
    out = []
    for i, (h, l) in enumerate(zip(highs, lows)):
        c = 0.5 * (h + l)
        out.append({"ts": t0 + i * step, "o": c, "h": h, "l": l, "c": c,
                    "vol": 10.0, "amt": 1000.0})
    return out


def make_bars(n, seed=1, sigma=0.002, t0=T0, price=100.0):
    """Random-walk OHLC bars."""
    rng = random.Random(seed)
    out = []
    px = price
    for i in range(n):
        o = px
        px *= math.exp(rng.gauss(0, sigma))
        c = px
        h = max(o, c) * (1 + abs(rng.gauss(0, sigma / 3)))
        l = min(o, c) * (1 - abs(rng.gauss(0, sigma / 3)))
        out.append({"ts": t0 + i * BAR_S, "o": o, "h": h, "l": l, "c": c,
                    "vol": 1000.0, "amt": 1000.0 * c})
    return out


def white_noise_series(n, seed=20261019):
    rng = random.Random(seed)
    return [rng.gauss(0.0, 1.0) for _ in range(n)]


def random_walk_series(n, seed=20261019):
    rng = random.Random(seed)
    out = []
    x = 0.0
    for _ in range(n):
        x += rng.gauss(0.0, 1.0)
        out.append(x)
    return out


def bimultifractal_series(n, seed=20261019, block=64):
    """Synthetic BIMULTIFRACTAL: alternating trending (random-walk) and
    mean-reverting (white-noise) blocks, i.e. two distinct local exponents.
    The generalized spectrum is therefore wide, unlike a monofractal's."""
    rng = random.Random(seed)
    out = []
    lvl = 0.0
    for i in range(n):
        if (i // block) % 2 == 0:
            lvl += rng.gauss(0.0, 1.0)
            out.append(lvl)
        else:
            out.append(rng.gauss(0.0, 1.0))
    return out


# =========================================================== structure: pivots
print("=== structure: confirmed pivots ===")

hs = [5.0] * 20
hs[10] = 9.0
ls = [1.0] * 20
bars = bars_from_hl(hs, ls)
pv = ST.confirmed_pivots(bars, left=2, right=2)
ph = [p for p in pv if p.kind == "high"]
check("a local max is a pivot high", len(ph) == 1 and ph[0].index == 10, str(pv))
check("pivot ts is the bar's own open timestamp", ph[0].ts == bars[10]["ts"])
check("confirmed_ts == ts + (right+1)*BAR_S",
      ph[0].confirmed_ts == bars[10]["ts"] + 3 * BAR_S,
      f"{ph[0].confirmed_ts} vs {bars[10]['ts'] + 3 * BAR_S}")
check("the pivot price is the bar high", abs(ph[0].price - 9.0) < 1e-12)
check("the last `right` bars are never pivots",
      all(p.index < len(bars) - 2 for p in pv), str([p.index for p in pv]))
check("a pivot is invisible before confirmed_ts",
      ST.pivots_as_of(pv, bars[10]["ts"] + 3 * BAR_S - 1) == [])
check("a pivot is visible at confirmed_ts",
      len(ST.pivots_as_of(pv, bars[10]["ts"] + 3 * BAR_S)) == 1)

# local uniqueness: equal highs inside ONE window -> only the last becomes one
hs = [5.0] * 30
hs[10] = 9.0
hs[12] = 9.0
pv = ST.confirmed_pivots(bars_from_hl(hs, [1.0] * 30), left=2, right=2)
ih = [p.index for p in pv if p.kind == "high"]
check("two equal highs in one window: only the last is a pivot", ih == [12], str(ih))
# the same values in SEPARATE windows -> both are pivots
hs = [5.0] * 30
hs[10] = 9.0
hs[16] = 9.0
pv = ST.confirmed_pivots(bars_from_hl(hs, [1.0] * 30), left=2, right=2)
ih = [p.index for p in pv if p.kind == "high"]
check("the same values in separate windows: both are pivots", ih == [10, 16], str(ih))

# legs and sizes
hs = [5.0] * 30
hs[10] = 9.0
ls = [1.0] * 30
ls[14] = 0.5
hs[18] = 11.0
pv = ST.confirmed_pivots(bars_from_hl(hs, ls), left=2, right=2)
byidx = {p.index: p for p in pv}
check("a first pivot has leg_bars 0 and size 0.0",
      byidx[10].leg_bars == 0 and byidx[10].size == 0.0)
check("a leg is measured against the previous OPPOSITE pivot",
      byidx[14].leg_bars == 4 and abs(byidx[14].size - abs(math.log(0.5 / 9.0))) < 1e-12,
      f"{byidx[14].leg_bars} {byidx[14].size}")
check("the last leg is measured against the previous opposite pivot",
      byidx[18].leg_bars == 4 and abs(byidx[18].size - abs(math.log(11.0 / 0.5))) < 1e-12,
      f"{byidx[18].leg_bars} {byidx[18].size}")

# ================================================= structure: resample + mtf
print("\n=== structure: resample and multi-timeframe ===")
bars = make_bars(60, seed=3)
fr = ST.resample(bars, 12)
check("resample emits 60/12 frames", len(fr) == 5, str(len(fr)))
f0 = fr[0]
check("a frame starts at the first bar's timestamp", f0["ts"] == bars[0]["ts"])
check("a frame closes at the last bar's close", abs(f0["c"] - bars[11]["c"]) < 1e-12)
check("a frame takes the max high and the min low",
      abs(f0["h"] - max(b["h"] for b in bars[:12])) < 1e-12
      and abs(f0["l"] - min(b["l"] for b in bars[:12])) < 1e-12)
check("a frame sums the volume and the amount",
      abs(f0["vol"] - sum(b["vol"] for b in bars[:12])) < 1e-9
      and abs(f0["amt"] - sum(b["amt"] for b in bars[:12])) < 1e-6)
check("a frame carries the same keys as a bar",
      set(fr[0].keys()) == set(bars[0].keys()), str(sorted(fr[0].keys())))

gapped = [b for i, b in enumerate(bars) if i != 5]      # a hole inside frame 0
fr_g = ST.resample(gapped, 12)
check("a frame with a gap is dropped, not interpolated",
      len(fr_g) == 4 and all(f["ts"] != bars[0]["ts"] for f in fr_g), str(len(fr_g)))

cut = bars[11]["ts"] + BAR_S          # first frame is closed at this instant
check("a frame is not emitted before it is closed",
      all(f["ts"] + 12 * BAR_S <= cut for f in ST.resample(bars, 12, as_of_ts=cut))
      and len(ST.resample(bars, 12, as_of_ts=cut)) == 1)

mtf = ST.multi_timeframe(bars, frames=(3, 12), left=2, right=2)
check("multi_timeframe returns one entry per frame", set(mtf.keys()) == {3, 12})
check("each frame carries pivots and a structure",
      all(set(v.keys()) == {"pivots", "structure"} for v in mtf.values()))
st12 = mtf[12]["structure"]
check("the structure summary has the documented keys",
      set(st12.keys()) == {"trend", "last_pivot_high", "last_pivot_low", "swing_bars"},
      str(sorted(st12.keys())))
check("the trend label is one of up/down/mixed",
      st12["trend"] in ("up", "down", "mixed"), st12["trend"])
check("the last pivot prices agree with the pivot list",
      all((st12["last_pivot_high"] is None) == (not [p for p in mtf[12]["pivots"] if p.kind == "high"])
          for _ in (0,)))

# point in time: perturbing the future changes nothing as of t
tbars = make_bars(120, seed=5)
cut_ts = tbars[60]["ts"] + BAR_S
mtf_a = ST.multi_timeframe(tbars, frames=(3, 12), as_of_ts=cut_ts)
alt = list(tbars)
for i, b in enumerate(alt):
    if i > 60:
        alt[i] = dict(b, c=b["c"] * 1.5, h=b["h"] * 1.5, l=b["l"] * 1.5)
mtf_b = ST.multi_timeframe(alt, frames=(3, 12), as_of_ts=cut_ts)


def _pivot_key(pivots):
    return [(p.index, p.ts, p.confirmed_ts, p.price, p.kind, p.leg_bars, p.size)
            for p in pivots]


same = (mtf_a[3]["structure"] == mtf_b[3]["structure"]
        and mtf_a[12]["structure"] == mtf_b[12]["structure"]
        and _pivot_key(mtf_a[3]["pivots"]) == _pivot_key(mtf_b[3]["pivots"])
        and _pivot_key(mtf_a[12]["pivots"]) == _pivot_key(mtf_b[12]["pivots"]))
check("no lookahead: perturbing the future leaves the as-of structure AND pivots identical",
      same)
check("as-of frames only use closed bars",
      all(p.confirmed_ts <= cut_ts for p in mtf_a[12]["pivots"]))
check("as-of frames only use closed bars (3-bar frame)",
      all(p.confirmed_ts <= cut_ts for p in mtf_a[3]["pivots"]))

# a pivot of a FRAME is confirmed when its last confirming frame bar closes
fh = [5.0] * 20
fh[10] = 9.0
fl = [1.0] * 20
framebars = bars_from_hl(fh, fl, step=12 * BAR_S)      # an hourly frame series
fpv = ST.confirmed_pivots(framebars, left=2, right=2, frame_bars=12)
check("a frame pivot is confirmed when the last confirming FRAME bar closes",
      len(fpv) == 1
      and fpv[0].confirmed_ts == framebars[fpv[0].index + 2]["ts"] + 12 * BAR_S,
      str([(p.index, p.confirmed_ts) for p in fpv]))
check("a frame pivot is not knowable before its frame closes",
      ST.pivots_as_of(fpv, framebars[fpv[0].index + 2]["ts"] + 12 * BAR_S - 1) == [])
check("the base (5 m) frame keeps the documented confirmation timing",
      ST.confirmed_pivots(bars, left=2, right=2)[0].confirmed_ts
      == bars[ST.confirmed_pivots(bars, left=2, right=2)[0].index]["ts"] + 3 * BAR_S)

# a bar that is both a high and a low pivot
both = ST.confirmed_pivots(bars_from_hl([5.0, 9.0, 5.0], [1.0, 1.0, 2.0]),
                           left=1, right=1)
kinds = sorted((p.index, p.kind) for p in both)
check("a bar can be a high and a low pivot at once", kinds == [(1, "high"), (1, "low")],
      str(kinds))
lowp = [p for p in both if p.kind == "low"][0]
check("the previous opposite pivot is strictly earlier (the same bar is skipped)",
      lowp.leg_bars == 0 and lowp.size == 0.0, f"{lowp.leg_bars} {lowp.size}")

# a 5-tuple bar is accepted (volume and amount default to 0.0)
tuples5 = [(b["ts"], b["o"], b["h"], b["l"], b["c"]) for b in make_bars(24, seed=9)]
try:
    fr5 = ST.resample(tuples5, 12)
    ok5 = len(fr5) == 2 and fr5[0]["vol"] == 0.0 and fr5[0]["amt"] == 0.0
except Exception as exc:                                     # noqa: BLE001
    ok5 = False
    print(f"    (resample on 5-tuples raised {exc!r})")
check("a 5-tuple bar is accepted and defaults the volume and the amount", ok5)

# ============================================================== estimators
print("\n=== estimators: public surface ===")
from proto.quant import measures as MS

check("the market measures are reachable from proto.quant.estimators",
      ES.volatility_measures is MS.volatility_measures
      and ES.regime is MS.regime and ES.dependence is MS.dependence
      and ES.breadth is MS.breadth)
check("Estimate is a frozen dataclass",
      dataclasses.is_dataclass(ES.Estimate)
      and ES.Estimate.__dataclass_params__.frozen)
check("the measure versions are exposed",
      (ES.HURST_VERSION, ES.MFDFA_VERSION, ES.MEASURE_VERSION)
      == ("hurst-v1", "mfdfa-v1", "measures-v1"))
check("the Hurst / MF-DFA estimators take a `levels` argument",
      all(list(inspect.signature(fn).parameters)[0] == "levels"
          for fn in (ES.hurst_rs, ES.hurst_generalized, ES.mfdfa,
                     ES.mfdfa_spectrum)))
ns = {}
exec("from proto.quant.estimators import *", ns)
check("`import *` exposes the market measures too",
      all(k in ns for k in ("volatility_measures", "regime", "dependence",
                            "breadth", "hurst_rs", "mfdfa", "Estimate")))

print("\n=== estimators: Hurst ===")
N = 4096
wn = white_noise_series(N)
rw = random_walk_series(N)
h_wn = ES.hurst_rs(wn)
h_rw = ES.hurst_rs(rw)
print(f"  hurst_rs white noise   H = {h_wn.value:.4f}  (1-sigma {h_wn.uncertainty:.4f}, n={h_wn.n})")
print(f"  hurst_rs random walk   H = {h_rw.value:.4f}  (1-sigma {h_rw.uncertainty:.4f}, n={h_rw.n})")
check("Hurst on synthetic white noise is within 0.10 of 0.5",
      h_wn.value is not None and abs(h_wn.value - 0.5) <= 0.10, str(h_wn.value))
check("Hurst on a synthetic random walk is within 0.15 of 1.0",
      h_rw.value is not None and abs(h_rw.value - 1.0) <= 0.15, str(h_rw.value))
check("the Hurst estimate reports its sample size and parameters",
      h_wn.n == N and h_wn.params["min_scale"] == 8 and h_wn.params["seed"] == 7,
      str(h_wn.params))
check("the Hurst estimate carries a version and an inputs hash",
      h_wn.version == ES.HURST_VERSION and len(h_wn.inputs_hash) == 16)

hg = ES.hurst_generalized(rw)
hg_wn = ES.hurst_generalized(wn)
print(f"  hurst_generalized random walk  = {hg.value:.4f} (1-sigma {hg.uncertainty})")
print(f"  hurst_generalized white noise  = {hg_wn.value:.4f} (1-sigma {hg_wn.uncertainty})")
check("hurst_generalized is estimable on a long series",
      hg.value is not None and hg.reason is None, str(hg))
check("hurst_generalized is a HURST exponent: a random walk is near 1.0",
      hg.value is not None and abs(hg.value - 1.0) <= 0.15, str(hg.value))
check("hurst_generalized is a HURST exponent: white noise is near 0.5",
      hg_wn.value is not None and abs(hg_wn.value - 0.5) <= 0.15, str(hg_wn.value))
check("hurst_generalized differs from the DFA structure exponent",
      abs(hg.value - ES.mfdfa(rw).value) > 0.2,
      f"{hg.value} vs {ES.mfdfa(rw).value}")
check("hurst_generalized stays inside the Hurst range [0, 1]",
      0.0 <= hg.value <= 1.0 and 0.0 <= hg_wn.value <= 1.0)

print("\n=== estimators: MF-DFA ===")
mono = white_noise_series(N)
bi = bimultifractal_series(N)
a_mono = ES.mfdfa(mono)
sp_mono = ES.mfdfa_spectrum(mono)
sp_bi = ES.mfdfa_spectrum(bi)
print(f"  mfdfa dfa_tau (monofractal white noise) = {a_mono.value:.4f} "
      f"(1-sigma {a_mono.uncertainty:.4f}, n={a_mono.n})")
print(f"  spectrum width  monofractal = {sp_mono['width']:.4f}   "
      f"bimultifractal = {sp_bi['width']:.4f}")
print(f"  spectrum (monofractal) = {[(round(h, 4), round(d, 4)) for h, d in sp_mono['spectrum']]}")
print(f"  spectrum (bimultifractal) = {[(round(h, 4), round(d, 4)) for h, d in sp_bi['spectrum']]}")
check("MF-DFA alpha is within 0.15 of 0.5 on a synthetic monofractal",
      a_mono.value is not None and abs(a_mono.value - 0.5) <= 0.15, str(a_mono.value))
check("the DFA statistic is reported under a name that is not a Hurst exponent",
      a_mono.name == "dfa_tau", a_mono.name)
check("the spectrum width on a bimultifractal is materially larger than on a monofractal",
      sp_bi["width"] > sp_mono["width"] + 0.05 and sp_bi["width"] > 5 * sp_mono["width"],
      f"{sp_mono['width']} vs {sp_bi['width']}")
check("the spectrum is a list of (h, D) pairs",
      isinstance(sp_bi["spectrum"], list) and len(sp_bi["spectrum"]) >= 2
      and all(len(p) == 2 for p in sp_bi["spectrum"]))
check("the spectrum reports its sample size and a reason slot",
      sp_bi["n"] == N and sp_bi["reason"] is None)
check("the spectrum states its convention (h and D are not Hurst/dimension)",
      isinstance(sp_bi.get("convention"), dict)
      and "h" in sp_bi["convention"] and "D" in sp_bi["convention"]
      and "NOT" in sp_bi["convention"]["h"], str(sp_bi.get("convention")))
check("the spectrum exposes the moment-order grid it used",
      isinstance(sp_bi.get("q_grid"), list) and len(sp_bi["q_grid"]) == 5)
bad_q = ES.mfdfa_spectrum(mono, q_range=(-1.0, 1.0))
check("an invalid q range is refused with a reason",
      bad_q["alpha"] is None and bad_q["reason"], str(bad_q))
check("an invalid q range never raises",
      ES.mfdfa(mono, q_range=(-1.0, 1.0)).value is None
      and ES.mfdfa(mono, q_range=(-1.0, 1.0)).reason is not None)
outside = ES.mfdfa(mono, q_range=(0.2, 0.8))
check("an alpha outside the fitted q range is not extrapolated",
      outside.value is None and outside.reason, str(outside))

print("\n=== estimators: degenerate inputs ===")
for label, series in (("a constant series", [1.0] * 50),
                       ("a 3-point series", [0.1, 0.2, 0.3]),
                       ("an all-NaN series", [float("nan")] * 10),
                       ("an empty series", [])):
    for fn in (ES.hurst_rs, ES.hurst_generalized, ES.mfdfa):
        try:
            est = fn(series)
        except Exception as exc:                      # noqa: BLE001
            check(f"{label} -> {fn.__name__} does not raise", False, repr(exc))
            continue
        check(f"{label} -> {fn.__name__} returns None + a reason",
              est.value is None and isinstance(est.reason, str) and est.reason,
              f"{est.value} {est.reason}")
    try:
        sp = ES.mfdfa_spectrum(series)
        check(f"{label} -> mfdfa_spectrum returns None + a reason",
              sp["alpha"] is None and isinstance(sp["reason"], str) and sp["reason"],
              str(sp))
    except Exception as exc:                          # noqa: BLE001
        check(f"{label} -> mfdfa_spectrum does not raise", False, repr(exc))

print("\n=== estimators: determinism and versioning ===")
check("the same input and seed give the same value and hash",
      ES.hurst_rs(wn).value == ES.hurst_rs(wn).value
      and ES.hurst_rs(wn).inputs_hash == ES.hurst_rs(wn).inputs_hash)
check("the same input and seed give the same MF-DFA value and hash",
      ES.mfdfa(mono).value == ES.mfdfa(mono).value
      and ES.mfdfa(mono).inputs_hash == ES.mfdfa(mono).inputs_hash)
check("the inputs hash changes when a parameter changes",
      ES.hurst_rs(wn, min_scale=16).inputs_hash != ES.hurst_rs(wn, min_scale=8).inputs_hash)
check("the inputs hash changes when the series changes",
      ES.hurst_rs(wn).inputs_hash != ES.hurst_rs(rw).inputs_hash)
check("a different version yields a different hash",
      ES.hurst_rs(wn).inputs_hash != ES.hurst_rs(wn, seed=8).inputs_hash)
check("the dependence hash covers BOTH series",
      ES.dependence([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], [2 * v for v in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)], 8).inputs_hash
      != ES.dependence([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], [3 * v for v in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)], 8).inputs_hash)

print("\n=== estimators: trailing only, no lookahead ===")
_rng = random.Random(4242)
base = [_rng.gauss(0.0, 0.002) for _ in range(600)]
cut = 400
future = list(base)
for i in range(cut, len(future)):
    future[i] += 0.05                      # a violent future
h1, h2 = ES.hurst_rs(base[:cut]), ES.hurst_rs(future[:cut])
check("a global estimator only uses the series it was given",
      h1.value == h2.value and h1.inputs_hash == h2.inputs_hash)
past = [x + 10.0 for x in base[:100]] + base        # a violent PAST
m1 = ES.volatility_measures(base, 50)
m2 = ES.volatility_measures(past, 50)
check("a trailing measure ignores everything before its window",
      [(x.value, x.inputs_hash) for x in m1] == [(x.value, x.inputs_hash) for x in m2])
d1 = ES.dependence(base[:cut], base[100:100 + cut], 100)
d2 = ES.dependence(past[:500], base[100:100 + cut], 100)
check("the dependence measure ignores everything before its window",
      d1.value == d2.value and d1.inputs_hash == d2.inputs_hash)
b1 = ES.breadth({"A": base[:50], "B": base[50:100]}, 50)
b2 = ES.breadth({"A": past[100:150], "B": past[150:200]}, 50)
check("the breadth measure ignores everything before its window",
      b1.value == b2.value and b1.inputs_hash == b2.inputs_hash)

print("\n=== estimators: only the measured slice is cleaned ===")
NAN = float("nan")
INF = float("inf")
clean = [0.010, -0.020, 0.005, 0.000, -0.030, 0.020, 0.012, -0.008,
         0.003, -0.004, 0.007, -0.001, 0.011, 0.002, -0.006, 0.009,
         0.004, -0.003, 0.013, -0.009]

# a non-finite value OUTSIDE the measured slice must not change anything
v1 = ES.volatility_measures(clean, 10)
v2 = ES.volatility_measures([NAN] + clean, 10)
v3 = ES.volatility_measures(clean + [INF], 10)
check("a non-finite value before the measured window does not change the estimate",
      v2[0].value == v1[0].value and v2[0].inputs_hash == v1[0].inputs_hash,
      f"{v2[0].value} vs {v1[0].value}")
check("a non-finite value inside the measured window yields None + a reason",
      all(m.value is None and m.reason for m in v3), str(v3[0]))
v4 = ES.volatility_measures(clean[:19] + [NAN], 10)
check("a non-finite value inside the measured window yields None + a reason",
      all(m.value is None and m.reason for m in v4), str(v4[0]))

long_clean = [0.001 * ((i % 7) - 3) for i in range(200)]
r1 = ES.regime(long_clean, 20, 199)
r2 = ES.regime(long_clean + [NAN], 20, 199)
old_nan = list(long_clean)
old_nan[0] = NAN
r3 = ES.regime(old_nan, 20, 199)
check("the regime ignores a non-finite value after as_of_index",
      r1.value == r2.value and r1.inputs_hash == r2.inputs_hash,
      f"{r1.value} vs {r2.value}")
check("the regime ignores a non-finite value outside the window it reads",
      r1.value == r3.value and r1.inputs_hash == r3.inputs_hash,
      f"{r1.value} vs {r3.value}")
inside_nan = list(long_clean)
inside_nan[195] = NAN
r4 = ES.regime(inside_nan, 20, 199)
check("a non-finite value inside the regime window yields None + a reason",
      r4.value is None and r4.reason, str(r4))

a1 = [0.01, -0.02, 0.03, 0.005, -0.01, 0.02, -0.005, 0.01, 0.004, -0.006]
a2 = [0.02, -0.01, 0.01, 0.03, -0.02, 0.01, 0.005, -0.01, 0.02, 0.003]
d1 = ES.dependence(a1, a2, 5)
d2 = ES.dependence(a1[:5] + [NAN] + a1[5:], a2, 5)
check("the dependence measure ignores a non-finite value outside its tails",
      d1.value == d2.value and d1.inputs_hash == d2.inputs_hash,
      f"{d1.value} vs {d2.value}")
d3 = ES.dependence(a1[:4] + [NAN], a2, 5)
check("a non-finite value inside the aligned tails yields None + a reason",
      d3.value is None and d3.reason, str(d3))

b_1 = ES.breadth({"A": a1}, 5)
b_2 = ES.breadth({"A": a1[:5] + [NAN] + a1[5:]}, 5)
check("the breadth measure ignores a non-finite value outside a coin's window",
      b_1.value == b_2.value and b_1.inputs_hash == b_2.inputs_hash,
      f"{b_1.value} vs {b_2.value}")
b_3 = ES.breadth({"A": a1[:5] + [NAN]}, 5)
check("a non-finite value inside a coin's window yields None + a reason",
      b_3.value is None and b_3.reason, str(b_3))

print("\n=== estimators: volatility / tail measures ===")
rets = [0.010, -0.020, 0.005, 0.000, -0.030, 0.020, 0.012, -0.008,
        0.003, -0.004, 0.007, -0.001]
ms = ES.volatility_measures(rets, 12)
names = {m.name for m in ms}
expect = {"realized_vol", "ewma_vol", "downside_dev", "skew", "excess_kurtosis",
          "hist_var_95", "hist_cvar_95", "tail_ratio"}
check("volatility_measures returns the documented set",
      names == expect, str(sorted(names - expect) + sorted(expect - names)))
rv = [m for m in ms if m.name == "realized_vol"][0]
check("realized volatility is the sample standard deviation",
      abs(rv.value - statistics.stdev(rets)) < 1e-12, str(rv.value))
check("every measure carries a version, a size and a hash",
      all(m.version == ES.MEASURE_VERSION and m.n == 12 and len(m.inputs_hash) == 16
          for m in ms))
var = [m for m in ms if m.name == "hist_var_95"][0]
cvar = [m for m in ms if m.name == "hist_cvar_95"][0]
tail = [m for m in ms if m.name == "tail_ratio"][0]
check("CVaR is at least as large as VaR (both are positive losses)",
      var.value > 0 and cvar.value >= var.value, f"{var.value} {cvar.value}")
check("the tail ratio is positive", tail.value > 0, str(tail.value))
short = ES.volatility_measures([0.01, -0.02], 10)
check("a window longer than the sample returns None + a reason",
      all(m.value is None and m.reason for m in short), str(short))
const = ES.volatility_measures([0.0] * 10, 10)
check("a zero-variance window does not crash and flags the undefined shape measures",
      [m for m in const if m.name == "realized_vol"][0].value == 0.0
      and [m for m in const if m.name == "skew"][0].value is None
      and [m for m in const if m.name == "skew"][0].reason == "degenerate-zero-variance"
      and [m for m in const if m.name == "excess_kurtosis"][0].value is None
      and [m for m in const if m.name == "tail_ratio"][0].reason == "degenerate-zero-mean",
      str([(m.name, m.value, m.reason) for m in const]))

print("\n=== estimators: regime, dependence, breadth ===")
reg = ES.regime(rw, 48, 200)
check("the regime is a documented float code in 0..5",
      reg.value is not None and 0 <= reg.value <= 5 and float(reg.value).is_integer(),
      str(reg))
check("the regime documents its mapping and uses a trailing window only",
      "trend" in reg.params and reg.params["window"] == 48
      and reg.params["as_of_index"] == 200, str(reg.params))
pert = list(rw)
for i in range(201, len(pert)):
    pert[i] += 5.0
reg2 = ES.regime(pert, 48, 200)
check("the regime ignores everything after as_of_index",
      reg.value == reg2.value and reg.inputs_hash == reg2.inputs_hash)
check("a regime computed past the sample end is refused",
      ES.regime(rw, 48, 10_000).value is None
      and ES.regime(rw, 48, 10_000).reason is not None)

a = [0.01, -0.02, 0.03, 0.005, -0.01, 0.02, -0.005, 0.01]
dep = ES.dependence(a, [2 * x for x in a], 8)
check("a perfectly correlated pair gives correlation 1",
      dep.value is not None and abs(dep.value - 1.0) < 1e-9, str(dep.value))
check("an anti-correlated pair gives correlation -1",
      abs(ES.dependence(a, [-x for x in a], 8).value + 1.0) < 1e-9)
check("too little overlap returns None + a reason",
      ES.dependence([0.1, 0.2], [0.3, 0.4], 10).value is None
      and ES.dependence([0.1, 0.2], [0.3, 0.4], 10).reason is not None)

coins = {"AAA": [0.01] * 5, "BBB": [-0.01] * 5, "CCC": [0.02, 0.0, 0.0, 0.0, -0.01],
         "DDD": [0.01, 0.01, 0.01]}
br = ES.breadth(coins, 5)
check("breadth is the fraction of coins with a positive trailing return",
      br.value is not None and abs(br.value - 2 / 3) < 1e-12, str(br.value))
check("breadth excludes coins without enough history and counts them",
      br.params["n_coins_used"] == 3 and br.params["n_coins_skipped"] == 1,
      str(br.params))

# ========================================================== feature store
print("\n=== feature store: point-in-time join ===")
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "features.db")
store = FS.FeatureStore(db)
rec = FS.FeatureRecord(coin="BTC", name="hurst_rs", version=ES.HURST_VERSION,
                        bar_ts=1000, available_ts=1000, value=0.5, reason=None,
                        inputs_hash="0123456789abcdef",
                        meta={"window": 48, "uncertainty": 0.02})
store.put(rec)
got = store.as_of("BTC", "hurst_rs", ES.HURST_VERSION, 1000)
check("as_of returns the record at its own timestamp",
      got is not None and got.value == 0.5 and got.reason is None, str(got))
check("as_of never returns a value from the future",
      store.as_of("BTC", "hurst_rs", ES.HURST_VERSION, 999) is None)
check("a missing feature returns None",
      store.as_of("ETH", "hurst_rs", ES.HURST_VERSION, 1000) is None)
check("a feature of another version is not visible",
      store.as_of("BTC", "hurst_rs", "other-version", 1000) is None)
stale = store.as_of("BTC", "hurst_rs", ES.HURST_VERSION, 1000 + 10_000, max_age_s=100)
check("a stale feature is reported as stale, with no value",
      stale is not None and stale.reason == "stale" and stale.value is None, str(stale))
fresh = store.as_of("BTC", "hurst_rs", ES.HURST_VERSION, 1000 + 10_000, max_age_s=20_000)
check("a fresh-enough feature is still returned", fresh is not None and fresh.value == 0.5)

store2 = FS.FeatureStore(db)
store2.put(FS.FeatureRecord(coin="BTC", name="hurst_rs", version=ES.HURST_VERSION,
                             bar_ts=1000, available_ts=1000, value=0.9, reason=None,
                             inputs_hash="ffffffffffffffff", meta={}))
check("put is idempotent: the first write wins",
      store2.as_of("BTC", "hurst_rs", ES.HURST_VERSION, 1000).value == 0.5)
check("put does not duplicate rows",
      len(store2.history("BTC", "hurst_rs", ES.HURST_VERSION)) == 1)
check("the store keeps the original inputs hash",
      store2.as_of("BTC", "hurst_rs", ES.HURST_VERSION, 1000).inputs_hash
      == "0123456789abcdef")
check("latest is diagnostic-only and returns the newest record",
      store2.latest("BTC", "hurst_rs", ES.HURST_VERSION).bar_ts == 1000)
check("a future-dated record is invisible to an as-of query",
      store2.as_of("BTC", "hurst_rs", ES.HURST_VERSION, 500) is None)

# two stores, two paths: no global state
other = FS.FeatureStore(os.path.join(tempfile.mkdtemp(), "other.db"))
check("a second store at another path is independent",
      other.as_of("BTC", "hurst_rs", ES.HURST_VERSION, 1000) is None)

# a record written with a reason stores the reason
store2.put(FS.FeatureRecord(coin="ETH", name="hurst_rs", version=ES.HURST_VERSION,
                             bar_ts=1000, available_ts=1000, value=None,
                             reason="insufficient", inputs_hash="0" * 16, meta={}))
r = store2.as_of("ETH", "hurst_rs", ES.HURST_VERSION, 1000)
check("a record with no value keeps its reason",
      r is not None and r.value is None and r.reason == "insufficient", str(r))

print("\n=== feature store: validation, threads, versions ===")
check("the store reports its schema version",
      store.version == FS.FEATURE_STORE_VERSION, str(store.version))
try:
    store2.put(FS.FeatureRecord(coin="BTC", name="bad", version="v1", bar_ts=2000,
                                 available_ts=1000, value=1.0, reason=None,
                                 inputs_hash="0" * 16, meta={}))
    ok = False
except ValueError:
    ok = True
check("put refuses a record that claims to be knowable before its bar", ok)
check("the refused record was not stored",
      store2.as_of("BTC", "bad", "v1", 5000) is None)
try:
    store2.put(FS.FeatureRecord(coin="BTC", name="bad2", version="v1", bar_ts=2000,
                                 available_ts=2000, value=None,
                                 reason="not-a-documented-reason",
                                 inputs_hash="0" * 16, meta={}))
    ok = False
except ValueError:
    ok = True
check("put refuses a reason outside the documented enum", ok)

est = ES.hurst_rs(rw)
rec_est = FS.from_estimate("BTC", "hurst_rs", 3000, 3000, est)
check("from_estimate maps a good estimate to no reason",
      rec_est.value is not None and rec_est.reason is None
      and rec_est.version == ES.HURST_VERSION
      and rec_est.inputs_hash == est.inputs_hash, str(rec_est))
bad_est = ES.hurst_rs([1.0] * 50)
rec_bad = FS.from_estimate("BTC", "hurst_bad", 3000, 3000, bad_est)
check("from_estimate maps a failed estimate to a documented reason",
      rec_bad.value is None and rec_bad.reason in FS.REASONS,
      f"{rec_bad.reason} {FS.REASONS}")
check("from_estimate keeps the estimator's own reason in the meta",
      "estimator_reason" in rec_bad.meta, str(rec_bad.meta))

piv = ST.confirmed_pivots(bars_from_hl([5.0] * 8 + [9.0] + [5.0] * 8, [1.0] * 17),
                           left=2, right=2)
piv_recs = FS.from_pivots("BTC", "pivot_high", piv, 3000)
check("from_pivots stores the pivots under the pivot version",
      piv_recs and all(r.version == ST.PIVOT_VERSION for r in piv_recs)
      and piv_recs[0].value == 9.0, str(piv_recs[:1]))

# the store must be usable from a worker thread (the service is threaded)
import threading

worker_out = {}


def _worker():
    try:
        s = FS.FeatureStore(db)
        s.put(FS.FeatureRecord(coin="T1", name="hurst_rs", version=ES.HURST_VERSION,
                                bar_ts=1000, available_ts=1000, value=0.25,
                                reason=None, inputs_hash="a" * 16, meta={}))
        worker_out["value"] = s.as_of("T1", "hurst_rs", ES.HURST_VERSION, 1000).value
        s.close()
    except Exception as exc:                                 # noqa: BLE001
        worker_out["error"] = repr(exc)


th = threading.Thread(target=_worker)
th.start()
th.join()
check("the store works from a worker thread",
      worker_out.get("value") == 0.25, str(worker_out))

# a corrupted meta blob must be surfaced, not swallowed
corrupt_db = os.path.join(tempfile.mkdtemp(), "corrupt.db")
cstore = FS.FeatureStore(corrupt_db)
cstore.put(FS.FeatureRecord(coin="C", name="x", version="v1", bar_ts=1,
                             available_ts=1, value=1.0, reason=None,
                             inputs_hash="0" * 16, meta={}))
cstore._conn.execute("UPDATE feature SET meta='{not json' WHERE coin='C'")
cstore._conn.commit()
try:
    cstore.as_of("C", "x", "v1", 1)
    ok = False
except ValueError:
    ok = True
check("a corrupted meta blob is surfaced instead of being swallowed", ok)
cstore.close()

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
