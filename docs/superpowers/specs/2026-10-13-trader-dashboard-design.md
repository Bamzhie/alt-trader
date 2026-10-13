# Trader Dashboard Design — Qt replica + stakeless model + setup-gated signals

**Date:** 2026-10-13
**Status:** Spec (Sections A–D approved in chat 2026-10-13)
**Sources:** dashboard mockup (user-supplied image, 2026-10-13); watcher setup spec (`2026-10-12-watcher-setup-design.md`); watcher UI spec (`2026-10-12-watcher-ui-design.md`, superseded for Qt by this spec); `proto/watcher.py`, `proto/watcher_report.py`, `proto/watcher_view.py`, `proto/watcher_feed.py`; `proto/scan.py` (universe), `proto/mexc.py` (`INTERVALS`, tickers/details), `proto/indicators.py`, `proto/planner.py`.
**Scope:** Qt (`qtgui/`) dashboard replicating the mockup; stakeless universe; signals only for coins with watcher setups; heuristic confidence tiers; unified coin detail; five full rail pages. tkinter/TUI keep existing views. Scanner benchmark ends at an explicit epoch cut. README rules apply (read-only, `tests/run_all.py` green, no invented data, no edge/profitability claims, no order path ever).

## 1. Emission cut (from prior approval, binding)

- `meta` marker `signal_epoch="watcher-only-v1"` + `cut_ts`, written once by explicit operator command, never auto. When set: scan skips `signal_log`/`coin_state` signal writes and plan-attach writes; scan still fetches tickers/universe in memory for the coin list and price freshness only — it does NOT feed watcher candidates (watcher evaluates setups independently from bars via collector + triggers; no scan→watcher dependency). Gate is one `if` at call sites — reversible by meta flip. Absent marker → legacy behavior.
- History stays readable; reports gain cut-aware headers. Episode/cohort code works on old rows + watcher tables forward.
- Post-cut plans: generated in memory on demand from the setup's frozen levels (entry/stop/tgt + 100U reference notional); only pre-cut plans come from `plan_log`. The Trades page reads on-demand plans for active setups and stored rows for history.
- Activity journal: new capped table `watcher_events(ts, kind, coin, text, tier)` (kinds: emission, invalidation, expiry, scan-complete), writers = watcher lifecycle transitions + scan-loop completion; cap 200 rows (prune oldest); tests pin cap + prune order.

## 2. Stakeless model (Section D)

- Universe = all eligible MEXC USDT perps (synthetic filter kept). Stake groups, `tradeable` gating, stake-gated thresholds removed from logic. `min_notional` retained as venue info on detail only.
- Full sweep ≈ 2 min at 20 req/s; rotation pointer generalizes to a continuous sweep with visible progress (`sweeping N/<live eligible count>`); freshness read from `data as of`, never assumed.
- Plans use fixed **100 USDT reference notional** ("reference size, not a position"); levels + leverage headroom + R-multiples shown; risk-per-100U formula displayed. Settings loses stake control.
- Universe policy `mexc_full_v1` → `mexc_full_stakeless_v2`; cohorts never mix across the cut.

## 3. Dashboard data (Section A)

- Stat cards: Active Signals (active setups), Total Scanned (universe size), High Conviction (HIGH-tier count + share), Avg Confidence (mean heuristic score). "vs last 24h" via `meta` daily counters (`dash_active:<date>` etc.) written per scan.
- Top Signals: one row per active setup — coin + full name, price, 24h%, 24×5m sparkline, 2-line analysis, confidence (tier pill + heuristic bar), LONG/SHORT pill, expiry countdown. Filters All/Long/Short + Market (liquidity tier: All / Top 20 by 24h quote volume / Tail) + Horizon (setup horizon bucket from the geometry grid: 4h (N=48) / 24h (N=288), default 4h) + Sort (Confidence default; Expiry, 24h%). (The mockup's "Timeframe: 1H" is a horizon filter, not a bar interval — bar intervals live on the chart tabs.)
- Market Overview: BTC + ETH only (TOTAL row dropped — no source). Coin detail: tabs 5m/15m/1H/4H/1D (no 1m — unsupported), candles + EMA50/200 + volume, Analysis/Trade Plan/Metrics tabs, validity + next-update lines. No-setup coins show "no setup yet" + blocking reason in the same panel.
- Bottom row: Market Movers (Gainers/Losers/High Volume from tickers), Recent Analysis (last 5 emissions/transitions), Activity Log (capped structured journal: emission, invalidation, expiry, scan-complete).

## 4. Confidence + copy (Section B)

- `confidence_tier()`: HIGH / STANDARD / SPECULATIVE from (EV_R, p̂_LB, evidence completeness, funding/monitoring penalties); WITHHELD (no score) when net EV unavailable. `confidence_score()` provisional heuristic (marked provisional until holdout), deterministic 0–100:
  `score = clamp(100 * (0.55*tier_base + 0.25*ev_scaled + 0.20*evidence_frac) - penalties, 0, 100)`
  with `tier_base` HIGH 0.85 / STANDARD 0.65 / SPECULATIVE 0.45; `ev_scaled = clamp(EV_R / 1.0, 0, 1)`; `evidence_frac` = fraction of required inputs available (0–1); penalties: funding unavailable +10, monitoring unavailable +15, stale funding +5. Tier cuts (provisional): score ≥ 75 → HIGH, 55–74 → STANDARD, < 55 → SPECULATIVE; WITHHELD overrides regardless of score. Tests pin this table exactly; holdout may replace it under a new rule version.
- Avg Confidence = mean score over scored setups only (WITHHELD excluded from numerator and denominator).
- `summarize_setup()` 2-line copy cites only computed inputs. Banned words (test-enforced): VWAP, "high probability", profit certainty, probability claims, fill/execution language. Missing inputs → shorter honest copy.
- Letter-badge coin glyphs (drawn, no assets, no network).

## 5. Pages + states (Section C)

- Rail (`QStackedWidget`, Dashboard default): Dashboard, Watchlist (starred, `meta`-persisted), Signals (full setup ledger + history + filters), Analysis (indicator board + input-coverage + event-study N/kill status, dev-labeled), Trades (**hypothetical-only** ledger: setups, plans, touches, horizon returns; "No orders placed — hypothetical outcomes, not P&L"), Settings (coins, interval, leverage cap, thresholds, pause/disable writing the same `meta` flags).
- Errors: zero setups → "silence is valid"; feed/DB failure → contained fallback, scanner intact; chart timeframe failure → "bars unavailable" per tab. Close-time honesty throughout (`data as of`, last closed bar).

## 6. Tests + rollout

- Units (headless, `run_all.py`): tier/score mapping incl. WITHHELD, banned-words, counters, journal cap, epoch-gate on/off, stakeless universe membership, reference-notional math, parity of state wording.
- Qt offscreen construction tests per existing pattern (skip-guard, temp DB, retired workers, socket tripwire); no network. Visual pass = manual checklist against the mockup, region by region, recorded in the manual.
- Task order: epoch gate + counters/journal → tiers/score/copy → Dashboard table + cards → detail/chart → bottom row → five pages → parity/polish. Additive; scanner UI paths in Qt untouched until the operator opens the new surfaces.

## Open decisions

- `EV_hurdle`/K and tier-cut values predeclared before holdout; shown as-is until then.
- Sweep-window size for the full universe (default: entire eligible set per cycle, progress shown).
