# ALT RADAR — quant research capabilities + startup optimization

**Final report** · folder: `docs/implementation-reports/quant-research-2026-10-19/`
**Base commit:** `95d0a56` (design + plan) → **HEAD** (all six workstreams merged)
**Date:** 2026-10-20 · **Status:** implemented, reviewed, **not deployed**
**Plan/spec:** `docs/superpowers/specs/2026-10-19-quant-research-and-startup-design.md`

> Older reports are preserved untouched. Where this effort supersedes a figure
> from the 2026-10-18/19 quant roadmap report, that report is **not** rewritten;
> the correction is recorded here and in `docs/roadmap-status.md`.

---

## 1. What was implemented, and what remains

| WS | Scope | Implemented (code, reviewed, merged) | Remaining / not done |
|---|---|---|---|
| **A** | Market measurement & structure engine | `proto/quant/structure.py` (multi-timeframe structure, **confirmed pivots** with frame-aware `confirmed_ts`), `estimators.py` + `mfdfa.py` (rolling R/S Hurst, generalized Hurst, MF-DFA + multifractal spectrum, each with documented assumptions, an uncertainty band and an `inputs_hash`), `measures.py` (versioned volatility / tail-risk / regime / dependence / breadth measures), `feature_store.py` (point-in-time as-of join keyed on `available_ts`) | The measures are **descriptive only** — nothing blends them into a score, and they are not used for selection. `hurst_rs` / `hurst_generalized` / `mfdfa` are O(n) and slow (~8 s per 28 k bars): window or subsample per coin. Adverse excursion (MAE) is not computable from the research event contract (no bar path). |
| **B** | Outcomes & inference | `proto/quant/competing_outcomes.py` (cause taxonomy `TP / SL / TIME / INVALIDATED / CENSORED`, mutually exclusive and exhaustive; friction, funding and net separately attributable; conservative stop-first barrier ordering reused from `barrier.py`), `inference.py` (expanding-window probability + calibration buckets, day- **and** coin-cluster uncertainty, three baselines), `family_test.py` (Holm + bootstrap max-t over a declared family) | The placebo null **retains the rule's average edge** (it breaks temporal alignment, so it detects date-specific deviations — it is not a signal-free null). `net_r` is `None` whenever funding is unattributable, so a run without a funding archive has an empty headline net metric (the friction-only figure is reachable only under an explicitly named key). End-of-sample censoring is label-blind and conservative: it discards observable outcomes and shrinks the resolved denominator. |
| **C** | Preregistered research workflow | `proto/quant/prereg.py` (versioned record: feature definitions, point-in-time membership, horizons, event rules, costs, splits, embargo, statistics, promotion criteria, config/data/code hashes, and an **append-only variant registry that records failures too**), `research_report.py` (slices by coin, period, volatility group and liquidity group; every cell carries its denominator, and a cell below `min_cell` reports `insufficient` with **no mean**), the `prereg` CLI, and `docs/hypotheses/HYD-0002.*` registering the completed 288-configuration evaluation | The registration is **retrospective** (written after the run) and says so. It registers the sweep as **one** variant, not per-config. The volatility-group labels are **not comparable** with `measures.regime` codes (the event stream carries no market factor). |
| **D** | Research UI expansion | `proto/quant/research_surface.py` + the read-only `GET /quant-research` route (statistics **reused** from `report.py`, never re-implemented) and the expanded Research view: sample counts with denominators, interval endpoints only, calibration/coverage/validation status, feature provenance, insufficient-evidence states **with reasons** | Calibration is honestly **`unavailable`** (the shadow store records outcomes, not predicted outcomes) — no table is fabricated. The route is SHADOW-only, so it can differ from the CLI report on a store holding legacy `ACTIONABLE` rows. |
| **E** | Champion/candidate tracking | `proto/quant/champion.py` (versioned champion/candidate registry; the matched comparison evaluates both configurations on the **same events** with a **shared** cost model and reports the paired day- and coin-cluster intervals of the difference) and `proto/quant/decision_log.py` (immutable, hash-chained `docs/decisions/chain.jsonl`; `append` re-hashes every cited evidence file at write time and refuses a mismatching or missing one) | **Nothing was promoted.** The human-owned `docs/decisions/log.jsonl` stays 0 bytes; `chain.jsonl` does not exist in the repository; `set_champion` requires an explicit `allow_promote` opt-in. The chain's hypothesis linkage is **advisory** (the id is format-checked, not existence-checked). |
| **F** | Startup & first-sweep optimization | `proto/daemon_timing.py` (per-stage instrumentation for scan / collect / funding / settlements / prices / resolve / measure / quant, persisted across restarts, exposed additively on `/scanner-health`); `/readyz` is now **API-serving readiness** (a read-only DB probe) with a distinct `first_sweep` block on `/scanner-health`; persisted per-stage due-times so a restart **resumes**; a per-coin bar index so the collector stops re-parsing whole archives; a bounded quant rebuild (proven identical to the full rebuild on all 579 production archives); interruptible shutdown; exactly one journal row + one dashboard row per completed iteration | The **end-to-end time-to-ready is not measurable without deploying** (§2). The bar index's fast path needs a gap-free archive: one venue outage disables it for that coin (silently — only slower, never wrong). The measure-cycle interrupt is **soft** (the submitted venue fetches drain, ≈28 s, inside the 600 s grace). |

