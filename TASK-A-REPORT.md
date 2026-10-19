# Task A report — Workstream A: market measurement & structure engine

**Status: DONE_WITH_CONCERNS**

(The concerns are documentation/semantics decisions that the orchestrator may
want to overrule, plus one extra module file. No functional blocker; every
acceptance criterion is implemented and tested.)

- Worktree: `/tmp/opencode/wt-a` (branch `feature/ws-a-measurement`)
- Base commit: `95d0a56` (docs: design and progress report …)
- Implementation commit: **`8a6f9ab`** — `feat(quant): add the market measurement & structure engine (workstream A)`
- Report commit: see the last line of this file (HEAD)

---

## 1. What changed, file by file

Nothing outside the files below was touched. `git status` shows only the new
files; no existing file was modified, no archive was written, the live
service was not touched, and no `requirements.txt` was added.

| File | Lines | Status |
|---|---:|---|
| `proto/quant/structure.py` | 282 | new |
| `proto/quant/estimators.py` | 670 | new |
| `proto/quant/measures.py` | 371 | new (split out of `estimators.py`, see §5.1) |
| `proto/quant/feature_store.py` | 209 | new |
| `tests/test_ws_a_measurement.py` | 452 | new |

Imports used (standard library only): `math`, `json`, `hashlib`, `os`,
`time`, `sqlite3`, `dataclasses` (+ `proto.quant.config.BAR_S`). No
numpy/pandas/scipy anywhere; nothing was installed.

### 1.1 `proto/quant/structure.py`

```python
PIVOT_VERSION = "pivots-v1"

@dataclass(frozen=True)
class Pivot:
    index: int          # index of the pivot bar in the analysed series
    ts: int             # open timestamp of the pivot bar
    confirmed_ts: int   # when the pivot BECAME KNOWABLE = ts + (right+1)*BAR_S
    price: float
    kind: str           # "high" | "low"
    leg_bars: int       # bars in the leg that ended here (vs the previous opposite pivot)
    size: float         # |log(price / previous opposite pivot price)| (0.0 when there is none)

def confirmed_pivots(bars, left: int = 2, right: int = 2) -> list[Pivot]
def pivots_as_of(pivots, as_of_ts) -> list[Pivot]          # helper (additive)
def structure_summary(pivots) -> dict                       # helper (additive)
def resample(bars, frame_bars: int, *, as_of_ts=None) -> list[dict]
def multi_timeframe(bars, frames=(3, 12, 48), left=2, right=2, as_of_ts=None) -> dict
```

Semantics implemented (exactly as briefed):

- A pivot high at `i` needs `h[i] >= h[i-left..i-1]` (non-strict on the left)
  **and** `h[i] > h[i+1..i+right]` (strict on the right, so a plateau is
  resolved by its **last** bar); the low case is the mirror image. The test
  is **local to the window** — the global `list.count(...)` test of
  `proto/indicators.py` is not used and that file is untouched.
- `confirmed_ts = ts + (right + 1) * BAR_S` (the close of bar `i+right`).
  The last `right` bars of a series can never be a pivot.
- `leg_bars` / `size` are measured against the previous **opposite** pivot;
  with no such pivot they are `0` and `0.0` (never `None`).
- `resample` buckets bars by **wall-clock period**
  (`floor(ts / (frame_bars*BAR_S))`); a bucket becomes a frame only when it
  holds exactly `frame_bars` mutually contiguous bars. A bucket with a gap is
  **dropped, never interpolated**, and because the buckets are wall-clock
  aligned a gap drops that frame only (the other frames keep their
  boundaries). A frame is emitted only when it is **closed**
  (`last_bar_ts + BAR_S <= as_of_ts`). The frame dict uses the same keys as
  the input bars (`ts,o,h,l,c,vol,amt`), `ts` = the open timestamp of the
  frame's first bar, `h`/`l` = extremes, `vol`/`amt` = sums.
- `multi_timeframe` returns `{frame_bars: {"pivots": [...], "structure": {...}}}`
  where `structure` = `{"trend": "up"|"down"|"mixed", "last_pivot_high",
  "last_pivot_low", "swing_bars"}`, computed from the confirmed pivots only
  (`trend` = "up" when the last confirmed high is above the last confirmed
  low **and** the most recent pivot is a high; "swing_bars" = the length of
  the most recent completed leg, `None` when there is none).
- **Point-in-time**: with `as_of_ts`, only bars with `ts + BAR_S <= as_of_ts`
  are used, only closed frames are built, and only pivots with
  `confirmed_ts <= as_of_ts` are reported.
- Input: bar dicts (`ts,o,h,l,c,vol,amt`) **or** `(ts,o,h,l,c,...)` tuples.

### 1.2 `proto/quant/estimators.py`

