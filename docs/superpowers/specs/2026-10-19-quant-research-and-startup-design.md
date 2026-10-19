# ALT RADAR — quant research capabilities + startup optimization (design & plan)

**Date:** 2026-10-19 · **Status:** DRAFT for approval · **Scope:** Workstreams A–F
**Author:** implementation lead (OpenCode). Research-only, SHADOW-only.

---

## 0. What the code actually says (verified, not inherited)

Everything below was verified against the current tree (`main` @ `4f9d7a7`) by
read-only code survey + a live read-only inspection of the running service.

### 0.1 Hard constraints discovered

| # | Constraint | Consequence |
|---|---|---|
| C1 | **No scientific stack.** No `requirements.txt`/`pyproject.toml`; `pandas`, `numpy`, `scipy`, `statsmodels`, `sklearn` are all absent. The codebase is explicitly **stdlib-only** (`proto/quant/__init__.py:7`). | All new research code is **pure-stdlib Python 3.10+**. No dependency install (that would be a state change needing approval). |
| C2 | **The service already listens in <1 s.** `proto/service.py:2009-2017` binds + serves *before* the loop thread starts (`:2085-2087`). | Workstream F is **not** "make the API start faster" — it is "stop *readiness* from being hostage to the first sweep, and stop a restart from redoing the same work". |
| C3 | `/readyz` means **"first sweep complete"** (`sweep_done.set()` fires only at `proto/daemon.py:471-487`, the *end* of the whole first iteration). | `/readyz` must be split from first-sweep status. |
| C4 | **Only settlements and prices resume across a restart** (persisted `.last_collect` files). Scan, collect, resolve and measure due-times are **process-local** (`proto/daemon.py:243-245` → `0.0`). | A restart re-runs the full 150-coin scan, the full 560-coin collection, the full resolution and the full measurement cycle. |
| C5 | The **collect phase is dominated by re-reading every archive** to compute the dedupe set (`proto/collector.py:27-41` calls `read_bars` → full gzip parse per coin per cycle). 579 files × ~28 k rows ≈ 287 MB parsed per cycle. | Incremental collect must stop re-parsing whole archives. |
| C6 | The **quant step rebuilds every coin from scratch on first boot** (579 × full `load_coin`), even though `quant_watermark` is persisted and **never read back** (`proto/quant/qstore.py:48-50`). | The watermark becomes a resume point. |
| C7 | `/readyz` also does a **write probe** on the live 170 MB WAL DB (`_db_writable`, `proto/service.py:443-459`) → 503 "database not writable" under daemon write-lock contention, *after* a successful sweep. | Readiness must not depend on a write probe. |
| C8 | **Each iteration writes the sweep twice** — `proto/app.py:192` (end of scan) and `proto/daemon.py:181` (end of iteration) → 2 `watcher_events` rows + 2 `dashboard_snapshot` rows per sweep. | Fix the double count; keep sweep accounting honest. |
| C9 | `TimeoutStopSec=600s` is an **operator drop-in** (`~/.config/systemd/user/altradar.service.d/quant-shadow.conf`) and must not be modified. Shutdown currently blocks on `loop_thread.join()` (`:2096`) for a whole non-interruptible phase. | Shutdown must become **phase-interruptible** so the 600 s grace is sufficient. |
| C10 | Promotion is unreachable **by design**: the watcher hard-pins `promo = None` (`proto/quant/engine.py:198`), the route hard-codes `"mode": "shadow"` (`proto/service.py:1433`), and `tests/test_quant_shadow_only.py` proves it. | Nothing in A–E may weaken this. |
| C11 | The frontend has **strict house rules** (banned vocabulary, no `<table>` in Research, no raw `fetch`, theme tokens only, `import type` required, `--max-warnings 0` lint). | Workstream D must follow them exactly. |
| C12 | The holdout is **sealed** (`docs/decisions/log.jsonl` is 0 bytes; `evaluation.json` holdout fields are `null`; `research.holdout_once` writes a lock). | No workstream may spend, unlock or tune against the holdout. |

### 0.2 Measured baseline (live service, read-only)

From the running unit (`journalctl --user -u altradar`, pid 572048, started 15:46:39):