**Not implemented at all, and deliberately out of scope:** no new venue endpoints,
no order execution, no ACTIONABLE emission, no promotion, no holdout spend, no
re-run or retune of the 288-configuration evaluation, and no change to the
archived data formats.

---

## 2. Before/after stage timings, and how they were measured

### 2.1 Before — measured on the live unit (read-only)

Source: `journalctl --user -u altradar` for pid 572048 (started 2026-10-19
15:46:39), the last restart before this effort. **No service was restarted for
this work**, so these are the "before" numbers as observed in production.

| Stage | Log line | Elapsed from start |
|---|---|---:|
| HTTP listener | `service: listening on 127.0.0.1:18741` | ~0 s |
| scan | 15:54:17 | **458 s** |
| collect | 16:00:50 | **851 s** |
| funding | 16:00:58 | 859 s |
| settlements | 16:09:39 | 1 380 s |
| prices | 16:21:53 | 2 114 s |
| measure | 16:35:38 | 2 939 s |
| quant step (silent) | — | ~1 012 s |
| **`/readyz` → 200** | 16:52:30 | **3 951 s (65.8 min)** |

`/scanner-health` reported `sweep_count=1, last_sweep_duration_s=3950.25` — one
number for eight stages. **There was no per-stage instrumentation at all**:
`app.last_scan_phases` was written but never read, and the quant step's
`seconds` return was discarded.

### 2.2 After — what the code changes do, and how that was measured

| Change | Effect | How it was measured |
|---|---|---|
| `/readyz` no longer waits for the first sweep | The API answers as soon as the listener is up and the DB is **readable** (the listener already binds before the loop thread starts — measured ~0 s above). The first-sweep state moved to `/scanner-health` as `first_sweep` (`pending` / `in_progress` / `complete`, with `completed_ts` null until it really completes). | **Structural, not yet measured end-to-end**: the end-to-end time-to-ready requires deploying and restarting, which is a separate, owner-authorized step (§4). The readiness path is deterministic by construction (a read-only `SELECT count(*) FROM sqlite_master`). |
| Per-stage instrumentation | `scan` / `collect` / `funding` / `settlements` / `prices` / `resolve` / `measure` / `quant` each get a duration **and** an outcome (`ok` / `skipped` / `failed` / `not_due` / `partial`), persisted (last 5 iterations) and exposed on `/scanner-health`. The quant stage now logs even on success. | Code + 91-check regression suite. |
| Persisted due-times | A restart **resumes**: stages the persisted cadence says are not due are recorded `not_due` and do no work (no 150-coin scan, no 560-coin collection, no resolution, no measurement cycle). Before, only settlements and prices resumed — scan/collect/resolve/measure were process-local and redid everything. | Code + tests; the *seconds saved* depend on the deployment and the stage intervals, so they are not claimed here. |
| Collector dedupe | The archive is **not read at all** when the index matches: `read_bars` was **585 ms/coin → 339 s** for 579 coins *every* collect cycle; that I/O disappears (only the venue fetch remains: 560 coins at the shared 20 req/s limiter ≈ 28 s minimum). | Measured read-only on the production archives. |
| Quant rebuild | Bounded window: `load_coin` **434 ms/coin → 275 ms/coin** (251 s → 159 s for 579 coins); the redundant per-coin tail read is skipped via the file signature the rebuild already verified (≈87 s). The feature warm-up replay (≈175 s) is unchanged — it is warm-up, not waste. | Measured read-only on the production archives. |
| Bounded rebuild equivalence (the load-bearing claim) | The bounded window is **the same set of bars**, not "close enough". | `DONE 579 files, mismatches: 0` over the 579 production archives (470 s, read-only), plus bit-identical `Snap` values on a synthetic 30-day archive. |
| Shutdown | The stop flag is observed between stages and between per-coin units, so a SIGTERM drains inside the operator's 600 s grace instead of blocking on a whole phase. | Code + tests; the live service was not restarted to measure it. |
| Sweep accounting | Exactly one `watcher_events` row and one `dashboard_snapshot` row per completed daemon iteration (before: **two** of each, because the scan and the iteration each wrote one). A *total* collector failure no longer records a false `eligible=0` sweep. | Code + tests. |