```python
HURST_VERSION = "hurst-v1"
MFDFA_VERSION = "mfdfa-v1"
MEASURE_VERSION = "measures-v1"

@dataclass(frozen=True)
class Estimate:
    name: str
    version: str
    value: float | None
    uncertainty: float | None     # 1-sigma band, or None if not estimable
    n: int                        # sample size actually used
    params: dict                  # the exact parameters used
    inputs_hash: str              # sha256[:16] of name + version + params + series
    reason: str | None            # None when estimable, else why not

def hurst_rs(returns, min_scale=8, max_scale=None, seed=7) -> Estimate
def hurst_generalized(returns, min_scale=8, max_scale=None, seed=7) -> Estimate
def mfdfa(returns, q_min=8, q_max=64, q_range=(0.5, 1.5), seed=7, n_q=5) -> Estimate
def mfdfa_spectrum(returns, q_min=8, q_max=64, q_range=(0.5, 1.5), seed=7, n_q=5) -> dict
def level_from_returns(returns) -> list[float] | None      # helper (additive)
# re-exported from proto.quant.measures (same public surface):
def volatility_measures(returns, window) -> list[Estimate]
def regime(reference_returns, window, as_of_index) -> Estimate
def dependence(returns_a, returns_b, window) -> Estimate
def breadth(per_coin_returns, window) -> Estimate
```

**Input convention (important, see §5.2).** `hurst_rs`, `hurst_generalized`
and `mfdfa` take a **LEVEL series (a price path)**, not increments. The
contract names the argument `returns` for compatibility, but the classic
mathematics is defined on a level series, and the acceptance criteria
(white noise → 0.5, random walk → 1.0) only hold under that reading.
`level_from_returns()` performs the documented conversion (cumulate the
returns into a log-price path). The distribution/tail/regime/dependence/
breadth measures take **increments**, which is their domain.

#### `hurst_rs` — rescaled range (Mandelbrot–Wallis)

- **Construction**: for every scale `q` the series is cut into non-overlapping
  chunks of `q`; inside each chunk the cumulative deviation series is formed
  **with that chunk's own mean**, `R = max - min` of it, `S` = the sample sd
  of the chunk; the Anis–Lloyd finite-sample correction `(n-q)/(n-1.5)` is
  applied; the mean `R/S` per scale is regressed on `log q` and the **slope is
  H**. (A *global* mean inside the chunks was tried first and is wrong: it
  breaks the scaling of a non-stationary series — it returned H = 0.46 for a
  random walk instead of 1.0.)
- **Assumptions**: the input is a level series; white-noise levels give
  H ≈ 0.5, a random-walk path H ≈ 1.0. The statistic needs a bounded (or
  slowly growing) range; a strong deterministic trend biases H up, a
  mean-reverting series biases it down. The estimate is only meaningful for
  `n >> max_scale` (default `max_scale = n // 4`, geometric ladder
  `q, 2q, 4q, …`); the Anis–Lloyd correction removes most, but not all, of
  the finite-size bias (a few hundredths at these sizes).
- **Uncertainty**: the **standard error of the log-log slope** (the brief's
  wording). It covers the scatter of the R/S points around the fitted line
  only. It does **not** cover the finite-size bias, the sensitivity to the
  scale range, or the model risk of applying a stationary-process H to a real
  price. A **seeded** block bootstrap of the per-chunk R/S values is reported
  in `params["bootstrap_slope_se"]` as an independent cross-check (it does not
  change the value or the primary uncertainty).
- Degenerate inputs → `value=None` + `reason`:
  `"insufficient-sample"`, `"degenerate-zero-variance"`, `"non-finite-input"`.

#### `hurst_generalized` — generalized (aggregated-variance) H at q = 2

- **Construction**: the cumulative deviation profile of the level series is
  cut into segments of `s` bars, each segment is detrended by its own linear
  fit, the aggregated variance `F_2(s) = sqrt(mean |Y(s,t)|²)` is the RMS
  fluctuation of the detrended profile, and the OLS slope of `log F_2(s)` on
  `log s` over the scale range is **tau(2)** — the q = 2 member of the
  generalized structure function.
- **Assumptions**: a level series; for a path with stationary increments
  (a random walk) `tau(2) = H + 0.5` (1.5), for white-noise levels
  `tau(2) = 0.5`. A deterministic trend inflates the exponent (only a linear
  trend per segment is removed). The scale range and the `n >> max_scale`
  requirement are as above.
- **Uncertainty**: the standard error of the log-log slope; same scope and
  same exclusions as `hurst_rs` (no finite-size bias, no scale-range risk,
  no model risk). `params["bootstrap_slope_se"]` is the seeded cross-check.

#### `mfdfa` / `mfdfa_spectrum` — MF-DFA and the multifractal spectrum

