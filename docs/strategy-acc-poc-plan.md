# Plan v2: Accumulation → POC breakout → retest long ("ACC-POC")

**Status:** deferred plan only; no code written. During quant-engine
development, **no rules run or emit candidate signals**, and ACC-POC is
inactive. Only after the quant engine is ready may ACC-POC be introduced as
the first **long-oriented guidance mechanism represented inside the quant
analysis engine**. Its accumulation → POC breakout → retest concepts can help
identify potential long candidates, but the engine is not required to follow
the setup literally: it may adapt, combine, reweight, or replace calculations
as it tests them. ACC-POC does not declare a coin profitable. Its mode when
eventually built remains research/shadow and read-only; there is no order
execution. Under the overhaul plan, the new engine is built and evaluated only
from `data/bars/`; legacy `signals.db` and `quant.db` outcomes are not training
inputs.

Combines the two earlier plans: the data-first sequencing and extra metrics of the shorter one, with the numeric definitions, point-in-time rules, controls and validation discipline of the longer one.

## 0. Outcome and honesty

This plan delivers (1) a causal detector that reads your accumulation → POC retest sequence and flags each stage early, (2) a way to check it against your own eye on real charts, and (3) a measured answer to whether it beats controls after costs. It cannot promise the setup works: the rest of this archive behaved like a random walk after costs, so the setup earns no presumption of edge until it passes validation.

## 1. Step 0 (gate): can we measure volume at price?

The POC depends on volume at price, and 5-minute OHLCV bars do not contain it. Settle this before any profitability work.

1. **Probe the venue** (needs your machine; my sandbox cannot reach MEXC): does the contract kline endpoint serve 1-minute bars (`Min1`), how far back, at what rate? The app's `mexc.INTERVALS` has no 1m entry today, and one request returns at most 2000 bars (about 33 hours of 1m), so a 2-10 day range is 2-8 requests.
2. **Fetch 1m bars only around detected ranges** (not the whole universe), cached to disk, via the same rate-limited client. Never replace the 5m archive.
3. **Compare three POC methods** on a pilot set of detected ranges:
   - **M1 (preferred):** profile built from 1m bars, each 1m bar's volume spread across its own tiny range.
   - **M2 (fallback):** 5m bars, volume spread uniformly over [low, high].
   - **M3 (robustness):** 5m bars, volume spread triangularly around the body.
4. **Report agreement:** POC shift in σ-units and how often POC, VAH, VAL land in the same bin. **Gate:** if methods disagree materially, restrict the study to periods and coins with M1 data and treat M2/M3 as sensitivity checks only; if M1 is unavailable, proceed with M2 and say so everywhere.
5. Every event stores `poc_method`. An approximate POC is **never presented as exchange-measured volume at price**; even M1 is an OHLCV-based estimate (trade-level history is not assumed).

## 2. The setup as observable, timestamped events

All thresholds are volatility-normalized (σ from `stream.py`, or ATR on the resampled frame) and computed only from bars closed at time t. Every threshold is part of the configuration identity: changing one creates a new hypothesis version.

**States:** `range_candidate → accumulation_confirmed → manipulation_sweep (optional) → breakout_confirmed → poc_retest → bullish_reaction (entry) → invalidated | expired`.

| Stage | Rule |
|---|---|
| **Accumulation range** | On 15m frames (`structure.resample`): window of 2-10 days, grown while sideways: Kaufman efficiency ratio ≤ 0.25, net drift ≤ 0.35 of range height, height ≤ k × the 30-day median daily range, ≥ 3 touches in each boundary zone (outer 15%). Support/resistance use closing-body extremes so wicks beyond them are sweep candidates. |
| **No look-ahead on the range** | The start of an accumulation is only knowable later. The range is whatever the criteria satisfy using bars up to t; boundaries are recorded with the time they became knowable and are not redrawn with future data. A leak test proves features at t do not change when later bars change. |
| **Fixed-range volume profile** | N = 24 price bins (tick-aware), summed from range start to the last bar before the break; **POC** = peak bin after a 3-bin smooth; VAH/VAL = 70% value area. **Frozen at the break bar.** |
| **Manipulation sweep** | A low below support by ≥ 0.25σ·√bars that closes back inside within 3 bars, usually on elevated volume. Its low is the **manipulation low** (the invalidation level). **[variant]** no sweep: stop falls back to below range support. |
| **Breakout** | First 15m close above resistance with volume ≥ 1.5× range median. **[variant]** a close above the POC then a higher high. No entry here. |
| **POC retest** | Return into the POC zone (POC ± 0.25σ·√bars) within ≤ 3 days without a close below POC − 1 bar-σ. |
| **Bullish reaction (entry)** | In the zone: lower wick ≥ 50% of the bar, close in the top third and above POC. **[variant]** bullish engulfing. Entry at the next 5m open (market), the system's standard fill convention; a limit-at-POC fill is recorded as a labeled variant. |
| **Stop / target** | Stop = manipulation low − 0.2σ·√bars. Targets defined before testing: T1 = first confirmed 1h swing high above entry (`structure.confirmed_pivots`), T2 = range resistance + 1.0 × range height. Skip if reward:risk to T1 < 2 or friction > 0.10R. |
| **Distribution / cancel** | Before entry: close below POC − 1 bar-σ, an upthrust (failed break closing back inside on a volume climax), a lower high on rising volume, or timeout. Each is logged as the invalidation reason. |

**Distribution operationally (for "early before distribution"):** the first close below range support − 0.5σ after the break, or an upthrust, whichever comes first. This makes "early" a measurable quantity.

## 3. How it fits the intended quant system

