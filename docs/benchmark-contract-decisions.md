# Phase 1: Benchmark Contract — Decisions and Baseline

> **Status: awaiting [OWNER] sign-off before proceeding to Phase 2.**
> This document presents D1–D10 with the measured baseline table below.
> **The agent has NOT recorded your answers, written `docs/benchmark-contract.json`,
> stored a contract row, or unblocked the benchmark gate.** All of those happen
> only after you decide.
> The baseline was **re-measured after round 2 of the Phase 1 review fixes**
> (real pre-holdout boundary, per-entry point-in-time costs, 12-cell grid);
> sign-off is still pending.

---

## 1. What Phase 1 delivered

| Artifact | File | Tests |
|---|---|---|
| Frozen BenchmarkContract dataclass | `proto/engine/contract.py` | `tests/test_engine_contract.py` — ALL PASS (70 checks) |
| Read-only baseline CLI | `proto/engine/baseline.py`, `proto/engine/__main__.py` | `tests/test_engine_baseline.py` — ALL PASS (186 checks); `tests/test_engine_cli.py` — ALL PASS (75 checks) |
| Data protection scan | inherited from Phase 0 | `tests/test_engine_data_protection.py` — ALL PASS (8 checks) |

**Integrity verified:** baseline never writes under `data/`, never creates or
writes `data/research/altradar-research.db`, never touches `signals.db` or
`quant.db`. The CLI validates `--out` **before** opening any data file, reusing
`proto.research.store.validate_db_path` (plus `data/research` and
`altradar-research.db` refusals). The contract's `save_once()` uses `O_EXCL` to
refuse overwriting an existing file, and `cost_model` (including nested
`slip_tiers`) is frozen read-only. The baseline mirrors `roe.empirical_rates`
(the existing, hindsight-free base-rate calculator) — identical stride-grid
sampling and labeling, with the cost tiered point-in-time at each sampled
entry and agreement against `empirical_rates` asserted in the suite — and
never touches the quarantined `proto/quant/backtest.py`.

---

## 2. Measured baseline table

The table below was produced by:
```
python3 -m proto.engine baseline --data data/bars --leverage 10,20,25 --horizon-days 1,3,7,14 --window pre-holdout --seed 1 --out /tmp/opencode/alt-radar/baseline-phase1-fixed3.json
```
(3 leverages × 4 horizons = 12 cells; re-measured after round 2 of the Phase 1
review fixes — the real pre-holdout boundary plus per-entry point-in-time
measurement costs. The JSON records `window_bounds`, the exact `coins`
universe, the boundary costs in `cost_frac_per_coin` (selection only), the
point-in-time tier usage per cell in `entry_cost_counts`, and the pooled
cells in `pooled[L][T_days]`.)

**Window (pre-holdout = dev + validation):** anchored at BTC's first bar (repo
convention, `proto/quant/__main__.py`). From the JSON's `window_bounds`:
anchor `t_min` = 1783910100 (**2026-07-13 02:35 UTC**), `t_lo` = 1784428500
(**2026-07-19 02:35 UTC**, day 6), `val_end_ts` = 1790562900 (**2026-09-28
02:35 UTC**, day 77), `holdout_start_ts` = 1790735700 (**2026-09-30 02:35 UTC**,
day 79), `n_days` = 103.3125, `boundaries_in_archive` = **true**. These four
dates are the **base window bounds** — the entry window is `[t_lo, val_end)`
and selection reads the boundary at `val_end`. On top of that, each
(leverage, horizon) cell applies a **horizon-specific effective entry
cutoff** `min(val_end, holdout_start − T·BAR_S)` (BAR_S = 300 s), because no
label window (bars i+1 … i+T) may open at or after the holdout:

| Horizon | Effective entry cutoff (exclusive, UTC) |
|---|---|
| 1 day | **2026-09-28 02:35** (base `val_end` binds; the guard alone would allow 09-29) |
| 3 days | **2026-09-27 02:35** |
| 7 days | **2026-09-23 02:35** |
| 14 days | **2026-09-16 02:35** |

