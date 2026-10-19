# ALT RADAR Quant Roadmap — Progress and Final Report

**Date:** 2026-10-18 — **updated 2026-10-19** (dev-only investigation of the
all-negative WS5 result; three independent audits; four pipeline and five
funding-attribution fixes with regression tests, including a
transition-aware settlement schedule; corrected funding-adjusted numbers;
the gross-outcome SPA labelled exploratory; evidence files preserved)
**Plan:** `docs/superpowers/plans/2026-10-18-quant-roadmap-completion.md` (local, gitignored)
**Role:** implementation lead. Research-only / SHADOW; service untouched.

> A prior attempt at this same task left a report at this path
> (`prior-attempt-report-superseded.md`). Its conclusions were based on a
> mid-backfill dataset and are superseded; the file is preserved.

Status legend: **VERIFIED** (observed this session) · **PLANNED** · **BLOCKED**.

---

## 0. Initial repository state (VERIFIED)

- Branch `main`; `HEAD == origin/main == 17e9a74`
  (`docs(quant): 2026-10-18 data-readiness snapshot; WS5 harness still unrun`),
  committed 2026-10-18 12:56:51 +0100.
- Working tree at start: clean except untracked `docs/implementation-reports/`.
- Live service: pid 715, `python3 -m proto.service --collect-every 300
  --resolve-every 3600`, running since 2026-10-16; predates all 2026-10-17
  route commits. **Not restarted.**
- Network: `https://contract.mexc.com/api/v1/contract` reachable.
- A concurrent prior-agent artifact (NOT mine): pid 403144 ran
  `proto.quant evaluate ... --out /tmp/ws5-eval-final`; it exited and wrote
  `evaluation.json` (see §4). It was not killed.

## 1. Verified data readiness (VERIFIED 2026-10-18)

| Archive | Files | Verified content |
|---------|-------|------------------|
| `data/bars/` | 578 | 5m bars. Files are batch-appended and **not chronological**; span must come from sorted unique min/max. |
| `data/funding_settlements/` | 559 | `settle_ts,settled_rate,cycle_hours`; 1,366,260 rows; spans up to ~540 d; cycles 1/2/4/8h (some mixed). |
| `data/funding/` | 571 | `ts,rate,cap`; ~2 d each (observations). |
| `data/prices/` | absent | mark/index collector code-complete but uncollected. |
| `data/quant_research/` | absent | no committed evaluation report. |

### Bar coverage distribution (independent 578-file pass; exact)

Span = (last − first)/86400 over **unique sorted** timestamps.

| Bucket | Coins |
|--------|-------|
| ≥ 91 days (**full split support**) | **523** |
| 79–91 days (holdout present but short) | 3 (CATE 83.3, GRVT 80.1, MARSCOIN 80.0) |
| 63–79 days | 4 |
| 30–63 days | 24 |
| < 30 days | 24 |
| **Total** | **578** |

- BTC: ≈28.1k unique bars / **≈97.45 days** at this snapshot; universe max
  ≈97.5 days. Counts are deliberately approximate — the live collector
  appends a 5m bar for every coin every 300 s, so exact row counts rot.
- Duplicate timestamps: **0**. Invalid rows: **0**. (Perfect-300s-grid WS1
  invariant still holds.)