**What is deliberately *not* claimed:** no end-to-end "time to ready" number,
because the service was not restarted. Deploying and restarting is the only way
to produce that number, and deployment is outside this task's authority.

---

## 3. Tests and exact results

### 3.1 Offline backend suite (integrated `main`)

```
$ python3 tests/run_all.py
...
ALL SUITES PASS (87 files)
```

### 3.2 Frontend gates (integrated `main`, `app/`)

```
$ npm run test      → Test Files 14 passed (14) | Tests 200 passed (200)
$ npm run typecheck → tsc --noEmit           (exit 0, no output)
$ npm run lint      → eslint . --max-warnings 0  (exit 0, no output)
$ npm run build     → ✓ built in 9.44s
```

### 3.3 Per-workstream focused suites

| Workstream | Suite | Checks |
|---|---|---:|
| F | `tests/test_ws_f_startup.py` | 91 |
| A | `tests/test_ws_a_measurement.py` | 147 |
| B | `tests/test_ws_b_outcomes.py` | 95 |
| C | `tests/test_ws_c_prereg.py` | 116 |
| D | `tests/test_ws_d_research_surface.py` (+ 200 frontend tests) | 86 (+200) |
| E | `tests/test_ws_e_champion.py` | 138 |

### 3.4 Review record (findings and resolutions)

Each workstream had a **fresh implementer** and a **fresh reviewer**; fix
rounds were run by the original implementer and re-reviewed against the
fix diff only.

| WS | Task review | Fix rounds | Outcome |
|---|---|---|---|
| F | 13 findings (2 Important: an **interrupted** iteration was reported as a completed first sweep; a `/readyz` assertion that could not fail; 1 Important wording overstatement; 10 minor) | 1 | re-review: **approved** (4 minors deferred) |
| A | 1 **Critical** (a non-finite value *outside* the measured window changed the as-of estimate — a point-in-time violation) + 4 Important (frame-aware `confirmed_ts`; `hurst_generalized` returned a DFA exponent instead of H; the `returns`/`levels` parameter contract; a single-thread-only feature store) + 15 minor | 3 | re-review: **approved** (4 minor nits deferred) |
| B | 3 Important (the expanding-window estimator never used its training prefix; `expected_return` reported an interval with <3 clusters; the placebo was described as a signal-free null) + 8 minor | 1 | re-review: **approved** (6 minor deferred) |
| C | approved with findings (2 Important: the `code_hash` was untruthful mid-review; append-only could be bypassed via a corrupt sidecar) + 11 minor | 1 | pending the whole-branch review |
| D | approved (2 to resolve before merge: an impossible mock fixture with a wrong invariant; a gate-passed rule rendering as "not actionable") + 10 minor | 1 (12 findings) | pending the whole-branch review |
| E | **blocked on one fix** (a test walking the tree for `*.db` would fail in the real checkout, where `data/*.db` exists) + 2 Important + 6 minor | 1 (8 findings) | pending the whole-branch review |
| — | **whole-branch review** (fresh, adversarial, on the integrated diff) | see §6 | see §6 |

Every finding is recorded with its resolution in the workstream reports filed
beside this one (`ws-a-report.md` … `ws-f-report.md`).

---

## 4. Data integrity and deployment status

### 4.1 Data integrity

- **No production archive was modified, backfilled or rewritten.** Every
  diagnostic and test read the archives read-only or used `tempfile`
  fixtures; the worktrees have no `data/` directory at all.
- The live collector continued to append bars and settlements to `data/` on
  its own schedule (that is the service's normal work, not this effort's).
- `docs/decisions/log.jsonl` is still **0 bytes**; `docs/decisions/chain.jsonl`
  does **not** exist in the repository; no champion registry file exists in
  the repository; `data/promotions.json` was not created.
- The preserved `evaluation.json` is untouched: its holdout fields are still
  `null` and its `dev`/`val`/`spa`/`selected` blocks are byte-identical to the
  2026-10-19 artifact (sha256 `81affa4a…3242c`).

