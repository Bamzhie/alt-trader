# ALT RADAR Roadmap Status

Product goal: quantitative research and analysis for MEXC altcoin perpetual
futures. Rules support quant analysis; heuristic scores are not evidence of
edge. Read-only; no order execution. BTC is labeled market context only.

Last updated: 2026-10-20 (six research/startup workstreams implemented and
reviewed, the pivot feature-provenance fix merged, and the merged `main`
**DEPLOYED** to the local user systemd service under owner authorization —
service now runs `c8647b5`; reports in
`docs/implementation-reports/quant-research-2026-10-19/`, deployment in
`deployment-report.md`).
Earlier (2026-10-19): dev-only investigation of the all-negative WS5 result —
friction, not a signal defect; three independent audits; four pipeline and
five funding-path fixes (including a transition-aware settlement schedule)
with corrected funding-adjusted numbers; the gross-outcome SPA is
exploratory; per-coin settlement-backfill manifest reconciled against the
venue; the WS5 evaluation report records the selected strategy's identity.
(That line describes the 2026-10-19 state; the service was deployed and
restarted on 2026-10-20 — see "Current deployment state" below.)
Verified against code/data/live probes/tests, not inherited summaries.

## Research data readiness snapshot (2026-10-18, corrected)

VERIFIED by direct archive read (2026-10-18):

- `data/bars/`: **578** coin archives (incl. BTC). **Correction:** the
  earlier "~16.41 days / no coin ≥91 days" claim was the classic
  unsorted-file first/last-line artifact that WS1 already fixed in code —
  the bar files are batch-appended and are NOT chronologically ordered, so
  span must be taken from **sorted unique min/max**. Measured this way:
  **523 of 578 coins have ≥91 days** of 5m bars. BTC has ≈28.1k unique bars
  spanning **≈97.5 days** (counts grow with live collection; measured at
  this snapshot); the universe maximum is ≈97.5 days. Span
  distribution: ≥91d **523**; 79–91d **3** (CATE 83.3, GRVT 80.1,
  MARSCOIN 80.0); 63–79d **4**; 30–63d **24**; <30d **24**. Zero duplicate
  timestamps, zero invalid rows; the perfect-300s-grid invariant (WS1)
  still holds. **The default WS5 dev→val→holdout split (~91 days) is
  therefore satisfiable for the great majority of the universe.**
