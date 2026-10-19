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

---

# FIX ROUND 1 (reviewer findings)

**Status: DONE** — every finding addressed; no finding was left unaddressed.

- Branch: `feature/ws-a-measurement` (worktree `/tmp/opencode/wt-a`), not pushed.
- The fix commit is the HEAD of the branch (see §7).
- File map after the fixes (all under the ~700-line guidance):

  | File | Lines | Note |
  |---|---:|---|
  | `proto/quant/structure.py` | 303 | pivots, resample, multi_timeframe |
  | `proto/quant/estimators.py` | 508 | the `Estimate` core + the Hurst family + the re-exports |
  | `proto/quant/mfdfa.py` | 325 | **new** — the MF-DFA family (split out, see F13) |
  | `proto/quant/measures.py` | 413 | the market measures |
  | `proto/quant/feature_store.py` | 275 | the point-in-time store |
  | `tests/test_ws_a_measurement.py` | 680 | 134 checks (was 120) |

## 1. Critical

### F1 — a non-finite value outside the measured window changed the as-of estimate

**Cause.** `_clean` rejected the WHOLE series on the first NaN/inf and was
applied *before* windowing, so a value after the as-of instant poisoned the
estimate (and the append-only store could never repair it).

**Fix — clean only the slice that is actually read** (the slices are the ones
the reviewer listed):

| measure | slice that is read and cleaned | code |
|---|---|---|
| `volatility_measures` | the last `window` values | `measures.py:104` (`_clean(raw[-W:])`) |
| `regime` | the window that ends at `as_of_index` **plus** the `W` calibration windows before it (`raw[start:idx+1]`, `start = max(0, idx-2W+1)`) | `measures.py:243` |
| `dependence` | the aligned tails of the two series | `measures.py:321` |
| `breadth` | each coin's last `window` values | `measures.py:382` |

A non-finite value **inside** the slice yields `None` + `"non-finite-input"`
(the breadth measure fails as a whole and counts the offending coin in
`params["n_coins_corrupt"]` — a corrupted window is never scored as "not
positive"). A non-finite value outside the slice cannot change the value
*or* the `inputs_hash`.

**Tests (new, section "estimators: only the measured slice is cleaned")**

```
  PASS  a non-finite value before the measured window does not change the estimate
  PASS  a non-finite value inside the measured window yields None + a reason
  PASS  a non-finite value inside the measured window yields None + a reason
  PASS  the regime ignores a non-finite value after as_of_index
  PASS  the regime ignores a non-finite value outside the window it reads
  PASS  a non-finite value inside the regime window yields None + a reason
  PASS  the dependence measure ignores a non-finite value outside its tails
  PASS  a non-finite value inside the aligned tails yields None + a reason
  PASS  the breadth measure ignores a non-finite value outside a coin's window
  PASS  a non-finite value inside a coin's window yields None + a reason
```

The reviewer's own reproductions now behave correctly (measured on this
tree):

```
volatility_measures(clean, 10)      -> 0.00937985646250592
  with a NaN BEFORE the window        -> 0.00937985646250592   (identical)
  with a NaN INSIDE the window        -> None ("non-finite-input")
regime(base, 20, 50)                 -> 1.0
  with +[nan]                         -> 1.0                   (identical)
breadth({"A": base}, 20)            -> 0.0
  with +[nan] (inside the window)     -> None ("non-finite-input")
```

## 2. Important

### F2 — `Pivot.confirmed_ts` was wrong for non-base timeframes

**Fix.** `confirmed_pivots` takes a keyword-only `frame_bars` (the number of
base 5 m bars one analysed bar spans) and the confirmation is the close of
the last confirming FRAME bar:

```
structure.py:105   def confirmed_pivots(bars, left=2, right=2, *, frame_bars=1)
structure.py:169   confirmed_ts = ts[i] + (right + 1) * frame_bars * BAR_S
structure.py:216   multi_timeframe passes frame_bars=fb
```

The base (5 m) series keeps the briefed timing (`ts + 3*BAR_S` for
`right=2`); a 1 h frame now confirms at `ts + 3*12*BAR_S` — the close of the
last confirming hourly bar, i.e. 45 minutes LATER than the buggy value
(which claimed knowability before the frame had even closed).

