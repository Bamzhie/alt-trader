# Trader Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Qt trader dashboard (setup-gated signals, rule-evidence display, stakeless model) on the revised spec with no ML/probability outputs anywhere.

**Architecture:** Epoch-gated scan writes + append-only epoch records; watcher tables plus `watcher_events` journal and `dashboard_snapshot` stay the only post-cut writers; one shared evidence-copy helper feeds a Qt dashboard (cards, setup table, detail/chart, bottom row, six rail pages). Scanner engine code is gated, never deleted.

**Tech Stack:** Python 3.10+ stdlib, PySide6 (offscreen tests), SQLite, self-checking script tests via `tests/run_all.py`.

**Spec:** `docs/superpowers/specs/2026-10-13-trader-dashboard-design.md` (commit `2089540`; executors read both).

## Global Constraints

- Read-only stays read-only — no order placement, no key storage, no execution code, ever.
- No ML scorer or probability output: never compute or display `p̂_LB`, `EV_R`, confidence scores, HIGH/STANDARD/SPECULATIVE tiers, or EV-selected labels in this release.
- No invented data: every number traces to a venue response or logged row; assumptions labeled; missing data renders shorter honest copy.
- Closed bars only; lagging feeds render `monitoring=unavailable`, never invented freshness.
- No profitability or edge claims; Trades page is hypothetical-only ("No orders placed — hypothetical outcomes, not P&L").
- `python3 tests/run_all.py` must stay green; ZERO network in UI tests.

## Review Focus

- A surface shows a probability-like number or tier word — expect the banned-wording test to fail on "high conviction", "%", "probability", "confidence", "EV-selected".
- Post-cut scan still writes a signal row — expect the epoch-gate test to fail on any `signal_log`/`coin_state`/`plan_log` write while a watcher-only epoch is active.
- Rolling-24h comparison invented without snapshots — expect "comparison unavailable", never an inferred value.
- Pre-cut and post-cut rows pooled in one cohort — expect epoch-separated denominators everywhere.
- Stale feed presented as live — expect `data as of` + last-closed-bar stamps, never assumed freshness.

---

### Task 1: Epoch records + scan gate

**Files:**
- Modify: `proto/store.py` (epoch table + pointer), `proto/scan.py` + `proto/app.py` (write gate), `proto/measurement.py` (policy constant `mexc_full_stakeless_v2`)
- Test: `tests/test_epoch_cut.py`

**Interfaces:**
- Consumes: existing `Store`/`meta` pattern.
- Produces:
  - `append_epoch(conn, mode: str) -> str` (writes immutable `(epoch_id, mode, cut_ts)`, updates active pointer; rollback = new row, never overwrite)
  - `active_epoch(conn) -> dict`
  - `writes_signals(conn) -> bool` (False while watcher-only epoch active)
  - `MEASUREMENT_UNIVERSE_POLICY = "mexc_full_stakeless_v2"`

- [ ] **Step 1: Write the failing test**

```python
def test_watcher_only_blocks_signal_writes():
    eid = append_epoch(conn, "watcher-only")
    assert writes_signals(conn) is False
    assert append_epoch(conn, "scan") != eid  # rollback is a new row
    assert writes_signals(conn) is True
```

plus: pre-cut rows keep their epoch label; cohort query separates epochs (no pooling).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_epoch_cut.py`
Expected: FAIL (no `append_epoch`).

- [ ] **Step 3: Implement in `proto/store.py`; gate the three write call sites; bump the policy constant**

One `if writes_signals(...)` at each of `log_signal`, `upsert_current`, plan-attach call sites. No deletions.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_epoch_cut.py`, then `python3 tests/run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add proto/store.py proto/scan.py proto/app.py proto/measurement.py tests/test_epoch_cut.py
git commit -m "feat(dash): immutable epochs with watcher-only write gate"
```

### Task 2: Event journal + dashboard snapshots

**Files:**
- Create: `proto/watcher_journal.py`
- Modify: `proto/watcher.py` (append transition writes only)
- Test: `tests/test_watcher_journal.py`

**Interfaces:**
- Consumes: Task 1 (epoch helpers for cohort labels, not gating).
- Produces:
  - `ensure_journal(conn)` (`watcher_events(ts, kind, coin, text)`, kinds emission/invalidation/expiry/sweep-complete)
  - `log_event(conn, kind, coin, text)` (prune oldest beyond 200 rows)
  - `JOURNAL_CAP = 200`
  - `write_snapshot(conn, eligible_count, evaluated_count, active_setups, new_setups_24h, monitoring_coverage)` (`dashboard_snapshot(...)`)
  - `snapshot_pair(conn, now) -> (latest, prior_or_None)` (prior = nearest at or before `now-24h`, else None)