| Stage | Log line | Elapsed from start |
|---|---|---:|
| HTTP listener | `service: listening on 127.0.0.1:18741` | ~0 s |
| first `scan:` | 15:54:17 | **458 s** |
| `collect:` | 16:00:50 | **851 s** |
| `funding:` | 16:00:58 | 859 s |
| `settlements:` | 16:09:39 | 1 380 s |
| `prices:` | 16:21:53 | 2 114 s |
| `measure:` summary | 16:35:38 | 2 939 s |
| **`/readyz` → 200** | 16:52:30 | **3 951 s (65.8 min)** |

`/scanner-health` reported `sweep_count=1, last_sweep_duration_s=3950.25` — a
single number lumping eight phases. The quant step ran silently for ~1 012 s.

**No per-stage durations exist anywhere.** `app.last_scan_phases` is written
but never read; the quant step's `seconds` return is discarded.

### 0.3 What already exists and must be reused (not rebuilt)

`stats.day_cluster_mean` / `day_cluster_t` / `spa_max_t` (multiplicity) /
`aalen_johansen` (censoring) / `EProcess` / `calibration_slope`;
`model.bucket_table` (calibration curve); `research.run_stage_dev_val`
(dev→val selection), `research.holdout_once` (lock), `eval_harness.check_no_leakage`;
`cost_attribution` (per-regime venue schedule — see the 2026-10-19 report);
`barrier.label_path` (conservative stop-first ordering), `barrier.make_path(run_end=…)`;
`stream.FeatureStream` (causal per-bar features); `outcomes.resolve_trade`
(stop/TP2/expiry, fill-bar stop terminal); `episode_report` (coin-cluster
bootstrap, denominators, `exploratory` labels); `hypothesis_registry`
(front-matter validator, split/embargo rules); `decisions.validate_log`;
`quant.gates.evaluate` (the real holdout gate).

### 0.4 What is genuinely missing

- **A:** rolling Hurst, MF-DFA + multifractal spectrum, real multi-scale
  confirmed pivots (with *confirmation* timestamps), a point-in-time feature
  store, tail-risk measures, market-regime classification, breadth,
  cross-sectional dependence measures, volatility/return measure versioning.
- **B:** thesis-invalidation and true-censoring outcome causes, gross/net
  outcome distributions with risk measures, chronological probability
  estimation + calibration artifacts, coin×date uncertainty for *outcomes*,
  baselines (unconditional, rule-without-features, matched placebo),
  hypothesis-**family**-level multiple-testing correction.
- **C:** a preregistration record that actually captures feature definitions,
  point-in-time universe membership, event rules, costs, statistics,
  promotion criteria, **data + code hashes**, and **all tested variants
  including failures**; results sliced by coin / period / vol-regime /
  liquidity group.
- **E:** any champion/candidate concept (0 matches for `champion` in `*.py`),
  versioned persisted configs, matched-cost comparison of two configs on
  identical later data, decision-log `reject`/`continue` kinds, evidence
  hashes, tamper evidence.
- **F:** per-stage instrumentation, readiness split, persisted due-times,
  incremental/resumable collect + quant step, interruptible phases,
  honest scanner health.

---

## 1. Design

### 1.1 Shape

Six workstreams, three waves, one module boundary per concern. Every new
module is **stdlib-only**, **read-only w.r.t. production archives** during
research, **deterministic** (fixed seeds), and **honest** (explicit
`None`/`unavailable` instead of fabricated values).

```
proto/quant/
  structure.py      NEW  A  multi-timeframe structure + confirmed pivots
  estimators.py     NEW  A  Hurst, MF-DFA, vol/tail/regime/dependence/breadth
  feature_store.py  NEW  A  point-in-time feature join (as-of) + versioning
  outcomes.py       NEW  B  competing outcomes + censoring + gross/net dists
  inference.py      NEW  B  chronological probability, calibration, baselines
  family_test.py    NEW  B  hypothesis-family-level multiple testing
  prereg.py         NEW  C  preregistration record + variant registry
  research_report.py NEW C  sliced results (coin/period/regime/liquidity)
  champion.py       NEW  E  champion/candidate registry + matched comparison
  decision_log.py   NEW  E  immutable decision entries w/ hash chain
  daemon_timing.py  NEW  F  per-stage timing + persisted due-times
  readiness.py      NEW  F  readiness model (api vs first-sweep)
```

