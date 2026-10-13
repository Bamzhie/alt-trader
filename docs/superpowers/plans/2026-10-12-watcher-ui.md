# Watcher Setup UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a stable setup view (watcher setups, lifecycle states, rationale) to the TUI, tkinter GUI, and Qt GUI behind one shared tested view-model.

**Architecture:** New pure stdlib `proto/watcher_view.py` builds a `WatcherPanel` from DB rows; each front end adds one thin additive surface on its existing patterns (TUI `w` screen, tkinter side panel via `Worker`, Qt `QDockWidget` via queue/QTimer). Scanner views untouched.

**Tech Stack:** Python 3.10+ stdlib, tkinter, curses, PySide6 (offscreen tests), SQLite read-only.

**Spec:** `docs/superpowers/specs/2026-10-12-watcher-ui-design.md` (commit `ac94474`; executors read both).

## Global Constraints

- Read-only stays read-only — no order placement, no key storage, no execution code, ever.
- Scanner views, ranking, flags, plans untouched; watcher surfaces are additive and research-labeled until holdout passes.
- Every displayed number traces to a venue response or a logged row; estimates labeled estimates.
- Closed bars only; a lagging feed renders `monitoring=unavailable`, never invented freshness.
- No profitability or edge claims anywhere in UI copy.
- `python3 tests/run_all.py` must stay green; ZERO network in UI tests (fake cards, temp-DB SQLite).

## Review Focus

- Setup list reorders between refreshes and selection jumps to another setup — expect selection pinned to setup id, never row index, with a test that reorders input rows.
- Watcher DB locked or missing makes the whole UI blank or crash — expect a contained "watcher data unavailable" panel with the scanner intact.
- A front end rewords a lifecycle state ("done" vs "resolved") — expect byte-identical state wording from the shared module on all three surfaces.
- Pause/disable written by one front end invisible to the others — expect flags persisted to `meta` and read on every panel build.
- Stale feed shown as live — expect `data as of` + last-closed-bar timestamps on every surface, with a stale banner past the lag threshold.

---

### Task 1: Shared view-model

**Files:**
- Create: `proto/watcher_view.py`
- Test: `tests/test_watcher_view.py`

