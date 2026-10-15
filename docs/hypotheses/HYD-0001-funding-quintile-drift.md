---
id: HYD-0001
title: Top funding-rate quintile drifts up over 24h
status: testing
prediction: Coins in the top quintile of observed funding rates outperform the bottom quintile on median 24h forward return
universe: all MEXC USDT perps in build_universe, BTC excluded
horizon: 24h
dev_start: 2025-10-01
dev_end: 2025-12-01
val_start: 2025-12-05
val_end: 2026-02-01
holdout_start: 2026-11-01
metric: median_forward_return_24h
threshold: 0.001
denominator: resolved_episodes
costs: taker 0.05pct per side, funding from the data-funding observation archive when available else stated estimate
comparisons: 3
rule_version: v2.0.0
config_hash: d413bf95f535a40b
---

# HYD-0001: Funding-quintile drift (ILLUSTRATIVE example)

**This hypothesis is illustrative.** It ships as the worked example for
the registry format (WS5 increment 1): every field is filled so the
validator has a full `testing`-status file to check, but no evaluation
has been run and no claim is made. The evaluation harness is a later
increment; until it lands, this entry must not be read as evidence.

## Rationale

Persistently high funding rates mean longs pay shorts; if crowded-long
positioning drifts price upward over the next day, the top funding
quintile should show a positive median 24h forward return net of the
stated costs. Pass criterion is `median_forward_return_24h >= 0.001`
(10 bps) over `resolved_episodes` (episodes that reached a terminal
close, so the denominator excludes still-open and coverage-lost
episodes). Three related quintile cuts were examined alongside this one
(`comparisons: 3` — the hypothesis itself counts), so any later
evaluation must account for multiple comparisons.

## Windows

Half-open day intervals per the registry spec: dev covers
[2025-10-01, 2025-12-01), validation [2025-12-05, 2026-02-01), holdout
starts 2026-11-01 with no end date (open means unseen future data —
holdout must postdate the present, so the start was set after the
example's frozen dev/val windows, not adjacent to them).
Ordering holds (2025-12-01 <= 2025-12-05 <= 2026-02-01 <= 2026-11-01)
and both gaps (4d dev/val, 273d val/holdout) clear the 1-day embargo
for the 24h horizon.

## Version pins

`rule_version` v2.0.0 is `EPISODE_RULE_VERSION` from
`proto/measurement.py` (sibling pins at time of writing:
`PLAN_RULE_VERSION` v1.0.0, `OUTCOME_RULE_VERSION` v2.0.0,
`COST_MODEL_VERSION` v0.1.0-unstable, `CODE_REV`
2026-10-10-score-v3-shadow). `config_hash` d413bf95f535a40b is the
`proto.measurement.config_snapshot` hash of the measurement defaults
(stake 0.10, log_threshold 24.0, leverage_cap null, universe_budget 0,
universe_policy mexc_full_stakeless_v2) — recompute with those inputs
to verify. Costs assume taker 0.05pct per side with funding taken from
the `data/funding/` observation archive (`ts,rate,cap` per coin) when
available, else a stated estimate recorded alongside the evaluation.
