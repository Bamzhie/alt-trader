# Architecture cleanup: legacy scanner/scanner retirement — implementation report

Date: 2026-10-22
Scope: backend data-flow, service/scheduler, and React frontend. No live
service was stopped, restarted, or reconfigured. No protected data was
modified (verified by mtime before/after the full test run).

## Before — runtime / dependency map

```
systemd unit altradar.service
  └─ python3 -m proto.service --collect-every 300 --resolve-every 3600 --quant-shadow
       └─ proto.service  (HTTP/SSE + embedded proto.daemon)
            ├─ proto.daemon.run_daemon_loop  (scan cycle + collect + resolve + measure)
            │     ├─ proto.scan.build_universe   (scanner rotation)
            │     ├─ proto.collector.collect_full_universe (5m bars)
            │     ├─ proto.funding.collect_funding
            │     ├─ proto.settlements.maybe_collect
            │     ├─ proto.prices.maybe_collect
            │     ├─ proto.outcomes.resolve_pending   (outcome resolution)
            │     └─ proto.measurement                  (signal evaluation)
            ├─ proto.store.Store  -> SQLite signals.db (signal journal, plans)
            ├─ proto.watcher.*    -> CUSUM/T1 SHADOW watcher over quant.db
            ├─ proto.planner.*    -> trade-plan generation
            ├─ proto.scorer.*     -> rule scoring
            ├─ proto.promotion.*  -> rule promotion
            └─ proto.app          -> terminal TUI
      ├─ proto/app.py            -> TerminalUi
      ├─ gui/                    -> tkinter desktop UI
      └─ qtgui/                  -> Qt desktop UI

frontend app/  (React/Tauri)
  ├─ Dashboard, Watchlist, Research, stubs  -> signal/scanner surfaces
  ├─ api/client.ts : snapshot, setups, setup, tradeplan, metrics, quant-*, SSE events
  ├─ store/dashboard.ts (Zustand)          -> candidates + SSE reconciliation
  └─ TopBar "SCANNING" pill / live clock

legacy CLI one-shots: proto.app, proto.report, proto.daemon, gui, qtgui
```

Data paths: `data/bars/` (archives), `data/signals.db` (signal journal),
`data/quant.db` (shadow watcher), `data/scanner.lock`.

## After — runtime / dependency map

```
systemd unit altradar.service   (active drop-in enforces the collection-only command)
  └─ python3 -m proto.service --collect-every 300
       └─ proto.service  (loopback HTTP + neutral read API ONLY)
            ├── proto.collection.run_collection_loop
            │     ├─ proto.collector.collect_full_universe  -> data/bars
            │     ├─ proto.funding.collect_funding          -> data/funding
            │     ├─ proto.settlements.maybe_collect        -> data/funding_settlements
            │     └─ proto.prices.maybe_collect             -> data/prices
            └── read-only routes over those archives
                  /healthz (auth-free), /readyz,
                  /collector-health, /bars, /funding, /settlements, /prices

frontend app/  (React/Tauri)
  ├─ Status.tsx  : single page — "quant engine: developing / not ready",
  │               real collector-health stats, no candidates
  ├─ App.tsx     : neutral shell (TopBar, ConnectionBanner, Status)
  └─ api/client.ts : healthz/readyz/collectorHealth/bars only

repo-only offline research toolkits (NOT wired to runtime, no live watch):
  - proto/research/...           (protected, uncommitted safeguards intact)
  - proto/quant/{bars,prereg,report,gates,registry,qstore,...}/
      (historical; the `watch` command and engine/signals/research_surface
       modules were removed; quant watch/emit is gone)
```

Data paths: `data/bars/`, `data/funding`, `data/funding_settlements`,
`data/prices`, and a new `data/collector.lock`. `signals.db`/`quant.db`/`scanner.lock`
are no longer opened or written by the service.

## Architecture/behavior changes at the dependency boundary

- `proto.universe` now owns `canon`, `is_synthetic`, `_fnum`,
  `build_universe`, `RateLimiter`/`LIMITER`, and synthetic-marker tables.
- `proto.collection` owns the cadence/loop (`run_collection_loop`) and the
  thread-safe `CollectionState` snapshot used by `/readyz` and `/collector-health`.
- `proto.service` is the only runtime entrypoint and no longer imports
  `scan/store/app/daemon/measurement/outcomes/watcher/planner/scorer/picks/
  promotion/replay/report/snapshot/setup_copy/episode_report/daemon_timing`.
- The `/events` SSE bus and every `/snapshot`, `/setups`, `/setup/*`,
  `/tradeplan/*`, `/commands/*`, `/quant-*`, `/metrics`, `/scanner-health`
  route was removed; unknown routes (including legacy ones) return 404.
