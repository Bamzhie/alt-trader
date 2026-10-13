# Alt Radar Performance Analysis

**Date:** 2026-10-12  
**Data source:** `data/signals.db` (SQLite) + `data/bars/` (574 coins, 80+ days each)  
**Period:** Signals: 2026-10-08 → 2026-10-12 (3.4 days) | Bars: 2026-10-02 → 2026-10-12 (10 days)  
**Signals logged:** 152,266 total | 16,944 flagged (11.1%) | 97,946 with score_v3 shadow (read-only snapshot, 2026-10-12)

**Updated:** 2026-10-12 (data continued growing during analysis)

## Review update — 2026-10-12

The database was re-read in SQLite read-only mode on 2026-10-12. It continues to change while the daemon runs, so these are a snapshot, not immutable totals.

| Current snapshot | Value |
|---|---:|
| Signal rows | 152,266 |
| v2-flagged rows | 16,944 (11.1%) |
| Rows with `score_v3` | 97,946 |
| `plan_outcome` rows | 13,358 |
| Stop hits / TP2 hits | 8,868 / 1,270 |
| Terminal stop-or-TP2 outcomes | 10,138 |
| Episode rows / episode horizons | 0 / 0 |

The v3 score is implemented as a shadow under `score-v3-dis`; it does not control ranking or flags. The current `score_v3 >= 40` plan-outcome slice contains 2,382 resolved plans across 150 coins (998 stop hits and 350 TP2 hits). Those plans were selected and planned by the existing v2 path. The database contains 6,408 `score_v3 >= 40` signal observations overall; 3,884 of those observations were not v2-flagged and therefore have no legacy plan outcomes. These counts are observations, not independent trades.

**Interpretation:** the positive EV claims below do not establish that v3 has positive expected value or passes its promotion gate. The legacy plan outcomes are conditioned on v2 flagging and use frozen v2 plans. They are not the pre-registered episode comparison, do not include the v3-only observations, and have no second-half paired comparison or coin-cluster confidence interval. With episode tables empty, the section 6 test in `docs/grok-review.md` has not been run. Keep v3 shadow-only pending that comparison; do not promote it based on these legacy tables.

This snapshot supersedes earlier totals and rate tables below wherever they conflict. Older calculations are retained as the analysis history and should not be read as current database totals.

---

## Stop Loss Hit Rate

> **Historical snapshot:** The following detailed tables were calculated before the 2026-10-12 review update above. Their counts are retained for provenance; use the review update for current totals. The v3 comparison tables are exploratory v2-plan subsets, not promotion evidence.

| Metric | Value |
|---|---|
| Total plans with outcomes | 12,953 |
| Stop hit | 8,263 (63.8% of resolved) |
| TP1 hit | 2,944 |
| TP2 hit | 1,022 (7.9% of resolved) |
| Terminal resolved (stop OR TP2) | 9,285 |
| Still open / expired (neither) | 3,668 |

**Direction breakdown of terminal outcomes (current DB, v2-flagged cohort):**

| Direction | Terminal | Stop % | TP2 % |
|---|---|---|---|
| LONG | 4,768 | 63.6% | 12.4% |
| SHORT | 4,605 | 69.4% | 6.4% |

LONGs outperform — 63.6% stop vs 69.4% for SHORTs. The earlier 83.9%/94.6% figures were from the partial backfill; deeper SHORT coverage narrowed the gap, but LONGs remain structurally stronger on TP2: 12.4% vs 6.4%.

### Time-to-stop distribution

| Bucket | Stop count | % of all stops |
|---|---|---|
| 0–5 bars (0–25m) | 1,147 | 22.0% |
| 6–12 bars (25–60m) | 914 | 18.0% |
| 13–24 bars (1–2h) | 826 | 16.0% |
| 25–48 bars (2–4h) | 647 | 12.0% |
| 49–96 bars (4–8h) | 701 | 13.0% |
| 97–288 bars (8h–24h) | 668 | 13.0% |
| >288 bars (>24h) | 142 | 2.0% |

**22% of stops hit within the first 25 minutes.** Early stops dominate — the first hour captures 40% of all stop losses.

### Resolution completeness

| Bars examined | Plans | Stop rate | TP2 rate |
|---|---|---|---|
| < 24h (incomplete) | 7,451 | 65.8% | 5.7% |
| ≥ 24h (complete) | 593 | 24.3% | 3.9% |

Plans that survive to 24h resolution have a much lower stop rate (24.3% vs 65.8%), suggesting early exits are the main source of losses. However, the TP2 rate does not improve at 24h — it stays flat at ~4-5%.

---

## TP Hit Rate

### TP1 analysis
- **2,944 TP1 hits** total (22.7% of plans)
- Of those, **1,823 hit TP1 without ever hitting stop** (14.1% of all plans)
- **1,121 hit both TP1 and stop** (TP1 before stop: these count as stop-loss scenarios since stop is terminal)