`proto/service.py` gains **read-only** routes for the new research surfaces;
`proto/daemon.py` gains phase timing, persisted due-times and interruptible
phases. The frontend gains one expanded Research view.

### 1.2 Workstream A — measurement & structure

**Contracts.**
- `structure.confirmed_pivots(bars, left, right) -> list[Pivot]`
  `Pivot = {index, ts, price, kind: "high"|"low", confirmed_ts, bars_of_leg, size}`
  `confirmed_ts` = the timestamp at which the pivot became knowable
  (= the close of bar `index+right`) — **the key field** for as-of joins.
  Uniqueness is **local-window**, not global (the existing
  `indicators.swing_low/high` uses a global uniqueness test and returns a
  price only — it is *not* reused; it stays as-is for the watcher).
- `structure.multi_timeframe(bars, frames) -> {frame: {structure, pivots}}`
  Frames are resampled **from the 5 m series** (5 m/15 m/1 h/4 h) with
  closed-bar-only semantics; higher frames expose only bars whose close is
  at or before the as-of timestamp.
- `estimators.hurst_rs(returns) -> {h, n_scales, uncertainty}`
  `estimators.hurst_generalized(returns, max_q) -> {h, q, uncertainty}`
  `estimators.mfdfa(returns, q_range) -> {alpha, width, h, spectrum}`
  All with documented assumptions (i.i.d. vs stationary, q-range, scale
  choice) and a **bootstrap or jackknife uncertainty band**; `None` when
  the sample is too short (never a fabricated value).
- `estimators.volatility_measures / tail_measures / regime / dependence / breadth`
  → a `Measure` record: `{name, version, value, units, window, as_of, inputs, uncertainty}`.
- `feature_store.FeatureStore` — append-only, keyed `(coin, bar_ts, name, version)`
  with `available_ts` (when the value became knowable). The as-of join
  returns the **latest value with `available_ts <= event_ts`**, and
  `None` + a reason (`"stale"`, `"insufficient"`, `"missing"`) otherwise.
  **No filling, no forward-fill, no interpolation.**

**Acceptance.** Unit tests: pivot confirmation timing (a pivot is invisible
before `confirmed_ts`); local vs global uniqueness; no lookahead (features
at `t` are unchanged when all bars after `t` are perturbed); Hurst on known
series (white noise ≈ 0.5, random walk ≈ 1.0) with a stated tolerance; MF-DFA
α on a synthetic monofractal ≈ 0.5 and on a constructed multifractal > 0.5;
as-of join returns `None` for a missing/stale feature and never a future
value; measure versioning bumps on any parameter change.

**Non-goals.** No feature is blended into a score; no feature is used for
selection in this task.

### 1.3 Workstream B — outcomes & inference

**Contracts.**
- `outcomes.resolve_competing(...)` → a **cause taxonomy**:
  `TP | SL | TIME | INVALIDATED | CENSORED`, where
  `CENSORED` covers end-of-sample and unresolved data loss (explicitly
  distinct from `INVALIDATED`), and conservative barrier ordering
  (stop-first on the same bar, fill-bar stop terminal) is preserved.
- `outcomes.outcome_distribution(...)` → gross and net distributions,
  expected return, and risk measures (hit rate, adverse excursion, tail
  quantiles), with **friction and funding separately attributable**
  (`gross`, `friction`, `funding`, `net` are four separate fields; a
  funding-adjusted figure is never labelled friction-only, and an
  incomplete funding attribution yields `net=None` + a reason, not a
  friction-only number).
- `inference.chronological_probability(...)` → expanding-window
  probability estimates with a calibration table (predicted vs realized by
  bucket, with per-bucket denominators).
- `inference.uncertainty(values, days, coins)` → day-cluster **and**
  coin-cluster intervals (both, reported separately).
- `inference.baselines(...)` → three baselines: unconditional/no-event,
  the existing rule **without** the new features, and a **matched placebo**
  (same time-of-day/coin/liquidity distribution, different event).
- `family_test.correct(family)` → family-level multiple-testing correction
  over the declared experiment family (bootstrap max-t over the family's
  hypotheses, plus a Holm fallback), with the family **declared before**
  evaluation.

