# Quant Integration (WS5 shadow path)

Vendored research-and-shadow engine (`proto/quant/`, Task 1) wired into the
local stream as a permanently-shadowed observer: a guarded daemon hook, a
read-only service route plus a counts-only SSE kind, and a Research view.
No rule has passed promotion; everything the engine produces is labeled
SHADOW / not actionable / insufficient-evidence.

Engine authority (rules, gates, thresholds, verdict: no rule has passed):
`docs/QUANT_MODULE.md` + `docs/quant-research-2026-10-12.md`.
This document covers only the local glue.

## Architecture

```
fixture/live bars (data/bars, append-only .csv.gz, ts,o,h,l,c,vol,amount)
  │  research CLI (offline, tmp out dir): dev -> val, holdout-once locked
  ▼
Watcher(data_dir, QuantStore(quant.db))   # own store; never signals.db
  │  .step() per sweep (guarded; never touches sweep state)
  │  SHADOW rows (mode='shadow') + suppression counters + watermarks
  ├─► GET /quant-status (read-only, mode=ro): availability + counts
  └─► emit_quant_shadow → quant.shadow SSE (counts only, on change)
        └─► Research view (Analysis page): SHADOW banner, counts,
            suppression rows, measured-range promotions, no setup tables
```

Separation (structural, not conventional):

- Quant keeps its own store (`quant.db`, default `data/quant.db`, beside
  the bars dir). It never opens `signals.db`; the service route opens
  quant.db with `file:…?mode=ro` per request and never imports
  `QuantStore` (no WAL side effects — direct SQL).
- Placement (`proto/service.py:_make_handler`, `serve`, `run_service`):
  `quant_db` is resolved once per server, mirroring `funding_dir`.
- SSE frames carry counts only (`state: {emitted, resolved}`, `coin:
  None`): no levels, no advice, nothing actionable.
- Research rows never render as setups: the Research view uses no
  setup-table components (no `<table>`, no TopSignals/StatCards), and
  Dashboard/setup endpoints are behaviorally unchanged (existing suite
  covers the contract).

## Commands

```bash
# offline research on any bars dir (never production data/ without intent)
python3 -m proto.quant research --data <bars> --out <tmp-out>   # no --holdout: holdout stays unevaluated
python3 -m proto.quant research --data <bars> --out <tmp-out> --holdout  # once-only; creates holdout_evaluated.json lock

# WS5 evaluation harness: research splits on production bars with funding cost attribution
python3 -m proto.quant evaluate --data data/bars \
    --funding-dir data/funding \
    --settlements-dir data/funding_settlements \
    --out <tmp-out>              # research-only; never writes data/, never starts service
python3 -m proto.quant evaluate --data data/bars --funding-dir data/funding --holdout \
    --out <tmp-out>             # spends the one-shot holdout (lock enforced)

python3 -m proto.quant watch --data <bars> --db <quant.db> --once   # single step; fresh process replays silently
python3 -m proto.quant report --db <quant.db>    # events/open/resolved denominators + TP/SL/TIME vs driftless null
python3 -m proto.quant status --db <quant.db>    # config_hash, pause flags, event count
python3 -m proto.quant gates --research <out/research.json> --db <quant.db>  # promotion check (records pass/fail)
```

The `evaluate` command (WS5 evaluation harness) runs `research.collect_events`
+ `run_stage_dev_val` + one-shot holdout over production bar archives with
funding-cost attribution from the WS4 archives. Splits, embargo, and the
holdout lock are inherited from the research harness. Missing funding data
is surfaced as `missing_cost_coins`, never zero-filled. The report carries
`config_hash`, `cost_hash`, `git_rev`, and `bar_digests` for reproducibility.
See `docs/ws5-evaluation-harness.md` for the full operator reference.

Daemon hook (opt-in, default off):

```bash
python3 -m proto.service --quant-shadow --quant-db <qdb> --quant-bars <bars> --quant-equity 1000
```

`--quant-shadow/--quant-db/--quant-bars/--quant-equity` are daemon-parser
flags (same names/defaults as the engine CLI). The service parser inherits
them; `main()` forwards them into the embedded loop's argv via
`_daemon_argv_for` (route and loop derive from the same args, so they can
never disagree about which store is live). The hook init is lazy (zero
import cost when off); init failure prints `quant init FAILED` and leaves
the watcher disabled. The per-sweep step sits after the measure block and
before sweep accounting, inside the existing never-raise guard: a watcher
failure is logged as `quant FAILED` and never touches
`sweep_ok/collect_ok/sweep_counts/last_*` nor calls `on_sweep`.

## Data flow (watcher → API → UI)

1. Each daemon-loop iteration steps the watcher (when enabled).
2. `emit_quant_shadow(last, result)` compares the step's cumulative
   `(emitted, resolved)` against the previous tuple and appends one
   `quant.shadow` event to the existing ring **only when the counts
   changed and at least one is nonzero** — per-sweep polling never spams
   the ring. It rides the unchanged `Last-Event-ID` / `resync` / keepalive
   machinery (`EVENT_KINDS` += `"quant.shadow"`; `/snapshot`
   `event_kinds` picks it up automatically).