- **Construction**: the cumulative deviation profile is cut into segments of
  `s` bars (`s` from `q_min` to `q_max`), each segment is detrended by its
  own linear fit, and the generalized structure function
  `F_q(s) = (mean |Y(s,t)|^q)^(1/q)` is formed for each moment order `q` in
  `q_range`; `tau(q)` is the log-log slope of `F_q(s)` on `s`.
  **`alpha = tau(1)`** (the DFA exponent at the first moment; for a
  monofractal `tau` is flat in `q`, so `alpha` is the same for every `q`).
  `mfdfa_spectrum` returns
  `{"alpha", "spectrum": [(h, D)], "width", "n", "reason"}` with
  `h(q) = dtau/dq` (centred differences on the q grid) and the Legendre
  transform `D(h) = q·h − tau + 1`, **normalized to a peak of D = 1** (the
  DFA profile carries one extra integration, so the absolute level of the
  transform is a normalization artefact; only the shape and the width carry
  information). `width = h_max − h_min` is the multifractality.
- **Assumptions**: a level series; white-noise levels give `alpha = 0.5`, a
  random-walk path gives `alpha = 1.5` (the standard `alpha = H + 0.5`).
  The measure assumes a self-similar process over the scale range; outside it
  the fit is meaningless. The finite-size caveat dominates: the scale range
  must stay well below `n`, and the smallest scales carry a bias of a few
  hundredths. The spectrum resolution is limited by `n_q` — a coarse q grid
  smooths the spectrum and can understate the width.
- **Uncertainty**: the standard error of the q = 1 log-log slope; same scope
  and exclusions as above. `params["bootstrap_slope_se"]` is the seeded
  cross-check.
- `mfdfa_spectrum`'s `seed` is accepted for signature symmetry only: the
  spectrum involves no sampling step and is fully deterministic.

#### `volatility_measures(returns, window)` → `list[Estimate]`

Trailing window = the last `window` observations. The eight measures:
`realized_vol` (sample sd), `ewma_vol` (λ = 0.94, seeded with the window mean
square), `downside_dev` (`sqrt(mean(min(r,0)²))`), `skew` (`m3/m2^1.5`),
`excess_kurtosis` (`m4/m2² − 3`), `hist_var_95` (the 5 % quantile, reported as
a **positive loss magnitude**), `hist_cvar_95` (the mean of the worst 5 %,
also a positive loss magnitude), `tail_ratio`
(`|mean of the worst 5 %| / |mean of the window|`).

- **Assumptions**: the window is assumed to be representative of the recent
  regime; the measures are trailing and never look forward. The tail
  measures use the empirical distribution of a finite window (with a window
  of 100 the 5 % tail is 5 observations, so `hist_cvar_95` and `tail_ratio`
  are noisy); no distributional assumption is made.
- **Uncertainty**: the delta-method standard error of the standard deviation
  (`sd / sqrt(2(n−1))`), which assumes **independent** returns; it does not
  cover autocorrelation, volatility clustering, the finite window or the tail
  estimation error. The shape and tail measures therefore report **no**
  uncertainty rather than a misleading one.

#### `regime(reference_returns, window, as_of_index)` → `Estimate`

- **Mapping (documented, fixed)**: trend sign `+1` ("up") when the
  cumulative return of the trailing window is positive, `-1` ("down")
  otherwise; volatility tercile "low" when the window volatility is at or
  below the 1/3 quantile of the trailing distribution of window volatilities,
  "high" at or above the 2/3 quantile, "mid" in between. The trailing
  distribution is built from the **preceding** windows only (the window that
  ends at `as_of_index-1` and the ones before it), so the current window
  never defines its own thresholds.
  `code = trend_index*3 + tercile_index` (trend 0/1, tercile 0/1/2) →
  `0 down/low-vol, 1 down/mid-vol, 2 down/high-vol, 3 up/low-vol,
  4 up/mid-vol, 5 up/high-vol`; the human label is in `params["label"]`.
- **Assumptions**: the reference series is a market factor (e.g. BTC log
  returns) — the label describes that factor, not a single coin. The terciles
  are relative (self-referential) thresholds: they describe the current
  regime against the recent past and are **not** comparable across a change
  of the window length or of the reference series. The label is a coarse
  human-readable bucket and carries no uncertainty (it is a deterministic
  function of the trailing window, not an estimate with a sampling error).
  With fewer than `window` observations up to `as_of_index`, or fewer than
  `MIN_CALIB` (4) preceding window volatilities, the value is `None` with a
  reason.

#### `dependence(returns_a, returns_b, window)` → `Estimate`

- **Alignment**: the two series are **right-aligned on their tails** — the
  last `n = min(len(a), len(b), window)` observations of each are paired.
  The function takes no timestamps, so the caller must pass two series that
  cover the same clock time; unequal lengths are aligned at the end (the
  shorter series is assumed to end at the same instant).
- **Minimum overlap**: `MIN_OVERLAP = 5`; below that → `None` +
  `"insufficient-sample"`.
