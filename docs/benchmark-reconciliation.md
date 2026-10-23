# Reconciliation note: quant-engine benchmark implementation

## Repo state

- **Current branch:** `engine-benchmark` (created from `research-substrate-phase1`)
- **Commit:** `acf82d0` — "Phase 0: data protection harness and engine package skeleton"
- **Working tree:** clean. The stopped agent's untracked files (`proto/quant/backtest.py`, `tests/test_quant_backtest.py`) remain untracked and uncommitted.
- **Python:** Python 3.10 is **NOT available**. Python 3.11.16 is at `/home/bamzhie/.local/bin/python3.11`. Python 3.14.7 is the system default (`python3`). Per the plan's rule 4 (run tests under the oldest supported interpreter), tests run under 3.11.
- **test_quant_backtest.py status:** This is an untracked test left by the stopped agent. It is **not** part of the original tracked baseline — it was placed on disk by the stopped agent's work. It currently fails (3 failures: `snap has rvol`, `result has coin keys`, `ALT1 found in results`) due to issues in its synthetic archive fixtures, not due to this plan. It will be quarantined in Phase 3.

## What already exists (matches plan §1)

| Plan component | Location | Status |
|---|---|---|
| Bar archive loader (unsorted, gaps) | `proto/quant/bars.py` | ✅ exists |
| Read-only mirror, content-hashed snapshots, idempotent sync | `proto/research/sync.py`, `store.py` | ✅ exists |
| Append-only run registry (identity + started before execution) | `proto/research/runs.py` | ✅ exists |
| Deterministic seeded sampling from frozen universe | `proto/research/universe.py` (`sha256-rank-v1`) | ✅ exists |
| Benchmark gate (closed until sampling design approved) | `universe.py` `SAMPLE_DESIGN_BLOCKED`, `runs.eligibility` | ✅ exists |
| Causal feature stream | `proto/quant/stream.py` (`FeatureStream`, `Cusum`) | ✅ exists |
| ROE math | `proto/quant/roe.py` (`target_plan`, `empirical_rates`, `pooled_table`) | ✅ exists |
| Barrier math / triple barrier labeling | `proto/quant/barrier.py` (`Path`, `label_path`, `label_reference`, `net_r`) | ✅ exists — referenced by plan §5 for the labeler |
| Stats: block-cluster bootstrap, SPA max-t, e-process | `proto/quant/stats.py` | ✅ exists |
| Coverage policy | `proto/quant/coverage.py` (`classify_coverage`, `audit_spans`) | ✅ exists |
| Risk sizing / cross-margin | `proto/quant/risk.py` (`MAINT_MARGIN`, `plan_levels`, `size_position`) | ✅ exists |
| Structure / pivots (point-in-time) | `proto/quant/structure.py` (`confirmed_pivots`, `multi_timeframe`, `resample`) | ✅ exists |
| Measures (regime, volatility, breadth) | `proto/quant/measures.py` (descriptive only) | ✅ exists |
| Config (frozen, versioned, hashable) | `proto/quant/config.py` (`QuantConfig`, `CostModel`, `Geometry`, `Splits`) | ✅ exists |
| Decision log (hash-chained) | `proto/quant/decision_log.py` (`append`, `validate_chain`) | ✅ exists |
| Collection-only service + read routes | `proto/service.py` (`/healthz`, `/readez`, `/collector-health`, `/bars`, `/funding`, `/settlements`, `/prices`) | ✅ exists — **no `/snapshot` route** |
| React status page | `app/src/pages/Status.tsx`, `app/src/App.tsx` | ✅ exists |
| Dev mock service | `app/dev/mock_service.py` | ✅ exists — **has `/snapshot` route** (stale, see §4) |
| Append-only DB schema (v2) | `proto/research/store.py` | ✅ exists, `SCHEMA_VERSION = 2` |

## Data directory current state

```
data/bars/                    — bar archive (collector-owned, append-only)
data/prices/                  — price indexes (collector-owned, append-only)
data/funding/                 — funding rates (collector-owned, append-only)
data/funding_settlements/     — settlements (collector-owned, append-only)
data/research/                — **does NOT exist yet** (no research DB present)
data/.cadence.json
data/.last_entries
data/.timing.json
data/collector.lock
data/scanner.lock
data/signals.db               — PROTECTED (live scanner)
data/quant.db                 — PROTECTED (live scanner)
```

**The research database (`data/research/altradar-research.db`) does not currently exist.** The only file that lived under `data/research/` was the temporary `.untouched-manifest.json`, which was created by the Phase 0 draft and has been removed. The manifest is now stored at its default location (`/tmp/opencode/alt-radar/.untouched-manifest.json`) to avoid touching `data/` without owner authorization.

## What does NOT exist yet (the plan's missing components)

- `proto/engine/` package — exists only as `__init__.py`; all modules need building.
- Schema additions: `SCHEMA_VERSION` must bump to 3; new tables (`contract`, `cohort_design`, `cohort`, `cohort_event`, `engine_version`, `evaluation`, `decision`, `coin_outcome`) must be added to `APPEND_ONLY_TABLES` with no-update/no-delete triggers.
- `docs/benchmark-contract-decisions.md` — does not exist.
- `docs/benchmark-contract.json` — does not exist.
- Research API read-only routes on the service (`/research/status`, `/research/runs`, etc.) — do not exist.
- Frontend Research page — does not exist.

## Differences from the plan author's source tree

### 1. `proto/quant/backtest.py` exists and contains the F1 hindsight defect

The plan (§1) lists `proto/quant/backtest.py` as **missing** and says Phase 3
quarantines it. In the actual checkout it is present (untracked, 336 lines).
The plan (§2.1, defect F1) correctly identifies the defect:

> `evaluate_cycle` picks each coin's signal with
> `best = max(events, key=lambda e: e["net_r"])` ... This guarantees inflated
> pass rates and cannot support any benchmark claim.

**Confirmed in actual code** — see `proto/quant/backtest.py:253`:
```python
best = max(events, key=lambda e: e["net_r"])
```
`net_r` is the realized outcome (gross_log minus cost, divided by stop distance).
The new `proto/engine/` evaluator must NOT replicate this. The plan's
Phase 3 quarantine (make `evaluate_cycle` raise unless
`allow_hindsight_for_audit=True`, plus a regression test) applies here.

The stopped agent's `tests/test_quant_backtest.py` is an untracked companion
to this `backtest.py`. It currently fails on 3 assertions in its synthetic
archive (rvol feature, coin result lookup). These are pre-existing failures
in the stopped agent's work, not regressions from this plan.

### 2. The plan mentions `data/bars/` file-copy design — overridden

The attached plan explicitly supersedes any `data/quant/bars/` file-copy
design: the engine reads `data/bars/` **through the research mirror**
(snapshot DB), via `proto/research/sync.py`'s `load_snapshot_bars`. The new
`proto/engine/` package must read bars from the research DB, not from
`data/bars/` directly. `store.validate_db_path` already enforces the boundary
(refuses `data/bars`, `quant.db`, `signals.db`).

### 3. The `/snapshot` frontend/backend route discrepancy

**Status: unconfirmed as the cause of the reported error.**

The owner reports the app displays "Snapshot unavailable — no such route:
/snapshot". This message is **not** produced by any code currently in the
checked-out branch. Tracing the verified route sets:

- **Real service** (`proto/service.py`, current `main` branch): routes are
  `/healthz`, `/readyz`, `/collector-health`, `/bars`, `/funding`,
  `/settlements`, `/prices`. **No `/snapshot` route.** A request to
  `/snapshot` returns `{"ok": false, "error": {"code": "NOT_FOUND",
  "message": "no such route: /snapshot"}}` (see `proto/service.py:303`).

- **Dev mock service** (`app/dev/mock_service.py`, current `HEAD`): serves
  `/snapshot` with the old scanner contract (setups, market overview, trade
  plans, SSE events). This is the stale route set.

- **Client** (`app/src/api/client.ts`, current `main` branch): calls
  `/readyz` and `/collector-health`. It does **not** call `/snapshot`.

- **Frontend** (`app/src/pages/Status.tsx`, current `main` branch): does
  **not** call `/snapshot`.

The "Snapshot unavailable — no such route: /snapshot" message is NOT
produced by the current code in any of these files. It could come from:
  1. A stale app build (bundle from a prior branch that expected `/snapshot`),
  2. A running service instance from a different branch (e.g., `feature/ws-d-ui`
     or `trader-dashboard` which have scanner routes),
  3. A modified client in another worktree.

**This cause is unconfirmed until the exact running app build, API base
URL, and requested endpoint are identified.** The plan says "Do not add a
generic `/snapshot` route solely to accommodate a stale client." The fix is
the plan's Phase 9 Research API routes + a frontend Research page.

#### Verified route discrepancy (separate from the cause)

The dev mock service (`app/dev/mock_service.py`) and the real service
(`proto/service.py`) have **different route sets**. The dev mock serves
`/snapshot`, `/setups`, `/setup/`, `/metrics`, `/tradeplan/`, `/quant-status`,
`/quant-research`, `/events` — none of which exist on the real service. The
real service serves `/collector-health`, `/funding`, `/settlements`, `/prices`
— none of which exist on the dev mock. The dev mock and the real service
cannot be drop-in compatible: a client built against the mock will get
404s against the real service.

### 4. Frontend "Starting…" issue

`app/src/App.tsx` calls `client.healthz()` (which fetches `/healthz`) on mount
via the `useConnectionState` hook. The real service serves `/healthz`
without auth, returning `{"alive": true}`. The hook transitions to `"connected"`
on success, `"error"` on failure. If the service is not running, or the token
is invalid, `healthz()` fails and the hook flips to `"error"` → "Connection
error". 

The hook does NOT currently distinguish 401 (unauthorized), 404 (missing route),
or timeout — it lumps all errors into a generic `"error"` state. The plan
requires: "The UI must leave 'Starting…' on success, failure, timeout,
unauthorized response, and missing route; show a usable state instead of
spinning indefinitely." The `readyz()` path in `client.ts` already handles
`UNAVAILABLE` → returns `false`, but `healthz()` has no error granularity.
The fix (Phase 9) hardens the connection state handling.

### 5. Baseline test status

Running `python3.11 tests/run_all.py` (Python 3.11.16):

- **26 suites pass** (27 test files minus test_app which is excluded).
- **1 file fails**: `test_quant_backtest.py` — the stopped agent's untracked
  test (3 failures in synthetic archive assertions).

The pre-existing `test_quant_backtest.py` is **untracked** — it was placed on
disk by the stopped agent. It is NOT part of the original tracked codebase.
It will be quarantined in Phase 3 per the plan.

## Summary

The stopped agent's work (`proto/quant/backtest.py` + `tests/test_quant_backtest.py`)
is confirmed and contains the exact F1 hindsight-selection defect documented
in the plan. The new `proto/engine/` package will quarantine it (Phase 3) and
build the correct outcome-independent path on top of the existing
`proto/research/*` substrate (sync, store, runs, universe) and `proto/quant/*`
primitives (barrier, labeler math, roe, stream, stats, structure, measures,
coverage, risk, config, decision_log).
