# ALT RADAR Roadmap Status

Product goal: quantitative research and analysis for MEXC altcoin perpetual
futures. Rules support quant analysis; heuristic scores are not evidence of
edge. Read-only; no order execution. BTC is labeled market context only.

Last updated: 2026-10-15 (late — R1–R4 + WS5-1 closed, full suite green).
Verified against code/data, not inherited summaries.

## Deployment gap (verified 2026-10-15 ~22:05 UTC, live system)

- The running backend (ActiveEnterTimestamp 2026-10-15 11:09) **predates
  all of today's commits**: `GET /scanner-health` answers `404 NOT_FOUND`
  ("no such route") although the route exists at `proto/service.py:653`,
  and `data/funding/` does not exist although `proto/funding.py` is
  committed and the collect cycle is running (569 coins, bars flowing).
  Cause is a stale process, not a code bug.
- Action required before any WS2/WS3/WS4 behavior is live:
  `systemctl --user restart altradar.service`, then re-verify
  `/scanner-health` (expect 200 with lifecycle state) and
  `data/funding/*.csv.gz` after one collect cadence. Restart not performed
  — it recycles the 24/7 collector, so it waits on user go-ahead.

## Deployment gap RESOLVED (R1 verified 2026-10-15, post-restart)

- Restart performed (`systemctl --user restart altradar.service`,
  exit 0; new pid 200835, exactly one listener on 127.0.0.1:18741).
- `GET /scanner-health` answers 200 `{"ok": true, "data":
  {"status": "healthy", ...}}` (pre-restart probe confirmed the old
  code: `NOT_FOUND no such route`).
- Funding archive grew from baseline `(absent)` — `data/funding/` did
  not exist pre-restart — to 569 files / 41,615 bytes with header
  `ts,rate,cap` (spot-check `BTC.csv.gz`: one data row). Growth, not
  mere existence. First collect took ~10min (universe refresh + slow
  first scan/measure), slower than the nominal 300s cadence.
- Note: the old process needed SIGKILL after SIGTERM timed out
  (mid-measure-cycle); pre-existing shutdown behavior, new process
  acquired the lock cleanly.

## Workstream status

### 1. Protect and validate existing data — STAGE 1 COMPLETE (audit tool delivered) + WS1 FINDINGS RESOLVED
- Deps: none (foundation for all later streams).
- Complete: append-only archives (`proto/collector.py:23-37`, 576 coins in
  `data/bars/`); venue adapters validate + sort + drop forming bar
  (`proto/mexc.py:124-162`, `proto/bybit.py:148-183`); outcomes/episode
  layers treat gaps explicitly (`proto/outcomes.py:247-285`,
  `proto/measurement.py:509-539`).
- Stage 1 complete (audit tool delivered): `proto/bar_quality.py` audit
  (single-pass, no archive sort) + CLI report + tests.
  - Commits: f11b789 (audit_bar_sequence), 0bd1938 (streaming archive audit +
    incoming check), 8073b63 (CLI `main`).
  - Verification: `tests/test_bar_quality.py` → ALL PASS (113 checks).
    `tests/run_all.py` → ALL SUITES PASS (60 files).
- WS1 data-quality findings RESOLVED (2026-10-15, commits c1a490b + 27299bf):
  - Audit defect fixed first: `coverage`/`age_s` used raw FILE-ORDER
    first/last, so out-of-order archives reported a negative span and the
    bogus `coverage.ratio=7.03` "anomaly" (an artifact, not a data defect).
    Both now use the sorted unique min/max; `first_ts`/`last_ts` keep
    documenting file order (the ordering diagnostic).
  - Corrected corpus findings (all 576 archives): perfect 300s grids —
    ratio exactly 1.0, zero duplicates, zero gaps, zero invalid rows. The
    data is COMPLETE; only file LAYOUT is out of order (566 coins, 12
    deterministic older-batch-appended-after-newer jumps each); 15 coins
    genuinely >36h stale.
  - Remediation decision (justified): sort-ON-READ (`read_bars`, commit
    27299bf) — zero archive mutation, idempotent, fixes every read path
    (service /bars, outcomes, replay) at once. Sort-AND-REWRITE NOT
    justified: data already complete, rewrite adds corruption risk for
    zero gain. Archives on disk remain untouched (hash-verified by test).
  - The previously uncommitted `proto/collector.py` change was
    deliberately reviewed as part of this stage and committed.
