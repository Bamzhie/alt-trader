---
id: HYD-0003
title: Manual-review signal family on the collected altcoin history
status: registered
prediction: A point-in-time structure filter applied to the registered breakout/shock rules produces a positive expected net R per trade on the dev window that survives the validation window, the family-level multiple-testing correction and the matched-placebo comparison.
universe: MEXC USDT perpetuals in the archived bar universe, restricted to the preregistered point-in-time coverage rule (>= 60 complete days of 5m bars covering the dev window, no malformed rows); BTC excluded
horizon: 24h
dev_start: 2026-07-19
dev_end: 2026-09-12
val_start: 2026-09-14
val_end: 2026-09-28
holdout_start: 2026-09-30
metric: mean_net_r_per_trade
threshold: 0.0
denominator: tradeable_events_in_window
costs: taker 0.05% per side (the planner's UNCONFIRMED constant), per-side slippage tiered by trailing-24h quote volume (an assumption, not a measurement), funding from the venue settlement archive on its own per-cycle schedule; friction-only and funding-adjusted results are reported separately
comparisons: 5
rule_version: quant-v1
config_hash: d33cff513c30
---

**This record is frozen before any new result is examined.** The machine
readable sidecar is `HYD-0003.json` (written by `proto/quant/prereg.py`);
it carries the data and code hashes of the run. Anything changed after the
run is visible in the git history, and a changed result under an unchanged
hash is a protocol violation.

## What is frozen

**Universe and point-in-time membership.** Every coin in the archived bar
universe (`data/bars/*.csv.gz`, MEXC USDT perps, BTC excluded) whose bars
satisfy the coverage rule **at the time of the run**: at least 60 complete
days of 5m bars covering the dev window, no malformed rows, and a latest
bar inside the archive span. Membership is decided by the rule, never by
performance; a coin that becomes eligible later is not added mid-analysis
and a coin that falls below the rule is reported, not silently dropped.
Every selected coin, every excluded coin and the reason are reported.

**Splits (chronological, from the archive start 2026-07-13 02:35 UTC).**
dev = days [6, 61) = 2026-07-19 .. 2026-09-12; validation = days
[63, 77) = 2026-09-14 .. 2026-09-28; embargo 2 days between windows;
holdout = days [79, end] = 2026-09-30 onward, **sealed**. The splits are
the configured `proto/quant/config.py:Splits` boundaries, applied to the
measured archive span.

**Rules, direction, entry and exit.** The four registered rules in
`proto/quant/registry.py` at their registered geometries, plus one NEW
preregistered member. Side policy, direction, entry, stop, target and time
exit are the registered ones: entry at the open of the bar after the
signal, stop at `k_stop * sigma * sqrt(T)`, target at `r_tp` times the
stop distance, time exit at the close of the last bar of the horizon. When
a bar touches both the stop and the target, the **stop wins** (the
conservative convention in `proto/quant/barrier.py`). No leverage is
applied to the per-trade figures: the unit of measurement is **R (return
per unit of risk taken at the stop)**, and the equity-denominated return
is not computable without a position-sizing and liquidation model, which
this study does not claim. A futures return is therefore never stated
without its denominator, its stop and liquidation risk, its costs, its
holding period and its leverage assumption.

**The declared family (5 members).**

| # | member | status |
|---|---|---|
| 1 | `breakout_start[rvol>=4]/fade` @ T288_ks1.5_R1 | previously examined -> **exploratory** |
| 2 | `breakout_start[rvol>=4]/cont` @ T288_ks1.5_R1 | previously examined -> **exploratory** (control) |
| 3 | `residual_shock[h=8]/fade` @ T144_ks3_R1 | previously examined -> **exploratory** |
| 4 | `residual_shock[h=8]/cont` @ T144_ks3_R1 | previously examined -> **exploratory** (control) |
| 5 | `breakout_start[rvol>=4]/fade` @ T288_ks1.5_R1 **+ point-in-time structure filter** (see below) | **new, preregistered here** |

**The new member's feature (point-in-time safe).** Member 5 fires only
when the **confirmed 5m pivot state** at the signal bar is a *pivot low*
within the previous 200 bars (the last confirmed pivot before the signal is
a low). The pivot set is computed from bars whose close is at or before
the signal bar only, and the pivot's own confirmation time is at or before
the signal bar, so the filter cannot see the future. The window (200
bars) and the pivot settings (`left=2`, `right=2`, base frame) are frozen
here. Members 1-4 do not use the filter.

**Costs.** Fee 0.05%/side (the planner's **unconfirmed** constant), per-side
slippage tiered by trailing-24h quote volume (an **assumption**, not a
measurement), and funding from the venue settlement archive on its own
per-cycle schedule. Friction-only and funding-adjusted results are reported
**separately**; an event whose funding cannot be attributed is excluded
from the funding-adjusted denominator and counted, never zero-filled and
never relabelled as friction-only.

**Metrics and uncertainty.** Per member and per split: number of events,
number of tradeable events, number of attributed (funding-adjusted)
events, distinct coins, distinct days, mean net R with a **day-cluster
bootstrap 95% interval** (B=2000) and a **coin-cluster bootstrap 95%
interval**, the competing-outcome cause shares (TP / SL / TIME /
INVALIDATED / CENSORED) with their denominators, and the family-corrected
p-value (SPA max-t over the 5 members, days resampled jointly, plus a Holm
fallback).

**Baselines.** (a) *unconditional*: the same events with no conditioning;
(b) *rule without the new feature*: member 1 evaluated without the
structure filter; (c) *matched placebo*: events re-drawn to match the
observed (coin, time-of-day, liquidity) distribution on a different date,
so the placebo keeps the rule's average edge and only breaks the temporal
alignment; the observed mean must beat 95% of the placebo means.

**Minimum sample sizes.** A member is only evaluable on a split with at
least **200 tradeable events**, **15 distinct coins** and **20 distinct
days**; below that the cell is reported as `insufficient` with its
denominators and **no mean**.

**Decision criteria to FREEZE a configuration (all must hold on dev and
on validation).**
1. dev mean net R (friction-only) > 0 **and** the dev day-cluster 95%
   interval's lower bound > 0;
2. validation mean net R (friction-only) > 0 **and** the validation
   day-cluster lower bound > 0;
3. the member meets the minimum sample sizes on both splits;
4. the family-corrected p-value (SPA max-t over the 5 members) <= 0.10;
5. the matched-placebo comparison: the observed mean exceeds at least 95%
   of the placebo means (p <= 0.05);
6. the funding-adjusted mean is not materially worse than the friction-only
   mean (funding delta >= -0.002 R per trade);
7. the member is not merely its own mirror image: a `fade` member must
   beat its paired `cont` control on both splits.

**Decision criteria to SPEND the one-shot holdout.** All seven criteria
above must hold on dev **and** validation. The winning configuration is
then frozen (parameters + hash) and evaluated on the holdout **exactly
once**. If no member satisfies the criteria, **the holdout is not spent**,
and the reason and the missing evidence are reported instead. The holdout
is never used to select, tune, threshold or modify a configuration, and no
result is reported as "validated" unless it passes the criteria above.

**Multiple testing.** The 5 members form the declared family; the
correction is applied to the family, not per member. Members 1-4 were
examined in the 2026-10-18 sweep, so their individual results are
**exploratory** by construction; only member 5 is a genuinely new
preregistered test, and it is still corrected within the family.

## What is NOT claimed

No result of this study guarantees future returns. A target of +100% or
more is a research objective, not an expectation, and no figure here
supports it: the units of measurement are R per trade, the equity-denominated
return is not computable without a position-sizing and liquidation model,
and the costs are assumptions rather than measurements.
