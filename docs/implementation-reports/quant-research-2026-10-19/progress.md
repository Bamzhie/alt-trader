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
| A | measurement & structure engine | fix round 2 done, re-review in flight |
| B | outcomes & inference | **approved**, merged (`280e41c`) |
| C | preregistered research workflow | briefed; starts after A merges |
| D | Research UI expansion | briefed; starts after the API contracts land |
| E | champion/candidate tracking | implementing (worktree `/tmp/opencode/wt-e`) |
| F | startup & first-sweep optimization | **approved**, merged (`dacf7e7`) |

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
| 1 | F, A, B | separate git worktrees, disjoint file ownership | running |
| 2 | C, E | separate worktrees; depend on A/B contracts | pending |
| 3 | D | separate worktree; depends on the A/B/C/E API contracts | pending |
| 4 | whole-branch review + full suites + integration | orchestrator + fresh reviewer | pending |

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

