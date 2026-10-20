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

### 4.2 Deployment status — **DEPLOYED 2026-10-20** (supersedes "NOT deployed")

*This section was written before the owner authorized a deployment. It is
updated, not rewritten: the code, the archives and the quant-safety
statements below are unchanged, and the deployment evidence lives in
**`deployment-report.md`**.*

- The code is merged into `main` and pushed, and the **local user systemd
  service was restarted on 2026-10-20** with a clean tree at the deployed
  commit **`c8647b5`** (the only later commit is documentation). The
  running service's `provenance.code_rev` reads the *current* git HEAD at
  request time, so it tracks HEAD rather than the loaded revision; the
  proof that the new code is executing is behavioural (the `first_sweep`
  block, the per-stage `stages`, the research route and the three new
  runtime artifacts).
- The unit file and the operator's drop-in (`--quant-shadow`,
  `TimeoutStopSec=600s`) are **byte-identical** to before; the stop grace
  was not shortened, extended or overwritten.
- Restart: requested 11:22:57 local, new process up 11:32:58 (601 s
  stop→start — the OLD code's non-interruptible drain reaching the
  operator's 600 s grace). The API answered `/healthz` and `/readyz`
  **1 second** after the start.
- First sweep after the deploy: **1 959 s (32.7 min)** vs 3 951 s before,
  now with per-stage attribution (`scan=449.7(partial) collect=438.4
  funding=9.0 settlements=0.0(not_due) prices=0.0(not_due)
  resolve=0.0(skipped) measure=450.1(partial) quant=611.8`). The **second**
  sweep — the steady state, with a warm bar index and warm quant state —
  took **660.8 s (11 min)**: `scan=321.6(partial) collect=64.3(partial)
  funding=19.0 settlements=0.0(not_due) prices=0.0(not_due) resolve=1471.9
  measure=178.7(partial) quant=77.2`. The collect phase drops from 438 s to
  64 s (the bar index removes the dedupe read) and the quant step from
  612 s to 77 s (the bounded rebuild and the file-signature fast path);
  the scan and measure differences are venue-side variance and are not
  attributed to the code.
- Quant output is still **SHADOW-only** (`mode:"shadow"`,
  `actionable_events: 0`, `promotions: []`), the **holdout was not spent**
  (`data/quant_research/` absent), the decision log is still 0 bytes, and
  **no production archive was rewritten**.
- **Deploying is not evidence of an edge.** The WS5 evaluation still found
  no evidence of a positive edge; the gross-outcome SPA is still
  exploratory; H1 is still a candidate for preregistration.

#### 4.2.1 The "not deployed" statements below, and their status

These statements describe the 2026-10-19 session. They are kept as
history, with their operational status marked:

- "The code is merged into `main`, and `main` is pushed to `origin/main`
  so that the pushed tip IS the tip of `main`" — still true, and the tip is
  now the deployed commit.
- "The **running service still executes the pre-merge code** (pid 572048,
  started 2026-10-19 15:46:39)" — **superseded**: the service was restarted
  on 2026-10-20 and runs the merged code (new pid 732289).
- "The service was **not** restarted, reloaded or altered at any point in
  this effort" — true for the 2026-10-19 effort; the restart happened
  under the owner's later, explicit authorization.
- "**Deploying is a separate, explicitly authorized step.** … it is the
  only way to produce the end-to-end time-to-ready number this report
  declines to invent." — **superseded**: it has been authorized, performed
  and measured (`deployment-report.md`).
- "Two new runtime artifacts will appear on first boot of the new code:
  `data/.cadence.json` (persisted due-times) and `data/bars/.bar_index.json`
  (per-coin archive index)." — they did, together with
  `data/.timing.json`; all three are accelerators/state, safe to delete,
  and none is an archive.

<details>
<summary>The 2026-10-19 "NOT deployed" text (superseded, kept verbatim)</summary>


- The code is merged into `main`, and `main` is pushed to `origin/main` so
  that the pushed tip IS the tip of `main` — **verify it with
  `git rev-parse origin/main` == `git rev-parse main`**.
  *The pushed tip at the time of the whole-branch review was `51eb418`
  ("docs(quant): file the WS-C, WS-D and WS-E workstream reports"); the two
  report commits `a74f945` (this report) and `0d7e9d8` (the roadmap
  status) were AHEAD of `origin/main` and not yet pushed at that moment.
  The orchestrator pushes them together with the final-review fix commit,
  so after the push the tip is the fix commit (the child of `0d7e9d8`).*
