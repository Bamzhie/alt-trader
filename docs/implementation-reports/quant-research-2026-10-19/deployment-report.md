# ALT RADAR — pivot feature provenance fix + deployment of the quant research and startup changes

**Date:** 2026-10-20 · **Deployed commit:** `c8647b5` · **Status:** fix merged and pushed; the service is running the new code
**Parent report:** `final-report.md` (the six workstreams) · **Predecessor reports:** `ws-a-report.md` … `ws-f-report.md`

> **What this does and does not show.** Deploying the code proves the code
> runs; it is **not** evidence that any quant signal has an edge. The WS5
> evaluation still found **no evidence of a positive edge**, the one-shot
> holdout is still **sealed**, and output is still **SHADOW-only**. API
> readiness (`/readyz` = 200) means the HTTP service is serving — it does
> **not** mean the first sweep has completed.

---

## 1. The provenance defect

`proto/quant/feature_store.py` stored pivot features under the bare measure
version (`pivots-v1`) and hashed only
`coin | name | version | pivot_index | ts | price`.

The pivot **configuration** — `left`, `right`, `frame_bars` — was in neither
the identity nor the hash, even though it decides *which* bars are pivots
and *when* each becomes knowable
(`confirmed_ts = ts + (right + 1) * frame_bars * BAR_S`). The store's
uniqueness key is `(coin, name, version, bar_ts)`, and `put()` used
`INSERT OR IGNORE`: a later record computed with **different** settings was
therefore **silently dropped**, leaving the earlier record — and its
`available_ts` — in place. A caller reusing a feature identity across
configurations would believe its record was stored when it was not, and the
stored row would carry the other configuration's identity and hash.

**Callers traced.** `from_pivots()` has **no production caller**: the only
references are the module itself and `tests/test_ws_a_measurement.py`. The
feature store is listed for *provenance* only
(`proto/quant/research_report.py:79`, `proto/quant/research_surface.py:204`).
No store file exists in `data/`, so **no stored data is affected** and no
migration is required.

## 2. The chosen identity design

The feature's identity is `(coin, name, version, bar_ts)`, and `version` is
now the **full feature identity**: the measure version **plus the
configuration identity** when the measure has one.

