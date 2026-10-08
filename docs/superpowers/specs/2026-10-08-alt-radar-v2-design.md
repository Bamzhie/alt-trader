# Alt Radar v2 — Loop Rebuild Design Spec

**Date:** 2026-10-08
**Status:** Design approved section-by-section by user (§1-§4), pending spec-file review
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

## 3. Scan loop (§1 approved)

- Budget 150/cycle: 80 tradeable-by-stake (min_notional ≤ stake, vol-ranked), 40 guaranteed MEXC-only tail (rotation pointer in `meta`), 30 remainder rotation. Full ~581 universe every ~4 cycles. Pointer survives restarts.
- Cycle target <90s (existing `tests/test_app.py:116` gate). Per-coin failures counted, surfaced in header.

## 4. Venues + MTF (§2 approved)

- New `proto/bybit.py`, same interface as `mexc.py` (`tickers/details/klines/depth/funding`, envelope + column validation). Public REST, no keys. MEXC = discovery breadth; Bybit = OI history + 296 shared-coin confirmation. OKX deferred.
- Per scored coin: 5m x200 (entry), 1H x200 (trend), 4H x200 (swing bias). 15m/1D derived from collector later. Cross-TF alignment = scored bonus; counter-trend labelled elevated risk.
- OI leg: Bybit OI-change where available; MEXC-only keeps funding-only path capped at `0.35*fund_mag` (never outscores full OI+funding).

## 5. Vetoes + persistence (§3 approved)

- Keep `low_volume/wide_spread/thin_history`; add 24h verticality (`abs(change_24h) ≥ 35%` → `late_move`). Vetoed excluded from ranking into separate TUI section, still shadow-logged.
- Flag frozen: `flagged = actionable and score ≥ 24`, new `flag_version=2` column so historic rows stay comparable.
- Outcomes: resolver runs post-scan (never blocks scan budget); `7d` resolves from collector bars only (REST 2000-bar cap). Collector every 5m, per-coin CSV.gz, dedupe by ts.

## 6. TUI + errors + tests (§4 approved)

- TUI: ranked table + vetoed section + UNVALIDATED markers. Compounding tracker, `f` filter, extra columns deferred.
- Errors: counted failures + per-coin last-error; degraded venue marked and excluded, never silent.
- Tests: existing suites stay green; new `test_universe_rotation`, `test_bybit_adapter`, `test_mtf_alignment`, `test_veto_24h`. Live smoke <90s.

## 7. Non-goals (v2)

Order placement/keys, Telegram/news ingestion, auto-execution, threshold tuning before outcome rows exist, OKX adapter, TUI polish beyond vetoed section.