Reuses `bars.py`/`stream.py` (closed-bar feed, σ), `structure.py` (resample, confirmed pivots), `feature_store.py` (point-in-time features with `available_ts`), and appropriate selector/risk/ROE utilities. Only after quant-engine readiness, ACC-POC may supply long-oriented guidance inside the quant engine. The engine may adapt its calculations and evaluates one selected signal per coin in each benchmark cycle; each changed calculation is versioned. The existing HYD-0003 rule-family and promotion protocol does not automatically govern this new benchmark; see [Quant System Overhaul Plan](quant-system-overhaul-plan.md) for the proposed mirror database, eight-hour sync, staged coin benchmark, and unresolved evaluation decisions.

New pieces:
1. `range_profile.py`: pure functions for volume spreading (M1/M2/M3), profile, POC, value area, sideways tests.
2. `accum.py`: streaming per-coin state machine, advanced once per closed bar, every transition timestamped.
3. `label_levels`: structural stop/target labeler. The existing labeler uses σ-based distances; this strategy has price-level stops. Same conventions: entry at next open, stop-first on ties, time exit, tiered costs plus funding attribution.
4. **Post-readiness only:** ACC-POC long guidance integrated with the quant engine. Candidate and backtest records include the POC method, range start/end and boundaries, manipulation low, breakout and retest timestamps, entry assumption, stop, targets, costs, `roe_100` and invalidation reason where applicable. The engine may adapt these calculations, records each version, and evaluates one selected signal per coin in a cycle. The legacy registered-rule mechanism may be reused only if it serves as guidance/candidate intake; it must not make the profitability verdict. No ACC-POC rule is active during engine development.

Showing watch states live ("coin X is pulling back to POC") would be a watchlist; the Research view is counts-only today, so that needs your explicit approval and is off by default.

## 4. Evaluation

**Population:** every entry the machine would have produced across the whole liquid universe (all coins, not the 82-coin sample), cost-gated.

**Preregistered hypotheses (comparisons counted):**
- **H1 retest vs chase:** the retest entry beats entering at the breakout close for the same setups.
- **H2 sweep:** setups with a manipulation sweep beat the no-sweep variant.
- **H3 POC:** retest of the POC beats retest of a matched non-POC level (range midpoint or VAH).
- **Base rates first:** how often ranges resolve up vs down, how often a break retests at all, how many setups fail before entry.

**Metrics:** stage counts and survival to entry; net R after fees, slippage and funding; stop-first / target-first / timeout shares; maximum adverse and favorable excursion; time from range confirmation to entry; the share of entries made before the distribution proxy and before the subsequent high; results by liquidity tier, market regime and `poc_method`; the `roe_100` leverage needed and stop cost on margin per setup.

**Controls:** breakout continuation without retest; matched random entries (same coin, time of day, regime, same stop/target distances); the unconditional ROE base rates (`proto.quant roe`); the mirrored distribution/short setup.

**Discipline:** at most ~8 pre-declared variants with a max-t test over all of them; chronological dev / validation / sealed holdout with embargo; holdout evaluated once; day-cluster intervals; a **power check before any verdict** (if dev has fewer than ~150 events across ≥ 40 coins and ≥ 30 days, the answer is "insufficient data, keep collecting"). Promotion uses the existing gate unchanged.

## 5. Detector review before any evaluation

You supply 20-30 setups you consider textbook plus ~10 you would reject. The detector must reproduce most of them (fidelity to your reading, not profit). Then review false positives and misses on annotated charts, adjust the spec only against that review, **freeze it into a `HYD-xxxx` hypothesis**, and only then run validation. After freezing, any change is a new version with fresh evidence.

## 6. Work packages

| # | Package | Acceptance |
|---|---|---|
| WP0 | Data gate: probe 1m availability; pilot M1/M2/M3 comparison | Written verdict on which POC method the study uses |
| WP1 | `range_profile.py` | POC/VA correct on constructed profiles; methods agree on edge cases |
| WP2 | `accum.py` state machine | Causality/leak test; restart replay equals batch; gap handling |
| WP3 | Audit tool | Stage counts per coin/day; annotated chart export; golden-set agreement report |
| WP4 | Review and freeze | Reviewed false positives/misses; frozen `HYD` file |
| WP5 | `label_levels` + research integration | Labels equal a reference loop on randomized cases; controls and variants labeled |
| WP6 | Evaluation | Base rates, H1-H3, power check, dev/validation (holdout sealed) |
| WP7 | Shadow rule in the watcher | SHADOW events with full stage timeline; suppressions counted |
| WP8 | Decision | Promote / keep collecting / reject, recorded in the decision log |

WP0-WP4 give you something to inspect on real charts before any profitability question is asked.

## 7. Risks

- **Volume at price is estimated**, not measured; M1 reduces but does not remove this.
- **Discretion lost in translation:** the rule for "sideways" or "bullish reaction" will not match every case you would take; the golden set measures the gap.
- **Many degrees of freedom:** window, bins, tolerance, reaction definition. Defense: preregistration and a small variant set.
- **Low event counts, thin coins** (wide structural stops fail the cost gate), a single market path, regime dependence.
- **1m data** may be shallow or rate-limited; WP0 decides this up front.

## 8. What I need from you

1. 20-30 textbook examples (symbol and timestamp or screenshots) plus ~10 you would reject.
2. Confirmation of the break, reaction-candle and T1/T2 definitions in section 2.
3. The result of the 1m probe from your machine (I can write the probe script).
4. Watch-state display: off (counts only) or approved as a separate stage.
5. Your MEXC fee tier and the full bar archive for all coins.
