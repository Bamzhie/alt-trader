# TASK F report — startup & first-sweep optimization (Workstream F)

**Worktree:** `/tmp/opencode/wt-f` (branch `feature/ws-f-startup`)
**Brief:** `task-F-brief.md` (Workstream F of the research-only quant platform)

---

## 1. Status

**DONE_WITH_CONCERNS**

Everything in the brief (F1–F7) is implemented, tested and committed. The
"concerns" are (a) two documented deviations from the brief's letter (the
measure cycle's per-coin interrupt and the bar-index file location), and
(b) the end-to-end wall-clock improvement is **not measurable without
deploying** — the numbers below are micro-benchmarks on the production
archives (read-only) plus the code paths that produce them.

Nothing was pushed. The live systemd service was never stopped, reloaded or
altered; no `systemctl` write command was run; the operator's drop-in
(`~/.config/systemd/user/altradar.service.d/quant-shadow.conf`) was not
read-modified; `data/` in the orchestrator checkout was not written to
(verified: no `data/bars/.bar_index.json`, no `data/.cadence.json`, no
`data/.timing.json` there after the work).

---

## 2. What changed, file by file

### 2.1 `proto/daemon_timing.py` (NEW)

Stdlib-only module holding the two persisted sidecars and the stage state
machine. Module docstring states the contract (a failure is never recorded
as a success; a failed stage is never marked as run; the cadence file is
only written when a stage actually completes).

```python
STAGES = ("scan", "collect", "funding", "settlements", "prices", "resolve",
          "measure", "quant")
OUTCOMES = ("ok", "skipped", "failed", "not_due", "partial")
KEEP_ITERATIONS = 5
TIMING_FILE = ".timing.json"
CADENCE_FILE = ".cadence.json"

def sidecar_path(db_path, name) -> str          # <db dir>/<name>
def db_key(db_path) -> str                      # absolute DB path
def atomic_write_json(path, payload) -> str     # temp file + os.replace
def read_json(path) -> dict                     # {} on any problem
def format_stages(stages) -> str                # "stages: scan=458.2 collect=..."

class TimingLog:
    def __init__(self, path=None, keep=KEEP_ITERATIONS, now_fn=time.time, db=None)
    def load(self) -> list
    def save(self) -> str | None
    def begin(self, ts=None) -> dict             # start an iteration record
    def record(self, stage, duration_s, outcome, **extra) -> dict
    def end(self, duration_s=None, ts=None) -> dict | None
    def last(self) -> dict | None
    def last_stages(self) -> dict                # {stage: {duration_s, outcome}}
    def stage_durations(self) -> dict
    def summary(self) -> str
    def mark_sweep_started(self, ts=None) -> dict
    def mark_sweep_completed(self, ts=None, duration_s=None, stages=None) -> dict
    def first_sweep_state(self) -> dict          # the /scanner-health block

class Cadence:
    def __init__(self, path, intervals=None, now_fn=time.time, db=None)
    def interval(self, stage) -> int
    def due(self, stage, now=None) -> bool
    def mark(self, stage, now=None) -> dict
    def last_run_ts(self, stage) -> int | None
    def save(self) -> str | None
```

Design points:

* `data/.timing.json` (next to the DB) keeps the **last 5 iterations**,
  newest last, written atomically. Format:
  `{"version": 1, "db": "<abs db path>", "iterations": [...]}`.
* `data/.cadence.json` (next to the DB) keeps the per-stage due-times:
  `{"version": 1, "db": "<abs db path>", "stages": {"scan": {"last_run_ts": int,
  "interval_s": int}}}`.
* Both sidecars record **which DB they belong to**. A directory may hold
  more than one DB (tests, side-by-side deployments); a sidecar written
  for another DB is ignored, so a cadence can never suppress work for a
  different database.
* A sidecar that cannot be written never fails the stage that produced it
  (`save()` swallows everything; the in-memory state is still correct).

### 2.2 `proto/daemon.py`

* `run_daemon_loop(args, stop_event=None, on_sweep=None, app_box=None, timing=None)`
  — new optional `timing` keyword. The service hands its shared
  `TimingLog` over through `app_box["timing"]` instead, so the embedded
  loop's **call shape never changes** (no existing caller/test had to be
  touched for the signature).
* New helper `_accepts_kwarg(fn, name)` — the repo's existing
  "decide the call shape ONCE by signature inspection" rule, reused for
  `App.scan_once(record_sweep=...)` and `Watcher.step(should_stop=...)`.
