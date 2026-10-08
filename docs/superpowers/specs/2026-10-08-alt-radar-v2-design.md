# Alt Radar v2 — Loop Rebuild Design Spec

**Date:** 2026-10-08
**Status:** Design approved section-by-section (§1-§4) + review r1 decisions incorporated, pending re-review
**Path:** Architectural (loop + inputs restructured, library kept)
**Supersedes:** `2026-10-07-alt-radar-design.md` loop/scan assumptions (spec §3/§6 intent kept)

## 1. Purpose

Rebuild the scan loop and scoring inputs so scores can mean something; keep the tested math library. The v1 loop is single-venue, single-timeframe, volume-ranked with a dead 30% leg, zero outcome rows, and an unwired collector — its premises, not its defects, block spec compliance.

**Kept as library:** `proto/indicators.py`, planner core (`proto/planner.py:92-160`), adapter validation pattern (`proto/mexc.py:44-122`), symmetry-test harness, SQLite schema shape.

**Rewritten:** `proto/scan.py` / `proto/app.py` loop logic, universe selection, venue layer, MTF inputs, veto enforcement, collector/outcome wiring + scheduling.

## 2. Architecture

```
feeds (MEXC REST + Bybit REST) → universe (stake-aware rotation) → scorer (5m+1H+4H)
  → planner → TUI + SQLite + collector (5m scheduled) + outcome resolver (post-scan)
```

## 3. Scan loop (§1 approved, review r1: request volume + concurrency)

- Budget 150/cycle, three DISJOINT groups: 80 tradeable-by-stake (min_notional ≤ stake, vol-ranked), 40 guaranteed MEXC-only tail (rotation pointer in `meta`), 30 remainder rotation. Groups never overlap (dedupe by coin, priority 80 → 40 → 30). If a group has fewer eligible coins, the shortfall spills to remainder rotation to still fill 150. Rotation pointer advances by (30 + spill-unused + 40-tail-consumed) each cycle and persists in `meta`; Tradeable 80 + tail 40 covered every cycle; remainder (~460 coins) sweeps at ~30/cycle → full remainder sweep every ~15 cycles (~15 min at 60s cadence). Pointer survives restarts.
- Request volume per cycle: per coin 3 MEXC klines (5m/1H/4H) + 1 depth + 1 Bybit klines/OI for shared coins only → ~4-5 req × 150 ≈ 600-750 requests.
- Concurrency/rate-limit: ThreadPoolExecutor max_workers=12 for per-coin fetch, 100ms stagger on start, venue-wide cap 20 req/s, on HTTP 429 / MexcError exponential backoff (0.4s × 2^attempt, 3 retries) then counted failure. No extra threads for universe fetch.
- 90s gate scope: covers `scan_once` per-coin fetch + score ONLY (existing `tests/test_app.py:116` gate). Universe refresh (`tickers` + `details` + Bybit equivalents) is cached 10 min in memory and excluded from the 90s budget.
- Per-coin failures counted (`failed N/150`), surfaced in header; detail view shows last error per coin.

## 4. Venues + MTF (§2 approved, review r1: bonus formula + cap)

- New `proto/bybit.py`, same interface as `mexc.py` (`tickers/details/klines/depth/funding`, envelope + column validation). Public REST, no keys. MEXC = discovery breadth; Bybit = OI history + 296 shared-coin confirmation. OKX deferred.
- Per scored coin: 5m x200 (entry), 1H x200 (trend), 4H x200 (swing bias). 15m/1D derived from collector later. Lean computed per TF with the same `volume_price` lean formula (donchian/ema/obv/rsi blend); 5m lean drives direction.
- Alignment bonus (inside 0–100): `aligned = sign(lean_5m)==sign(lean_1H)==sign(lean_4H) and all |lean|>0.15`. If aligned: `score = min(100, score + 8)`. If not aligned: no bonus, no penalty. Bonus applied AFTER `base * earlyness`, before rounding. Cap is the 100 ceiling; bonus never pushes past it.
- Counter-trend risk (label only, no score effect): if `sign(lean_5m) != sign(lean_4H)` and both `|lean|>0.15`, append warning `counter-trend: 5m {DIR5} vs 4H {DIR4} — elevated risk` to Scorecard vetoes-adjacent `notes` AND to plan `warnings` (planner surfaces, never swallowed). Shown in TUI detail view warnings block.
- OI leg: Bybit OI-change where available; MEXC-only keeps funding-only path capped at `0.35*fund_mag` (never outscores full OI+funding).

## 5. Vetoes + persistence (§3 approved, review r1: allocation/actionable/migration/timing/coverage)

- Keep `low_volume/wide_spread/thin_history`; add 24h verticality (`abs(change_24h) ≥ 35%` → `late_move`, alongside 1h ≥12%). Vetoed excluded from ranking into separate TUI section, still shadow-logged.
- Actionable (stake-aware): keep `Scorecard.actionable` property unchanged (stake-agnostic: not vetoes and tradeable and direction != NEUTRAL, for backward compat with existing tests/callers). Add distinct method `Scorecard.is_actionable(stake) = self.actionable and (min_notional is not None and min_notional <= stake)`. Unknown minimum (`min_notional is None`) is fail-closed: NOT actionable for flagging — a coin without a confirmed minimum cannot be flagged, though it still ranks and shadow-logs. Ranking shows all non-vetoed; `flagged = is_actionable(stake) and score ≥ 24`.
- Flag versioning: `ALTER TABLE signal_log ADD COLUMN flag_version INTEGER DEFAULT 1`; backfill `UPDATE signal_log SET flag_version=1 WHERE flag_version IS NULL`; all new rows write `flag_version=2`. Historical 692 flagged rows stay comparable by filtering on version.
- Outcome timing (bar mapping): signal `ts` (seconds) → entry bar = first 5m bar with `bar.ts > signal.ts`. Horizon `h` with `need` bars (12/48/288/2016): return from close of bar at index `need-1` within fut window; `max_fav`/`max_adv` = max/min signed returns over `fut[:need]`. If fewer than `need` future bars exist, horizon stays unresolved (never zero-filled).
- Collector coverage: collector covers the FULL universe (~581 coins), NOT the scan rotation — independent 5m job so a coin that leaves rotation still accumulates bars for outcome resolution. `7d` (2016 bars) resolves from collector bars only, never REST (2000-bar cap). Collector every 5m, per-coin CSV.gz, dedupe by ts.

## 6. TUI + errors + tests (§4 approved)

- TUI: ranked table + vetoed section + UNVALIDATED markers. Compounding tracker, `f` filter, extra columns deferred.
- Errors: counted failures + per-coin last-error; degraded venue marked and excluded, never silent.
- Tests: existing suites stay green; new `test_universe_rotation`, `test_bybit_adapter`, `test_mtf_alignment`, `test_veto_24h`. Live smoke <90s.

## 7. Non-goals (v2)

Order placement/keys, Telegram/news ingestion, auto-execution, threshold tuning before outcome rows exist, OKX adapter, TUI polish beyond vetoed section.