**Tests (new)**

```
  PASS  a frame pivot is confirmed when the last confirming FRAME bar closes
  PASS  a frame pivot is not knowable before its frame closes
  PASS  the base (5 m) frame keeps the documented confirmation timing
```

The test fixture is a genuine hourly frame series (bars 3600 s apart), so
the assertion is `confirmed_ts == frame[last confirming frame bar].ts +
12*BAR_S` — it FAILS against the old code (which returned `ts + 3*BAR_S`).
Verified on the real archive (read-only): every pivot of every frame now
satisfies `confirmed_ts == ts + 3*frame_bars*BAR_S` and
`confirmed_ts <= as_of_ts`.

### F3 — `hurst_generalized` returned no Hurst exponent

**Fix.** It now implements the classical aggregated-variance estimator: for
every block size `q` the series is cut into NON-OVERLAPPING blocks of `q`
points, the block SUMS are formed, the variance `V(q)` of those sums is
regressed on `log q`, and **H = slope / 2** (`estimators.py:364`,
`_bootstrap_agg` at `estimators.py:425`).

* A random walk now gives **H = 0.9843** (was 1.5279 — the DFA tau, the same
  statistic `mfdfa` reports).
* White noise gives **H = 0.5559**.
* The value is a Hurst exponent in [0, 1] and the docstring states that it
  is NOT the DFA structure exponent (which is H + 0.5 for a path with
  stationary increments) and that the two must never be mixed up.
* The scale range is capped so that every scale keeps at least
  `MIN_BLOCKS = 16` non-overlapping blocks (with fewer blocks the variance
  estimate is dominated by its own sampling error). The cap is reported in
  `params["max_scale_used"]`.
* The uncertainty is the slope standard error divided by two; the
  assumptions, the `None`+reason behaviour and the determinism are kept.
  The docstring now states explicitly that the band does NOT cover the
  block-sampling error (the observed seed-to-seed spread on white noise is
  about ±0.1).

`mfdfa` keeps the DFA/tau statistic and is now reported under a name that
cannot be misread: `Estimate.name == "dfa_tau"` (`mfdfa.py:195`) with
`params["value_def"] = "dfa_tau = tau(1), the DFA structure exponent (NOT a
Hurst exponent H)"`.

**Tests (new)**

```
  PASS  hurst_generalized is a HURST exponent: a random walk is near 1.0
  PASS  hurst_generalized is a HURST exponent: white noise is near 0.5
  PASS  hurst_generalized differs from the DFA structure exponent
  PASS  hurst_generalized stays inside the Hurst range [0, 1]
  PASS  the DFA statistic is reported under a name that is not a Hurst exponent
```

### F4 — the parameter was named `returns` but takes a level path