This is why n shrinks as the horizon grows (284,438 → 226,696 labeled
outcomes): the longer the label window, the earlier the last admissible entry.
**Archive-span disclosure:** the bar files are *not* time-ordered on disk (the
collector appends backfill pages after forward rows); sorted by timestamp the
archive spans ~103.3 days, so the split boundaries lie INSIDE the archive and
the pre-holdout window is a genuine ~71-day dev + validation window. (The
collector tail drifted since the previous run — `n_days` moved 102.91 →
103.3125 — which cannot affect any number in this table because labeling is
window-bounded.) Two limitations: (a) the boundary machinery is proven on
synthetic 100-day archives (tests assert holdout outcomes are excluded and
pre-holdout outcomes are kept, including the 14-day-horizon guard); (b) the
live collector kept appending during measurement, which cannot affect labels
because labeling is bounded by the window — appended holdout-region bars are
read but never labeled.

**Coin universe:** all **117** archive coins passing the gates at the
pre-holdout window boundary — trailing-24h quote volume ≥ 1,000,000 USDT (the
cost-model floor) AND a contiguous trailing-24h window (a window spanning a
data gap does not describe the coin's liquidity). No cap (`--max-coins`
unset). Floor and tradeable-cost gate now evaluate the **same sorted boundary
window**, so selection and cost provably share one boundary evaluation — in
this run the cost gate dropped 0 coins (every floor-passing coin is
tradeable). Because the boundary sits inside the archive, selection is
genuinely **point-in-time** (trailing-24h QV ending at the day-77 boundary,
`val_end_ts`), NOT end-of-archive liquidity. The exact universe (`coins` in
the JSON): 1000BONK, AAVE, ADA, AERO, AKE, ALGO, APE, APT, AR, ARB, ASTER,
ATOM, AVAX, AXS, BATON, BCH, BEAT, BLESS, BNB, BR, BSV, BTC, BTW, CAKE, CFX,
COTI, CRO, CRV, DOGE, DOT, EIGEN, ENA, ETC, ETH, ETHFI, FARTCOIN, FET, FLOKI,
FLOW, GALA, GPS, GRAM, GRASS, GRT, HBAR, HNT, HYPE, ICP, IMX, INJ, JASMY,
JTO, JUP, KAS, KITE, LDO, LINK, LIT, LSK, LTC, LUNC, MARSCOIN, NEAR, NEO,
NIL, ONDO, ONE, OP, ORDI, PENDLE, PENGU, PEPE, PHA, PI, POL, PONS, PYTH, QNT,
RARE, RAY, RENDER, RIVER, RUNE, SAGA, SAND, SEI, SHIB, SKY, SKYAI, SOL, STRK,
STX, SUI, SYN, TAO, TIA, TRB, TRX, UAI, UNI, US, USELESS, VET, VIRTUAL, VVV,
W, WIF, WLD, WLFI, XAI, XLM, XMR, XPL, XRP, ZAMA, ZEN, ZRO.

> **⚠ Universe shortfall vs the plan's cohort design — open owner decision
> (feeds D6 and D7), NOT decided here.** The plan (§2.5) requires
> development + 20 + 50 + 100+ ≥ **170** eligible coins so the three
> validation cohorts can be disjoint. The measured candidate universe is
> **117** coins — **below the minimum**. Per the plan, the owner must choose:
>
> - **(a) lower the liquidity floor** (D7, currently trailing-24h QV ≥ 1M
>   USDT) to grow the universe, or
> - **(b) let the stage cohorts nest** (S20 ⊂ S50 ⊂ S100) and report the
>   new-coins-only subset separately.
>
> Until this choice is made, D6's "disjoint cohorts" default cannot stand as
> written; both options are recorded here for the owner's decision.

**Cost model (one tier ladder, two distinct roles):** round-trip =
2 × (fee 0.0005 + tiered per-side slippage) via `DEFAULT.cost.round_trip(qv24)`
(`proto/quant/config.py`).

- **Universe selection — boundary cost.** One cost per coin from the
  trailing-24h QV at the day-77 boundary (`cost_frac_per_coin` in the JSON),
  used **only** as the selection/tradeable-cost gate. Tier distribution
  across the 117 coins: **0.0013 × 19** coins (QV ≥ 30M), **0.0016 × 16**
  (QV ≥ 10M), **0.0022 × 48** (QV ≥ 3M), **0.0034 × 34** (QV 1M–3M).
