# Quant Analytics System Proposal

**Date:** 2026-10-13  
**Status:** Proposal for review; no implementation authorized by this document  
**Product focus:** Quantitative analysis of MEXC altcoin perpetuals, including the Russell Pate Fractal System as a named research model

## 1. Purpose

Make quantitative measurement and statistical evidence the system's primary basis for analyzing altcoin perpetual markets. The system should describe market structure, estimate distributions of possible outcomes, quantify costs and risk, and show uncertainty. Rule-based triggers support event definition and explanation; they do not substitute for the analytical engine.

The system remains research and decision support. It places no orders and makes no profitability claim without robust out-of-sample evidence.

## 2. Design principles

- **Quant first:** prioritize measured return distributions, volatility, dependence, market regime, outcome probabilities, expected net return, and uncertainty.
- **Rules support the analysis:** CUSUM, breakout conditions, confirmed pivots, and vetoes define or annotate events. A trigger firing alone is not evidence of edge.
- **Alt-perp first:** use the specific traded perpetual contract's data for price, volatility, geometry, and outcomes. BTC is contextual or a comparison factor, not the default traded asset.
- **Causal and reproducible:** use only information available at the calculation timestamp; version every data rule, feature definition, parameter set, and evaluation cohort.
- **No forced estimates:** inadequate data or sample size produces “insufficient evidence,” not a heuristic score or fabricated probability.
- **No ML requirement:** begin with statistical estimators, empirical distributions, and resampling. Consider ML only if it improves out-of-sample results over these baselines.

## 3. Proposed system layers

### 3.1 Point-in-time data foundation

Build an auditable history for each MEXC USDT perpetual contract:

- Closed 5-minute OHLCV bars and derived 15-minute, 1-hour, 4-hour, and daily views.
- Contract metadata, listing and delisting dates, symbol changes, and point-in-time universe membership.
- Funding rates and actual settlement timestamps by contract.
- Spread and order-book snapshots with timestamps and coverage measures.
- Open-interest observations with source and observation-time gaps.
- Mark/index prices where available; clearly label trade-price proxies where unavailable.

Store source timestamps, ingestion timestamps, data-quality status, and provenance. Keep delisted contracts in historical evaluation. Missing, stale, or mismatched inputs remain explicitly unavailable.

**Gate:** produce coverage reports by contract, input, and date. Do not describe a historical result as net of costs that were not historically observed or defensibly proxied.

### 3.2 Quantitative fractal and market-state engine

The primary analytical engine measures the alt-perp's structure across scales. Russell Pate's system is the reference for the kind of algorithmic-geometry analysis desired—visualizing price structure and separating structure from noise. The implementation here is an independently specified quant system, not a claim to reproduce Russell Pate's proprietary calculations.

Compute transparent, versioned measurements for each asset and horizon:

- **Multi-scale structure:** confirmed swing pivots and nested structure on 5m, 15m, 1h, and 4h bars; quantify swing size, duration, expansion/contraction, and alignment across scales. A pivot becomes available only after all right-side confirmation bars close.
- **Fractal scaling:** rolling generalized Hurst estimates and MF-DFA measures, including multifractal spectrum width, across preregistered return horizons. Treat them as estimates of persistence, roughness, and scale behavior, with uncertainty—not as direct directional forecasts.
- **Return distribution:** close-to-close log returns and forward returns over fixed horizons, with conditional summaries by asset, volatility regime, and market state.
- **Volatility and tails:** realized variance, EWMA volatility, robust range-based volatility, jump/outlier diagnostics, and empirical tail quantiles. Keep estimator assumptions and horizon units explicit.
- **Cross-sectional context:** alt-market breadth, liquidity groups, return correlation, and dependence clusters. Use point-in-time constituents and exclude the subject asset from its own benchmark.
- **Perpetual economics:** direction-aware fees, spread, slippage, funding settlements, and holding-horizon costs. Separate gross return, execution cost, funding PnL, and net return in storage and reporting.

For swing fractals, an initial comparison method may define a five-bar pivot high as a center high exceeding the highs of the two bars on either side, with the low version reversed. Tie handling, sampling horizons, and alternative definitions must be preregistered before evaluation. Pivots and scale measures are descriptive until they pass the validation process below.

Russell Pate's Fractal Software is publicly described as proprietary. Include it as a separate, versioned external comparator only through authorized access, documentation, or timestamped exports; do not reverse-engineer it from marketing descriptions. The public product description does not specify the calculations or an integration API. [Product description](https://whop.com/fractalx/fractal-software/?funnelId=product_5f1d3a6c-0e50-4db2-9336-87b1d8ad5de3). MF-DFA is a published method for characterizing scaling structure in financial time series, not proof of predictive power. [MF-DFA research](https://www.sciencedirect.com/science/article/pii/S0378475416000598)

### 3.3 Event and setup layer

Maintain the stable, closed-bar setup lifecycle so repeated fetches do not create signal flicker. The quant market-state engine supplies the primary analysis; event rules are supporting instruments for sampling, segmentation, and explanation:

- CUSUM events identify unusual standardized moves.
- T1 breakout conditions identify the specified proposal-defined long setup.
- Confirmed fractal pivots identify structural swing levels after confirmation; they do not by themselves determine setup quality.
- Liquidity, spread, stale-data, and volatility vetoes indicate whether an event can be evaluated safely.

Each event records the exact rule version, inputs, trigger timestamp, asset, direction, and data-quality state. Multiple trigger families remain separately identifiable for ablation and comparison. Quant market-state measurements are joined to each event as-of its timestamp. The system must not blend either event rules or fractal measurements into one undocumented heuristic score.

### 3.4 Outcome distributions and uncertainty

