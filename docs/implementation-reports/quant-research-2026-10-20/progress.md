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

| # | Task | Owner | State |
|---|---|---|---|
| 1 | Pivot configuration-identity alignment (TDD) | orchestrator | pending |
| 2 | Report/status corrections (resolve overlap, code-revision wording) | orchestrator | pending |
| 3 | Read-only data-coverage audit | subagent (read-only) | pending |
| 4 | "BTC bars stale" diagnosis (read-only) | orchestrator | pending |
| 5 | Preregistration + analysis protocol | orchestrator | pending |
| 6 | Dev/validation run; holdout decision | orchestrator | pending |
| 7 | Manual-review signal display path (code + API + UI) | orchestrator | pending |
| 8 | Fresh review, verification, final report | subagent + orchestrator | pending |

## 4. Findings log

| When | Finding | Resolution |
|---|---|---|
| 2026-10-20 | `pivot_config_id()` rejects `left=0`/`right=0` while `structure.confirmed_pivots()` accepts them — the feature-identity helper is stricter than the detector it identifies. | Task 1 (TDD) |

## 5. Verification log

| Check | Command | Result |
|---|---|---|
| — | (filled as work proceeds) | — |

## 6. Safety status

- Live service: running the deployed code commit `c8647b5` (pid 732289);
  untouched so far in this effort.
- Holdout: sealed (`data/quant_research/` absent).
- Decision log: 0 bytes; no chain file; no promotion record.