- **Measurement — point-in-time cost (plan §5.1: "tiered by trailing 24h
  quote volume at the decision bar").** Every sampled entry is cost-tiered
  at the trailing-24h QV of its own entry bar, so a coin's tier moves with
  its liquidity across the window (JSON `cost_application` =
  "point-in-time at each sampled entry (trailing-24h QV at the entry bar)").
  Tier usage summed over all 12 measured cells (`entry_cost_counts`;
  3,147,624 labeled outcomes in total): **0.0013 × 622,800**,
  **0.0016 × 557,964**, **0.0022 × 997,344**, **0.0034 × 969,516**. These
  usage shares differ from the boundary tiers above precisely because a
  coin's liquidity tier varies across the window. The plan-conformant
  per-entry result **is** the table below — no boundary-cost approximation
  is presented as the plan's result. (A fixed `--cost-frac` override remains
  available for sensitivity runs and is labeled `"fixed (cost_frac
  override)"` in the result metadata, never as the point-in-time result.)

**Sampling:** deterministic stride-grid entries — every `--stride` (12) bars
on a global phase grid, both sides labeled per entry. Entries are **NOT
random**: entry times and sides are fixed by the grid, not by outcomes, which
makes this a valid no-look-ahead baseline. **This is exactly the plan's
Phase 1 mechanism, not an alternative to it:** the plan's §2.2 "random
entries" table and its Phase 1 command both route through
`proto.quant.roe.empirical_rates`, which is this same stride-grid,
both-sides measurement ("random entry" in the plan means outcome-independent
no-skill entry, not hash-based selection) — no estimator choice exists at
Phase 1. The plan's hash-based `random_baseline` (plan §4 D4:
`sha256(seed|coin)` for decision time, hash parity for side) is the matched
baseline of the plan's §8 evidence rule and arrives with Phase 6 — it is not
this table. The binomial pass-count
simulations in §2.2 are seeded random draws (`--seed 1`, 10,000 draws) for
mean/95% band; `p_reach_threshold` is the exact binomial tail.

### 2.1 Pooled baseline pass rates (TP-first / total outcomes)

| Leverage | Horizon | Pass (TP) | Stop-first | Time-out | n (samples) | Mean ROE/entry |
|---|---|---|---|---|---|---|
| 10× | 1 day | 9.9% | 1.6% | 88.4% | 284,438 | −2.4% |
| 10× | 3 days | 22.3% | 5.1% | 72.5% | 279,144 | −4.2% |
| 10× | 7 days | 36.4% | 11.3% | 52.4% | 258,930 | −8.1% |
| 10× | 14 days | 50.1% | 17.2% | 32.7% | 226,696 | −4.6% |
| 20× | 1 day | 25.5% | 5.7% | 68.9% | 284,438 | −6.1% |
| 20× | 3 days | 43.9% | 13.4% | 42.6% | 279,144 | −7.0% |
| 20× | 7 days | 57.2% | 21.1% | 21.7% | 258,930 | −7.2% |
| 20× | 14 days | 65.7% | 25.0% | 9.3% | 226,696 | −1.9% |
| 25× | 1 day | 31.8% | 8.1% | 60.0% | 284,438 | −7.7% |
| 25× | 3 days | 50.5% | 17.2% | 32.3% | 279,144 | −7.9% |
| 25× | 7 days | 61.7% | 24.3% | 14.0% | 258,930 | −7.2% |
| 25× | 14 days | 67.5% | 27.1% | 5.5% | 226,696 | −3.4% |

> **Note:** These numbers differ from the plan's §2.2 table (8.3% to 56.6%)
> because (a) the universe is all 117 coins passing the point-in-time liquidity
> + tradeable-cost gates at the day-77 pre-holdout boundary (no cap; trailing
> 24h QV at the boundary, not end-of-archive liquidity), (b) measurement costs
> are per-coin tiered point-in-time at each sampled entry (trailing-24h QV at
> the entry bar) rather than a fixed 0.0013, and (c) the pre-holdout window is
> dev + validation (day 6 → day 77) with per-cell effective entry cutoffs
> (see the window table above) excluding any entry whose label window would
> reach the holdout (day 79). Corrected defect, disclosed plainly: an earlier
> run of this table reported 73 coins because the liquidity floor was
> evaluated on unsorted file rows; the review caught it and the gate now
> evaluates the sorted boundary window. The archive genuinely spans ~103.3
> days (the bar files are not time-ordered on disk — sorted by timestamp, the
> split boundaries lie inside the archive, `boundaries_in_archive: true`).
> The pattern is the same as the plan's: longer horizons and higher leverage
> raise the baseline pass rate (9.9% → 67.5% across the grid). The 75%
> threshold sits 7.5–65.1 points above the baseline depending on cell — the
> gap is widest at 10×/1d and narrowest at 25×/14d.