- The **running service still executes the pre-merge code** (pid 572048,
  started 2026-10-19 15:46:39).
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

</details>

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

**Public API that is available but not wired yet**

These are implemented, documented and tested, but nothing in the shipped
paths calls them (they are referenced only by their own definitions and by
the tests).  They are listed here so that nobody reads them as an
integrated feature, and so that the next iteration does not re-invent
them:

| Symbol | What it is | Why it is not wired |
|---|---|---|
| `collector.CollectCounts.attempted` | the number of coins a collect cycle touched (succeeded + failed) | the daemon reports the succeeded count and the failed count separately; the sum was never needed |
| `daemon_timing.TimingLog.summary()` | the one-line summary of the last completed iteration | the service publishes the structured `stages` map instead |
| `daemon_timing.TimingLog.stage_durations()` | `{stage: duration_s}` of the most recent iteration | same — the structured map carries the durations and the outcomes |
| `quant.feature_store.FeatureStore.latest()` | the newest stored record of one coin/measure, regardless of any as-of timestamp (the module itself marks it DIAGNOSTIC ONLY) | a point-in-time join must use `as_of`, so the diagnostic read is not on any shipped path |
| `quant.estimators.level_from_returns()` | cumulates a return series into a level series | the estimators take levels directly; the helper is for a caller that has returns only |

---

## 6. Whole-branch review

A fresh, adversarial reviewer read the **whole** integrated diff (base
`95d0a56` → HEAD, ≈22 k lines) and re-ran both suites itself.

**Verdict: no ship blockers; the branch is safe to ship as a research-only,
SHADOW-only change.** All nine binding constraints were verified with hard
evidence, not asserted:

1. **Holdout sealed** — `data/quant_research/` does not exist; the only
   writer (`proto/quant/research.py`) is byte-identical to the base and
   still needs `--holdout` plus the one-shot lock; the research report
   refuses any window reaching into the holdout (`holdout-sealed:`); the
   preserved `evaluation.json` is untouched with `holdout: null`.
2. **SHADOW-only** — the watcher still pins `promo = None`; the selector's
   ACTIONABLE path is unreachable; the service still reports
   `"mode": "shadow"`; the research surface filters `mode='SHADOW'`.
3. **No archive modified or backfilled**, and no `data/` write at import
   (the reviewer verified this by importing every new module). The three new
   runtime sidecars (`data/.timing.json`, `data/.cadence.json`,
   `data/bars/.bar_index.json`) are created only by the deployed daemon.
4. **Auth, response shapes, archive formats, funding attribution and
   per-coin failure isolation preserved** — every protected module is
   byte-identical to the base; `/readyz`'s shape is unchanged (only its
   meaning), `/quant-research` is additive and behind the same gate.
5. **Decision log empty** — `docs/decisions/log.jsonl` is 0 bytes;
   `chain.jsonl` does not exist; no registry file exists.
6. **Stdlib only**; `app/package.json` untouched, so no new frontend
   dependency.
7. **Point-in-time** — the frame-aware `confirmed_ts`, the store's
   `available_ts` refusal, and the "clean only the slice you read" rule all
   hold.
8. **Honesty** — the registered 288-config record reproduces the artifact
   byte-for-byte and states *failed / no positive edge*; the surface
   publishes interval endpoints only and returns `status: "insufficient"`
   with a reason on an empty store.
9. **The live service was not restarted or altered** (pid 572048, up since
   2026-10-19 15:46:39) and the operator's drop-in is untouched.

The reviewer also **independently re-ran the bounded-rebuild equivalence**
on 80 production archives: **0 mismatches**, bit-identical replayed bars.

**Findings and resolutions.** No Critical findings. Two Important items were
honesty defects and are fixed (§8): a degenerate zero-width interval could
be presented as a cluster interval, and `uncertainty()` raised an
unhandled `TypeError` on a `None` day label. A dozen Minor items were
raised (an unlocked read in `last_stages()`, a mid-flush index record lost,
a resolve submitted during shutdown, a deprecation warning Python hides by
default, and nine stale or non-reproducing statements in the reports) — all
fixed in the same wave, with two adjacent fixes the fix agent made on the
same defect class (the paired-difference interval in `champion.compare()`
and a `None` coin label in `uncertainty()`).

The reviewer verified the `__main__.py` merge resolution: both `prereg
validate|show` and `champions list|compare` work, `--window {dev,val}`
cannot reach the holdout, and no conflict markers remain anywhere.

