# Review: docs/watcher-architecture-proposal.md

**Reviewer:** Day trader / system builder
**Date:** 2026-10-12
**Documents reviewed:**
- `docs/watcher-architecture-proposal.md` (the proposal)
- `docs/grok-review.md` (v3 score design, for context on the scoring layer the proposal builds on)

## TL;DR

This proposal diagnoses the right problem and prescribes the right architecture. The shift from "score everything, every minute" to "accumulate → update → trigger, silence is valid" is exactly what a resilient, accurate signal system needs. The calibration math is honest. The acceptance gates are correct. My one real concern is implementation order — I'd build the backtest harness *first*, not the layer-2 engine.

## What's Good

### 1. The diagnosis is data-backed, not hand-wavy
The proposal opens with measured numbers:
- 1h win rates below coin-flip (41.5%, σ≈0.8% → ~10σ under 50%)
- Inverted gradients: v2 40+ → 24.1% (1h), |lean| 0.7+ → 25.6%
- Terminal stop rate 91.9% (5,489 finished trades)

These aren't opinions — they're audit results. And the three upstream causes it identifies line up precisely with the v3 score defects documented in `grok-review.md`:
1. **Detector fires at exhaustion** — the `earlyness` term in v2 doesn't prevent breakout-chasing (grok-review §2 confirms this: proximity uses the current bar, so a freshly-made high gets earlyness ≈ 0)
2. **OI quadrant backwards** — (grok-review §1 confirms: rising OI + falling price is coded as +0.50 lean when it should be −1.00, full bearish)
3. **Stop distances too tight at 50× leverage** — structural risk issue, not a scoring bug

The v3 score (already approved as shadow) fixes defects 1 and 2. But as the proposal correctly notes, fixing the score definition alone doesn't fix the emission policy — you're still emitting ~11,000 signals/day, most of which are repeated measurements of the same setup.

### 2. The architecture is sound
Four layers:
1. **Accumulate** (running, untouched) — bars, ticker, OI, funding into store
2. **Update** (new) — incremental per-bar-close state refresh, in-memory with hourly SQLite checkpoints
3. **Trigger** (new) — named rules that emit one signal + frozen plan
4. **Measure** (exists) — outcomes, episodes, reports

The key insight: **Layer 2 is incremental by contract.** It processes only the newly closed bar per coin per cycle. Full recomputation is banned outside backtests. This is what frees up the compute budget for real analysis per decision.

As a trader, this matters because it means every emitted signal has a frozen plan computed at emission time. No drift. No "well the stop was there yesterday but the setup changed" — the plan is locked when the trigger fires.

### 3. The triggers are declared priors, not fits
Three triggers with stated hypotheses:
- **T1** — initiation + regime-gated long (strongest evidence)
- **T2** — OI-divergence spike (moderate)
- **T3** — funding-extreme fade (weak, exploratory)

Each states its hypothesis, inputs, cooldowns, and expected volume before seeing outcomes. The replay numbers for T1 (53.3% / +1.28% at 24h, n=1,827) are pre-registered, not cherry-picked.

The cooldown discipline is smart:
- T1: one signal per coin per 24h
- T2: one per coin per 12h
- T3: capped at 2/day

This alone attacks the "11,000 signals/day, 91.9% stop rate" problem. At ~10 signals/day, each signal actually matters.

### 4. The calibration math is honest
10 signals/day → distinguishing 55% from 50% at 95% confidence needs ≈400 events ≈ 40 days minimum per trigger. That's slow. That's honest. And the proposal accepts the consequences upfront:
- No live ranking before sample minimum
- Regime coverage, not row counts, is the gating resource
- v2/v3 shadow columns keep logging (comparison data is free)

### 5. The backtestability constraint is the right gate
"A trigger ships only on inputs replayable over the 90-day archive." This kills the "single REST book snapshot as a trigger" problem dead. Live book can *confirm* at emission but never *trigger*. That's correct — you can't backtest a non-reproducible snapshot.

## What Concerned Me (and how it's addressed)

### The v3 score is additive to the architecture, not a replacement
The v3 score (from `grok-review.md`) fixes the OI quadrant and earlyness defects. But it's still a *score*, not a *trigger*. The watcher architecture wraps it: the trigger layer decides when a score crosses a threshold with the right context (regime, age, OI flow state) to emit. This is the right separation of concerns.

