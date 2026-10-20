# ALT RADAR — quant research capabilities + startup optimization

**Progress report** · folder: `docs/implementation-reports/quant-research-2026-10-19/`
**Started:** 2026-10-19 · **Base commit:** `4f9d7a7` · **Status:** planning

> This folder is the live status record for this effort. Older reports are
> preserved untouched; superseded figures are marked, never rewritten.

---

## 1. Scope

Six workstreams (A–F) as briefed: market measurement & structure (A),
outcomes & inference (B), preregistered research workflow (C), Research UI
expansion (D), champion/candidate tracking (E), startup & first-sweep
optimization (F).

Design and plan: `docs/superpowers/specs/2026-10-19-quant-research-and-startup-design.md`.

## 2. Baseline verification (read-only, no service changes)

Verified against the tree and the running unit; **not** inherited from the roadmap.

| Finding | Evidence |
|---|---|
| No scientific stack: pandas/numpy/scipy/statsmodels/sklearn all absent; codebase is stdlib-only | no requirements/pyproject in repo; `proto/quant/__init__.py:7`; import probe fails |
| HTTP listener is up in <1 s; the 65.8 min is the **readiness gate**, not startup | `proto/service.py:2009-2017` (bind+serve) before `:2085-2087` (loop thread) |
| `/readyz` = "first sweep complete" | `sweep_done.set()` only at `proto/daemon.py:471-487` |
| Restart re-runs scan/collect/resolve/measure (process-local due-times) | `proto/daemon.py:243-245` → `0.0`; only settlements/prices persist a throttle |
| Collect cost dominated by re-parsing every archive for dedupe | `proto/collector.py:27-41` → `read_bars` full parse; 579 files / 287 MB |
| Quant step rebuilds every coin on first boot; watermark persisted but never read | `proto/quant/qstore.py:48-50`; `proto/quant/engine.py:102-121` |
| `/readyz` performs a **write** probe on the live 170 MB WAL DB | `proto/service.py:443-459` |
| Each sweep is recorded **twice** (2 journal rows + 2 dashboard rows) | `proto/app.py:192` and `proto/daemon.py:181` |
| Promotion unreachable by design (watcher pins `promo=None`; route hard-codes `mode:"shadow"`) | `proto/quant/engine.py:198`; `proto/service.py:1433`; `tests/test_quant_shadow_only.py` |
| Holdout sealed; decision log 0 bytes; no `champion` concept anywhere | `evaluation.json` holdout fields `null`; `docs/decisions/log.jsonl` 0 B; `grep champion *.py` → 0 |

### Measured baseline (live unit, pid 572048, started 15:46:39)

| Stage | Elapsed |
|---|---:|
| HTTP listener | ~0 s |
| scan | 458 s |
| collect | 851 s |
| funding | 859 s |
| settlements | 1 380 s |
| prices | 2 114 s |
| measure | 2 939 s |
| **/readyz → 200** | **3 951 s (65.8 min)** |