- **Assumptions**: a linear co-movement measure over the window; it says
  nothing about causality or lead-lag. The uncertainty is the Fisher-z
  approximation `(1 − r²)/sqrt(n−1)`, valid only under bivariate normality —
  with heavy-tailed crypto returns it understates the real error. A zero
  variance on either side is reported as `"degenerate-zero-variance"` rather
  than a correlation of 0.

#### `breadth(per_coin_returns, window)` → `Estimate`

- The fraction of coins with a **positive** trailing-window return. A coin
  contributes only if it has at least `window` observations; a coin with a
  shorter history is **excluded and counted** (`params["n_coins_skipped"]`),
  never scored as "not positive". The denominator is the number of coins
  that could be classified.
- **Assumptions**: trailing only (point-in-time). The uncertainty is the
  binomial standard error `sqrt(p(1−p)/n)`, which assumes **independent**
  coins — crypto coins are strongly correlated, so the real uncertainty is
  larger; the number is a lower bound and is reported for that reason. The
  measure counts coins, it does not measure returns.

### 1.3 `proto/quant/measures.py` (new — see §5.1)

Holds the implementation of `volatility_measures`, `regime`, `dependence` and
`breadth` (the "market measures" family). They are **re-exported** by
`proto.quant.estimators` (lazy PEP-562 `__getattr__`, so there is no import
cycle in either import order), so the documented public surface is unchanged:
`proto.quant.estimators.volatility_measures` etc. A test asserts this.

### 1.4 `proto/quant/feature_store.py`

```python
FEATURE_STORE_VERSION = "features-v1"

@dataclass(frozen=True)
class FeatureRecord:
    coin: str
    name: str
    version: str
    bar_ts: int          # the bar the feature describes
    available_ts: int    # when the value became knowable (>= bar_ts)
    value: float | None
    reason: str | None   # None when present, else "missing"|"stale"|"insufficient"
    inputs_hash: str
    meta: dict

class FeatureStore:
    def __init__(self, path)                       # append-only SQLite, no global state
    def put(record) -> bool                        # INSERT OR IGNORE: the first write wins
    def as_of(coin, name, version, ts, max_age_s=None) -> FeatureRecord | None
    def latest(coin, name, version) -> FeatureRecord | None   # diagnostics only
    def history(coin, name, version) -> list[FeatureRecord]   # diagnostics only
    def count(coin=None, name=None, version=None) -> int
    def close()

def from_estimate(coin, name, bar_ts, available_ts, estimate) -> FeatureRecord
```

- The table is
  `feature(coin, name, version, bar_ts, available_ts, value, reason, inputs_hash, meta, recorded_ts)`
  with `UNIQUE(coin, name, version, bar_ts)`.
- `put` inserts and never overwrites → the first write for a key wins and
  the history of a measurement cannot be rewritten.
- `as_of` is the **point-in-time join**: the newest record with
  `available_ts <= ts`, never a future value, never an interpolated one, and
  `None` when nothing qualifies. With `max_age_s`, a record older than the
  limit is returned with the reason `"stale"` **and no value** (the caller
  must not treat it as fresh).
- `latest` is explicitly diagnostic-only and documented as such.
- The store is usable from a temp path (or `":memory:"`); there is no global
  state, and two stores on two paths are independent (tested).
- `from_estimate` builds a record from an `estimators.Estimate`
  (the estimate's reason is carried over; `"insufficient-sample"` becomes
  `"insufficient"`, a missing reason with a missing value becomes
  `"missing"`; the parameters, `n` and the uncertainty go into `meta`).

---

## 2. Tests

Test file: `tests/test_ws_a_measurement.py` — a self-checking script in the
repo house style (prints `PASS`/`FAIL`, `sys.exit(1)` on failure, ends with
`ALL PASS`), auto-discovered by `tests/run_all.py`. All fixtures are
synthetic or in `tempfile.mkdtemp()`; nothing is written to `data/`.

### 2.1 Method (RED first)

1. `tests/test_ws_a_measurement.py` was written **before** the implementation
   and run first — it failed with:

   ```
   Traceback (most recent call last):
     File "/tmp/opencode/wt-a/tests/test_ws_a_measurement.py", line 16, in <module>
       from proto.quant import structure as ST
   ImportError: cannot import name 'structure' from 'proto.quant' (/tmp/opencode/wt-a/proto/quant/__init__.py)
   ```
   (`EXIT=1`)
2. The three modules were then implemented; the focused test was re-run
   until green.

### 2.2 Focused test

```
$ cd /tmp/opencode/wt-a && python3 tests/test_ws_a_measurement.py
```
(final lines; the whole file is 120+ checks)

