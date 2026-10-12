# Watcher Architecture Proposal v2: event-driven day-trading decision support

**Date:** 2026-10-12 (v2; supersedes the 2026-10-12 v1 draft)
**Status:** Proposal. No code changed. Nothing here runs until approved.
**Responds to:** `watcher-architecture-counterproposal.md` (accepted in large
part — see §9 for the ruling on each point) and two corrected errors of my
own (flag-rate math, T1 replay labeling).

## 0. Corrections to v1 (mine, owned)

- **Flag rate:** v1 said "~11,000 flagged/day" — wrong; I misread a running
  total as a rate. Measured: **4,500–5,500 flagged/day** over complete UTC
  days. All volume math below uses the corrected figure.
- **T1 evidence:** v1's "+1.28% up-long" table came from the VP-leg replay
  (lean-signed forward returns, no initiation gating, no cooldowns), not
  from T1 trigger semantics. It is demoted to *supporting*: it shows the
  price/volume leg works with-trend on longs, which motivates T1 — it does
  not test T1. A trigger-faithful replay (§6) is required before T1 means
  anything.

## 1. Product contract: day-trading decision support

The app is not an auto-trader and must never imply a frozen plan is
executable or profitable. Each emitted **event** answers, on screen:

- What changed, on which completed bar, in which direction?
- Which rule fired (versioned id) with the exact inputs used?
- What supports it, what conflicts, what data is missing or stale?
- What price or state invalidates it?
- Is it still active, emitted, cooling down, or resolved?

Plans are research annotations until entry, fill, fee, slippage, funding,
and position-management assumptions are explicitly modeled and validated.
Every number on screen traces to a venue response or a logged row;
estimates are labeled estimates.

Silence is valid. Frequency targets below are capacity estimates for
sizing the statistics, never quotas — the system must never loosen a rule
to hit a daily count.

## 2. Architecture (four layers, two running)

| # | Layer | Status |
|---|---|---|
| 1 | **Accumulate**: bars, point-in-time ticker/OI/funding snapshots with source timestamps + quality flags | RUNNING, untouched |
| 2 | **Update**: per-coin state refreshed once per newly closed bar; watermarks + hourly SQLite checkpoints; on gaps/restarts/duplicates/out-of-order bars, affected features go unavailable until caught up or rebuilt from checkpoint; a periodic full recomputation exists as reconciliation (batch reference must match incremental state within declared tolerance) | to build |
| 3 | **Trigger**: small registry of versioned pure rules on layer-2 state; each states exact inputs, thresholds, missing-data behavior, event identity, invalidation, cooldowns | to build |
| 4 | **Measure**: plans, outcomes, episodes, cohorts, reports | RUNNING, untouched |

Full recomputation is banned as the steady state but required as the
recovery/reconciliation tool — "never recompute" is not a crash policy.

## 3. Event lifecycle (states, not just emits)

`candidate → emitted → invalidated | expired | resolved`. Repeated
qualifying bars deduplicate into one event. Cooldowns (per coin, per rule)
suppress duplicate alerts; they do **not** make observations statistically
independent — analysis still clusters by coin and shared market moves.
Outcomes attach to the event id under the trigger's immutable version;
exploratory tallies stay separate from confirmatory holdout counts.

## 4. Trigger registry (priors frozen at approval)

- **T1 — initiation + BTC regime, LONG only** (research candidate, not
  approved). Hypothesis: break age ≤ 6 bars + trailing-7d BTC drift > +5%
  isolates onsets the VP replay favors. Requires the §6 trigger-faithful
  replay first; report with and without cooldown so selection effects show.
  Intraday horizons (15m/1h/4h) plus 24h, excursions both ways, documented
  conservative entry. Expected: 3–8/day.
- **T2 — OI divergence: DEFERRED.** OI history is ~8 days deep; a 90-day
  replay is impossible until point-in-time OI covers the window. Collect
  prospectively, keep in shadow, revisit on coverage — not on hope.
