# Counterproposal: event-driven day-trading watcher

**Date:** 2026-10-12  
**Status:** Draft for review. Documentation only; no code or live behavior changes.  
**Responds to:** [`watcher-architecture-proposal.md`](watcher-architecture-proposal.md)

## 1. Position

Keep the proposal's main direction: move from repeated score-and-flag observations toward a small number of distinct, explainable market events. Change the implementation order and the evidence bar.

The app's goal should be **day-trading decision support**: detect a potentially actionable setup, explain what happened and what could invalidate it, and give the trader enough current context to decide. The app is not an auto-trader and should not imply that a frozen hypothetical plan is executable or profitable.

Do not approve the proposed trigger set or a live ranking change yet. The reported T1 replay is not reproducible from the current repository's replay implementation, the current measurement epoch is unset, and the episode tables are empty. A read-only status check on 2026-10-12 reported calibration FAIL (p95 gap 719s; p99 28,791s). The local database's three complete days had 4,544–5,527 v2-flagged observations per day, rather than the proposal's stated ~11,000; reconcile the cohort, dates, and definition before using either figure.

## 2. Product contract

The watcher reports **events**, not repeated score observations. Each event should answer:

- What changed, on which completed bar, and in which direction?
- Which rule fired, with a versioned rule id and the exact inputs used?
- What supports the setup, what conflicts with it, and what data is missing or stale?
- What price or state would invalidate the setup?
- Is the setup still active, already emitted, cooling down, or resolved?

Silence is valid. A frequency target is a capacity estimate, not a quality signal or a quota. The system must never loosen a rule to reach a target number of daily alerts.

The watcher remains read-only. Plans are research annotations until entry, fill, fee, slippage, funding, and position-management assumptions are explicitly modeled and validated. No UI wording should tell a trader to follow a signal or imply execution certainty.

## 3. Proposed architecture

Keep the existing collector and legacy benchmark. Build the watcher beside the current emission path, with explicit boundaries:

1. **Ingest:** store completed bars and point-in-time ticker, OI, and funding observations with source timestamps and quality status. Do not treat a current funding value as historical data.
2. **State:** calculate per-coin features once per newly closed bar. Maintain watermarks and versioned state. On gaps, restarts, duplicates, out-of-order bars, or corrections, mark affected features unavailable until caught up or rebuilt from a known checkpoint.
3. **Trigger:** evaluate a small registry of versioned, pure rules against that state. Rules specify exact inputs, thresholds, missing-data behavior, event identity, and invalidation conditions.
4. **Event lifecycle:** deduplicate repeated qualifying bars into one event; record candidate, emitted, invalidated, expired, and resolved transitions. Cooldowns prevent duplicate alerts; they do not make observations statistically independent.
5. **Measure:** attach outcomes to the event id and immutable trigger version. Keep exploratory results separate from confirmatory holdout results.

Checkpoint feature state and its processed-bar watermark atomically. Recovery should replay bars since the checkpoint idempotently. A periodic full recomputation remains available as a reconciliation tool; incremental state must match the batch reference within declared tolerances. “Never recompute” is not a recovery policy when a machine is offline longer than the checkpoint interval.

## 4. Trigger disposition

### T1 — initiation with BTC regime context

Keep T1 as the first **research candidate**, not an approved live trigger. Before implementation as a production rule, publish the replay code and a reproducible result bundle: data cutoff, universe membership by date, feature definitions, trigger events, cooldown application, sample counts by coin and regime, and return calculation.

Freeze the exact event semantics: which completed bar first qualifies, how age resets after a re-entry, how the seven-day BTC drift is calculated without look-ahead, how stale BTC data is handled, and how the per-coin 24-hour cooldown affects the replay. Report the result both with and without cooldown so its selection effect is visible.

Use T1 to test the event and measurement pipeline in shadow first. Its reported 24-hour return is not sufficient for a day-trading decision; include 15-minute, 1-hour, and 4-hour results, adverse/favorable excursion, and a documented conservative entry assumption.

### T2 — OI divergence

Defer T2 from the initial trigger set. Define “mature-position extreme” as measurable conditions, including direction and lookback. The stated OI history depth is much shorter than the proposed 90-day archive, so T2 cannot claim a 90-day replay until point-in-time OI data is available for that period. Otherwise collect OI prospectively and keep T2 in shadow until the predeclared sample and regime coverage are reached.

### T3 — funding fade

