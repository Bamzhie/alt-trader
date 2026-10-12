# Watcher Setup Design — stable criteria-triggered setups (Stage 1-2 core)

**Date:** 2026-10-12
**Status:** Spec (frozen Sections 1–3, approved in chat 2026-10-12)
**Sources of truth:**
- `~/Downloads/watcher-architecture-proposal-review.md` (primary; "v2 with quant upgrades") + `barrier_math.py` (verified)
- Repo `docs/watcher-architecture-proposal.md` v2 (T1: break age ≤6 + BTC 7d drift >+5%, LONG only; lifecycle states; tiered gates; silence valid)
- Repo `docs/grok-review.md` (score-v3-dis: `L5`, `initiation=exp(-age/18)`, flow_state, book/funding confirmation-only)
- Repo `docs/watcher-architecture-counterproposal.md` (accepted: chronological splits, baselines, invalidation, kill switches, T2/T3 deferrals, never-loosen-to-quota)
- No `AGENTS.md` in repo — README contributing rules apply (read-only, version rules, no invented data, `tests/run_all.py` green).

**Scope:** Stage 1–2 core. Sidecar watcher beside the scanner. Scanner (`scan/scorer/store signal_log`, flags, ranking, plans, episodes) stays untouched as benchmark. No live ranking change. T2/T3 deferred. No profitability claim without out-of-sample evidence.

## 1. Architecture

```
collector 5m closed bars (MEXC perp) → watcher_quant (pure) → watcher state machine → watcher_setup tables → UI/API + watcher_report
                                          ↑ BTC ctx (T1 gate + features)   ↑ checkpoint/watermark
```

- Candidates (broad, event-time) → scorer (≤8 features, calibrated, `p̂_LB`) → selector marks shadow subset. Emission of watcher setups is structural (CUSUM/T1 + vetoes); EV/K/correlation only set `shadow_selected` flag, never suppress setups. Silence valid.
- Closed bars only: `bar.ts + 300 ≤ now` else `unavailable`. Full recompute banned as steady state; required as reconciliation (tolerance 1e-9).
- Cooldowns per (coin, rule): T1 24h, CUSUM 12h — suppress duplicate alerts, not independence claims.

## 2. Quant formulas (alt-perp first, BTC contextual)

Price series: signals/CUSUM/vol/geometry/outcomes on the setup's own MEXC alt-perp 5m closes (`r_t=ln(C_t/C_{t−1})`). Stop/target touches on perp H/L (`price_source=perp_trades`, proxy — no mark feed in repo, documented limitation). BTC is context only.

- **Vol:** `gk_t=0.5·ln(H/L)²−(2ln2−1)·ln(C/O)²` floored 0; TOD-adjusted `gk_adj=gk_t/s_vol[b]`; EWMA span 24 (`λ=2/25`, seed mean first 24 valid); `σ̂_t=sqrt` = per-5m SD. `s_vol[b]`: 48 buckets `(ts//1800)%48`, median prior-14d same-bucket / median prior-14d all, strictly `ts < today 00:00 UTC`, <30 samples → `unavailable`. Warm-up 60 bars. Residual vol (comparison only): EWMA on `e_t²`, same spans/buckets, never mixed.
- **CUSUM (default raw):** `z_t=r_t/σ̂_t`; `S⁺=max(0,S⁺+z−0.5)`, `S⁻=max(0,S⁻−z−0.5)`; long `S⁺>H_cusum`, short `S⁻>H_cusum`; fire-side reset, 6-bar refractory/side. `H_cusum`: training-first-50% grid {3,4,5,6}, smallest with ≤2500/day universe-wide, frozen; report by coin decile/regime on validation.
- **Comparisons (dev, frozen, side-by-side):** (a) raw default, (b) BTC residual `e=r−β̂·r_BTC` / residual vol, (c) broad-market residual vs median top-20 perp 5m return (≥10 constituents else `unavailable`). Beta: OLS no intercept, current bar excluded, winsorize ±5 MAD, zero-BTC-var → `unavailable`; sensitivity {OLS, 0.5-shrunk `0.5·β_OLS+0.5`, 1.0} on dev, freeze after.
- **T1-break (separate source `src=t1_break`):** 48-bar Donchian excl. current bar, `age` from first bar of current uninterrupted exit run; `initiation=exp(−age/18)`; requires `age≤6`, `L5>0.15`, LONG, trailing-7d BTC drift `(last−first)/first>+5%` on BTC perp closes, + vetoes (vol ≥$250k, spread ≤3%, 1h<12%, 24h<35%). Drift is T1 inclusion only. Report with/without drift; split excluded from training unless predeclared.
- **L5:** `clip(0.35·Donchian20+0.35·EMA_gap+0.30·OBV,−1,1)`; `|L5|≤0.15` → no setup. Book/funding/OI never set direction.
- **RVOL:** raw `3-bar mean / 48-bar median` base vol, `/s_rvol[b]` (same 48-bucket prior-only construction).
- **OI flow:** 1h change newest-vs-prior hourly sample, gap 45–90 min else `unavailable`; `opening/unwind/flat/unavailable`, `opening_for/against` vs `sign(L5)`. 24h OI never enters quadrant.
- **Costs (round-trip, return fraction):** `c_total=2·taker+spread/entry+slip+funding_exp`. Defaults `taker 0.0005/side`, `slip 0.0005`, spread contemporaneous top-of-book (snapshot label), all labeled assumptions. Funding: direction-signed sum over contract scheduled settlements in `[entry,entry+N]`, `fund_cost=−sign(dir)·Σ rates`; projected settlements use `fund_rate_now` labeled `assumption`; no historical funding archive → never backfilled (limitation). Stale funding (>8h): setup still emitted (`funding=unavailable`, `cost_floor` geometry), gross available, net EV unavailable, selector silent for it.
- **Geometry:** `N` from gross event study (below); `b_stop=max(k_s·σ̂√N,10·c_total)`, `a_tgt=k_t·σ̂√N`. Grid 24 pre-registered: `k_s∈{1.5,2,3}×k_t∈{1.5,2}×time∈{48,288}×trail∈{none,breakeven}`. Same-bar stop+target → stop.
- **Event study / N:** dev window, cumulative signed `R_h=dir·ln(C_h/C_0)` at 5-min steps to 288; `score(h)=(mean_cand−mean_plac)/SD_cand`; placebo same coin/TOD±30min/regime, random bar ±5d, same scale. `N=argmax` over {12,48,96,288}. Kill: gross placebo-adjusted ≤0 at all {12,48,288} AND net (gross−0.20%−funding-upper) ≤0 at all three → stop, don't build layer 2. Costs as sensitivity bands (0.10/0.20/0.40%), never subtracted to pick N.
- **Scorer:** ≤8 features (initiation age, TOD-RVOL, residual/raw return, breadth=share outside 48-bar channel, BTC vol percentile 7d, spread/depth, OI flow, listing age). Monotone logistic (stdlib), triple-barrier labels, purged walk-forward + 288-bar embargo (`[T−288,T+288]` purged), weights `w=1/(1+overlap)` normalized, calibration judged (Brier, reliability slope). Hierarchical Beta by coin/regime → `p̂_LB` 20th pct of 2000 draws (seed 7), frozen pre-holdout.
- **Selector (shadow only):** `EV_R=[p̂_LB·a−(1−p̂_LB)·b−c_total]/b≥EV_hurdle` (e.g. 0.1R), rank `EV_R`, K=15/day ceiling, ≤2/cluster (`|ρ|>0.7` on trailing-7d 1h residuals; `N_eff=N/(1+(N−1)ρ̄)` diagnostic). Sets `shadow_selected` only.