- Stale-15 investigation RESOLVED (2026-10-15, report-first, explicit
  no-fix): the Stage-1 tool
  (`python3 -m proto.bar_quality --data-dir data/bars --all`) confirms
  exactly 15 archives with `age_s > 129600` (36h); the other 561 are
  fresh (~20min old). ALL 15 are MEXC delistings, not a collector
  defect:
  - COOL + ALLINU (~129h stale; last bars 2026-10-10 12:50/12:55Z):
    MEXC delisted the ALLINU and COOL USDT-M perps 2026-10-10 13:00Z —
    the last bars are the final closed bars before delisting.
  - 1000BTT, BOBA, DBR, RON, ZBCN, DEBIT, DELTA, GEOD, NPC, SWARM,
    TFUEL, UPROBINHOOD, ZIG (~47h stale; last bars 2026-10-13
    23:00/23:05Z): MEXC delisted all 13 (plus SNT, never archived)
    2026-10-14 07:00Z. Venue quotes froze ~8h before the official
    delist; daemon collect cycles kept running clean at 582 coins with
    no errors, and the universe stepped 582 → 569 (= 582 − 13) across
    the 2026-10-14 00:11–09:38 service gap. All 15 are absent from the
    live ticker list (1208 tickers) and return empty klines today.
  - No-fix statement: archives are intact frozen history (still
    readable via `read_bars`); `collect_full_universe`
    (`proto/collector.py:43-53`) iterates the live `build_universe`,
    so a delisted coin stops accumulating by design — no exception,
    fetch, or data path is broken. No code or test change shipped.
    (Possible future nicety, NOT implemented: log coins that drop out
    of the universe between cycles for visibility.)
- Remaining: archive rewrite stays deferred unless a future consumer
  needs on-disk order (none does today).

