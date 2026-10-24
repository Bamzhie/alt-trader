# Reconciliation note: quant-engine benchmark implementation

> **Dated current state** (2026-10-24T20:52Z UTC, re-verified).
> - Branch: `engine-benchmark` at `cd5a2ab` — no commits since; all Phase 1 work is in the working tree, uncommitted by owner instruction.
> - Working tree: `docs/benchmark-reconciliation.md` (modified — see "This file's own git status" below) and untracked: `docs/benchmark-contract-decisions.md`, `implementation status/`, `proto/engine/{__main__,baseline,contract}.py`, the stopped-agent pair `proto/quant/backtest.py` + `tests/test_quant_backtest.py`, and `tests/test_engine_{contract,baseline,cli}.py`.
> - **Phase 1 status:** implemented and review-verified (two review rounds closed), **awaiting [OWNER] decisions D1–D10 plus the cohort-feasibility choice** — nothing frozen, no `docs/benchmark-contract.json`, benchmark gate closed. See "Phase 1 status" below.
> - `data/research/` directory: **absent** — no manifest, no research DB (re-verified 2026-10-24).
> - Manifest location: `/tmp/opencode/alt-radar/.untouched-manifest.json` (outside `data/`; still present, mtime 2026-10-23 12:29).
> - Test suite (offline, per-file direct execution — `tests/run_all.py` is unusable in this environment because `sys.executable` points at the ZCode AppImage): **37 suites, 36 pass, 1 fail**. The only failure is the untracked stopped-agent `test_quant_backtest.py` (3 assertions, pre-existing). Focused engine suites: contract 70 / baseline 186 / cli 75 / data-protection 8 checks, all passing (re-run 2026-10-24).
>
> **This file's own git status (reconciled 2026-10-24):** `docs/benchmark-reconciliation.md` shows `M` against HEAD because the on-disk version is an **uncommitted rewrite that predates the Phase 1 fix sessions** (content dated 2026-10-23T12:12Z; file mtime 2026-10-23 15:46; `git diff` vs HEAD: 140 insertions / 146 deletions — HEAD carries an older "Repo state"-structured version). It was already `M` when the fix sessions began, those sessions did not edit it, and today's update is the first edit since. Earlier wording elsewhere ("untouched") meant "not edited by the fix sessions" and did not mean "identical to HEAD" — both facts are true simultaneously.

---

## Current state (commit cd5a2ab)

- **Branch:** `engine-benchmark` (from `research-substrate-phase1`).
- **Commit:** `cd5a2ab`.
- **Untracked stopped-agent files:** `proto/quant/backtest.py`, `tests/test_quant_backtest.py` — present on disk, untracked, uncommitted, unmodified. Preserved intact.
- **`data/research/`:** absent. No manifest, no research database.
- **Manifest:** `/tmp/opencode/alt-radar/.untouched-manifest.json` — created by `scripts/verify_data_untouched.py` with default `--manifest` (outside `data/`). Never written under `data/` without explicit `--manifest` owner action.
- **Python:** 3.10 not installed. 3.11.16 at `/home/bamzhie/.local/bin/python3.11`. 3.14.7 is `python3`. Tests run under 3.11.
- **Suite (corrected 2026-10-24):** the offline suite is **37 files: 36 pass, 1 fail** — the only failure is the untracked stopped-agent `test_quant_backtest.py` (3 failures: `snap has rvol` (rvol=None), `result has coin keys`, `ALT1 found in results` (ALT1 not in result)), pre-existing and not this work. The earlier "26 suites pass" figure counted fewer files and is superseded. `tests/run_all.py` itself is unusable in this environment (`sys.executable` → ZCode AppImage spawns Electron instead of the tests); the per-file direct loop under Python 3.11 is the honest number.

---

## What already exists (matches plan §1)

| Plan component | Location | Status |
|---|---|---|
| Bar archive loader (unsorted, gaps) | `proto/quant/bars.py` | ✅ exists |
| Read-only mirror, content-hashed snapshots, idempotent sync | `proto/research/sync.py`, `store.py` | ✅ exists |
| Append-only run registry | `proto/research/runs.py` | ✅ exists |
| Deterministic seeded sampling from frozen universe | `proto/research/universe.py` | ✅ exists |
| Causal feature stream | `proto/quant/stream.py` | ✅ exists |
| ROE math | `proto/quant/roe.py` | ✅ exists |
| Barrier math / triple barrier labeling | `proto/quant/barrier.py` | ✅ exists (plan §5 labeler) |
| Stats: block-cluster bootstrap, SPA max-t, e-process | `proto/quant/stats.py` | ✅ exists |
| Coverage policy | `proto/quant/coverage.py` | ✅ exists |
| Risk sizing / cross-margin | `proto/quant/risk.py` | ✅ exists |
| Structure / pivots (point-in-time) | `proto/quant/structure.py` | ✅ exists |
| Measures (regime, volatility, breadth) | `proto/quant/measures.py` | ✅ exists |
| Config (frozen, versioned, hashable) | `proto/quant/config.py` | ✅ exists |
| Decision log (hash-chained) | `proto/quant/decision_log.py` | ✅ exists |

