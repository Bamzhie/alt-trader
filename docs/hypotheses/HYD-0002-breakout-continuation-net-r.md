---
id: HYD-0002
title: Breakout continuation edges have no positive net edge after friction
status: rejected
prediction: Pre-declared continuation rules (breakout start, residual shock) have a positive mean net R after fees and tiered slippage on the development window
universe: all archived MEXC USDT altcoin perps in data/bars, BTC excluded from the traded universe and loaded only as the market factor
horizon: 24h
dev_start: 2026-07-19
dev_end: 2026-09-12
val_start: 2026-09-14
val_end: 2026-09-28
holdout_start: 2026-09-30
metric: mean_r
threshold: 0
denominator: tradeable_events_in_window
costs: taker 0.05pct per side plus tiered slippage by trailing-24h quote volume (30M/10M/3M/1M floors), funding is NOT modelled in the friction-only metric
comparisons: 288
rule_version: quant-v1
config_hash: d33cff513c30
---

# HYD-0002: Breakout-continuation net edge (REJECTED on the 2026-10-18 sweep)

**Why this is a separate hypothesis from `HYD-0001`.** `HYD-0001` is the
funding-quintile drift hypothesis. The 288-configuration sweep that has
already been run tested the *breakout / residual-shock continuation* rule
family, not the funding quintile. Registering that sweep as a variant of
`HYD-0001` would put a result under a hypothesis that was never tested, so
the sweep is recorded under its own id instead. `HYD-0001` is untouched and
still `testing`.

## What was declared before the sweep

Eight pre-declared rules x 36 pre-declared geometries = **288
configurations**, fixed in `proto/quant/config.py` (`GRID_HORIZONS`,
`GRID_K_STOP`, `GRID_R_TP`, `GRID_RVOL`) and never extended afterwards:

* rules: `breakout_start[rvol>=2|4]` and `residual_shock[cusum_h=5|8]`,
  each with the `fade` and the `cont` side policy;
* geometries: horizon bars 48 / 144 / 288, `k_stop` 1.0 / 1.5 / 2.0 / 3.0,
  `r_tp` 1.0 / 2.0 / 3.0;
* metric: mean net R after fees and tiered slippage (friction only),
  denominator = tradeable events in the window;
* selection: the largest day-cluster `t` on the development window with a
  max-t SPA multiplicity correction over all 288 configurations; only the
  selected configuration is re-evaluated on validation;
* promotion thresholds: the fixed gate in `proto/quant/gates.py`
  (`min_events` 200, `min_days` 25, `min_coins` 15, CI lower bound > 0,
  SPA `p <= 0.10`, positive mean with slippage doubled). They were fixed
  before the holdout was seen and are never relaxed.

The machine-readable version of all of this is the sidecar
`docs/hypotheses/HYD-0002.json` (the preregistration record), which also
carries the **variant registry**: every tested variant, failures included.

## Windows

Day coordinates are relative to the archive start (`union_min =
1783910100` = 2026-07-13T02:35:00Z, recorded in
`docs/implementation-reports/quant-roadmap-2026-10-18/archive-span.json`).
The calendar dates above are that start plus the quoted offsets: dev
`[6, 61)`, val `[63, 77)`, holdout from `79`. Both gaps are 2 days, which
clears the 1-day embargo of the 24h horizon.

## The outcome (as recorded, not as wished)

The sweep is **finished and it failed**. The preserved artifact is
`docs/implementation-reports/quant-roadmap-2026-10-18/evaluation.json`
(sha256 `81affa4ad5d1661f7a2b55f3e776f74bea65ef7ec136f7b1f70c1836f513242c`):

| Split | n | days | coins | mean_r | 95% day-cluster CI | t |
|---|---:|---:|---:|---:|---|---:|
| dev (friction-only) | 24,749 | 55 | 375 | -0.01542 | [-0.04171, +0.01424] | -1.062 |
| val (friction-only) | 6,495 | 14 | 248 | -0.01661 | [-0.04676, +0.01742] | -1.013 |

* **0 of 288** configurations had a positive development mean
  (`share_positive = 0.0`; the dev means run from -0.2336 to -0.0147).
* The selected configuration was `breakout_start[rvol>=4]/cont` with the
  geometry `T288_ks3_R2`; its SPA p-value is **1.0**.
* Every interval straddles zero and every point estimate is negative: the
  run found **no evidence of a positive edge**.

## The holdout was NOT spent

`holdout`, `holdout_slip2x`, `holdout_funding_adjusted` and
`holdout_slip2x_funding_adjusted` are all `null` in the artifact. The
one-shot holdout (from 2026-09-30) stays sealed: nothing cleared
dev -> val, so there was nothing to spend it on. The holdout is evaluated
at most once, by `proto.quant.research.holdout_once`, under its own
one-shot lock.

## Honest scope

This record is a **retrospective** registration: it was written on
2026-10-19, *after* the run it documents, and it transcribes that run's
preserved artifacts. It is not a prospective pre-registration and must not
be read as one. The failure it records is a result about this archive
snapshot and this rule family, not a proof that no continuation edge can
exist. The artifact's funding-adjusted blocks predate the 2026-10-19
cost/split audit and are superseded by `funding-transitions.json`; the
friction-only rows quoted above are unchanged.