- [ ] **Step 1: Write the failing test**

```python
def test_cap_prunes_oldest():
    for i in range(205): log_event(conn, "emission", "C", f"e{i}")
    assert count(conn) == 200 and oldest_text(conn) == "e5"
def test_pair_without_history():
    assert snapshot_pair(fresh_conn, 9999)[1] is None  # caller shows unavailable
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_watcher_journal.py`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement in `proto/watcher_journal.py`; hook transition writes in `proto/watcher.py` lifecycle**

Append-only hooks at the existing transition points (emit/invalidate/expire); sweep-complete written by the scan loop.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_watcher_journal.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add proto/watcher_journal.py proto/watcher.py tests/test_watcher_journal.py
git commit -m "feat(dash): capped event journal and snapshot pairs"
```

### Task 3: Stakeless universe + reference plans

**Files:**
- Modify: `proto/scan.py` (universe), `proto/planner.py` (reference display helper only), `proto/app.py` (sweep progress)
- Test: `tests/test_stakeless.py`

**Interfaces:**
- Consumes: Tasks 1–2 (epoch label for cohort tests only).
- Produces:
  - `build_universe()` returns all eligible MEXC USDT perps (synthetic filter kept; stake groups/budget/rotation-tail removed; sweep window walks the full set with `(done, total)` progress)
  - `min_notional` populated, never consulted for inclusion/flags
  - `reference_plan(levels) -> dict` (100 USDT reference notional; levels, R-multiples, stop-risk; NO leverage headroom, NO liquidation claims)
  - `REFERENCE_NOTIONAL = 100.0`

- [ ] **Step 1: Write the failing test**

```python
def test_no_stake_gating():
    uni = build_universe(stake=None)  # signature must not require stake
    assert low_min_notional_coin in uni and "tradeable" not in gating
def test_reference_plan_has_no_leverage():
    assert "leverage" not in reference_plan(levels) and reference_plan(levels)["notional"] == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_stakeless.py`
Expected: FAIL.

- [ ] **Step 3: Implement; keep removed code paths deleted only where stake-exclusive, otherwise gated**

Settings stake control removed in Task 7; here keep the plumbing compiling.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_stakeless.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add proto/scan.py proto/planner.py proto/app.py tests/test_stakeless.py
git commit -m "feat(dash): stakeless universe with reference-notional plans"
```

### Task 4: Rule-evidence copy

**Files:**
- Create: `proto/setup_copy.py`
- Test: `tests/test_setup_copy.py`

**Interfaces:**
- Consumes: watcher_setup rows as dicts; `indicators.py` values passed in (no computation here).
- Produces:
  - `summarize_setup(setup, inputs) -> (line1: str, line2: str)` (rule criteria + computed inputs only; shorter honest copy when inputs missing)
  - `blocking_reason(coin, ctx) -> str` ("no setup yet" reason: veto / cooldown / missing data / recently invalidated + last-eval ts)
  - `BANNED_WORDS = ("vwap", "high probability", "high conviction", "confidence", "probability", "confirmed profit", "fill", "executed", "leverage")` (case-insensitive test helper `assert_clean(text)`)
  - `badge_letters(coin: str) -> str` (1–2 letters for drawn disc badges)

- [ ] **Step 1: Write the failing test**

```python
def test_banned_words_rejected():
    for w in ("VWAP support", "High conviction long", "88% confidence"):
        assert_clean_raises(w)
def test_missing_inputs_shorten():
    assert "unavailable" in summarize_setup(setup, {"oi": None})[1].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_setup_copy.py`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement in `proto/setup_copy.py`**

Pure stdlib. Copy cites triggering rule, direction, passed criteria, confirmations, availability/staleness. Never probability language.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_setup_copy.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add proto/setup_copy.py tests/test_setup_copy.py
git commit -m "feat(dash): rule-evidence copy with banned-wording guard"
```

### Task 5: Dashboard cards + Top Signals table (Qt)

**Files:**
- Modify: `qtgui/app.py` (additive dashboard page widgets)
- Test: `tests/test_qtgui_dashboard.py` (offscreen + skip-guard per `test_qtgui.py`)

**Interfaces:**
- Consumes: Tasks 1–4 (`snapshot_pair`, `build_panel`-compatible rows, `summarize_setup`, `assert_clean`).
- Produces: Dashboard page — 4 stat cards (Active Setups, Eligible Perps, Monitoring Coverage, New Setups 24h; eligible vs evaluated shown separately; snapshot comparison or "comparison unavailable"), Top Signals table (All/Long/Short + Market All/Top20/Tail + Horizon 4h/24h default 4h + Sort newest/Expiry/24h%), evidence-indicator cells (NO tier/score columns), expiry countdowns, research banner.

- [ ] **Step 1: Write the failing test**

```python
def test_no_probability_copy_on_dashboard():
    assert_clean(window_text())  # full-page text through the banned list