### 2.2 Binomial pass-count distributions (chance of reaching 75% by random draw)

Using the measured pooled pass rate as the true probability, P(reach ≥
`ceil(0.75×n)` by chance) from exact binomial, for cohort sizes n = 20/50/100
(thresholds 15/38/75):

| (L, T) | Pass rate | 15/20 | 38/50 | 75/100 |
|---|---|---|---|---|
| 10×/1d | 9.9% | < 0.0001% | < 0.0001% | < 0.0001% |
| 10×/3d | 22.3% | < 0.0001% | < 0.0001% | < 0.0001% |
| 10×/7d | 36.4% | 0.05002% | < 0.0001% | < 0.0001% |
| 10×/14d | 50.1% | 2.127% | 0.01639% | < 0.0001% |
| 20×/1d | 25.5% | 0.0004877% | < 0.0001% | < 0.0001% |
| 20×/3d | 43.9% | 0.4893% | 0.00041% | < 0.0001% |
| 20×/7d | 57.2% | 8.076% | 0.4497% | 0.01627% |
| 20×/14d | 65.7% | 26.65% | 8.025% | 2.959% |
| 25×/1d | 31.8% | 0.00933% | < 0.0001% | < 0.0001% |
| 25×/3d | 50.5% | 2.28% | 0.01949% | < 0.0001% |
| 25×/7d | 61.7% | 15.94% | 2.362% | 0.3393% |
| 25×/14d | 67.5% | 32.4% | 12.64% | 6.425% |

> **Key takeaway:** At the most favorable cell (25×/14d = 67.5% baseline),
> there's a 32.4% chance that a no-skill engine reaches 15/20 by luck (12.6%
> for 38/50, 6.4% for 75/100) — the pass count alone is weak there. At the
> recommended D2 cell (20×/3d = 43.9%), it's 0.5% for 15/20 and < 0.0001% for
> 75/100. The threshold is a genuine barrier at short horizons; at long
> horizons the evidence rule leans on D9's confidence-interval requirement
> against the matched baseline.

### 2.3 ROE-space geometry (plan §5.1, frozen by the contract)

For target +100% ROE and stop −250% ROE at cost c and leverage L (geometry per coin; c varies by trailing-24h QV tier; table at c = 0.0013, the lowest tier). The driftless null is `p_null = b/(a+b)` with `a = target_roe/L + c` (gross target move) and `b = stop_roe/L − c` (gross stop move) (`proto/quant/roe.py` `target_plan`):

| L | Target move (c=0.0013) | Stop move (c=0.0013) | p_null (driftless) | Cross-margin bound |
|---|---|---|---|---|
| 10× | 10.13% | 24.87% | 71.1% | margin/E < 39.2% |
| 20× | 5.13% | 12.37% | 70.7% | margin/E < 38.5% |
| 25× | 4.13% | 9.87% | 70.5% | margin/E < 38.1% |

(Stop is −250% of margin = 2.5/L of price. p_null at c = 0.0013: 10× →
0.2487/0.35 = 71.1%; 20× → 0.1237/0.175 = 70.7%; 25× → 0.0987/0.14 = 70.5%.
Cross-margin survivability at mm = MAINT_MARGIN = 0.005 = **0.5%**
(`proto/quant/risk.py:14`):
`margin/E < 1/(2.5 + 0.005·L)`, computed as:
- L=10: 1/(2.5+0.05) = 1/2.55 = 39.2%
- L=20: 1/(2.5+0.10) = 1/2.60 = 38.5%
- L=25: 1/(2.5+0.125) = 1/2.625 = 38.1%

(The implementation plan's own "(L = 20: < 36%)" is a typo in that plan —
1/2.6 = 38.5%; the values above agree with the stated formula.))