```
=== structure: confirmed pivots ===
  PASS  a local max is a pivot high
  PASS  pivot ts is the bar's own open timestamp
  PASS  confirmed_ts == ts + (right+1)*BAR_S
  PASS  the pivot price is the bar high
  PASS  the last `right` bars are never pivots
  PASS  a pivot is invisible before confirmed_ts
  PASS  a pivot is visible at confirmed_ts
  PASS  two equal highs in one window: only the last is a pivot
  PASS  the same values in separate windows: both are pivots
  PASS  a first pivot has leg_bars 0 and size 0.0
  PASS  a leg is measured against the previous OPPOSITE pivot
  PASS  the last leg is measured against the previous opposite pivot

=== structure: resample and multi-timeframe ===
  PASS  resample emits 60/12 frames
  PASS  a frame starts at the first bar's timestamp
  PASS  a frame closes at the last bar's close
  PASS  a frame takes the max high and the min low
  PASS  a frame sums the volume and the amount
  PASS  a frame carries the same keys as a bar
  PASS  a frame with a gap is dropped, not interpolated
  PASS  a frame is not emitted before it is closed
  PASS  multi_timeframe returns one entry per frame
  PASS  each frame carries pivots and a structure
  PASS  the structure summary has the documented keys
  PASS  the trend label is one of up/down/mixed
  PASS  the last pivot prices agree with the pivot list
  PASS  no lookahead: perturbing the future leaves the as-of structure identical
  PASS  as-of frames only use closed bars

=== estimators: public surface ===
  PASS  the market measures are reachable from proto.quant.estimators
  PASS  Estimate is a frozen dataclass
  PASS  the measure versions are exposed

=== estimators: Hurst ===
  hurst_rs white noise   H = 0.5498  (1-sigma 0.0149, n=4096)
  hurst_rs random walk   H = 0.9549  (1-sigma 0.0152, n=4096)
  PASS  Hurst on synthetic white noise is within 0.10 of 0.5
  PASS  Hurst on a synthetic random walk is within 0.15 of 1.0
  PASS  the Hurst estimate reports its sample size and parameters
  PASS  the Hurst estimate carries a version and an inputs hash
  hurst_generalized random walk  = 1.5279 (1-sigma 0.023944910581035574)
  PASS  hurst_generalized is estimable on a long series
  PASS  hurst_generalized on a random walk is above 1.0 (a trending path)

=== estimators: MF-DFA ===
  mfdfa alpha (monofractal white noise) = 0.5316 (1-sigma 0.0041, n=4096)
  spectrum width  monofractal = 0.0011   bimultifractal = 0.1459
  spectrum (monofractal) = [(-0.0012, 1.0), (-0.0014, 0.9998), (-0.0018, 0.9995), (-0.0021, 0.9991), (-0.0023, 0.9989)]
  spectrum (bimultifractal) = [(-0.0762, 1.0), (-0.1227, 0.9651), (-0.1922, 0.9072), (-0.2187, 0.8799), (-0.2221, 0.8756)]
  PASS  MF-DFA alpha is within 0.15 of 0.5 on a synthetic monofractal
  PASS  the spectrum width on a bimultifractal is materially larger than on a monofractal
  PASS  the spectrum is a list of (h, D) pairs
  PASS  the spectrum reports its sample size and a reason slot

=== estimators: degenerate inputs ===
  PASS  a constant series -> hurst_rs returns None + a reason
  PASS  a constant series -> hurst_generalized returns None + a reason
  PASS  a constant series -> mfdfa returns None + a reason
  PASS  a constant series -> mfdfa_spectrum returns None + a reason
  PASS  a 3-point series -> hurst_rs returns None + a reason
  PASS  a 3-point series -> hurst_generalized returns None + a reason
  PASS  a 3-point series -> mfdfa returns None + a reason
  PASS  a 3-point series -> mfdfa_spectrum returns None + a reason
  PASS  an all-NaN series -> hurst_rs returns None + a reason
  PASS  an all-NaN series -> hurst_generalized returns None + a reason
  PASS  an all-NaN series -> mfdfa returns None + a reason
  PASS  an all-NaN series -> mfdfa_spectrum returns None + a reason
  PASS  an empty series -> hurst_rs returns None + a reason
  PASS  an empty series -> hurst_generalized returns None + a reason
  PASS  an empty series -> mfdfa returns None + a reason
  PASS  an empty series -> mfdfa_spectrum returns None + a reason

=== estimators: determinism and versioning ===
  PASS  the same input and seed give the same value and hash
  PASS  the same input and seed give the same MF-DFA value and hash
  PASS  the inputs hash changes when a parameter changes
  PASS  the inputs hash changes when the series changes
  PASS  a different version yields a different hash
  PASS  the dependence hash covers BOTH series

=== estimators: trailing only, no lookahead ===
  PASS  a global estimator only uses the series it was given
  PASS  a trailing measure ignores everything before its window
  PASS  the dependence measure ignores everything before its window
  PASS  the breadth measure ignores everything before its window

=== estimators: volatility / tail measures ===
  PASS  volatility_measures returns the documented set
  PASS  realized volatility is the sample standard deviation
  PASS  every measure carries a version, a size and a hash
  PASS  CVaR is at least as large as VaR (both are positive losses)
  PASS  the tail ratio is positive
  PASS  a window longer than the sample returns None + a reason
  PASS  a zero-variance window does not crash and flags the undefined shape measures

=== estimators: regime, dependence, breadth ===
  PASS  the regime is a documented float code in 0..5
  PASS  the regime documents its mapping and uses a trailing window only
  PASS  the regime ignores everything after as_of_index
  PASS  a regime computed past the sample end is refused
  PASS  a perfectly correlated pair gives correlation 1
  PASS  an anti-correlated pair gives correlation -1
  PASS  too little overlap returns None + a reason
  PASS  breadth is the fraction of coins with a positive trailing return
  PASS  breadth excludes coins without enough history and counts them

=== feature store: point-in-time join ===
  PASS  as_of returns the record at its own timestamp
  PASS  as_of never returns a value from the future
  PASS  a missing feature returns None
  PASS  a feature of another version is not visible
  PASS  a stale feature is reported as stale, with no value
  PASS  a fresh-enough feature is still returned
  PASS  put is idempotent: the first write wins
  PASS  put does not duplicate rows
  PASS  the store keeps the original inputs hash
  PASS  latest is diagnostic-only and returns the newest record
  PASS  a future-dated record is invisible to an as-of query
  PASS  a second store at another path is independent
  PASS  a record with no value keeps its reason

ALL PASS
```
`EXIT=0` (runtime ≈ 12 s).