- Frontend removed: the six-page nav, the Zustand store, the SSE client,
  `types.ts`, and every scanner/signal component. Retained neutral shell:
  `TopBar`, `ConnectionBanner`, `StatCard`, `theme.css`.

## Changed / deleted files

Backend / proto (removed — legacy scanner/signal/rule runtime):
- `proto/app.py`, `proto/daemon.py`, `proto/daemon_timing.py`,
  `proto/scan.py`, `proto/scorer.py`, `proto/planner.py`, `proto/store.py`,
  `proto/measurement.py`, `proto/outcomes.py`, `proto/picks.py`,
  `proto/promotion.py`, `proto/replay.py`, `proto/report.py`,
  `proto/snapshot.py`, `proto/episode_report.py`, `proto/setup_copy.py`,
  `proto/watcher*.py` (all watcher modules).

Live quant watcher (removed):
- `proto/quant/engine.py`, `proto/quant/signals.py`,
  `proto/quant/research_surface.py`, `proto/quant/selector.py`.
  `proto/quant/__main__.py`: removed `watch`/`status`/`pause`/`resume`
  commands and their branches.

UIs (removed):
- `gui/`, `qtgui/`, `scripts/render_dashboard.py`.

Backend (added/modified):
- `proto/universe.py` (new): extracted collection helpers.
- `proto/collection.py` (new): cadence loop + state.
- `proto/service.py` (rewritten): collection-only HTTP service.
- `proto/collector.py`, `proto/funding.py`, `proto/settlements.py`,
  `proto/prices.py`, `proto/backfill.py`: `scan`→`universe` imports.

Frontend app (removed):
- `app/src/pages/{Dashboard,Research,Watchlist,StubPage}.tsx`
- `app/src/components/{Sidebar,TopBar,ConnectionBanner,ActivityLog,
  CandleChart,CoinDetail,EvidenceBar,FilterBar,LetterBadge,MarketMovers,
  MarketOverview,RecentAnalysis,SignalPill,Sparkline,StatCards,
  TopSignalsTable}.tsx` (TopBar/ConnectionBanner/StatCard rewritten)
- `app/src/api/{client,types,sse}.ts`, `app/src/store/dashboard.ts`
- `app/tests/{scaffold,tes, test_dashboard, test_detail, test_research,
  test_research_signals, test_research_surface, test_shell, test_sse,
  test_sse_wiring, test_stat_cards, test_store, tripwire}.*`
- `app/dev/` (mock service + dev serve script)

Frontend app (added/modified):
- `app/src/App.tsx`, `app/src/pages/Status.tsx`, `app/src/components/
  {TopBar,ConnectionBanner}.tsx`, `app/src/api/client.ts`
- `app/tests/test_status.test.tsx`

Service / docs:
- `altradar.service.example` (collection-only command; owner cutover notes).
- `README.md`, `app/README.md`.
- `docs/superpowers/plans/2026-10-22-legacy-scanner-retirement.md` (plan, gitignored).

Backend tests removed (depend on deleted modules):
`test_app.py`, `test_app_watcher.py`, `test_coin_state.py`,
`test_episode_outcomes.py`, `test_episode_regressions.py`,
`test_episode_reporting.py`, `test_epoch_cut.py`, `test_epoch_integration.py`,
`test_flagging.py`, `test_gui*.py`, `test_measurement.py`, `test_mtf.py`,
`test_oi_wiring.py`, `test_outcomes.py`, `test_picks.py`, `test_planner.py`,
`test_plans.py`, `test_promotion.py`, `test_qtgui*.py`,
`test_quant_shadow_hook.py`, `test_quant_shadow_only.py`, `test_quant_engine.py`,
`test_quant_signals.py`, `test_quant_api.py`, `test_replay.py`, `test_report.py`,
`test_scan_phases.py`, `test_scorer.py`, `test_score_v3.py`,
`test_service_commands.py`, `test_service_events.py`,
`test_service_lifecycle.py`, `test_service_routes.py`, `test_setup_copy.py`,
`test_snapshot.py`, `test_snapshot_file.py`, `test_stakeless.py`,
`test_universe.py`, `test_watcher_*.py`, `test_ws_d_research_surface.py`,
`test_ws_f_startup.py`, `test_ws_a_measurement.py`, `test_ws_b_outcomes.py`,
`test_quant.py`, `test_quant_roe.py`.

Backend tests added:
- `tests/test_collection_only.py` — loop writes nothing to `signals.db`/
  `quant.db`; no legacy runtime modules imported.
- `tests/test_collection_service.py` — neutral routes work, legacy routes 404.