3. `GET /quant-status` (behind the bearer gate, 401 without token) answers
   200 whenever quant.db is readable, with `{mode: "shadow", status,
   paused, pause_reason, config_hash, event_counts: {total, emitted,
   resolved}, suppression_top: [{rule_id, reason, n}], promotions,
   reason}`. Empty store → explicit zeros + `reason:
   "insufficient-evidence"`, never 404. 503 only when quant.db itself is
   unreadable (missing file, missing `quant_event` table).
4. The Research view re-reads `/quant-status` on every `quant.shadow`
   frame (and on resync) via the existing `useEvents` hook.

Availability states (single source of truth: `QuantStore` meta +
watermark): `unseen` (watcher never ran — no watermark), `healthy`,
`paused` (manual `pause` flag or watcher `auto_pause`, with
`auto_pause_reason` naming the cause — e.g. `no BTC bars` — set by the
engine itself), `unavailable` (readable but not writable). Bars-health is
intentionally not plumbed separately: the watcher's own pause reason
names the cause.

## Limitations (load-bearing, verified by tests/e2e)

- **Fresh-process replay is silent by engine design.** A new `Watcher`
  replays the file tail up to EOF with `emit=False`, so `watch --once`
  on static files can never emit — live emission needs new bars arriving
  between steps of a PERSISTENT watcher (same process / same daemon
  loop). Bounded runs must stream day-by-day with increments < `TAIL_ROWS`
  (600), or emissions are lost to gap-rebuilds.
- **Pause lags one step.** Candidates are judged by the previous step's
  pause flag, and signal age must stay within `max_signal_age_bars` (3):
  fixtures for emission runs must end within minutes of now.
- **Per-coin per-rule cooldown (144 bars).** A second emission for the
  same coin+rule inside the window is suppressed, not emitted.
- **No holdout without `--holdout`, and `--holdout` is once-only**
  (lock file). Research without the flag leaves holdout unevaluated.
- **Unwritable quant.db degrades, never breaks the loop**: watcher step
  and status route report explicit `unavailable`; daemon loop and API
  are unaffected.
- **Shadow-only live contract.** The embedded watcher never writes
  `ACTIONABLE` rows: promotions are not consulted at emission time, so
  every persisted row is `mode='shadow'`. A passed promotion is a
  research fact (visible in `promotions` and the gates record), never a
  live-emission mode switch — enabling ACTIONABLE emission requires a
  separately approved stage. `/quant-status` counts shadow rows only;
  any legacy `ACTIONABLE` rows are reported separately as
  `actionable_events`, never mixed into shadow counts. The UI has no
  path that renders a quant finding as a setup, plan, or order advice;
  promotion records render uncertainty ONLY as `measured range
  (lower–upper)` with the caption `range of measured outcomes, not a
  prediction`.

## Registry relationship (no code merge)

Two registries, two jobs, linked by id pairs in future decision-log
entries:

- `proto/quant/registry.py` = **rule code**: the tradeable rule grid
  (`REGISTERED` specs with stable `rule_id`s, candidate/control pairs).
  Registration is NOT promotion — every rule starts `research`-status and
  only a `gates` pass for that exact id + config hash can change that.
- `docs/hypotheses/` = **preregistration process**: human-owned
  hypothesis files (`HYD-*`, e.g. the illustrative
  `HYD-0001-funding-quintile-drift.md`) with windows, thresholds,
  denominators, and costs, validated by `proto/hypothesis_registry.py`.

The link between a code rule and a hypothesis is a `(rule_id, HYD-id)`
pair recorded in a decision-log entry (`docs/decisions/log.jsonl`,
validated by `proto/decisions.py`) — deliberately a record, not a code
merge: neither side imports the other, and no merge is planned. The
evaluation harness that would produce such entries is a later WS5
increment (unbuilt).

## Verification

- `python3 tests/test_quant.py`, `tests/test_quant_engine.py` (vendored).
- `python3 tests/test_quant_shadow_hook.py` (hook guards + service
  forwarding of the quant flags into the embedded loop argv).
- `python3 tests/test_quant_api.py` (route shape/auth/empty/unwritable,
  emit gating, SSE ordering + below-floor resync).
- `python3 tests/run_all.py` (full backend suite).
- `cd app && npm run test && npm run typecheck && npm run lint`.
- Bounded e2e (tmp only, Task 5 report): fixture bars → research
  (dev/val n>0, denominators, holdout unevaluated) → `watch --once`
  replay + persistent streaming (SHADOW rows, `mode='shadow'`) → report /
  status → embedded-loop test service → `/quant-status` counts match db →
  `quant.shadow` SSE frame with matching counts → below-floor `resync` →
  separation proof (zero quant tables in signals db, production `data/`
  append-only-or-untouched, no quant artifacts).
