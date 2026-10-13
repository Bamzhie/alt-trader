# Qt Terminal Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Qt front end's chrome around the existing tested helpers so the dashboard matches the reference mockup, with zero backend changes.

**Architecture:** Pure visual/structural rebuild inside `qtgui/` only: theme-token swap, new top bar / rail / overview widgets, QPainter sparklines + gauge, 3-region grid restructure. All data flows reuse existing helpers (`format_dashboard_cards`, `build_dashboard_rows`, `detail_metrics`, `correlated_section_lines`, feed `load_panel`); engine, scan, watcher, store untouched.

**Tech Stack:** Python 3.10+ stdlib, PySide6 (QPainter, QSS), offscreen PNG verification, self-checking script tests via `tests/run_all.py`.

**Spec:** Operator-pasted dashboard spec (21 sections; not in repo — executors follow this plan's pinned values) as modified by binding rulings in `docs/superpowers/specs/2026-10-13-trader-dashboard-design.md` (commit `2089540`, which overrides the pasted spec wherever they conflict).

## Global Constraints

- Frontend-only: no new endpoints, no contract changes, no new backend services, no extra API calls for decoration, no engine/lifecycle changes.
- Binding overrides from spec 2089540: NO confidence % bars, tiers, or probability language anywhere; evidence-indicator cells instead. No 1m timeframe. No TOTAL market-cap row. No leverage headroom. Trades hypothetical-only.
- No invented data: missing values render `—`/unavailable states, never fabricated prices, scores, or history.
- Read-only stays read-only — no orders, no keys, no execution code.
- `python3 tests/run_all.py` must stay green; ZERO network in UI tests (offscreen + skip-guard + tripwire per `test_qtgui.py`).

## Review Focus

- A confidence %, tier word, or probability claim ships on a new surface — expect `assert_clean` + banned-tuple tests to fail on that surface.
- A sparkline/gauge renders fabricated points when history is short — expect flat unavailable rendering, never invented points.
- Bell icon added with no backing feature — expect omission; icons without actions are a defect.
- Countdown implies a backend schedule that doesn't exist — expect countdowns sourced only from scan interval, barrier math, or sweep state.
- Layout overflows or clips at 1536×1024 — expect the offscreen PNG checklist to catch it per region.

---

### Task 1: Theme tokens + QSS pass

**Files:**
- Modify: `qtgui/theme.py`
- Test: `tests/test_qtgui_theme.py`

**Interfaces:**
- Consumes: pasted spec §3 (exact hexes below).
- Produces: `BG_MAIN="#030B15"`, `BG_SIDEBAR="#06111F"`, `BG_CARD="#071522"`, `BG_ELEVATED="#091A2A"`, `BORDER="#142A3D"`, `INK="#E6F0FF"`, `INK_DIM="#8EA8C4"`, `INK_MUT="#627B96"`, `ACC_POS="#00D6AD"`, `ACC_2="#8B5CF6"`, `ACC_NEG="#FF4D5A"`, `GRID="#14273A"` + QSS consuming them (8–12px radii, thin borders, no heavy shadows).

- [ ] **Step 1: Write the failing test**

```python
def test_palette_tokens():
    assert theme.BG_MAIN == "#030B15" and theme.ACC_POS == "#00D6AD"  # all 12
def test_qss_references_tokens():
    assert "#030B15" not in theme.qss()  # no hardcoded hexes outside tokens
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_qtgui_theme.py`
Expected: FAIL (tokens differ).

- [ ] **Step 3: Implement token swap + QSS re-tint in `qtgui/theme.py`**

Keep every token name referenced elsewhere compiling; map old names to new values where names differ.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_qtgui_theme.py`, then `python3 tests/run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add qtgui/theme.py tests/test_qtgui_theme.py
git commit -m "feat(qt): spec palette tokens with thin-border dark QSS"
```

### Task 2: 3-region layout restructure

**Files:**
- Modify: `qtgui/app.py` (main-window layout only)
- Test: `tests/test_qtgui_layout.py` (offscreen + skip-guard)

**Interfaces:**
- Consumes: Task 1 tokens.
- Produces: rail 140–150px, header 48–52px, center 60–65% / right 32–35% split via flexible grid (no absolute positioning); right panel collapses to a drawer below center under ~1100px width, never horizontal page overflow.

- [ ] **Step 1: Write the failing test**

```python
def test_regions():
    assert 140 <= win.rail.width() <= 150 and 48 <= win.header.height() <= 52
def test_narrow_collapses():
    win.resize(900, 1024); assert win.detail_dock.isHidden() or win.drawer_available()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_qtgui_layout.py`
Expected: FAIL (rail is 88px).

- [ ] **Step 3: Implement layout in `qtgui/app.py`**

Move existing widgets into the grid; no widget logic changes.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_qtgui_layout.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add qtgui/app.py tests/test_qtgui_layout.py
git commit -m "feat(qt): three-region terminal grid with collapsible detail"
```

### Task 3: Top bar + sidebar

**Files:**
- Modify: `qtgui/app.py` (topbar + rail only)
- Test: `tests/test_qtgui_chrome.py` (offscreen + skip-guard)

**Interfaces:**
- Consumes: Tasks 1–2; venue health + scan timing for real states.
- Produces: topbar (logo, name, subtitle, SCANNING dot + elapsed/last-scan from real timing, MEXC identity, UTC clock; bell ONLY if an event source exists else omitted; gear → Settings; native window controls, no custom ones), rail (Dashboard/Watchlist/Signals/Analysis/Trades/Settings with selected highlight + green edge; bottom API-status/venue/account blocks from real connection state, never hardcoded Connected).

- [ ] **Step 1: Write the failing test**

```python
def test_no_dead_icons():
    assert window_icon_has_action("bell") is False  # omitted unless backed
def test_status_is_real():
    assert status_block_text() != "Connected" or venue_health_ok() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_qtgui_chrome.py`
Expected: FAIL.

- [ ] **Step 3: Implement topbar + rail in `qtgui/app.py`**

Reuse existing rail/stack switching; elapsed time only from real scan timestamps.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_qtgui_chrome.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add qtgui/app.py tests/test_qtgui_chrome.py
git commit -m "feat(qt): terminal top bar and status sidebar"
```

### Task 4: KPI cards with sparklines + gauge

**Files:**
- Modify: `qtgui/app.py` (cards row only)
- Test: `tests/test_qtgui_cards.py` (offscreen + skip-guard)

**Interfaces:**
- Consumes: Tasks 1–3; `format_dashboard_cards` values (binding set: Active Setups, Eligible Perps, Monitoring Coverage, New Setups — NOT High-Conviction/Avg-Confidence).
- Produces: 4 cards in one row with QPainter sparklines (24×5m closes; flat unavailable state when short) + semicircular gauge (coverage value); deltas from snapshot pairs or "comparison unavailable".

- [ ] **Step 1: Write the failing test**

```python
def test_no_confidence_cards():
    assert "High Conviction" not in cards_text() and "Avg. Confidence" not in cards_text()
def test_short_history_no_fabrication():
    assert sparkline_points(cards, short_history) in ([], "unavailable")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_qtgui_cards.py`
Expected: FAIL.

- [ ] **Step 3: Implement cards row in `qtgui/app.py`**

Pure painters over existing values; no new data fetching.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_qtgui_cards.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add qtgui/app.py tests/test_qtgui_cards.py
git commit -m "feat(qt): KPI cards with honest sparklines and gauge"
```

### Task 5: Signals table fidelity

**Files:**
- Modify: `qtgui/app.py` (dashboard table only)
- Test: `tests/test_qtgui_sigtable.py` (offscreen + skip-guard)

**Interfaces:**
- Consumes: Tasks 1–4; `build_dashboard_rows` + `summarize_setup` + `assert_clean`.
- Produces: columns coin/price/24h/trend-sparkline/analysis/evidence/LONG-SHORT-pill/expiry/chevron; filters All/Long/Short + Market + Horizon + Sort(newest); row click → detail; selected-row highlight; letter-badge icons; every cell string through `assert_clean`.

- [ ] **Step 1: Write the failing test**

```python
def test_columns_in_order():
    assert headers() == ["COIN", "PRICE", "24H", "TREND", "ANALYSIS", "EVIDENCE", "SIGNAL", "EXPIRES", ""]
def test_row_click_selects_detail():
    click(row(2)); assert detail_coin() == row_coin(2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_qtgui_sigtable.py`
Expected: FAIL (headers differ).

- [ ] **Step 3: Implement table in `qtgui/app.py`**

Reuse existing table machinery; add sparkline + chevron delegates.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_qtgui_sigtable.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add qtgui/app.py tests/test_qtgui_sigtable.py
git commit -m "feat(qt): mockup-fidelity signals table"
```

### Task 6: Market Overview + detail panel

**Files:**
- Modify: `qtgui/app.py` (right column only)
- Test: `tests/test_qtgui_overview.py` (offscreen + skip-guard)

**Interfaces:**
- Consumes: Tasks 1–5; tickers (BTC/ETH), `detail_metrics`, klines chart widget.
- Produces: Market Overview card (BTC + ETH rows with sparklines; NO total row — assert absent); detail header (badge, symbol, name, price, 24h%, pill, sparkline); tabs 5m/15m/1H/4H/1D (assert no 1m); candle + volume + EMA chart reusing existing widget; Analysis/Trade Plan/Metrics tabs; entry/TP/SL + RSI/MACD/EMA50/EMA200/Volume (present only when provided); validity + next-update lines; View Full Analysis scrolls detail.

- [ ] **Step 1: Write the failing test**

```python
def test_no_total_row_no_1m():
    assert "TOTAL" not in overview_text() and "1m" not in tab_labels()
def test_detail_selects_from_table():
    select_setup("BTC"); assert detail_header().startswith("BTC")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_qtgui_overview.py`
Expected: FAIL.

- [ ] **Step 3: Implement right column in `qtgui/app.py`**

Restyle/reparent existing widgets; no new data fetching beyond existing paths.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_qtgui_overview.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add qtgui/app.py tests/test_qtgui_overview.py
git commit -m "feat(qt): market overview and asset detail column"
```

### Task 7: Bottom row + visual verification

**Files:**
- Modify: `qtgui/app.py` (bottom row only)
- Create: `scripts/render_dashboard.py` (offscreen 1536×1024 PNG renderer, fixture DB; NOT shipped UI code)
- Test: `tests/test_qtgui_bottom.py` (offscreen + skip-guard)

**Interfaces:**
- Consumes: Tasks 1–6; journal rows, mover sorts.
- Produces: Market Movers (Gainers/Losers/High Volume), Recent Analysis (last 5), Activity Log (real events or empty-state copy); `scripts/render_dashboard.py` saving `region_*.png` per region (topbar, rail, cards, table, overview, detail, bottom) + `full_1536x1024.png`; manual checklist in `docs/GUI_MANUAL.md` recording per-region pass/fail vs the mockup.

- [ ] **Step 1: Write the failing test**

```python
def test_bottom_row_real_or_empty():
    assert "no activity yet" in activity_text(empty_db) and "scan completed" in activity_text(seeded_db)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_qtgui_bottom.py`
Expected: FAIL.

- [ ] **Step 3: Implement bottom row; write render script; render PNGs and fix spacing/overflow/clipping found**

Render, inspect pixel output for overflow/clip/contrast per region, fix, re-render until clean.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_qtgui_bottom.py`, then full `run_all.py` — ALL SUITES PASS.

- [ ] **Step 5: Commit**

```bash
git add qtgui/app.py scripts/render_dashboard.py docs/GUI_MANUAL.md tests/test_qtgui_bottom.py
git commit -m "feat(qt): bottom row panels with offscreen visual verification"
```

## Self-Review

- Spec coverage: pasted §3→T1; §4+§19→T2; §5+§6→T3; §7→T4 (binding metric set); §8→T5; §9+§10+§11–14→T6; §15→T7; §16 structure via additive tasks; §17/§18 enforced per-task + reviews; §20→T7 renders + run_all; §21→this plan's task order. Binding overrides (no confidence, no 1m, no TOTAL, no headroom, hypothetical Trades) pinned in T4–T6 tests.
- Step scan: test steps pin names/assertions; code steps pin signatures/files; no TBD.
- Type consistency: token names, `build_dashboard_rows`, `summarize_setup`/`assert_clean`, `dash_market_data` seam shared verbatim across tasks.
- Review Focus: all five lines owned (banned copy→T4–T6 asserts; fabricated points→T4; dead icons→T3; fake countdowns→T6 validity lines; overflow→T7 PNGs).
- Proportion: signatures + assertions only; no bodies.
