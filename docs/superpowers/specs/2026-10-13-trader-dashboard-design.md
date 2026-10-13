# Trader Dashboard Design — Qt replica + stakeless model + setup-gated signals

**Date:** 2026-10-13
**Status:** Revised spec (2026-10-13; Sections A–D updated after review, awaiting review of this revision)
**Sources:** dashboard mockup (user-supplied image, 2026-10-13); watcher setup spec (`2026-10-12-watcher-setup-design.md`); watcher UI spec (`2026-10-12-watcher-ui-design.md`, superseded for Qt by this spec); `proto/watcher.py`, `proto/watcher_report.py`, `proto/watcher_view.py`, `proto/watcher_feed.py`; `proto/scan.py` (universe), `proto/mexc.py` (`INTERVALS`, tickers/details), `proto/indicators.py`, `proto/planner.py`.
**Scope:** Qt (`qtgui/`) dashboard adapting the mockup; stakeless universe; signals only for coins with watcher setups; transparent rule-evidence display; unified coin detail; six rail pages. No ML scorer or probability/confidence score in this release. tkinter/TUI keep existing views. Scanner benchmark ends at an explicit epoch boundary. README rules apply (read-only, `tests/run_all.py` green, no invented data, no edge/profitability claims, no order path ever).

## 1. Emission cut (from prior approval, binding)

- An explicit operator command appends an immutable epoch record (`epoch_id`, `mode`, `cut_ts`) and updates the active-epoch pointer; it is never set automatically. The first transition to `watcher-only` freezes further scanner `signal_log`/`coin_state` signal writes and plan-attach writes. The scan loop may still fetch tickers for universe/freshness display, but it does not feed watcher candidates; the watcher evaluates bars independently.
- Rollback is an explicit new epoch transition with a new `epoch_id` and `cut_ts`; it never overwrites or removes an earlier cut. Reports and UI keep epoch cohorts separate. Scanner rows written after rollback belong to the new epoch and are not pooled with pre-cut rows. Existing scanner history remains readable and is labeled as the legacy benchmark.
- Reports gain cut-aware headers. Episode/cohort code handles old rows and watcher tables forward while keeping their source and epoch distinct.
- Post-cut plans: generated in memory on demand from the setup's frozen levels (entry/stop/tgt + 100U reference notional); only pre-cut plans come from `plan_log`. The Trades page reads on-demand plans for active setups and stored rows for history.
- Activity journal: new capped table `watcher_events(ts, kind, coin, text)` (kinds: emission, invalidation, expiry, sweep-complete), writers = watcher lifecycle transitions + completed universe sweeps; cap 200 rows (prune oldest); tests pin cap + prune order.
- Dashboard comparisons: append completed-sweep snapshots to `dashboard_snapshot(ts, eligible_count, evaluated_count, active_setups, new_setups_24h, monitoring_coverage)`. Retain enough history to cover the latest snapshot and its 24-hour comparison point; if unavailable, show no comparison rather than infer one.

## 2. Stakeless model (Section D)

- Universe = all eligible MEXC USDT perps (synthetic filter kept). Stake groups, `tradeable` gating, stake-gated thresholds removed from logic. `min_notional` retained as venue info on detail only.
- Full sweep ≈ 2 min at 20 req/s; rotation pointer generalizes to a continuous sweep with visible progress (`sweeping N/<live eligible count>`); freshness read from `data as of`, never assumed.
- Plans use fixed **100 USDT reference notional** ("reference size, not a position"); levels, R-multiples, and reference stop-risk are shown. Do not show leverage headroom or liquidation safety without venue maintenance-margin and mark-price inputs. Settings loses stake control.
- Universe policy `mexc_full_v1` → `mexc_full_stakeless_v2`; cohorts never mix across the cut.

## 3. Dashboard data (Section A)