- `data/funding_settlements/`: **559** coin archives, 1,366,260 rows,
  header `settle_ts,settled_rate,cycle_hours`; per-coin spans up to ~540
  days; venue cycles 1h/2h/4h/8h (some coins mixed). Backfilled
  2026-10-18. (The earlier snapshot's "absent" claim is superseded.)
- `data/funding/`: observations for 571 coins, ~2 days each
  (`ts,rate,cap`) — observations, not settlement history.
- `data/quant_research/`: absent. The harness writes its report to its own
  `--out` directory (CLI default `data/quant_research`, an output dir, never
  a bar/funding/settlement archive). No production archive is rewritten.
- Next evidence step: the WS5 dev→val harness has been run on the current
  bars with settlement-derived funding costs; **the one-shot holdout
  remains SEALED** pending review of coverage, dev/validation results and
  the frozen evaluation setup (see WS5 below).

## Current deployment state (verified live 2026-10-20)

- **Deployed 2026-10-20 (owner-authorized).** The local user systemd
  service was restarted with a clean tree at the deployed commit
  **`c8647b5`** (the only later commit is documentation). The running
  service's `provenance.code_rev` reads the *current* git HEAD at request
  time, so it tracks HEAD rather than the loaded revision; the proof that
  the new code is executing is behavioural (the `first_sweep` block, the
  per-stage `stages`, the research route, the three new runtime
  artifacts). New pid
  **732289**, started 2026-10-20 11:32:58 WAT, one loopback listener on
  127.0.0.1:18741. The unit file and the operator's drop-in
  (`--quant-shadow`, `TimeoutStopSec=600s`) are **byte-identical** to
  before; the stop grace was not modified.
- **API readiness is now decoupled from the first sweep.** `/healthz` and
  `/readyz` answered **1 second** after the start; `/scanner-health`
  reported `first_sweep.state = "in_progress"` with `completed_ts: null`
  while the sweep was still running, and `complete` with a measured
  **1 959 s (32.7 min)** afterwards — with the new per-stage attribution
  (`scan=449.7(partial) collect=438.4 funding=9.0 settlements=0.0(not_due)
  prices=0.0(not_due) resolve=0.0(skipped) measure=450.1(partial)
  quant=611.8`). The pre-deploy first sweep took 3 951 s.
- **Boundaries intact after the deploy:** `mode:"shadow"`,
  `actionable_events: 0`, `promotions: []`, `docs/decisions/log.jsonl`
  still 0 bytes, no `docs/decisions/chain.jsonl`, **no
  `data/quant_research/`** (the one-shot holdout was never evaluated), and
  no production archive was rewritten (the collector appends only new
  bars). The quant watcher is auto-paused (`BTC bars stale` — the venue's
  klines lag ~12 min behind the collect; the 2026-10-19 suppression rows
  carry the same reason, so it is pre-existing behaviour, not a
  regression).
- **Pivot feature-provenance fix (2026-10-20, `c8647b5`):** the feature
  store's identity is now the measure version **plus the configuration
  identity** (`pivots-v1+L2R2F1`), `from_pivots()` requires and
  cross-checks the pivot configuration against the pivots themselves, and
  a same-key write with different content is **refused** instead of
  silently ignored. The store has no production caller and no stored
  data, so nothing was affected. 18 new regression checks; full offline
  suite ALL SUITES PASS (87 files).
- Full evidence: `docs/implementation-reports/quant-research-2026-10-19/deployment-report.md`.
- **Deploying the code is not evidence of an edge.** The WS5 evaluation
  still found no evidence of a positive edge, the gross-outcome SPA is
  still exploratory, and H1 is still a candidate for preregistration whose
  ~15 bp break-even has no supporting execution-cost evidence here.

## Deployment state before 2026-10-20 (verified live 2026-10-17)

- Running service: systemd user unit `altradar.service`, **active**, pid
  715, `ActiveEnterTimestamp` 2026-10-16 15:41 WAT, exactly one loopback
  listener on 127.0.0.1:18741. It was **NOT** restarted for any of the
  2026-10-17 commits, and the owner has not authorized a restart.
- Live routes verified by read-only authenticated probe (2026-10-17):
  `/healthz` 200, `/readyz` 200 (`ready: true`), `/scanner-health` 200
  (`status: healthy`, sweep_count 17), `/funding?coin=BTC` 200 (ascending
  rows).
- **Deployed and live-verified:** `/funding` (WS4e, commit 858201c,
  2026-10-16 00:53) — the 2026-10-16 15:41 restart is AFTER that commit,
  so the funding route and `/scanner-health` are live.
- **Code delivered, DEPLOY PENDING** (the running process predates the
  commits; a restart waits on owner go-ahead because it recycles the 24/7
  collector):
  - `/quant-status` + `quant.shadow` SSE (WS5, commits e80c07c/139bcac,
    2026-10-16 22:07+) → live probe returns
    `404 no such route: /quant-status`.
  - `/settlements` (WS4 settlement history, commits 90a568a/2184f87/
    48ef382, 2026-10-17) → live probe returns
    `404 no such route: /settlements`.
  - `/prices` (WS4 mark/index price history, commits ce97d5f + 6d27543,
    2026-10-17) → not present in the running process.
  - Re-verified 2026-10-18 (read-only authenticated probe, same pid 715):
    `/quant-status`, `/settlements`, `/prices` all return
    `404 no such route`; `/funding` returns 200. The deploy gap is
    unchanged and still waits on owner approval.
- Restart + live-verification is therefore a single explicit operator
  action awaiting approval (see "Deployment / live-verification steps").

### Historical (superseded) deployment-gap record

- Pre-restart (verified 2026-10-15 ~22:05 UTC): the then-running backend
  (ActiveEnterTimestamp 2026-10-15 11:09) predated that day's commits;
  `GET /scanner-health` returned `404 NOT_FOUND` and `data/funding/` did
  not exist, although both were committed. Cause was a stale process, not
  a code bug.
- Resolved (R1, 2026-10-15 post-restart to pid 200835): `/scanner-health`
  answered 200 healthy and the funding archive grew from absent to 569
  files (header `ts,rate,cap`). A later restart (2026-10-16 15:41, pid
  715) superseded that process and included the `/funding` route. The old
  process needed SIGKILL after SIGTERM timed out (mid-measure-cycle);
  pre-existing shutdown behavior.

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
- Remaining: nothing structural left in WS2 (sweep-level lag/duration
  delivered WS3 6fb0356; scan-phase timing delivered R2 478489f).

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
- Delivered (2026-10-16, WS4e, commit 858201c): **service route for the
  funding series** — `GET /funding` over the observation archive
  (`proto/service.py` handler + dispatch, `funding_dir` threaded through
  `_make_handler`/`serve`/`run_service`/`--funding-dir`, the same
  configuration surface that sets the bars dir). Contract mirrors
  `/bars`: bearer gate → `coin` required → allowlist (`^[A-Za-z0-9_-]+$`,
  before any filesystem access) → 404 when unarchived → limit 1–5000
  (never clamped) → 503 on unreadable (never a stack trace); ascending
  rows, `count == len(rows)`, `data_as_of == last_ts`.
  - Verification: `tests/test_funding_route.py` → ALL PASS (39 checks);
    `tests/run_all.py` → ALL SUITES PASS (66 files).
  - Deploy status: **LIVE-VERIFIED (2026-10-17).** The 2026-10-16 15:41
    restart (pid 715) is AFTER commit 858201c (2026-10-16 00:53), and an
    authenticated loopback probe returns 200 with ascending rows and
    `count == len(rows)`. (The historical note that pid 200835, 2026-10-15
    22:59, predated the route is superseded by that later restart.)
- Delivered (2026-10-17, WS4 mark/index price history): fair/mark and
  index hourly candles via public
  `GET /kline/{fair_price|index_price}/{symbol}`
  (`proto/prices.py`: `parse_candles/collect_coin/backfill/collect_recent`
  + CLI, idempotent by ts; `proto/ratelimit.py`
  `RollingRateLimiter(20, 2.0)` — the endpoint's documented 20 req/2s
  cap, NOT scan.LIMITER); append-only
  `data/prices/{mark|index}/<COIN>.csv.gz`
  (`ts,o,h,l,c`); guarded daily daemon hook; read-only `GET /prices`
  mirroring `/settlements` (auth, limits, 404/503/400s, exact keys);
  `bar_quality --tf-sec 3600` audits the hourly grid. Only CLOSED hourly
  candles stored (forming hour dropped at parse); `vol`/`amount` absent
  (venue returns 0.0 — not a measurement); `real*` columns not archived
  (undocumented). Operator reference in
  `docs/ws4-mark-index-history.md`; spec in
  `docs/superpowers/specs/2026-10-17-mark-index-history-design.md`.
- Remaining (WS4 market-data history): book/spread history, OI series
  (both OPEN-BLOCKED — no public history endpoint, see availability
  finding below); DEPLOY + live-verify the code-delivered read routes
  (`/settlements`, `/prices`, `/quant-status`). `/funding` is
  LIVE-verified.
- **Availability finding (verified against official MEXC contract docs,
  2026-10-17):** public *history* endpoints DO exist for the INDEX PRICE
  kline (`GET /api/v1/contract/kline/index_price/{symbol}`) and the FAIR
  (mark) PRICE kline (`GET /api/v1/contract/kline/fair_price/{symbol}`) —
  both public, columnar OHLC, 2000-candle cap per request, slow-capable
  to `Day1`. There is **NO public historical OI endpoint** (open interest
  appears only as the current `holdVol` snapshot on `/ticker`) and **NO
  public historical order-book/spread endpoint** (only current `/depth`
  and the live `/depth_commits` snapshot). OI-series and book/spread
  archives therefore cannot be built from public venue data without
  fabricating history; both stay OPEN-BLOCKED (see Outstanding).
- Persisted today: 5m OHLCV bars, funding observations, venue-reported
  funding settlements (filling on the first backfill/collect after deploy),
  and mark/index price history (filling on the first backfill/collect after
  deploy).
- Delivered (2026-10-17, WS4 settlement history): venue-reported
  settled rates via public `/funding_rate/history`
  (`proto/settlements.py`: `fetch/parse/collect/backfill/collect_recent`
  + CLI, idempotent by settle_ts, malformed rows counted never
  zero-filled); append-only `data/funding_settlements/<COIN>.csv.gz`
  (`settle_ts,settled_rate,cycle_hours`); guarded daemon hook with 24h
  throttle (never fails the sweep); read-only `GET /settlements`
  mirroring `/funding` (auth, limits, 404/503/400s, exact keys);
  `bar_quality` audits the 8h grid via `ts_field="settle_ts"`. No
  estimation anywhere (no rate_applied, no interpolation, no filled
  gaps); not fed into scoring, setups, costs, gates, or quant emission.
  Full spec in `docs/superpowers/specs/2026-10-17-funding-settlement-design.md`,
  operator reference in `docs/ws4-settlement-history.md`.
- Audited (2026-10-19): `proto.quant.settlement_audit` rebuilds a
  read-only per-coin manifest and reconciles it against the venue's own
  `totalCount`. Outcomes over the 579-coin bar universe at capture
  (`observed_at 1792396320`; grew from 578 when `PAPER` listed): **544
  complete, 7 truncated, 28 missing_archive, 0 failed_or_error**. The 551
  present archives hold 1,351,943 rows (0 malformed, 0 duplicate). The 7
  truncated (FLOCK, LA, LAYER, MEGA, MYX, SIGN, XTZ) sit on an exact
  1000-row page boundary with a venue shortfall the elapsed live tail
  cannot explain (6,432 rows the venue still serves; 6,299 beyond the
  tail). The 28 missing are recorded as venue state, not a cause: **27**
  are absent from the venue's current ticker list — hence outside the
  ticker-derived backfill universe — although the venue still serves
  history for all of them (probe `totalCount` 72–7468); the **28th**
  (`PAPER`) is already in the current tickers, so its archive holds only
  the rows served since it listed. No delisting is asserted. The original
  backfill's per-coin API errors were never persisted and are
  unrecoverable; the reconciliation probe returned 0 errors. Predecessor:
  the aggregate audit (559 files / 1,366,260 rows / 0 dup / 0 malformed).

### 5. Quantitative research foundation — SHADOW PATH DELIVERED (engine + hook + status/SSE + Research view + e2e)
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
- Delivered (2026-10-16, WS5 shadow path, commits 2c85807, deba410,
  e80c07c, 139bcac, 44df80f + task-5 docs):
  vendored `proto/quant` research-and-shadow engine (16 files,
  byte-identical copies, + vendored tests + engine docs); guarded
  `--quant-shadow` daemon hook (lazy init, per-sweep step after the
  measure block, never touches sweep state, never raises — `quant FAILED`
  is logged); service forwards the quant flags into the embedded loop
  argv (`_daemon_argv_for`, so route and loop agree on the store);
  read-only `GET /quant-status` (200 with unseen/healthy/paused states,
  empty store → explicit zeros + `insufficient-evidence`, 503 only when
  quant.db itself is unreadable) + counts-only `quant.shadow` SSE kind on
  the unchanged ring/`Last-Event-ID`/`resync` machinery; Research view on
  the Analysis page (SHADOW banner, counts, suppression rows,
  measured-range promotions, no setup-table mixing, banned-word clean);
  `docs/quant-integration.md` (architecture, commands, data flow,
  limitations, registry relationship note).
  - Engine verdict stands: no rule has passed promotion; every live
    output is SHADOW / not actionable / insufficient-evidence. A
    `gates` pass for an exact rule id + config hash is recorded as a
    research fact — it does NOT switch live emission (shadow-only
    contract; ACTIONABLE emission needs a separately approved stage).
  - Verification: `tests/run_all.py` → ALL SUITES PASS (71 files);
    `npm run test` 185 pass + typecheck + lint clean + `npm run build`
    clean; bounded tmp-only e2e (fixture bars → research dev/val n>0 +
    denominators + holdout unevaluated → `watch --once` replay silent +
    persistent streaming SHADOW rows with `mode='shadow'` → report /
    status → embedded-loop test service → `/quant-status` counts match db
    → `quant.shadow` frame with matching counts → below-floor `resync` →
    separation proof: zero quant tables in signals db, production `data/`
    append-only-or-untouched with no quant artifacts).
- Remaining: WS5 becomes live-data evaluation evidence (engine on
  production bars with funding-archive costs), a calibration record, and
  broader WS6 display (sample-n / CI / calibration / coverage /
  validation-status beyond the Research view's current
  counts+suppression+measured-range). Everything in
  `docs/quant-analytics-system-proposal.md` §3–§5 beyond the registry and
  the shadow path (evaluation harness, Hurst/MF-DFA, competing outcomes,
  calibrated probs, clustered uncertainty) is unbuilt.
  - **Delivered (2026-10-20, WS-A/B/C/D/E):** the research capabilities
    that section listed are now built and reviewed (report:
    `docs/implementation-reports/quant-research-2026-10-19/final-report.md`).
    **WS-A** `proto/quant/{structure,estimators,mfdfa,measures,feature_store}.py` —
    multi-timeframe structure with **confirmed pivots** (frame-aware
    `confirmed_ts`), rolling R/S + generalized Hurst and MF-DFA with
    documented assumptions, an uncertainty band and an `inputs_hash`, and a
    **point-in-time** feature store whose as-of join returns `None` + a
    reason rather than an imputed value. **WS-B**
    `proto/quant/{competing_outcomes,inference,family_test}.py` — competing
    outcomes (stop / target / time exit / thesis invalidation / true
    censoring) with friction, funding and net **separately attributable**,
    chronological probability + calibration, day- **and** coin-cluster
    uncertainty, three baselines and family-level multiple testing.
    **WS-C** `proto/quant/{prereg,research_report}.py` — the preregistration
    record (feature definitions, point-in-time membership, event rules,
    costs, splits, embargo, statistics, promotion criteria, config/data/code
    hashes, an append-only variant registry that records failures) and the
    sliced report; the completed 288-config evaluation is **registered**
    (truthfully quoted, never re-run) as `HYD-0002`. **WS-D** the read-only
    `GET /quant-research` route and the expanded Research view. **WS-E** the
    champion/candidate registry, the matched comparison and the hash-chained
    decision log. All six workstreams were implemented in isolated worktrees
    by independent implementers, each reviewed by a fresh reviewer, and
    integrated in dependency order; the full offline suite is **ALL SUITES
    PASS (87 files)** and the frontend gates (200 tests, typecheck, lint,
    build) are clean. Still **unbuilt**: anything that turns a measurement
    into a trade — the calibration record, the live-data evaluation of the
    new features, and the 1m/venue design stage.
  - Delivered (2026-10-17, WS5 evaluation harness; corrected same day):
  `proto/quant/cost_attribution.py` — funding cost attribution from the
  WS4 archives at the venue's OWN settle boundaries. Venue-reported
  settlement rows (`settle_ts`/`settled_rate`) are the funding events and
  are used when they cover every boundary inside a holding window; the
  observation archive (`data/funding/`) is a documented fallback for
  observation-only periods — each boundary takes the last snapshot in
  `[b - cycle, b)`, counted once, NEVER summed per snapshot. The fallback
  maps snapshots to the VENUE grid (phase/cycle inferred from the
  settlement archive), so an off-epoch venue still maps to its real
  boundaries; only a coin with NO settlement archive at all assumes the
  epoch-aligned 8h UTC grid, and a settlement archive without a usable
  `cycle_hours` yields an uninferable grid. Windows whose
  funding events cannot all be attributed — a missing settlement row, an
  uncovered boundary cycle, or an uninferable venue grid — are
  `incomplete`: excluded from
  funding-adjusted metrics and counted, never zero-filled or estimated. A
  coin with no archive rows at all is `missing`. The position side comes
  from the SELECTED rule's policy (`fade` inverts the break, `cont` keeps
  it) and drives the funding sign, the exit-offset slot (funding charged
  only through the actual exit timestamp; TIME exits use the horizon), and
  the coverage accounting. The cost is additive to friction in
  `barrier.net_r`. Reproducible via `config_hash(funding_dir,
  settlements_dir, cfg)` (SHA16 of the quant config hash + archive paths +
  settle-seconds + boundary tolerance).
  `proto/quant/eval_harness.py` + CLI `python3 -m proto.quant evaluate` —
  runs `research.collect_events` → `run_stage_dev_val` → one-shot holdout
  with the dev/val/holdout splits and embargo from `config.Splits`.
  Selection stays friction-only (fees+slippage); the report adds
  funding-adjusted dev/val/holdout/2x-slip metrics for the selected config
  with EXPLICIT denominators (`n_events`, `n_funding_adjusted`,
  `n_excluded_missing_coin`, `n_excluded_incomplete`, `coverage`) — a
  funding-adjusted metric with nothing attributable is null, never a
  friction-only number under a funding label. Holdout once-only via the
  `holdout_evaluated.json` lock (second attempt refused gracefully,
  including the funding-adjusted fields); explicit `check_no_leakage`
  asserts no event window crosses a split boundary. Reports carry
  config_hash, cost_hash, git_rev, all-coin bar digests (no cap),
  funding/settlement archive digests for every contributing coin, and the
  list of coins with missing cost data. Output goes to a temp/`--out` dir
  only — production `data/` is never written, the service is never
  restarted, no quant.db is created. Also fixed a latent holdout crash:
  the one-shot path re-summarized an already-summarized holdout, so any
  real holdout spend would have failed. Verification:
  `tests/test_ws5_eval_harness.py` → ALL PASS (137 checks covering
  settlement-boundary attribution, snapshot-vs-boundary mapping,
  off-epoch venue-grid fallback / uninferable-grid exclusion, sign
  correctness, actual-exit charging, fade-policy side regression,
  funding-adjusted vs friction-only divergence, no-funding null metrics,
  missing-boundary exclusion+count, holdout-once, splits, leakage, archive
  digests, and no-production-write guarantees); the 2026-10-19 audit
  regressions are covered by `tests/test_quant_pipeline_regressions.py`
  (run-boundary path, union span, CUSUM reset, sparse-day guard) and
  `tests/test_quant_funding_regressions.py` (window-local settle grid,
  matched funding denominator, TIME-exit close, 289-bar leakage bound, and
  cycle transitions 8h→4h / 4h→8h including the observation fallback);
  full offline backend suite `tests/run_all.py` → ALL SUITES PASS (81
  files). The venue-network suite
  `tests/test_app.py` is excluded from run_all and timed out in this
  environment (unverified here).
  - Added (2026-10-19): the evaluation report now persists the SELECTION
  IDENTITY (`selected`: rule id, geometry key, resolved `RuleParams` and
  `Geometry`, geom index) plus `n_configs`, `spa` (multiplicity-corrected
  max-t), `dev_config_means` and `top5_dev`, so `evaluation.json` names the
  strategy it evaluated instead of reporting metrics for an unnamed config.
  Regression: `tests/test_ws5_eval_harness.py` case 7 (insufficient-data
  fallback) and case 20 (real selection).
  - Investigated (2026-10-19, dev-only): why all 288 configs had negative
    dev means and the selected config lost on val. Per-event decomposition
    `net_R = gross_R − drag_R` over the canonical cached event set shows the
    gross distribution is centred on zero (median +0.0012R; 150/288 configs
    positive; selected dev gross +0.0040, t +0.27; val gross −0.0015,
    t −0.09), while drag follows `cost/(k·σ·√T)` (median 0.1796R at
    T48_ks1, 0.0244R at T288_ks3; ratio 7.36 vs 7.35 predicted), so the
    negative net means are friction, not a sign/label/metric defect. A
    gross-outcome SPA finds a pre-cost short-horizon reversal
    (`residual_shock[cusum_h=5]/fade`, T48_ks1.5_R3): +0.052R/trade
    (t +3.71, p = 0.005), positive at 36/36 geometries and 5/5 sub-periods,
    but drag is 0.1198R, so net is −0.068R and negative at every geometry.
    That gross SPA is **exploratory**: the outcome metric and the analysis
    were chosen after reviewing the net results, so the multiplicity
    correction (over the 288 configs) does not cover that choice.
    Coin and sub-period splits are in the report (§4). Independent read-only
    audits: signal/event SOUND; metrics/statistics SOUND; cost/split found
    five funding-path defects (whole-archive settle grid, single-grid
    inference across cycle transitions, one-bar-early TIME exit, unmatched
    funding denominator, 288-bar leakage bound), all fixed TDD in
    `tests/test_quant_funding_regressions.py`. MEXC's per-row `collectCycle`
    was verified against the archive (the gap to the next row equals the
    current row's cycle in 1,365,207 of the 1,365,701 rows that have a
    following row; the 494 exceptions include cycle transitions and
    same-cycle gaps, so they are not all transition bridges. A separate
    audit found 512 cycle-label transitions, with 23 bridges longer than
    the new cycle treated as unverifiable. The schedule is now a sequence of per-regime grids, so a
    shortening transition (8h→4h) charges its intermediate settlements, a
    lengthening transition (4h→8h) does not demand the settlement the
    venue skipped, and an unestablishable schedule is reported incomplete
    rather than silently omitting a settlement. 219 of 24,749 dev and 99 of
    6,495 val holding windows cross a transition (1,602 of 197,724 across
    all 8 rules) and **all 318 are attributed**.
    Corrected funding-adjusted dev/val means are −0.01585 / −0.01745
    (coverage 0.9995 / 0.9974); funding is a small cost (−0.0005R dev) on
    the matched set, not a credit. The friction-only selection and no-edge
    conclusion are unchanged.
    Recommendation: do not promote and do not spend the holdout. H1
    (short-horizon reversal) is a **candidate for preregistration, not a
    confirmed edge**: its break-even needs round-trip friction ≤ ~0.052R
    (≈15 bp) and no execution-cost evidence here shows that is reachable
    (the 5 bp/side taker constant is an unconfirmed placeholder, slippage is
    assumed rather than measured, and there are no fill/depth/latency
    data) — an operator-confirmed fee tier and a measured execution-cost
    model must come first. H2 (long-horizon breakout) and H3 (funding
    carry) are rejected as standalone edges. Evidence + hashes: report §11;
    diagnostics in `docs/implementation-reports/quant-roadmap-2026-10-18/`.

### 6. Evidence communication in app — PARTIAL (Research view delivered; broader evidence display not started)
- Deps: WS5 outputs to display; WS1/WS4 coverage labels.
- Complete: `unavailable`-as-null hygiene across table/detail/cards;
  "silence is valid" empty states; reference-notional wording.
- Delivered (2026-10-16, WS5 shadow path, commits 44df80f etc.): the
  Research view on the Analysis page — SHADOW banner, event counts,
  suppression rows, measured-range promotions, and an explicit
  insufficient-evidence state, with no setup-table mixing and banned-word
  hygiene. It renders quant shadow counts only; it never labels shadow
  output as actionable.
- Delivered (2026-10-20, WS-D): the Research view now shows **sample counts
  with denominators**, **interval endpoints only** (never a point reading),
  calibration / coverage / validation status, feature provenance
  (`config_hash`, `code_rev`, rule versions) and **insufficient-evidence
  states with reasons**, fed by a new read-only `GET /quant-research` route
  (`proto/quant/research_surface.py`) that reuses the existing
  `report.py` statistics. A gate-passed rule still renders as research, never
  as actionable; calibration is honestly `unavailable` (the shadow store
  records outcomes, not predicted outcomes). Frontend gates: 200 tests /
  14 files, typecheck, lint (0 warnings) and build all clean.
- Remaining: representation of the new mark/index or settlement archives in
  the view; anything beyond the Research view itself.

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
- Delivered (2026-10-20, WS-E): `proto/quant/champion.py` — a **versioned**
  champion/candidate registry (insert-only; a status change appends a new
  version) and a **matched** comparison that evaluates both configurations
  on the **same events** with a **shared** cost model, reporting the paired
  day- and coin-cluster intervals of the difference. `same_events` and the
  cost-model identity are **verified**, not assumed; a mismatch is refused
  with a reason. `proto/quant/decision_log.py` — an immutable,
  **hash-chained** decision log (`docs/decisions/chain.jsonl`, a NEW
  artifact beside the human-owned one) whose `append` re-hashes every cited
  evidence file at write time and refuses a mismatching or missing one;
  `validate_chain` detects an edited entry, a broken link, a duplicate id, a
  non-monotonic timestamp and an unknown kind. **Nothing was promoted:**
  `docs/decisions/log.jsonl` is still 0 bytes, `chain.jsonl` does not exist
  in the repository, no registry file exists in the repository, and
  `set_champion` needs an explicit `allow_promote` opt-in. 138 checks in
  `tests/test_ws_e_champion.py`.

## Outstanding (NOT in this plan — kept visible)

- WS3 beyond R2/R3: 1m/venue-probe design stage (read-only record
  exists); provisional-bar tier (decided against, needs per-bar markers +
  scorer re-validation to reopen).
- WS4 beyond delivered work: **OPEN-BLOCKED (public venue data does not
  exist)** — OI history (only a current `holdVol` snapshot on `/ticker`)
  and book/spread history (only current `/depth` and `/depth_commits`
  snapshot); **DEPLOY PENDING** — `/settlements` + `/prices` + `/quant-status`
  routes live-verify (code delivered; running service predates the commits).
  `/funding` is live-verified. Mark/index price history is **DELIVERED in
  code** with operator reference `docs/ws4-mark-index-history.md`; deploy
  pending.
- WS5 beyond delivered work: the evaluation harness **HAS now been run** on
  the current ~97.5-day bar snapshot with venue settlement costs
  (`python3 -m proto.quant evaluate`, no `--holdout`, `EXIT=0`). Canonical
  run `git_rev da5f43c`; output to a temp `--out` dir, with the report and
  preserved artifacts in `docs/implementation-reports/quant-roadmap-2026-10-18/`.
  **The evaluated strategy:** rule `breakout_start[rvol>=4]/cont`, geometry
  `T288_ks3_R2` (24 h, k_stop 3.0, r_tp 2.0), chosen by a
  multiplicity-corrected max-t (`spa_max_t`) over **288** pre-declared
  configs. The winning dev day-cluster t is **−1.06**, the corrected SPA
  **p-value is 1.0**, and **0/288** configs had a positive dev mean.
  Universe **dev** n=24,749 mean_r −0.0154 CI[−0.0417,+0.0142] and **val**
  n=6,495 mean_r −0.0166 CI[−0.0468,+0.0174] (95% **day-cluster bootstrap**
  intervals) — both straddle zero with negative point estimates: **the run
  found no evidence of a positive edge**. Current transition-aware
  funding-adjusted metrics are reported separately on matched denominators:
  dev 24,736/24,749 (5 missing-coin + 8 incomplete; coverage 0.9995),
  mean_r −0.01585, 95% day-cluster CI[−0.04191,+0.01339]; val
  6,478/6,495 (17 incomplete; coverage 0.9974), mean_r −0.01745, CI
  [−0.04736,+0.01666]. Funding deltas are −0.00051R / −0.00113R on the
  matched sets. The initial pre-transition-audit figures are historical and
  superseded by these results.
  `missing_cost_coins=[1000BTT,RON,ZBCN]`, no leakage, all holdout keys
  `null`. **The one-shot holdout remains SEALED.** Still owned (not started, evidence-gated): a frozen-setup
  decision to spend the holdout once; a calibration record; estimators
  (Hurst/MF-DFA), competing outcomes (stop/tp/time/invalidation/censor per
  proposal §3.4), calibrated probabilities (proposal §3–§5). No rule has
  passed promotion; all outputs are SHADOW/read-only.
- WS6 evidence UI: sample-n / CI / calibration / coverage /
  validation-status display (blocked on WS5 outputs).
- WS7 beyond the decision log: champion/candidate persistence,
  outcome-based comparison (blocked on WS5).

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
|- Stage G (WS5 shadow path, complete 2026-10-16): vendored
|  proto/quant research-and-shadow engine + guarded --quant-shadow daemon
|  hook + read-only GET /quant-status + quant.shadow SSE + Research view
|  (commits 2c85807, deba410, e80c07c, 139bcac, 44df80f, 68bfd5a).
|  Shadow-only promotion contract: a gates pass is a research fact and
|  never switches live emission (b5ec5f8 + docstring/count corrections
|  e442ad9). run_all.py ALL SUITES PASS (71 files); frontend 185 tests +
|  typecheck + lint + build green. Deploy PENDING (live service predates
|  e80c07c).
|- Stage H (WS4 settlement history, complete in code 2026-10-17):
|  venue-reported settled rates via public /funding_rate/history
|  (proto/settlements.py + mexc.funding_history); append-only
|  data/funding_settlements/<COIN>.csv.gz
|  (settle_ts,settled_rate,cycle_hours); guarded daily daemon hook;
|  read-only GET /settlements (commits 55f96b7, 237c7e0, 90a568a,
|  cbb1351); auditable settle grid via bar_quality ts_field (cbb1351);
|  spec/operator doc (2ee0438); post-review fixes — shared settlements
|  dir, currentPage check, dedicated 20-req/2s limiter, read-path
|  finiteness (2184f87) and correct history-endpoint limiter + data-dir
|  default alignment (48ef382). No estimation anywhere; not fed into
|  scoring/costs/gates/emission. Deploy PENDING.
|  NOTE: no raw archive was ever rewritten in any stage.
|- Stage I (WS4 mark/index, complete in code 2026-10-17): hourly mark (fair)
|  and index candles via public kline endpoints (proto/prices.py +
|  proto/ratelimit.py RollingRateLimiter(20, 2.0), matching the documented
|  20 req/2s cap, NOT scan.LIMITER); append-only
|  data/prices/{mark|index}/<COIN>.csv.gz (ts,o,h,l,c, closed candles only,
|  vol/amount deliberately absent); guarded daily daemon hook; read-only
|  GET /prices mirroring /settlements; bar_quality --tf-sec 3600 audits the
|  hourly grid; CLI --ts-field flag added so the settlement grid audit
|  works from the CLI too. Tests: test_prices.py + test_prices_api.py
|  (52 checks). No estimation; not fed into scoring/costs/gates/emission.
|  Operator reference docs/ws4-mark-index-history.md. Deploy PENDING (live
|  service not restarted). NOTE: no raw archive was ever rewritten.
|- Stage J (WS5 evaluation harness, complete 2026-10-17):
|  proto/quant/cost_attribution.py (funding cost from WS4 archives,
|  missing-not-zero, reproduction hash, position-sign aware: positive rates
|  are costs for longs and credits for shorts) + proto/quant/eval_harness.py
|  (research splits + leakage check + holdout-once + reproducibility
|  metadata on production data); tests/test_ws5_eval_harness.py
|  (120 checks: cost attribution, missing-data, sign correctness, early-exit
|  exit-timestamp charging, splits, leakage, holdout lock, archive digests
|  for every coin, incomplete-boundary exclusion, venue-grid phase fallback,
|  unknown-grid exclusion, no-production-write).
|  CLI: python3 -m proto.quant evaluate. All output to temp/ --data/
|  append-only-or-untouched, service NOT restarted.
|  Fixes (review round): (1) funding cost applies trade side from the
|  barrier labeler — longs pay positive rates, shorts receive them;
|  (2) funding charged only through the event's actual exit timestamp
|  (TP/SL exit early via retained exit_offset); (3) holdout one-shot lock
|  enforced with graceful refusal on second attempt; (4) archive digests
|  for every contributing coin (no cap); (5) incomplete boundary coverage
|  excluded from funding-adjusted metrics with affected counts reported.
|- Stage K (roadmap completion, 2026-10-18): (1) Data-readiness docs
|  corrected — the committed `17e9a74` snapshot was a mid-backfill read of
|  batch-appended, non-chronological archives. By sorted unique min/max:
|  523/578 coins have ≥91 d of 5m bars (BTC ≈97.45 d; median 97.44 d), and
|  data/funding_settlements/ holds 559 archives / 1,366,260 venue settle
|  rows. Confirmed independently (578-file pass + proto.bar_quality --all:
|  0 gaps/dups/invalid, ratio 1.0). Commit faa7583.
|  (2) New proto/quant/coverage.py — minimum-coverage policy (holdout
|  starts day 79; full needs ≈91 d) + read-only audit CLI; TDD
|  tests/test_quant_coverage.py. Fixed the silent `--coins` no-op in
|  proto/quant/__main__.py and forwarded it through eval_harness.evaluate →
|  research.collect_events (WS5 case 23). Commits 168b960 + e51ce1c
|  (review-round guards: non-finite spans, corrupt archive, whitespace list,
|  doc consistency).
|  (3) WS5 dev/val run on the full snapshot (no --holdout, EXIT=0; canonical
|  git_rev da5f43c): dev n=24,749 mean_r −0.0154 CI[−0.0417,+0.0142], val
|  n=6,495 −0.0166 CI[−0.0468,+0.0174] (95% day-cluster bootstrap) —
|  straddle zero: NO EVIDENCE of a positive edge. Selected rule
|  breakout_start[rvol>=4]/cont × geom T288_ks3_R2, best of 288 configs,
|  dev day-cluster t −1.06 / SPA p 1.0 / 0 of 288 positive on dev.
|  Historical, superseded funding-adjusted figures from the initial run:
|  dev 24,311 (excluded 5 missing-coin + 433 incomplete; coverage 0.982),
|  mean −0.0149 CI[−0.0411,+0.0153]; val 6,374 (excluded 121 incomplete),
|  mean −0.0163 CI[−0.0473,+0.0184]. These were superseded by the
|  transition-aware recompute documented in the current WS5 summary and
|  canonical implementation report. Holdout keys null. Reproduced identically by an independent
|  concurrent run at 17e9a74 and by a third snapshot on 2026-10-19. Holdout
|  remains SEALED. Report + hashed artifacts:
|  docs/implementation-reports/quant-roadmap-2026-10-18/.
|  (4) WS3/WS4/WS6/WS7 gap audit (read-only probes): OI + book/spread
|  history externally blocked; 1m served but adapter unimplemented and the
|  sweep ~25× off schedule; /settlements + /prices + /quant-status still
|  404 on pid 715 (deploy pending owner approval). No promotion records;
|  all registry rules `research`. run_all.py ALL SUITES PASS (78 files).
|  NOTE: no raw archive was ever rewritten in any stage.

## Handoff (committed-state reference — NOT part of the stage log above)

### Working-tree state
- The following was a historical workspace snapshot recorded 2026-10-15,
  not a current status assertion. The ignored `docs/superpowers/plans/`
  directory holds local SDD plans and is intentionally preserved. The
  `proto/collector.py` sort fix was
  reviewed as part of the WS1 stage and committed at 27299bf; the WS7
  promotion gate is committed (Stage D, 502df2e).

### Previously untracked files, now committed
- `docs/handoff-2026-10-15.md` (committed at a2f74e7): committed state,
  invariants, next priorities, pitfalls.
- `docs/roadmap-status.md` — this file (roadmap status; now tracked).
- `docs/ws3-timeliness-feasibility.md` — WS3 decision record (1m
  feasibility, provisional-vs-closed).
- `proto/promotion.py`, `tests/test_promotion.py` — WS7 promotion gate
  (committed in the Stage D commit 502df2e).

### Committed state (2026-10-15)
- `983c083` feat(WS4): point-in-time funding observation archive +
  audit --tf-sec
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

- WS5 (research foundation): hypothesis registry + shadow engine +
  evaluation harness + cost attribution DELIVERED; IN PROGRESS:
  calibration run on production bars (research-only, SHADOW, output to
  temp), competing outcomes, calibrated probabilities (proposal §3–§5).
  The evaluation harness runs the research splits (dev/val/holdout with
  embargo) on production data with funding-cost attribution; the
  promotion gate still states outcome inputs are unbuilt. No rule has
  passed.
- WS6 (evidence-focused UI): Research view DELIVERED (shadow counts);
  sample-n / CI / calibration / coverage / validation-status display NOT
  STARTED (blocked on WS5 outputs).
- WS7: decision log file + validator DELIVERED (empty log awaits its
  first genuine entry); still pending: champion/candidate persistence
  and outcome-based comparison (needs WS5).
- WS3: 1m design stage (venue probe + request budget + archive
  decision); stale-coins uptime look.
- The overall quantitative research system is NOT complete: WS4 deploy +
  WS5 outcome evidence + WS6/WS7 remain.

### Committed state (2026-10-16 → 2026-10-17)
- `ce97d5f` feat(WS4): mark/index price history archive and /prices route
- `6d27543` feat(WS4): --ts-field CLI for archive grid audit
- `c207b4a` docs(roadmap): reconcile deployment state, WS4 findings,
  WS5/WS6 status
- `48ef382` fix(WS4): correct settlement-endpoint rate limiter + data-dir
  default alignment
- `2184f87` fix(WS4): shared settlements dir, currentPage check, rate
  limiting, read-path finiteness
- `2ee0438` docs(WS4): settlement history spec, operator reference, status
- `cbb1351` feat(WS4): auditable timestamp column for settle grid
- `90a568a` feat(WS4): settlements read route
- `237c7e0` feat(WS4): guarded settlement collect hook
- `55f96b7` feat(WS4): venue-reported settlement archive
- `e442ad9` docs(WS5): shadow-only docstring, suite count, QuantStatus
  actionable_events
- `b5ec5f8` feat(WS5): shadow-only promotion contract with regression cover
- `68bfd5a` docs(WS5): quant integration architecture and status
- `44df80f` feat(WS5): research view for shadow findings
- `139bcac` test(WS5): route-level unavailable-status coverage
- `e80c07c` feat(WS5): quant status route and shadow event
- `deba410` feat(WS5): guarded quant-shadow daemon hook
- `2c85807` feat(WS5): vendor quant research-and-shadow engine (additive)

### Committed state (2026-10-18)
- `61e0616` fix(WS5): funding-adjusted metrics, settle-boundary attribution,
  policy side
- `45902c1` fix(WS5): map observation fallback to the venue settle grid
- `9024f1b` docs(WS5): venue-grid observation fallback; uninferable grid
  incomplete
- `17e9a74` docs(quant): 2026-10-18 data-readiness snapshot; WS5 harness
  still unrun
- `faa7583` docs(quant): correct 2026-10-18 data-readiness snapshot
- `168b960` feat(quant): minimum-coverage policy + forward `--coins` in
  evaluate
- `f44e1dc` docs(quant): 2026-10-18 roadmap-completion progress and final
  report
- `e51ce1c` fix(quant): harden coverage policy and `--coins` wiring
  (review round)