**Interfaces:**
- Consumes: `watcher_setup` rows as dicts (spec §4 columns), latest `watcher_eval` per setup as dicts, `paused: bool`, `disabled: tuple[str,...]`; pure helpers `gui.model.watcher_row_text`, `watcher_report.correlated_opens` (call, don't copy).
- Produces:
  - `WatcherPanel` dataclass: `groups: dict[str, list[str]]` (keys `active`, `unavailable`, `invalidated`, `expired`, `resolved`; values ordered setup ids), `by_id: dict[str, dict]`, `selected_id: str | None`, `notices: list[str]`, `asof_ts: int`, `last_bar_ts: int | None`
  - `build_panel(rows, evals, selected_id=None, paused=False, disabled=()) -> WatcherPanel`
  - `RESEARCH_BANNER = "WATCHER (research, shadow-only — scanner untouched; no orders)"`
  - `STATE_WORDS = {"active": "active", "invalidated": "invalidated", "expired": "expired", "resolved": "resolved"}` (front ends must render these verbs verbatim)

- [ ] **Step 1: Write the failing test**

```python
def test_selection_survives_reorder():
    p1 = build_panel([row_b, row_a], {}, selected_id="r:AAA:1")
    p2 = build_panel([row_a, row_b], {}, selected_id="r:AAA:1")
    assert p1.selected_id == p2.selected_id == "r:AAA:1"
    assert detail(p1) == detail(p2)
```

plus: grouping counts per state; unknown selected_id falls back to first active (or None when empty + "no active setups — silence is valid" notice); all-`unavailable` adds stale notice with `asof_ts`; paused adds paused notice; research banner always first notice.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_watcher_view.py`
Expected: FAIL with "No module named 'proto.watcher_view'" (or ImportError).

- [ ] **Step 3: Implement `build_panel` + `WatcherPanel` + constants in `proto/watcher_view.py`**

Pure stdlib, no GUI imports, no I/O. Detail dict per setup: rule, coin, direction, src_bar_ts, entry, stop_px, tgt_px, invalid_price, state (verbatim from STATE_WORDS), monitoring, missing list, cost dict, ev_r, p_lb, shadow_selected.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_watcher_view.py`
Expected: ALL PASS; then `python3 tests/run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add proto/watcher_view.py tests/test_watcher_view.py
git commit -m "feat(ui): shared watcher view-model with id-stable selection"
```

### Task 2: Read-only feed + pause/disable flags

**Files:**
- Create: `proto/watcher_feed.py`
- Test: `tests/test_watcher_feed.py`

**Interfaces:**
- Consumes: Task 1 (`build_panel`); `proto/store.py` `Store` (read-only use only); spec §4 tables.
- Produces:
  - `open_ro(db_path: str) -> sqlite3.Connection` (`file:...?mode=ro`, missing file raises `WatcherUnavailable`)
  - `fetch_setups(conn) -> list[dict]` (empty list when tables absent — never raises on fresh DB)
  - `fetch_latest_evals(conn) -> dict[str, dict]`
  - `get_flags(conn) -> (paused: bool, disabled: tuple)` (reads `meta` keys `watcher_paused`, `watcher_disabled:<rule>`; absent → `(False, ())`)
  - `set_paused(conn_rw, paused: bool)`, `set_rule_disabled(conn_rw, rule: str, disabled: bool)` (only writers; UIs call these on user action)
  - `load_panel(db_path, selected_id=None) -> WatcherPanel` (composes open+fetch+flags+build; maps any sqlite error to `WatcherUnavailable`)

- [ ] **Step 1: Write the failing test**

```python
def test_missing_db_is_contained():
    with pytest_raises(WatcherUnavailable):
        load_panel("/nonexistent/x.db")
def test_fresh_db_is_empty_panel():
    assert load_panel(temp_empty_db()).groups["active"] == []
```

plus: flags round-trip via set/get on temp DB; locked-DB read maps to `WatcherUnavailable` (or documents why untestable — then the UI fallback test in Tasks 3–5 covers it).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_watcher_feed.py`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement in `proto/watcher_feed.py`**

stdlib sqlite3 only. `fetch_setups` selects exactly the spec §4 columns by name (tolerate missing `trail_extra` columns from older DBs via PRAGMA check).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_watcher_feed.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add proto/watcher_feed.py tests/test_watcher_feed.py
git commit -m "feat(ui): read-only watcher feed with contained failures"
```

### Task 3: TUI watcher screen

**Files:**
- Modify: `proto/app.py` (additive: `w` key, `draw_watcher` + `draw_watcher_detail`; no changes to existing draw/sort/key code)
- Test: `tests/test_app_watcher.py`

**Interfaces:**
- Consumes: Tasks 1–2 (`load_panel`, `WatcherPanel`, `STATE_WORDS`).
- Produces: `App.show_watcher: bool`, `App.watcher_selected: str | None`; `draw_watcher(stdscr, h, w)`, `draw_watcher_detail(stdscr, h, w)` following `draw_table`/`draw_detail` shapes.

- [ ] **Step 1: Write the failing test**

```python
def test_w_key_toggles_watcher_screen():
    app.on_key(ord("w")); assert app.show_watcher is True
    app.on_key(ord("w")); assert app.show_watcher is False
def test_detail_shows_verbatim_state():
    out = render_to_fake_stdscr(app_with_one_resolved_setup)
    assert "resolved" in out and "research, shadow-only" in out
```

Fake stdscr records `addstr` calls (no curses init; `curses.A_BOLD` constants need no screen). Key handling: if `on_key` doesn't exist as a seam, drive the same branch the main loop uses — the test names the seam it found.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_app_watcher.py`
Expected: FAIL (no `show_watcher` / no `draw_watcher`).

- [ ] **Step 3: Implement in `proto/app.py`**

Additive only. Table columns: id-short, rule, coin, dir, state (verbatim), entry→stop/tgt. ENTER opens detail; `w`/ESC returns. Scanner keys, sorts, logging untouched.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_app_watcher.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add proto/app.py tests/test_app_watcher.py
git commit -m "feat(ui): TUI watcher screen behind w key"
```

### Task 4: tkinter watcher panel

**Files:**
- Modify: `gui/app.py` (additive panel + detail; existing table/sort/workers untouched), `gui/model.py` (only if a pure helper is missing — prefer `watcher_view` output)
- Test: `tests/test_gui_watcher.py` (MODEL pure tests always run; WIDGET tests skip cleanly without display, following `test_gui.py` HAS_DISPLAY pattern)

**Interfaces:**
- Consumes: Tasks 1–2. Produces: `RadarGUI.watcher_list` (listbox), `RadarGUI.watcher_detail` (`tk.Text`), refreshed by the existing `Worker` thread pattern.

- [ ] **Step 1: Write the failing test**

```python
def test_panel_lists_active_first():
    panel = build_panel(fixture_rows, {}, paused=False)
    assert gui_watcher_order(panel) == ["active-id-1", ...]
def test_widget_builds_or_skips():
    # HAS_DISPLAY gate: builds RadarGUI and asserts watcher_list exists, else SKIP
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_gui_watcher.py`
Expected: FAIL (no watcher widgets / helpers).

- [ ] **Step 3: Implement in `gui/app.py`**

Side panel beside scan table; selection drives `watcher_detail` from `WatcherPanel.selected`; pause/disable via small buttons calling Task 2 writers; worker-thread fetch with None-sentinel retire in tests.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_gui_watcher.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add gui/app.py gui/model.py tests/test_gui_watcher.py
git commit -m "feat(ui): tkinter watcher side panel"
```

### Task 5: Qt watcher dock

**Files:**
- Modify: `qtgui/app.py` (additive `QDockWidget`; table/sort/workers untouched)
- Test: `tests/test_qtgui_watcher.py` (`QT_QPA_PLATFORM=offscreen` first, PySide6-missing skip guard, following `test_qtgui.py`)

**Interfaces:**
- Consumes: Tasks 1–2. Produces: `RadarWindow.watcher_dock` with setup `QTableWidget` + detail pane, fed via the existing queue→QTimer poll.

- [ ] **Step 1: Write the failing test**

```python
def test_dock_columns_match_tui():
    headers = [dock.table.horizontalHeaderItem(i).text() for i in range(dock.table.columnCount())]
    assert headers == ["ID", "RULE", "COIN", "DIR", "STATE", "ENTRY", "STOP", "TGT"]
def test_state_text_is_verbatim():
    assert cell_text == "resolved"  # STATE_WORDS, not a paraphrase
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_qtgui_watcher.py`
Expected: FAIL (no `watcher_dock`).

- [ ] **Step 3: Implement in `qtgui/app.py`**

Dock with table + detail; selection by setup id; pause/disable actions; lands the Task 7-deferred Qt panel. This closes the `qtgui` deferral note — remove or update that code comment.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_qtgui_watcher.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add qtgui/app.py tests/test_qtgui_watcher.py
git commit -m "feat(ui): Qt watcher dock widget"
```

### Task 6: Cross-surface consistency + visual pass

**Files:**
- Test: `tests/test_watcher_ui_parity.py`
- Docs: `docs/GUI_MANUAL.md` (short watcher-view section)

**Interfaces:**
- Consumes: Tasks 1–5. Produces: parity proof + manual checklist record.

- [ ] **Step 1: Write the failing test**

```python
def test_all_surfaces_share_state_words():
    assert tui_state_text("resolved") == qt_state_text("resolved") == tk_state_text("resolved") == "resolved"
def test_pause_notice_on_all_surfaces():
    for surface in (tui, tk, qt): assert "paused" in surface.notices_when(paused=True)
```

Derive each surface's text from its real render path (fake stdscr / pure model / offscreen Qt), not duplicated literals.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_watcher_ui_parity.py`
Expected: FAIL (helper missing).

- [ ] **Step 3: Implement the parity helpers + manual section in `docs/GUI_MANUAL.md`**

Checklist in the manual: open each surface against a fixture DB, screenshot/read, confirm states/rationale/pause visible. Record the pass date + fixture in the manual.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_watcher_ui_parity.py`, then full `python3 tests/run_all.py` — ALL SUITES PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_watcher_ui_parity.py docs/GUI_MANUAL.md
git commit -m "feat(ui): cross-surface parity tests and manual checklist"
```

## Self-Review

- Spec coverage: §1 view-model → Task 1; §2 read path/refresh/actions → Task 2 (+ surfaces); §3 TUI/tk/Qt → Tasks 3/4/5; §4 errors → Tasks 1–2 + surfaces, tests → each task, rollout order → task order (model → TUI → tk → Qt). Open decisions carried: EV_hurdle/K shown as-is; pause/disable immediate + notice.
- Step scan: each test step pins names/assertions; code steps pin signatures/files; no TBD.
- Type consistency: `WatcherPanel.groups/by_id/selected_id/notices/asof_ts/last_bar_ts`, `STATE_WORDS` verbs, `load_panel(db_path, selected_id=None)`, `(paused, disabled)` tuple shape shared across tasks.
- Review Focus: all five lines owned (id-selection→T1, contained failure→T2, verbatim wording→T6 parity, flags→T2, staleness→T1 notices + surfaces).
- Proportion: signatures + assertions, no bodies; plan shorter than the code it will produce.