- **T3 — funding fade: DEFERRED.** No funding history exists to replay
  against; short-only asymmetry unjustified against the mirrored long case.
  Archive timestamped funding first.

Deliberately excluded: book-snapshot triggers (single REST prints are not an
event series and never backtest); anything needing inputs the archive lacks.

## 5. Validation protocol (tiered — light gate to shadow, full gate to rank)

**Stage A — data audit (before anything else).** Freeze a snapshot; publish
coverage by input × coin × date (delisted/missing included); state exactly
what counts rows vs events vs coins vs resolved outcomes.

**Stage B — trigger replay + chronological holdout.** Dev interval for rule
design, validation interval for implementation checks, final untouched
holdout for promotion. Thresholds are never chosen on the full archive and
reported on it. Per trigger, direction, and regime: event count, unique
coins, calendar/regime coverage, missing-data rates; 15m/1h/4h/24h signed
returns; stop/target first touches (conservative same-bar rule); gross AND
cost-sensitive columns (documented fees, spread, slippage, funding);
excursions, drawdown, loss streaks, exposure overlap; simple baselines on
identical events/dates; clustered uncertainty with multiplicity accounted.
Sample-size rule is a power/precision plan per trigger (rate × minimum
detectable effect × clustering × regime splits), not a fixed event count —
at T1's 3–8/day this means calendar months, stated up front.

**Stage C — operational shadow.** Replay-passed rules only. Validates timing,
freshness, missing-data handling, dedupe, cooldowns, crash recovery, and
emitted-vs-replayed agreement. Shadow proves operations, never edge.

**Promotion** needs: frozen rule, untouched holdout with uncertainty
supporting positive after-cost expectancy, adequate coin/time/regime
coverage, and a confirming shadow. Pre-declare the confidence and coverage
thresholds before seeing holdout outcomes. Miss → keep collecting or reject;
never lower the bar after a disappointing result.

## 6. Day-trader safeguards (all required before any live ranking)

- Every event shows timestamp, last completed bar, ticker/candle skew, OI
  sample gap, funding timestamp, and explicit stale/unavailable states.
- Liquidity + spread context, contract status, and a warning wherever a plan
  assumes an unobserved fill.
- Correlated open setups and aggregate exposure context (per-coin cooldowns
  don't protect a book concentrated in one altcoin move).
- 1h/24h movement vetoes stay visible; regime is context, not protection.
- Research alerts visually distinct from actionable plans until execution
  assumptions validate.
- Global pause + per-trigger disable for stale data, degradation, defects,
  or alert storms.

## 7. Calibration math, corrected

At 3–8 T1 events/day, a ±5-point read on win rate needs hundreds of events
spanning more than one regime — roughly one to two quarters, not 40 days
(the v1 estimate used the wrong daily rate and ignored clustering). This is
stated so the timeline is honest, not to justify weakening §5.

## 8. Migration (unchanged from v1)

Collector, journal, outcomes, episodes, daemon untouched and still the
system of record. Layer 2 + harness built beside the scanner. Triggers go
live one at a time as new rule ids. The scan-score-flag path stays as the
losing benchmark until a trigger set demonstrably covers its few working
cells — a benchmark you delete is a lesson you can't re-learn.

## 9. Rulings on the counterproposal (accepted / declined, with reasons)

- Accept: chronological splits, power framing, baselines, invalidation,
  kill switches, checkpoints, T2/T3 deferrals, report-with/without-cooldown,
 never-loosen-to-hit-quota, hypothetical plan labeling.
- Accept with tiering: full §5 protocol at promotion; lighter (frozen rule +
  reproducible replay + honest denominators) at shadow entry — otherwise the
  process guarantees permanent research.
- Decline: nothing structural. The remaining delta is my two corrected
  errors above, both fixed in this revision.

## 10. Decisions needed from you

1. Approve T1 as first *candidate* (research only — no live ranking).
2. Approve the tiered gates (§5) including the months-long honest timeline.
3. Build order: backtest harness first (unchanged recommendation — judging
   matters more than plumbing).