## 3. Lifecycle, persistence, UI/API, tests

- **Per-bar order:** gap/stale → `unavailable` stop; else barrier resolution (stop → `resolved_stop`, else target → `resolved_target`, both → stop); else close-based invalidation (`L5` flip beyond ±0.15, opposite-`src` same coin, disable/defect). Invalidation never masks a touch.
- **Terminal:** `resolved_{stop,target}` (hit-rate/EV + horizons); `expired_time` (time-bar, return recorded, competing event); `expired_gap` (48 bars `unavailable` → censored); `invalidated` (competing event, return recorded separately, excluded from hit-rate). Censoring only for gap/end-of-sample/unresolved. Cumulative incidence (Aalen–Johansen) with competing {stop,target,time-expiry,invalidated}.
- **Age:** bars since emission. No 4h validity rule. Time barrier from grid. Gap-expiry only 48-bar rule.
- **Identity:** `rule_id:coin:src_bar_ts` (`t1-break-v1`, `cusum-raw-v1`, …). Repeated bars dedupe. Terminal immutable.
- **Persistence:** additive `watcher_setup` + `watcher_eval` tables; watermark per (coin,rule) + hourly atomic checkpoint; restart replays missed closed bars idempotently, no duplicate emits.
- **Missing/stale:** any required input missing → no candidate; open setup → `monitoring=unavailable`; all counted in coverage input×coin×date. Never defaults to 0/confirmation.
- **UI/API:** read-only watcher panel (both GUIs + `watcher_report.py`): id, rule, coin, dir, src bar, last closed bar, entry/stop/tgt, state, monitoring, invalidation price, missing/stale, cost assumptions, EV_R/p̂_LB, correlated opens. Research-labeled; global pause + per-trigger disable. Scanner unchanged.
- **Tests (closed bars):** GK hand-calc, EWMA, TOD prior-only, CUSUM symmetry, initiation age, RVOL, boundaries (H_cusum, age≤6, drift, vetoes), missing/stale→unavailable, dedupe/cooldown, invalidation/expiry/resolved incl. same-bar, restart no-dup, checkpoint replay, recompute tolerance, funding schedule sign, gross/net columns. `tests/run_all.py` green.
- **Evaluation:** period/cohort/sample/baseline/costs reported; chronological dev/val/holdout; clustered uncertainty (coin + day); no look-ahead (prior-only features, embargo); survivorship (delisted retained in coverage). Unit-test success ≠ edge. No profitability claim without holdout after-cost expectancy + shadow agreement.

## Decisions still open
- Mark feed (none — perp proxy), historical funding/spread archive (none — assumptions labeled), fee tiers unverified, `EV_hurdle`/K final values predeclared before holdout.