**Acceptance.** Cause taxonomy is exhaustive and mutually exclusive
(counts sum to the denominator); censored events are excluded from the
resolved denominator and reported separately; gross/net decomposition
reconciles exactly (`net = gross − friction − funding`); calibration
buckets carry denominators; every baseline is computed on the *same*
event set; family correction returns a p-value ≥ the per-hypothesis
p-value and is monotone under family enlargement.

### 1.4 Workstream C — preregistration workflow

**Contract.** `prereg.record(...)` writes a **versioned preregistration
record** (JSON, one file per hypothesis, append-only) capturing:
hypothesis + feature definitions; universe + point-in-time membership rules;
horizons; event rules; costs; splits; embargo; statistics; promotion
criteria; `config_hash`, **data hash** (archive digests), **code hash**
(git rev + file digests); and a **variant registry** listing every tested
variant *including failures* with its own result and hash.

`prereg.load` / `prereg.validate` extend the existing front-matter
discipline: the existing `docs/hypotheses/*.md` format stays valid
(compatible), and a richer **sidecar JSON** carries the machine-readable
record. Results are reported by **coin, period, volatility/regime group and
liquidity group** where the data supports it (`research_report.py`),
always with denominators and an `insufficient` label when a cell is too
small. Dev→val→holdout order and embargo are enforced; the holdout is
**never** spent by this workflow.

### 1.5 Workstream D — Research UI expansion

Read-only, after the API contracts land. The Research view gains:
sample counts + denominators ("X of Y" house pattern), interval endpoints
only (never a point reading), calibration/coverage/validation status,
feature provenance (`config_hash` + data hash + measure version), and
**insufficient-evidence states with reasons**. The existing house rules
(no `<table>` in Research, banned vocabulary, no raw `fetch`, theme tokens
only, `import type`, lint `--max-warnings 0`) are preserved; the dev mock
service gains the routes it lacks.

### 1.6 Workstream E — champion/candidate tracking

`champion.Registry` persists **versioned** champion and candidate
configurations (config + rule ids + hashes + status + timestamps).
`champion.compare(champion, candidate, window, ...)` evaluates **both on
identical later data with matched costs** (same events, same cost model,
same funding attribution) and returns a paired comparison with
coin×date-clustered uncertainty. `decision_log.append(...)` writes an
**immutable** decision entry: `{id, ts, kind: promote|reject|continue,
subject, evidence: [{path, sha256}], prev_hash, hash}` — a hash chain makes
tampering detectable. The current log (`docs/decisions/log.jsonl`, 0 bytes)
is **preserved as-is**; the new store is a separate, additive artifact
until the owner decides to migrate. **No promotion happens in this task.**

### 1.7 Workstream F — startup & first-sweep

1. **Instrument first.** `daemon_timing.StageTimer` records
   `scan / collect / funding / settlements / prices / measure / quant /
   resolve` durations per iteration into a persisted ring
   (`quant_meta` / a new `data/` JSON, no archive writes) and exposes them
   via `/scanner-health`. This is the "diagnose the actual bottleneck"
   step and it is the **first** deliverable, because F's other changes are
   only justified once the numbers exist.
2. **Readiness split.** `/readyz` becomes **API-serving readiness**: 200
   once the listener is up and the DB is *readable* (a **read** probe, never
   a write probe → fixes C7). A **distinct** first-sweep/scanner status is
   added to `/scanner-health` (`first_sweep: {state, started_ts,
   completed_ts, duration_s, stage_durations}`) and a new
   `/scanner-status` alias may be added. The change is documented, and the
   frontend's `readyz()` usage is updated accordingly.
   **No route may claim a sweep completed that did not.**
3. **Persisted due-times.** `scan`, `collect`, `resolve`, `measure` due
   times move from process-local floats to a persisted file
   (`data/.cadence.json`, sibling of the DB) so a restart **resumes**:
   a restart inside the interval does not redo full-universe work. First
   boot / stale data / interrupted work still schedules promptly and
   exposes progress.
4. **Incremental & resumable.** The collector stops re-parsing whole
   archives: a per-coin **last-seen ts** index (persisted) replaces the
   full-file dedupe scan. The quant watcher **reads back
   `quant_watermark`** so a restart resumes instead of rebuilding 579
   coins. Long phases get **bounded per-coin timeouts**; per-coin failure
   isolation is preserved (already present in the collector).
