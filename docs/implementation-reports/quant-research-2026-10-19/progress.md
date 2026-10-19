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
| A | measurement & structure engine | not started |
| B | outcomes & inference | not started |
| C | preregistered research workflow | not started |
| D | Research UI expansion | not started |
| E | champion/candidate tracking | not started |
| F | startup & first-sweep optimization | not started |

## 4. Safety status

- Live service: **not** restarted, **not** altered (pid 572048, operator
  drop-in `TimeoutStopSec=600s` untouched).
- Production archives: not modified, not backfilled.
- Holdout: sealed, untouched. ACTIONABLE output: still disabled. No
  promotion performed.