---

## What does NOT exist yet (updated 2026-10-24)

- ~~`proto/engine/` package — only `__init__.py` (empty skeleton).~~ **Phase 1 now exists in the working tree** (uncommitted): `contract.py` (frozen BenchmarkContract), `baseline.py` (read-only calibration with point-in-time per-entry costs), `__main__.py` (CLI). `__init__.py` is the tracked skeleton from `cd5a2ab`. See "Phase 1 status" below.
- ~~`docs/benchmark-contract-decisions.md`~~ **Exists** (untracked, 2026-10-24): D1–D10 with the round-2 measured baseline — ends awaiting owner sign-off.
- `docs/benchmark-contract.json` — deliberately NOT written (owner gate).
- Schema additions: `SCHEMA_VERSION` must bump to 3; new tables (`contract`, `cohort_design`, `cohort`, `cohort_event`, `engine_version`, `evaluation`, `decision`, `coin_outcome`) — Phase 2+.
- Research API read-only routes on the service; frontend Research page — Phase 9.

## Phase 1 status (2026-10-24) — implemented vs owner-pending

**Implemented in the working tree (uncommitted; two review rounds closed, all findings verified by read-only review agents):**
- Frozen `BenchmarkContract` (`proto/engine/contract.py`): required `_sha256` on load, deep-frozen cost_model, `decision_grid_bars`/`stage_sizes` in the canonical hash, strict positivity for slip tiers and τ.
- Read-only baseline CLI (`proto/engine/baseline.py`, `__main__.py`): real pre-holdout boundary (dev+val anchored at BTC's first bar, per-cell holdout guard), point-in-time per-entry cost tiering (plan §5.1), `--out` refusal for any path under a `data/` root / protected DBs / the research DB before any file access, deterministic stride-grid sampling labeled accurately.
- Tests: `tests/test_engine_{contract,baseline,cli}.py` (+ inherited data-protection suite) — 70 / 186 / 75 / 8 checks, all passing.
- Authoritative measurement: `/tmp/opencode/alt-radar/baseline-phase1-fixed3.json` — 117-coin point-in-time universe, 12-cell grid (10/20/25 × 1/3/7/14 d), best cell 25×/14d 67.5%. Full report: `docs/superpowers/plans/phase1-review-fixes/final-report.md`.

**Owner-pending (nothing decided by the agent):** D1–D10 sign-off plus the cohort-feasibility choice (117 eligible coins < the plan's ≥170 disjoint-cohort minimum: lower the liquidity floor, or nest S20 ⊂ S50 ⊂ S100 with new-coins-only reporting). The contract is not frozen, no cohort policy chosen, no research DB created.

---

## Differences from the plan author's source tree

### 1. `proto/quant/backtest.py` exists and contains the F1 hindsight defect

The plan (§1) lists `proto/quant/backtest.py` as **missing**. In the actual checkout it is present (untracked, 336 lines). The plan (§2.1, defect F1) correctly identifies the defect:

> `evaluate_cycle` picks each coin's signal with `best = max(events, key=lambda e: e["net_r"])` ... This guarantees inflated pass rates and cannot support any benchmark claim.

**Confirmed** at `proto/quant/backtest.py:253`:
```python
best = max(events, key=lambda e: e["net_r"])
```

`net_r` is the realized outcome. The new `proto/engine/` evaluator must NOT replicate this. Phase 3 quarantine applies.

### 2. Engine reads bars through the research mirror

The plan supersedes any `data/quant/bars/` file-copy design. The engine must read `data/bars/` **through the research DB** (`proto/research/sync.py` → `load_snapshot_bars`). `store.validate_db_path` enforces the boundary (refuses `data/bars`, `quant.db`, `signals.db`).

**Phase 1-only exception (documented 2026-10-24, resolving the plan's internal conflict):** the plan's own Phase 1 command calibrates with `--data data/bars` (plan line 175) while its global rule says mirror-only (plan line 12) — the two conflict. Resolution in force: the Phase 1 **calibration baseline only** (`proto/engine/baseline.py`) reads the archive directly, **strictly read-only** (no writes are possible through it: the data-protection suite fences writes, and the CLI's `--out` guard refuses any destination under `data/`). All Phase 2+ engine components (labeler, decision grid, cycle runner, scheduler) read the mirror only (`proto/research/sync.py:475` `load_snapshot_bars(db_path, snapshot_id, coin)`), and the planned CLI migration is a snapshot-input mode — **proposed, not implemented, and the research DB is neither created nor populated** while the owner gate stands. The package docstring (`proto/engine/__init__.py`) and the baseline module docstring state this exception.

### 3. The `/snapshot` route discrepancy

**Status: the root cause of the owner's "Snapshot unavailable — no such route: /snapshot" error is unconfirmed.** The exact error message string is **not produced by any code in the current checkout** — it was not found in `app/src/`, `app/src-tauri/`, `proto/service.py`, or `app/dev/mock_service.py`. **Re-verified 2026-10-24:** the string still does not appear anywhere under `app/` or `proto/`, and `proto/service.py` still has no `/snapshot` route. The cause therefore remains unconfirmed; do not treat any explanation below as established.

**Verified facts:**

- **Real service** (`proto/service.py`): routes are `/healthz` (line 274, auth-free), `/readyz` (line 283), `/collector-health` (line 286), `/bars`, `/funding`, `/settlements`, `/prices`. Unknown paths fall through to `_deny(404, "NOT_FOUND", f"no such route: {path}"` at line 303. **No `/snapshot` route.**

- **Dev mock service** (`app/dev/mock_service.py`): routes are `/healthz` (line 380), `/events` (line 387), `/snapshot` (line 399, returns `self._snapshot()`), `/setups` (line 402), `/setup/` (line 405), `/bars` (line 408), `/metrics` (line 411), `/tradeplan/` (line 414), `/quant-status` (line 417), `/quant-research` (line 420). Unknown paths return `self.fail(404, NOT_FOUND, f"no route for {path}")` (line 423). Line numbers re-verified 2026-10-24. **Has `/snapshot` route with the old scanner contract.**

- **Client** (`app/src/api/client.ts`): methods are `healthz()` (calls `/healthz`), `readyz()` (calls `/readyz`), `collectorHealth()` (calls `/collector-health`), `bars()` (calls `/bars`). **Does NOT call `/snapshot`.** Comment at line 5: "There are no candidates, no snapshot, no SSE event bus, and no commands."

- **Frontend** (`app/src/App.tsx`): `useConnectionState` calls `client.healthz()` on mount. `Status.tsx` calls `client.readyz()` and `client.collectorHealth()`. **Does NOT call `/snapshot`.** The `ConnectionBanner` component shows "starting" → "connected" → "error" with no snapshot reference.

- **Vite dev proxy** (`app/vite.config.ts:11-17`): proxies `/api/*` to `http://127.0.0.1:18741` with the `/api` prefix stripped. During dev, `/api/snapshot` would be proxied to `http://127.0.0.1:18741/snapshot` → hits the mock service's `/snapshot` route (200 OK).

**The "Snapshot unavailable — no such route: /snapshot" message is not produced by the current code.** Possible sources:
1. A stale app bundle from a prior branch that called `/snapshot` against the real service (which 404s).
2. A running service from another worktree/branch (e.g. `feature/ws-d-ui` or `trader-dashboard`) that had snapshot routes.
3. A modified client/bundle in another working directory.

Per the plan: "Do not add a generic `/snapshot` route solely to accommodate a stale client." The fix is Phase 9 Research API routes + a frontend Research page.

**Route discrepancy summary (dev mock vs. real service):**

| Route | Real service (`proto/service.py`) | Dev mock (`app/dev/mock_service.py`) |
|---|---|---|
| `/healthz` | ✅ (auth-free liveness) | ✅ (auth-free liveness) |
| `/readyz` | ✅ | ✅ (behind auth) |
| `/collector-health` | ✅ | ❌ |
| `/snapshot` | ❌ (404) | ✅ (old scanner contract) |
| `/setups` | ❌ | ✅ |
| `/setup/<id>` | ❌ | ✅ |
| `/metrics` | ❌ | ✅ |
| `/tradeplan/<id>` | ❌ | ✅ |
| `/quant-status` | ❌ | ✅ |
| `/quant-research` | ❌ | ✅ |
| `/events` | ❌ | ✅ |
| `/bars` | ✅ | ✅ |
| `/funding` | ✅ | ❌ |
| `/settlements` | ✅ | ❌ |
| `/prices` | ✅ | ❌ |

The dev mock and real service are **not** drop-in compatible.

**Current frontend/backend route state (re-verified 2026-10-24, evidence: `app/src/api/client.ts` methods, `proto/service.py` route list, `app/dev/mock_service.py` route list):**
- The real frontend client calls exactly four paths — `/healthz` (`client.ts:139`), `/readyz` (`client.ts:150`), `/collector-health` (`client.ts:159`), `/bars` (`client.ts:164`) — and **all four exist on the real collection-only service**. There are **zero** `/snapshot` references in `app/src` (only explanatory comments). The shipped frontend and the real backend are route-compatible today.
- The mismatch that exists is **dev mock vs real service**: the mock still carries scanner-era routes the real service 404s (`/snapshot`, `/setups`, `/setup/<id>`, `/metrics`, `/tradeplan/<id>`, `/events`, `/quant-status`, `/quant-research`) and lacks the real service's `/collector-health`, `/funding`, `/settlements`, `/prices`. Anything built against the dev mock that calls those routes will fail against the real backend — per the plan, the fix is the Phase 9 Research API routes plus a frontend Research page, and no generic `/snapshot` route is to be added for a stale client.

### 4. Frontend "Starting…" issue

`app/src/App.tsx` line 22: `const [state, setState] = useState<ConnectionState>("starting")`. The `useConnectionState` hook (lines 21-38) calls `client.healthz()` on mount and every 5s. `healthz()` (line 137-145 of `client.ts`) fetches `/healthz` and returns `false` on any error (404, 401, network, timeout). The hook maps `true → "connected"`, `false → "error"`.

The hook does NOT distinguish:
- 401 Unauthorized
- 404 Missing route
- Timeout
- Network failure

It lumps all into `"error"` → `ConnectionBanner` shows "Connection error: ALT RADAR service unavailable..." (line 35 of `ConnectionBanner.tsx`).

The plan requires: "The UI must show useful connection states (success, failure, timeout, unauthorized, missing route) instead of spinning in 'Starting…'." Current state shows "Starting…" only until the first healthz probe completes, then flips to either "Connected" or "Connection error". It never stays stuck in "Starting…" indefinitely unless the probe never returns.

The improvement (Phase 9): `useConnectionState` should inspect the HTTP status to produce `unauthorized`, `missing-route`, `timeout`, and `service-unavailable` states, each with a distinct UI message.

### 5. `data/research/` and the manifest

The Phase 0 draft initially created a manifest at `data/research/.untouched-manifest.json`. This was **removed** — no file is written under `data/` without explicit owner action. The manifest is now at `/tmp/opencode/alt-radar/.untouched-manifest.json`, outside the repo and outside `data/`.

`store.validate_db_path` in `proto/research/store.py` rejects:
- Any DB path whose basename is `signals.db` or `quant.db`
- Any DB path that resolves into `data/bars`, `data/prices`, `data/funding`, or `data/funding_settlements`

The research DB path `data/research/altradar-research.db` is accepted.

### 6. `test_quant_backtest.py` — untracked, fails

This is an **untracked** file left by the stopped agent. It is **not** part of the original tracked baseline. It fails on 3 assertions:
- `snap has rvol` — `rvol=None` (line 238 of test_quant_backtest.py)
- `result has coin keys` — `ALT1` key missing from backtest result (line 259)
- `ALT1 found in results` — `ALT1 not in result` (line 271)

These are failures in the stopped agent's synthetic archive, not regressions from this plan. Quarantined in Phase 3.

---

## Historical baseline (preserved for reference)

The initial Phase 0 write-up (commit `acf82d0`, before the review correction) made these claims that were since corrected:

1. **Manifest location:** stated the manifest lived at `data/research/.untouched-manifest.json`. Corrected: the manifest was removed from `data/` and now lives at `/tmp/opencode/alt-radar/.untouched-manifest.json`. No file under `data/` is written without explicit `--manifest` owner action.

2. **Suite status:** initially described as "26 suites pass, 1 fails" without distinguishing that `test_quant_backtest.py` is untracked and not part of the original baseline. Corrected: 26 tracked suites pass; the failing test is an untracked stopped-agent artifact.

3. **Commit hash:** initially referenced `acf82d0`. Corrected to `cd5a2ab` (the amended commit after review fixes).

4. **`data/research/` state:** initially described as containing the old manifest. Corrected: the directory is absent; the manifest file was removed.

5. **`/snapshot` cause:** initially stated the route discrepancy was "confirmed" and the cause was the dev mock having `/snapshot` while the real service doesn't. Corrected: the route discrepancy is verified, but the **root cause of the owner's error message is unconfirmed** — the exact message text does not appear in any checked-out source file.

---

## Summary

The stopped agent's `proto/quant/backtest.py` (line 253) contains the exact F1 hindsight-selection defect the plan documents. The new `proto/engine/` package will quarantine it in Phase 3 and build the correct outcome-independent path on top of the existing `proto/research/*` substrate and `proto/quant/*` primitives.
