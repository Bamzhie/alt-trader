# ALT RADAR Quant Roadmap — Progress and Final Report

**Date:** 2026-10-18 — **updated 2026-10-19** (per-coin settlement manifest,
selected-strategy identity in the evaluation report, evidence files preserved,
tightened no-edge wording and reproducibility notes)
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
| dev (funding-adjusted) | 24,311 | 55 | 364 | −0.01485 | [−0.04114, +0.01526] | −1.007 |
| val (funding-adjusted) | 6,374 | 14 | 241 | −0.01633 | [−0.04725, +0.01836] | −0.978 |

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
  dev 24,311 + 5 (missing coin) + 433 (incomplete) = **24,749**;
  val 6,374 + 0 + 121 = **6,495**. Funding coverage ≈0.982.
  `missing_cost_coins = [1000BTT, RON, ZBCN]`; `leakage_violations = []`;
  incomplete-boundary 554 / 31,244 events; digests bars 450 / funding 447 /
  settlements 443.
- **In this run every dev/val interval straddles zero and every point
  estimate is negative: it found no evidence of a positive edge.**
  Intervals are **95% day-cluster bootstrap** intervals (resampled over
  trading days, the harness's unit of independence). They do **not**
  establish robustness across coins or across sub-periods — no coin-level
  or period-split analysis was run — so a future snapshot or a different
  universe could differ. Friction-only and funding-adjusted are reported
  **separately** (never merged); funding adjustment moves the mean by only
  ~+0.0005.
- **The one-shot holdout stays SEALED:** `holdout`, `holdout_slip2x`,
  `holdout_funding_adjusted`, `holdout_slip2x_funding_adjusted` are all
  `null`. No decision to spend it has been made, and none is warranted
  without a positive, CI-clearing dev/val edge.
- **Reproducibility across archive snapshots:** the two 2026-10-18 runs —
  this one at `e51ce1c` and an independent concurrent run at `17e9a74` —
  produced **identical dev/val statistics** (byte-identical `dev` and `val`
  blocks, including both funding-adjusted ones) under the same
  `config_hash d33cff513c30` / `cost_hash 762515f702934998`. The runs are
  **not**, however, over identical inputs:
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
| `python3 tests/run_all.py` | **ALL SUITES PASS (79 files, 0 failures)** |
| `python3 -m proto.bar_quality --data-dir data/bars --all --json` | 578 coins; 0 gaps / 0 dups / 0 invalid; ratio 1.0 |
| `python3 -m proto.quant.settlement_audit …` | `EXIT=0`; 579 coins → 544 / 7 / 28 / 0 (§1, §11) |
| `python3 -m proto.quant evaluate …` (no `--holdout`) | `EXIT=0`; numbers + selected identity in §4 |
| selection-identity cross-check (`run_stage_dev_val` recompute from a cached event set) | matches the harness exactly: rule, geometry, params, `n_configs`, `spa`, `dev`, `val` |
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
- Final doc update committed and pushed last; `origin/main` equals that tip
  (verified with `git fetch`).
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

Both evaluation artifacts keep `holdout`, `holdout_slip2x` and the two
`holdout*_funding_adjusted` blocks **null** — the one-shot holdout is SEALED.
No production archive was modified to produce any of these: `evaluation.json`
is written by the harness to its own `--out` dir (never `data/`), and the
manifest is produced read-only.