Two items were explicitly **deferred with documentation**: an int-keyed
dict accepted as `q_range` (documented, not changed), and a set of
available-but-unwired public helpers (`CollectCounts.attempted`,
`TimingLog.summary()`, `feature_store.latest()`,
`estimators.level_from_returns()`) — listed in §5.

The durable review record is this report plus the six workstream reports
filed beside it (`ws-a-report.md` … `ws-f-report.md`); each carries its own
findings-and-resolutions table.

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

---

## 8. Final review fix wave

A fresh whole-branch review of the integrated `main` found **no ship
blockers** and a set of fix-before-ship items: two honesty defects, several
small code defects, and several documentation statements that did not
reproduce or were stale.  All of them are fixed in one wave, on `main`,
without touching `data/`, without restarting the live service (pid 572048)
and without touching the holdout, a promotion or a registry file.

### 8.1 The fixes, item by item

| # | Item | Changed | Test that now covers it |
|---|---|---|---|
| 1 | **honesty** — the ≥ 3-cluster branch of `expected_return` reported a zero-width "cluster interval" as if it were certainty | `proto/quant/competing_outcomes.py:631-651` (the ≥ 3-day branch now suppresses `ci_lo`/`ci_hi`/`t` and says why; `cluster` falls back to `"row"`, the row-level interval stays available) | `tests/test_ws_b_outcomes.py:336-356` — *"a degenerate day-cluster interval is suppressed, not reported"*, *"the suppressed interval says why"*, *"the cluster field does not claim a day cluster it did not report"*, *"a degenerate interval takes its statistic with it"* (FAILS against the old code: `ci_lo=0.01 ci_hi=0.01`, `cluster="day"`, `t=0.0`) |
| 1b | the same gap in the champion comparison (reachable from the `champions compare` CLI) | `proto/quant/champion.py:485-538` (`_side_block`: the interval, the statistic and a new `reason` key) and `proto/quant/champion.py:753-766` (the paired-difference interval, same rule — the same defect in the same function) | `tests/test_ws_e_champion.py:448-470` — *"a degenerate side interval is suppressed, not reported"*, *"a degenerate side interval takes its statistic with it"*, *"a degenerate difference interval is suppressed too"* (FAILS against the old code: `ci_lo == ci_hi == 0.20207259421636908`, `t=0.0`) |
| 2 | **honesty** — `uncertainty()` raised an unhandled `TypeError` on a `None` day label | `proto/quant/inference.py:339-344` (docstring) and `proto/quant/inference.py:357-366` — a `None` day label is a *missing* label, treated exactly like "no day labels" (`no-day-labels`, no day interval, no day statistic, no exception). The same rule now covers a `None` **coin** label (`proto/quant/inference.py:381-391`), which had the identical crash (`_coin_bootstrap` sorts the coin keys) | `tests/test_ws_b_outcomes.py:461-479` — *"a None day label never raises"*, *"a None day label means no day interval"*, *"a None coin label never raises either"* (the old code raised `TypeError: '<' not supported between instances of 'NoneType' and 'str'`) |
| 3 | the running stage map was read **without** the lock while the daemon thread mutated it | `proto/daemon_timing.py:269-286` (`last_stages()` takes the snapshot under the lock) and `proto/daemon_timing.py:181-266` (`load`/`save`/`begin`/`record`/`end`/`last` are lock-protected too, so the lock actually protects something) | `tests/test_ws_f_startup.py:601-670` — *"reading the running stage map never raises"* (FAILS against the old code: `TypeError: 'NoneType' object is not subscriptable`, swallowed by `service._scanner_health`) |
| 3b | `first_sweep` must not be read as "the system ever completed a sweep" | `proto/daemon_timing.py:34-40` (module docstring), `proto/daemon_timing.py:305-313` and `proto/daemon_timing.py:357-365` (the property and the reader), `proto/service.py:19-25` (the module docstring) and `proto/service.py:1125-1135` (the `/scanner-health` route docstring): the state is **process-local** and resets to `pending` on every restart | documentation (no behaviour change) |
| 4 | a coin recorded **during** a flush was never persisted (the flag was cleared after the snapshot) | `proto/collector.py:105-154` — `dirty` is cleared **before** the snapshot, under the same lock; a failed write restores it | `tests/test_ws_f_startup.py:545-598` — *"the coin recorded during the flush keeps the index dirty"*, *"the coin recorded during the flush reaches the disk"*, *"a failed write keeps the index dirty"* (FAILS against the old code: `dirty=False`, the late coin never reaches the disk) |
| 5 | a resolve pass could be **submitted during a SIGTERM iteration** | `proto/daemon.py:573-590` — the resolve block now tests `interrupted` first: no pass is started and the stage is recorded as `skipped` | `tests/test_ws_f_startup.py:993-1046` — *"an interrupted iteration starts no resolve pass"* (FAILS against the old code: the pool received `resolve_batch`) |
| 6 | the `ready=`/`is_ready=` deprecation was invisible (a `DeprecationWarning` is filtered out outside `__main__`) | `proto/service.py:467-505` — the deprecation is written to **stderr**, logged through `proto.service` and still raised as a `DeprecationWarning`; the parameters' behaviour is unchanged | `tests/test_ws_f_startup.py:945-991` — *"the deprecation is written to stderr"*, *"the deprecation still reaches the warnings machinery"*, *"the deprecation is emitted only once per process"* (FAILS against the old code: stderr empty) |
| 7 | the "a 4-scale ladder (8..16)" claim was false (the ladder is every integer in the range: **9** scales at n = 32) | `proto/quant/mfdfa.py:207-213`, `docs/…/ws-a-report.md:1167` and `docs/…/ws-a-report.md:1288` | documentation (verified: `mfdfa(levels, n=32)` reports `n_scales = 9`) |
| 8 | the quoted probe "first prediction p = 0.995455" did not reproduce | `docs/…/ws-b-report.md:530-540` — the figure is `0.986979` (the exact value the committed fixture produces: `0.9869791666666666`; `n_train = 40` and `n_predictions = 10` do reproduce) | documentation (verified against `tests/test_ws_b_outcomes.py`'s fixture) |
| 9 | the WS-B "Public surface" list omitted `cause_incidence` | `docs/…/ws-b-report.md:55-64` | documentation (the signature `cause_incidence(outcomes, horizon_bars)` matches the module) |
| 10 | "the layer is not wired into any CLI, report or service path" was false | `docs/…/ws-b-report.md:762-771` — the `champions compare` sub-command of `proto/quant/__main__.py` drives `competing_outcomes` + `inference` through `champion.compare` (read-only: it cannot promote and it cannot spend the holdout) | documentation (the CLI path is covered by `tests/test_ws_e_champion.py`) |
| 11 | the `_quant_db_writable` docstring pointed at the deleted `_db_writable` | `proto/service.py:523-537` — the reference is now `_db_readable`, and the sentence says what that probe actually does | documentation |
| 12 | four stale line references in the WS-A report | `docs/…/ws-a-report.md:745` (`measures.py:321-322`), `docs/…/ws-a-report.md:791-796` (`structure.py:174`, `structure.py:288`, `structure.py:308`) and `docs/…/ws-a-report.md:822-823` (`estimators.py:470`, `estimators.py:474`) | documentation — every referenced line was re-read and now matches the code. **One deviation from the review:** the review asked for `measures.py:321 → 320`; line 321 **is** the aligned-tail cleaning (`xa, bad_a = _clean(ra[-n:])`, and 322 is the second series), while 320 is the `"insufficient-sample"` return — so the reference was tightened to `321-322` instead of being moved to a line that does not hold the code. The review's `estimators.py:364 → 474` was also re-checked: 474 is `_bootstrap_agg`, and the `H = slope / 2` expression is at 470; both are now cited. |
| 13 | "The code is merged into `main` and pushed" overstated the state at review time | `docs/…/final-report.md:143-156` — the pushed tip is now stated explicitly and is verifiable (`git rev-parse origin/main` == `git rev-parse main`), and the two commits that were ahead of `origin/main` (`a74f945`, `0d7e9d8`) are named. The orchestrator pushes them together with this fix commit | documentation |
| 14 | the workstream / wave status tables were stale | `docs/…/progress.md:54-74` (C/D/E: whole-branch review **done**) and `docs/…/progress.md:92-99` (waves 1-3 **done**, wave 4 review **done**) | documentation |
| 15 | the `first_sweep` fallback for an embedder with its own `scanner_health` can report `state: "complete"` with `completed_ts: null` | `proto/service.py:1163-1180` — the fallback is documented in the code: it means "the embedder's readiness flag says the first sweep happened", never "a sweep completed at a known time"; the service itself never takes that path | documentation (the reviewer's triage: defer the behaviour change, fix the wording) |
| D1 | *deferred, documented only* — `_orders` accepts an int-keyed mapping as `q_range` but refuses the same mapping with string keys | `proto/quant/mfdfa.py:131-151` — the accepted shapes and the inconsistency are now in the docstring | documentation (no behaviour change) |
| D2 | *deferred, documented only* — public API that is available but not wired | `docs/…/final-report.md:224-238` (§5) — `collector.CollectCounts.attempted`, `daemon_timing.TimingLog.summary()` / `stage_durations()`, `feature_store.FeatureStore.latest()` and `estimators.level_from_returns()` are listed with what each one is and why nothing calls it yet | documentation (no behaviour change) |

### 8.2 What was deliberately NOT changed

- The live service (pid 572048) was not restarted, reloaded or altered, and
  no systemd file was touched.
- `data/` was not written to; the holdout was not spent; nothing was
  promoted; no `docs/decisions/chain.jsonl` and no registry file was
  created in the repository.
- The `first_sweep` fallback of item 15 keeps its behaviour (the wording is
  fixed, the behaviour is deferred by the reviewer's triage).
- `_orders` (D1) keeps its behaviour (the inconsistency is documented).

### 8.3 The command outputs

The six focused workstream suites:

```
$ python3 tests/test_ws_f_startup.py
ALL PASS  tests/test_ws_f_startup.py  (106 checks)

$ python3 tests/test_ws_a_measurement.py
ALL PASS

$ python3 tests/test_ws_b_outcomes.py
ALL PASS

$ python3 tests/test_ws_c_prereg.py
ALL PASS

$ python3 tests/test_ws_d_research_surface.py
ALL PASS  tests/test_ws_d_research_surface.py  (86 checks)

$ python3 tests/test_ws_e_champion.py
ALL PASS  tests/test_ws_e_champion.py  (146 checks)
```

The three quant-side suites:

```
$ python3 tests/test_quant.py
ALL PASS

$ python3 tests/test_hypotheses.py
ALL PASS

$ python3 tests/test_decisions.py
ALL PASS
```

The full offline backend suite:

```
$ python3 tests/run_all.py
...
PASS  test_ws_a_measurement.py  [ALL PASS]
PASS  test_ws_b_outcomes.py  [ALL PASS]
PASS  test_ws_c_prereg.py  [ALL PASS]
PASS  test_ws_d_research_surface.py  [ALL PASS  tests/test_ws_d_research_surface.py  (86 checks)]
PASS  test_ws_e_champion.py  [ALL PASS  tests/test_ws_e_champion.py  (146 checks)]
PASS  test_ws_f_startup.py  [ALL PASS  tests/test_ws_f_startup.py  (106 checks)]

ALL SUITES PASS (87 files)
```

The frontend gates (`app/`):

```
$ npm run test
 Test Files  14 passed (14)
      Tests  200 passed (200)

$ npm run typecheck
> tsc --noEmit
(exit 0, no output)

$ npm run lint
> eslint . --max-warnings 0
(exit 0, no output)

$ npm run build
> tsc --noEmit && vite build
✓ 66 modules transformed.
dist/index.html                   0.44 kB │ gzip:  0.28 kB
dist/assets/index-2Pm9sww9.css   11.81 kB │ gzip:  3.22 kB
dist/assets/index-BxMF15B1.js   367.35 kB │ gzip: 114.47 kB
✓ built in 9.79s
```

### 8.4 The regression tests fail against the old code

Every new check was run against the pre-fix source (the fixed files were
stashed and the suites re-run).  The results:

| New check | Against the pre-fix code |
|---|---|
| WS-B "a degenerate day-cluster interval is suppressed, not reported" | `FAIL  ci_lo=0.01 ci_hi=0.01` |
| WS-B "a None day label never raises" | `TypeError: '<' not supported between instances of 'NoneType' and 'str'` (the suite crashed) |
| WS-E "a degenerate side interval is suppressed, not reported" | `FAIL  0.20207259421636908 0.20207259421636908` |
| WS-F "the coin recorded during the flush reaches the disk" | `FAIL  ['AAA']` (the late coin never reached the disk) |
| WS-F "reading the running stage map never raises" | `FAIL  TypeError("'NoneType' object is not subscriptable")` |
| WS-F "an interrupted iteration starts no resolve pass" | `FAIL  ['resolve_batch']` |
| WS-F "the deprecation is written to stderr" | `FAIL` (stderr empty) |

