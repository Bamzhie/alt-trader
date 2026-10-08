# Outcome Loop + Bar Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the measurement gap — resolve forward returns for every logged signal and start the 5m bar collector clock.

**Architecture:** Two small modules with no TUI changes: `outcomes.py` reads pending signals from SQLite and uses the existing `mexc.klines` adapter to compute signed returns; `collector.py` appends 5m bars to per-coin CSV.gz. Both reuse `Store` and `mexc` interfaces.

**Tech Stack:** Python stdlib only (sqlite3, urllib, gzip, csv), existing `proto/mexc.py`, `proto/store.py`.

**Spec:** `docs/superpowers/specs/2026-10-07-alt-radar-design.md` §6.1, §6.1a, §6.3 (signal/outcome log, shadow logging, 5m-only collector, horizons 1h/4h/24h/7d).

## Global Constraints

- Read-only venue access, no orders/keys (spec §5.1).
- Direction-symmetric returns: LONG return = (px_later-px_entry)/px_entry*100, SHORT mirrored.
- Store both `vol` (base) and `amount` (quote) per bar (spec §6.3).
- 5m only; 15m/1H/4H/1D derived by aggregation, no extra collection.
- Stdlib only for TUI/collector path.

## Review Focus

- Signal with no later bars yet (too recent) → left unresolved, not zero-filled.
- Vetoed/shadow rows also get outcomes (denominator requirement §6.1a).
- MEXC kline gaps/empty payload → skip horizon, don't crash resolver.
- Collector re-run same 5m bar twice → dedupe by ts, no duplicates.
- Coin with <30 bars → veto thin_history still applies, outcome still recorded.

---

### Task 1: Outcome resolver

**Files:**
- Create: `proto/outcomes.py`
- Modify: `proto/store.py` (add `pending_outcomes()`, `log_outcome()`)
- Test: `tests/test_outcomes.py`

**Interfaces:**
- Consumes: `Store.conn` (sqlite3), `mexc.klines(symbol, "5m", limit=2000)`, `signal_log(id, coin, price, direction, ts)`.
- Produces: `resolve_pending(store, symbol_map, horizons=(1,4,24,168)) -> int` (count resolved); `signed_return(entry, later, direction) -> float`; `Store.log_outcome(signal_id, horizon, return_pct, max_fav, max_adv)`.

- [ ] **Step 1: Write failing test** `tests/test_outcomes.py::test_signed_return_mirrors` — LONG +10% vs SHORT -10% produce +10/-10 mirrored, `test_log_outcome_roundtrip` inserts signal then outcome and reads it back.
- [ ] **Step 2: Run test to verify it fails** — `python3 tests/test_outcomes.py`; expect FAIL (module missing).
- [ ] **Step 3: Implement `signed_return` + `Store.log_outcome/pending_outcomes` + `resolve_pending`** in `proto/outcomes.py`, `proto/store.py` — horizons map `{'1h':12,'4h':48,'24h':288,'7d':2016}` bars of 5m; max_fav/max_adv signed by direction; skip if insufficient later bars.
- [ ] **Step 4: Run tests** — `python3 tests/test_outcomes.py && python3 tests/test_scorer.py && python3 tests/test_app.py`; expect ALL PASS.
- [ ] **Step 5: Commit** — `git add proto/outcomes.py proto/store.py tests/test_outcomes.py; git commit -m "feat: resolve forward outcomes for logged signals"`

### Task 2: 5m bar collector

**Files:**
- Create: `proto/collector.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Consumes: `mexc.klines(symbol,"5m")`, `scan.build_universe()` for symbol map.
- Produces: `collect_once(data_dir="data/bars", limit_per_coin=200) -> dict` {coin: new_bars}; files `data/bars/<COIN>.csv.gz` with header `ts,o,h,l,c,vol,amount`, deduped by ts, appended.

- [ ] **Step 1: Write failing test** `tests/test_collector.py::test_dedupe_by_ts` — write same bars twice, row count unchanged; `test_stores_vol_and_amount` asserts both columns present.
- [ ] **Step 2: Run test to verify it fails** — `python3 tests/test_collector.py`; expect FAIL.
- [ ] **Step 3: Implement `collect_once` + `append_bars`** in `proto/collector.py` using gzip+csv stdlib, per-coin files, sort by ts.
- [ ] **Step 4: Run tests** — `python3 tests/test_collector.py`; expect ALL PASS; verify `data/bars/` gitignored (bars are data, not code).
- [ ] **Step 5: Commit** — `git add proto/collector.py tests/test_collector.py; git commit -m "feat: start 5m forward collector clock"`