### TP2 analysis
- **1,022 TP2 hits** total (7.9% of plans)
- TP1 before stop (both hit): 501 plans hit TP1 before stop
- TP2 before stop: 1,022 plans hit TP2 (terminal)

---

## Risk/Reward Profile

From the frozen plan levels (`plan_log` table):

| Metric | LONG | SHORT |
|---|---|---|
| Avg RR (TP1) | 1.76R | 1.77R |
| Avg RR (TP2) | 4.52R | 4.53R |

The plans target a 1:1.77 reward-to-risk for TP1 and 1:4.5 for TP2 — standard futures swing planning.

### Implied expected value

Using terminal outcomes only (stop = -1R, TP2 = +4.27R avg):

| Metric | Value |
|---|---|
| Total terminal trades | 9,285 |
| Stop losses (-1R each) | 8,263 |
| TP2 winners (avg +4.27R) | 1,022 |
| Average R per terminal trade | **-0.39R** |
| Win rate (TP2 vs terminal) | 11.0% |
| Loss rate (stop vs terminal) | 89.0% |
| EV per terminal trade | **-0.39R** |
| Breakeven TP2 R (for 89.0% stop rate) | 8.09R |

**The system is currently net-negative.** With an 89.0% stop rate, the average TP2 reward of +4.27R is insufficient to breakeven. You'd need a +8.09R reward just to break even at current win rates.

---

## Horizon Returns (outcome_log)

Close-to-close returns from signal price to future bar closes (pre-cost, no fill model):

| Horizon | Count | Avg Return | Positive | Negative |
|---|---|---|---|---|
| 1h | 93,216 | -0.03% | 47.2% | 51.0% |
| 4h | 82,974 | -0.12% | 46.6% | 52.4% |
| 24h | 50,312 | -0.68% | 42.6% | 57.2% |

All horizons show slightly negative average returns, consistent with the stop/TP analysis. 7d horizon is not yet available (data is only ~2.5 days old; needs ~7 days to mature).

---

## Key takeaways

1. **Stop rate is very high** — 66.4% of all terminal plans hit their stop loss (all flagged, not just terminal). This is especially acute for SHORTs (69.4%).
2. **22% of stops hit within 25 minutes** — early exits are the dominant loss driver.
3. **Plans that survive 24h** have a lower stop rate (24.3%), but TP2 rate doesn't improve, so longer holds don't meaningfully help the edge.
4. **Legacy v2 plan performance is negative in this historical snapshot** — the EV estimate depends on the terminal-outcome denominator and assumed reward multiple; read the stated assumptions before interpreting it.
- **LONGs significantly outperform SHORTs** — 12.4% TP2 rate vs 6.4%.
- **Risk/reward and win-rate estimates are descriptive** of these hypothetical v2 plans, not execution P&L.
- **Backfill and shadow status** — additional history expanded the legacy v2 outcome cohort. Its negative results are descriptive for v2 plans. The v3≥40 legacy-plan slice has lower stop and higher TP2 rates, but it is selected by v2 and does not estimate v3's full-cohort EV; the registered comparison remains outstanding.

---

## Is this a code problem or a signal edge problem?

**This is not only a signal-edge question.** The audit found data and outcome limitations, and the v2 OI formula does not match the spec's stated treatment of opening shorts versus liquidation. The historical v2 plan results are negative under the assumptions shown below, but they do not validate v3.

Evidence:

- **Score is uncalibrated.** The flag threshold is hardcoded at 24 (a placeholder per the audit docs). Flagged signals span 24.0 to 55.8, with an average of 29.6. The score measures signal *quality* (magnitude of abnormal activity), not *predictive power*. The audit explicitly states: *"score measures magnitude/heuristics, not confidence."*

- **Score does not predict outcomes.** Looking at terminal outcomes by score band (13K plans, post-backfill):
  - Score 24-30: **63.8% stop rate**, 8.6% TP2
  - Score 30-40: **63.6% stop rate**, 6.5% TP2
  - Score 40+: **66.7% stop rate**, 7.9% TP2 (only 342 plans)

  Even the highest v2-score signals still hit stops at ~66%. **Score quality does not discriminate between winning and losing setups** — the v2 score measures magnitude of abnormal activity, not probability of profit.

**Exploratory result — score_v3 bands show a gradient in this legacy-plan subset.** This is not an out-of-sample test. The rows have plans because they passed the v2 flag, so the table excludes v3-high observations that v2 did not flag:

| v3 band | Stop rate | TP2 rate |
|---|---|---|
| 0 / null | 78.1% | 8.3% |
| 0–20 | 67.1% | 8.2% |
| 20–40 | 53.1% | 9.6% |
| 40–60 | 42.7% | 13.2% |
| 60+ | 41.1% | 16.1% |