### 2.3 The other focused tests (unchanged modules)

```
$ cd /tmp/opencode/wt-a && python3 tests/test_quant.py
...
ALL PASS
EXIT=0

$ cd /tmp/opencode/wt-a && python3 tests/test_quant_engine.py
...
ALL PASS
EXIT=0
```

### 2.4 Full offline suite

```
$ cd /tmp/opencode/wt-a && python3 tests/run_all.py
PASS  test_adapter.py  [ALL PASS]
... (82 files) ...
PASS  test_ws_a_measurement.py  [ALL PASS]

ALL SUITES PASS (82 files)
```
`EXIT=0` (runtime ≈ 7 min).

### 2.5 Read-only sanity check against the real archive (no writes)

`data/bars` was only **read**. 579 coins; the bars are on a clean 5-minute
grid (`off-grid = 0`). Example (`0G`, 28 431 bars, 98.7 days, no gaps):

```
   frame=  3 frames=  9477 pivots= 2564 trend=up    lastH=0.2271 lastL=0.2244 swing=2
   frame= 12 frames=  2369 pivots=  636 trend=up    lastH=0.2313 lastL=0.2249 swing=2
   frame= 48 frames=   592 pivots=  146 trend=up    lastH=0.2323 lastL=0.218  swing=2
   as-of pivots: {3: 2564, 12: 636, 48: 146}
```

Estimator timings on that real series (28 430 log returns):
`hurst_rs` 3.7 s (H = 0.5402 ± 0.0084), `hurst_generalized` 0.9 s
(tau2 = 0.4935), `mfdfa` 8.5 s (alpha = 0.5092), `mfdfa_spectrum` 7.6 s
(width = 0.0005), `volatility_measures` 0.03 s, `regime` 0.04 s
(code 5, "up/high-vol"), `dependence` 0.04 s.
A lag-1 autocorrelation of the same returns is **−0.0225 ± 0.059** and the
self-correlation is exactly 1.0 — the alignment and the correlation are
correct.

---

## 3. Numerical evidence (Hurst and MF-DFA)

Seeds: synthetic series `seed = 20261019`, estimator `seed = 7`, `n = 4096`.
The series are: white noise `N(0,1)`; a random walk (cumulative sum of
`N(0,1)`); a synthetic **bimultifractal** = alternating trending
(random-walk) and mean-reverting (white-noise) blocks of 64 samples, i.e.
two distinct local exponents.

| series | `hurst_rs` H | `hurst_generalized` tau(2) | `mfdfa` alpha | spectrum width |
|---|---|---|---|---|
| white noise (monofractal) | **0.5498 ± 0.0149** | 0.5631 ± 0.0233 | **0.5316 ± 0.0041** | **0.0011** |
| random walk | **0.9549 ± 0.0152** | 1.5279 ± 0.0239 | 1.5306 ± 0.0093 | 0.0022 |
| bimultifractal (blocks) | 0.8578 ± 0.0655 | 1.4064 ± 0.1555 | 1.7814 ± 0.0721 | **0.1459** |

Tolerances as required by the brief: white noise within **0.10** of 0.5
(actual deviation **0.0498**), random walk within **0.15** of 1.0 (actual
deviation **0.0451**), MF-DFA alpha within **0.15** of 0.5 (actual deviation
**0.0316**), and the bimultifractal spectrum width **0.1459** vs the
monofractal **0.0011** (≈ 130× wider; the test asserts
`width_bi > width_mono + 0.05` and `width_bi > 5 * width_mono`).