**Fix.** Renamed to `levels` in `hurst_rs`, `hurst_generalized`, `mfdfa` and
`mfdfa_spectrum` (per the orchestrator's ruling). The module docstring, the
per-function docstrings and `level_from_returns()` (the explicit bridge)
say so; the distribution/tail/regime/dependence/breadth measures keep
`returns` because increments ARE their domain.

**Test (new)**

```
  PASS  the Hurst / MF-DFA estimators take a `levels` argument
```

### F5 — the feature store was single-thread-only and off house style

**Fix** (`feature_store.py:141-142`), matching `proto/quant/qstore.py:63-65`
and `proto/store.py:383-384`:

```python
self._conn = sqlite3.connect(path, timeout=5, check_same_thread=False)
self._conn.execute("PRAGMA journal_mode=WAL")
```

**Test (new)** — the store is written and read from a worker thread:

```
  PASS  the store works from a worker thread
```

## 3. Minor findings

| # | Fix | Test |
|---|---|---|
| **F6** | `_interp` no longer clamps: an `x0` outside the fitted range returns `None` (`mfdfa.py:86`, docstring corrected). `mfdfa` then returns `None` + `"alpha-outside-q-range"` instead of an extrapolated number. | `PASS  an alpha outside the fitted q range is not extrapolated` |
| **F7** | `mfdfa_spectrum` now returns a `convention` dict stating, key by key, that `h` is the local slope of the DFA curve (**not** a Hurst exponent, **not** the price's Hölder exponent), that `D` is the Legendre transform rescaled to a peak of 1 (**not** a fractal dimension), that only the `width` is comparable across series, and that `alpha` is the DFA exponent (not H). It also returns `q_grid` and `tau`. | `PASS  the spectrum states its convention (h and D are not Hurst/dimension)`, `PASS  the spectrum exposes the moment-order grid it used` |
| **F8** | The moment-order range is validated: a non-positive or inverted `q_range` returns `None` + `"invalid-q-range"` instead of raising `ZeroDivisionError` (`mfdfa.py:131` `_orders`). | `PASS  an invalid q range is refused with a reason`, `PASS  an invalid q range never raises` |
| **F9** | `_vol`/`_amt` accept a 5-tuple (volume/amount default to 0.0) instead of indexing 5/6 unconditionally (`structure.py:91-99`); the docstring now says `(ts, o, h, l, c[, vol[, amt]])`. | `PASS  a 5-tuple bar is accepted and defaults the volume and the amount` |
| **F10** | `put` refuses a record with `available_ts < bar_ts` (ValueError, nothing is written) and a reason outside `REASONS` (`feature_store.py:173-180`). | `PASS  put refuses a record that claims to be knowable before its bar`, `PASS  the refused record was not stored`, `PASS  put refuses a reason outside the documented enum` |
| **F11** | A corrupt `meta` blob is surfaced (ValueError) instead of being silently swallowed (`feature_store.py:196-205`). | `PASS  a corrupted meta blob is surfaced instead of being swallowed` |
| **F12** | `from_estimate` maps the estimate's reason onto the documented enum (`missing` / `stale` / `insufficient`) and keeps the estimator's own reason in `meta["estimator_reason"]`; `REASONS` is now enforced by `put`. | `PASS  from_estimate maps a good estimate to no reason`, `PASS  from_estimate maps a failed estimate to a documented reason`, `PASS  from_estimate keeps the estimator's own reason in the meta` |
| **F13** | The version constants are now USED: `FeatureStore.version` / a `store_meta` row carry `FEATURE_STORE_VERSION`; `from_pivots()` stores the pivots under `PIVOT_VERSION`; `REASONS` validates the stored reason. `__all__` was added to `estimators.py`, `mfdfa.py` and `feature_store.py`, so `from proto.quant.estimators import *` now exposes the measures and the MF-DFA family. | `PASS  the store reports its schema version`, `PASS  from_pivots stores the pivots under the pivot version`, `PASS  \`import *\` exposes the market measures too` |
| **F14** | The dead no-op loop in the test is gone and the no-lookahead check now compares the **pivots** of both frames, not only frame 3's structure. | `PASS  no lookahead: perturbing the future leaves the as-of structure AND pivots identical` |
| **F15** | The "previous opposite pivot" must be strictly earlier: a bar that is both a high and a low pivot no longer counts as its own predecessor, so `leg_bars` and `size` stay consistent (`structure.py:159`). | `PASS  a bar can be a high and a low pivot at once`, `PASS  the previous opposite pivot is strictly earlier (the same bar is skipped)` |

### Module split (F13 follow-up)

`estimators.py` had grown to 804 lines with the F3/F7 rewrites — over the
brief's "~700 lines is a signal to split it" guidance. The MF-DFA family
moved to a new **`proto/quant/mfdfa.py`** (325 lines) and the Hurst family +
the core stayed in `estimators.py` (508 lines). The public surface is
unchanged: `proto.quant.estimators.mfdfa` / `.mfdfa_spectrum` /
`.SPECTRUM_CONVENTION` resolve through the same lazy re-export used for the
market measures, and a test asserts `from proto.quant.estimators import *`
exposes them.

## 4. Orchestrator rulings (recorded, no change)

* **The Pivot field name stays `leg_bars`.** The plan's §1.2 said
  `bars_of_leg`; the orchestrator is aligning the plan to the shipped name.
  The divergence is recorded here and in §5.2 of the original report.
* **`hurst_rs` / `hurst_generalized` / `mfdfa` keep their function names,
  but the parameter is now `levels`** (F4).

## 5. Numerical evidence after the fixes

Seeds: synthetic series `seed = 20261019`, estimator `seed = 7`, `n = 4096`.

| series | `hurst_rs` H | `hurst_generalized` H (aggregated variance) | `mfdfa` dfa_tau | spectrum width |
|---|---|---|---|---|
| white noise (monofractal) | **0.5498 ± 0.0149** | **0.5559 ± 0.0113** | **0.5316 ± 0.0041** | **0.0011** |
| random walk | **0.9549 ± 0.0152** | **0.9843 ± 0.0042** | 1.5306 ± 0.0093 | 0.0022 |
| bimultifractal (blocks) | 0.8578 ± 0.0655 | 0.7452 ± 0.0843 | 1.7814 ± 0.0721 | **0.1459** |

Tolerances: white noise within **0.10** of 0.5 (dev 0.0498), random walk
within **0.15** of 1.0 (dev 0.0451), dfa_tau within **0.15** of 0.5 (dev
0.0316), the aggregated variance within **0.15** of 0.5 / 1.0 (dev 0.0559 /
0.0157). The bimultifractal spectrum width stays 0.1459 vs 0.0011 (≈130×).

**Seed robustness** (six series seeds; the result must not depend on a lucky
seed):

```
  seed=        1  H_rs(noise)=0.5263  H_rs(walk)=0.9555  H_agg(noise)=0.5132  H_agg(walk)=0.9826  dfa_tau(noise)=0.4916  width(bi)=0.1773  width(mono)=0.0010
  seed=        7  H_rs(noise)=0.5453  H_rs(walk)=0.9259  H_agg(noise)=0.4525  H_agg(walk)=0.9633  dfa_tau(noise)=0.5218  width(bi)=0.1138  width(mono)=0.0015
  seed=       42  H_rs(noise)=0.5316  H_rs(walk)=0.9637  H_agg(noise)=0.5134  H_agg(walk)=0.9942  dfa_tau(noise)=0.5247  width(bi)=0.0969  width(mono)=0.0005
  seed=      999  H_rs(noise)=0.5100  H_rs(walk)=0.9657  H_agg(noise)=0.5322  H_agg(walk)=1.0023  dfa_tau(noise)=0.5334  width(bi)=0.0782  width(mono)=0.0022
  seed= 20261019  H_rs(noise)=0.5498  H_rs(walk)=0.9549  H_agg(noise)=0.5559  H_agg(walk)=0.9843  dfa_tau(noise)=0.5316  width(bi)=0.1459  width(mono)=0.0011
  seed=    12345  H_rs(noise)=0.5567  H_rs(walk)=0.9787  H_agg(noise)=0.5970  H_agg(walk)=1.0045  dfa_tau(noise)=0.5013  width(bi)=0.2115  width(mono)=0.0013
```

Worst case over the six seeds: `hurst_rs` white noise |dev| 0.0567 (limit
0.10), random walk |dev| 0.0741 (limit 0.15); `hurst_generalized` white
noise |dev| 0.0970 and random walk |dev| 0.0367 (limit 0.15 — the
aggregated variance is intrinsically noisier on a noisy series, which the
docstring now states); `dfa_tau` |dev| 0.0334 (limit 0.15); the spectrum
width stays 0.078–0.212 against a monofractal 0.0005–0.0022 (40–400×).

**Real archive (read-only, no writes).** `0G`, 28 431 bars: 2 566 / 637 /
146 pivots on the 15 m / 1 h / 4 h frames; every pivot satisfies
`confirmed_ts == ts + 3*frame_bars*BAR_S` and `confirmed_ts <= as_of_ts`.
The 1 h frame's trend label changed from "up" to "down" after the F2 fix —
the old label was computed from pivots that were not yet knowable, i.e. it
was a look-ahead; this is exactly the defect F2 describes.

## 6. Test commands and their real output

```
$ cd /tmp/opencode/wt-a && python3 tests/test_ws_a_measurement.py
...
ALL PASS
$ echo $?
0
```
(134 PASS / 0 FAIL; the full output is reproduced in the fix commit message
summary and the sections above.)

```
$ cd /tmp/opencode/wt-a && python3 tests/test_quant.py
...
ALL PASS
$ echo $?
0

$ cd /tmp/opencode/wt-a && python3 tests/test_quant_engine.py
...
ALL PASS
$ echo $?
0
```

```
$ cd /tmp/opencode/wt-a && python3 tests/run_all.py
PASS  test_adapter.py  [ALL PASS]
... (82 files) ...
PASS  test_ws_a_measurement.py  [ALL PASS]

ALL SUITES PASS (82 files)
$ echo $?
0
```

## 7. Commits

| Hash | Message |
|---|---|
| `8a6f9ab` | feat(quant): add the market measurement & structure engine (workstream A) — round 0 |
| `66a4a02` | docs(quant): task A report — round 0 |
| _(HEAD)_ | fix(quant): task A review findings (F1-F15) + report |

The fix commit's hash is the branch HEAD; it is printed in the handover
message (writing it into this file would change the file and therefore the
hash).

---

# FIX ROUND 2 (scoped re-review findings)

**Status: DONE** — every finding addressed; nothing left unaddressed.

- Branch: `feature/ws-a-measurement` (worktree `/tmp/opencode/wt-a`), not pushed.
- The fix commit is the branch HEAD (see §10).
- Sizes after the fixes: `structure.py` 312, `estimators.py` 526,
  `mfdfa.py` 338, `measures.py` 413, `feature_store.py` 291 lines
  (all under the ~700-line guidance); the test file is 742 lines /
  146 checks.

## 1. F15 (was PARTIAL) — the "previous opposite pivot" must be the last *strictly earlier* one

**Defect.** The round-1 fix *discarded* the opposite pivot when it sat on the
same bar instead of falling back to the last strictly earlier one.

**Fix** (`structure.py:155-180`): the leg/size loop now keeps, per pivot kind,
the last pivot (`last`) **and** the one before it (`second`).  When the
opposite pivot is on the same bar (a bar that is both a high and a low
pivot), the computation falls back to `second[opp_kind]` — the last
STRICTLY EARLIER opposite pivot:

```python
opp_kind = "low" if kind == "high" else "high"
opp = last[opp_kind]
if opp is not None and opp.index >= i:
    # the same bar is not a PREVIOUS pivot: fall back to the last
    # STRICTLY EARLIER opposite pivot
    opp = second[opp_kind]
```

(The pivots of one kind come at strictly increasing indices — a bar yields
at most one high and one low — so `second` always holds the previous,
strictly earlier, pivot of that kind.)

**Verification of the reviewer's counter-example** (highs
`[10,12,14,12,13,11]`, lows `[5,3,4,5,2,3]`, left/right = 1):

```
  pivot idx=1 kind=low  price= 3.0 leg_bars=0 size=0.0000 confirmed_ts=1700000900
  pivot idx=2 kind=high price=14.0 leg_bars=1 size=1.5404 confirmed_ts=1700001200
  pivot idx=4 kind=high price=13.0 leg_bars=3 size=1.4663 confirmed_ts=1700001800
  pivot idx=4 kind=low  price= 2.0 leg_bars=2 size=1.9459 confirmed_ts=1700001800
```

The pivot at index 4 (both a high and a low) now gets `leg_bars=2` and
`size=1.9459` against the last strictly earlier opposite pivot (index 2) —
the reviewer's expected values.  The "no earlier opposite at all" case is
still `leg_bars=0, size=0.0`.

**Tests (new)**

```
  PASS  a bar that is both a high and a low pivot is found
  PASS  the leg uses the last STRICTLY EARLIER opposite pivot
```

(the pre-existing check "the previous opposite pivot is strictly earlier
(the same bar is skipped)" still covers the no-earlier-opposite case).

## 2. N1 (new Important) — `from_pivots` could host a look-ahead

**Defect.** `from_pivots` took a caller-supplied `available_ts` and never
checked it against `Pivot.confirmed_ts`, so `from_pivots(pivots,
available_ts=pivot.ts)` was accepted and the store then served that pivot
**15 minutes before it was knowable**.

**Fix** (`feature_store.py:100-131`):

* `available_ts` now DEFAULTS to `p.confirmed_ts` — the instant the pivot
  became knowable;
* an `available_ts` EARLIER than the confirmation is REFUSED with a
  `ValueError` naming both timestamps, so the store cannot host a
  not-yet-knowable value;
* the docstring no longer contradicts the code.

```
N1 refused -> available_ts 1700002400 is before the pivot's confirmed_ts 1700003300: a pivot cannot be knowable before it is confirmed
N1 (confirmed-1) refused -> available_ts 1700003299 is before the pivot's confirmed_ts 1700003300: a pivot cannot be knowable before it is confirmed
default available_ts == confirmed_ts: True
as_of at confirmed-1 : None
as_of at confirmed   : True
```

**Tests (new)**

```
  PASS  from_pivots defaults available_ts to the pivot's confirmed_ts
  PASS  from_pivots refuses a pivot before it was knowable
  PASS  from_pivots refuses an available_ts before the confirmation
  PASS  a stored pivot is invisible before its confirmed_ts
  PASS  a stored pivot is visible at its confirmed_ts
```

## 3. Minor findings

| # | Fix | Test |
|---|---|---|
| **N2** | The `FeatureRecord` docstring now matches the code: the stored `reason` is always one of `REASONS`, and the estimator's own reason lives in `meta["estimator_reason"]` (`feature_store.py:49-58`). | (docstring; the behaviour is covered by the N6 checks below) |
| **N3** | The worker-thread test now uses the **same** `FeatureStore` object from the worker thread (a read AND a write), so it fails against the pre-fix code (which raised `ProgrammingError: SQLite objects created in a thread can only be used in that same thread`). | `PASS  the store works from a worker thread` |
| **N4** | The moment-order range is validated BEFORE the parameters are built and before the series is touched (`mfdfa.py:144-150` `_safe_q_range`, `mfdfa.py:207-217`, `mfdfa.py:290-296`).  A broken range (`None`, `"x"`, a 1-tuple, an inverted range) now returns `None` + `"invalid-q-range"` instead of raising `TypeError` / `ValueError`. | `PASS  a broken q_range (None) returns None + a reason instead of raising`, `... ('x') ...`, `... ((1.0,)) ...`, `... ((2.0, 1.0)) ...` |
| **F3 caveat** | The docstring no longer claims H is "in [0, 1]": it says the value is a Hurst exponent (≈0.5 for noise, ≈1.0 for a random walk) and that it is **NOT clamped** — a finite-sample estimate can fall slightly outside (a deterministic ramp gives **1.0079**).  The code was left unclamped, and the report agrees. | `PASS  a deterministic ramp gives a Hurst exponent near 1.0`, `PASS  the Hurst exponent is NOT silently clamped into [0, 1]` |
| **N6** | The stored enum stays small and stable: every failed estimate stores `"missing"` (or `"insufficient"`), and the distinction between a degenerate estimate and an absent feature lives **only** in `meta["estimator_reason"]` (and `meta["n"]`).  This is now stated in the `FeatureRecord` and `from_estimate` docstrings and in §7 below. | `PASS  from_estimate maps a failed estimate to a documented reason`, `PASS  from_estimate keeps the estimator's own reason in the meta` |
| **N5** | The applicability limit is now in the `hurst_generalized` docstring ("APPLICABILITY", `estimators.py:389-393`) and in §6 below: the aggregated-variance estimator needs at least `MIN_BLOCKS = 16` non-overlapping blocks at every scale, i.e. roughly **n ≥ 256**; below that it returns `None` + `"insufficient-sample"`.  This is a property of the estimator (the R/S estimator has no such limit), not a defect — and it is a silent loss of applicability, so it is called out explicitly. | (docstring + report; the behaviour is `None` + "insufficient-sample" for n = 256) |

## 4. Report accuracy corrections

1. **The F5 citation was wrong.**  The round-1 report claimed the store
   "matches `proto/quant/qstore.py:63-65` and `proto/store.py:383-384`".
   Neither of those uses `timeout=5` or `check_same_thread=False`:
   `qstore.py:63-64` is `sqlite3.connect(path)` (no timeout) and
   `store.py:383` is `sqlite3.connect(path, timeout=30)`.  The `timeout=5`
   convention comes from `proto/service.py` (e.g. `:312`, `:347`, `:487`)
   and `proto/daemon.py:179`; the WAL pragma is the house pattern of
   `qstore.py:65` and `store.py:384`.  The **code** is unchanged and
   correct (`sqlite3.connect(path, timeout=5, check_same_thread=False)` +
   `PRAGMA journal_mode=WAL`); only the citation was wrong and it is
   corrected here.
2. **The brief / plan text is stale on the estimator parameter.**  The plan
   (`docs/superpowers/specs/2026-10-19-quant-research-and-startup-design.md`
   §1.2) and the task brief still name the Hurst / MF-DFA parameter
   `returns`; the shipped signature uses **`levels`** (an orchestrator
   ruling, recorded in round 1 and below).  The code keeps `levels`.

## 5. Orchestrator rulings (still in force)

* The Pivot field name stays **`leg_bars`** (the plan's §1.2 says
  `bars_of_leg`; the orchestrator is aligning the plan to the shipped name).
* `hurst_rs` / `hurst_generalized` / `mfdfa` keep their function names; the
  parameter is **`levels`**.

## 6. Applicability limits (stated, not hidden)

| estimator | minimum input | reason |
|---|---|---|
| `hurst_rs` | 2 * min_scale = 16 points | the R/S curve needs >= 3 scales |
| `hurst_generalized` | ~256 points (16 non-overlapping blocks at every scale) | `MIN_BLOCKS`; below that `None` + "insufficient-sample" |
| `mfdfa` / `mfdfa_spectrum` | 2 * q_min = 16 points, and the q range must contain 1.0 | otherwise `None` + a reason |

## 7. The stored reason vocabulary (N6)

`REASONS = ("missing", "stale", "insufficient")` is the stored contract and
it is deliberately small.  A failed estimate is stored as:

| estimator outcome | stored `reason` | the detail |
|---|---|---|
| a value | `None` | — |
| "insufficient-sample" | `"insufficient"` | `meta["estimator_reason"]` |
| "degenerate-zero-variance" / "non-finite-input" / … | `"missing"` | `meta["estimator_reason"]` |

So a degenerate estimate and a feature that was never computed are
indistinguishable **in the stored reason column**; they are told apart by
`meta["estimator_reason"]` (and by `meta["n"]`).  The stored enum was not
widened on purpose: it is a cross-module contract, and the diagnosis is
already carried losslessly in the meta.

## 8. Test commands and their real output

```
$ cd /tmp/opencode/wt-a && python3 tests/test_ws_a_measurement.py
... (146 checks) ...
ALL PASS
$ echo $?
0
```

```
$ cd /tmp/opencode/wt-a && python3 tests/test_quant.py
...
ALL PASS
$ echo $?
0

$ cd /tmp/opencode/wt-a && python3 tests/test_quant_engine.py
...
ALL PASS
$ echo $?
0
```

```
$ cd /tmp/opencode/wt-a && python3 tests/run_all.py
PASS  test_adapter.py  [ALL PASS]
... (82 files) ...
PASS  test_ws_a_measurement.py  [ALL PASS]

ALL SUITES PASS (82 files)
$ echo $?
0
```

## 9. Numerical evidence (unchanged by round 2, re-verified)

| series | `hurst_rs` H | `hurst_generalized` H | `mfdfa` dfa_tau | spectrum width |
|---|---|---|---|---|
| white noise (monofractal) | 0.5498 ± 0.0149 | 0.5559 ± 0.0113 | 0.5316 ± 0.0041 | 0.0011 |
| random walk | 0.9549 ± 0.0152 | 0.9843 ± 0.0042 | 1.5306 ± 0.0093 | 0.0022 |
| bimultifractal (blocks) | 0.8578 ± 0.0655 | 0.7452 ± 0.0843 | 1.7814 ± 0.0721 | 0.1459 |
| deterministic ramp | — | **1.0079** (not clamped) | — | — |

## 10. Commits

| Hash | Message |
|---|---|
| `8a6f9ab` | feat(quant): add the market measurement & structure engine — round 0 |
| `66a4a02` | docs(quant): task A report — round 0 |
| `d4d7d77` | fix(quant): task A review findings (F1-F15) + report — round 1 |
| _(HEAD)_ | fix(quant): task A round 2 (F15 leg fallback, N1 pivot look-ahead, N2/N3/N4/N5/N6) + report |

The fix commit's hash is the branch HEAD; it is printed in the handover
message (writing it into this file would change the file and therefore the
hash).