The stop-rate pattern is encouraging in this selected sample, but it does not establish monotonic predictive quality or separate signal from noise in the full scan population. It needs the paired, episode-level holdout comparison specified in `docs/grok-review.md` §6.

- **Directional asymmetry is baked in.** SHORTs stop out **69.4%** of the time vs **63.6%** for LONGs (terminal outcomes). Post-backfill the gap has narrowed from the earlier 94.6%/83.9% split — the backfill added more SHORT data that resolves more favorably. Still, LONGs outperform on TP2: **12.4% TP2 vs 6.4%**. This is a structural pattern in the signal logic, not a bug — the book/OI components may be biased toward long setups, or altcoin mean-reversion favors the long side.

- **Early stops dominate.** 40% of all stops hit within the first hour. This suggests the signal fires at local extrema (high earlyness = price just broke out) but the breakout frequently fails immediately — consistent with altcoin pump-and-dump patterns that the audit flags as unmitigated.

---

## Reward/risk vs win rate: the real diagnosis

**The plan R-multiples already exceed your targets.** The planner (`planner.py:73-79`) sets:
- TP1 at **2R** = 200% return per 1R risk (your target: 100%)
- TP2 at **5R** = 500% return per 1R risk (your target: 200%)

Verified from the database: average actual R-multiples are **1.82R (TP1)** and **4.63R (TP2)** — already above your 1R/2R targets. The plan code is doing the right thing.

**The problem is win rate.** With the current plan structure:

| Metric | Current | Target |
|---|---|---|
| Stop rate | 63.8% | Lower |
| TP1 hit rate | 22.7% | Higher |
| TP2 hit rate | 7.9% | Higher |
| Terminal win rate | 11.0% | Higher |
| TP2 R-multiple | 4.63R | ≥2R ✓ |

At 11.0% TP2 win rate with 4.63R reward, EV = 0.11 × 4.63 - 0.89 × 1.0 = **-0.39R per trade** (before costs).

**Breakeven analysis:**

| TP2 R-multiple | Required win rate |
|---|---|
| 2.0R (your target) | 33.3% |
| 3.0R | 25.0% |
| 5.0R (current plan) | 16.7% |
| 8.1R | 11.0% (current) |

Even at the current 4.52R reward, breakeven needs a 16.7% win rate — the system is at 11.0%.

**The measurement system is faithfully recording reality.** The outcome resolver correctly walks bars, detects first touches, and logs results. The problem is that the signals don't have enough predictive edge at the current thresholds, and the plan structure (while having good R-multiples) can't overcome the low win rate.

### What would improve performance

1. **Resolve the measurement calibration gate.** A read-only `python3 -m proto.daemon --measure-status` check on 2026-10-12 reported 575 coins, 176,724 attempts, 8,725 failures, and 8,609 degraded observations. Gap p50 was 362s, p95 717s (limit 600s), and p99 28,799s (limit 900s); calibration remains FAIL and the epoch is UNSET, so episode creation is disabled. Diagnose which observations drive the tail before deciding whether to change coverage or tolerances. This blocks the episode comparison, not the legacy outcome pipeline.

2. **Keep score_v3 in shadow pending its registered comparison.** The v3 shadow score (`docs/grok-review.md`, implemented in `proto/scorer.py`) is logged alongside v2. The legacy plan table is an exploratory slice, not validation:

   | Score band | n resolved | Stop rate | TP2 rate | EV @ 4.5R |
   |---|---|---|---|---|
   | v2 ≥ 24 (current threshold) | 13,358 | 66.4% | 9.5% | −0.31R |
   | v2 ≥ 40 | 355 | 65.9% | 9.3% | −0.34R |
   | **v3 ≥ 40** | **2,382** | **41.9%** | **14.7%** | Not established |
   | **v3 ≥ 60** | **1,202** | **41.1%** | **16.1%** | Not established |

   These figures use outcomes only for v2-flagged plans, whose selection, direction, and plan levels came from v2. They are not a paired v2/v3 comparison, do not cover v3-high observations outside the v2 cohort, and have no holdout interval. They do not show that switching the flag would produce the listed EV.

3. **Do not adopt a LONG-only filter from these results.** At v3≥40, the v2-planned subset shows different LONG and SHORT hit rates, but this does not establish a directional edge for the full v3-selected cohort. Evaluate directions separately under the registered comparison.

4. **Do not tune or raise the v3 threshold from this sample.** The v3≥60 rates are from the same v2-selected legacy-plan subset and do not establish positive EV. Threshold selection should wait for the pre-registered holdout comparison; changing the threshold in response to these outcomes would make that comparison exploratory.

