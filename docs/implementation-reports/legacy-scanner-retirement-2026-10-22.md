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
systemd unit altradar.service   (drop-in to be edited by owner)
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

The running service (PID 883661, argv
`python3 -m proto.service --collect-every 300 --resolve-every 3600 --quant-shadow`,
holding `data/signals.db`/`data/quant.db`/`data/scanner.lock`) was left
running. Cutover must be performed by the owner after reviewing the diff:

1. Inspect `git diff` / the implementation report above; confirm no live
   service file needs to ship as the new unit is already collection-only.
2. Edit ONLY the owner drop-in:
   `~/.config/systemd/user/altradar.service.d/quant-shadow.conf` (or the unit
   in `~/.config/systemd/user/altradar.service`) and remove the
   `--resolve-every`/`--quant-shadow` flags so `ExecStart` is
   ```
   /usr/bin/python3 -m proto.service --collect-every 300
   ```
   (keep `TimeoutStopSec=600s`).
3. Stop the old process **first** so `data/collector.lock` (and
   `data/scanner.lock`) is free:
   ```
   systemctl --user stop altradar.service
   # or, if run manually: kill the old PID
   ```
4. Reload and start:
   ```
   systemctl --user daemon-reload
   systemctl --user start altradar.service
   ```
5. Verify collection continuity:
   - `journalctl --user -u altradar.service -f` shows `collect:` lines.
   - `data/.bar_index.json` `max_ts`/`mtime` advance on the cadence.
   - No `scan:` / `quant:` / `emit_quant_shadow:` log lines appear.
   - `bar_count` (signal rows in `data/signals.db`) does not grow.
6. A saved copy of the old `quant-shadow.conf` drop-in serves as rollback.

Because the new service uses `data/collector.lock` (not `scanner.lock`), a
premature start before the old process stops will not corrupt archives
(`append_bars`/`collect_recent` deduplicate by timestamp), but both processes
should not run simultaneously.

## Verification

- Python offline suite: `python3 tests/run_all.py` →
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
- Live service process unchanged (argv confirmed at PID 883661).

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