5. **Interruptible shutdown.** The daemon checks a stop flag **between
   phases and between per-coin units**, so `SIGTERM` drains inside the
   operator's 600 s grace. The grace value is **not** modified.
6. **Honest accounting.** The double sweep record (C8) is collapsed to
   one authoritative write; `last_sweep_duration_s` keeps its meaning and
   a new `stage_durations` breakdown is added. `data_lag_s` and the
   scanner lock semantics are untouched.
7. **Rate limits.** The shared 20 req/s limiter is preserved; the
   resumable collect must not exceed it.

**Acceptance.** A restart with warm data reaches API-ready in **< 2 s** and
first-sweep status honestly reports "in progress" with per-stage durations;
a cold start does **not** repeat a full-universe scan/collect that is not
due; interrupted work is resumable; `SIGTERM` drains within the 600 s
grace; the scanner lock still refuses a second instance; no secret is
logged.

---

## 2. Execution plan (waves, dependencies, isolation)

| Wave | Workstream | Agent(s) | Depends on | Files touched (disjoint by construction) |
|---|---|---|---|---|
| 1 | **F** | 1 implementer + 1 reviewer | — | `proto/daemon.py`, `proto/service.py`, `proto/daemon_timing.py`, `proto/collector.py`, `proto/quant/engine.py`, `proto/quant/qstore.py` |
| 1 | **A** | 1 implementer + 1 reviewer | — | `proto/quant/structure.py`, `proto/quant/estimators.py`, `proto/quant/feature_store.py` (+ their tests) |
| 1 | **B** | 1 implementer + 1 reviewer | — | `proto/quant/outcomes.py`, `proto/quant/inference.py`, `proto/quant/family_test.py` (+ tests) |
| 2 | **C** | 1 implementer + 1 reviewer | A, B contracts | `proto/quant/prereg.py`, `proto/quant/research_report.py`, `docs/hypotheses/` |
| 2 | **E** | 1 implementer + 1 reviewer | B contracts | `proto/quant/champion.py`, `proto/quant/decision_log.py` |
| 3 | **D** | 1 implementer + 1 reviewer | A/B/C/E API contracts | `app/src/**`, `app/tests/**`, `app/dev/**` |
| 4 | **integration** | me + 1 fresh whole-branch reviewer | all | — |

Rules honoured: each wave-1/2 task gets a **fresh implementer and a fresh
reviewer**; implementers work in **separate git worktrees**; **no two
agents touch the same file concurrently** (the table is the ownership map);
I integrate in dependency order and resolve conflicts myself; the final
whole-branch review is fresh.

**Verification gates per workstream:** interfaces + acceptance criteria
defined *before* dispatch (this document) → regression tests written first
(RED) → implementation → focused tests → reviewer pass → integrate →
full offline suite (`tests/run_all.py`) → for D: `npm run test`,
`npm run typecheck`, `npm run lint`, `npm run build`.

---

## 3. Safety boundaries (non-negotiable)

- No modification or backfill of production archives; tests use temp fixtures;
  archive hashes verified unchanged where relevant.
- **No restart/alteration of the live systemd service** during implementation.
  Deployment is a separate, reviewed, user-authorized step.
- No ACTIONABLE output, no order placement, no promotion, no holdout spend
  or retuning. The 288-config evaluation is **not** re-run.
- Route auth, response shapes, archive formats, funding attribution and
  failure isolation are preserved unless a reviewed plan says otherwise
  (the only planned shape change is `/readyz` semantics, which is
  documented and mirrored by a distinct first-sweep status).
- No production data or local systemd drop-ins in commits.
- The operator's `TimeoutStopSec=600s` drop-in is read-only.

## 4. Open decisions (need the owner's call)

1. **`/readyz` semantics** — change it to API-serving readiness (recommended,
   matches the brief) and expose first-sweep status on `/scanner-health`,
   or keep `/readyz` as-is and add a separate `/api-ready`?
2. **Depth vs breadth** — implement all six workstreams to the contracts
   above (large), or land F first (it has the clearest operational payoff)
   and then A–E?
3. **Decision-log migration** — keep the new hash-chained log as a
   *separate* artifact (recommended; the current log is 0 bytes and
   human-owned) or write into `docs/decisions/log.jsonl`?