5. **Add a regime filter.** The audit notes the system lacks: BTC/ETH correlation filter, funding-extremes veto, liquidity-degradation check, listing-age minimum. The 12% / 35% vertical-move vetoes catch chases but not pump-and-dump failed breakouts.

6. **Address the early stop pattern.** The historical snapshot showed many stops arriving early. That is a diagnostic pattern, not proof that v3 will improve it; assess it with the same episode-level holdout before changing entry or stop rules.

---

## Important caveat on the data

The performance numbers above come from the **legacy outcome system** (`plan_outcome`, `outcome_log` tables), which records v2-selected plans. The **new episode-based system** (`signal_episode`, `episode_outcome`, `episode_horizon` tables) is **completely empty** — zero episodes created. Legacy outcomes cannot stand in for the planned v2/v3 episode comparison.

Calibration status from the 2026-10-12 read-only status command:
- **Gap p50: 362s**
- **Gap p95: 717s** (limit: 600s) — **FAIL**
- **Gap p99: 28,799s** (limit: 900s) — **FAIL**

The very high p99 indicates long observation gaps, but the status summary alone does not identify their causes or whether they reflect scan-cycle duration. Attribute the gaps by coin and timestamp before changing the measurement cohort or tolerances.

The measurement epoch is not set in `meta`, so `enable_epoch()` is refused. The status command reported 176,724 attempts; the episode tables remain empty. The legacy outcome pipeline (`plan_outcome`, `outcome_log`) is independent and continues to resolve v2 plans.

---

## What to do next (priority order)

### 1. Keep score_v3 shadow-only until the registered comparison is available

The v3 shadow score (`docs/grok-review.md`) is implemented and logged. Current legacy plan outcomes show a promising association in the v2-selected subset, but do not establish that v3 beats v2:

| Gate | Stop rate | TP2 rate | EV |
|---|---|---|---|
| v2 ≥ 24 (current) | 66.4% | 9.5% | **−0.31R** |
| **v3 ≥ 40** | **41.9%** | **14.7%** | Not established |
| v3 ≥ 40 LONG-only | 38.4% | 16.3% | Not established |

These rates are measured on 2,382 resolved v2 plans, not on a v3-selected cohort. They are descriptive, not a promotion result. The v3 score changes the definitions of OI flow, initiation, and direction evidence:
- **OI quadrant mismatch**: rising OI + falling price did receive a bearish lean in v2, but only half strength; falling price with falling OI (an unwind) received full bearish lean. This reverses the relative strength described by the spec. v3 uses matched 1h OI+price windows.
- **Earlyness penalizes breakouts**: v2's proximity-to-extreme factor gave fresh breakouts score ≈0.55 (55% penalty), then a floor of 55%. v3 uses breakout age (`exp(-age/18)`), so a 3-hour-old grind scores ~14% of its peak — as it should.
- **Book skew can flip direction**: v3 derives direction from 5m structure only (Donchian + EMA + OBV); book and funding are confirmation bits, never direction-setters.

The promotion gate (`docs/grok-review.md` §6) requires episode-level, second-half comparisons for both directions, paired against v2 and the specified baselines. The episode and horizon tables currently contain no rows, so this gate has not been evaluated. Legacy outcomes cannot substitute: they are v2-selected and v2-planned, and this analysis reports no coin-cluster interval or holdout comparison.

**Coverage caveat:** At this snapshot, 3,884 of the 6,408 `score_v3 >= 40` signal observations lack legacy plan outcomes because they were not v2-flagged. Their future results are unknown; the observed overlap cannot establish how the full v3-selected population will perform.

### 2. Fix the calibration gate (for the episode system)

The current status is p95 717s and p99 28,799s, with a 575-coin measurement cohort. Diagnose the gap distribution and failure/degradation counts before changing the cohort or tolerance; the p99 alone does not establish that the per-cycle scan is taking 8 hours.

### 3. Do not adopt a LONG-only v3 filter from these results

The v2-planned subset shows different LONG and SHORT hit rates at v3≥40, but it does not establish a general directional edge. Do not infer that SHORTs are structurally broken or that a LONG-only filter has positive EV from these selected legacy outcomes. Evaluate directions separately in the registered episode comparison.

### 4. Add regime filters (defensive)

The audit (§10) notes no BTC/ETH regime filter, no funding-extremes veto, no listing-age minimum. These are what let junk coins through the 30-bar gate. v3 helps but doesn't eliminate the problem — 21% of stops still hit within 25 minutes, suggesting some entries are at pump-and-dump failure points.

### 5. Defer ML until a valid holdout exists

The current shadow window is short, episode outcomes are empty, and legacy plans are selected by v2. A model trained now would inherit that selection and outcome-alignment problem. Define and freeze the episode-level target, cohort, and temporal split before fitting any model.