| Element | Before | After |
|---|---|---|
| Pivot record identity | `pivots-v1` | `pivots-v1+L{left}R{right}F{frame_bars}` (e.g. `pivots-v1+L2R2F1`) |
| Provenance hash inputs | coin, name, version, index, ts, price | the above **plus the configuration id** |
| Configuration source | implicit (the caller's choice) | **required** keyword arguments, validated (positive integers) and **cross-checked** against the pivots: a pivot's `confirmed_ts` must equal `ts + (right + 1) * frame_bars * BAR_S` for the declared configuration |
| Configuration in `meta` | absent | `pivot_config_id`, `left`, `right`, `frame_bars` recorded with the record |
| Same key, different content | **silently ignored** (`INSERT OR IGNORE`) | **refused** as a provenance conflict (ValueError) — history is still never rewritten, and an identical record stays idempotent (returns `False`) |
| Helpers | — | `pivot_config_id(left, right, frame_bars)` and `pivot_feature_version(left, right, frame_bars)` (exported) |

Two configurations are now two different features: they cannot share a
uniqueness key, cannot share a provenance hash, and each keeps its own
`available_ts`. A caller that claims a configuration which contradicts the
pivots it is storing is refused before anything is written.

**Remaining limitation (stated, not hidden).** For estimator-backed features
(`from_estimate`) the identity is still the estimator version, so two
parameterizations under the same `(coin, name, version, bar_ts)` are
**refused** (never silently merged, never overwritten) rather than stored
side by side; a caller that needs both must use a distinct feature `name`.
The estimator's own `inputs_hash` already differs between them, so the
refusal is precise rather than a blind collision.

## 3. Tests

**RED verified against the pre-fix code** (`git stash` of the source change):

```
TypeError: from_pivots() got an unexpected keyword argument 'left'      # the configuration was not part of the contract
PRE-FIX: second write returned False -> the conflict was SILENT
POST-FIX: refused the conflict -> feature B/x/v1 at bar_ts 1 already exists with different content ...
stored value still: 0.5                                               # history not rewritten
```

| Check | Result |
|---|---|
| `python3 tests/test_ws_a_measurement.py` | **ALL PASS** (170 PASS lines; 18 new provenance checks) |
| `python3 tests/run_all.py` (full offline backend suite) | **ALL SUITES PASS (87 files)** |
| frontend (`npm run test` / `typecheck` / `lint` / `build`) | **not re-run** — the change touches no frontend code (the feature store is not wired to the UI). Last green run on the same tree: 200 tests / 14 files, typecheck clean, lint 0 warnings, build clean |

The new checks cover: the identity and hash change when `left`, `right` or
`frame_bars` changes; two configurations of the **same bar** are stored as
two features, each with its own `available_ts`, and neither shadows the
other; a configuration that contradicts the pivots is refused; `available_ts`
is never earlier than the pivot's true confirmation; and identical inputs
stay deterministic and idempotent.

## 4. Commit and remote verification

```
c8647b5  fix(quant): make the feature store's identity configuration-aware
```

**Deployed code commit: `c8647b5`.** `git rev-parse HEAD` ==
`git rev-parse origin/main` == `c8647b5` at the time of the fix push;
working tree clean. Every commit after it is **documentation only** — the
deployment report (`7afb386`), the code-revision correction (`51c577f`),
the second-sweep measurement (`e8973c3`) and the source archive
(`d5097cb`) — so `git diff c8647b5..HEAD` touched **only** files under
`docs/` at that moment.

> **SUPERSEDED 2026-10-20.** The service was restarted again on 2026-10-20
> (owner-authorized) and now runs the 2026-10-20 code, so the running code
> is **not** `c8647b5` any more.  The measurements below are the 2026-10-19
> deployment's and remain accurate for it.  See
> `docs/implementation-reports/quant-research-2026-10-20/final-report.md`. The tip
moves with every documentation commit; the **deployed code** does not.

**What the running service reports, and what it does not prove.**
`GET /quant-research` returns `provenance.code_rev`, which
`research_surface.code_rev()` reads from git **at request time** (cached
for a short TTL). It showed `c8647b5` right after the restart and tracks
whatever HEAD is now, because HEAD moved with the documentation commits. It
is provenance of the *working tree*, **not** a pin of the revision the
process loaded — so it is not, on its own, proof of what is executing.

The evidence that the **new** code is executing is behavioural, and it is
unambiguous: the `first_sweep` block and the per-stage `stages` map on
`/scanner-health`, the `research-surface-v1` payload on `/quant-research`,
and the three new runtime artifacts (`data/.cadence.json`,
`data/.timing.json`, `data/bars/.bar_index.json`) — none of which exists
in the pre-deploy code. The process was started at 11:32:58 with a clean
tree at `c8647b5`, and the only later commit is documentation.

## 5. Deployment

### 5.1 Pre-restart state (recorded before touching anything)

| Item | Value |
|---|---|
| Unit | `altradar.service` (`~/.config/systemd/user/altradar.service`) + drop-in `altradar.service.d/quant-shadow.conf` |
| Drop-in (unmodified) | `ExecStart=… -m proto.service --collect-every 300 --resolve-every 3600 --quant-shadow` and `TimeoutStopSec=600s` |
| Effective timeout | `TimeoutStopUSec=10min` (the operator's 600 s) |
| Old process | MainPID **572048**, started 2026-10-19 15:46:39 WAT, `NRestarts=0` |
| Quant DB | 0 events, 0 outcomes, 14 suppression rows, 580 watermarks |
| Holdout / promotion artifacts | `data/quant_research/` **absent**; `data/promotions.json` **absent**; `docs/decisions/log.jsonl` 0 bytes |

### 5.2 Restart

`systemctl --user restart altradar.service` (the documented procedure; the
unit file and the drop-in were **not** modified).

| Event | Time (local) |
|---|---|
| Restart requested | 11:22:57 |
| Old process: `service: stopped cleanly` | 11:31:53 (536 s into the drain) |
| systemd: `State 'stop-sigterm' timed out. Killing.` → SIGKILL | 11:32:57 (600 s) |
| New process started (`Started ALT RADAR …`) | 11:32:58 |
| `service: listening on 127.0.0.1:18741` | 11:32:58 (**1 s** after the start) |

**Stop→start total: 601 s.** The old process logged a clean stop at 536 s
but was still finishing its shutdown when systemd's 600 s grace expired, so
systemd sent the SIGKILL. That is the **pre-change** code's behaviour (its
phases were not interruptible); the deployed code adds per-coin stop checks,
but the shorter drain is **not yet measured in production** — see §7.

New process: MainPID **732289**, started 2026-10-20 11:32:58, cwd
`/home/bamzhie/alt-radar`, command line
`python3 -m proto.service --collect-every 300 --resolve-every 3600 --quant-shadow`
— the `--quant-shadow` flag and the 600 s stop grace are preserved.

### 5.3 Route results (live, with the bearer token)

| Route | Result |
|---|---|
| `GET /healthz` | **200** `{"ok":true,"alive":true}` (2.7 ms) |
| `GET /readyz` | **200** `{"ready":true,"ts":…}` (5.7 ms) — API-serving readiness, **1 s after the start**, no longer gated on the first sweep |
| `GET /scanner-health` | **200**; at that moment `first_sweep = {"state":"in_progress","started_ts":…,"completed_ts":null,"duration_s":null,"stages":{}}` — **the first sweep had NOT completed, and the route said so** |
| `GET /quant-status` | **200**; `mode:"shadow"`, `status:"paused"`, `pause_reason:"BTC bars stale"`, `event_counts {total:0, emitted:0, resolved:0}`, **`actionable_events: 0`**, `promotions: []` |
| `GET /quant-research` | **200**; `status:"insufficient"`, `reasons:["no shadow events recorded yet"]`, `provenance.code_rev` = the git HEAD at request time (`c8647b5` when first checked; it tracks HEAD, not the loaded revision), feature versions listed |
| `GET /readyz` / `/quant-research` **without** a token | **401** — the auth gate is intact |

### 5.4 First sweep and the new per-stage timings (the first production measurement)

`first_sweep.state = "complete"`, `duration_s = 1959.06` (32.7 min) — the
journal's one-line summary (a new capability; the quant stage now logs on
success):