`last_sweep_duration_s = 3950.25` — one number for eight phases. No
per-stage instrumentation exists (`app.last_scan_phases` is written but
never read; the quant step's `seconds` return is discarded).

## 3. Workstream status

| WS | Scope | State |
|---|---|---|
| A | measurement & structure engine | **merged** (`af1814e`) — 3 fix rounds, re-reviewed, approved |
| B | outcomes & inference | **merged** (`280e41c`) — fix round 1, re-reviewed, approved |
| C | preregistered research workflow | **merged** (`c14bda1`) — fix round 1, whole-branch review **done** (no ship blockers) |
| D | Research UI expansion | **merged** (`6d20e87`) — fix round 1, whole-branch review **done** (no ship blockers) |
| E | champion/candidate tracking | **merged** (`93bf6ee`) — fix round 1, whole-branch review **done** (no ship blockers) |
| F | startup & first-sweep optimization | **merged** (`dacf7e7`) — fix round 1, re-reviewed, approved |

The whole-branch review of the integrated `main` is **done**: it found no
ship blockers and a set of fix-before-ship items (two honesty defects,
several small code defects and several documentation statements that did
not reproduce or were stale).  Those items are fixed in the final review
fix wave (see the "Final review fix wave" section of `final-report.md`).

Integration on `main`: the offline backend suite is **ALL SUITES PASS (87
files)**; the frontend gates are 200 tests / 14 files, typecheck clean, lint
(0 warnings) clean and the build clean. One merge conflict was resolved by
the orchestrator (`proto/quant/__main__.py` — both the `prereg` and the
`champions` subcommands were kept; neither can promote or spend the
holdout).

## 4. Owner decisions taken (2026-10-19)

1. **`/readyz` changes meaning** to API-serving readiness (200 once the
   listener is up and the DB is *readable* via a read-only probe). The
   first-sweep status moves into `/scanner-health` as a distinct
   `first_sweep` block (`pending` / `in_progress` / `complete`, with
   `completed_ts` and per-stage durations). Nothing may report a completed
   sweep that did not complete.
2. **All six workstreams are attempted**, staged by wave, and any workstream
   that ends partial is reported as partial.
3. **The new hash-chained decision log is a separate artifact**
   (`docs/decisions/chain.jsonl`). The human-owned
   `docs/decisions/log.jsonl` stays 0 bytes and untouched.

## 5. Execution waves

| Wave | Workstreams | Isolation | State |
|---|---|---|---|
| 1 | F, A, B | separate git worktrees, disjoint file ownership | **done** — all three merged and re-reviewed |
| 2 | C, E | separate worktrees; depend on A/B contracts | **done** — both merged (fix round 1 each) |
| 3 | D | separate worktree; depends on the A/B/C/E API contracts | **done** — merged (fix round 1) |
| 4 | whole-branch review + full suites + integration | orchestrator + fresh reviewer | **review done** (no ship blockers); the fix-before-ship items are fixed in the final review fix wave |

Ownership map (no two agents may edit the same file concurrently):

| WS | Files owned |
|---|---|
| F | `proto/daemon_timing.py`, `proto/daemon.py`, `proto/service.py`, `proto/collector.py`, `proto/quant/engine.py`, `proto/quant/qstore.py`, `proto/app.py` (snapshot suppression only) |
| A | `proto/quant/structure.py`, `proto/quant/estimators.py`, `proto/quant/feature_store.py` |
| B | `proto/quant/competing_outcomes.py`, `proto/quant/inference.py`, `proto/quant/family_test.py` |
| C | `proto/quant/prereg.py`, `proto/quant/research_report.py`, `docs/hypotheses/*` |
| E | `proto/quant/champion.py`, `proto/quant/decision_log.py` |
| D | `app/src/**`, `app/tests/**`, `app/dev/**` |

Protected (read-only to every workstream): `proto/quant/stats.py`,
`proto/quant/barrier.py`, `proto/quant/cost_attribution.py`,
`proto/quant/research.py`, `proto/quant/eval_harness.py`,
`proto/quant/gates.py`, `proto/quant/registry.py`, `proto/decisions.py`,
`proto/hypothesis_registry.py`, `data/**`.

## 6. Safety status

- Live service: **not** restarted, **not** altered (operator drop-in
  `TimeoutStopSec=600s` untouched).
- Production archives: not modified, not backfilled.
- Holdout: sealed, untouched. ACTIONABLE output: still disabled. No
  promotion performed. `docs/decisions/log.jsonl` still 0 bytes.


- Task B: **APPROVED** by the scoped re-review (all 9 findings addressed; 6 minor
  report/doc nits deferred to the final review). Merged into `main` as `280e41c`.
- Task A: round 2 addressed every behavioural finding; the re-review left three
  documentation findings partially open (wrong applicability thresholds, a stale
  "H in [0,1]" claim in the report body, a stale `qstore.py` citation). Round 3
  (documentation + one `KeyError` line) committed as `ef438e6`; scoped re-review in flight.
- Task E: implemented (commits 2d7ec8f RED-first, 5bef58d, e17d5cf), full suite
  ALL SUITES PASS (84 files) in its worktree; fresh task reviewer in flight.
  Nothing promoted: `docs/decisions/log.jsonl` still 0 bytes, `chain.jsonl` absent
  from the repository, no `data/promotions.json`.
## Ledger rulings (decisions taken on the owner's behalf)

Preserved from the scratch SDD workspace so the decisions stay auditable.

Rulings:
- **Ruling: B does not import A's `estimators` module in wave 1.** A and B run
  in parallel worktrees and neither can see the other's code. B implements
  the small regime/breadth/dependence helpers it needs locally, and wave-2
  tasks (C) may adopt A's `estimators` once merged. Cost if wrong: a small
  amount of duplicated helper code in wave 1.
- **Ruling: `/readyz` changes meaning (API-serving readiness) and the
  first-sweep status moves into `/scanner-health` as a `first_sweep` block.**
  Owner approved. The frontend (wave 3) must be updated to match; the old
  503 "first sweep not complete" meaning must not survive anywhere.
- **Ruling: the new hash-chained decision log is a SEPARATE artifact**
  (`docs/decisions/chain.jsonl`); `docs/decisions/log.jsonl` stays 0 bytes and
  untouched. Owner approved.
- **Ruling: all six workstreams are attempted**, staged by wave, and any
  workstream that ends up partial is reported as partial (owner approved).

Rulings made in this wave:
- **Ruling: the Pivot field name stays `leg_bars`** (the brief's name); the plan's
  §1.2 said `bars_of_leg` and the plan is aligned to the shipped name. Cost if
  wrong: a rename in the plan, no code change.
- **Ruling: the Hurst/MF-DFA estimators keep their names but take a `levels`
  parameter** (a level path), with `level_from_returns()` as the explicit bridge.
  The brief's parameter name (`returns`) and its acceptance criterion 4 pull in
  opposite directions; the criterion wins. Cost if wrong: a rename.
- **Ruling: adverse excursion (MAE) is documented as not computable** from the
  research event contract (no bar path), rather than implemented. Cost if wrong: MAE
  is missing from the risk measures.
- **Ruling: the placebo null is described as "retains the rule's average edge"**,
  not as signal-free. Cost if wrong: a mis-stated limitation in the report.

## 2026-10-20 — pivot provenance fix + deployment

- **Finding (feature provenance).** `feature_store.from_pivots()` hashed
  the coin/name/version/index/ts/price but NOT the pivot configuration
  (`left`/`right`/`frame_bars`), and stored the record under the bare
  `pivots-v1`; with the store's uniqueness key and a silent
  `INSERT OR IGNORE`, a later record computed with different settings was
  dropped and the earlier record (and its `available_ts`) stayed in place.
  No production caller and no stored data were affected.
- **Fix (`c8647b5`).** The feature identity is the measure version PLUS
  the configuration identity (`pivots-v1+L2R2F1`); `from_pivots()` requires
  and cross-checks the configuration against the pivots themselves; the hash
  covers it; the configuration is recorded in the meta; a same-key write
  with different content is refused as a provenance conflict instead of
  being silently ignored. 18 new regression checks; RED verified against the
  pre-fix code; `tests/test_ws_a_measurement.py` ALL PASS;
  `tests/run_all.py` → ALL SUITES PASS (87 files). No frontend change, so
  the frontend gates were not re-run.
- **Deployment (owner-authorized).** The local user systemd service was
  restarted with a clean tree at `c8647b5` (new pid 732289, up 2026-10-20
  11:32:58 WAT; only a documentation commit followed). `/healthz` and `/readyz` answered 1 s after the start;
  `/scanner-health` reported `first_sweep: in_progress` (completed_ts null)
  until the sweep finished at **1 959 s** with the new per-stage
  attribution. SHADOW-only, no promotion, no holdout artifact, no archive
  rewritten, the operator's drop-in untouched. Evidence:
  `deployment-report.md`.