- Stat cards: Active Setups, Eligible Perps, Monitoring Coverage (% of active setups with `monitoring=ok`), and New Setups (rolling 24h). Do not call universe size "scanned": show `eligible_count` and `evaluated_count` separately, based on actual per-contract evaluation results for the latest sweep. Rolling-24h comparisons use timestamped dashboard snapshots: compare the latest snapshot with the nearest snapshot at or before `now−24h`. If no suitable prior snapshot exists, show comparison unavailable. Daily counters are not a rolling-24h measure.
- Top Signals: one row per active setup — coin + full name, price, 24h%, 24×5m sparkline, 2-line rule-based analysis, matched criteria/evidence indicators, LONG/SHORT pill, expiry countdown. No confidence tier or probability-like bar. Filters All/Long/Short + Market (liquidity tier: All / Top 20 by 24h quote volume / Tail) + Horizon (setup horizon bucket from the geometry grid: 4h (N=48) / 24h (N=288), default 4h) + Sort (newest setup default; Expiry, 24h%). (The mockup's "Timeframe: 1H" is a horizon filter, not a bar interval — bar intervals live on the chart tabs.)
- Market Overview: BTC + ETH only (TOTAL row dropped — no source). Coin detail: tabs 5m/15m/1H/4H/1D (no 1m — unsupported), candles + EMA50/200 + volume, Analysis/Trade Plan/Metrics tabs, validity + next-update lines. No-setup coins show "no setup yet" + blocking reason in the same panel.
- Bottom row: Market Movers (Gainers/Losers/High Volume from tickers), Recent Analysis (last 5 emissions/transitions), Activity Log (capped structured journal: emission, invalidation, expiry, sweep-complete).

## 4. Rule evidence + copy (Section B)

- ML scoring is deferred. Watcher setup emission, lifecycle, and dashboard rendering must not depend on model outputs. This release does not compute or display `p̂_LB`, `EV_R`, probability claims, confidence scores, or HIGH/STANDARD/SPECULATIVE tiers. The dashboard must not infer these values from indicator strength or data completeness. A future scorer requires a separately versioned rule and holdout review before its outputs become UI inputs.
- Show transparent setup evidence instead: triggering rule, direction, required criteria passed, optional confirmation inputs, and input availability/staleness. Monitoring coverage is an operational data-quality metric only; it is not setup quality or probability.
- `shadow_selected` and EV-based top-K are unavailable/disabled until a validated probability source and net-EV definition are approved. Do not label setups as EV-selected when the selector has no usable estimate.
- `summarize_setup()` 2-line copy cites only computed inputs and rule criteria. Banned words (test-enforced): VWAP, "high probability", "high conviction", profit certainty, probability/confidence claims, and fill/execution language. Missing inputs → shorter honest copy.
- Letter-badge coin glyphs (drawn, no assets, no network).

## 5. Pages + states (Section C)

- Rail (`QStackedWidget`, Dashboard default): six pages — Dashboard, Watchlist (starred, `meta`-persisted), Signals (full setup ledger + history + filters), Analysis (indicator board + input coverage + event-study N/kill status, dev-labeled), Trades (**hypothetical-only** ledger: setups, plans, touches, horizon returns; "No orders placed — hypothetical outcomes, not P&L"), Settings (display preferences, pause/disable writing the same `meta` flags).
- Settings may pause the watcher or disable a trigger; it may not edit thresholds or model parameters in place. Any strategy-parameter change requires a new immutable rule version and a separately labeled evaluation cohort. Remove leverage-cap editing from this release. Show only 100 USDT reference-notional stop-risk; do not present leverage headroom or liquidation safety without venue maintenance-margin and mark-price inputs.
- Errors: zero setups → "silence is valid"; feed/DB failure → contained fallback, scanner intact; chart timeframe failure → "bars unavailable" per tab. Close-time honesty throughout (`data as of`, last closed bar).

## 6. Tests + rollout

- Units (headless, `run_all.py`): criteria/evidence rendering, no probability/confidence wording, rolling-snapshot comparison and unavailable case, journal cap, append-only epoch transitions and cohort separation, stakeless universe membership vs actually evaluated counts, reference-notional risk math, parity of state wording.
- Qt offscreen construction tests per existing pattern (skip-guard, temp DB, retired workers, socket tripwire); no network. Visual pass = manual checklist against the mockup, region by region, recorded in the manual.
- Task order: epoch gate + timestamped snapshots/journal → rule-evidence copy → Dashboard table + cards → detail/chart → bottom row → six pages → parity/polish. Additive; scanner UI paths in Qt remain available for legacy history and are clearly labeled around the epoch cut.

## Open decisions

- ML, `p̂_LB`, EV-based selector, `EV_hurdle`, and K are deferred from this dashboard release. They may be reconsidered only after a separately approved evaluation establishes a usable probability source and net-EV definition.
- Sweep-window size for the full universe (default: entire eligible set per cycle, progress shown).
- Rolling-snapshot spacing/retention policy; comparisons remain unavailable when snapshots do not cover the requested 24-hour point.