Backend tests adapted: `test_collector.py`, `test_funding.py`,
`test_settlements.py`, `test_prices.py`, `test_settlements_regression.py`,
`test_funding_route.py`, `test_prices_api.py`, `test_settlements_api.py`,
`test_service_auth.py`.

Frontend tests added: `app/tests/test_status.test.tsx` — covers the engine
"developing/not-ready" guarantee and the unavailable-at-launch transition.

### Frontend review findings addressed (2026-10-22)

1. **The quant engine is never "ready".** `Status.tsx` renders a fixed
   "In development / not ready" label for the quant engine. The `/readyz`
   result is shown only under a separate **Service readiness** card; an
   affirmative `/readyz` does not flip the engine label. Tests assert the
   engine label row never contains the word `ready` and always contains the
   developing string.
2. **Unavailable-at-launch transition.** `App.tsx` `useConnectionState` now
   flips straight from `starting` to `error` on the first false liveness
   probe (the prior "idle-twice" gate masked an unreachable host). Tests
   assert the banner transitions from "Starting…/connecting" to
   "Connection error" with the systemd hint.

## Bar-collection continuity

- `collect_full_universe` behavior is unchanged: `build_universe(stake=inf,
  budget=None)` covers every eligible universe coin, `LIMITER` is reused,
  and `append_bars`/`read_bars` archive dedupe logic is untouched.
- A collection-only replacement (`proto/collection.py` + `proto.service.py`)
  was added and verified by `tests/test_collection_only.py` and
  `tests/test_collection_service.py` **before** any scanner module was
  deleted. The collector was switched to `proto.universe` one step earlier
  so `scan.py` deletion broke no collector path.
- No test or command writes to `data/` during the run. Mtimes of
  `data/bars`, `data/quant.db`, `data/signals.db` were identical before and
  after the full suite (`1792687223 / 1792663909 / 1792686540`).

## Live-service cutover (owner-controlled — NOT done here)

The running service was left unchanged during the code cleanup. Cutover was
documented as owner work. This section is now updated with the **recorded
post-cleanup cutover** (still no restart performed by these edits).

**Before cutover** the live process was:
- PID 883661, argv
  `python3 -m proto.service --collect-every 300 --resolve-every 3600 --quant-shadow`,
  holding `data/signals.db`/`data/quant.db`/`data/scanner.lock`.

**After cutover** (performed by the owner on the live host, not here):
- The owner drop-in was edited so `ExecStart` runs the collection-only
  command: `python3 -m proto.service --collect-every 300`
  (the `--resolve-every` and `--quant-shadow` flags were removed; the
  `TimeoutStopSec=600s` drop-in was preserved).
- `systemctl --user stop` was used first to free `data/scanner.lock`, then
  `systemctl --user daemon-reload` + `systemctl --user start`.
- The new process is **PID 1036944**, running
  `python3 -m proto.service --collect-every 300`.
- First cycle of the new service: **healthy**, **561 coins**, **1,659 new
  bars**, `collect:` lines in the journal; no `scan:` / `quant:` /
  `emit_quant_shadow:` log lines.
- The old process's in-flight shutdown pass logged **46 legacy quant
  emissions** (SHADOW watcher steps draining its final cycle); after stop,
  `data/signals.db` signal-row count stopped growing. The new service opens
  neither `signals.db` nor `quant.db`.
- New lock path is `data/collector.lock` (no overlap with the old
  `scanner.lock` after the old process stopped).

The checked-in example and the installed unit now identify the
collection-only service (see `altradar.service.example`, reproduced below).
The installed unit's `Description=` line was corrected to the collection-only
wording via a `daemon-reload` metadata refresh (which does not restart or
reconfigure the running service). The base unit's `ExecStart=` line was
intentionally **left untouched** so the running service was not disturbed;
the collection-only command is enforced at runtime by the **operator drop-in**,
not by the base unit.

## Reconciliation: example unit vs installed unit (verified from systemctl)

The cutover history above describes the **owner's drop-in edit and the
collection-only restart** that produced PID 1036944. Verified read-only from
`systemctl --user cat altradar.service` and `systemctl --user show altradar.service`,
the installed configuration is:

- **`altradar.service.example` (checked-in reference)** is collection-only:
  - `Description=ALT RADAR unattended bar/funding/sets/prices collector (read-only)`
  - `ExecStart=/usr/bin/python3 -m proto.service --collect-every 300`
  - Comment block instructs the owner to remove any leftover
    `--resolve-every` / `--quant-shadow` / `--no-scan` flags from the old unit.
- **Installed base unit (`~/.config/systemd/user/altradar.service`)**:
  - `Description=ALT RADAR unattended bar/funding/settles/prices collector (read-only)`
    — already the collection-only wording (corrected during the metadata refresh).
  - `ExecStart=/usr/bin/python3 -m proto.service --collect-every 300 --resolve-every 3600`
    — the **base unit still carries `--resolve-every 3600`**. This is the stale
    command baked into the file, but it is **not** what systemd runs.