**Seed robustness** (the same estimators, six different series seeds — the
result must not depend on a lucky seed):

```
  seed=        1  H(white noise)=0.5263  H(random walk)=0.9555  alpha(white noise)=0.4916  width(bimultifractal)=0.1773  width(monofractal)=0.0010
  seed=        7  H(white noise)=0.5453  H(random walk)=0.9259  alpha(white noise)=0.5218  width(bimultifractal)=0.1138  width(monofractal)=0.0015
  seed=       42  H(white noise)=0.5316  H(random walk)=0.9637  alpha(white noise)=0.5247  width(bimultifractal)=0.0969  width(monofractal)=0.0005
  seed=      999  H(white noise)=0.5100  H(random walk)=0.9657  alpha(white noise)=0.5334  width(bimultifractal)=0.0782  width(monofractal)=0.0022
  seed= 20261019  H(white noise)=0.5498  H(random walk)=0.9549  alpha(white noise)=0.5316  width(bimultifractal)=0.1459  width(monofractal)=0.0011
  seed=    12345  H(white noise)=0.5567  H(random walk)=0.9787  alpha(white noise)=0.5013  width(bimultifractal)=0.2115  width(monofractal)=0.0013
```

Worst case over the six seeds: white noise |dev| = 0.0567 (limit 0.10),
random walk |dev| = 0.0741 (limit 0.15), alpha |dev| = 0.0334 (limit 0.15),
and the bimultifractal width stays 0.078–0.212 against a monofractal
0.0005–0.0022 (a 40–400× separation in every seed).

**Method note.** The R/S estimator needed the **chunk-local** cumulative
deviation (each chunk centred on its own mean) and the **Anis–Lloyd**
finite-sample correction `(n−q)/(n−1.5)`. With a global mean the random-walk
estimate came out at 0.46 (wrong); without the correction the white-noise
estimate came out at 0.60 (a 0.10 deviation, i.e. at the tolerance limit).
With both corrections the numbers above are comfortably inside the
tolerances.

---

## 4. Acceptance criteria — where each one is covered

| # | Criterion | Where |
|---|---|---|
| 1 | Pivot confirmation timing (`confirmed_ts == ts + 3*BAR_S` for `right=2`; invisible before; the last `right` bars produce nothing) | test "structure: confirmed pivots" (7 checks) |
| 2 | Local uniqueness (equal highs in one window → one pivot; in separate windows → two) | test "structure: confirmed pivots" (2 checks) |
| 3 | No lookahead (a perturbed future leaves the as-of structure / estimates bit-identical) | test "structure: resample and multi-timeframe" (2 checks) + "estimators: trailing only, no lookahead" (4 checks) + "estimators: regime…" (1 check) |
| 4 | Hurst: white noise within 0.10 of 0.5, random walk within 0.15 of 1.0, fixed seed, tolerance stated | test "estimators: Hurst" (2 checks) |
| 5 | MF-DFA alpha within 0.15 of 0.5 on a monofractal; the bimultifractal spectrum width materially larger; both printed | test "estimators: MF-DFA" (2 checks, 4 printed lines) |
| 6 | Degenerate inputs (constant / 3-point / all-NaN / empty) → `None` + a reason, no exception | test "estimators: degenerate inputs" (16 checks) |
| 7 | As-of join (a future `available_ts` is invisible, a stale feature is `"stale"`, a missing feature is `None`, never a future value) | test "feature store: point-in-time join" (6 checks) |
| 8 | Store idempotence (the first write wins, no duplicate rows) | test "feature store: point-in-time join" (3 checks) |
| 9 | Determinism (byte-identical `inputs_hash` and value across runs) | test "estimators: determinism and versioning" (5 checks) |

Additional coverage: the `Estimate`/`FeatureRecord` dataclasses are frozen;
the public surface (`proto.quant.estimators.<measure>`) is intact; the
`inputs_hash` changes with the data, the parameters and the version; the
`inputs_hash` of `dependence` covers **both** series.

---

## 5. Concerns and deviations

### 5.1 One extra file: `proto/quant/measures.py` (deviation from the file list)

`estimators.py` reached 988 lines, over the brief's "~700 lines is a signal
to split it" guidance, because the brief requires a long assumptions
docstring per estimator. I split the module **by topic**: the Hurst / MF-DFA
family stays in `proto/quant/estimators.py` (670 lines) and the market
measures (`volatility_measures`, `regime`, `dependence`, `breadth`) moved to
`proto/quant/measures.py` (371 lines). The **public surface is unchanged**:
`proto.quant.estimators.volatility_measures` etc. still resolve (lazy
PEP-562 `__getattr__` → no import cycle in either import order), and a test
asserts it. If the orchestrator prefers a single file, the two modules can
be concatenated with no other change. `proto/quant/measures.py` conflicts
with no other workstream's file list (B owns `outcomes/inference/family_test`).