- Corroborated by the project's own `python3 -m proto.bar_quality --data-dir
  data/bars --all --json`: 578 coins, **zero gaps, zero duplicates, zero
  invalid rows, coverage ratio 1.0** for every coin (12 `out_of_order`
  chunks per long file — the batch-append layout, not a defect).
- **This directly contradicts the committed snapshot** (`~16.41 days / no
  coin ≥91 days / settlements absent`). Example of the artifact: BTC's
  file-order span reads 16.49 d, its sorted span is 97.45 d.

### Settlement archive validation (VERIFIED)

- 559 files / **1,366,260 rows**; **0 duplicate `settle_ts`; 0 malformed
  rows** (no placeholder is ever written — `_as_finite_rate` / `_as_cycle`
  return `None`).
- Span buckets: ≥91 d **525** · 79–91 d **4** · 30–79 d **15** · <30 d
  **15**.
- `cycle_hours` ∈ {1,2,4,8}; coins-per-cycle {1:151, 2:12, 4:448, 8:159};
  **175 mixed-cycle coins**. Grid breaks at cycle changes are expected;
  **7 single-cycle coins are genuinely irregular** (1 break each, cycle 4:
  APEX, BTW, EDGE, FLUID, GP, RHEA, STONK).
- **Per-coin manifest (VERIFIED 2026-10-19, `observed_at 1792396320`):**
  `proto.quant.settlement_audit` records one outcome for **every** coin in
  the bar universe as it stood at capture (**579** — the universe is live
  and grew from 578 during this session when `PAPER` listed; funding grew
  571→572). Outcomes: **544 complete · 7 truncated · 28 missing_archive ·
  0 failed_or_error** (see §11). The 551 present archives hold
  **1,351,943** rows with **0 malformed** and **0 duplicate** timestamps.
  The reconciliation probe returned **0 errors**. The original backfill's
  per-coin API errors were never persisted and are unrecoverable, so this
  column describes the probe, not that run.
- **Truncation:** two independent signals — an exact 1000-row
  `collect_coin` page boundary **and** a venue shortfall larger than the
  live tail accrued since the backfill can explain — agree on exactly
  **7 truncated coins**, all at 5000 rows: FLOCK, LA, LAYER, MEGA, MYX,
  SIGN, XTZ. Current venue `totalCount` 5074–7031; total deficit **6,432
  rows** (FLOCK 264, LA 1917, LAYER 323, MEGA 478, MYX 1345, SIGN 74,
  XTZ 2031), of which **6,299** exceed the elapsed live tail (133 rows =
  7 × 19 at each coin's 1h cycle). Every other coin's shortfall is within
  its tail. 345 coins share the venue retention floor `1745683200`
  (2025-04-26).
- **Missing archives (28):** recorded as venue state, not as a cause.
  **27** are **absent from the venue's current ticker list** yet the venue
  **still serves history** for every one (probe `totalCount` 72–7468) —
  the fact the backfill's ticker-derived universe (`_universe_coins()` =
  current `mexc.tickers()`) explains; **no delisting is asserted**. The
  28th, `PAPER`, **is** in the current ticker list with only 2 venue rows —
  a listing that postdates the 2026-10-18 backfill. 8 settlement coins are
  not in the bar universe (MX, MXSOL, STETH, TON, USD1, USDE, USDGO, WBTC).
- **Backfill path is safe/idempotent:** `funding_rate/history` paginated,
  `SETTLEMENT_LIMITER` 20 req/2 s, append-only gzip `"at"`, dedupe by
  `settle_ts` on-disk and in-batch → re-run adds 0 rows; ~2.8 min for the
  full universe at the configured rate. No rewrite of production archives.

## 2. Workstreams and ownership

| WS | Description | Owner | Status |
|----|-------------|-------|--------|
| A | Coverage audit (bars/settlements/funding) | lead + subagents 1,2 | done (verified) |
| B | Correct data-readiness docs | lead | done (commit `faa7583`) |
| C | Coverage policy + `--coins` defect (TDD) | lead | done (commit `168b960`, hardened `e51ce1c`) |
| D | WS5 dev/val run, holdout SEALED | lead | **done** (§4) |
| E | WS3/WS4 availability audit | subagent 3 | done |
| F | WS6/WS7 status | subagent 3 + lead | done (findings) |
| G | Deployment checklist (restart pending) | lead | done (§10) |
| H | Final review + commit + push | lead | done (§8) |
| I | Persist WS5 selection identity (TDD) | lead | done (commit `4419f49`) |
| J | Per-coin settlement backfill manifest | lead | done (§1, §11) |
| K | Preserve evidence + tighten report wording | lead | done (§4, §11) |

## 3. Code changes (TDD)

- **New `proto/quant/coverage.py`** — minimum-coverage policy. Pure,
  offline, pure-logic core (`holdout_start_day`, `min_span_days`,
  `classify_coverage`, `summarize`) plus a read-only `audit_spans`. Derived
  from `Splits`: holdout starts at day **79.0**; a full run needs ≈ **91**
  days (holdout start + 12-day holdout, matching the original study's
  79→91.4 d holdout). Buckets: `full` / `partial` / `insufficient`.
  Tests: `tests/test_quant_coverage.py` (RED→GREEN; includes the
  unsorted-archive trap).
- **`--coins` defect fix** — `proto/quant/__main__.py:130` built a dead
  `fargs` list; `evaluate()` never received `coins`. Now
  `eval_harness.evaluate(..., coins=None)` forwards `coins` to
  `research.collect_events` and the CLI splits `--coins` on commas.
  Regression: `tests/test_ws5_eval_harness.py` case 23.
- **New `proto/quant/settlement_audit.py`** — read-only per-coin
  settlement-backfill manifest: per-coin archive stats, a per-coin venue
  `totalCount` reconciliation with an injectable probe, and the
  four-outcome classification (complete / truncated / missing_archive /
  failed_or_error). Pure core never writes; the CLI writes only `--out` /
  `--csv`. Tests: `tests/test_settlement_audit.py`.
- **`evaluation.json` now records the selected strategy** —
  `eval_harness.EvaluationReport` gains `selected` (rule id, geometry key,
  resolved `RuleParams`/`Geometry`, geom index), `n_configs`, `spa`,
  `dev_config_means`, `top5_dev`, so a saved report names what it
  evaluated. Tests: `tests/test_ws5_eval_harness.py` case 7
  (insufficient-data fallback) and case 20 (real selection).
- **Pipeline hardening from the 2026-10-19 audit** — four latent defects,
  each fixed TDD with `tests/test_quant_pipeline_regressions.py`:
  `barrier.make_path` now takes `run_end`, so a label path cannot cross a
  timestamp gap into a different contiguous run; `research.collect_events`
  recreates the CUSUM state on an invalid-bar restart; `meta.t_max` uses
  the union archive max instead of BTC's alone; `stats.spa_max_t` and
  `research.run_stage_dev_val` exclude configs with <3 distinct days (where
  `day_cluster_t` is undefined and returns 0.0). None fires on the current
  archive (no gaps, no invalid bars, all 288 configs have 55 days), and the
  post-fix dev/val recompute is identical; the union-span fix moves the
  holdout end by minutes only.
- **Funding-attribution fixes from the 2026-10-19 cost/split audit** — TDD
  with `tests/test_quant_funding_regressions.py` (RED→GREEN, 5 groups):
  `cost_attribution` now infers the venue's schedule per holding window as
  a **sequence of constant-cycle regimes** (`_settlement_regimes` +
  `_expected_boundaries`) instead of one grid, so a shortening transition
  (8h→4h) charges its intermediate settlements, a lengthening transition
  (4h→8h) does not demand the settlement the venue skipped, and a schedule
  that cannot be established (a row off its regime's grid, or a transition
  bridge longer than the new cycle) is reported **incomplete** rather than
  silently omitting a settlement; `eval_harness._event_exit_ts` timestamps
  a TIME exit at the final bar's close (`horizon+1` bars) while keeping
  TP/SL at the exit bar's open; `funding_adjusted_config` returns the
  matched friction-only mean and `funding_delta`; `check_no_leakage` uses
  the true 289-bar span. The friction-only selection is unchanged;
  corrected funding-adjusted dev/val means are −0.01585 / −0.01745 with
  coverage 0.9995 / 0.9974 (funding is a small cost, not a credit).

## 4. WS5 evaluation (holdout SEALED)

**Canonical run** (reproducible; no `--holdout`), from `HEAD = da5f43c`:

```
python3 -m proto.quant evaluate --data data/bars --funding-dir data/funding \
  --settlements-dir data/funding_settlements --out /tmp/opencode/ws5-eval-selected