```
stages: scan=449.7(partial) collect=438.4 funding=9.0 settlements=0.0(not_due)
        prices=0.0(not_due) resolve=0.0(skipped) measure=450.1(partial) quant=611.8
```

| Stage | Duration | Outcome | Detail |
|---|---:|---|---|
| scan | 449.705 s | **partial** | 148 evaluated, 2 failed coins |
| collect | 438.393 s | ok | 556 coins, 3 222 new bars |
| funding | 9.024 s | ok | 556 coins |
| settlements | 0.0 s | **not_due** | the persisted daily throttle from the previous run (19 h old) |
| prices | 0.0 s | **not_due** | same |
| resolve | 0.0 s | skipped | background resolver — **not part of the foreground wall time** (it overlaps the following sweep) |
| measure | 450.133 s | **partial** | 556 attempted, 13 failed |
| quant | 611.759 s | ok | 580 coins, 0 emitted / 0 resolved (the watcher was auto-paused) |
| **total** | **1 959.06 s** | | vs **3 951 s** for the pre-deploy first sweep → **~50 % faster** |

### 5.4.1 The second sweep — the steady-state numbers (warm index)

The **first** sweep is the cold path: it builds the per-coin bar index and
rebuilds every coin's quant state from scratch. The **second** sweep is the
steady state (warm index, warm quant state, persisted cadence):

| Stage | 1st sweep (cold) | 2nd sweep (warm) |
|---|---:|---:|
| scan | 449.7 | 321.6 |
| **collect** | 438.4 | **64.3** |
| funding | 9.0 | 19.0 |
| settlements | 0.0 (not_due) | 0.0 (not_due) |
| prices | 0.0 (not_due) | 0.0 (not_due) |
| resolve | 0.0 (skipped) | 1 471.9 (ok, **background — overlaps the sweep; NOT additive**) |
| measure | 450.1 | 178.7 |
| **quant** | 611.8 | **77.2** |
| **whole sweep** | **1 959.1 s** | **660.8 s** |

`last_sweep_duration_s = 660.76`; the journal prints
`stages: scan=321.6(partial) collect=64.3(partial) funding=19.0
settlements=0.0(not_due) prices=0.0(not_due) resolve=1471.9 measure=178.7
quant=77.2`.

