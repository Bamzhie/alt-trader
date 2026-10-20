# ALT RADAR — production archive data-coverage audit

**Run at:** 2026-10-20T11:36:34Z (epoch 1792496194)  
**Repo:** `/home/bamzhie/alt-radar` · commit `dc2ca113121816a6635e1389490d7b7a67ac21d9` · branch `main`  
**Scope:** READ-ONLY inventory of `data/bars`, `data/funding`, `data/funding_settlements`.  
**Nothing was written to `data/`.** No backfill, no archive rewrite, no deletion, no service restart. `data/quant_research/` was not created and the holdout was not spent.

> The commit shown above is the repository HEAD at the moment the audit ran (2026-10-20T11:36:34Z); the working tree was being committed to concurrently, so a later HEAD may differ. The archives themselves were read as they were on disk at that instant, while the collector was still appending to them.

> This is an **inventory of what the archives contain**. It contains no view on whether any strategy works, and no result here should be read as evidence for or against a strategy, a rule, or a configuration.

---

## 1. Headline

| Quantity | Measured value |
|---|---|
| Bar archive files | **580** |
| Coins with at least one usable bar | **580** |
| Archive span (first → last bar, all coins) | **2026-07-13T02:35:00Z → 2026-10-20T11:30:00Z** = **99.37 days** |
| Total rows on disk / usable rows | 15,525,436 / 15,525,436 |
| Duplicate timestamps (whole archive) | **0** |
| Malformed rows (whole archive) | **0** (breakdown: none) |
| Gaps ≠ 300 s (whole archive) | **0**, largest gap **0 s** |
| Coins with fewer than 95 days of coverage | **65** (by span) / **65** (by complete 288-bar days) / **65** (by bar-equivalent days) |
| Coins ≥ 95 days on both definitions | **515** |
| Newest bar in the archive | 2026-10-20T11:30:00Z — age **394 s** at run time |
| Coins the watcher would count as stale | **32 / 579** live non-BTC coins = **5.5%** (auto-pause threshold 30%) |
| Funding observation archive | **565** coins, 98,177 rows, **2026-10-15T22:10:23Z → 2026-10-20T11:36:25Z** (median span 4.56 d) |
| Funding settlement archive | **552** coins, 1,355,039 rows, **2025-04-26T16:00:00Z → 2026-10-19T15:00:00Z** (median span 540.7 d) |
| Coins whose funding cycle changed | **175 / 552** |
| Attributable windows (sample) | **583/600** complete, **17** incomplete, **0** missing (60 coins) |
| Configured splits (day numbers from archive start) | dev **6–61**, val **63–77**, holdout **79–99.3715**, embargo **2 d** |

**Two headline asymmetries dominate everything else in this report:**

1. The **bar** archive covers ~99.4 days; the **funding-observation** archive covers only ~4.6 days (2026-10-15T22:10:23Z → 2026-10-20T11:36:25Z), i.e. the last 4.6% of the archive window on the median coin. The **settlement** archive is much longer than the bar archive (541 days median) and starts at or before the bar archive for 517 of 552 coins.
2. Coverage is **not** uniform: 65 of 580 coins have fewer than 95 days of bars. The archive must not be described as "95 days for every coin".

## 2. Method and definitions

### 2.1 How to reproduce

The audit scripts live outside the repo (in `/tmp/opencode/`), so nothing was added to the repository except this report and its JSON twin:

```
python3 /tmp/opencode/audit_scan.py     # per-coin bars/funding/settlements + window sample
python3 /tmp/opencode/audit_order.py    # bar file-order (descents) measurement
python3 /tmp/opencode/audit_verify.py   # independent cross-check vs the repo loader
python3 /tmp/opencode/audit_render.py   # aggregation -> data-audit.json
python3 /tmp/opencode/audit_md.py       # this markdown
```

Runtime of the scanning pass: 111.8 s (8 worker processes).

### 2.2 Definitions used throughout

- **`usable_rows`** — distinct timestamps with a parseable ts and numeric o/h/l/c/vol/amt after sort+dedup; identical to proto.quant.bars.load_coin output
- **`malformed`** — unparseable ts, short row, non-numeric o/h/l/c, non-positive o/h/l/c, h<l, h<max(o,c), l>min(o,c)
- **`gap`** — a step between consecutive (sorted, de-duplicated) bars that is not 300 s
- **`coverage_days_span`** — (last_ts - first_ts)/86400
- **`coverage_days_bar`** — usable_rows/288
- **`complete_day`** — a UTC day in which all 288 five-minute slots are present
- **`watcher_stale`** — lag_bars_vs_btc > stale_coin_bars (3), i.e. the engine's proto/quant/engine.py:437 rule
- **`flag_complete`** — dev window: no gaps, no malformed price rows, and not watcher-stale
- **`flag_partial`** — dev window: has rows but has gaps / malformed rows / is stale
- **`flag_insufficient`** — dev window: fewer than 288 usable rows (< 1 complete day)

### 2.3 Independent cross-check

`proto.quant.bars.load_coin` was run over **all 580** coins and compared row-for-row against the audit scanner: **0 mismatches** (kept rows, gap count, first and last timestamp). The scanners therefore agree with the code the research harness actually uses.

The malformed/duplicate/gap detectors were additionally validated against a synthetic file containing one row of every defect class (unparseable ts, short row, non-numeric OHLC, non-positive price, `h<l`, `h<max(o,c)`, `l>min(o,c)`, duplicate ts, one 600 s step); all classes were detected, so the zero counts reported below are real measurements rather than dead code paths.

## 3. Bars (`data/bars/*.csv.gz`)

### 3.1 File count, header and row accounting

- Files: **580** (`<COIN>.csv.gz`, gzip CSV). The on-disk header is `ts,o,h,l,c,vol,amount` (the task brief wrote `amt`; the file says `amount` — the loader only checks `header[0] == "ts"`, so this is cosmetic).
- Rows read from disk: **15,525,436**
- Usable rows after sort + de-duplication: **15,525,436**
- Duplicate timestamps: **0**
- Malformed rows: **0** — breakdown {} (none)
- Coins with an unreadable file or a bad header: **0** (all 580 files read cleanly)

### 3.2 Gaps

- Total non-300 s steps between consecutive bars (all coins): **0**
- Coins with at least one gap: **0**
- Largest gap anywhere in the archive: **0 s**

> The bars are **contiguous**: for every coin, every consecutive pair of bars is exactly 300 s apart, and no bar is duplicated. Coverage is therefore limited only by where a coin's series *starts*, not by holes inside it.

### 3.3 Coverage distribution (days per coin)

| Metric | min | p10 | median | p90 | max |
|---|---|---|---|---|---|
| span days = (last−first)/86400 | 0.33 | 92.83 | 99.36 | 99.37 | 99.37 |
| bar-equivalent days = usable_rows/288 | 0.33 | 92.83 | 99.36 | 99.38 | 99.38 |
| complete days (UTC day with all 288 slots) | 0.00 | 91.00 | 98.00 | 98.00 | 98.00 |

**Coins with fewer than 95 days: 65 / 580** (span definition), **65** (complete-288-bar-days definition), **65** (bar-equivalent definition). The three agree because there are no gaps and no duplicate rows.

### 3.4 Worst 10 and best 5 by coverage

| Rank | Coin | span days | usable rows | complete days | last bar |
|---|---|---|---|---|---|
| 1 | `GTC` | 0.33 | 95 | 0 | 2026-10-20T11:30:00Z |
| 2 | `PAPER` | 0.79 | 227 | 0 | 2026-10-20T00:00:00Z |
| 3 | `DOPE` | 4.06 | 1,171 | 3 | 2026-10-20T11:30:00Z |
| 4 | `DNA` | 4.14 | 1,193 | 3 | 2026-10-20T11:30:00Z |
| 5 | `CENT` | 8.10 | 2,333 | 7 | 2026-10-20T11:30:00Z |
| 6 | `CLAUDIA` | 8.21 | 2,366 | 7 | 2026-10-20T11:30:00Z |
| 7 | `COOL` | 8.47 | 2,440 | 7 | 2026-10-10T12:50:00Z |
| 8 | `ALLINU` | 8.47 | 2,441 | 7 | 2026-10-10T12:55:00Z |
| 9 | `JEANPHIL` | 10.96 | 3,158 | 10 | 2026-10-20T11:30:00Z |
| 10 | `WHUF` | 11.83 | 3,409 | 11 | 2026-10-20T11:30:00Z |

| Rank | Coin | span days | usable rows | complete days | last bar |
|---|---|---|---|---|---|
| 1 | `1000BONK` | 99.37 | 28,620 | 98 | 2026-10-20T11:30:00Z |
| 2 | `AAVE` | 99.37 | 28,620 | 98 | 2026-10-20T11:30:00Z |
| 3 | `ADA` | 99.37 | 28,620 | 98 | 2026-10-20T11:30:00Z |
| 4 | `AERO` | 99.37 | 28,620 | 98 | 2026-10-20T11:30:00Z |
| 5 | `ALGO` | 99.37 | 28,620 | 98 | 2026-10-20T11:30:00Z |

The tail is not a cliff but a long ramp: after the coins listed above there are further coins between ~14 and ~93 days. The two worst coins (`GTC` 0.33 d, `PAPER` 0.78 d) have essentially no history.

### 3.5 On-disk ordering (why the sort matters)

- Files **not** in chronological order on disk: **566 / 580**
- Total backwards steps inside the files (descents): **6,476**

This confirms the property documented in `proto/quant/bars.py`: the collector appends backfilled pages after forward-collected rows, so a bar file is *not* time-ordered. Any coverage measurement that reads the file sequentially will report a wrong first/last bar. Every number in this report is computed on the sorted, de-duplicated series.

### 3.6 Freshness and the watcher's staleness rule

- Newest bar in the whole archive: **2026-10-20T11:30:00Z** (BTC), age **394 s** at run time.
- Per-coin latest-bar age (seconds since run): min **394**, p10 **394**, median **394**, p90 **394**, max **859594**.
- Coins the live watcher would count as stale (lag behind BTC > `stale_coin_bars` = 3 bars, the rule at `proto/quant/engine.py:437`): **32 / 579** live non-BTC coins = **5.5%**.
- Global auto-pause threshold `pause_stale_fraction` = **30%** → the stale-fraction rule **would NOT trigger a global auto-pause** on this snapshot (5.5% < 30%).
- BTC-staleness guard (`now − (btc_last+300) > 4×300`): BTC age is **394 s** → guard **not** tripped.

The stale coins are the same ones that have short histories: they stopped being collected rather than falling behind for a few bars.

## 4. Funding observations (`data/funding/*.csv.gz`)

- Files: **573**; of those, **8** belong to coins that have no bar archive at all. Coins with bars **and** a funding archive: **565**; coins with bars but **no** funding file: **15**.
- Total rows: **98,177**; malformed rows: **0**; duplicate timestamps: **0**.
- Archive range: **2026-10-15T22:10:23Z → 2026-10-20T11:36:25Z**.

| Metric | min | p10 | median | p90 | max |
|---|---|---|---|---|---|
| covered span (days) | 0.089 | 4.56 | 4.56 | 4.56 | 4.56 |
| gap between observations — median (s) | 760 | 1387 | 1387 | 1387 | 1626 |
| gap between observations — p95 (s) | 971.5 | 3901 | 3901 | 3901 | 3915 |
| coverage fraction of the archive span | 0.001 | 0.046 | 0.046 | 0.046 | 0.046 |

**Only 0 coins** cover the whole archive span with their funding observations. The observation archive starts 2026-10-15T22:10:23Z — roughly 95 days after the bar archive starts.

Per-coin funding coverage (full table in `data-audit.json`; the extremes here):