The v3 formula:
```
inner = 0.45 × activity + 0.25 × location + 0.20 × opening_for + 0.10 × book_agree
raw = 100 × initiation × inner
score_v3 = round(raw, 1)
```

The `initiation = exp(−age / 18)` decay (age in 5-min bars) is exactly the kind of named, testable function a trigger should consume. It's not a fit — the weights are declared priors frozen before any outcome data.

### The acceptance gate is correct
Per trigger, before going live:
1. Replay over full archive: hit-rate + avg signed return at 4h/24h, split by regime
2. Bar: 24h avg > +0.5% with n ≥ 300, no regime split below −0.5%
3. 7 live shadow days (logged, never ranked)
4. Promote only on backtest/shadow agreement

This mirrors the v3 promotion discipline from `grok-review.md` §6: "switch the live rank and flag to v3 only when" specific conditions hold on the second half of data, both directions. Consistency between the two documents is good.

## Implementation Order Recommendation

The proposal lists two questions for the user:
1. Approve trigger set (§4) — yes
2. Approve acceptance bar (§5–6) — yes, especially the 400-event minimum

3. Build first: layer-2 engine or backtest harness?

**My answer: build the backtest harness first.**

Here's why: the triggers' prior values (T1: 53.3% / +1.28%) are only as good as the harness that validates them. If you build the layer-2 engine first, you'll be tempted to tune thresholds against live shadow data — exactly the overfitting trap the proposal is trying to avoid. If the harness exists first, it acts as a forcing function: you can't ship a trigger until it passes the replay gate, period.

The harness is also where you discover that your "1-hour OI change" input looks different in the archive vs. live, or that your `oi_change_1h_pct` null handling creates edge-case bias. Better to find that on historical data than on day 3 of a 40-day shadow period.

## What's Missing / What I'd Push Back On

### 1. Funding data depth
The proposal says "current funding snapshots" are available in the 90-day archive. For T3 (funding-extreme fade), this matters. If the archive only has daily snapshots, the 0.8 threshold (|funding|/cap > 0.8) may be systematically biased vs. live 8-hour funding rates. Verify the archive's funding granularity before T3 is anything other than "exploratory."

### 2. T3's "2/day cap" needs a mechanism, not a hope
Capping at 2/day requires either a queue with priority ranking or a coin-level cooldown so the same coin doesn't fill both slots. The proposal doesn't specify which. As a trader, I'd want to see this resolved before implementation.

### 3. No mention of execution venues or liquidity
A frozen plan is great, but if the stop distance is 0.5–6% (as noted in §1), execution matters. The proposal explicitly says no order path / no keys (consistent with the v3 design). But I'd want at minimum a slippage model in the measurement layer — even if the trigger layer stays dry.

> Note: This is outside the proposal's scope (it explicitly says "the collector, journal, outcomes, episodes, and reports stay exactly as they are"). I'm flagging it as a dependency for when you promote a trigger.

### 4. The "up to 50×" reference is a risk flag, not an architecture issue
§1 notes: "Stops sit 0.5–6% away at up to 50×; 40% land within the first hour." This is a leverage/concentration issue, not a signal quality issue. The proposal handles it correctly by not trying to fix it in the scoring layer. But if any trigger ever emits on a coin with thin liquidity at 50×, the 0.5% stop distance is a coin-flip with the exchange. Verify liquidity screens are in the plan-touch rules (episode_report.py, which the proposal says stays untouched).

## Verdict

**Approve.** The proposal is correct in direction and honest in its math. The architecture shift (emit less, measure each signal harder) is the right move for a system that needs to be both resilient (won't crash on 11,000 signals/day) and accurate (each signal has a frozen plan with a known edge).

The one concrete recommendation: **build the backtest harness first**, because the triggers' priors need to be validated against replay data before any layer-2 code ships. A trigger that can't pass its own replay gate shouldn't consume engineering time in the engine.

## Alignment with v3 Score Design

The proposal and `grok-review.md` are well-aligned:
- Both are shadow/approval processes (no live changes until validation)
- Both use pre-registered comparisons (second-half split, coin-cluster bootstrap, 2,000 draws)
- Both have named, frozen components (v3 weights; trigger inputs)
- Both keep the existing measurement system untouched

The watcher architecture is effectively **v3 score + discipline layer**: take the fixed scoring formula and wrap it in a trigger system that only emits when the score, the initiation age, and the OI flow state all align. This is how you move from "shadow score logged alongside v2" to "signals that actually have an edge."