### 5.2 The Hurst/MF-DFA input is a LEVEL series, not increments (semantics)

The contract names the argument `returns`, but acceptance criterion 4
("white noise → 0.5, random walk → 1.0") is only satisfiable if the
estimator treats its input as a **level series**: the classic R/S of a
returns series gives 0.5 for *any* martingale, and the classic R/S of a
price path gives 1.0. I implemented the classic reading and documented it
loudly in the module docstring, in every estimator docstring, and added
`level_from_returns()` as the explicit conversion. **If the orchestrator
intends "feed me the bar returns and get the persistence of the price", it
must pass `level_from_returns(returns)`** — the alternative (integrating the
increments inside the estimator) would make criterion 4 unachievable.

### 5.3 The multifractal spectrum is a DFA-based Legendre transform

`mfdfa_spectrum` derives `h(q) = dtau/dq` and `D(h) = q·h − tau + 1` from the
**DFA** structure-function curve (the same curve `alpha = tau(1)` comes
from), not from the price's own Parisi–Chhabra structure function. In the
DFA normalization the curve is flat in `q` for a monofractal, so the `h`
values are small and their absolute level is a normalization artefact; the
spectrum is therefore reported with its **peak at D = 1** and only the shape
and the **width** are documented as comparable. The width ordering required
by criterion 5 is robust (40–400× across seeds).

### 5.4 `resample` frames are wall-clock aligned

The brief does not say whether the frames are cut from the bar sequence or
aligned to the clock. I chose **wall-clock alignment** (`floor(ts/(frame_bars*BAR_S))`)
so that a "1 h frame" is the hour it belongs to and a gap drops only the
frame it breaks instead of shifting every later boundary. Consequence: a
series that starts mid-period loses its first (incomplete) frame. The real
archive is on a clean 5-minute grid, so this only affects the first frame of
a coin.

### 5.5 Small API additions (all backwards compatible)

- `resample(bars, frame_bars, *, as_of_ts=None)` — the as-of parameter is
  **keyword-only** with a default, so `resample(bars, 12)` still works; the
  brief's semantics require it.
- `mfdfa(..., n_q=5)` and `mfdfa_spectrum(..., n_q=5)` — the number of moment
  orders on the q grid (default 5: 0.5, 0.75, 1.0, 1.25, 1.5).
- Additive helpers: `structure.pivots_as_of`, `structure.structure_summary`,
  `estimators.level_from_returns`, `feature_store.from_estimate`,
  `FeatureStore.history/count/close`.
- `resample` returns the frame dict with **exactly** the input bar keys
  (`ts,o,h,l,c,vol,amt`) — no extra key was added, so the frame's close
  timestamp is `ts + frame_bars*BAR_S` for a complete frame.

### 5.6 Uncertainties are deliberately narrow, and some are `None`

The reported 1-sigma bands are the standard errors of the relevant
regression slopes (or the delta-method error of a standard deviation) and
explicitly do **not** cover the finite-size bias, the scale-range
sensitivity, autocorrelation, cross-sectional correlation, or tail
estimation error. The skewness, kurtosis, VaR, CVaR and tail-ratio measures
report `uncertainty=None` rather than a normal-theory band that would be
misleading for fat-tailed crypto returns.

### 5.7 Performance (for the orchestrator's planning)

`mfdfa` / `mfdfa_spectrum` are `O(n_scales × n × n_orders)` and take ~8 s on
a 28 000-point series (and ~1 s at n = 4096); `hurst_rs` ~3.7 s at
n = 28 000. For a per-coin sweep over 579 coins the caller should window or
subsample the series (e.g. the trailing N bars) — the estimators have no
internal windowing.

### 5.8 Non-issues worth recording

- The seeded bootstrap in `params["bootstrap_slope_se"]` resamples the
  per-chunk R/S values (Hurst) or the scale-ladder points (DFA). The scale
  points of one ladder are not independent, so that band is **optimistic**;
  it is a cross-check, never the primary uncertainty.
- The PRNG is a self-contained xorshift64\* (not `random`), so a seed gives
  the same numbers on every machine and Python build.
- `task-A-brief.md` (the orchestrator's file) is left untracked in the
  worktree; it is not part of the commit.

---

## 6. Commits

| Hash | Message |
|---|---|
| `8a6f9ab` | `feat(quant): add the market measurement & structure engine (workstream A)` |
| _(HEAD)_ | `docs(quant): task A report (workstream A: measurement & structure engine)` |

Branch: `feature/ws-a-measurement` (worktree `/tmp/opencode/wt-a`).
**Not pushed**, no other branch checked out, no existing file modified.