### 2. API available through scanner failures — STAGE 2 COMPLETE (committed)
- Deps: WS1 (bar validity for /bars), WS1 audit findings inform health signals.
- Committed behavior (in the three audit commits' successor commit, below):
  - HTTP API stays reachable while the scanner is down: /healthz answers 200
    through crashes, retries, and give-up; /readyz independently reports
    first-sweep readiness; /scanner-health reports the scanner lifecycle.
  - Scanner crashes retry with capped exponential backoff
    (`DEFAULT_BACKOFF_BASE_S=5`, doubling to `DEFAULT_BACKOFF_MAX_S=300`,
    at most `MAX_SCANNER_RETRIES=12` retries; all tunable per-run via
    `run_service(backoff_base_s=..., backoff_max_s=...,
    max_scanner_retries=...)`).
  - A finite `--iterations` run that completes normally is completion, not a
    crash: it is never restarted in a tight loop.
  - Recovery is sweep-driven: a retry starts in `restarting` and becomes
    `healthy` only when its first completed sweep fires the explicit
    recovery signal (`on_sweep`); `restarting` is never reported as
    recovered. `restart_count` / `last_restart_ts` count restart attempts
    BEGUN (the initial start is not a restart; a crash detection is not a
    restart), so a parked scanner reports exactly the retries attempted.
  - On retry-cap exhaustion the scanner parks in FAILED with the server up
    (process is not torn down by a dead scanner); SIGTERM/SIGINT still
    shuts down loop → server → sessions in order.
  - Scanner health IS exposed via `GET /scanner-health` (envelope
    `{ok, data:{status,last_error,restart_count,last_restart_ts}}`) —
    readable by any API consumer with the bearer token. When no health
    source is wired the route answers 503 UNAVAILABLE; it never claims
    health it cannot read.
- Committed: server binds before loop starts; per-phase try/except keeps
  loop alive (`proto/daemon.py:224-323`); process restart via systemd
  (`altradar.service.example:11-12`); single-scanner lock
  (`proto/service.py:1334-1361`).
- Verification: `tests/test_service_lifecycle.py` → ALL PASS (59 checks:
  40 pre-existing + 19 focused resilience checks covering finite-run
  completion, crash → failed-visible-via-API → sweep-demonstrated
  recovery to healthy (restarting explicitly rejected), /healthz 200 +
  /readyz 503 during scanner downtime, retry-cap give-up with server up
  and restart_count == cap, and unwired-server honesty).
  `tests/run_all.py` → ALL SUITES PASS (60 files).
  RED-verified: removing the recovery signal fails the recovery check.
- Commits: 80991bb (resilience implementation), 1e34cd2 (recovery
  reporting fix — sweep-driven healthy + restart-attempt counters).
- Remaining: scan-lag/duration metrics (WS3); nothing structural left in
  WS2.

### 3. Timely MEXC alt-perp updates — IN PROGRESS (lag/duration delivered; 1m deferred by design)
- Deps: WS1 (bar validity), WS2 (survives scan faults) — both complete.
- Committed (6fb0356, hardened after review): sweep-level lag/duration/
  cadence via `/scanner-health` — `sweep_count`, `last_sweep_ts`,
  `last_sweep_duration_s` (measured wall time of the last sweep, on the
  MONOTONIC clock), `observed_sweep_interval_s`, `data_lag_s`. All
  measured from completed sweeps; None until first real observation. The
  daemon loop passes `duration_s` to `on_sweep`; the call shape is
  decided once by signature inspection, so a TypeError raised inside a
  callback is never retried (no double-record).
- Complete (pre-existing): overlap guards (lockfile + sequential loop +
  `max_workers=1` resolver, `proto/daemon.py:195-265`); data age exposed
  (`data_as_of`, `snapshot_history`, `last_bar_ts`); closed-only bars.
- Decision record (`docs/ws3-timeliness-feasibility.md`):
  - Live/1m NOT feasible as-is, with evidence: measured `INTERVALS`
    starts at Min5 (no Min1); 576-coin universe ≈ 29s at the shared
    20 rps cap; 1m would need ~347s/sweep; archives are 300s grids.
    1m is a design stage (venue probe + request budget + archive
    decision), not a flag.
  - Provisional-vs-closed DECIDED: closed-only stays; forming bar
    dropped at ingest; `/bars` reports `closed_only`. No provisional
    tier without per-bar markers and scorer re-validation.
- Remaining: the 15 stale coins are RESOLVED as MEXC delistings (see
  WS1 — no collector defect).
- Delivered (2026-10-15, R2, commit 478489f): scan-phase durations in
  `scan_once()` — `self.last_scan_phases` with `universe_refresh_s`,
  `score_s`, `log_s`, `total_s` (all `>= 0`, monotonic clock, recorded
  on the empty-universe path too; no scoring/logging behavior change).
  Verification: `tests/test_scan_phases.py` → ALL PASS.

### 4. Point-in-time coverage (funding/mark/book/OI) — IN PROGRESS (funding archive delivered)
- Deps: WS1 audit pattern (reused), WS3 timeliness signals.
- Complete (pre-existing): what IS collected is explicit — MEXC klines 5m +
  depth-20 spread + ticker row (funding/current/cap), Bybit OI 1h-pct only
  (`proto/scan.py:417-480`); only 5m OHLCV persisted.
- Delivered (2026-10-15, WS4 first increment): **point-in-time funding
  observation archive** — `proto/funding.py`.
  - Funding rides the bulk ticker row, so ONE extra request per snapshot
    records the whole universe (`collect_funding`), appended on the same
    cadence as the bars collect cycle in the daemon loop (guarded: a
    funding failure never fails the bars sweep).
  - Append-only `<COIN>.csv.gz` under `data/funding/` with
    `ts, rate, cap` — the observation ts is what makes the (otherwise
    mutating) rate series reconstructible point-in-time; `cap` is
    funding's own denominator, recorded beside it.
  - Same contract as the bar archives: sorted on read, deduped by ts,
    never rewritten (hash-verified by test); synthetics excluded with the
    same canon/is_synthetic filter so coin names match the bar archives.
  - Audit reuse: the CLI gained `--tf-sec` so observation archives at any
    cadence audit correctly (default 300 = 5m bars).
- Remaining: funding SETTLEMENT history (settle times + settled amounts —
  needs the per-coin funding endpoint or settlement events, a separate
  design); mark/index archive; book/spread history; OI series; a service
  route to read the funding series (UI evidence is WS6). Only 5m OHLCV +
  funding observations are persisted today.

### 5. Quantitative research foundation — INCREMENT 1 DELIVERED (registry + split discipline)
- Deps: WS1 (PIT truth), WS4 (funding/cost inputs).
- Complete: episode lifecycle + calibration gate + cost stub + explicit
  denominators + no-net-P&L prohibition (`proto/measurement.py`,
  `proto/episode_report.py`); heuristic scores labeled UNVALIDATED,
  shadow score read-only (`proto/scorer.py:97-99,506-528`).
- Delivered (2026-10-15, WS5-1, commits 9d74b29 + this plan):
  `proto/hypothesis_registry.py` (stdlib reader + validator, read-only)
  with `load_registry` / `validate` / `load_and_validate`; split
  discipline enforced (half-open windows, ordering + embargo from the
  hypothesis's own horizon, status-gated required fields); plus the
  illustrative `testing`-status example
  `docs/hypotheses/HYD-0001-funding-quintile-drift.md` (self-consistent
  windows, `>=` threshold with explicit denominator, costs referencing
  the funding archive, pinned rule/config refs).
  Verification: `tests/test_hypotheses.py` → ALL PASS (parse + fixtures
  + shipped example + decision-log wiring); `tests/run_all.py` → ALL
  SUITES PASS (65 files).
- Remaining: everything in `docs/quant-analytics-system-proposal.md`
  §3–§5 beyond the registry (evaluation harness, Hurst/MF-DFA,
  competing outcomes, calibrated probs, clustered uncertainty) is unbuilt.

### 6. Evidence communication in app — NOT STARTED
- Deps: WS5 outputs to display; WS1/WS4 coverage labels.
- Complete: `unavailable`-as-null hygiene across table/detail/cards;
  "silence is valid" empty states; reference-notional wording.
- Remaining: no sample-n / CI / calibration / coverage / validation-status
  / insufficiency-reason display; no research-vs-live distinction.

### 7. Change control (candidate vs validated) — PARTIAL; PROMOTION GATE DELIVERED (evaluation only)
- Deps: WS5 promotion gates (outcome evidence still unbuilt — the gate
  says so explicitly rather than inventing it).
- Complete: per-episode flag/plan/episode/outcome/cost versions +
  `config_hash` + `CODE_REV` (`proto/measurement.py:32-39,100-127`);
  cohort isolation (`proto/episode_report.py:88-111`); no order-execution
  paths anywhere (verified: read-only public fetches only).
- Delivered (2026-10-15): `proto/promotion.py` — a pure
  `evaluate_promotion(stats, candidate)` gate returning
  promote/hold with auditable checks and cited reasons. It evaluates
  ONLY cadence evidence that exists today (calibration pass, cohort
  breadth ≥20 coins, sample ≥200 attempts, majority-not-failed) and
  states `outcome_evidence: "not evaluated (WS5 inputs unbuilt)"` —
  no confidence/probability language (test-enforced). 16 checks in
  `tests/test_promotion.py`.
- Remaining: champion/candidate OBJECTS persisted in the Store,
  decision-log ENTRIES (the log file + validator exist; no decisions
  recorded yet), and outcome-based comparison once WS5 lands
  (competing outcomes, calibrated probabilities — proposal §3–§5).
- Delivered (2026-10-15, R4, commit 47ff908): `proto/decisions.py` —
  `validate_decision` + `validate_log` (stdlib, never writes the log)
  over the human-owned append-only `docs/decisions/log.jsonl`
  (empty file is valid). Wiring to the real registry proven in
  `tests/test_hypotheses.py`: registry ids fed into `validate_log`
  accept `HYD-0001` and reject `HYD-9999` with `unknown-hypothesis`.
  Verification: `tests/test_decisions.py` → ALL PASS.

## Outstanding (NOT in this plan — kept visible)

- WS3 beyond R2/R3: 1m/venue-probe design stage; provisional-bar tier (decided against, needs per-bar markers + scorer re-validation to reopen).
- WS4 beyond funding observations: funding SETTLEMENT history, mark/index archive, book/spread history, OI series, service route for the funding series.
- WS5 beyond increment 1: evaluation harness, estimators, competing outcomes, calibrated probabilities (proposal §3–§5).
- WS6 evidence UI: sample-n / CI / calibration / coverage / validation-status display (blocked on WS5 outputs).
- WS7 beyond the decision log: champion/candidate persistence, outcome-based comparison (blocked on WS5).

## Stage log
|- Stage 1 (WS1, complete): bar-archive audit library + CLI + tests (3 commits:
|  f11b789, 0bd1938, 8073b63). Audit tool delivered AND verification gate
|  passed: run_all.py ALL SUITES PASS (60 files), 113 bar_quality checks.
|- Stage 2 (WS2, complete): scanner resilience — crash retry with capped
|  exponential backoff, `GET /scanner-health` (honest 503 when unwired),
|  finite-run completion never restarted, give-up parks with server up,
|  sweep-driven recovery, restart-attempt counters, explicit ok semantics.
|  Commits 80991bb + 1e34cd2 + af7da4b. run_all.py ALL SUITES PASS (60).
|- Stage B (WS1 findings, complete): audit coverage/age fixed to sorted
|  min/max (c1a490b — the ratio=7.03 "anomaly" was an artifact); all 576
|  archives proven perfect 300s grids; sort-on-read remediation committed
|  (27299bf, archives hash-verified untouched). run_all.py ALL SUITES PASS.
|- Stage C (WS3 begin, complete): sweep duration/cadence/lag via
|  /scanner-health + 1m/provisional decision record (6fb0356).
|  run_all.py ALL SUITES PASS (60).
|- Stage D (WS7 begin, complete): promotion-gate evaluation
|  (proto/promotion.py + 16 checks); WS4/5/6 remain tracked/proposed.
|- Review-fix stage (complete, 41d5352): no TypeError-based callback
|  retry; monotonic-clock sweep duration.
|- Stage E (WS4 begin, complete): point-in-time funding observation
|  archive (proto/funding.py, one tickers() request per snapshot,
|  daemon-wired on the collect cadence, funding failure isolated from
|  the bars sweep); audit CLI --tf-sec for observation cadences.
|  NOTE: no raw archive was ever rewritten in any stage.
|- Stage F (roadmap push 2, complete 2026-10-15): R1 live-service
|  deploy-verification (restart → /scanner-health healthy, funding
|  archive (absent) → 569 files); R2 scan-phase timing (478489f);
|  R3 stale-15 = MEXC delistings, no fix (a1e4734); R4 decision log +
|  validator (47ff908); WS5-1 registry + validator (9d74b29) with
|  illustrative HYD-0001 example + decision-log wiring (this commit).
|  run_all.py ALL SUITES PASS (65 files).

## Handoff (uncommitted / untracked — NOT part of committed stages)

### Working-tree state
- CLEAN of tracked modifications as of 2026-10-15: every change reviewed
  and committed (result semantics af7da4b; WS1 findings c1a490b+27299bf;
  WS3 begin 6fb0356; WS7 begin pending). The previously uncommitted
  `proto/collector.py` sort fix was deliberately reviewed as part of the
  WS1 stage and committed at 27299bf.

### Untracked files
- `docs/handoff-2026-10-15.md` — the standalone handoff document (committed
  at a2f74e7): committed state, invariants, next priorities, pitfalls.
- `docs/roadmap-status.md` — this file (roadmap status; kept untracked).
- `docs/ws3-timeliness-feasibility.md` — WS3 decision record (1m
  feasibility, provisional-vs-closed).
- `docs/superpowers/plans/2026-10-15-bar-archive-audit.md` — the Stage 1
  SDD plan (brief, task specs).
- `.superpowers/sdd/2026-10-15-bar-archive-audit/`, `.superpowers/sdd/2026-10-15-scanner-resilience/`,
  `.superpowers/sdd/2026-10-15-roadmap-push/` — SDD workspaces
  (`progress.md` ledgers, task briefs).
- `proto/promotion.py`, `tests/test_promotion.py` — WS7 promotion gate
  (committed in the Stage D commit).

### Committed state (2026-10-15)
- WS4 funding archive commit — proto/funding.py + tests/test_funding.py +
  daemon wiring + audit --tf-sec (pending sha at ledger time)
- `41d5352` fix(WS3): sweep-duration defects — no TypeError retry,
  monotonic clock
- `502df2e` feat(WS7): promotion-gate evaluation
- `6fb0356` feat(WS3): sweep duration/cadence/lag via /scanner-health;
  1m+provisional decision record
- `27299bf` fix(WS1): read_bars sorts on read — archives never rewritten
- `c1a490b` fix: audit coverage/age use sorted unique min/max
- `af7da4b` feat: explicit run_service result semantics
- `1e34cd2` fix: scanner-health recovery reporting
- `80991bb` feat: scanner resilience
- Stage 1 audit: `8073b63`, `0bd1938`, `f11b789` (carried forward, untouched)

### Next priorities by workstream
- WS4 (point-in-time coverage): funding/mark/book/OI archives — R1 has
  now verified the funding observation archive IS accumulating live
  (569 files); still NOT STARTED as archives: funding SETTLEMENT
  history, mark/index, book/spread history, OI series, service route
  for the funding series. The audit pattern (`proto/bar_quality.py`) is
  directly reusable for new archives once collection exists.
- WS5 (research foundation): hypothesis registry DELIVERED (this plan);
  still NOT STARTED: evaluation harness, purge/embargo splits usage,
  competing outcomes, calibrated probabilities (proposal §3–§5) — NOT
  STARTED. The promotion gate already states its outcome inputs are
  unbuilt rather than inventing them.
- WS6 (evidence-focused UI): sample-n / CI / calibration / coverage /
  validation-status display — NOT STARTED.
- WS7: decision log file + validator DELIVERED (empty log awaits first
  entry); still pending: champion/candidate objects + outcome-based
  comparison (needs WS5) — evaluation delivered, persistence pending.
- WS3: 1m design stage (venue probe + request budget + archive
  decision); stale-coins uptime look.
- The overall quantitative research system is NOT complete: WS4–WS6
  remain, and WS5/WS7 need outcome evidence.