# EXIT=0  -> /tmp/opencode/ws5-eval-selected/evaluation.json  (preserved: §11)
# sha256 81affa4ad5d1661f7a2b55f3e776f74bea65ef7ec136f7b1f70c1836f513242c
# config_hash d33cff513c30  cost_hash 762515f702934998
# splits dev(6,61) val(63,77) holdout(79, 98.3194)  n_days 98.3194
```

| Split | n | days | coins | mean_r | 95% day-cluster bootstrap CI | t |
|---|---:|---:|---:|---:|---|---:|
| dev (friction-only) | 24,749 | 55 | 375 | −0.01542 | [−0.04171, +0.01424] | −1.062 |
| val (friction-only) | 6,495 | 14 | 248 | −0.01661 | [−0.04676, +0.01742] | −1.013 |
| dev (funding-adjusted, corrected) | 24,736 | 55 | 371 | −0.01585 | [−0.04191, +0.01339] | −1.092 |
| val (funding-adjusted, corrected) | 6,478 | 14 | 246 | −0.01745 | [−0.04736, +0.01666] | −1.064 |

The funding-adjusted rows were **corrected on 2026-10-19** in two rounds
after the cost/split audit: the venue's settlement schedule is now derived
per holding window as a sequence of cycle regimes (see *Settlement-cycle
transitions* below), and the funding effect is reported on the **matched**
event set. The preserved `evaluation.json` predates both and records the
pre-fix values (dev 24,311, −0.01485; val 6,374, −0.01633); the corrected
recompute is `funding-transitions.json` (§11). The friction-only rows are
unchanged.

### The strategy these numbers describe

The harness recorded the selected config's **metrics** but not its
**identity**, so a saved `evaluation.json` could not name the strategy it
evaluated. That gap is fixed in `4419f49`, and the canonical run's
`evaluation.json` now carries the identity recorded below (§11).

- **Rule:** `breakout_start[rvol>=4]/cont` — a residual-breakout start with
  `rvol_min = 4.0` and `policy = cont` (trade *with* the break, not fade).
- **Geometry:** `T288_ks3_R2` (grid index 34) — `horizon_bars = 288`
  (24 h), `k_stop = 3.0`, `r_tp = 2.0`.
- **How it was chosen (`run_stage_dev_val`):** evaluate all **288**
  pre-declared configs (8 rules × 36 geometries) on the dev window, then
  take the one with the largest **day-cluster `t`** statistic — a
  multiplicity-corrected max-t (`spa_max_t`, B = 600). Only that config is
  then re-evaluated on validation. Selection is on the *t statistic*, not
  the highest dev mean.
- **The selection is already negative:** the winning day-cluster
  `t = −1.062`, the corrected **SPA p-value is 1.0**, and **0 of 288**
  configs had a positive dev mean (`share_positive = 0.0`; dev means range
  −0.2336 … −0.0147). The best of 288 candidates loses on dev, before
  validation even enters.

- **Denominators reconcile** exactly (friction-only = funding-adj + excluded):
  dev 24,736 + 5 (missing coin) + 8 (incomplete) = **24,749**;
  val 6,478 + 0 + 17 = **6,495**. Corrected funding coverage 0.9995 / 0.9974
  (was 0.9823 / 0.9814 before the 2026-10-19 audit-B grid fix, and
  0.9965 / 0.9915 after its first correction round).
  `missing_cost_coins = [1000BTT, RON, ZBCN]`; `leakage_violations = []`;
  incomplete-boundary 25 / 31,244 events (was 554 under the pre-fix grid);
  digests bars 450 / funding 447 / settlements 443.
- **In this run every dev/val interval straddles zero and every point
  estimate is negative: it found no evidence of a positive edge.**
  Intervals are **95% day-cluster bootstrap** intervals (resampled over
  trading days, the harness's unit of independence). Dev-only coin and
  sub-period splits are reported below; they show the same negative net
  result and large cross-sectional dispersion, so they do **not** establish
  robustness across coins or periods. A future snapshot or a different
  universe could differ. Friction-only and funding-adjusted are reported
  **separately** (never merged). On the **matched** event set, funding is a
  small **cost** (−0.0005R dev, −0.0011R val); the earlier +0.0005R
  "credit" was a different-denominator artefact and is corrected below.
- **The one-shot holdout stays SEALED:** `holdout`, `holdout_slip2x`,
  `holdout_funding_adjusted`, `holdout_slip2x_funding_adjusted` are all
  `null`. No decision to spend it has been made, and none is warranted
  without a positive, CI-clearing dev/val edge.
- **Reproducibility across archive snapshots:** the two 2026-10-18 runs —
  this one at `e51ce1c` and an independent concurrent run at `17e9a74` —
  produced **identical dev/val statistics** (byte-identical `dev` and `val`
  blocks, including both funding-adjusted ones) under the same
  `config_hash d33cff513c30` / `cost_hash 762515f702934998`. Those
  funding-adjusted blocks are historical: they use the pre-fix grid and
  unmatched denominator, and are superseded by `funding-fixed.json`. The
  runs are **not**, however, over identical inputs:
  - `n_days` differs (97.3993 vs 97.4514) — the live collector appended
    bars between them;
  - of the 448 coins contributing events, **441 bar digests differ** and
    only **7 are byte-identical** (funding: 441 of 445 differ), as expected
    when the collector appends a 5-minute bar per coin every 300 s;
  - **0 of 441 `settlement_digests` differ** — the settlement archive did
    not change (no settlement collection runs, since the live service
    predates that hook), so the cost inputs are identical.

  The matching dev/val statistics are explained by the appended bars
  falling **after** the val window (both runs' newest bar postdates day 77);
  they are **not** evidence that the two runs saw the same complete inputs.
- **A third snapshot agrees:** the canonical 2026-10-19 run (`da5f43c`,
  `n_days` 98.3194; bar/funding/settlement digests 450/447/443) reproduced
  the **same** dev/val metrics *and* selected the **same** config as both
  2026-10-18 runs — checked here two independent ways (the harness report
  and a separate `run_stage_dev_val` recomputation from a cached event
  set, which matched on rule, geometry, params, `n_configs`, `spa`, `dev`
  and `val`). The digest sets grew only because two coins listed.

### Why all 288 dev configs were negative — dev-only diagnostics (2026-10-19)

**Question.** The canonical run selected the *least negative* of 288
pre-declared configs and still lost on dev and val. Was that a sign, label,
cost, or metric defect — or the cost model doing its job? This section
separates the two. It uses **dev only** (days 6–61): no selection, no
validation tuning, and no holdout access.

**Method.** Reuse the canonical run's cached `collect_events` output
(86,038 events; `t_min=1783910100`, `t_max=1792404900`, `n_days=98.3194`)
and decompose every event exactly:

```
net_R = gross_log / dd - cost_frac / dd = gross_R - drag_R
```

`gross_R` is the triple-barrier outcome before costs, in stop-distance
units; `drag_R` is the same round-trip friction expressed in those units.
`mean(net_R) = mean(gross_R) - mean(drag_R)`, so a negative net with a
zero-mean gross is friction; a negative gross is a signal/label problem.
The three audits below were independent and read-only: two audited the code
and one re-derived the statistics.

**288 configs, dev, friction-only.**

| statistic | value |
|---|---:|
| net mean, min / median / max | −0.23358 / −0.05596 / −0.01467 |
| configs with net mean > 0 | **0 / 288** |
| gross mean, min / median / max | −0.03850 / **+0.00119** / +0.07425 |
| configs with gross mean > 0 | **150 / 288** |
| gross day-cluster t > +1.96 | 20 / 288 |
| gross day-cluster t < −1.96 | 8 / 288 |
| selected config, dev | gross **+0.00400** (t +0.27), drag 0.01942, net **−0.01542** (t −1.06) |
| selected config, val | gross **−0.00152** (t −0.09), drag 0.01509, net **−0.01661** (t −1.01) |

The pre-cost distribution is centred on zero (median +0.0012R; 52% of
configs positive) while every net mean is negative. That is the signature
of a cost drag, not a sign or label inversion.

**The drag is geometric.** For a fixed geometry the friction in R units is
`cost_frac / (k_stop · sigma · sqrt(T))`; the measured medians follow
`1/(k·√T)`:

| geometry | median gross R (8 rules) | median drag R | median net R |
|---|---:|---:|---:|
| `T48_ks1_R3` | +0.0239 | 0.1796 | −0.1249 |
| `T48_ks1.5_R3` | +0.0244 | 0.1198 | −0.0829 |
| `T48_ks3_R2` | +0.0131 | 0.0599 | −0.0438 |
| `T144_ks3_R2` | +0.0036 | 0.0346 | −0.0284 |
| `T288_ks3_R2` | +0.0001 | 0.0244 | −0.0265 |

The `T48_ks1` / `T288_ks3` drag ratio is **7.36**, against
`(3√288)/(1√48) = 7.35` from the formula (0.1% apart); 4 of 8 rules have
positive gross at every geometry, i.e. no direction is consistently
favoured.

**Cost sensitivity (selected config, dev).** Recomputing the same events
under different friction assumptions (reporting only — not a new
selection):

| friction assumption | net mean R | t | drag R |
|---|---:|---:|---:|
| none (gross) | +0.00400 | +0.27 | 0 |
| fees only (5 bp/side) | −0.00329 | −0.23 | 0.00730 |
| slippage only | −0.00812 | −0.56 | 0.01213 |
| **base (5 bp/side + tiered slip)** | **−0.01542** | **−1.06** | **0.01942** |
| MEXC 2 bp/side + tiered slip | −0.01104 | −0.76 | 0.01505 |
| 2 bp/side + half slip | −0.00498 | −0.34 | 0.00898 |
| 2 bp/side + double slip | −0.02317 | −1.59 | 0.02717 |

The planner's 5 bp/side constant is **2.5×** the 2 bp/side MEXC futures
taker rate. Even halving slippage does not lift the selected config above
zero, because its gross edge is only +0.004R.

**EXPLORATORY (post-hoc, not preregistered): a pre-cost signal at short
horizons.** Running the *same* day-cluster max-t/SPA procedure on **gross**
outcomes (still 288 configs, still dev only) gives `best_t = +3.7149`,
`p = 0.0050` (3 of 601 replicates), best config
`residual_shock[cusum_h=5]/fade`, `T48_ks1.5_R3` (fade the residual shock;
4-hour horizon):

| | gross | drag | net |
|---|---:|---:|---:|
| mean R | **+0.05208** | 0.11976 | **−0.06768** |
| 95% day-cluster CI | [+0.02464, +0.07928] | — | [−0.10379, −0.03263] |
| t | +3.71 | — | −3.81 |

**This is an exploratory diagnostic, not a test result.** The multiplicity
correction covers the 288 pre-declared configurations; it does **not** cover
the fact that the outcome metric (gross instead of net) and this analysis
were chosen *after* reviewing the net results. Neither the metric nor the
analysis was preregistered, so `p = 0.0050` is hypothesis-generating only
and must not be quoted as evidence of an edge.

n = 9,951 events, 341 coins, 55 days; TP/SL/TIME = 0.02/0.25/0.73.
Within this exploratory diagnostic the pattern is not a single-cell fluke:
gross is positive at **all 36 geometries** (+0.0062 … +0.0570), in **5 of
5** sub-periods (+0.0150 … +0.0885), at both CUSUM thresholds (h=8:
+0.04715, t +1.92), and the *opposite* policy is negative (cont: −0.02836,
t −1.57). Breadth is 208/341 coins positive; the top 10 coins contribute
34.5% of gross P&L and the top 5 days 44.7%. But net is negative at **all
36 geometries**, because the edge is only **0.435×** the modelled friction
at the best cell (0.369× at `T48_ks3_R2`; 0.220× at `T288_ks3_R2`).
Break-even at the best cell requires round-trip friction ≤ ~0.052R, i.e.
roughly **15 bp** versus the modelled ~34 bp for that coin mix — about 56%
lower. That threshold is arithmetic, not a demonstrated reachability: no
execution-cost evidence in this repository supports it (§H1).

**By coin (selected config, dev, friction-only).** 24,749 events across
375 coins. 140/375 coins have a positive gross mean and 123/375 a positive
net mean; restricting to coins with n ≥ 30, 91/220 are gross-positive.
Coin net means are dispersed: p10 −0.1223, median −0.0305, p90 +0.0797
(best: LSK +0.3226 on 40 events, XRP +0.2944 on 92, TUT +0.2894 on 163;
worst: STAR −0.3132 on 37, BLUAI −0.2710 on 41, PI −0.2658 on 72). This is
cross-sectional noise plus a few large winners, not a broad-based edge —
consistent with 0/288 positive net means.

**By sub-period (selected config, dev, friction-only).**

| days | n | gross R | gross t | drag R | net R | net t |
|---|---:|---:|---:|---:|---:|---:|
| 6–17 | 4,042 | −0.00405 | −0.24 | 0.0206 | −0.02466 | −1.43 |
| 17–28 | 4,089 | −0.01420 | −1.10 | 0.0210 | −0.03522 | −2.79 |
| 28–39 | 4,969 | +0.07818 | +1.46 | 0.0193 | **+0.05891** | +1.10 |
| 39–50 | 6,715 | −0.03122 | −1.52 | 0.0137 | −0.04494 | −2.25 |
| 50–61 | 4,934 | −0.00107 | −0.07 | 0.0251 | −0.02613 | −1.42 |

Four of five sub-periods lose net; the one positive window is not
replicated and does not survive the full-window test.

**Funding-adjusted (separate, reporting only; corrected 2026-10-19).**
After the audit-B fixes (per-regime settlement schedule, TIME exit at the
final bar's close, matched denominators), the selected config's dev mean is
**−0.01585** (95% CI [−0.04191, +0.01339], t −1.09) on
**24,736 / 24,749** events (coverage 0.9995; 5 events on coins with no
funding archive + 8 unattributable holding windows excluded, never
zero-filled). Val is **−0.01745** ([−0.04736, +0.01666], t −1.06) on
6,478 / 6,495. On the **matched** event set the friction-only mean is
−0.01533 (dev) / −0.01632 (val), so funding is a small **cost** of
**−0.00051R** (dev) and **−0.00113R** (val) — not the +0.00057R credit
obtained by comparing different denominators. At the selected geometry,
corrected funding-adjusted dev means are −0.0267 (rvol≥4/fade), −0.0159
(rvol≥4/cont), −0.0194 (h=5/fade), −0.0308 (h=5/cont); by sub-period:
−0.0270, −0.0361, +0.0581, −0.0443, −0.0257. Same pattern as
friction-only. The preserved `evaluation.json` predates this fix and still
records the old 24,311 / −0.01485 (dev) and 6,374 / −0.01633 (val); the
corrected recompute is `funding-transitions.json` (§11). Note: `cost_hash`
covers the archive paths and settle constants, **not the attribution code**,
so it is unchanged by these fixes; the corrected numbers are identified by
the evidence-file hashes and the commit (`git_rev` is recorded in the
report).

**Settlement-cycle transitions (2026-10-19, second correction round).**
MEXC's per-row `collectCycle` is the funding interval in force **from**
that settlement onwards — verified against the archive, not assumed: the gap
to the next row equals the current row's cycle in **1,365,207 of the
1,365,701** rows that have a following row, and the **494** exceptions are
exactly the venue-stated transition bridges. The cycle does change (1h/2h/4h/8h
all occur; 175 of 559 settlement archives hold more than one cycle value,
512 transitions in the archive), so a holding window's schedule is a
**sequence of constant-cycle regimes**, not one grid:

- a **shortening** transition (8h→4h) *adds* settlements — the new grid's
  intermediate boundaries must be charged;
- a **lengthening** transition (4h→8h) *skips* the intermediate settlement
  — the old grid's next boundary has no row, and that is the venue's
  stated behaviour, not a missing row.

The previous implementation inferred a single grid per window, which
silently omitted the intermediate settlements of a shortening transition
(a window that starts before an 8h→4h change was charged on the 8h grid)
and falsely excluded windows containing a lengthening transition. The
attribution now derives the per-regime schedule, and a schedule that
cannot be established — a row off its regime's grid, or a transition bridge
longer than the new cycle (i.e. the row that starts the new regime is
missing) — yields **incomplete**, never a silent omission. A row missing
*inside* a regime is a gap the observation fallback may still attribute.

**How many real evaluation windows cross a transition?** For the selected
config's dev window, **219 of 24,749** holding windows cross one
(4h→1h 108, 1h→4h 76, 8h→1h 23, 2h→4h 10, 1h→8h 1, 4h→2h 1); val is
**99 of 6,495** (4h→1h 39, 1h→4h 38, 8h→1h 8, 2h→4h 5, 1h→8h 5, 4h→2h 3,
2h→1h 1). Across all 8 rules × dev+val it is **1,602 of 197,724** windows.
The affected coins concentrate in a few names (RIF 38, SOPH 36, ONG 32,
ACE 19, IOST 14, SKR 14 dev windows). **All 219 dev and all 99 val
transition-crossing windows are attributed** — none is excluded, so the
per-regime schedule resolves every transition that actually occurs in the
evaluation windows. (23 of the archive's 512 transitions have a bridge
longer than the new cycle and are treated as unverifiable; none of them
falls in dev or val.) The corrected attribution raises dev funding coverage
from 0.9965 to **0.9995** and val from 0.9915 to **0.9974**, and the
funding effect stays a small cost on the matched set.

**Independent audits.**

- **Signal/event construction (A): SOUND.** No look-ahead (perturbing all
  future bars changed 0 fields; `label_path` vs `label_reference` 0/1,800
  mismatches; RVOL 0/300 mismatches; the channel excludes the current bar).
  Archive hygiene: 578 non-BTC coins, 578 contiguous runs, 0 gaps / 0
  duplicates / 0 invalid / 0 non-finite in 2.3M sampled rows. Three
  **latent** defects were found and fixed with regression tests (none is
  triggered by this archive): `make_path` could cross a contiguous-run
  boundary; CUSUM state survived an invalid-bar restart; `meta.t_max` used
  BTC alone. The union max is 600 s beyond BTC now (98.413194 vs 98.406250
  days); dev/val bounds are fixed at (6,61)/(63,77), so the canonical
  dev/val numbers are unchanged (confirmed by recompute, §7). The preserved
  `evaluation.json` predates this fix, so its holdout end is the BTC-only
  `n_days 98.3194`; a fresh run would extend it by minutes. All reported
  dev/val metrics and the still-sealed holdout status are unaffected.
- **Metric/statistics (C): SOUND.** The day-cluster mean/t is correctly
  specified and calibrated (false-positive rate 4.875% at |t| > 1.96; 94.1%
  CI coverage; algebraically identical to a cluster-robust sandwich). SPA
  resamples whole days jointly across configs and recentres each config at
  its own mean; the bootstrap max-t* minimum is **+0.324**, above the
  observed best **−1.062**, so p = 1.0 is exactly what the test implies
  (not a mis-specification); a planted edge returns p = 0.003, so the test
  is responsive. A driftless simulation gives net ≈ −c/dd with fade/cont
  symmetric. One latent sparse-day defect (a config with <3 distinct days
  got t = 0.0 and could win) was fixed with a guard + regression test; not
  triggered here (all 288 configs have 55 days). Three reporting nits are
  documented, not inference defects: the reported median is the upper
  median (−0.05596 vs true −0.05657); `top5_dev` CIs use B=300 vs B=1500;
  and "days" are 24h blocks anchored at t_min (2026-07-13 02:35 UTC), not
  UTC calendar days.
- **Cost/split integrity (B): DEFECT found and fixed — funding path only;
  friction-only SOUND.** The friction cost is applied exactly once per
  path, the units are consistent, the splits do not leak (the corrected
  289-bar bound is still below every embargo), and the selection path uses
  dev only. The defects were in funding attribution: the settlement grid
  was inferred from the whole archive's mode and the **oldest** row, so 14
  coins whose cycle changed and 2 with a poisoned oldest phase were
  wrongly marked incomplete (387 of the 433 pre-fix dev exclusions) and 6
  coins were silently undercharged; the single-grid inference **silently
  omitted** the intermediate settlements of a shortening cycle transition
  and falsely excluded windows containing a lengthening transition; the
  TIME exit was timestamped one bar early; and the funding-adjusted mean
  was compared with a different-denominator friction-only mean. All are
  fixed with `tests/test_quant_funding_regressions.py` (5 groups,
  RED→GREEN, including 8h→4h, 4h→8h and observation-fallback
  transition cases). The corrected dev funding-adjusted mean is **−0.01585**
  on 24,736 events (was −0.01485 on 24,311), and funding is a
  **−0.00051R cost** on the matched set. The friction-only headline is
  **unchanged**: all 288 dev means remain negative, the selected config
  remains `T288_ks3_R2`, and SPA p remains 1.0.

**What this means.** The negative dev means are **friction**, not a
sign/label/metric defect: gross is centred on zero for the
selection-relevant breakout family (dev +0.0040, val −0.0015) and the drag
matches `cost/(k·σ·√T)` to 0.1%. The pre-declared hypotheses therefore
show **no promise at the modelled costs**: even the best pre-cost cell
loses after friction, and the best-of-288 gross edge (+0.0743R) is
overwhelmed by its geometry's drag (0.195R). One **pre-cost**
short-horizon reversal signal is detectable in a **post-hoc, exploratory**
analysis — the gross-outcome SPA corrects for the 288 configurations but
not for choosing the gross metric and the analysis after seeing the net
results — and it is ~2.3× smaller than friction at the best geometry.
The study is also underpowered relative to the drag: the selected dev mean
has SE ≈ 0.0145R, so ~0.04R is needed for 80% power, while the drag at
that geometry is 0.019R; the run cannot resolve an edge of the size that
would matter.

**Candidate hypotheses for preregistration (not evaluated here).** These
are *candidates to be preregistered*, not findings and not confirmed
edges: each still needs a preregistration document (fixed rule, geometry,
outcome metric, cost model and analysis) written **before** any new data
is looked at.

1. **H1 — candidate: short-horizon residual-shock reversal (fade).**
   `cusum_h=5` (secondary h=8), `T48`, `k_stop` 1.5–3, `r_tp` 2–3.
   Exploratory support only: pre-cost dev +0.052R/trade (t +3.71; gross
   SPA p = 0.005, post-hoc), positive across 36/36 geometries and 5/5
   sub-periods, with the opposite policy negative.
   **The viability gate is arithmetic and is not demonstrated to be
   reachable.** At the best cell the edge is 0.435× the modelled friction,
   so break-even requires round-trip friction ≤ ~0.052R (≈15 bp on the
   modelled cost scale). **We hold no execution-cost evidence that this is
   achievable:** the 5 bp/side taker constant is the planner's placeholder
   (the operator's MEXC tier was never provided), slippage is an
   assumption rather than a measurement, and the repository contains no
   fills, depth, latency or maker-rebate data for these coins. Before H1 is
   preregistered, the operator must supply a confirmed fee tier and a
   measured execution-cost model for the sampled coins; if the achievable
   round-trip cost is not at or below the break-even threshold, H1 is dead
   on arrival and must not be promoted.
2. **H2 — reject: long-horizon breakout continuation.** Gross is ~0
   (dev +0.004R, val −0.0015R) and drag is 0.019R at `T288_ks3_R2`.
3. **H3 — reject: funding carry as a standalone edge.** On the matched
   event set funding is a small **cost** (−0.0005R dev, −0.0011R val), not
   a return source.
4. **Do not spend the one-shot holdout on exploration.** It is ~19 days
   and is the only clean sample. It may be used once to confirm a fully
   specified, preregistered H1 under a frozen, operator-confirmed cost
   model; otherwise it stays sealed.

No live behavior changed, no production archive was rewritten, and the
holdout fields remain `null`.

## 5. WS3/WS4/WS6/WS7 findings (subagent audit, VERIFIED probes)

- **WS3 1m:** venue *serves* `Min1` (HTTP 200, 60 s grid); the adapter
  simply lacks the interval key, and the live sweep is ~1516 s vs a 60 s
  schedule (~25×). Design stage, needs owner approval. Provisional tier
  decided against.
- **WS4 OI history: externally blocked** — every history URL 404; only
  current `holdVol`. **Book/spread history: externally blocked** — only
  live `/depth`.
- **WS4 mark/index:** code-complete, `data/prices/` absent; probe shape
  matches `proto/prices.py`. Implementable now (collector run).
- **WS4 routes `/settlements`, `/prices`, `/quant-status`: 404 on the live
  service** — deploy/restart pending owner approval. `/funding` live (200).
- **WS5 shadow** + **WS6 Research view**: code-complete, deploy pending.
- **Promotion:** registry has 4 rules, all `research`; **no promotion
  records**; `docs/decisions/log.jsonl` exists but 0 bytes.

## 6. Holdout status

- **SEALED.** No `--holdout` flag passed. Decision recorded after dev/val
  coverage and results are reviewed.

## 7. Tests and checks run

| Check | Result |
|-------|--------|
| `python3 tests/test_quant_coverage.py` | ALL PASS |
| `python3 tests/test_settlement_audit.py` | ALL PASS (50 checks) |
| `python3 tests/test_ws5_eval_harness.py` | ALL PASS (137 checks; selection-identity cases added) |
| `python3 tests/run_all.py` | **ALL SUITES PASS (81 files, 0 failures)** |
| `python3 -m proto.bar_quality --data-dir data/bars --all --json` | 578 coins; 0 gaps / 0 dups / 0 invalid; ratio 1.0 |
| `python3 -m proto.quant.settlement_audit …` | `EXIT=0`; 579 coins → 544 / 7 / 28 / 0 (§1, §11) |
| `python3 -m proto.quant evaluate …` (no `--holdout`) | `EXIT=0`; numbers + selected identity in §4 |
| selection-identity cross-check (`run_stage_dev_val` recompute from a cached event set) | matches the harness exactly: rule, geometry, params, `n_configs`, `spa`, `dev`, `val` |
| post-fix `run_stage_dev_val` recompute | identical to canonical: 288 configs, `spa {best_t: −1.06197, p: 1.0}`, same selection/dev/val |
| `python3 tests/test_quant_pipeline_regressions.py` | ALL PASS (4 regression groups: run-boundary path, union archive span, CUSUM restart, sparse-day guard) |
| `python3 tests/test_quant_funding_regressions.py` | ALL PASS (5 regression groups: window-local settle grid, matched funding denominator, TIME-exit close, 289-bar leakage bound, cycle transitions 8h→4h / 4h→8h / observation fallback) |
| cycle-semantics audit over all 559 settlement archives (read-only) | 1,366,260 rows, 0 without a cycle; the gap to the next row equals the current row's `collectCycle` in 1,365,207 of the 1,365,701 rows that have a following row; the 494 exceptions are the transition bridges; 175 archives hold >1 cycle value; 512 venue-stated transitions; 23 have a bridge longer than the new cycle (unverifiable) |
| cycle-transition audit over the real evaluation windows (read-only) | 219 of 24,749 dev and 99 of 6,495 val holding windows (selected config) cross a transition; 1,602 of 197,724 across all 8 rules × dev+val; **all 318 crossing windows are attributed** |
| funding-adjusted recompute after the transition fix (dev-only) | dev −0.01585 on 24,736 events (coverage 0.9995), val −0.01745 on 6,478 (0.9974); funding −0.00051R / −0.00113R on the matched set; evidence §11 |
| dev-only diagnostics (cached canonical event set) | 288 configs decomposed into gross/drag/net; rule, geometry, coin, sub-period and funding splits; cost sensitivity (§4) |
| gross-outcome SPA (dev only) | `best_t = +3.7149`, `p = 0.0050`; net-outcome SPA `p = 1.0000` |
| reversal-signal robustness (dev only) | gross-positive at 36/36 geometries, 5/5 sub-periods, both CUSUM thresholds; net negative at 36/36 |
| independent audits (3, read-only) | signal/event SOUND; metrics/statistics SOUND; cost/split **DEFECT found and fixed** (funding attribution/reporting only; friction-only sound) |
| `python3 -m proto.quant evaluate --help` | shows `--coins` |
| independent code review (subagent) | 2 must-fix docs issues + hardening; all addressed in `e51ce1c` |
| `tests/test_app.py` (venue-network smoke) | excluded — needs a live venue |

## 8. Commits

- baseline (pre-existing, factually wrong snapshot): `17e9a74`.
- `faa7583` docs(quant): correct 2026-10-18 data-readiness snapshot.
- `168b960` feat(quant): minimum-coverage policy + forward `--coins` in evaluate.
- `f44e1dc` docs(quant): 2026-10-18 roadmap-completion progress and final report.
- `e51ce1c` fix(quant): harden coverage policy and `--coins` wiring (review round).
- `4419f49` feat(quant): persist WS5 selection identity in evaluation report.
- `da5f43c` feat(quant): read-only per-coin settlement backfill audit.
- (2026-10-19 update) docs(quant): per-coin settlement manifest, the
  selected-strategy identity, preserved evidence files, and tightened
  no-edge / reproducibility wording (this report + `docs/roadmap-status.md`
  + `docs/ws4-settlement-history.md`).
- `83b9a22` fix(quant): correct pipeline and funding-attribution audit
  defects — four pipeline fixes (run-boundary path, CUSUM reset, union
  archive span, sparse-day guard), four funding fixes (window-local settle
  grid, TIME-exit close, matched denominator, 289-bar leakage bound), two
  regression test files, the dev-only diagnostics, and the corrected
  funding-adjusted numbers.
- `9c9bdcd` fix(quant): make funding attribution cycle-transition aware —
  the venue's per-regime settlement schedule (8h→4h / 4h→8h), the
  observation fallback on the same schedule, an unestablishable schedule
  reported incomplete, the transition audit (219 dev / 99 val crossing
  windows, all attributed), the recomputed funding-adjusted numbers, and
  the exploratory labelling of the gross-outcome SPA with H1 downgraded to
  a candidate for preregistration.
- A final docs-only commit records this hash and the suite result;
  `origin/main` equals the resulting tip (verified with `git fetch`).
- No raw archive was rewritten; `data/` is gitignored and untouched by the
  research runs (output went to a temp `--out` dir).

## 9. Blockers / open items

- Externally blocked: OI history, book/spread history (no public endpoint).
- Needs owner approval: service restart (`/settlements`, `/prices`,
  `/quant-status`); WS3 1m design; provisional tier.
- Open (implementable, evidence-gated): mark/index collection; WS5
  calibration record + estimators (Hurst/MF-DFA) + competing outcomes
  (stop/tp/time/invalidation/censor, proposal §3.4) + calibrated
  probabilities (proposal §3–§5); WS6 broader evidence display; WS7
  champion/candidate + gate records. **The best of all 288 pre-declared
  configs still had a negative dev day-cluster t (−1.06, SPA p = 1.0) and
  0/288 had a positive dev mean, so nothing cleared dev→val** — there is
  nothing to promote: the registry stays `research` and
  `docs/decisions/log.jsonl` stays empty until a config shows a positive,
  CI-clearing edge. These items are deliberately not forced.
- **Dev-only follow-up (2026-10-19):** the negative result is friction, not
  a sign/label/metric defect (gross median +0.0012R; selected gross +0.0040
  dev / −0.0015 val; drag 0.0194R). One pre-cost short-horizon reversal is
  detectable in a **post-hoc, exploratory** gross-outcome SPA (p = 0.005;
  the metric and analysis were chosen after seeing the net results, so the
  multiplicity correction does not cover that choice) and it is smaller
  than friction at every geometry. Nothing is promoted. H1 (fade residual
  shock, `T48`, `k_stop` 1.5–3, `r_tp` 2–3) is a **candidate for
  preregistration, not a confirmed edge**: its break-even needs round-trip
  friction ≤ ~0.052R (≈15 bp) and **no execution-cost evidence in this
  repository shows that is reachable** (the 5 bp/side taker constant is an
  unconfirmed placeholder, slippage is assumed rather than measured, and
  there are no fill/depth/latency data). An operator-confirmed fee tier and
  a measured execution-cost model must precede any preregistration; do not
  spend the one-shot holdout on exploration (§4).

## 10. Deployment / live-verification checklist (restart PENDING owner approval)

1. Confirm no collector cycle in flight; `systemctl --user stop altradar`.
2. Deploy `HEAD`; `systemctl --user start altradar`.
3. Verify `journalctl` shows the loop collecting, then authenticated
   loopback probes: `/healthz` 200, `/readyz` 200, `/scanner-health` 200,
   `/funding?coin=BTC` 200, and the newly-live `/settlements?coin=BTC`,
   `/prices?kind=mark&coin=BTC`, `/quant-status` (200, SHADOW).
4. Confirm no quant emission fired (`quant.shadow` counts-only).

## 11. Evidence files (preserved with this report)

Preserved in this directory; hashes are SHA-256 of the exact bytes, so the
claims above can be re-checked without trusting this prose.

| File | SHA-256 | What it is |
|------|---------|------------|
| `evaluation.json` | `81affa4ad5d1661f7a2b55f3e776f74bea65ef7ec136f7b1f70c1836f513242c` | Canonical WS5 dev/val run **with selection identity** (`breakout_start[rvol>=4]/cont`, `T288_ks3_R2`; regenerated 2026-10-19, no `--holdout`). |
| `evaluation-prior-concurrent-17e9a74.json` | `011663b701b7cb7cc6ef6108a51d955ca141e67db08c72a2c3ff29a21a38ea60` | Independent concurrent run at `17e9a74` — the source of the digest comparison in §4. |
| `settlement-backfill-manifest.json` | `de4334ae47706c809611296d49b86d69e3fefc373dbb9e0c342612b79fe9f36e` | Per-coin settlement backfill manifest (579 bar-universe coins, read-only). |
| `settlement-backfill-manifest.csv` | `45e4b666fc8f24d75c946a9c70668f5c513d5bba1cf44790393ef6c9f6718997` | The same manifest as a flat CSV (one row per coin). |
| `dev-diagnostics.json` | `2dd3d79d3be4293cc429fa3d09660bf430a28be1fafb87f22deb605606ddc86c` | Full dev-only diagnostics: all 288 configs' gross/net/drag, rule/geometry/coin/sub-period splits, and cost-sensitivity tables (2026-10-19). Its funding block is **pre-audit-B** and superseded by `funding-fixed.json`. |
| `dev-diagnostics-summary.json` | `ff709cf5a8b7658c8dabc823ec836adfe53705bdb908afdc772432ddbe8d2cf4` | Compact extract of the above, including the 36-geometry and 5-sub-period reversal tables used in §4. Funding block pre-audit-B. |
| `gross-spa.json` | `36d210cda5aabf5b752c9d8b0b214ce628c054cb777b3895d6a856cf0a887332` | Pre-cost vs net SPA results: `gross p=0.0050`, `net p=1.0000`. |
| `archive-span.json` | `5d8625319eddae835ab72e599f86612bbf0d7aec0a88333f76131eee48a69340` | Read-only 579-file archive span scan (union vs BTC; 15,372,713 rows). |
| `post-fix-recompute.json` | `d836b26f412055d37cafc3795e4b070bc1f2df720b5f44f9985cae8e7b153e08` | `run_stage_dev_val` after the four regression fixes — identical to the canonical dev/val result. |
| `signal-characterization.log` | `d598c2f1430d79e1dfba1a30c65c7316efe4cb45c8ea5db56bcc5dd6ea3b3633` | Console output of the reversal-signal characterization (36 geometries, 5 sub-periods, coin breadth). |
| `run.log` | `9a21f580260bfd1f9416ef95baece3c79370049db2fef51221bad41b446a9de3` | Console output of the main dev-only diagnostics, including the cost-sensitivity table. |
| `funding-fixed.json` | `26a82fc801c1068acc5cf8e994f49813f065634062023aa0ac8eb23984f4719a` | First correction round (window-local grid, matched denominators) — **superseded** by `funding-transitions.json`. |
| `funding-transitions.json` | `622cee87d50964db62ce660335fcd9b41b24146f9d5d120bf54fbba3c20e038e` | **Current** funding-adjusted dev/val (per-regime schedule, matched denominators) plus the cycle-transition audit counts (219 dev / 99 val / 1,602 of 197,724 windows); supersedes the funding blocks in `evaluation.json`. |
| `cycle-semantics.json` | `3747560b794bcd81eca1229256d15d981b0cd697cbe4084508e7ee7f2274d706` | Read-only audit of all 559 settlement archives: MEXC `collectCycle` semantics (gap-to-next-row vs the current row's cycle), the 512 venue-stated transitions and their bridge shapes. |
| `transition-attribution.json` | `666af808523084924f0d06552be7f07f0f780e10d11fcd3b2683b5f1b42aebb4` | Per-window outcome for the evaluation windows that cross a cycle transition: 219 dev / 99 val, all attributed (none excluded). |

Both evaluation artifacts keep `holdout`, `holdout_slip2x` and the two
`holdout*_funding_adjusted` blocks **null** — the one-shot holdout is SEALED.
No production archive was modified to produce any of these: `evaluation.json`
is written by the harness to its own `--out` dir (never `data/`), and the
manifest is produced read-only.