* The process-local floats `last_collect` / `last_resolve` / `last_measure`
  are **gone**; they are replaced by the persisted `Cadence`
  (`scan`→`--interval`, `collect`→`--collect-every`, `resolve`→
  `--resolve-every`, `measure`→`--measure-every`).
* Every stage is timed on the monotonic clock and recorded with its
  outcome: `ok`, `skipped`, `failed`, `not_due`, `partial`. A stage that
  raises is recorded as `failed`; a stage with per-coin errors is
  `partial` with `failed_coins`; a stage that is not due is `not_due` and
  does no work.
* Cadence marking rules (F3): **total failure ⇒ not marked** (it retries on
  the next iteration), **partial failure ⇒ marked** (with the per-coin
  error count recorded), success ⇒ marked. The mark is written with the
  stage's *start* time, exactly like the `last_collect = now` it replaces.
* One line per iteration:
  `stages: scan=458.2 collect=851.0 funding=8.1 settlements=520.6 prices=734.2 resolve=0.0 measure=441.3 quant=1012.0`
  (a non-`ok` stage carries its outcome in parentheses, e.g.
  `collect=0.0(not_due)`; a stage an interrupted iteration never reached
  shows `(skipped)` — a failure is never printed like a success).
* The **quant** stage is now logged even when it succeeds (it used to
  print nothing at all).
* Stop-flag (F5) checks: between stages (after the scan, after the collect
  block, after the venue hooks, after the resolve, after the measure) and
  between per-coin units: the collector (`stop_event=` per coin), the quant
  step (`step(should_stop=...)` per coin) and the measurement cycle (the
  progress callback raises `_StopRequested` between coins).
* F6: the app-level sweep write is suppressed on the daemon path
  (`App.scan_once(record_sweep=False)`), so the daemon writes **exactly
  one** `watcher_events` row and **one** `dashboard_snapshot` row per
  completed iteration. The CLI/TUI path is unchanged.
* A total collector failure (every coin errored) is now `failed` and is
  **not** counted as a completed sweep (before, an empty result was
  recorded as a successful sweep with `eligible=0`).

### 2.3 `proto/service.py`

* New `_db_readable(store_path)` — the readiness probe. A pure read
  (`SELECT count(*) FROM sqlite_master`); no write, no lock a writer could
  hold, so the daemon's write lock can never make readiness flap (the old
  probe wrote `INSERT OR REPLACE INTO meta` on the live 170 MB WAL DB).
* `_handle_readyz(self, origin, store_path)` — API-serving readiness:
  `200 {"ready": true, "ts": <epoch>}` as soon as the HTTP server serves
  and the DB is readable; `503 UNAVAILABLE` ("database not readable")
  otherwise. It no longer waits for the first sweep.
* `_handle_scanner_health` — additive keys only:
  `first_sweep` = `{"state": "pending"|"in_progress"|"complete",
  "started_ts": int|null, "completed_ts": int|null, "duration_s": float|null,
  "stages": {...}}` and `stages` = the per-stage breakdown of the most
  recent iteration. All pre-existing keys keep their meaning.
* `run_service` — owns the shared `TimingLog` and publishes
  `first_sweep` + `stages` through `_scanner_health()`; the loop receives
  it via `app_box["timing"]`.
* Module docstring documents the readiness contract (the brief asked for
  exactly this).

### 2.4 `proto/collector.py`

* New `BarIndex` class + `index_path(data_dir)` → `<data_dir>/.bar_index.json`:
  `coin -> {"max_ts", "min_ts", "size", "mtime_ns", "rows", "contiguous"}`,
  written atomically, flushed once per collect batch (not once per coin).
* `append_bars(data_dir, coin, bars, index=None)` — when the on-disk
  `(size, mtime_ns)` matches the index **and** the archive is provably
  gap-free (`rows == (max_ts-min_ts)//300 + 1`) **and** every incoming
  timestamp is on the archive's grid, the recorded `max_ts` is the exact
  dedupe cutoff and **the archive is not read at all**. Anything else
  (a rewritten file, a gap, an off-grid batch) falls back to the full
  read, so a stale or wrong index can never lose or duplicate a bar.