---

## 3. Decisions D1–D10

> Each decision is marked **[OWNER]** if it requires your explicit choice.
> The recommended default is the agent's suggestion based on the baseline
> table and the author's priors.

| # | Decision | Recommended default | Why |
|---|---|---|---|
| D1 | Leverage L for the benchmark | **20×** | Target 5.13% price move, stop 12.37% — both within normal altcoin daily range. Baseline pass rate at 20× is well-measured across 4 horizons. |
| D2 | Outcome horizon T (bars) | **864 bars (3 days × 288 five-minute bars)** | Baseline = 43.9%, so 75% is ~31 points clear — a meaningful skill gap. 1-day horizon (25.5%) is too easy to fail; 7-day (57.2%) is close enough that time-outs dominate and it's mostly patience. Cells above ~60% (25×/7d 61.7%, both 14d cells) are avoided: a no-skill engine reaches 15/20 by luck up to 32% there. |
| D3 | Time-out handling | **failure** (its own category) | Conservative: a time-out means "neither stop nor target was reached in the horizon." It never counts as a pass. |
| D4 | Signal selection per coin | **first-trigger on a frozen hourly grid** | Scan a fixed hourly decision grid in the cycle window; first grid time where `score ≥ τ` is the signal. No trigger = `NO_SIGNAL` = fail. Outcome-independent by construction. |
| D5 | Fee tier and slippage tiers | **Use `proto/quant/config.py` DEFAULT** (fee 0.05% per side, tiered slippage) | Fee and slippage costs are an assumption until your MEXC tier is confirmed. The cost model is frozen in the contract's hash. (Only fee and slippage live here — the maintenance margin is a separate owner item under D10.) |
| D6 | Cohort design | **Disjoint cohorts V20, V50, V100+**, stratified by liquidity + volatility | Fresh coins at each stage test generalization. Each cohort drawn once via `sha256-rank-v1`; never redrawn after seeing results. **Open — blocks this default:** the measured universe (117) is short of the plan's ≥170 for disjoint cohorts; see the shortfall block in §2. The owner must pick (a) lower liquidity floor (D7) or (b) nested cohorts S20 ⊂ S50 ⊂ S100 before this default can stand. |
| D7 | Eligibility rules | **liquidity floor: trailing-24h QV ≥ 1M USDT; min history: 3× horizon (9 days at the 3-day horizon); data coverage via `proto/quant/coverage.py`** | Must be fixed before sampling so the coin set cannot be selected on outcomes. |
| D8 | Chronology | **Development dates < validation dates, with embargo ≥ horizon after dev end** | No label leakage across the split. Embargo ≥ horizon_bars is enforced by the contract. |
| D9 | Evidence rule | **Owner pass (count ≥ ceil(0.75·n)) AND lower CI of TP rate above the matched baseline** | A count alone is weak: even no-skill reaches 67.5% at 25×/14d (32% chance of 15/20). The evidence check requires the lower confidence bound to exceed the matched random baseline. |
| D10 | Margin policy for −250% stop | **Cross margin. Survivability bound: max margin per signal ≤ equity / (2.5 + mm·L) — shown for geometry only. TWO items remain [OWNER]-pending under this decision, listed separately: (1) the safety-factor value the plan (§2.5 D10) requires on this bound; (2) verification of the maintenance-margin assumption (mm = 0.5% from `proto/quant/risk.py:14`, assumed until your venue/margin tier is confirmed). No value is added or frozen here.** | A −250% stop is not survivable under isolated margin. The contract records the cross-margin equity requirement plus the owner-chosen safety factor and verified mm at freeze (contract fields for both added with the frozen contract). |

---

## 4. Recommended contract defaults (pending owner sign-off)

These are the values the agent would freeze in
`docs/benchmark-contract.json` once you approve D1–D10:

