# Quant engine implementation status

**Reviewed:** 2026-10-24 (UTC 20:52) — supersedes the 2026-10-23 snapshot (which showed Phase 0 only)
**Plan:** `quant-engine-implementation-plan.md`
**Branch:** `engine-benchmark` at `cd5a2ab` (no commits since; all Phase 1 work is uncommitted in the working tree, per owner instruction)

## Summary

Phase 0 is committed. Phase 1 is **implemented, review-verified, and uncommitted**, and is **blocked at the owner gate**: D1–D10 sign-off plus the cohort-feasibility choice (117 eligible coins < the plan's ≥170 disjoint-cohort minimum — owner chooses: lower the liquidity floor, or nest cohorts S20 ⊂ S50 ⊂ S100 with new-coins-only reporting). Nothing is frozen: no `docs/benchmark-contract.json`, no contract row, no research DB, benchmark gate closed. No cohort policy has been chosen.

## Phase status

| Phase | Status | Current state |
|---|---|---|
| 0 — Orientation and safety harness | Implemented (committed `cd5a2ab`) | Engine skeleton, manifest tool outside `data/`, data-protection test. Known env caveat: `tests/run_all.py` unusable here (`sys.executable` → ZCode AppImage); per-file direct execution is authoritative. |
| 1 — Contract and baseline calibration | **Implemented (working tree, uncommitted); review rounds 1 and 2 closed; awaiting [OWNER] D1–D10 + feasibility choice** | `proto/engine/contract.py` (frozen BenchmarkContract: `_sha256`-required load, deep-frozen cost_model, `decision_grid_bars`/`stage_sizes` hashed, strict positivity for slip tiers and τ), `proto/engine/baseline.py` + `__main__.py` (read-only CLI: real pre-holdout boundary anchored at BTC's first bar with per-cell holdout guard, point-in-time per-entry cost tiering per plan §5.1, `--out` refusal under any `data/` root / protected DBs / research DB before any file access, deterministic stride-grid sampling labeled accurately). Tests: `tests/test_engine_{contract,baseline,cli}.py` — 70 / 186 / 75 checks + inherited data-protection 8, all passing (re-run 2026-10-24). Decisions doc `docs/benchmark-contract-decisions.md` presents D1–D10 with the measured baseline and stops at the owner gate. |
| 2 — Universe census and cohorts | Not started | Blocked on the owner's feasibility choice (117 < 170). Existing research sampling and benchmark gate are reusable foundations. |
| 3 — ROE labeler and legacy evaluator quarantine | Not started | Stopped-agent `proto/quant/backtest.py` remains untracked with the documented hindsight-selection defect at line 253 (`best = max(events, key=lambda e: e["net_r"])` — re-verified 2026-10-24); its untracked test fails 3 assertions (pre-existing, not this work). |
| 4 — Decision grid and point-in-time features | Not started | No engine decision selection, feature set, or leakage tests. |
| 5 — Models and version freeze | Not started | No engine baselines, logistic model, calibration utilities, engine-version registry, or freeze enforcement. |
| 6 — Cycle runner and S20 evidence | Not started | No engine cycle/evidence implementation or S20 evaluation. `data/research/` and its database are absent (re-verified 2026-10-24). |
| 7 — Eight-hour scheduler | Not started | No engine scheduler, development-run queue, or systemd unit. |
| 8 — Stage progression | Not started | No S20 → S50 → S100+ stage gate logic. |
| 9 — API and Research view | Not started | No research API routes or frontend Research page. The shipped frontend client calls only `/healthz`, `/readyz`, `/collector-health`, `/bars` — all present on the real collection-only service (route-compatible today; the mismatch is dev-mock vs real service — see the reconciliation note §3). |
| 10 — Readiness review and rule system | Deferred | ACC-POC and other rule guidance are not implemented; this phase waits on the owner's readiness decision. |

## Reusable foundations

The repository already has the bar loader, research mirror and sync code, run registry, deterministic sampler, benchmark gate, and quant primitives for ROE, barriers, statistics, features, risk, coverage, and structure. The research schema remains version 2; engine schema additions have not been made.

`proto/engine/` now contains the Phase 1 modules (uncommitted, above the tracked `__init__.py` skeleton from `cd5a2ab`).

## Test-suite state (evidence: per-file direct runs, 2026-10-24)

Offline suite = 37 files: **36 pass, 1 fail** (only the untracked stopped-agent `tests/test_quant_backtest.py`). Focused engine suites: contract 70, baseline 186, cli 75, data-protection 8 — all ALL PASS. `tests/run_all.py` reports spurious failures in this environment because its `sys.executable` subprocess spawns the ZCode AppImage; the per-file loop is the honest number.

## Authoritative Phase 1 measurement

`/tmp/opencode/alt-radar/baseline-phase1-fixed3.json` — 117-coin point-in-time universe (trailing-24h QV ≥ 1M USDT at the day-77 boundary, 2026-09-28 02:35 UTC), deterministic stride grid, per-entry point-in-time cost tiering; 12-cell grid 10/20/25 × 1/3/7/14 days; best cell 25×/14d 67.5% pass (P(no-skill ≥ 15/20) = 32.4%). Session report and audit trail: `docs/superpowers/plans/phase1-review-fixes/final-report.md` (single current result = round 2; round-1 numbers in its superseded-history section).

## Current checkout references

- Decisions doc (owner gate): `docs/benchmark-contract-decisions.md`
- Reconciliation note (updated 2026-10-24): `docs/benchmark-reconciliation.md`
- Implementation plan: `/home/bamzhie/Downloads/quant-engine-implementation-plan.md`
- Fix-session workspace: `docs/superpowers/plans/phase1-review-fixes/` (git-ignored)
- `data/research/`: absent; manifest remains at `/tmp/opencode/alt-radar/.untouched-manifest.json`

## Next planned phase

Owner decides D1–D10 + the cohort-feasibility choice; then Phase 2 (universe census and cohort design). No Phase 2+ code is written.

This status note does not authorize changes to collected data, service state, commits, or pushes.
