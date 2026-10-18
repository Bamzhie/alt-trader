# ALT RADAR Quant Roadmap — Progress and Final Report

**Date:** 2026-10-18
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
- **Truncation:** 7 coins are capped at exactly 5000 rows by the 5×1000
  page cap (FLOCK, LA, LAYER, MEGA, MYX, SIGN, XTZ); LAYER's venue
  `totalCount=5325` → ≥325 rows missing. 345 coins share the venue
  retention floor `1745683200` (2025-04-26).
- **Reconciliation:** 27 bar-universe coins have **no settlement archive**
  (mostly delisted names: ALLINU, COOL, BOBA, DBR, DEBIT, DELTA, GEOD,
  NPC, RON, SWARM, TFUEL, UPROBNHOOD, ZBCN, ZIG, …); 8 settlement coins are
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

## 4. WS5 evaluation (holdout SEALED)

**My run** (reproducible; no `--holdout`), from `HEAD = e51ce1c`:

```
python3 -m proto.quant evaluate --data data/bars --funding-dir data/funding \
  --settlements-dir data/funding_settlements --out /tmp/opencode/ws5-eval-1792331507
# EXIT=0  -> /tmp/opencode/ws5-eval-1792331507/evaluation.json
# sha256 5893481240e52d9ffb7e4b02e55b4a33ef3759b069a25547d803995afb8e69f5
# config_hash d33cff513c30  cost_hash 762515f702934998
# splits dev(6,61) val(63,77) holdout(79, 97.4514)  n_days 97.4514
```

| Split | n | days | coins | mean_r | 95% CI | t |
|---|---:|---:|---:|---:|---|---:|
| dev (friction-only) | 24,749 | 55 | 375 | −0.01542 | [−0.04171, +0.01424] | −1.062 |
| val (friction-only) | 6,495 | 14 | 248 | −0.01661 | [−0.04676, +0.01742] | −1.013 |
| dev (funding-adjusted) | 24,311 | 55 | 364 | −0.01485 | [−0.04114, +0.01526] | −1.007 |
| val (funding-adjusted) | 6,374 | 14 | 241 | −0.01633 | [−0.04725, +0.01836] | −0.978 |

- **Denominators reconcile** exactly (friction-only = funding-adj + excluded):
  dev 24,311 + 5 (missing coin) + 433 (incomplete) = **24,749**;
  val 6,374 + 0 + 121 = **6,495**. Funding coverage ≈0.982.
  `missing_cost_coins = [1000BTT, RON, ZBCN]`; `leakage_violations = []`;
  incomplete-boundary 554 / 31,244 events; digests bars 448 / funding 445 /
  settlements 441.
- **Every dev/val CI straddles zero and the point estimates are negative →
  no edge.** Friction-only and funding-adjusted are reported **separately**
  (never merged); funding adjustment moves the mean by only ~+0.0005.
- **The one-shot holdout stays SEALED:** `holdout`, `holdout_slip2x`,
  `holdout_funding_adjusted`, `holdout_slip2x_funding_adjusted` are all
  `null`. No decision to spend it has been made, and none is warranted
  without a positive, CI-clearing dev/val edge.
- **Reproducibility:** an independent concurrent run at `17e9a74`
  (`/tmp/ws5-eval-final/evaluation.json`, sha256 `011663b7…`, same
  `config_hash`/`cost_hash`) produced **identical dev/val statistics** — only
  `n_days` differed (97.399 vs 97.451) because the collector appended bars
  between the runs (the dev/val windows are unaffected).

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
| `python3 tests/test_quant_coverage.py` | ALL PASS (37 assertions) |
| `python3 tests/test_ws5_eval_harness.py` | ALL PASS (123 checks; case 23 added) |
| `python3 tests/run_all.py` | **ALL SUITES PASS (78 files, 0 failures)** |
| `python3 -m proto.bar_quality --data-dir data/bars --all --json` | 578 coins; 0 gaps / 0 dups / 0 invalid; ratio 1.0 |
| `python3 -m proto.quant evaluate …` (no `--holdout`) | `EXIT=0`; numbers in §4 |
| `python3 -m proto.quant evaluate --help` | shows `--coins` |
| independent code review (subagent) | 2 must-fix docs issues + hardening; all addressed in `e51ce1c` |
| `tests/test_app.py` (venue-network smoke) | excluded — needs a live venue |

## 8. Commits

- baseline (pre-existing, factually wrong snapshot): `17e9a74`.
- `faa7583` docs(quant): correct 2026-10-18 data-readiness snapshot.
- `168b960` feat(quant): minimum-coverage policy + forward `--coins` in evaluate.
- `f44e1dc` docs(quant): 2026-10-18 roadmap-completion progress and final report.
- `e51ce1c` fix(quant): harden coverage policy and `--coins` wiring (review round).
- Final doc update (this report + `docs/roadmap-status.md`) committed and
  pushed last; `origin/main` equals that tip (verified with `git fetch`).
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
  champion/candidate + gate records. **Because no config cleared dev→val,
  there is nothing to promote** — the registry stays `research` and
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
