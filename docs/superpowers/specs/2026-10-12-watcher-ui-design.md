# Watcher Setup UI Design — setup view across all three front ends

**Date:** 2026-10-12
**Status:** Spec (Sections 1–4 approved in chat 2026-10-12)
**Sources:** watcher setup spec (`2026-10-12-watcher-setup-design.md`), `proto/watcher_report.py`, `gui/model.py:549-587` (`watcher_row_text`, `watcher_panel_lines`), `gui/app.py` (`RadarGUI`, workers, `tk.Text` detail), `proto/app.py` (`draw_table`/`draw_detail`), `qtgui/app.py` (`RadarWindow`, QTimer queue, table widgets).
**Scope:** Usability redesign, core problem = missing setup view. Additive watcher surfaces on TUI + tkinter + Qt; scanner views, ranking, flags, plans untouched. No live ranking change; research-labeled until holdout passes. README rules apply (read-only, `tests/run_all.py` green, no invented data, no edge claims).

## 1. Shared view-model

- New pure stdlib module `proto/watcher_view.py` (no GUI imports, no I/O). Input: `watcher_setup` rows + latest `watcher_eval` per setup + pause/disable flags + scalars from `watcher_report` helpers. Output: `WatcherPanel` dataclass with `groups` (counts + ordered ids per state: active, monitoring-unavailable, invalidated, expired, resolved), `selected` detail (rule, coin, dir, src bar ts, entry/stop/tgt, invalidation price, state, monitoring, missing/stale list, cost assumptions, EV_R/p̂_LB, shadow flag), `notices` (pause, disables, research label, no-edge disclaimer).
- Selection by setup id (`rule:coin:src_bar_ts`), never row index — stable across refreshes (the flicker fix).
- Empty states as explicit strings ("no active setups — silence is valid", "monitoring unavailable — N setups"). All state wording frozen here; front ends must not reword.
- Tests: synthetic rows — grouping, id-selection stability under reorder, empty/unavailable rendering, assumption-label presence.

## 2. Data flow

- Single read path per front end: open DB read-only (`mode=ro`) → `watcher_report` fetchers + `watcher_view.build_panel` → render. No direct table queries; no writes except pause/disable flags to `meta`.
- Refresh on each UI's existing cadence (TUI scan loop, tkinter workers, Qt QTimer queue). Panel shows `last closed bar` + `data as of`. Closed-bar rule holds; lagging feed → `monitoring=unavailable`, never invented freshness.
- Only actions: global pause + per-trigger disable (spec-mandated safety switches), persisted to `meta`, shown in `notices` on all three UIs. Otherwise display-only.

## 3. Surfaces

- **TUI:** `w` key toggles watcher screen reusing `draw_table`/`draw_detail` shapes (setup table + ENTER detail); `w` returns. Scanner keys untouched.
- **tkinter:** side panel (setup list via one-liners + `tk.Text` detail), fed by existing `Worker` pattern. Scan table/sort/flags untouched.
- **Qt:** `QDockWidget` (setup `QTableWidget` + detail), fed via queue/QTimer. Lands the Task 7-deferred Qt panel.
- All three: research banner ("research, shadow-only — scanner untouched; no orders") + correlated-opens line.

## 4. Errors, tests, rollout

- Errors (identical everywhere): DB missing/locked → "watcher data unavailable", scanner intact; zero setups → silence-valid line; all-unavailable → stale banner + `data as of`; paused → gray banner, setups listed-but-marked. Watcher failure never blanks/crashes the scanner view.
- Tests: view-model units in `run_all.py` (headless, temp-DB fixtures); per-front-end smoke tests (construct + panel presence, no display, following `test_gui.py`/`test_qtgui.py` headless pattern); no network. Visual pass is a manual checklist (three reads), not automated.
- Rollout: shared model → TUI → tkinter → Qt. Additive, no flags; scanner UI unchanged until the operator uses the new surfaces.

## Open decisions

- Final `EV_hurdle`/K values (predeclared before holdout, shown as-is until then).
- Whether pause/disable needs operator confirmation affordance (default: immediate + notice line).
