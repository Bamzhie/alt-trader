# Watcher Architecture Proposal: from polling scanner to event-driven reporter

**Date:** 2026-10-12
**Status:** Proposal. No code changed. Nothing here runs until approved.
**Supersedes nothing:** the collector, journal, outcomes, episodes, and reports
stay exactly as they are. This replaces only the *emission* path (scan →
score → flag) with accumulate → update → trigger.

---

## 1. The problem, with numbers

The current loop emits ~11,000 flagged signals/day and measures:

- 1h win rates **below** coin-flip two days running (41.5%, σ≈0.8% → ~10σ under 50%).
- Score and conviction gradients **inverted**: v2 40+ → 24.1% (1h), |lean| 0.7+ → 25.6%.
- Terminal stop rate **91.9%** (5,489 finished trades).
- ~8,000 plans over ~240 coins: each sustained setup counted dozens of times,
  so sample sizes overstate independent evidence by an order of magnitude.

Upstream causes, all measured or audited, none speculative:

1. The detector fires hardest at exhaustion (volume + range position + book
   skew + OI agree only once the impulse is spent); 1h outcomes are mean
   reversion. The `earlyness` term was supposed to prevent this and doesn't
   (breakout pinned at window high → earlyness ≈ 0 by construction).
2. The OI quadrant is backwards vs the spec's own sentence (full lean to the
   ambiguous unwind, half lean to certain new shorts).
3. Stops sit 0.5–6% away at up to 50×; 40% land within the first hour.

Tuning thresholds cannot fix definition errors. Emitting less of the same
signal cannot either — the emission *policy* has to change.

## 2. The proposal in one paragraph

The app stops scoring every coin every minute. It **accumulates** data
continuously (already running), **updates** per-coin state incrementally on
every bar close, and **emits a signal only when a named trigger fires** —
target: single-digit to low-tens of signals per day, each with a frozen
plan, each an independent measurement sample. Selectivity is the product;
silence is a valid output.

## 3. Architecture (four layers, two already exist)

| # | Layer | Status | Cost per cycle |
|---|---|---|---|
| 1 | **Accumulate**: bars, ticker snapshots, OI, funding into store/files | RUNNING, untouched | already paid |
| 2 | **Update** (new): per bar-close, refresh per-coin state — indicators, BTC regime, swing structure, accumulated flow (session CVD, OBV slope over hours not minutes) | to build | O(coins), pure arithmetic, no network |
| 3 | **Trigger** (new): small named rule set evaluated against layer-2 state; a firing trigger emits one signal + frozen plan | to build | trivial |
| 4 | **Measure** (exists): plans, outcomes, episodes, reports, cohorts | RUNNING, untouched | already paid |

Layer 2 is incremental by contract: it processes only the newly closed bar
per coin per cycle and keeps running state in memory (checkpointed to SQLite
hourly so restarts resume, never recompute 90 days). Full recomputation is
banned outside backtests — this is what buys the compute budget for real
analysis per decision.

## 4. The triggers (declared priors, not fits)

Each trigger states its hypothesis, inputs, cooldowns, and expected volume
*before* seeing outcomes. Thresholds below are priors from measured evidence
(§1) and the v3 replay, frozen at approval.

- **T1 — initiation + regime-gated long** (strongest evidence). Fires when a
  v3-style initiation event (break age ≤ 6 bars) coincides with trailing-7d
  BTC drift > +5%. Replay: 53.3% / +1.28% at 24h, n=1,827. Cooldown: one
  signal per coin per 24h. Expected: 3–8/day.
- **T2 — OI-divergence spike** (moderate evidence). Fires on new-position flow
  (1h OI up > 5%) aligned with 5m structure *against* a mature-position
  extreme. Rationale: the one OI configuration the current code prices
  backwards. Cooldown: one per coin per 12h. Expected: 2–6/day.
- **T3 — funding-extreme fade** (weak evidence, marked exploratory). Fires on
  |funding|/cap > 0.8 *against* a vertical extension, SHORT only. This is a
  hypothesis with almost no data behind it; it ships capped at 2/day and is
  first to go if flat after 100 samples.

Deliberately excluded: pure book-skew triggers (a single REST snapshot is not
a backtestable event series), any trigger needing inputs the archive lacks.

## 5. The backtestability constraint (the anti-slop rule)

A trigger ships **only** on inputs replayable over the 90-day archive:
OHLCV bars, derived indicators/regimes/structure, OI within its 8-day depth,
current funding snapshots. Live book skew may *confirm* at emission (as in
v3) but never *trigger* — anything else is untestable by construction and
therefore not a trigger, just a hope.

Acceptance gate per trigger, run before it emits live:
1. Replay over the full archive: hit-rate and avg signed return at 4h/24h,
   split by regime, with sample counts shown.
2. Bar: 24h avg > +0.5% with n ≥ 300, no regime split below −0.5%.
3. Then 7 live shadow days (logged, never ranked). Promote only on agreement
   between backtest and shadow.

## 6. The calibration math, stated plainly

At ~10 signals/day, distinguishing 55% from 50% at 95% confidence needs
≈400 events ≈ **40 days minimum per trigger** — and that's one regime mix,
not proof. Consequences accepted up front:

- No live ranking on trigger output before its sample minimum, no exceptions.
- Regime coverage, not row counts, is the gating resource; a second choppy
  fortnight teaches nothing new.
- The v2/v3 shadow columns keep logging throughout (comparison data is free).

## 7. Migration (nothing stops, nothing breaks mid-stream)

1. Collector, journal, outcomes, episodes, daemon: untouched, still the system
   of record.
2. Layer 2 + backtest harness built beside the scanner; scanner keeps its
   cadence until triggers pass §5.
3. Triggers go live one at a time, each as a new rule id (never an edit),
   behind the same promotion discipline as v3.
4. The scan-score-flag path is retired only when a trigger set demonstrably
   covers its few working cells (up-regime longs) — until then it stays as
   the (losing) benchmark, because a benchmark you delete is a lesson you
   can't re-learn.

## 8. Decisions needed from you

1. Approve the trigger set (§4) as priors — cut or add before anything builds.
2. Approve the acceptance bar (§5–6) — especially the 400-event minimum, which
   is what makes this slow and honest.
3. Which to build first: the layer-2 state engine, or the backtest harness
   (my recommendation: harness first — it judges the triggers, and judging
   matters more than plumbing).