| Coin | rows | first | last | span (d) | median gap (s) | p95 gap (s) | coverage of archive span |
|---|---|---|---|---|---|---|---|
| `BONER` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.09 | 760 | 972 | 0.09% |
| `FONE` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.09 | 760 | 972 | 0.09% |
| `MASKSOL` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.09 | 760 | 972 | 0.09% |
| `ZORA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.56 | 1387 | 3901 | 4.58% |
| `ZRO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.56 | 1387 | 3901 | 4.58% |
| `ZRX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.56 | 1387 | 3901 | 4.58% |

Coins with bars but no funding archive: `1000BTT`, `ALLINU`, `BOBA`, `COOL`, `DBR`, `DEBIT`, `DELTA`, `GEOD`, `NPC`, `RON`, `SWARM`, `TFUEL`, `UPROBINHOOD`, `ZBCN`, `ZIG`.

## 5. Funding settlements (`data/funding_settlements/*.csv.gz`)

- Files: **560**; of those, **8** belong to coins that have no bar archive at all. Coins with bars **and** a settlement archive: **552**; coins with bars but **no** settlement file: **28**.
- Total rows: **1,355,039**; malformed rows: **0**; duplicate settle timestamps: **0**; rows with a missing/invalid `cycle_hours`: **0**.
- Archive range: **2025-04-26T16:00:00Z → 2026-10-19T15:00:00Z** (median span 540.7 days) — it starts long before the bar archive and ends at the most recent venue settlement.
- Settlement archives that **start** at or before the bar archive's first bar: **517 / 552**; that **span the whole configured dev window**: **518 / 552**.
- The settlement archive's tail falls short of the newest bar by a median of **84600 s** (min 73800 s, max 99000 s) — the venue's last settlement precedes the newest bar by roughly one funding cycle, which is expected, not a gap in the settlement series itself.

### 5.1 Cycle changes

- Coins whose `cycle_hours` changed at least once: **175 / 552** (31.7%).
- Coins with a constant cycle: **377**.
- Transitions per changed coin: min 1, median 2, p90 6, max 12.

Distinct `cycle_hours` combinations observed (coins → combination):

| `cycle_hours` set | coins |
|---|---|
| {4} | 267 |
| {1, 4} | 114 |
| {8} | 108 |
| {1, 4, 8} | 32 |
| {4, 8} | 17 |
| {2, 4} | 8 |
| {1, 2, 4} | 4 |
| {1} | 2 |

Every settlement row in the archive carries a usable `cycle_hours`, so the venue grid is inferable for every coin that has a settlement archive.

## 6. Attributable vs incomplete funding windows

**Method.** `proto.quant.cost_attribution.funding_cost(coin, open, close, funding_dir=data/funding, settlements_dir=data/funding_settlements, side=+1)` was called over a bounded sample of holding windows:

- **60 coins** — every floor(n/60)-th coin of the sorted bar universe (deterministic spread, not random).
- **5 window starts per coin** at fractions [0.15, 0.35, 0.55, 0.75, 0.9] of that coin's own bar span, so the windows land inside the archive.
- Durations: **4h and 24h**.
- **600 windows** in total.
- The archive loaders inside `cost_attribution` were wrapped with `functools.lru_cache` so the repeated lookups stay cheap.

| Duration | windows | complete | incomplete | missing |
|---|---|---|---|---|
| 4h | 300 | 294 | 6 | 0 |
| 24h | 300 | 289 | 11 | 0 |
| **all** | **600** | **583** | **17** | **0** |

- **Complete (cost attributable): 583/600 = 97.2%**
- **Incomplete: 17/600 = 2.8%**
- **Missing: 0/600 = 0.0%**

**Reason strings on the incomplete windows** (as returned by `funding_cost`):

| reason | windows |
|---|---|
| `observation-coverage-gap` | 9 |
| `settlement-boundaries-missing+observation-coverage-gap` | 8 |

**Source of the attribution** (all non-missing windows):

| source | windows |
|---|---|
| `settlement` | 589 |
| `observation` | 11 |

Reading of these counts (descriptive only): the venue-reported settlement archive is long and dense enough that nearly every sampled window can be priced from settled facts with no mapping; the residual incompleteness is concentrated in windows where a settlement row is absent **and** the much shorter observation archive cannot cover the same boundary.

> The sample is 60 coins × 5 start points × 2 durations = 600 windows. It is a sample, not the full universe; the per-window rows are in `data-audit.json` under `attribution_windows` only as aggregates, and the raw window list was not carried into the repository.

## 7. Usable universe and the configured split boundaries

### 7.1 Usable universe

| Population | Count |
|---|---|
| Coins with at least one usable bar | 580 |
| Coins with ≥ 1 complete day of bars | 578 |
| Coins with ≥ 95 days of coverage (span) | 515 |
| Coins with ≥ 95 complete 288-bar days | 515 |
| Coins with ≥ 95 days on **both** definitions | **515** |
| Coins with a funding-observation archive | 565 |
| Coins with a settlement archive | 552 |
| Coins usable in the dev window (≥ 1 day of bars there) | 548 |

Dev-window usability flags (see §8 for the per-coin table):

| flag | coins | meaning |
|---|---|---|
| `complete` | 528 | dev window: no gaps, no malformed price rows, and not watcher-stale |
| `partial` | 20 | dev window: has rows but has gaps / malformed rows / is stale |
| `insufficient` | 32 | dev window: fewer than 288 usable rows (< 1 complete day) |

### 7.2 Configured split boundaries

`proto/quant/config.py` → `Splits` (frozen defaults of `QuantConfig DEFAULT`), evaluated as `Splits.bounds(n_days)` with `n_days = 99.3715`, i.e. the measured archive span (measured archive span (last bar - first bar)/86400).

- `first_event_day` = 6
- `dev_end_day` = 61
- `embargo_days` = 2
- `val_len_days` = 14

| split | day range | UTC window |
|---|---|---|
| dev | 6 – 61 | 2026-07-19T02:35:00Z → 2026-09-12T02:35:00Z |
| embargo | 61 – 63 (2 d) | — |
| val | 63 – 77 | 2026-09-14T02:35:00Z → 2026-09-28T02:35:00Z |
| embargo | 77 – 79 (2 d) | — |
| holdout | 79 – 99.3715 | 2026-09-30T02:35:00Z → 2026-10-20T11:30:00Z |

> **These are the *configured* boundaries, not evidence about the data.** `Splits.bounds` returns the holdout as `(79, n_days)` where `n_days` is whatever the archive length is at call time. The archive is a live, still-appending one, so the holdout's end is a **moving boundary**, not a sealed interval. Nothing in this audit establishes that any split is populated, that the holdout is safe to spend, or that the embargo is sufficient for any particular rule.

## 8. Per-coin inventory

### 8.1 Bars — per coin

`rows` = usable rows after sort + dedup; `dup` = duplicate timestamps; `mal` = malformed rows; `gaps` = non-300 s steps; `age` = seconds since the coin's newest bar at run time; `lag` = bars behind BTC.

| coin | first | last | span d | rows | dup | mal | gaps | max gap s | complete d | age s | lag bars | ordered? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `0G` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `1000000BABYDOGE` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `1000000MOG` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `1000BONK` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `1000BTT` | 2026-07-13T02:50:00Z | 2026-10-13T23:05:00Z | 92.84 | 26,740 | 0 | 0 | 0 | 0 | 91 | 563494 | 1877 | no |
| `1000RATS` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `1INCH` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `2Z` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `4` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `A` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AAVE` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ACE` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ACH` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ACT` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ACU` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ADA` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AERO` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AEVO` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AGENCY` | 2026-10-04T07:20:00Z | 2026-10-20T11:30:00Z | 16.17 | 4,659 | 0 | 0 | 0 | 0 | 15 | 394 | 0 | yes |
| `AGI` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AGT` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AIA` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AIN` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AIO` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AIOT` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AIOZ` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AIXBT` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AKE` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AKT` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ALCH` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ALGO` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ALICE` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ALL` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ALLINU` | 2026-10-02T01:35:00Z | 2026-10-10T12:55:00Z | 8.47 | 2,441 | 0 | 0 | 0 | 0 | 7 | 859294 | 2863 | yes |
| `ALLO` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ALPINE` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ALT` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ANIME` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ANKR` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ANSEM` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `APE` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `APEX` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `API3` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `APM` | 2026-09-18T05:50:00Z | 2026-10-20T11:30:00Z | 32.24 | 9,285 | 0 | 0 | 0 | 0 | 31 | 394 | 0 | no |
| `APR` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `APT` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AR` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ARB` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ARCSOL` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ARGUS` | 2026-09-16T03:35:00Z | 2026-10-20T06:10:00Z | 34.11 | 9,824 | 0 | 0 | 0 | 0 | 33 | 19594 | 64 | no |
| `ARIA` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ARK` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ARKM` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ARPA` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ASR` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ASTER` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ASTR` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AT` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ATH` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ATOM` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AUCTION` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AUDIO` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AURORA` | 2026-09-23T06:20:00Z | 2026-10-20T11:30:00Z | 27.21 | 7,839 | 0 | 0 | 0 | 0 | 26 | 394 | 0 | no |
| `AVA` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AVAAI` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AVAX` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AVNT` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AWE` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AXL` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `AXS` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `B` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `B2` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `B3` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BABY` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BAN` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BANANA` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BANANAS31` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BAND` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BANK` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BARD` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BAS` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BASECAT` | 2026-08-17T09:10:00Z | 2026-10-20T11:30:00Z | 64.10 | 18,461 | 0 | 0 | 0 | 0 | 63 | 394 | 0 | no |
| `BASED` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BAT` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BATON` | 2026-09-15T06:35:00Z | 2026-10-20T11:30:00Z | 35.20 | 10,140 | 0 | 0 | 0 | 0 | 34 | 394 | 0 | no |
| `BB` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BCH` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BEAMX` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BEAT` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BEL` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BERA` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BICO` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BIGTIME` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BIO` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BIRB` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BLEND` | 2026-07-13T03:10:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,613 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BLESS` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BLUAI` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BLUR` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BMT` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BNB` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BNT` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BOBA` | 2026-07-13T03:10:00Z | 2026-10-13T23:05:00Z | 92.83 | 26,736 | 0 | 0 | 0 | 0 | 91 | 563494 | 1877 | no |
| `BOME` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BONER` | 2026-09-01T06:00:00Z | 2026-10-16T00:10:00Z | 44.76 | 12,891 | 0 | 0 | 0 | 0 | 44 | 386794 | 1288 | no |
| `BP` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BR` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BRETT` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BROCCOLI` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BROCCOLIF3B` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BSB` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BSV` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BTC` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BTR` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BTW` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `BULLA` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `C` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `C98` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CAKE` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CAP` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CARV` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CASHCAT` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CAT` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CATE` | 2026-07-27T05:55:00Z | 2026-10-20T11:30:00Z | 85.23 | 24,548 | 0 | 0 | 0 | 0 | 84 | 394 | 0 | no |
| `CATI` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CC` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CELO` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CELR` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CENT` | 2026-10-12T09:10:00Z | 2026-10-20T11:30:00Z | 8.10 | 2,333 | 0 | 0 | 0 | 0 | 7 | 394 | 0 | yes |
| `CETUS` | 2026-07-13T04:10:00Z | 2026-10-20T11:30:00Z | 99.31 | 28,601 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CFG` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CFX` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CGPT` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CHEEMS` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CHILLGUY` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CHIP` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CHR` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CHZ` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CKB` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CLANKER` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CLAUDIA` | 2026-10-12T06:25:00Z | 2026-10-20T11:30:00Z | 8.21 | 2,366 | 0 | 0 | 0 | 0 | 7 | 394 | 0 | yes |
| `CLAUS` | 2026-10-08T05:20:00Z | 2026-10-20T11:30:00Z | 12.26 | 3,531 | 0 | 0 | 0 | 0 | 11 | 394 | 0 | yes |
| `CLO` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CNPY` | 2026-09-07T12:05:00Z | 2026-10-20T11:30:00Z | 42.98 | 12,378 | 0 | 0 | 0 | 0 | 42 | 394 | 0 | no |
| `COAI` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `COLLECT` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `COMP` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `COOKIE` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `COOL` | 2026-10-02T01:35:00Z | 2026-10-10T12:50:00Z | 8.47 | 2,440 | 0 | 0 | 0 | 0 | 7 | 859594 | 2864 | yes |
| `CORE` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `COTI` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `COW` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CP` | 2026-09-02T14:50:00Z | 2026-10-20T11:30:00Z | 47.86 | 13,785 | 0 | 0 | 0 | 0 | 47 | 394 | 0 | no |
| `CRO` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CROSS` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CRV` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CT` | 2026-09-30T08:20:00Z | 2026-10-20T11:30:00Z | 20.13 | 5,799 | 0 | 0 | 0 | 0 | 19 | 394 | 0 | no |
| `CTC` | 2026-07-13T03:10:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,613 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CTK` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CTR` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CTSI` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CVC` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CVX` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CYBER` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `CYS` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `DATA` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `DBR` | 2026-07-13T03:10:00Z | 2026-10-13T23:05:00Z | 92.83 | 26,736 | 0 | 0 | 0 | 0 | 91 | 563494 | 1877 | no |
| `DEBIT` | 2026-08-26T10:15:00Z | 2026-10-13T23:00:00Z | 48.53 | 13,978 | 0 | 0 | 0 | 0 | 47 | 563794 | 1878 | no |
| `DEEP` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `DELTA` | 2026-08-25T06:10:00Z | 2026-10-13T23:00:00Z | 49.70 | 14,315 | 0 | 0 | 0 | 0 | 48 | 563794 | 1878 | no |
| `DEXE` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `DGAI` | 2026-08-25T06:10:00Z | 2026-10-20T11:30:00Z | 56.22 | 16,193 | 0 | 0 | 0 | 0 | 55 | 394 | 0 | no |
| `DIA` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `DNA` | 2026-10-16T08:10:00Z | 2026-10-20T11:30:00Z | 4.14 | 1,193 | 0 | 0 | 0 | 0 | 3 | 394 | 0 | yes |
| `DODO` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `DOGE` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `DOGS` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `DOLO` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `DOOD` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `DOPE` | 2026-10-16T10:00:00Z | 2026-10-20T11:30:00Z | 4.06 | 1,171 | 0 | 0 | 0 | 0 | 3 | 394 | 0 | yes |
| `DOS` | 2026-08-11T05:10:00Z | 2026-10-20T11:30:00Z | 70.26 | 20,237 | 0 | 0 | 0 | 0 | 69 | 394 | 0 | no |
| `DOT` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `DRIFT` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `DYDX` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `DYM` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `EACC` | 2026-09-26T13:00:00Z | 2026-10-20T06:10:00Z | 23.71 | 6,831 | 0 | 0 | 0 | 0 | 23 | 19594 | 64 | no |
| `EDEN` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `EDGE` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `EDU` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `EIGEN` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ELSA` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ENA` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ENJ` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ENS` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ENSO` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `EPIC` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ERA` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ESP` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ESPORTS` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ETC` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ETH` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ETHFI` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ETHW` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `EUL` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `EVAA` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `F` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FARTCOIN` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FET` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FF` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FHE` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FIDA` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FIGHT` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FLOCK` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FLOKI` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FLOW` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FLR` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FLUID` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FLUX` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FOGO` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FOLKS` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FONE` | 2026-08-28T04:55:00Z | 2026-10-16T00:10:00Z | 48.80 | 14,056 | 0 | 0 | 0 | 0 | 48 | 386794 | 1288 | no |
| `FORM` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `FRAX` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `G` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GALA` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GENIUS` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GEOD` | 2026-07-27T05:50:00Z | 2026-10-13T23:00:00Z | 78.72 | 22,671 | 0 | 0 | 0 | 0 | 77 | 563794 | 1878 | no |
| `GIGGLE` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GLM` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GMT` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GMX` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GOAT` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GP` | 2026-09-24T04:25:00Z | 2026-10-20T11:30:00Z | 26.30 | 7,574 | 0 | 0 | 0 | 0 | 25 | 394 | 0 | no |
| `GPS` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GRAM` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GRASS` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GRIFFAIN` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GRT` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GRVT` | 2026-07-30T12:10:00Z | 2026-10-20T11:30:00Z | 81.97 | 23,609 | 0 | 0 | 0 | 0 | 81 | 394 | 0 | no |
| `GTC` | 2026-10-20T03:40:00Z | 2026-10-20T11:30:00Z | 0.33 | 95 | 0 | 0 | 0 | 0 | 0 | 394 | 0 | yes |
| `GUA` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GUN` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `GWEI` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `H` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HAEDAL` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HANA` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HBAR` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HEI` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HEMI` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HIVE` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HMSTR` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HNT` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HOLO` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HOME` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HOOKED` | 2026-10-04T07:25:00Z | 2026-10-20T11:30:00Z | 16.17 | 4,658 | 0 | 0 | 0 | 0 | 15 | 394 | 0 | yes |
| `HOT` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HUMA` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HYPE` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `HYPER` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ICNT` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ICP` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ID` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `IDOL` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ILV` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `IMX` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `IN` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `INIT` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `INJ` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `INX` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `IO` | 2026-07-13T03:40:00Z | 2026-10-20T11:30:00Z | 99.33 | 28,607 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `IOST` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `IOTA` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `IOTX` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `IRYS` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `JASMY` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `JCT` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `JEANPHIL` | 2026-10-09T12:25:00Z | 2026-10-20T11:30:00Z | 10.96 | 3,158 | 0 | 0 | 0 | 0 | 10 | 394 | 0 | yes |
| `JELLYJELLY` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `JOE` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `JST` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `JTO` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `JUP` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `KAIA` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `KAITO` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `KAS` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `KAT` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `KAVA` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `KERNEL` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `KGEN` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `KII` | 2026-08-14T13:10:00Z | 2026-10-20T11:30:00Z | 66.93 | 19,277 | 0 | 0 | 0 | 0 | 66 | 394 | 0 | no |
| `KITE` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `KMNO` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `KNC` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `KOMA` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `KSM` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LA` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LAB` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LAYER` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LDO` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LIGHT` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LINEA` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LINK` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LISTA` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LIT` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LPT` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LQTY` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LRC` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LSK` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LTC` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LUMIA` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LUNC` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `LYN` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `M` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MAGIC` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MAGMA` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MANA` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MANTA` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MANTRA` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MARSCOIN` | 2026-07-30T13:40:00Z | 2026-10-20T11:30:00Z | 81.91 | 23,591 | 0 | 0 | 0 | 0 | 81 | 394 | 0 | no |
| `MASKSOL` | 2026-09-26T13:00:00Z | 2026-10-16T00:10:00Z | 19.46 | 5,607 | 0 | 0 | 0 | 0 | 19 | 386794 | 1288 | no |
| `MAV` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MAVIA` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ME` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MEGA` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MELANIA` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MEME` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MEMEROBINHOOD` | 2026-09-04T09:10:00Z | 2026-10-20T11:30:00Z | 46.10 | 13,277 | 0 | 0 | 0 | 0 | 45 | 394 | 0 | no |
| `MERL` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MET` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `METIS` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MEW` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MIRA` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MITO` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MMT` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MNT` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MOCA` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MOO` | 2026-09-01T05:55:00Z | 2026-10-16T00:10:00Z | 44.76 | 12,892 | 0 | 0 | 0 | 0 | 44 | 386794 | 1288 | no |
| `MOODENG` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MORPHO` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MOVE` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MOVR` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MTL` | 2026-07-13T03:10:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,613 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MUBARAK` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `MUSEBOOK` | 2026-09-16T14:15:00Z | 2026-10-16T00:10:00Z | 29.41 | 8,472 | 0 | 0 | 0 | 0 | 29 | 386794 | 1288 | no |
| `MYX` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `NAORIS` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `NEAR` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `NEIROCTO` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `NEO` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `NES` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `NEWT` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `NEX` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `NIL` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `NMR` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `NOM` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `NOT` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `NPC` | 2026-07-13T02:40:00Z | 2026-10-13T23:00:00Z | 92.85 | 26,741 | 0 | 0 | 0 | 0 | 91 | 563794 | 1878 | no |
| `NXPC` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `O` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `OFC` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `OG` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `OGN` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `OKB` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `OL` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ON` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ONDO` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ONE` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ONG` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ONT` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `OP` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `OPG` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `OPN` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ORBIO` | 2026-09-03T10:40:00Z | 2026-10-16T00:10:00Z | 42.56 | 12,259 | 0 | 0 | 0 | 0 | 42 | 386794 | 1288 | no |
| `ORCA` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ORDER` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ORDI` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `OTC` | 2026-09-23T06:20:00Z | 2026-10-20T06:10:00Z | 26.99 | 7,775 | 0 | 0 | 0 | 0 | 26 | 19594 | 64 | no |
| `PAID` | 2026-09-16T03:55:00Z | 2026-10-20T06:10:00Z | 34.09 | 9,820 | 0 | 0 | 0 | 0 | 33 | 19594 | 64 | no |
| `PAPER` | 2026-10-19T05:10:00Z | 2026-10-20T00:00:00Z | 0.79 | 227 | 0 | 0 | 0 | 0 | 0 | 41794 | 138 | yes |
| `PARTI` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PEAQ` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PENDLE` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PENGU` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PEOPLE` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PEPE` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PHA` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PI` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PIEVERSE` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PIPPIN` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PIXEL` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PLAY` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PLUME` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PNUT` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `POD` | 2026-10-02T05:55:00Z | 2026-10-16T00:10:00Z | 13.76 | 3,964 | 0 | 0 | 0 | 0 | 13 | 386794 | 1288 | yes |
| `POL` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `POLYX` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PONS` | 2026-07-15T09:00:00Z | 2026-10-20T11:30:00Z | 97.10 | 27,967 | 0 | 0 | 0 | 0 | 96 | 394 | 0 | no |
| `POPCAT` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PORTAL` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `POWER` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `POWR` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PRL` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PROM` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PROVE` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PTB` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PUFFER` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PUNDIX` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `PYTH` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `Q` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `QNT` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `QTUM` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RARE` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RAVE` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RAY` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RE` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RECALL` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RED` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RENDER` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RESOLV` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `REZ` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RHEA` | 2026-09-23T06:15:00Z | 2026-10-20T11:30:00Z | 27.22 | 7,840 | 0 | 0 | 0 | 0 | 26 | 394 | 0 | no |
| `RIF` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RIVER` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RLC` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RLS` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ROBO` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RON` | 2026-07-13T03:55:00Z | 2026-10-13T23:05:00Z | 92.80 | 26,727 | 0 | 0 | 0 | 0 | 91 | 563494 | 1877 | no |
| `RPL` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RSR` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RUNE` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `RVN` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `S` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SAFE` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SAGA` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SAHARA` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SAND` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SANTOS` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SAPIEN` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SATS` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SCR` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SEI` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SENT` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SFP` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SHELL` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SHIB` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SHROOM` | 2026-09-06T06:10:00Z | 2026-10-16T00:10:00Z | 39.75 | 11,449 | 0 | 0 | 0 | 0 | 39 | 386794 | 1288 | no |
| `SI` | 2026-09-26T13:00:00Z | 2026-10-20T11:30:00Z | 23.94 | 6,895 | 0 | 0 | 0 | 0 | 23 | 394 | 0 | no |
| `SIGN` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SIREN` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SKL` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SKR` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SKY` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SKYAI` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SLP` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SLX` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SNX` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SOCK` | 2026-09-08T05:45:00Z | 2026-10-16T00:10:00Z | 37.77 | 10,878 | 0 | 0 | 0 | 0 | 37 | 386794 | 1288 | no |
| `SOL` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SOLV` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SOMI` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SONIC` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SOPH` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SPACE` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SPELL` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SPK` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SQD` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SSV` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `STABLE` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `STANDARD` | 2026-09-15T06:35:00Z | 2026-10-16T00:10:00Z | 30.73 | 8,852 | 0 | 0 | 0 | 0 | 30 | 386794 | 1288 | no |
| `STAR` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `STBL` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `STEEM` | 2026-07-13T03:10:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,613 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `STO` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `STONK` | 2026-09-06T15:40:00Z | 2026-10-20T11:30:00Z | 43.83 | 12,623 | 0 | 0 | 0 | 0 | 43 | 394 | 0 | no |
| `STORJ` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `STRK` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `STX` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SUI` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SUN` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SUPER` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SUSHI` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SWARM` | 2026-08-20T12:25:00Z | 2026-10-13T23:00:00Z | 54.44 | 15,680 | 0 | 0 | 0 | 0 | 53 | 563794 | 1878 | no |
| `SWARMS` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SXT` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SYN` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `SYRUP` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `T` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TA` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TAC` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TAG` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TAIKO` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TAKE` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TAO` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TFUEL` | 2026-07-13T02:40:00Z | 2026-10-13T23:00:00Z | 92.85 | 26,741 | 0 | 0 | 0 | 0 | 91 | 563794 | 1878 | no |
| `THE` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `THETA` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TIA` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TLM` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TMX` | 2026-08-25T10:05:00Z | 2026-10-20T11:30:00Z | 56.06 | 16,146 | 0 | 0 | 0 | 0 | 55 | 394 | 0 | no |
| `TNSR` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TOSHI` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TOWNS` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TRADOOR` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TRB` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TREAD` | 2026-09-17T04:50:00Z | 2026-10-20T11:30:00Z | 33.28 | 9,585 | 0 | 0 | 0 | 0 | 32 | 394 | 0 | no |
| `TREE` | 2026-07-13T03:10:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,613 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TRIA` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TRUST` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TRUTH` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TRX` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TST` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TURBO` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TURTLE` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TUT` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `TWT` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `UAI` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `UB` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `UBIK` | 2026-09-22T04:25:00Z | 2026-10-16T00:10:00Z | 23.82 | 6,862 | 0 | 0 | 0 | 0 | 23 | 386794 | 1288 | no |
| `UMA` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `UNI` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `UP` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `UPROBINHOOD` | 2026-08-11T05:10:00Z | 2026-10-13T23:00:00Z | 63.74 | 18,359 | 0 | 0 | 0 | 0 | 62 | 563794 | 1878 | no |
| `US` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `USDC` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `USELESS` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `USTC` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `USUAL` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `VANA` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `VELO` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `VELODROME` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `VELVET` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `VET` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `VIRTUAL` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `VTHO` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `VVV` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `W` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `WAL` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `WAVES` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `WAXP` | 2026-07-13T03:10:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,613 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `WCT` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `WET` | 2026-07-13T03:10:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,613 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `WHUF` | 2026-10-08T15:30:00Z | 2026-10-20T11:30:00Z | 11.83 | 3,409 | 0 | 0 | 0 | 0 | 11 | 394 | 0 | yes |
| `WIF` | 2026-07-13T02:40:00Z | 2026-10-20T11:15:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 1294 | 3 | no |
| `WLD` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `WLFI` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `WOO` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XAI` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XAN` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XCN` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XDC` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XDP` | 2026-09-28T13:50:00Z | 2026-10-20T11:30:00Z | 21.90 | 6,309 | 0 | 0 | 0 | 0 | 21 | 394 | 0 | no |
| `XEC` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XLM` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XMR` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XNY` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XPIN` | 2026-07-13T03:00:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,615 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XPL` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XRP` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XTZ` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XVG` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `XVS` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `YB` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `YFI` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `YGG` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ZAMA` | 2026-07-13T02:45:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,618 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ZBCN` | 2026-07-13T03:05:00Z | 2026-10-13T23:05:00Z | 92.83 | 26,737 | 0 | 0 | 0 | 0 | 91 | 563494 | 1877 | no |
| `ZBT` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ZC` | 2026-09-29T06:00:00Z | 2026-10-20T11:30:00Z | 21.23 | 6,115 | 0 | 0 | 0 | 0 | 20 | 394 | 0 | no |
| `ZCAT` | 2026-09-07T09:15:00Z | 2026-10-20T11:30:00Z | 43.09 | 12,412 | 0 | 0 | 0 | 0 | 42 | 394 | 0 | no |
| `ZEN` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ZEREBRO` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ZEST` | 2026-07-13T03:05:00Z | 2026-10-20T11:30:00Z | 99.35 | 28,614 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ZETA` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ZIG` | 2026-07-13T02:40:00Z | 2026-10-13T23:00:00Z | 92.85 | 26,741 | 0 | 0 | 0 | 0 | 91 | 563794 | 1878 | no |
| `ZIL` | 2026-07-13T02:50:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,617 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ZKC` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ZORA` | 2026-07-13T02:55:00Z | 2026-10-20T11:30:00Z | 99.36 | 28,616 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ZRO` | 2026-07-13T02:35:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,620 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ZRX` | 2026-07-13T02:40:00Z | 2026-10-20T11:30:00Z | 99.37 | 28,619 | 0 | 0 | 0 | 0 | 98 | 394 | 0 | no |
| `ZZZ` | 2026-09-06T06:10:00Z | 2026-10-16T00:10:00Z | 39.75 | 11,449 | 0 | 0 | 0 | 0 | 39 | 386794 | 1288 | no |

### 8.2 Dev window (configured dev range) — per coin

Dev window = **2026-07-19T02:35:00Z → 2026-09-12T02:35:00Z** (day 6–61 from the archive start), i.e. 55 days = 15,840 possible 5-minute bars.

| coin | usable days | usable rows | cover | first usable | last usable | gaps | flag | reason |
|---|---|---|---|---|---|---|---|---|
| `0G` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `1000000BABYDOGE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `1000000MOG` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `1000BONK` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `1000BTT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1877 |
| `1000RATS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `1INCH` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `2Z` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `4` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `A` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AAVE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ACE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ACH` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ACT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ACU` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ADA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AERO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AEVO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AGENCY` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `AGI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AGT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AIA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AIN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AIO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AIOT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AIOZ` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AIXBT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AKE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AKT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ALCH` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ALGO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ALICE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ALL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ALLINU` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `ALLO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ALPINE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ALT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ANIME` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ANKR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ANSEM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `APE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `APEX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `API3` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `APM` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `APR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `APT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ARB` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ARCSOL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ARGUS` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `ARIA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ARK` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ARKM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ARPA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ASR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ASTER` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ASTR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ATH` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ATOM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AUCTION` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AUDIO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AURORA` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `AVA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AVAAI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AVAX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AVNT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AWE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AXL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `AXS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `B` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `B2` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `B3` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BABY` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BAN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BANANA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BANANAS31` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BAND` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BANK` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BARD` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BAS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BASECAT` | 25.73 | 7,409 | 46.8% | 2026-08-17T09:10:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BASED` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BAT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BATON` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `BB` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BCH` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BEAMX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BEAT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BEL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BERA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BICO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BIGTIME` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BIO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BIRB` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BLEND` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BLESS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BLUAI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BLUR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BMT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BNB` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BNT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BOBA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1877 |
| `BOME` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BONER` | 10.86 | 3,127 | 19.7% | 2026-09-01T06:00:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1288 |
| `BP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BRETT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BROCCOLI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BROCCOLIF3B` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BSB` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BSV` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BTC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BTR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BTW` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `BULLA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `C` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `C98` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CAKE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CAP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CARV` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CASHCAT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CAT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CATE` | 46.86 | 13,496 | 85.2% | 2026-07-27T05:55:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CATI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CELO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CELR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CENT` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `CETUS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CFG` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CFX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CGPT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CHEEMS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CHILLGUY` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CHIP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CHR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CHZ` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CKB` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CLANKER` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CLAUDIA` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `CLAUS` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `CLO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CNPY` | 4.60 | 1,326 | 8.4% | 2026-09-07T12:05:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `COAI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `COLLECT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `COMP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `COOKIE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `COOL` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `CORE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `COTI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `COW` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CP` | 9.49 | 2,733 | 17.2% | 2026-09-02T14:50:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CRO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CROSS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CRV` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CT` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `CTC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CTK` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CTR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CTSI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CVC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CVX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CYBER` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `CYS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DATA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DBR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1877 |
| `DEBIT` | 16.68 | 4,804 | 30.3% | 2026-08-26T10:15:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1878 |
| `DEEP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DELTA` | 17.85 | 5,141 | 32.5% | 2026-08-25T06:10:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1878 |
| `DEXE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DGAI` | 17.85 | 5,141 | 32.5% | 2026-08-25T06:10:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DIA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DNA` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `DODO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DOGE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DOGS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DOLO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DOOD` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DOPE` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `DOS` | 31.89 | 9,185 | 58.0% | 2026-08-11T05:10:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DOT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DRIFT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DYDX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `DYM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `EACC` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `EDEN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `EDGE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `EDU` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `EIGEN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ELSA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ENA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ENJ` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ENS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ENSO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `EPIC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ERA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ESP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ESPORTS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ETC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ETH` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ETHFI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ETHW` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `EUL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `EVAA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `F` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FARTCOIN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FET` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FF` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FHE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FIDA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FIGHT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FLOCK` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FLOKI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FLOW` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FLR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FLUID` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FLUX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FOGO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FOLKS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FONE` | 14.90 | 4,292 | 27.1% | 2026-08-28T04:55:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1288 |
| `FORM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `FRAX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `G` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GALA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GENIUS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GEOD` | 46.87 | 13,497 | 85.2% | 2026-07-27T05:50:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1878 |
| `GIGGLE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GLM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GMT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GMX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GOAT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GP` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `GPS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GRAM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GRASS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GRIFFAIN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GRT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GRVT` | 43.60 | 12,557 | 79.3% | 2026-07-30T12:10:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GTC` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `GUA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GUN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `GWEI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `H` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HAEDAL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HANA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HBAR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HEI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HEMI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HIVE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HMSTR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HNT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HOLO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HOME` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HOOKED` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `HOT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HUMA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HYPE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `HYPER` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ICNT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ICP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ID` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `IDOL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ILV` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `IMX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `IN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `INIT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `INJ` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `INX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `IO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `IOST` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `IOTA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `IOTX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `IRYS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `JASMY` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `JCT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `JEANPHIL` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `JELLYJELLY` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `JOE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `JST` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `JTO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `JUP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `KAIA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `KAITO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `KAS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `KAT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `KAVA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `KERNEL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `KGEN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `KII` | 28.56 | 8,225 | 51.9% | 2026-08-14T13:10:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `KITE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `KMNO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `KNC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `KOMA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `KSM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LAB` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LAYER` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LDO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LIGHT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LINEA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LINK` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LISTA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LIT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LPT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LQTY` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LRC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LSK` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LTC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LUMIA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LUNC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `LYN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `M` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MAGIC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MAGMA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MANA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MANTA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MANTRA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MARSCOIN` | 43.54 | 12,539 | 79.2% | 2026-07-30T13:40:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MASKSOL` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `MAV` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MAVIA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ME` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MEGA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MELANIA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MEME` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MEMEROBINHOOD` | 7.73 | 2,225 | 14.1% | 2026-09-04T09:10:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MERL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MET` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `METIS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MEW` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MIRA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MITO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MMT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MNT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MOCA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MOO` | 10.86 | 3,128 | 19.8% | 2026-09-01T05:55:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1288 |
| `MOODENG` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MORPHO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MOVE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MOVR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MTL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MUBARAK` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `MUSEBOOK` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `MYX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `NAORIS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `NEAR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `NEIROCTO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `NEO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `NES` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `NEWT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `NEX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `NIL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `NMR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `NOM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `NOT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `NPC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1878 |
| `NXPC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `O` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `OFC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `OG` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `OGN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `OKB` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `OL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ON` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ONDO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ONE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ONG` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ONT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `OP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `OPG` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `OPN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ORBIO` | 8.66 | 2,495 | 15.8% | 2026-09-03T10:40:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1288 |
| `ORCA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ORDER` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ORDI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `OTC` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `PAID` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `PAPER` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `PARTI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PEAQ` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PENDLE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PENGU` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PEOPLE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PEPE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PHA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PIEVERSE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PIPPIN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PIXEL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PLAY` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PLUME` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PNUT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `POD` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `POL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `POLYX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PONS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `POPCAT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PORTAL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `POWER` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `POWR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PRL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PROM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PROVE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PTB` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PUFFER` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PUNDIX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `PYTH` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `Q` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `QNT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `QTUM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RARE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RAVE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RAY` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RECALL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RED` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RENDER` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RESOLV` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `REZ` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RHEA` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `RIF` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RIVER` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RLC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RLS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ROBO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RON` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1877 |
| `RPL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RSR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RUNE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `RVN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `S` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SAFE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SAGA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SAHARA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SAND` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SANTOS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SAPIEN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SATS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SCR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SEI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SENT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SFP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SHELL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SHIB` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SHROOM` | 5.85 | 1,685 | 10.6% | 2026-09-06T06:10:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1288 |
| `SI` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `SIGN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SIREN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SKL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SKR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SKY` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SKYAI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SLP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SLX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SNX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SOCK` | 3.87 | 1,114 | 7.0% | 2026-09-08T05:45:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1288 |
| `SOL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SOLV` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SOMI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SONIC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SOPH` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SPACE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SPELL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SPK` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SQD` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SSV` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `STABLE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `STANDARD` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `STAR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `STBL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `STEEM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `STO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `STONK` | 5.46 | 1,571 | 9.9% | 2026-09-06T15:40:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `STORJ` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `STRK` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `STX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SUI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SUN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SUPER` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SUSHI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SWARM` | 22.59 | 6,506 | 41.1% | 2026-08-20T12:25:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1878 |
| `SWARMS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SXT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SYN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `SYRUP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `T` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TAC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TAG` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TAIKO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TAKE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TAO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TFUEL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1878 |
| `THE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `THETA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TIA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TLM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TMX` | 17.69 | 5,094 | 32.2% | 2026-08-25T10:05:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TNSR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TOSHI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TOWNS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TRADOOR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TRB` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TREAD` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `TREE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TRIA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TRUST` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TRUTH` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TRX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TST` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TURBO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TURTLE` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TUT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `TWT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `UAI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `UB` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `UBIK` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `UMA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `UNI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `UP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `UPROBINHOOD` | 31.89 | 9,185 | 58.0% | 2026-08-11T05:10:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1878 |
| `US` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `USDC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `USELESS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `USTC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `USUAL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `VANA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `VELO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `VELODROME` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `VELVET` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `VET` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `VIRTUAL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `VTHO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `VVV` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `W` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `WAL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `WAVES` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `WAXP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `WCT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `WET` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `WHUF` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `WIF` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `WLD` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `WLFI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `WOO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XAI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XAN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XCN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XDC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XDP` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `XEC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XLM` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XMR` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XNY` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XPIN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XPL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XRP` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XTZ` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XVG` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `XVS` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `YB` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `YFI` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `YGG` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ZAMA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ZBCN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1877 |
| `ZBT` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ZC` | 0.00 | 0 | 0.0% | - | - | 0 | `insufficient` | rows<1 day |
| `ZCAT` | 4.72 | 1,360 | 8.6% | 2026-09-07T09:15:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ZEN` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ZEREBRO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ZEST` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ZETA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ZIG` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1878 |
| `ZIL` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ZKC` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ZORA` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ZRO` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ZRX` | 55.00 | 15,840 | 100.0% | 2026-07-19T02:35:00Z | 2026-09-12T02:30:00Z | 0 | `complete` | none |
| `ZZZ` | 5.85 | 1,685 | 10.6% | 2026-09-06T06:10:00Z | 2026-09-12T02:30:00Z | 0 | `partial` | stale_lag_bars=1288 |

### 8.3 Funding observations — per coin

| coin | rows | first | last | span d | median gap s | p95 gap s | coverage of archive span |
|---|---|---|---|---|---|---|---|
| `0G` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `1000000BABYDOGE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `1000000MOG` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `1000BONK` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `1000BTT` | *no file* | - | - | - | - | - | - |
| `1000RATS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `1INCH` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `2Z` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `4` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `A` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AAVE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ACE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ACH` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ACT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ACU` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ADA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AERO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AEVO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AGENCY` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AGI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AGT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AIA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AIN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AIO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AIOT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AIOZ` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AIXBT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AKE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AKT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ALCH` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ALGO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ALICE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ALL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ALLINU` | *no file* | - | - | - | - | - | - |
| `ALLO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ALPINE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ALT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ANIME` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ANKR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ANSEM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `APE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `APEX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `API3` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `APM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `APR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `APT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ARB` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ARCSOL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ARGUS` | 166 | 2026-10-15T22:10:23Z | 2026-10-20T06:16:25Z | 4.338 | 1377 | 3513 | 4.365% |
| `ARIA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ARK` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ARKM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ARPA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ASR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ASTER` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ASTR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ATH` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ATOM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AUCTION` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AUDIO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AURORA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AVA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AVAAI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AVAX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AVNT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AWE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AXL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `AXS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `B` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `B2` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `B3` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BABY` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BAN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BANANA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BANANAS31` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BAND` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BANK` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BARD` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BAS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BASECAT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BASED` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BAT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BATON` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BB` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BCH` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BEAMX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BEAT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BEL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BERA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BICO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BIGTIME` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BIO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BIRB` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BLEND` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BLESS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BLUAI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BLUR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BMT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BNB` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BNT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BOBA` | *no file* | - | - | - | - | - | - |
| `BOME` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BONER` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.089 | 760 | 972 | 0.089% |
| `BP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BRETT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BROCCOLI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BROCCOLIF3B` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BSB` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BSV` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BTC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BTR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BTW` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `BULLA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `C` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `C98` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CAKE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CAP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CARV` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CASHCAT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CAT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CATE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CATI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CELO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CELR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CENT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CETUS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CFG` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CFX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CGPT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CHEEMS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CHILLGUY` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CHIP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CHR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CHZ` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CKB` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CLANKER` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CLAUDIA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CLAUS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CLO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CNPY` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `COAI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `COLLECT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `COMP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `COOKIE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `COOL` | *no file* | - | - | - | - | - | - |
| `CORE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `COTI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `COW` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CRO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CROSS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CRV` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CTC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CTK` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CTR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CTSI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CVC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CVX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CYBER` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `CYS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DATA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DBR` | *no file* | - | - | - | - | - | - |
| `DEBIT` | *no file* | - | - | - | - | - | - |
| `DEEP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DELTA` | *no file* | - | - | - | - | - | - |
| `DEXE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DGAI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DIA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DNA` | 167 | 2026-10-16T07:54:56Z | 2026-10-20T11:36:25Z | 4.154 | 1428 | 3430 | 4.176% |
| `DODO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DOGE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DOGS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DOLO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DOOD` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DOPE` | 157 | 2026-10-16T09:59:43Z | 2026-10-20T11:36:25Z | 4.067 | 1454 | 3915 | 4.088% |
| `DOS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DOT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DRIFT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DYDX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `DYM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `EACC` | 166 | 2026-10-15T22:10:23Z | 2026-10-20T06:16:25Z | 4.338 | 1377 | 3513 | 4.365% |
| `EDEN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `EDGE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `EDU` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `EIGEN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ELSA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ENA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ENJ` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ENS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ENSO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `EPIC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ERA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ESP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ESPORTS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ETC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ETH` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ETHFI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ETHW` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `EUL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `EVAA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `F` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FARTCOIN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FET` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FF` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FHE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FIDA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FIGHT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FLOCK` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FLOKI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FLOW` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FLR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FLUID` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FLUX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FOGO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FOLKS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FONE` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.089 | 760 | 972 | 0.089% |
| `FORM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `FRAX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `G` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GALA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GENIUS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GEOD` | *no file* | - | - | - | - | - | - |
| `GIGGLE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GLM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GMT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GMX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GOAT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GPS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GRAM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GRASS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GRIFFAIN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GRT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GRVT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GTC` | 13 | 2026-10-20T06:16:25Z | 2026-10-20T11:36:25Z | 0.222 | 1532 | 2810 | 0.219% |
| `GUA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GUN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `GWEI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `H` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HAEDAL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HANA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HBAR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HEI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HEMI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HIVE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HMSTR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HNT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HOLO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HOME` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HOOKED` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HOT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HUMA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HYPE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `HYPER` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ICNT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ICP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ID` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `IDOL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ILV` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `IMX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `IN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `INIT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `INJ` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `INX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `IO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `IOST` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `IOTA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `IOTX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `IRYS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `JASMY` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `JCT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `JEANPHIL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `JELLYJELLY` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `JOE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `JST` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `JTO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `JUP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `KAIA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `KAITO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `KAS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `KAT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `KAVA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `KERNEL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `KGEN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `KII` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `KITE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `KMNO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `KNC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `KOMA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `KSM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LAB` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LAYER` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LDO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LIGHT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LINEA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LINK` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LISTA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LIT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LPT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LQTY` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LRC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LSK` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LTC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LUMIA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LUNC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `LYN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `M` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MAGIC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MAGMA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MANA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MANTA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MANTRA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MARSCOIN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MASKSOL` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.089 | 760 | 972 | 0.089% |
| `MAV` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MAVIA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ME` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MEGA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MELANIA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MEME` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MEMEROBINHOOD` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MERL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MET` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `METIS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MEW` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MIRA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MITO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MMT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MNT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MOCA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MOO` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.089 | 760 | 972 | 0.089% |
| `MOODENG` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MORPHO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MOVE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MOVR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MTL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MUBARAK` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `MUSEBOOK` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.089 | 760 | 972 | 0.089% |
| `MYX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `NAORIS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `NEAR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `NEIROCTO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `NEO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `NES` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `NEWT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `NEX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `NIL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `NMR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `NOM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `NOT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `NPC` | *no file* | - | - | - | - | - | - |
| `NXPC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `O` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `OFC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `OG` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `OGN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `OKB` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `OL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ON` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ONDO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ONE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ONG` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ONT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `OP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `OPG` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `OPN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ORBIO` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.089 | 760 | 972 | 0.089% |
| `ORCA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ORDER` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ORDI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `OTC` | 166 | 2026-10-15T22:10:23Z | 2026-10-20T06:16:25Z | 4.338 | 1377 | 3513 | 4.365% |
| `PAID` | 166 | 2026-10-15T22:10:23Z | 2026-10-20T06:16:25Z | 4.338 | 1377 | 3513 | 4.365% |
| `PAPER` | 34 | 2026-10-19T07:51:47Z | 2026-10-20T00:10:57Z | 0.680 | 1626 | 2850 | 0.684% |
| `PARTI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PEAQ` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PENDLE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PENGU` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PEOPLE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PEPE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PHA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PIEVERSE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PIPPIN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PIXEL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PLAY` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PLUME` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PNUT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `POD` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.089 | 760 | 972 | 0.089% |
| `POL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `POLYX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PONS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `POPCAT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PORTAL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `POWER` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `POWR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PRL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PROM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PROVE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PTB` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PUFFER` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PUNDIX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `PYTH` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `Q` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `QNT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `QTUM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RARE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RAVE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RAY` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RECALL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RED` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RENDER` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RESOLV` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `REZ` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RHEA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RIF` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RIVER` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RLC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RLS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ROBO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RON` | *no file* | - | - | - | - | - | - |
| `RPL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RSR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RUNE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `RVN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `S` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SAFE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SAGA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SAHARA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SAND` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SANTOS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SAPIEN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SATS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SCR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SEI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SENT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SFP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SHELL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SHIB` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SHROOM` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.089 | 760 | 972 | 0.089% |
| `SI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SIGN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SIREN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SKL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SKR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SKY` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SKYAI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SLP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SLX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SNX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SOCK` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.089 | 760 | 972 | 0.089% |
| `SOL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SOLV` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SOMI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SONIC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SOPH` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SPACE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SPELL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SPK` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SQD` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SSV` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `STABLE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `STANDARD` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.089 | 760 | 972 | 0.089% |
| `STAR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `STBL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `STEEM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `STO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `STONK` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `STORJ` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `STRK` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `STX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SUI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SUN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SUPER` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SUSHI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SWARM` | *no file* | - | - | - | - | - | - |
| `SWARMS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SXT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SYN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `SYRUP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `T` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TAC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TAG` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TAIKO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TAKE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TAO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TFUEL` | *no file* | - | - | - | - | - | - |
| `THE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `THETA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TIA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TLM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TMX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TNSR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TOSHI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TOWNS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TRADOOR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TRB` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TREAD` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TREE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TRIA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TRUST` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TRUTH` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TRX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TST` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TURBO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TURTLE` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TUT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `TWT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `UAI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `UB` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `UBIK` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.089 | 760 | 972 | 0.089% |
| `UMA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `UNI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `UP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `UPROBINHOOD` | *no file* | - | - | - | - | - | - |
| `US` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `USDC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `USELESS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `USTC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `USUAL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `VANA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `VELO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `VELODROME` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `VELVET` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `VET` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `VIRTUAL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `VTHO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `VVV` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `W` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `WAL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `WAVES` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `WAXP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `WCT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `WET` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `WHUF` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `WIF` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `WLD` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `WLFI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `WOO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XAI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XAN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XCN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XDC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XDP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XEC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XLM` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XMR` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XNY` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XPIN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XPL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XRP` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XTZ` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XVG` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `XVS` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `YB` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `YFI` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `YGG` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZAMA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZBCN` | *no file* | - | - | - | - | - | - |
| `ZBT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZCAT` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZEN` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZEREBRO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZEST` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZETA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZIG` | *no file* | - | - | - | - | - | - |
| `ZIL` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZKC` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZORA` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZRO` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZRX` | 178 | 2026-10-15T22:10:23Z | 2026-10-20T11:36:25Z | 4.560 | 1387 | 3901 | 4.584% |
| `ZZZ` | 11 | 2026-10-15T22:10:23Z | 2026-10-16T00:17:55Z | 0.089 | 760 | 972 | 0.089% |

### 8.4 Funding settlements — per coin

| coin | rows | first | last | span d | cycle_hours | transitions |
|---|---|---|---|---|---|---|
| `0G` | 3,732 | 2025-09-17T20:00:00Z | 2026-10-19T12:00:00Z | 396.67 | {1, 4} | 4 |
| `1000000BABYDOGE` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `1000000MOG` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `1000BONK` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `1000BTT` | *no file* | - | - | - | - | - |
| `1000RATS` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `1INCH` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `2Z` | 2,315 | 2025-10-02T16:00:00Z | 2026-10-19T12:00:00Z | 381.83 | {1, 4} | 2 |
| `4` | 2,293 | 2025-10-02T12:00:00Z | 2026-10-19T12:00:00Z | 382.00 | {4} | 0 |
| `A` | 3,062 | 2025-05-27T08:00:00Z | 2026-10-19T12:00:00Z | 510.17 | {4} | 0 |
| `AAVE` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `ACE` | 3,503 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 4 |
| `ACH` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `ACT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ACU` | 1,633 | 2026-01-20T12:00:00Z | 2026-10-19T12:00:00Z | 272.00 | {4} | 0 |
| `ADA` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `AERO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `AEVO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `AGENCY` | 92 | 2026-10-04T08:00:00Z | 2026-10-19T12:00:00Z | 15.17 | {4} | 0 |
| `AGI` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `AGT` | 3,127 | 2025-05-16T12:00:00Z | 2026-10-19T12:00:00Z | 521.00 | {4} | 0 |
| `AIA` | 3,322 | 2025-09-18T12:00:00Z | 2026-10-19T12:00:00Z | 396.00 | {1, 4} | 2 |
| `AIN` | 2,827 | 2025-07-05T12:00:00Z | 2026-10-19T12:00:00Z | 471.00 | {4} | 0 |
| `AIO` | 2,593 | 2025-08-13T12:00:00Z | 2026-10-19T12:00:00Z | 432.00 | {4} | 0 |
| `AIOT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `AIOZ` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `AIXBT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `AKE` | 2,571 | 2025-08-21T16:00:00Z | 2026-10-19T12:00:00Z | 423.83 | {1, 4} | 2 |
| `AKT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ALCH` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ALGO` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `ALICE` | 2,386 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `ALL` | 1,317 | 2025-08-06T16:00:00Z | 2026-10-19T08:00:00Z | 438.67 | {8} | 0 |
| `ALLINU` | *no file* | - | - | - | - | - |
| `ALLO` | 2,053 | 2025-11-11T12:00:00Z | 2026-10-19T12:00:00Z | 342.00 | {4} | 0 |
| `ALPINE` | 3,217 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 1 |
| `ALT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ANIME` | 3,526 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 4 |
| `ANKR` | 2,301 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `ANSEM` | 680 | 2026-06-28T08:00:00Z | 2026-10-19T12:00:00Z | 113.17 | {4} | 0 |
| `APE` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `APEX` | 2,819 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `API3` | 4,189 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 4 |
| `APM` | 188 | 2026-09-18T08:00:00Z | 2026-10-19T12:00:00Z | 31.17 | {4} | 0 |
| `APR` | 2,167 | 2025-10-23T12:00:00Z | 2026-10-19T12:00:00Z | 361.00 | {4} | 0 |
| `APT` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `AR` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `ARB` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `ARCSOL` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ARGUS` | 201 | 2026-09-16T04:00:00Z | 2026-10-19T12:00:00Z | 33.33 | {4} | 0 |
| `ARIA` | 2,660 | 2025-08-21T16:00:00Z | 2026-10-19T12:00:00Z | 423.83 | {1, 4} | 2 |
| `ARK` | 3,451 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 6 |
| `ARKM` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `ARPA` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `ASR` | 3,217 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 1 |
| `ASTER` | 2,373 | 2025-09-19T04:00:00Z | 2026-10-19T12:00:00Z | 395.33 | {4} | 0 |
| `ASTR` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `AT` | 2,161 | 2025-10-24T12:00:00Z | 2026-10-19T12:00:00Z | 360.00 | {4} | 0 |
| `ATH` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ATOM` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `AUCTION` | 3,308 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `AUDIO` | 2,581 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 1 |
| `AURORA` | 211 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 1 |
| `AVA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `AVAAI` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `AVAX` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `AVNT` | 2,430 | 2025-09-09T16:00:00Z | 2026-10-19T12:00:00Z | 404.83 | {4} | 0 |
| `AWE` | 3,097 | 2025-05-21T12:00:00Z | 2026-10-19T12:00:00Z | 516.00 | {4} | 0 |
| `AXL` | 3,936 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `AXS` | 2,604 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `B` | 3,097 | 2025-05-21T12:00:00Z | 2026-10-19T12:00:00Z | 516.00 | {4} | 0 |
| `B2` | 3,223 | 2025-04-30T12:00:00Z | 2026-10-19T12:00:00Z | 537.00 | {4} | 0 |
| `B3` | 4,456 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 2, 4} | 11 |
| `BABY` | 3,294 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `BAN` | 3,311 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {2, 4} | 2 |
| `BANANA` | 3,400 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `BANANAS31` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `BAND` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `BANK` | 3,349 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `BARD` | 3,028 | 2025-09-18T12:00:00Z | 2026-10-19T12:00:00Z | 396.00 | {1, 4} | 2 |
| `BAS` | 2,545 | 2025-08-21T12:00:00Z | 2026-10-19T12:00:00Z | 424.00 | {4} | 0 |
| `BASECAT` | 379 | 2026-08-17T12:00:00Z | 2026-10-19T12:00:00Z | 63.00 | {4} | 0 |
| `BASED` | 1,219 | 2026-03-30T12:00:00Z | 2026-10-19T12:00:00Z | 203.00 | {4} | 0 |
| `BAT` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `BATON` | 206 | 2026-09-15T08:00:00Z | 2026-10-19T12:00:00Z | 34.17 | {4} | 0 |
| `BB` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `BCH` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `BEAMX` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `BEAT` | 2,449 | 2025-11-01T12:00:00Z | 2026-10-19T12:00:00Z | 352.00 | {1, 4} | 2 |
| `BEL` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `BERA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `BICO` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `BIGTIME` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `BIO` | 4,030 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 4 |
| `BIRB` | 1,705 | 2026-01-28T12:00:00Z | 2026-10-19T12:00:00Z | 264.00 | {1, 4} | 4 |
| `BLEND` | 1,068 | 2026-04-24T16:00:00Z | 2026-10-19T12:00:00Z | 177.83 | {4} | 0 |
| `BLESS` | 2,347 | 2025-09-23T12:00:00Z | 2026-10-19T12:00:00Z | 391.00 | {4} | 0 |
| `BLUAI` | 2,179 | 2025-10-21T12:00:00Z | 2026-10-19T12:00:00Z | 363.00 | {4} | 0 |
| `BLUR` | 2,270 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `BMT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `BNB` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `BNT` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `BOBA` | *no file* | - | - | - | - | - |
| `BOME` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `BONER` | *no file* | - | - | - | - | - |
| `BP` | 1,303 | 2026-03-16T12:00:00Z | 2026-10-19T12:00:00Z | 217.00 | {4} | 0 |
| `BR` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `BRETT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `BROCCOLI` | 3,279 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `BROCCOLIF3B` | 4,748 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 4 |
| `BSB` | 1,375 | 2026-03-04T12:00:00Z | 2026-10-19T12:00:00Z | 229.00 | {4} | 0 |
| `BSV` | 3,168 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 1 |
| `BTC` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `BTR` | 2,527 | 2025-08-27T12:00:00Z | 2026-10-19T12:00:00Z | 418.00 | {1, 4} | 2 |
| `BTW` | 1,241 | 2026-03-02T12:00:00Z | 2026-10-19T12:00:00Z | 231.00 | {4} | 0 |
| `BULLA` | 3,184 | 2025-06-06T12:00:00Z | 2026-10-19T12:00:00Z | 500.00 | {1, 4} | 6 |
| `C` | 2,896 | 2025-07-15T08:00:00Z | 2026-10-19T12:00:00Z | 461.17 | {1, 4} | 2 |
| `C98` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `CAKE` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `CAP` | 690 | 2026-06-26T16:00:00Z | 2026-10-19T12:00:00Z | 114.83 | {4} | 0 |
| `CARV` | 4,261 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 6 |
| `CASHCAT` | 618 | 2026-07-08T16:00:00Z | 2026-10-19T12:00:00Z | 102.83 | {4} | 0 |
| `CAT` | 3,438 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `CATE` | 506 | 2026-07-27T08:00:00Z | 2026-10-19T12:00:00Z | 84.17 | {4} | 0 |
| `CATI` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `CC` | 2,118 | 2025-10-31T16:00:00Z | 2026-10-19T12:00:00Z | 352.83 | {4} | 0 |
| `CELO` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `CELR` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `CENT` | 43 | 2026-10-12T12:00:00Z | 2026-10-19T12:00:00Z | 7.00 | {4} | 0 |
| `CETUS` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `CFG` | 1,412 | 2026-02-26T08:00:00Z | 2026-10-19T12:00:00Z | 235.17 | {4} | 0 |
| `CFX` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `CGPT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `CHEEMS` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `CHILLGUY` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `CHIP` | 1,118 | 2026-04-16T08:00:00Z | 2026-10-19T12:00:00Z | 186.17 | {4} | 0 |
| `CHR` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `CHZ` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `CKB` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `CLANKER` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `CLAUDIA` | 44 | 2026-10-12T08:00:00Z | 2026-10-19T12:00:00Z | 7.17 | {4} | 0 |
| `CLAUS` | 68 | 2026-10-08T08:00:00Z | 2026-10-19T12:00:00Z | 11.17 | {4} | 0 |
| `CLO` | 2,326 | 2025-10-14T12:00:00Z | 2026-10-19T12:00:00Z | 370.00 | {1, 4} | 2 |
| `CNPY` | 290 | 2026-09-07T16:00:00Z | 2026-10-19T12:00:00Z | 41.83 | {1, 2, 4} | 5 |
| `COAI` | 3,126 | 2025-09-25T08:00:00Z | 2026-10-19T12:00:00Z | 389.17 | {1, 4} | 2 |
| `COLLECT` | 1,765 | 2025-12-29T12:00:00Z | 2026-10-19T12:00:00Z | 294.00 | {4} | 0 |
| `COMP` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `COOKIE` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `COOL` | *no file* | - | - | - | - | - |
| `CORE` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `COTI` | 2,501 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `COW` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `CP` | 282 | 2026-09-02T16:00:00Z | 2026-10-19T12:00:00Z | 46.83 | {4} | 0 |
| `CRO` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `CROSS` | 2,833 | 2025-07-04T12:00:00Z | 2026-10-19T12:00:00Z | 472.00 | {4} | 0 |
| `CRV` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `CT` | 115 | 2026-09-30T12:00:00Z | 2026-10-19T12:00:00Z | 19.00 | {4} | 0 |
| `CTC` | 2,921 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 3 |
| `CTK` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `CTR` | 876 | 2026-05-26T16:00:00Z | 2026-10-19T12:00:00Z | 145.83 | {4} | 0 |
| `CTSI` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `CVC` | 3,692 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 5 |
| `CVX` | 3,169 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 2 |
| `CYBER` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `CYS` | 1,873 | 2025-12-11T12:00:00Z | 2026-10-19T12:00:00Z | 312.00 | {4} | 0 |
| `DATA` | 755 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 1 |
| `DBR` | *no file* | - | - | - | - | - |
| `DEBIT` | *no file* | - | - | - | - | - |
| `DEEP` | 3,208 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 2 |
| `DELTA` | *no file* | - | - | - | - | - |
| `DEXE` | 1,016 | 2026-06-23T04:00:00Z | 2026-10-19T12:00:00Z | 118.33 | {1, 4} | 2 |
| `DGAI` | 332 | 2026-08-25T08:00:00Z | 2026-10-19T12:00:00Z | 55.17 | {4} | 0 |
| `DIA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `DNA` | 19 | 2026-10-16T12:00:00Z | 2026-10-19T12:00:00Z | 3.00 | {4} | 0 |
| `DODO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `DOGE` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `DOGS` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `DOLO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `DOOD` | 4,360 | 2025-05-09T16:00:00Z | 2026-10-19T12:00:00Z | 527.83 | {1, 4} | 2 |
| `DOPE` | 19 | 2026-10-16T12:00:00Z | 2026-10-19T12:00:00Z | 3.00 | {4} | 0 |
| `DOS` | 421 | 2026-08-11T08:00:00Z | 2026-10-19T12:00:00Z | 69.17 | {2, 4} | 2 |
| `DOT` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `DRIFT` | 3,396 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `DYDX` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `DYM` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `EACC` | 138 | 2026-09-26T16:00:00Z | 2026-10-19T12:00:00Z | 22.83 | {4} | 0 |
| `EDEN` | 2,305 | 2025-09-30T12:00:00Z | 2026-10-19T12:00:00Z | 384.00 | {4} | 0 |
| `EDGE` | 2,260 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `EDU` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `EIGEN` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ELSA` | 1,695 | 2026-01-20T12:00:00Z | 2026-10-19T12:00:00Z | 272.00 | {1, 4} | 2 |
| `ENA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ENJ` | 2,539 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 4 |
| `ENS` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `ENSO` | 2,529 | 2025-10-14T12:00:00Z | 2026-10-19T12:00:00Z | 370.00 | {1, 4} | 4 |
| `EPIC` | 3,290 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `ERA` | 3,450 | 2025-07-17T16:00:00Z | 2026-10-19T15:00:00Z | 458.96 | {1, 4} | 7 |
| `ESP` | 1,681 | 2026-02-10T12:00:00Z | 2026-10-19T12:00:00Z | 251.00 | {1, 4} | 2 |
| `ESPORTS` | 3,127 | 2025-07-19T12:00:00Z | 2026-10-19T12:00:00Z | 457.00 | {1, 4} | 6 |
| `ETC` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `ETH` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `ETHFI` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ETHW` | 3,168 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 1 |
| `EUL` | 2,456 | 2025-09-05T08:00:00Z | 2026-10-19T12:00:00Z | 409.17 | {4} | 0 |
| `EVAA` | 2,287 | 2025-10-03T12:00:00Z | 2026-10-19T12:00:00Z | 381.00 | {4} | 0 |
| `F` | 3,373 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 3 |
| `FARTCOIN` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `FET` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `FF` | 2,310 | 2025-09-29T16:00:00Z | 2026-10-19T12:00:00Z | 384.83 | {4} | 0 |
| `FHE` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `FIDA` | 3,287 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `FIGHT` | 1,621 | 2026-01-22T12:00:00Z | 2026-10-19T12:00:00Z | 270.00 | {4} | 0 |
| `FLOCK` | 5,006 | 2025-06-02T00:00:00Z | 2026-10-19T12:00:00Z | 504.50 | {1, 4} | 3 |
| `FLOKI` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `FLOW` | 3,093 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `FLR` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `FLUID` | 2,357 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `FLUX` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `FOGO` | 1,692 | 2026-01-10T16:00:00Z | 2026-10-19T12:00:00Z | 281.83 | {4} | 0 |
| `FOLKS` | 2,439 | 2025-11-06T16:00:00Z | 2026-10-19T12:00:00Z | 346.83 | {1, 4} | 2 |
| `FONE` | *no file* | - | - | - | - | - |
| `FORM` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `FRAX` | 1,663 | 2026-01-15T12:00:00Z | 2026-10-19T12:00:00Z | 277.00 | {4} | 0 |
| `G` | 3,372 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 4 |
| `GALA` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `GENIUS` | 1,131 | 2026-04-14T04:00:00Z | 2026-10-19T12:00:00Z | 188.33 | {4} | 0 |
| `GEOD` | *no file* | - | - | - | - | - |
| `GIGGLE` | 2,353 | 2025-09-22T12:00:00Z | 2026-10-19T12:00:00Z | 392.00 | {4} | 0 |
| `GLM` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `GMT` | 2,562 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `GMX` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `GOAT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `GP` | 931 | 2025-07-18T12:00:00Z | 2026-10-19T12:00:00Z | 458.00 | {4} | 0 |
| `GPS` | 3,749 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `GRAM` | 756 | 2026-06-15T16:00:00Z | 2026-10-19T12:00:00Z | 125.83 | {4} | 0 |
| `GRASS` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `GRIFFAIN` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `GRT` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `GRVT` | 486 | 2026-07-30T16:00:00Z | 2026-10-19T12:00:00Z | 80.83 | {4} | 0 |
| `GTC` | *no file* | - | - | - | - | - |
| `GUA` | 2,063 | 2025-11-27T12:00:00Z | 2026-10-19T12:00:00Z | 326.00 | {1, 4} | 6 |
| `GUN` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `GWEI` | 1,890 | 2026-01-21T16:00:00Z | 2026-10-19T12:00:00Z | 270.83 | {1, 4} | 2 |
| `H` | 3,553 | 2025-06-25T12:00:00Z | 2026-10-19T12:00:00Z | 481.00 | {1, 4} | 6 |
| `HAEDAL` | 3,228 | 2025-04-29T16:00:00Z | 2026-10-19T12:00:00Z | 537.83 | {4} | 0 |
| `HANA` | 3,535 | 2025-09-26T12:00:00Z | 2026-10-19T12:00:00Z | 388.00 | {1, 4} | 4 |
| `HBAR` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `HEI` | 3,260 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `HEMI` | 2,498 | 2025-08-29T08:00:00Z | 2026-10-19T12:00:00Z | 416.17 | {4} | 0 |
| `HIVE` | 3,329 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `HMSTR` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `HNT` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `HOLO` | 2,419 | 2025-09-11T12:00:00Z | 2026-10-19T12:00:00Z | 403.00 | {4} | 0 |
| `HOME` | 3,322 | 2025-06-10T12:00:00Z | 2026-10-19T12:00:00Z | 496.00 | {1, 4} | 2 |
| `HOOKED` | 92 | 2026-10-04T08:00:00Z | 2026-10-19T12:00:00Z | 15.17 | {4} | 0 |
| `HOT` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `HUMA` | 3,067 | 2025-05-26T12:00:00Z | 2026-10-19T12:00:00Z | 511.00 | {4} | 0 |
| `HYPE` | 3,078 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 5 |
| `HYPER` | 3,396 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `ICNT` | 2,839 | 2025-07-03T12:00:00Z | 2026-10-19T12:00:00Z | 473.00 | {4} | 0 |
| `ICP` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `ID` | 2,124 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `IDOL` | 2,971 | 2025-06-11T12:00:00Z | 2026-10-19T12:00:00Z | 495.00 | {4} | 0 |
| `ILV` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `IMX` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `IN` | 2,649 | 2025-08-07T12:00:00Z | 2026-10-19T12:00:00Z | 438.00 | {1, 4} | 2 |
| `INIT` | 3,304 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `INJ` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `INX` | 1,571 | 2026-01-30T20:00:00Z | 2026-10-19T12:00:00Z | 261.67 | {4} | 0 |
| `IO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `IOST` | 1,845 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `IOTA` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `IOTX` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `IRYS` | 2,038 | 2025-11-25T12:00:00Z | 2026-10-19T12:00:00Z | 328.00 | {1, 4} | 2 |
| `JASMY` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `JCT` | 2,059 | 2025-11-10T12:00:00Z | 2026-10-19T12:00:00Z | 343.00 | {4} | 0 |
| `JEANPHIL` | 60 | 2026-10-09T16:00:00Z | 2026-10-19T12:00:00Z | 9.83 | {4} | 0 |
| `JELLYJELLY` | 3,259 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `JOE` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `JST` | 3,241 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 1 |
| `JTO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `JUP` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `KAIA` | 3,331 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `KAITO` | 3,471 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `KAS` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `KAT` | 1,388 | 2026-03-02T08:00:00Z | 2026-10-19T12:00:00Z | 231.17 | {4} | 0 |
| `KAVA` | 3,304 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `KERNEL` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `KGEN` | 2,262 | 2025-10-07T16:00:00Z | 2026-10-19T12:00:00Z | 376.83 | {4} | 0 |
| `KII` | 396 | 2026-08-14T16:00:00Z | 2026-10-19T12:00:00Z | 65.83 | {4} | 0 |
| `KITE` | 2,373 | 2025-10-29T12:00:00Z | 2026-10-19T12:00:00Z | 355.00 | {1, 4} | 2 |
| `KMNO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `KNC` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `KOMA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `KSM` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `LA` | 5,006 | 2025-08-24T00:00:00Z | 2026-10-19T12:00:00Z | 421.50 | {1, 4} | 5 |
| `LAB` | 2,605 | 2025-10-15T08:00:00Z | 2026-10-19T12:00:00Z | 369.17 | {1, 4} | 4 |
| `LAYER` | 5,006 | 2025-05-17T11:00:00Z | 2026-10-19T12:00:00Z | 520.04 | {1, 4} | 5 |
| `LDO` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `LIGHT` | 2,389 | 2025-09-27T12:00:00Z | 2026-10-19T12:00:00Z | 387.00 | {1, 4} | 2 |
| `LINEA` | 2,479 | 2025-09-01T12:00:00Z | 2026-10-19T12:00:00Z | 413.00 | {4} | 0 |
| `LINK` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `LISTA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `LIT` | 1,942 | 2025-12-22T18:00:00Z | 2026-10-19T12:00:00Z | 300.75 | {1, 4} | 1 |
| `LPT` | 4,387 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 4 |
| `LQTY` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `LRC` | 2,706 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {1, 4, 8} | 5 |
| `LSK` | 4,397 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 4 |
| `LTC` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `LUMIA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `LUNC` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `LYN` | 2,772 | 2025-10-06T08:00:00Z | 2026-10-19T12:00:00Z | 378.17 | {1, 4} | 6 |
| `M` | 3,946 | 2025-07-03T12:00:00Z | 2026-10-19T12:00:00Z | 473.00 | {1, 4} | 6 |
| `MAGIC` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `MAGMA` | 1,843 | 2025-12-16T12:00:00Z | 2026-10-19T12:00:00Z | 307.00 | {4} | 0 |
| `MANA` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `MANTA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `MANTRA` | 1,375 | 2026-03-04T12:00:00Z | 2026-10-19T12:00:00Z | 229.00 | {4} | 0 |
| `MARSCOIN` | 486 | 2026-07-30T16:00:00Z | 2026-10-19T12:00:00Z | 80.83 | {4} | 0 |
| `MASKSOL` | *no file* | - | - | - | - | - |
| `MAV` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `MAVIA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ME` | 4,440 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 6 |
| `MEGA` | 5,006 | 2025-11-11T11:00:00Z | 2026-10-19T12:00:00Z | 342.04 | {1, 4} | 1 |
| `MELANIA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `MEME` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `MEMEROBINHOOD` | 271 | 2026-09-04T12:00:00Z | 2026-10-19T12:00:00Z | 45.00 | {4} | 0 |
| `MERL` | 3,410 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 5 |
| `MET` | 2,245 | 2025-10-10T12:00:00Z | 2026-10-19T12:00:00Z | 374.00 | {4} | 0 |
| `METIS` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `MEW` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `MIRA` | 2,740 | 2025-09-26T16:00:00Z | 2026-10-19T12:00:00Z | 387.83 | {1, 4} | 6 |
| `MITO` | 2,502 | 2025-08-28T16:00:00Z | 2026-10-19T12:00:00Z | 416.83 | {4} | 0 |
| `MMT` | 2,143 | 2025-10-27T12:00:00Z | 2026-10-19T12:00:00Z | 357.00 | {4} | 0 |
| `MNT` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `MOCA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `MOO` | *no file* | - | - | - | - | - |
| `MOODENG` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `MORPHO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `MOVE` | 3,282 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `MOVR` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `MTL` | 1,842 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `MUBARAK` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `MUSEBOOK` | *no file* | - | - | - | - | - |
| `MYX` | 5,006 | 2025-08-18T00:00:00Z | 2026-10-19T12:00:00Z | 427.50 | {1, 4} | 3 |
| `NAORIS` | 2,670 | 2025-07-31T16:00:00Z | 2026-10-19T12:00:00Z | 444.83 | {4} | 0 |
| `NEAR` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `NEIROCTO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `NEO` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `NES` | 702 | 2026-06-24T16:00:00Z | 2026-10-19T12:00:00Z | 116.83 | {4} | 0 |
| `NEWT` | 3,567 | 2025-06-19T16:00:00Z | 2026-10-19T12:00:00Z | 486.83 | {1, 4} | 4 |
| `NEX` | 912 | 2026-05-20T16:00:00Z | 2026-10-19T12:00:00Z | 151.83 | {4} | 0 |
| `NIL` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `NMR` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `NOM` | 2,503 | 2025-10-01T08:00:00Z | 2026-10-19T12:00:00Z | 383.17 | {1, 4} | 2 |
| `NOT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `NPC` | *no file* | - | - | - | - | - |
| `NXPC` | 3,134 | 2025-05-15T08:00:00Z | 2026-10-19T12:00:00Z | 522.17 | {4} | 0 |
| `O` | 749 | 2026-06-17T20:00:00Z | 2026-10-19T12:00:00Z | 123.67 | {2, 4} | 2 |
| `OFC` | 1,159 | 2026-04-09T12:00:00Z | 2026-10-19T12:00:00Z | 193.00 | {4} | 0 |
| `OG` | 3,199 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 1 |
| `OGN` | 2,384 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `OKB` | 2,593 | 2025-08-13T12:00:00Z | 2026-10-19T12:00:00Z | 432.00 | {4} | 0 |
| `OL` | 3,019 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 3 |
| `ON` | 2,161 | 2025-10-24T12:00:00Z | 2026-10-19T12:00:00Z | 360.00 | {4} | 0 |
| `ONDO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ONE` | 1,935 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 4 |
| `ONG` | 3,662 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 4 |
| `ONT` | 2,982 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 4 |
| `OP` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `OPG` | 1,087 | 2026-04-21T12:00:00Z | 2026-10-19T12:00:00Z | 181.00 | {4} | 0 |
| `OPN` | 1,501 | 2026-02-11T12:00:00Z | 2026-10-19T12:00:00Z | 250.00 | {4} | 0 |
| `ORBIO` | *no file* | - | - | - | - | - |
| `ORCA` | 4,301 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 2, 4} | 8 |
| `ORDER` | 2,282 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `ORDI` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `OTC` | 158 | 2026-09-23T08:00:00Z | 2026-10-19T12:00:00Z | 26.17 | {4} | 0 |
| `PAID` | 201 | 2026-09-16T04:00:00Z | 2026-10-19T12:00:00Z | 33.33 | {4} | 0 |
| `PAPER` | 10 | 2026-10-19T06:00:00Z | 2026-10-19T15:00:00Z | 0.38 | {1} | 0 |
| `PARTI` | 3,268 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `PEAQ` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `PENDLE` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `PENGU` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `PEOPLE` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `PEPE` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `PHA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `PI` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `PIEVERSE` | 2,035 | 2025-11-14T12:00:00Z | 2026-10-19T12:00:00Z | 339.00 | {4} | 0 |
| `PIPPIN` | 4,576 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 6 |
| `PIXEL` | 3,276 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `PLAY` | 2,699 | 2025-07-31T12:00:00Z | 2026-10-19T12:00:00Z | 445.00 | {1, 4} | 2 |
| `PLUME` | 3,874 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `PNUT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `POD` | *no file* | - | - | - | - | - |
| `POL` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `POLYX` | 3,345 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `PONS` | 577 | 2026-07-15T12:00:00Z | 2026-10-19T12:00:00Z | 96.00 | {4} | 0 |
| `POPCAT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `PORTAL` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `POWER` | 2,095 | 2025-12-05T08:00:00Z | 2026-10-19T12:00:00Z | 318.17 | {1, 4} | 2 |
| `POWR` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `PRL` | 1,284 | 2026-03-25T12:00:00Z | 2026-10-19T12:00:00Z | 208.00 | {1, 4} | 2 |
| `PROM` | 3,305 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `PROVE` | 2,673 | 2025-08-05T16:00:00Z | 2026-10-19T12:00:00Z | 439.83 | {1, 4} | 2 |
| `PTB` | 2,489 | 2025-09-03T12:00:00Z | 2026-10-19T12:00:00Z | 411.00 | {1, 4} | 2 |
| `PUFFER` | 2,776 | 2025-04-26T16:00:00Z | 2026-10-19T15:00:00Z | 540.96 | {1, 4, 8} | 10 |
| `PUNDIX` | 3,224 | 2025-04-30T08:00:00Z | 2026-10-19T12:00:00Z | 537.17 | {4} | 0 |
| `PYTH` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `Q` | 2,474 | 2025-09-02T08:00:00Z | 2026-10-19T12:00:00Z | 412.17 | {4} | 0 |
| `QNT` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `QTUM` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `RARE` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `RAVE` | 2,075 | 2025-12-12T16:00:00Z | 2026-10-19T12:00:00Z | 310.83 | {1, 4} | 2 |
| `RAY` | 1,627 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 1 |
| `RE` | 745 | 2026-06-17T12:00:00Z | 2026-10-19T12:00:00Z | 124.00 | {4} | 0 |
| `RECALL` | 2,214 | 2025-10-15T16:00:00Z | 2026-10-19T12:00:00Z | 368.83 | {4} | 0 |
| `RED` | 3,645 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `RENDER` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `RESOLV` | 3,753 | 2025-06-10T16:00:00Z | 2026-10-19T12:00:00Z | 495.83 | {1, 4} | 4 |
| `REZ` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `RHEA` | 632 | 2025-07-30T16:00:00Z | 2026-10-19T12:00:00Z | 445.83 | {4} | 0 |
| `RIF` | 3,276 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `RIVER` | 3,523 | 2025-09-22T12:00:00Z | 2026-10-19T12:00:00Z | 392.00 | {1, 4} | 12 |
| `RLC` | 1,725 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `RLS` | 1,933 | 2025-12-01T12:00:00Z | 2026-10-19T12:00:00Z | 322.00 | {4} | 0 |
| `ROBO` | 1,416 | 2026-02-25T16:00:00Z | 2026-10-19T12:00:00Z | 235.83 | {4} | 0 |
| `RON` | *no file* | - | - | - | - | - |
| `RPL` | 3,346 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `RSR` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `RUNE` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `RVN` | 3,857 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 4 |
| `S` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `SAFE` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `SAGA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `SAHARA` | 3,716 | 2025-06-26T12:00:00Z | 2026-10-19T12:00:00Z | 480.00 | {1, 4} | 4 |
| `SAND` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `SANTOS` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `SAPIEN` | 2,549 | 2025-08-20T20:00:00Z | 2026-10-19T12:00:00Z | 424.67 | {4} | 0 |
| `SATS` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `SCR` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `SEI` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `SENT` | 2,513 | 2025-11-14T16:00:00Z | 2026-10-19T12:00:00Z | 338.83 | {1, 4} | 2 |
| `SFP` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `SHELL` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `SHIB` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `SHROOM` | *no file* | - | - | - | - | - |
| `SI` | 138 | 2026-09-26T16:00:00Z | 2026-10-19T12:00:00Z | 22.83 | {4} | 0 |
| `SIGN` | 5,006 | 2025-05-02T02:00:00Z | 2026-10-19T12:00:00Z | 535.42 | {1, 4} | 5 |
| `SIREN` | 3,714 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 4 |
| `SKL` | 2,796 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `SKR` | 1,876 | 2026-01-20T16:00:00Z | 2026-10-19T12:00:00Z | 271.83 | {1, 4} | 4 |
| `SKY` | 2,553 | 2025-08-20T04:00:00Z | 2026-10-19T12:00:00Z | 425.33 | {4} | 0 |
| `SKYAI` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `SLP` | 2,969 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 3 |
| `SLX` | 1,073 | 2026-05-25T16:00:00Z | 2026-10-19T12:00:00Z | 146.83 | {1, 4} | 2 |
| `SNX` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `SOCK` | *no file* | - | - | - | - | - |
| `SOL` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `SOLV` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `SOMI` | 2,521 | 2025-08-25T12:00:00Z | 2026-10-19T12:00:00Z | 420.00 | {4} | 0 |
| `SONIC` | 3,312 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {2, 4} | 2 |
| `SOPH` | 4,270 | 2025-05-28T16:00:00Z | 2026-10-19T12:00:00Z | 508.83 | {1, 4} | 6 |
| `SPACE` | 1,615 | 2026-01-23T12:00:00Z | 2026-10-19T12:00:00Z | 269.00 | {4} | 0 |
| `SPELL` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `SPK` | 2,935 | 2025-06-17T12:00:00Z | 2026-10-19T12:00:00Z | 489.00 | {4} | 0 |
| `SQD` | 3,656 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `SSV` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `STABLE` | 2,098 | 2025-11-06T16:00:00Z | 2026-10-19T12:00:00Z | 346.83 | {1, 4} | 2 |
| `STANDARD` | *no file* | - | - | - | - | - |
| `STAR` | 950 | 2026-05-14T08:00:00Z | 2026-10-19T12:00:00Z | 158.17 | {4} | 0 |
| `STBL` | 2,388 | 2025-09-16T16:00:00Z | 2026-10-19T12:00:00Z | 397.83 | {4} | 0 |
| `STEEM` | 3,412 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `STO` | 3,368 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 4 |
| `STONK` | 384 | 2026-08-06T08:00:00Z | 2026-10-19T12:00:00Z | 74.17 | {4} | 0 |
| `STORJ` | 2,517 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {1, 4, 8} | 5 |
| `STRK` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `STX` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `SUI` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `SUN` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `SUPER` | 4,710 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 4 |
| `SUSHI` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `SWARM` | *no file* | - | - | - | - | - |
| `SWARMS` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `SXT` | 3,211 | 2025-05-02T12:00:00Z | 2026-10-19T12:00:00Z | 535.00 | {4} | 0 |
| `SYN` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `SYRUP` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `T` | 3,714 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 2, 4} | 8 |
| `TA` | 2,732 | 2025-07-21T08:00:00Z | 2026-10-19T12:00:00Z | 455.17 | {4} | 0 |
| `TAC` | 2,767 | 2025-07-15T12:00:00Z | 2026-10-19T12:00:00Z | 461.00 | {4} | 0 |
| `TAG` | 2,985 | 2025-06-09T04:00:00Z | 2026-10-19T12:00:00Z | 497.33 | {4} | 0 |
| `TAIKO` | 3,173 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 3 |
| `TAKE` | 2,520 | 2025-08-25T16:00:00Z | 2026-10-19T12:00:00Z | 419.83 | {4} | 0 |
| `TAO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `TFUEL` | *no file* | - | - | - | - | - |
| `THE` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `THETA` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `TIA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `TLM` | 2,339 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `TMX` | 331 | 2026-08-25T12:00:00Z | 2026-10-19T12:00:00Z | 55.00 | {4} | 0 |
| `TNSR` | 3,955 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 3 |
| `TOSHI` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `TOWNS` | 2,640 | 2025-08-05T16:00:00Z | 2026-10-19T12:00:00Z | 439.83 | {4} | 0 |
| `TRADOOR` | 2,460 | 2025-09-04T16:00:00Z | 2026-10-19T12:00:00Z | 409.83 | {4} | 0 |
| `TRB` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `TREAD` | 779 | 2026-09-17T05:00:00Z | 2026-10-19T15:00:00Z | 32.42 | {1} | 0 |
| `TREE` | 2,741 | 2025-07-29T16:00:00Z | 2026-10-19T12:00:00Z | 446.83 | {1, 4} | 2 |
| `TRIA` | 1,549 | 2026-02-03T12:00:00Z | 2026-10-19T12:00:00Z | 258.00 | {4} | 0 |
| `TRUST` | 2,633 | 2025-11-05T12:00:00Z | 2026-10-19T12:00:00Z | 348.00 | {1, 4} | 2 |
| `TRUTH` | 2,298 | 2025-10-01T16:00:00Z | 2026-10-19T12:00:00Z | 382.83 | {4} | 0 |
| `TRX` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `TST` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `TURBO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `TURTLE` | 2,415 | 2025-10-22T16:00:00Z | 2026-10-19T12:00:00Z | 361.83 | {1, 4} | 2 |
| `TUT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `TWT` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `UAI` | 2,437 | 2025-11-06T12:00:00Z | 2026-10-19T12:00:00Z | 347.00 | {1, 4} | 2 |
| `UB` | 2,413 | 2025-09-12T12:00:00Z | 2026-10-19T12:00:00Z | 402.00 | {4} | 0 |
| `UBIK` | *no file* | - | - | - | - | - |
| `UMA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `UNI` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `UP` | 1,321 | 2026-03-13T12:00:00Z | 2026-10-19T12:00:00Z | 220.00 | {4} | 0 |
| `UPROBINHOOD` | *no file* | - | - | - | - | - |
| `US` | 1,872 | 2025-12-11T16:00:00Z | 2026-10-19T12:00:00Z | 311.83 | {4} | 0 |
| `USDC` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `USELESS` | 3,152 | 2025-05-12T08:00:00Z | 2026-10-19T12:00:00Z | 525.17 | {4} | 0 |
| `USTC` | 3,168 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 1 |
| `USUAL` | 3,298 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {2, 4} | 2 |
| `VANA` | 3,314 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {2, 4} | 2 |
| `VELO` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `VELODROME` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `VELVET` | 2,797 | 2025-07-10T12:00:00Z | 2026-10-19T12:00:00Z | 466.00 | {4} | 0 |
| `VET` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `VIRTUAL` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `VTHO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `VVV` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `W` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `WAL` | 3,498 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `WAVES` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `WAXP` | 3,291 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `WCT` | 4,113 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `WET` | 2,238 | 2025-12-04T16:00:00Z | 2026-10-19T12:00:00Z | 318.83 | {1, 4} | 2 |
| `WHUF` | 66 | 2026-10-08T16:00:00Z | 2026-10-19T12:00:00Z | 10.83 | {4} | 0 |
| `WIF` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `WLD` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `WLFI` | 2,533 | 2025-08-23T12:00:00Z | 2026-10-19T12:00:00Z | 422.00 | {4} | 0 |
| `WOO` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `XAI` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `XAN` | 2,311 | 2025-09-29T12:00:00Z | 2026-10-19T12:00:00Z | 385.00 | {4} | 0 |
| `XCN` | 3,480 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4} | 2 |
| `XDC` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `XDP` | 130 | 2026-09-28T16:00:00Z | 2026-10-19T12:00:00Z | 20.83 | {2, 4} | 2 |
| `XEC` | 2,090 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `XLM` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `XMR` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `XNY` | 2,718 | 2025-07-23T16:00:00Z | 2026-10-19T12:00:00Z | 452.83 | {4} | 0 |
| `XPIN` | 2,468 | 2025-09-03T08:00:00Z | 2026-10-19T12:00:00Z | 411.17 | {4} | 0 |
| `XPL` | 2,539 | 2025-08-22T12:00:00Z | 2026-10-19T12:00:00Z | 423.00 | {4} | 0 |
| `XRP` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `XTZ` | 5,006 | 2025-10-02T10:00:00Z | 2026-10-19T12:00:00Z | 382.08 | {1, 4} | 1 |
| `XVG` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `XVS` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `YB` | 2,244 | 2025-10-10T16:00:00Z | 2026-10-19T12:00:00Z | 373.83 | {4} | 0 |
| `YFI` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `YGG` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ZAMA` | 1,699 | 2026-01-09T12:00:00Z | 2026-10-19T12:00:00Z | 283.00 | {4} | 0 |
| `ZBCN` | *no file* | - | - | - | - | - |
| `ZBT` | 2,585 | 2025-10-17T12:00:00Z | 2026-10-19T12:00:00Z | 367.00 | {1, 4} | 2 |
| `ZC` | 122 | 2026-09-29T08:00:00Z | 2026-10-19T12:00:00Z | 20.17 | {4} | 0 |
| `ZCAT` | 253 | 2026-09-07T12:00:00Z | 2026-10-19T12:00:00Z | 42.00 | {4} | 0 |
| `ZEN` | 1,623 | 2025-04-26T16:00:00Z | 2026-10-19T08:00:00Z | 540.67 | {8} | 0 |
| `ZEREBRO` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ZEST` | 918 | 2026-05-19T16:00:00Z | 2026-10-19T12:00:00Z | 152.83 | {4} | 0 |
| `ZETA` | 3,281 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {2, 4} | 2 |
| `ZIG` | *no file* | - | - | - | - | - |
| `ZIL` | 2,568 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `ZKC` | 2,452 | 2025-09-15T16:00:00Z | 2026-10-19T12:00:00Z | 398.83 | {1, 4} | 2 |
| `ZORA` | 3,246 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4} | 0 |
| `ZRO` | 3,168 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {4, 8} | 1 |
| `ZRX` | 2,635 | 2025-04-26T16:00:00Z | 2026-10-19T12:00:00Z | 540.83 | {1, 4, 8} | 2 |
| `ZZZ` | *no file* | - | - | - | - | - |

## 9. What was not measured / limits of this audit

1. **The holdout was not spent.** `data/quant_research/` was not created and no holdout window was evaluated. The split boundaries in §7.2 are the configured ones only.
2. **No strategy, rule, cost model, feature or signal was evaluated.** This report contains no return, no R-multiple, no hit rate and no signal count. Nothing here is evidence for or against any strategy.
3. **The attribution window sample is 600 windows over 60 coins**, not the full universe and not every possible window. The sample covers 4 h and 24 h holdings only; other holding lengths, other window starts and the other 520 coins were not sampled. The ratio is a sample estimate, stated with its sample size.
4. **The funding-observation archive cannot be back-filled from these archives.** Its coverage is limited to 2026-10-15T22:10:23Z → 2026-10-20T11:36:25Z by the time the collector started recording; that is a property of the archive, not a gap this audit can repair, and repairing it would require venue backfill (out of scope and explicitly forbidden here).
5. **Coverage was measured on the bar series as stored.** Whether the venue itself returned every bar in the covered range is not verifiable from the archives alone; a bar the venue never sent would look exactly like the start of a coin's series.
6. **Freshness is a snapshot.** The age figures are the age at 2026-10-20T11:36:34Z. The archive is being appended to while the service runs, so ages grow; the numbers are a point-in-time measurement, not a steady state.
7. **The `quant.db` / `signals.db` databases were not audited** — only the three on-disk CSV archives under `data/`.
8. **The `data/.cadence.json`, `data/.last_entries`, `data/.timing.json` side-car files were not parsed.** They may carry collector-cadence evidence that would explain *why* some coins start late; this audit only measures *that* they start late.

---

Generated by the read-only audit at 2026-10-20T11:36:34Z. Machine-readable twin: `data-audit.json` (same directory).