Defer T3 from the initial trigger set. Current funding snapshots do not establish historical funding at each replay timestamp. Archive timestamped funding observations first. Justify the short-only rule against the mirrored long case or classify the directional asymmetry as an explicit hypothesis to test. A daily cap limits alert count; it does not control false-positive risk.

## 5. Validation protocol

### Stage A — evidence and data audit

Before tuning or emitting triggers, freeze a data snapshot and reconcile the proposal's headline counts with the database. Publish a coverage table by input, coin, and date, including delisted or missing instruments. State which metrics count scan rows, unique events, coins, or resolved outcomes.

The current `proto/replay.py` replays the v2 volume/price leg at four-hour sampling intervals; it does not implement the proposed T1/T2/T3 replay. Treat the reported T1 result as unverified until the trigger-specific replay can be rerun from a clean checkout against the stated snapshot.

### Stage B — replay harness and holdout

Build the harness before the incremental state engine or trigger UI. Split chronologically: development data for rule design, a validation interval for implementation checks, and a final untouched holdout for the promotion decision. Do not choose thresholds on the full archive and then report that same archive as confirmation. If available history cannot support a meaningful holdout or regime coverage, collect more data rather than weakening the gate.

Use event-level cohorts and report, for every trigger and direction:

- event count, unique coins, calendar coverage, regime coverage, and missing-data rates;
- 15-minute, 1-hour, 4-hour, and 24-hour signed returns;
- stop/target first-touch outcomes, with a conservative rule for bars that touch both;
- gross and cost-sensitive results using documented fees, spread, slippage, and funding assumptions;
- adverse/favorable excursion, drawdown, loss streaks, and exposure overlap.

Compare triggers with simple baselines on the same events and dates. Account for repeated events by coin and shared market moves by time/regime. Use clustered or block-based uncertainty estimates, and account for trying multiple triggers. Do not call a row count an effective sample size.

Replace the fixed “400 events equals 40 days” claim with a power/precision plan based on each trigger’s expected event rate, desired minimum detectable effect, clustering, and regime splits. At the proposal's own per-trigger rates, 400 raw events would take roughly 50–133 days for T1, 67–200 days for T2, and at least 200 days for T3. Seven shadow days can test data flow, deduplication, cooldowns, and recovery; they cannot establish an edge.

Promotion requires a frozen rule, an untouched holdout result whose uncertainty supports positive after-cost expectancy, adequate coin/time/regime coverage, and a live shadow period that confirms the same rule is operating as replayed. Define the exact confidence and coverage thresholds before seeing holdout outcomes. If these conditions are not met, continue collecting or reject the trigger; do not lower the bar after a disappointing result.

### Stage C — operational shadow

Run only rules that pass the replay gate in shadow mode. Validate event timing, source freshness, missing-data handling, duplicate suppression, cooldowns, crash recovery, and the match between emitted events and replay logic. Shadow duration is an operational check, not a substitute for the statistical holdout.

Do not change ranking or flags during this stage. Preserve the current scanner as a benchmark until a trigger passes the pre-registered gate and the operator explicitly approves promotion.

## 6. Day-trader safeguards

- Show the event timestamp, last completed bar, ticker/candle skew, OI sample gap, funding timestamp, and a clear stale/unavailable state.
- Include liquidity and spread context, contract status, and a warning when a plan assumes a fill that was not observed.
- Report correlated open setups and aggregate exposure context; independent per-coin cooldowns do not protect a portfolio concentrated in one altcoin move.
- Keep the existing 1-hour and 24-hour movement vetoes visible. Regime gating is context, not a guarantee against reversals or pump-and-dump behavior.
- Separate a research alert from an actionable trade plan. Until execution assumptions are validated, label entry, stop, and targets hypothetical.
- Provide a global pause and a per-trigger disable switch for stale data, feed degradation, rule defects, or alert storms.

## 7. Recommended decision

1. Approve Stage A and the replay harness as read-only research work.
2. Keep T1 as the first candidate; require a reproducible replay and temporal holdout before shadow deployment.
3. Defer T2 and T3 until their historical input coverage and exact rule semantics are documented.
4. Keep the current emission path as a benchmark; do not enable live ranking, flags, or execution from watcher events under this proposal.
5. Revisit trigger approval after a reviewable report shows the frozen rules, data coverage, holdout results, uncertainty, and operational shadow checks.

This preserves the product shift toward fewer, distinct events while making accuracy mean reproducible evidence and honest uncertainty—not simply lower alert volume or a promising in-sample percentage.