def test_withheld_comparison_when_no_history():
    assert "comparison unavailable" in cards_text(fresh_db)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_qtgui_dashboard.py`
Expected: FAIL (no dashboard page).

- [ ] **Step 3: Implement in `qtgui/app.py`**

Reuse rail/`QStackedWidget`/queue/QTimer patterns; QPainter sparklines + gauge reuse prior chart helpers where present. Scanner Qt views untouched.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_qtgui_dashboard.py`, then `run_all.py` green (both interpreters per prior pattern).

- [ ] **Step 5: Commit**

```bash
git add qtgui/app.py tests/test_qtgui_dashboard.py
git commit -m "feat(dash): Qt dashboard cards and setup table"
```

### Task 6: Coin detail + chart (Qt)

**Files:**
- Modify: `qtgui/app.py` (additive detail pane + chart)
- Test: `tests/test_qtgui_detail.py` (offscreen + skip-guard)

**Interfaces:**
- Consumes: Tasks 1–4 (`mexc.klines` live only in app code, never tests — tests use fixture bars); `indicators.py` RSI/MACD/EMA50/EMA200/volume.
- Produces: detail pane — tabs 5m/15m/1H/4H/1D (no 1m), candles + EMA50/200 + volume, Analysis/Trade Plan/Metrics tabs, validity + next-update lines, on-demand reference plan, no-setup state with `blocking_reason`.

- [ ] **Step 1: Write the failing test**

```python
def test_no_setup_state():
    assert "no setup yet" in detail_text(coin_without_setup).lower()
def test_timeframe_tabs_have_no_1m():
    assert "1m" not in tabs and set(tabs) == {"5m", "15m", "1H", "4H", "1D"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_qtgui_detail.py`
Expected: FAIL.

- [ ] **Step 3: Implement; per-tab fetch failure renders "bars unavailable" for that tab**

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_qtgui_detail.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add qtgui/app.py tests/test_qtgui_detail.py
git commit -m "feat(dash): Qt coin detail with chart and no-setup state"
```

### Task 7: Bottom row + six rail pages (Qt)

**Files:**
- Modify: `qtgui/app.py`, `docs/GUI_MANUAL.md` (dashboard section)
- Test: `tests/test_qtgui_pages.py` (offscreen + skip-guard)

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: Market Movers (Gainers/Losers/High Volume), Recent Analysis (last 5), Activity Log (journal rows); pages Watchlist (starred, `meta`), Signals (ledger + history + filters), Analysis (indicator board + coverage + event-study N/kill dev-labeled), Trades (hypothetical-only ledger + header), Settings (display prefs + pause/disable via Task-2 writers; NO threshold/leverage editing).

- [ ] **Step 1: Write the failing test**

```python
def test_trades_page_is_hypothetical_only():
    assert "no orders placed" in trades_text().lower() and "p&l" not in trades_text().lower()
def test_settings_has_no_threshold_edit():
    assert "threshold" not in settings_labels() and "leverage" not in settings_labels()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_qtgui_pages.py`
Expected: FAIL.

- [ ] **Step 3: Implement; remove stake control; pause/disable write the same `meta` flags panels read**

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_qtgui_pages.py`, then full `run_all.py` — ALL SUITES PASS.

- [ ] **Step 5: Commit**

```bash
git add qtgui/app.py docs/GUI_MANUAL.md tests/test_qtgui_pages.py
git commit -m "feat(dash): rail pages with hypothetical-only trades"
```

## Self-Review

- Spec coverage: §1 epoch/gate/plans/journal → Tasks 1–2; §2 stakeless/sweep/reference/policy → Task 3; §3 cards/table/detail/bottom → Tasks 5–7; §4 evidence/copy/badges → Task 4 (+ UI assertions Tasks 5–7); §5 pages/states → Task 7; §6 tests/order → each task + run_all gates.
- Step scan: test steps pin names/assertions; code steps pin signatures/files; verification steps pin commands/outputs.
- Type consistency: `append_epoch/active_epoch/writes_signals`, `log_event/JOURNAL_CAP/write_snapshot/snapshot_pair`, `REFERENCE_NOTIONAL`, `summarize_setup/blocking_reason/BANNED_WORDS/badge_letters` shared verbatim across tasks.
- Review Focus: all five lines owned (banned wording→T4+T5; epoch gate→T1; no-inference→T2+T5; epoch pooling→T1; staleness→T5–T7 stamps).
- Proportion: signatures + assertions only; no bodies; plan shorter than the code it yields.