- **Operator drop-in**
  (`~/.config/systemd/user/altradar.service.d/quant-shadow.conf`):
  - `ExecStart=` (clears the base unit's ExecStart),
  - `ExecStart=/usr/bin/python3 -m proto.service --collect-every 300`
    (collection-only override),
  - `TimeoutStopSec=600s` (preserved from the previous deployment for in-flight
    drain). This drop-in is an **active collection-only override**, not a
    leftover `--quant-shadow` holdover; the saved copy is retained as rollback.
- **Effective ExecStart** (from `systemctl --user show`):
  `argv[]=/usr/bin/python3 -m proto.service --collect-every 300` — the
  **drop-in wins** over the base unit. A `systemctl --user restart` keeps the
  collection-only command as long as the drop-in is present; the retired scanner
  path is **not** reintroduced by a restart. The scanner path returns only if the
  drop-in is removed (or its `ExecStart=` clear-line is deleted) **and** the base
  unit is restarted — both are out of scope here and no such action was taken.
- **Running process**: PID 1036944, argv
  `python3 -m proto.service --collect-every 300` — collection-only, healthy
  first cycle (561 coins, 1,659 new bars), no `scan:`/`quant:`/`emit_quant_shadow:`
  log lines.
- **Metadata refresh**: `systemd-analyze verify` + `systemctl --user
  daemon-reload` was run to refresh unit metadata (it updates the loaded
  `Description` shown by `systemctl status` and does **not** restart or
  reconfigure the running service). During this reconciliation the installed
  unit's `Description` line was corrected to
  `ALT RADAR unattended bar/funding/settles/prices collector (read-only)` to
  match the example; the base unit's `ExecStart` was intentionally **left
  untouched**. The running command was already collection-only via the drop-in,
  so no restart was needed to achieve the correct effective command.
  `ALL SUITES PASS (32 files)`, exit 0. (Was 6 failures on the deletion
  slice; resolved by adapting the collection-service tests and pruning the
  legacy quant/scanner suites.)
- Frontend: `npm run typecheck`, `npm run lint`, `npm run test` — all pass
  (5 Status-page tests).
- Protected data: unchanged (mtime proof above).
- `proto/research/**`: untouched; its 11 uncommitted review-fix changes are
  intact, and `tests/test_research_cli.py` still passes (import guard holds).
- No `from .scan` / `from .store` / `from .daemon` / etc. imports remain in
  `proto/`, `tests/`, or `scripts/` (grep confirmed empty).
- No scanner/signal/resolve routes, SSE bus, or SQLite imports remain in
  `proto/service.py` or `app/src/`.
- Live service: cutover performed by the owner as recorded above; the running
process is PID 1036944 with the collection-only argv
`python3 -m proto.service --collect-every 300`. A `daemon-reload` was run to
refresh the installed unit's `Description` metadata only (the running service
command and collected data were not changed). The base unit's `ExecStart`
still carries `--resolve-every 3600`, but the **operator drop-in clears it
(`ExecStart=`) and overrides it with the collection-only command** — this is
what systemd actually resolves to today (`systemctl --user show` confirms
`argv[]=/usr/bin/python3 -m proto.service --collect-every 300`). A
`systemctl --user restart` therefore keeps the collection-only command as
long as the drop-in is present; the scanner path is reintroduced only if the
drop-in is removed.

## Remaining references and classification

- `docs/superpowers/sdd/**`, `docs/quant-*.md`, `docs/hypotheses/*`,
  `docs/implementation-reports/quant-research-*.md`, `docs/QUANT_MODULE.md`:
  **historical documentation** of retired features — intentionally retained.
- `proto/quant/{report,gates,registry,qstore,champion,...}` and
  `tests/test_ws*_*.py`, `tests/test_quant_*`: **offline historical research
  toolkit**, not wired into the runtime. The `watch` command and the modules
  that performed live emission (`engine`, `signals`, `research_surface`,
  `selector`) were removed.
- `proto/decisions.py`, `proto/hypothesis_registry.py`,
  `proto/bar_quality.py`, `proto/bybit.py`, `proto/indicators.py`,
  `proto/barrier_math.py`, `proto/ratelimit.py`: **preserved reusable
  components** (venue adapters, audit math, indicators, sampling gate,
  hypothesis/decision registry) retained for the future quant interface.

What I skipped: I did not run the live service or the Tauri native build (the
Rust bundle still exists and was not touched). `proto/quant`'s offline report/
gates CLI remains historical; if you want it fully excised too, say so and I'll
prune it next.