* `collect_full_universe(data_dir="data/bars", limit_per_coin=200, max_workers=COLLECT_WORKERS, stop_event=None)`
  — new `stop_event` (F5: the stop is observed between coins). The return
  value is now a `CollectCounts` (a `dict` subclass) that also carries
  `.errors` (per-coin reasons) and `.stopped`; every existing consumer
  keeps working (`counts[coin]`, `len(counts)`, `sum(counts.values())`).
  Per-coin failure isolation is unchanged: one bad coin never fails the
  cycle, and the shared `scan.LIMITER` (20 req/s) is still the only
  fetcher — no second, parallel fetcher was added.

### 2.5 `proto/quant/engine.py`

* `BOUNDED_REBUILD_BARS = max(15 * BARS_PER_DAY, REPLAY_BARS)` (= 5880).
* `load_coin_window(data_dir, coin, n=BOUNDED_REBUILD_BARS)` — one
  streaming pass over the gzip file that keeps only the **n largest
  timestamps** (first occurrence of a duplicate wins, exactly like
  `bars.load_coin`). Only the timestamp of each row is parsed while
  streaming; the OHLCV values are parsed for the n rows that are kept. A
  row that is not well formed makes the coin fall back to the full parse,
  so the window is never silently different from `load_coin`'s.
* `rebuild_window(bars, n=REPLAY_BARS)` — the shared `(a, z)` window
  computation (the last contiguous run, pulled back to at most `n` bars).
  Both the full parse and the bounded window select the same bars through
  it, which is what makes the two rebuilds feature-identical.
* `Watcher._rebuild(self, coin, reason, full=False)` — `full=False` (the
  default) uses the bounded window; `full=True` is the historical full
  parse, kept for the equivalence test. The rebuild also:
  * reads the **persisted watermark** back (`self._watermarks`, refreshed
    once per `step()`), counts `resumed` and refuses the shortcut when the
    archive is *behind* the watermark (`watermark_rollback`) — the
    "file changed underneath" fallback;
  * records the file signature before and after the read and, when the
    file did not change and the rebuild consumed the whole window, stores
    it in `cs.sig` so the following `_advance` skips the redundant
    full-file tail read (the rebuild already covers the file's content).
* `Watcher.step(self, should_stop=None)` — the stop is observed between
  coins; the status dict gains `interrupted` and `watermarks`.
* `Watcher.status()` gains `watermarks`.

### 2.6 `proto/quant/qstore.py`

* `QuantStore.watermark(coin)` — the persisted watermark of one coin
  (None when it never ran). `watermarks()` already existed and is now
  actually read back by the watcher.

### 2.7 `proto/app.py`

* `App.scan_once(self, record_sweep=True)` — new keyword, **default
  `True`**, so the CLI/TUI path keeps writing its own sweep-complete
  journal event + dashboard snapshot row. The daemon passes `False`
  because it records the sweep itself (one row per iteration, never two).

### 2.8 `tests/test_ws_f_startup.py` (NEW, 76 checks)

Self-checking script in the repo's style (stdlib only, offline, temp-dir
fixtures). It covers every item the brief requires:

| Requirement | Checks |
|---|---|
| `/readyz` 200 before the first sweep; `first_sweep.state == in_progress`, `completed_ts: null` | `test_readyz_and_first_sweep` |
| a **failed** first sweep never claims "complete" | `test_readyz_and_first_sweep` |
| a warm restart does not re-run a stage that is not due; an expired stage runs | `test_cadence_persisted_across_restarts` |
| a failed stage is not marked as run and retries | `test_failed_stage_is_not_marked_as_run` |
| the bar index avoids the archive read; a mismatched index falls back (no duplicates, no loss) | `test_bar_index` |
| the bounded rebuild produces **identical** feature values (sigma, z, res, beta, rvol, qv24, z1h, z4h, up/dn age) | `test_bounded_rebuild_is_identical` |
| a stop flag mid-cycle ends the iteration promptly; the interrupted stage is not `ok` | `test_stop_flag_interrupts_the_iteration` |
| exactly one `watcher_events` + one `dashboard_snapshot` row per iteration; the CLI path still writes its own | `test_one_sweep_record_per_iteration` |
| the stage durations appear in `/scanner-health` | `test_stage_durations_in_scanner_health` |
| the timing log / cadence persistence, atomicity and outcomes | `test_timing_log_and_cadence` |

### 2.9 Existing tests changed (and why)

Two files, three edits, all forced by the `/readyz` contract change and by
one new call shape:

* `tests/test_service_routes.py` — the assertion *"readyz not-ready → 503
  UNAVAILABLE"* encoded the old contract (readiness waited for the first
  sweep). It is replaced by the new contract: *"/readyz does not wait for
  the first sweep (still 200)"* plus a new check that an **unreadable DB**
  is what makes `/readyz` answer `503 UNAVAILABLE`.
* `tests/test_service_lifecycle.py` — the assertion *"/readyz stays 503
  while scanner failed (distinct signal)"* encoded the old contract. It is
  replaced by *"/readyz is API readiness (not sweep readiness)"* plus two
  new checks on the `first_sweep` block (the state is published, and a
  failed scanner never claims a completed first sweep).
* `tests/test_service_lifecycle.py` — the check *"daemon has no copied
  scan body"* was a crude `scan_once not in daemon_src` proxy. The daemon
  now calls the app's scan entry point (three call shapes), so the check
  was sharpened to its intent: the daemon must not **define** a scan body
  and must not inline the scoring
  (`"def scan_once" not in daemon_src and "score_universe" not in daemon_src`).

No expectation was weakened; the new assertions are strictly stronger
(they check the new contract, not just "it returns something").

---

## 3. Tests — every command and its real output

```
$ python3 tests/test_ws_f_startup.py
exit=0
ALL PASS  tests/test_ws_f_startup.py  (76 checks)

$ python3 tests/test_service_lifecycle.py
exit=0
ALL PASS  tests/test_service_lifecycle.py  (78 checks)

$ python3 tests/test_service_routes.py
exit=0
ALL PASS  tests/test_service_routes.py  (34 checks)

$ python3 tests/test_service_auth.py
exit=0
ALL PASS  tests/test_service_auth.py  (19 checks)

$ python3 tests/test_collector.py
exit=0
  PASS  every coin has its bars on disk
ALL PASS

$ python3 tests/test_quant_engine.py
exit=0
ALL PASS

$ python3 tests/test_quant_shadow_hook.py
exit=0
ALL PASS (15 checks)

$ python3 tests/run_all.py
exit=0
PASS  test_watcher_ui_parray.py  [ALL PASS  tests/test_watcher_ui_parity.py — cross-surface parity green]
PASS  test_watcher_view.py  [ALL PASS]
PASS  test_ws5_eval_harness.py  [ALL PASS]
PASS  test_ws_f_startup.py  [ALL PASS  tests/test_ws_f_startup.py  (76 checks)]

ALL SUITES PASS (82 files)
```

(The `run_all.py` line above is `test_watcher_ui_parity.py`; the exact
final line of the run is `ALL SUITES PASS (82 files)` with 82 `PASS`
lines and zero `FAIL` lines.)

The suite was also run from an **empty working directory** (both with the
new code and with a pristine `git archive HEAD` copy) to prove the suite
writes nothing into `./data/`: both runs ended in `ALL SUITES PASS` and
created **no** `data/` directory. (The `data/` tree that appeared in this
worktree during development came from two ad-hoc debugging runs of mine
that used the *default* archive dirs with the live venue — before the
cadence fix and in a manual service probe. It has been deleted; the
worktree now has no `data/` directory, and the orchestrator's production
`data/` was never written to.)

---

## 4. Before / after reasoning

### 4.1 The restart path — "ready after 65.8 minutes"

**Before.** `GET /readyz` returned 503 until `sweep_done.set()` fired,
which happened at the **end of the whole first loop iteration**
(`proto/daemon.py` → `on_sweep` → `proto/service.py` `_on_sweep`). The
measured first iteration was 3951 s (65.8 min): scan + collect + funding +
settlements + prices + resolve + measure + quant, all of which a restart
re-ran from scratch. The readiness probe was also a **write**
(`INSERT OR REPLACE INTO meta`) on the live 170 MB WAL database, so under
the daemon's write lock it could answer 503 "database not writable" even
after a successful sweep.

**After.**
1. The HTTP listener binds and serves **before** the loop thread starts
   (unchanged: `serve_with_fallback(...)` runs before
   `loop_thread.start()`), so the API is reachable in < 1 s.
2. `GET /readyz` now answers `200 {"ready": true, "ts": <epoch>}` as soon
   as the server serves **and** the DB is readable — the probe is a pure
   read, so the daemon's write lock cannot make it flap. **The 65.8-minute
   wait is gone by construction: readiness no longer depends on the first
   sweep at all.** The first-sweep state moved to
   `GET /scanner-health → first_sweep`, which reports
   `pending → in_progress → complete` and never claims a sweep that did
   not complete (a failed or interrupted first iteration leaves
   `completed_ts: null`).