**The `resolve` stage is a BACKGROUND stage and its duration is NOT part of
the sweep's wall time.** The outcome resolver is submitted to a
single-worker pool and runs concurrently with the next iteration; the
1 471.9 s it reported is the time that background job took (it was still
running when the second sweep's foreground stages finished), and it
**overlaps** the 660.8 s foreground sweep. The foreground stages of the
second sweep sum to 321.6 + 64.3 + 19.0 + 0.0 + 0.0 + 178.7 + 77.2 =
**660.8 s**, which is the wall time the sweep actually took. Adding the
resolve duration to it would double-count work that was happening in
parallel (and would be wrong by ~1 471 s).

**What is attributable to the code, and what is not.**
- **collect 438.4 s → 64.3 s** is the bar index: the first sweep had to
  read every archive once to build the index; from the second sweep the
  dedupe read is gone. This confirms the isolated measurement (585 ms/coin
  ≈ 339 s/cycle) end-to-end. *Correction to the first draft of this
  report*: the claim "the collect phase did not measurably shrink" was
  true only of the **first** (cold) sweep; with a warm index the phase
  drops by ~374 s.
- **quant 611.8 s → 77.2 s** is the bounded rebuild plus the file-signature
  fast path (the pre-deploy figure was ~1 012 s).
- **scan 449.7 → 321.6 s and measure 450.1 → 178.7 s** are **venue-side
  variance** (per-coin latency and retries); no code change targets them,
  so no attribution is claimed.
- The daily `settlements`/`prices` stages were **not due** in both sweeps
  (the persisted throttle from the previous day) — the first observation of
  the resumed cadence in production.
- The pre-deploy per-stage numbers had to be derived from journal
  timestamps (no per-stage instrumentation existed). The pre-deploy
  steady-state iteration was ~2 300 s when settlements/prices were not due
  (scan 449 + collect 393 + funding 10 + measure 450 + quant ~1 012), and
  ~3 500 s when they were — so the steady-state improvement is roughly
  **3.5×**, and the **first-sweep** comparison (1 959 s vs 3 951 s) is
  **~2×**.

## 6. Data integrity and the safety boundaries

| Check | Result |
|---|---|
| Production archives | **not rewritten** — the collector appends only new bars (3 222 new bars for 556 coins). The bar index skips the *read* for dedupe, never the write; the write path was re-read line by line before the deploy. |
| Bar freshness after the collect | the newest bar of **41 sampled coins** is uniformly ~30–35 min old (BTC 34.7 min) — a **venue-side klines lag** at the collect, not data loss (the ages are uniform across coins, and the 2026-10-19 `paused` suppression rows show the watcher was **already** auto-paused before the restart, for the same reason) |
| Quant DB after the deploy | 0 events, 0 outcomes, 16 suppression rows (the pre-existing 2026-10-19 ones plus today's), 580 watermarks, `auto_pause=1` |
| **SHADOW-only** | `mode:"shadow"`, `actionable_events: 0`; `proto/quant/engine.py` still pins `promo = None` and the service still hard-codes `"mode": "shadow"` |
| **No promotion** | `promotions: []`; `data/promotions.json` absent; `docs/decisions/log.jsonl` still 0 bytes; `docs/decisions/chain.jsonl` absent |
| **No holdout spend** | `data/quant_research/` does not exist → the one-shot holdout was never evaluated; the new research report refuses a holdout-reaching window with a `holdout-sealed:` reason |
| Operator configuration | the unit file and the `quant-shadow.conf` drop-in are **byte-identical** to before; `TimeoutStopUSec=10min` is still effective |
| New runtime artifacts | `data/.cadence.json` (persisted due-times, scoped to the DB path), `data/.timing.json` (per-stage timings), `data/bars/.bar_index.json` (per-coin archive index). All three are **accelerators/state**, not archives; deleting any of them is safe (the code falls back to the full read / re-derives the state). |
| Venue backfill | none performed — the restart ran the normal collection cycle only |

## 7. Unresolved limitations

1. **The first sweep still takes 32.7 min.** The API is ready in ~1 s, but
   the *data* is only fresh when a sweep completes; `/readyz` deliberately
   says nothing about freshness.
2. **The watcher is auto-paused** (`BTC bars stale`) because the venue's
   klines lag ~12 min behind the collect, so the deployed service is not yet
   accumulating shadow events. This is the pre-existing pause rule (the
   2026-10-19 suppression rows carry the same reason), not a regression —
   but it does mean the deployment has produced **zero** shadow evidence so
   far.
3. **The drain was not re-measured on the new code.** The 601 s stop was the
   *old* code's behaviour (it reached the operator's 600 s grace). The new
   per-coin stop checks should shorten it; that will only be observable at
   the next restart.
4. **`first_sweep` is process-local** — it resets to `pending` on every
   restart and must not be read as "the system ever completed a sweep".
5. **The collect phase only shrinks from the second sweep onward** (§5.4.1):
   the first sweep builds the bar index, so it still pays the dedupe read
   once (438 s); with a warm index the phase is 64 s. A restart always
   starts from a cold index, so the first sweep after any restart pays
   that cost again.
6. **Deployment is not evidence of an edge.** The WS5 evaluation still
   found no evidence of a positive edge; the gross-outcome SPA remains
   exploratory; H1 remains a candidate for preregistration whose ~15 bp
   break-even has **no** supporting execution-cost evidence in this
   repository.
7. The feature store is still **not wired into any production path** — it
   remains descriptive infrastructure, so this fix changes no live behavior
   beyond the store's own contract.