```jsonc
{
  "version": "engine-v1",
  "leverage": 20.0,            // D1
  "target_roe": 1.0,           // +100% on margin
  "stop_roe": 2.5,             // -250% on margin
  "horizon_bars": 864,          // D2: 3 days × 288 bars/day = 864 bars
  "entry": "next_open",        // fixed
  "timeout": "fail",           // D3
  "gap_policy": "DATA_GAP_is_fail",
  "no_signal": "fail",         // D4
  "pass_threshold": 0.75,
  "min_history_days": 9,       // D7: 3× horizon (3 × 3 days)
  "liquidity_floor_qv24": 1000000,  // D7 (1M USDT)
  "embargo_bars": 864,         // D8: >= horizon_bars (3 days)
  "margin_policy": "cross",    // D10 — safety factor + mm assumption owner-pending; field added at freeze
  "funding": "excluded",       // bars-only boundary
  "decision_grid_bars": 12,    // D4: hourly grid at 5-min bars (288/24)
  "stage_sizes": [20, 50, 100], // D6: disjoint validation cohort sizes
  "cost_model": {              // D5: from proto/quant/config.py DEFAULT
    "fee_per_side": 0.0005,
    "slip_tiers": [[30000000, 0.00015], [10000000, 0.0003],
                   [3000000, 0.0006], [1000000, 0.0012]],
    "min_qv24": 1000000
  },
  "trigger_threshold_tau": 0.5  // D4: frozen with the version
}
```

> **Note:** `trigger_threshold_tau = 0.5` is a placeholder. D4 says the signal
> is the first grid time where `score ≥ τ`; the value of τ is decided with the
> first engine version (Phase 5). The contract records τ but the owner sets it.
> For Phase 1 the baseline is computed without any signal — deterministic
> stride-grid entries (every 12 bars on a global phase grid, both sides
> labeled; see §2).

---

## 5. STOPPING HERE — awaiting [OWNER] sign-off

**You have five explicit choices in D1, D2, D5, D7, and D10 — D10
including its safety factor and the mm = 0.5% maintenance-margin
assumption (both owner-pending, no value proposed) — plus
confirmation on D3, D4, D6, D8, D9 — and one round-2 open decision: the
117-coin universe vs the plan's ≥170 for disjoint cohorts (shortfall block
in §2; feeds D6/D7).**

Until you sign off:
- `docs/benchmark-contract.json` is **NOT** written.
- No contract row is stored in `data/research/altradar-research.db`.
- The benchmark gate (`universe.SAMPLE_DESIGN_BLOCKED`) remains **closed**.
- No Phase 2+ code is written (universe census, labeler, decision grid,
  engine, cycle runner, scheduler).

**To proceed, please confirm or adjust: D1, D2, D5, D7, D10.**

---

## 6. Test results

Focused suites, run for this reconciliation (2026-10-23):

```
tests/test_engine_contract.py         — ALL PASS  (70 checks: hash stability, validation, tamper detection, save_once, deep-frozen cost_model incl. nested slip_tiers, _sha256 requirement, decision_grid/stage_sizes hash coverage, strictly-positive slip-tier slip values and trigger_threshold_tau)
tests/test_engine_baseline.py         — ALL PASS  (186 checks: window bounds on synthetic 100-day archives, sorted-boundary universe gate, per-cell holdout guard + no over-exclusion, horizon arithmetic 864 = 3 × 288, per-coin boundary cost, point-in-time per-entry cost tiering incl. cost_application labels/entry_cost_counts and the "fixed (cost_frac override)" sensitivity label, stride-grid agreement with roe.empirical_rates, no data mutation)
tests/test_engine_cli.py              — ALL PASS  (75 checks: --out refusal before any data open (data/bars, data/prices, data/funding_2026, data/research, any path under a data/ root at any depth incl. resolved symlinks, altradar-research.db, signals.db/quant.db + sidecars), safe path, window metadata, no-cap default)
tests/test_engine_data_protection.py  — ALL PASS  (8 checks: source scan + runtime boundary)
```

Pre-existing (unmodified, not this work):
```
tests/test_quant_backtest.py        — FAIL  (3 stopped-agent assertion failures — documented, unmodified, not this work)
```

`python3 tests/run_all.py` — unusable in this environment (`sys.executable`
pollution breaks its subprocess spawning). The controller verified the offline
suites separately: **37 offline suites, 36 pass, 1 pre-existing failure**
(`tests/test_quant_backtest.py`, stopped-agent).