3. The first iteration after a restart no longer repeats work that is not
   due: `scan`, `collect`, `resolve` and `measure` read their due-times
   from `data/.cadence.json`, which is written **only when a stage
   actually completes**. So the very first restart after this change
   behaves conservatively (everything runs, as today) and every later
   restart resumes.

**Measured effect of the cadence on a warm restart:** the stages that the
persisted cadence says are not due are recorded `not_due` and do no work
at all — no scan of 150 coins, no full-universe collection, no outcome
resolution, no measurement cycle. With the shipped flags
(`--interval 60 --collect-every 300 --resolve-every 3600
--measure-every 300`) a restart that happens within 300 s of the last
collect skips the collect entirely. The end-to-end seconds saved are
**not measurable without deploying** (the numbers below are the measured
cost of the work that is now skipped).

### 4.2 The first-boot path

The first boot is deliberately **not** faster in terms of *what* runs: the
cadence file does not exist, so every stage is due and runs exactly as
today. What changed on the first boot is how much CPU/IO the work costs:

| Work | Before (measured on the production archives, read-only) | After |
|---|---|---|
| collector dedupe (read the whole archive of every coin, every cycle) | `read_bars` = **585 ms/coin** → **339 s** for 579 coins, *every* collect cycle (the cycle's own cadence is 300 s) | the archive is **not read at all** when the index matches → the 339 s of dedupe I/O per cycle disappears; only the venue fetch remains (560 coins at the shared 20 req/s limiter ≈ 28 s minimum) |
| quant rebuild (parse the whole archive per coin) | `load_coin` = **434 ms/coin** → **251 s** for 579 coins | bounded window = **275 ms/coin** → **159 s** for 579 coins |
| quant rebuild total (parse + feature replay) | 445 s for 579 coins (25-coin sample, extrapolated) | 399 s for 579 coins (same sample) |
| quant first step after a restart (parse + replay + the redundant per-coin tail read) | the tail read decompresses each whole gzip file again (0.15 s/coin ≈ 87 s) | skipped: the rebuild stores the file signature it verified, so `_advance` does not re-read the file |

The feature replay itself (5880 bars per coin, 303 ms/coin ≈ 175 s for 579
coins) is unchanged — it is the feature warm-up, not waste.

**Equivalence proof (the load-bearing claim).** The bounded window is not
"close enough": it is *the same set of bars*. Checked on the **579
production archives** (read-only, 470 s):

```
DONE 579 files, mismatches: 0 [] 470s
```

i.e. for every production coin, the bars the bounded rebuild replays are
**identical** (same count, same timestamps) to the bars the full parse
replays. On a synthetic 30-day archive the resulting `Snap` values
(sigma, z, res, beta, rvol, qv24, z1h, z4h, up/dn age) are identical
within 1e-12 (actually bit-identical), and the two `CoinState`s agree on
`last_ts`, the warm-up count, the volatility/beta/liquidity state.

### 4.3 Shutdown

The stop flag is now observed between stages and between per-coin units,
so a SIGTERM drains inside the operator's 600 s grace instead of blocking
on a whole non-interruptible phase. The shutdown **order is unchanged**
(`loop → server → sessions`, asserted by `tests/test_service_lifecycle.py`),
and the operator's `TimeoutStopSec=600s` was not touched.

### 4.4 Sweep accounting

Before, one iteration wrote **two** `watcher_events` rows and **two**
`dashboard_snapshot` rows (the app's `journalmod.record_sweep` at
`proto/app.py:192` plus the daemon's `record_completed_sweep`). Now the
daemon path writes exactly one of each; the CLI path keeps its own row.
`last_sweep_duration_s` still means "whole iteration duration" and the
per-stage breakdown is additive.

---

## 5. Concerns / deviations

1. **The measure cycle's per-coin interrupt is a soft one.**
   `proto/measurement.py` is **not** in this workstream's file list, so
   `run_cycle` could not be given a cancellation hook. The daemon instead
   raises `_StopRequested` from the progress callback (which runs between
   the per-coin attempts), so the cycle stops journaling new attempts.
   `run_cycle`'s `finally` still calls `executor.shutdown(wait=True)`, so
   the already-submitted venue fetches drain (bounded by the shared 20
   req/s limiter: ≈ 28 s for the full universe) before the loop actually
   stops. That is inside the 600 s grace, but it is not an instant abort.
   *If the orchestrator wants a hard abort, `measurement.run_cycle` needs
   a `should_stop` keyword — a one-line change in a file Workstream F does
   not own.*

2. **The bar index lives next to the ARCHIVE, not next to the DB.**
   The brief's example path is `data/.bar_index.json`; the implementation
   uses `<data_dir>/.bar_index.json` (= `data/bars/.bar_index.json` with
   the default). Reason: the index describes the files of one archive
   directory, and placing it inside that directory makes it impossible to
   pair it with a different archive. The collector does not know the DB
   path, so a DB-relative path was not available to it.

3. **A stage with no persisted entry is treated as "never ran"
   (`last_run_ts = 0`), not as "due at any interval".** The brief says
   "a stage with no persisted entry (first boot) is due immediately". With
   the repo's own test convention of a huge interval (`collect_every =
   10**12`, used to mean "never fires"), "due immediately" would fire the
   venue. Treating the missing entry as `last_run_ts = 0` reproduces
   today's behaviour exactly (`now - 0.0 >= interval`), i.e. every real
   interval (60/300/3600 s) is due on the first boot, and a huge interval
   still never fires.

4. **The sidecars are scoped to their DB.** `data/.cadence.json` and
   `data/.timing.json` sit next to the DB (as the brief asks) and record
   the DB's absolute path; a file written for another DB in the same
   directory is ignored. Without this, two DBs in one directory would
   share a cadence (observed while testing: a second DB in the same temp
   dir inherited the first DB's due-times and skipped its scan).

5. **A "partial" scan still completes a sweep.** The stage outcome is
   `partial` (with `failed_coins`) when some coins fail to fetch, per F7
   ("never 'ok'"), but the sweep itself is still recorded — exactly as
   today, with honest eligible/evaluated counts. A stage that *failed*
   (raised) never produces a sweep. A *total* collector failure (every
   coin errored) is now `failed` and produces **no** sweep; before, an
   empty collector result was recorded as a successful sweep with
   `eligible=0` — a small honesty fix that the brief's F6/F7 ask for.

6. **The quant watermark's use is deliberately conservative.** The
   watermark is read back and used to (a) prove which bars were already
   processed, (b) let the rebuild skip the redundant full-file tail read,
   and (c) detect an archive that is *behind* the watermark (the file
   changed underneath) — in which case the shortcut is refused and the
   current behaviour is kept. It is **not** used to skip the silent
   replay of the rebuild window: replayed history never emits (the
   engine's documented contract, and the DB's `UNIQUE(rule_id, coin,
   bar_ts)` is the backstop), and the selector deliberately drops signals
   older than `max_signal_age_bars`, so re-emitting replayed bars could
   only produce stale, suppressed rows.

7. **The end-to-end "time to ready" is not measurable without
   deploying.** The code path makes it deterministic (readiness = the
   server is up + the DB is readable), and the per-stage durations that
   used to be invisible are now printed every iteration and published in
   `/scanner-health` — so the next deployment measures it directly.

8. **Scope note — the `data/` tree in this worktree.** Two of my own
   ad-hoc debugging runs (one before the cadence fix, one manual service
   probe) used the *default* archive dirs with the live venue and created
   a `data/` tree inside the worktree. It has been deleted; the
   orchestrator's production `data/` was never written to (verified: no
   `.bar_index.json`, no `.cadence.json`, no `.timing.json` there).

9. **Not touched (by contract):** the systemd unit and the operator's
   drop-in, the live service, `data/**`, `app/**`, `proto/quant/{cost_
   attribution,research,eval_harness,stats,registry,gates}.py`,
   `proto/decisions.py`, `proto/hypothesis_registry.py`. The quant output
   stays SHADOW-only (`promo = None` in `proto/quant/engine.py` and
   `"mode": "shadow"` in `proto/service.py` are unchanged), nothing was
   promoted and the holdout was not spent.

---

## 6. Commits

| Hash | Subject |
|---|---|
| `749be83` | feat(daemon): instrument the loop, persist the cadence, split readiness (WS-F) |
| `90c5704` | test(ws-f): startup & first-sweep regression suite (76 checks) |
| `ce0eef5` | docs(ws-f): Workstream F report |

(branch `feature/ws-f-startup` in `/tmp/opencode/wt-f`; not pushed)
