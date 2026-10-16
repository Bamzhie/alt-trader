# Quant research result, 2026-10-12

**Question:** do volume-confirmed breakouts or BTC-residual volatility shocks
(either direction) earn positive net R after realistic costs?
**Answer on this archive: no.** No pre-declared configuration is positive.

## Data and protocol

- 82-coin sample of the archive (BTC, ETH, the 60 coins with most logged plans,
  20 low-liquidity coins), 2026-07-13 -> 2026-10-12 (91.4 days), 5-minute bars, gap-free after sorting.
  **Selection caveat:** the sample is biased toward coins the scanner flagged;
  one 91-day market path; liquidity is thin (28 coins >= $1M/day, 21 >= $3M,
  7 >= $10M by 14-day median daily quote volume).
- Splits by day: dev 6-61 (55 d), embargo 2, validation 63-77 (14 d), embargo 2,
  **holdout 79-91.4 (12.4 d, untouched)**. Embargo >= the longest label (1 day).
- Candidates: `breakout_start` (first close outside the prior 48-bar channel,
  time-of-day RVOL >= 2 or 4) and `residual_shock` (CUSUM on BTC-residual
  returns, h = 5 or 8), each with `fade` and `cont` policies = 8 rule variants;
  x 36 pre-declared geometries (horizon 48/144/288 bars, stop 1-3 sigma*sqrt(T),
  target 1-3R) = **288 configurations**. 14,736 labeled events (liquid >= $1M/day).
- Labels: entry at next bar open, stop-first on ties, time exit at horizon,
  net of fee 0.05%/side + tiered slippage (0.015%-0.12%/side). Funding not modeled.
  Fee tier is an assumption (not confirmed).

## Results

| Stage | Result |
|---|---|
| All 288 configs on dev | **0% positive**; median -0.0625R, best -0.0115R |
| Best-of-288 (max-t, day-cluster) | t = -0.59, **p = 1.00** |
| Dev-selected: `residual_shock h=8 / fade`, T144 ks3 R1 | dev -0.0136R [-0.0624, +0.0308], n=562 |
| Validation of that config | **-0.0223R [-0.1434, +0.0832]**, n=173, 14 days |
| Cost sensitivity (dev, selected) | slip x0.5: -0.0022R; fees only: +0.0091R; **zero cost: +0.0227R [-0.0256, +0.0706]** |
| Holdout | **not spent** (validation did not keep the config) |

**TP-hit view.** Among events that reach a barrier, the TP-first share equals the
driftless null: fade, rvol>=4: 0.498 vs 0.500 (T288 ks1 R1), 0.465 vs 0.500
(T288 ks1.5 R1); continuation: 0.499 / 0.532. The event types behave like a random
walk; the shortfall is not just costs (the dev-selected config at zero cost is only
+0.023R, with an interval spanning zero). Over 90% of wide-stop trades time out rather than hit a barrier,
and tight stops (1 sigma*sqrt(48), fade rvol>=4) lose about -0.13R, where friction is a large
fraction of the stop distance.

**Scorer (meta-labeling check).** Ridge on 6 side-signed features, trained on dev
(n=6,686), tested on validation (n=2,296): calibration slope 0.62; realized net R by
predicted tercile -0.085 / +0.029 / +0.008; top tercile +0.008R [-0.030, +0.041].
It separates clearly bad events from average ones but finds no profitable region.

**Exploratory, not pre-registered (dev only):** continuation of volume-confirmed
breakout starts lost money at 4 h and 24 h (rvol>=4, 24 h: gross -66.6 bps, net
-80.6 bps, CI [-138, -17] at a flat 14 bps cost); the proposal's T1/v3 hypothesis
(follow the initiation) is contradicted, and fading is gross-positive but not
net-positive after tiered costs. Treat as a lead, not a finding.

## Consequences

1. No rule is promoted; the watcher emits SHADOW events only, with controls.
2. T1 (initiation + BTC-regime, long-only continuation) should not be built as specified.
3. Evidence now accrues from the whole candidate stream, not from emissions:
   re-run `research` as the archive grows; the 12-day holdout stays reserved and
   is only meaningful once validation keeps a config.
4. The remaining ways to find edge need inputs this archive lacks (OI/funding
   history, order-book snapshots) or a different horizon/universe; none were
   claimed or tested here.

## Defect found and fixed during this work

`collector.read_bars` returned rows in file order, but backfill pages are appended
after forward-collected rows, so real bar files are not time-ordered (about 13
out-of-order chunks per file). `outcomes.resolve_plans` walks bars in that order,
so first-touch ordering (stop vs targets) could be wrong for plans whose forward
bars cross a chunk. `read_bars` now returns ascending, de-duplicated bars
(`tests/test_collector_order.py`). Legacy plan outcomes computed before this fix
for such plans may be wrong and are worth re-resolving.
