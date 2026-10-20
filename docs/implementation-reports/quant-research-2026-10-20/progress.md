# ALT RADAR — pivot fix, defensible quant analysis, and manual-review signals

**Progress report** · folder: `docs/implementation-reports/quant-research-2026-10-20/`
**Started:** 2026-10-20 · **Base commit:** `d5097cb` · **Status:** planning
**Plan:** `docs/superpowers/plans/2026-10-20-pivot-fix-and-quant-analysis.md` (local, gitignored)

> This folder is the live status record. Older reports are preserved; superseded
> figures are marked, never rewritten.

---

## 1. Scope

**Part 1 — fix the review findings**

1. `pivot_config_id()` must accept the same settings the pivot detector
   accepts (`left=0`, `right=0` are valid for
   `structure.confirmed_pivots`), with TDD regression coverage.
2. `deployment-report.md` and the directly affected status text: the
   `resolve` stage runs in the background and **overlaps** the foreground
   sweep (its duration is not additive to the 660.8 s), and the
   "current tip" / code-revision statements must distinguish the **deployed
   code commit `c8647b5`** from the later documentation-only commits.

**Part 2 — start defensible quant analysis on the collected data**

3. Read-only data-coverage audit (per coin, funding/settlements, usable
   universe, split boundaries) and a read-only diagnosis of the
   "BTC bars stale" pause that does **not** weaken the pause guards.
4. A **versioned preregistration** frozen *before* new results are
   examined, then dev+validation only, then a one-time holdout spend
   **only if** a valid configuration can be frozen.
5. A **manual-review signal display path**: rule-generated long/short
   candidates with entry/invalidation/exit, freshness, evidence status,
   denominators and an explicit "not executed by the app" statement — with
   no execution path and no weakening of the SHADOW/ACTIONABLE contract.

## 2. Safety boundaries (binding)

- **No order execution, trading credentials, account endpoints or
  automated trades.** Signal generation is in scope; execution is not.
- No bypass of stale-data or pause guards; no falsified freshness.
- No archive rewritten (bars, funding, settlements); no venue backfill.
- The one-shot holdout is spent **at most once**, only on a configuration
  frozen from dev+validation, and never tuned on afterwards.
- A promotion/validation result never authorises order placement.
- The live service is left safe; a restart only if needed to load
  completed code, preserving the systemd settings.

## 3. Task ledger

| # | Task | State |
|---|---|---|
| 1 | Pivot configuration-identity alignment (TDD) | **done** — `522647b`; RED-verified against the pre-fix code |
| 2 | Deployment/status wording corrections | **done** — the resolve stage is a background stage that overlaps the sweep; the deployed code commit is distinguished from later documentation commits |
| 3 | Read-only data-coverage audit | **done** — `data-audit.md` + `data-audit.json` (a subagent) |
| 4 | "BTC bars stale" diagnosis | **done** — the venue's klines lag the collect; the 20 min guard then refuses to emit; the guard was NOT weakened and the watcher resumed by itself |
| 5 | Preregistration + analysis protocol | **done** — HYD-0003 frozen before any result was examined |
| 6 | Dev/validation run + holdout decision | **done** — all five members negative on dev, all insufficient on validation, family p = 1.0, **the holdout was NOT spent** |
| 7 | Manual-review signal display | **done** — `proto/quant/signals.py`, `GET /quant-signals`, the Research view block; no execution path |
| 8 | Review, re-review, verification, deployment | **done** — two fresh reviewers (all findings closed), full suites green, the service restarted and verified |

## 3b. Verification log

| Check | Command | Result |
|---|---|---|
| Pivot fix | `python3 tests/test_ws_a_measurement.py` | ALL PASS |
| Analysis protocol | `python3 tests/test_quant_analysis.py` | ALL PASS |
| Signal surface | `python3 tests/test_quant_signals.py` | ALL PASS |
| Preregistration | `python3 tests/test_ws_c_prereg.py`, `tests/test_hypotheses.py` | ALL PASS |
| Full backend suite | `python3 tests/run_all.py` | **ALL SUITES PASS (87 files)** |
| Frontend | `npm run test` / `typecheck` / `lint` / `build` | 200 passed / clean / 0 warnings / built |
| Live routes | curl 127.0.0.1:18741 | all 200 with the token, 401 without |
| Safety | decision log / holdout dir / promotions.json | 0 bytes / absent / absent |

## 4. Findings log

| When | Finding | Resolution |
|---|---|---|
| 2026-10-20 | `pivot_config_id()` rejects `left=0`/`right=0` while `structure.confirmed_pivots()` accepts them — the feature-identity helper is stricter than the detector it identifies. | Fixed (`522647b`), RED-verified. |
| 2026-10-20 | The holdout wall was documentation, not enforcement (`refuse_holdout_window` had no callers). | Fixed: the wall is enforced inside `evaluate_member()` and is ON BY DEFAULT; the legacy `--holdout` path is refused while an open preregistration declares a holdout. |
| 2026-10-20 | The funding-adjusted uncertainty was computed against the FRICTION-only day/coin labels. | Fixed (own labels); the regression test now fails against the pre-fix code. |
| 2026-10-20 | `/quant-signals` was not read-only (it created the DB, ran DDL, committed). | Fixed: the store is opened `mode=ro`; a missing store is 503 and is NOT created. |
| 2026-10-20 | The universe rule as frozen was unsatisfiable (60 days covering a 55-day dev window) and the implementation added an unpreregistered gap check on the FILE order. | The rule and the implementation now agree (>= 60 days, no malformed rows, no gap in the SORTED series); the amendment was made before any result was examined. |
| 2026-10-20 | The decision criteria were not applied as written (criterion 7 missing; the placebo optional and comparing a hit rate). | Fixed: criterion 7 is implemented and fails closed; the placebo is mandatory and compares the MEAN net R. |
| 2026-10-20 | The evidence artifact was trusted by path and a member could inherit another member's verdict. | Fixed: the artifact is checked against the preregistration record it names (id + config hash) and is keyed by (rule, feature identity) with no fallback. |
| 2026-10-20 | The preregistration sidecar carried a null holdout end, which the registry validator rejects. | Fixed: a numeric bound (the archive end at preregistration time). |
| 2026-10-20 | **The validation window (14 days) is shorter than the preregistered minimum (20 distinct days), so no member can be evaluated on validation.** | Reported, NOT amended: the criteria were frozen and amending them after seeing the dev numbers would be criterion-shopping. Consequence: nothing can freeze and the holdout is not spent. |


## 6. Safety status

- Live service: running the deployed code commit `c8647b5` (pid 732289);
  untouched so far in this effort.
- Holdout: sealed (`data/quant_research/` absent).
- Decision log: 0 bytes; no chain file; no promotion record.