### 4.2 Deployment status — **NOT deployed**

- The code is merged into `main` and pushed, but the **running service still
  executes the pre-merge code** (pid 572048, started 2026-10-19 15:46:39).
- The service was **not** restarted, reloaded or altered at any point in this
  effort; the operator's drop-in
  (`~/.config/systemd/user/altradar.service.d/quant-shadow.conf` —
  `--quant-shadow`, `TimeoutStopSec=600s`) was read and never written.
- **Deploying is a separate, explicitly authorized step.** It will exercise
  the new startup path (readiness split, persisted cadence, resumable work)
  for the first time, and it is the only way to produce the end-to-end
  time-to-ready number this report declines to invent.
- Two new runtime artifacts will appear on first boot of the new code:
  `data/.cadence.json` (persisted due-times) and `data/bars/.bar_index.json`
  (per-coin archive index). Both are accelerators, both are safe to delete
  (the code falls back to the full read), and neither is an archive.

### 4.3 Quant safety

- The watcher still pins `promo = None` (`proto/quant/engine.py`), the
  selector's `ACTIONABLE` path is still unreachable, the service still
  reports `"mode": "shadow"`, and `tests/test_quant_shadow_only.py` still
  proves a passed promotion yields only `SHADOW` rows.
- **No promotion was performed and no decision was recorded.**
- **The one-shot holdout was never spent:** no code path in the branch writes
  `data/quant_research/holdout_evaluated.json`, and the research report
  refuses a window that reaches into the holdout range with a
  `holdout-sealed:` reason instead of computing it.

---

## 5. Remaining venue, sample-size and operational limitations

**Venue / external**

- The 20 req/s venue-wide limiter is unchanged and every new path respects it.
- OI history and book/spread history remain unavailable (no public endpoint);
  the WS3 1 m design stage and the provisional-bar tier are still owner
  decisions; the fee tier is still the planner's unconfirmed 5 bp constant.
- Nothing here makes a strategy tradable: the funding-adjusted numbers are
  an *accounting* of a cost model, not a fill model.

**Sample size / inference**

- The 288-configuration evaluation still finds **no evidence of a positive
  edge** and is not re-run. The new modules are measurement and inference
  infrastructure; they are not evidence of an edge, and nothing here
  re-opens that conclusion.
- The gross-outcome SPA in the 2026-10-19 report remains **exploratory** (its
  metric and analysis were chosen after seeing the net results) and the
  short-horizon reversal remains a **candidate for preregistration**, with a
  break-even that is arithmetic rather than demonstrated (no execution-cost
  evidence in this repository supports ~15 bp).
- The power of the existing sample is unchanged: the selected dev mean has
  SE ≈ 0.0145 R, so ~0.04 R is needed for 80 % power while the drag at that
  geometry is 0.019 R.

**Operational**

- The first-sweep readiness change means **`/readyz` no longer implies "the
  data is fresh"** — a consumer that used it for freshness must read
  `/scanner-health`'s `first_sweep` instead. This is the one intentional
  response-shape change and it is documented in the module docstring, in
  `docs/roadmap-status.md` and in this report.
- The bar index's fast path needs a gap-free archive; one venue outage
  disables it for that coin (silently, and only slower — never wrong).
- The measure cycle's interrupt is soft (≈28 s of in-flight venue fetches
  drain inside the 600 s grace); a hard abort would need a `should_stop`
  keyword in `proto/measurement.py`.
- The new estimators are O(n); at 28 k bars `mfdfa` takes ~8 s, so a
  production run would window or subsample per coin.

---

## 6. Whole-branch review

See §6 of the ledger at `.superpowers/sdd/2026-10-19-quant-research-and-startup-design/progress.md`
(the workspace is scratch; the durable record is this report and the
per-workstream reports beside it).

---

## 7. What awaits the owner

1. **Deployment** of the merged branch (a restart) — explicitly authorized
   only by you. It is also the only way to measure the new time-to-ready.
2. **The one-shot holdout** — still sealed, still yours to spend (or not).
   The preregistration workflow in Workstream C is the mechanism that would
   let a decision be made on a frozen, recorded setup; nothing in this effort
   touches it.
3. **The decision-log artifact** — the new hash-chained log lives beside the
   old human-owned one; migrating or replacing it is your call.
4. **WS3 items** unchanged: the 1 m design stage, the provisional-bar tier,
   and the confirmed fee tier (which the execution-cost viability question for
   H1 depends on).