For every eligible event, record competing outcomes: stop, target, time exit, thesis invalidation, and true censoring such as end-of-sample or unresolved data loss. Use conservative same-bar ordering when OHLC cannot reveal which barrier came first.

Estimate, where the sample supports it:

- Probability of each outcome by the chosen horizon.
- Gross and net return distributions, including realized time-exit returns.
- Expected net return and risk measures such as downside quantiles and drawdown.
- Uncertainty intervals that account for repeated events by coin and date.

Start with empirical conditional distributions and shrinkage across related groups where needed. Calibrate probabilities on chronological validation data. Do not use a two-outcome EV formula when time exits or invalidations have material outcomes; include those outcomes in the expected-return calculation.

### 3.5 Research and validation harness

Every new measurement, fractal feature, event trigger, or parameter set is a registered experiment:

1. State the hypothesis, feature definition, universe, horizons, costs, evaluation statistic, and promotion rule before looking at holdout results.
2. Use chronological development, validation, and holdout periods. Purge overlapping outcome windows and apply an embargo at least as long as the maximum label horizon.
3. Compare against simple baselines: no-event/unconditional outcomes, existing event rules without the new feature, and matched placebo events.
4. Evaluate by coin, time period, volatility regime, and liquidity group. Use coin/day-clustered uncertainty and correct for testing many variants.
5. Report sample size, data coverage, gross/net results, calibration, uncertainty, and all tested variants—including failures.

Evaluate the Russell Pate system as its own model first, then test whether combining its outputs with the quant baseline or independently computed fractal measures adds value. Record exact software/version, market, timeframe, parameters exposed by the product, output availability time, and any manual interpretation. Do not treat screenshots or retrospective chart markings as point-in-time signals. If authorized outputs cannot be exported or captured with reliable timestamps, keep the system as a documented external comparator and do not claim quantitative integration.

Fractal or other features are retained only when they add robust out-of-sample value over the baseline after costs. If evidence is mixed or uncertainty spans no improvement, keep the feature descriptive or reject it as a decision input.

### 3.6 Quant-first dashboard

Make quantitative results the primary view. For an asset or setup, display:

- Estimated outcome probabilities and horizon, when supported by data.
- Expected gross and net return, with costs and funding assumptions separated.
- Uncertainty interval, sample count, evaluation period, and calibration status.
- Volatility, tail-risk, dependence, regime, and fractal/scale diagnostics.
- Triggering rules as explanatory context, plus input coverage and freshness.

When estimates do not pass data or evidence thresholds, show **insufficient evidence** and the reason. Do not replace unavailable analytics with confidence tiers, heuristic probability bars, or marketing-style “conviction” labels. Keep hypothetical outcomes distinct from realized trading P&L.

## 4. Research cadence and system upgrades

Review new evidence every few days or weeks, depending on the number of matured, independent outcomes. The calendar cadence starts a review; it does not automatically authorize retuning.

For each proposed upgrade:

1. Preserve the current production/research version as the **champion**.
2. Create an immutable **candidate** version with a new rule/config hash and a written hypothesis.
3. Evaluate candidate and champion on identical, later data and matched costs.
4. Use a predeclared promotion gate based on after-cost results, calibration, uncertainty, and robustness across coins/regimes—not a single in-sample metric.
5. Promote only after the candidate passes the untouched evaluation and prospective shadow period; otherwise retain the champion and record the rejection.
6. Never retune on the same holdout and then continue calling it an untouched holdout. Roll forward to a new future evaluation period after promotion.

Record versions, parameter changes, decision dates, data epochs, and reasons for promotion or rejection. The scanner benchmark and prior cohorts remain separately labeled.

## 5. Delivery sequence

1. **Research contract:** define estimands, outcome classes, universe rules, costs, evaluation splits, uncertainty method, and promotion gates.
2. **Data audit and archive:** inventory existing histories, identify missing funding/book/mark data, and start point-in-time collection.
3. **Measurement core:** implement and verify returns, volatility, tails, dependence, regimes, and data-quality reporting.
4. **Fractal quant engine and Russell Pate integration:** implement multi-scale structure, confirmed pivots, and scaling diagnostics; verify authorized access and a timestamped output path for the Russell Pate system; validate estimators on synthetic and historical series; run surrogate and ablation tests.
5. **Outcome and inference layer:** build competing-outcome evaluation, net economics, calibrated probabilities, uncertainty, and baselines. Evaluate the Russell Pate system as its own model before any combination.
6. **Quant-first dashboard:** expose estimates, uncertainty, sample coverage, fractal-model provenance, and supporting rule explanations; show insufficient evidence when gates fail.
7. **Shadow cycles:** record live-time estimates without orders, review matured outcomes on the agreed cadence, and promote only versioned candidates that pass the predeclared gates.

## 6. Success criteria

The system is considered quant-analytic when its primary outputs are reproducible quantitative estimates with provenance, uncertainty, and out-of-sample evaluation—not merely when it contains technical indicators or fractal plots.

No feature is called predictive or useful for setup selection unless it improves the preregistered evaluation against the baseline after realistic costs and survives the holdout/prospective checks. No deployment or order execution is included in this proposal.

## 7. Open decisions before detailed specification

- Which horizons and outcome barriers are primary for the initial research release?
- Which contracts and venue data are available with adequate historical depth, funding, and mark/index coverage?
- What authorized Russell Pate Fractal System access, usage rights, supported markets/timeframes, and timestamped output/export path are available?
- Which fractal/scale estimators and parameter ranges will be preregistered for the first experiment set?
- What minimum sample, calibration, after-cost effect, and uncertainty requirements constitute promotion?
- What exact review cadence and prospective shadow duration fit the rate at which independent outcomes mature?
