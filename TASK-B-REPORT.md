# TASK B report — Workstream B: competing outcomes & inference

**Status: DONE_WITH_CONCERNS** (one documented deviation from the brief, see
§5; the code is complete, all tests pass, and the concern is a semantics
choice the brief left ambiguous, not a missing feature).

Worktree: `/tmp/opencode/wt-b`, branch `feature/ws-b-outcomes` (no push).
Plan reference: `docs/superpowers/specs/2026-10-19-quant-research-and-startup-design.md` §1.3.

---

## 1. Status

| | |
|---|---|
| Status | **DONE_WITH_CONCERNS** |
| Files added | `proto/quant/competing_outcomes.py`, `proto/quant/inference.py`, `proto/quant/family_test.py`, `tests/test_ws_b_outcomes.py`, `TASK-B-REPORT.md` |
| Files modified | **none** (no existing tracked file was touched) |
| Tests | `python3 tests/run_all.py` → `ALL SUITES PASS (82 files)` |
| Constraints honoured | stdlib only; no `data/` writes; no service changes; no holdout spend; no re-run/retune of the 288-configuration evaluation; `stats.py` / `barrier.py` / `cost_attribution.py` reused, never copied or modified |

---

## 2. What changed, file by file

### 2.1 `proto/quant/competing_outcomes.py` (new, 594 lines)

Public surface (exactly the brief's contract, verified by `inspect.signature`):

```python
OUTCOME_VERSION = "outcomes-v1"
CAUSES = ("TP", "SL", "TIME", "INVALIDATED", "CENSORED")

@dataclass(frozen=True)
class Outcome:
    event_id: str
    coin: str
    ts: int
    side: int
    cause: str
    exit_ts: int | None
    gross_log: float | None
    friction: float | None
    funding: float | None
    net_r: float | None
    dd: float | None
    reason: str | None

def resolve_competing(events, rule, geom, window, cfg=DEFAULT, *,
                      funding_dir=None, settlements_dir=None,
                      invalidation=None, sample_end_ts=None) -> dict

def outcome_distribution(outcomes, *, key="net_r") -> dict

def expected_return(outcomes, key="net_r", days=None, *, B=1500, seed=1) -> dict
```

Also public (helpers the other modules and the reviewer may use):
`slot_index(geom, side)`, `label_slot(ev, geom, side) -> (cause, gross, offset)`,
`friction_net_r(outcome)`.

**Documented assumptions**

* **Reuse, not re-implementation.** The barrier label (`gross`, `kind`,
  `exit_offset`) was produced by `proto/quant/barrier.py:label_path` when the
  events were collected; this module only *reads* the per-(geometry, side)
  slots, so the conservative ordering survives untouched: a bar touching both
  barriers resolves **SL**, and the fill bar's stop is terminal. The slot
  index is derived from `config.grid_geometries()`, so a geometry outside the
  pre-declared grid raises `ValueError` instead of silently mislabelling.
* **Selection** reuses `research._select` (identical to `research.eval_config`),
  and the **trade side** reuses `eval_harness.trade_side` (`fade` inverts the
  break direction), so the funding sign, the exit-offset slot and the label
  slot can never disagree.
* **Denominator.** `n_selected` = rule-selected **and tradeable** events
  (a `None` cost model means the event is not a trade). `n_rule_selected`
  and `n_untradeable` are reported next to it, so the filter is visible.
* **Cause precedence (fixed, documented):** (1) end of sample →
  `CENSORED`; (2) data loss → `CENSORED`; (3) invalidation → `INVALIDATED`;
  (4) otherwise the barrier label `TP`/`SL`/`TIME`.
* **Censoring by sample end is conservative and label-free:** an event is
  censored when `ts + (horizon_bars + 1) * BAR_S > sample_end_ts`, i.e. when
  its *full* observation window is not inside the declared sample. It does
  not consult the label, so a barrier hit inside the sample is still
  censored — trusting a label that may have been computed on bars outside the
  declared sample would leak data past the sample boundary. This is the
  brief's literal definition ("the sample ended … and the horizon extends
  past it").
* **Data loss** is detected through, in priority order: `ev.censored`
  (with optional `ev.censored_reason`), `ev.data_loss` (a string is used as
  the reason), `ev.bars_available` shorter than the horizon, and an
  unresolvable label slot (kind/gross missing). Reason strings:
  `censored:data-loss:<cause>`.
* **Invalidation** never fabricates a return. The caller supplies
  `ev.invalid_exit_offset[k]` (exit bar) and either `ev.invalid_gross[k]` or
  `ev.invalid_exit_price[k]` + `ev.entry_price` (the return). Without an
  offset the outcome is `INVALIDATED` with `gross_log=None` and
  `reason="invalidation:no-exit"`; with an offset but no return source,
  `reason="invalidation:no-gross"`.
* **Cost separation.** `net_r = (gross_log - friction - funding) / dd` and
  it is `None` whenever any input is `None`. Funding is attributed through
  `cost_attribution.funding_cost` (never re-implemented) over the actual
  holding window `[ts + BAR_S, exit_ts)`, where `exit_ts` follows the
  house convention: TP/SL fill at the open of the exit bar, a TIME exit is
  the close of the last bar, an event with no stored exit offset falls back
  to the full-horizon close (the same fallback `eval_harness` uses, and it
  is the longest possible funding window, i.e. conservative). When
  `funding_cost` reports `missing`/`incomplete`, `funding=None`,
  `net_r=None` and the reason names the cause
  (`funding:missing`, `funding:not-configured`,
  `funding:incomplete:<cost_attribution reason>`). **A friction-only number
  is never presented as net**: the friction-only view is a separate,
  explicitly named derived key `"friction_net_r"` (and the helper
  `friction_net_r(outcome)`).
* **Counters returned:** `n_selected`, `n_rule_selected`, `n_untradeable`,
  `n_resolved` (non-censored), `n_measured` (with a gross),
  `n_censored`, `n_invalidated`, `n_trade_resolved` (TP+SL+TIME),
  `n_net_evaluable` (the denominator of every net number), `causes`
  (summing **exactly** to `n_selected`), `outcomes`, `coverage`
  (= `n_resolved / n_selected`), `window`, `geometry`, `rule`,
  `selected_events`.
* **Distribution.** `outcome_distribution` reports `n`, `n_missing` (the
  excluded count), `mean`, `sd` (sample, n−1), `median`, `p05`, `p25`,
  `p75`, `p95`, `min`, `max`, `hit_rate` (TP among **terminal** outcomes
  TP+SL, with `n_terminal` as its denominator), `mean_gross`,
  `mean_friction`, `mean_funding` (each with its own `n_gross` /
  `n_friction` / `n_funding`). **Percentile convention: linear
  interpolation between order statistics** — `h = (n−1)·q`,
  `x[lo] + (h−lo)·(x[hi]−x[lo])` with `lo = floor(h)`, `hi = ceil(h)`.
* **Expected return.** With `days` the interval is
  `stats.day_cluster_mean` (day-block bootstrap) and the statistic is
  `stats.day_cluster_t`; the reported `se` is the matching analytic cluster
  standard error (`mean / t`), falling back to the row-level SE when the
  cluster t is undefined. Without days the plain mean with the row-level SE
  and a normal-approximation interval is used. `cluster` says which
  (`"day"` / `"row"` / `"none"`).

### 2.2 `proto/quant/inference.py` (new, 496 lines)

```python
INFERENCE_VERSION = "inference-v1"

def chronological_probability(events, rule, geom, window, *, min_train=30,
                              step=1, cfg=DEFAULT) -> dict

def uncertainty(values, days=None, coins=None, *, B=2000, seed=1) -> dict

def baselines(events, rule, geom, window, *, cfg=DEFAULT, placebos=200,
              seed=7) -> dict
```

**Documented assumptions**

* **Chronological estimator.** The rule's events are sorted by
  `(ts, coin)`; the first `min_train` form the training prefix, then the
  model is refitted and the next `step` events are predicted, and those
  events join the prefix (expanding window). The fitted model is the
  **label rate** (probability of a target hit):
  `p_global = (hits + 1) / (n + 2)` (Laplace) and
  `p_bucket = (hits_b + 5·p_global) / (n_b + 5)` (shrinkage constant 5,
  documented). Buckets are **time-of-day (6 h UTC) × liquidity tier** (the
  cost model's own slippage tiers) — the same two marginals the placebo is
  matched on. Events whose label cannot be resolved are predicted but not
  scored (`n_predictions` vs `n_outcomes`).
* **Calibration table.** `bins` = 5 equal-count buckets of the scored
  predictions, each `{"lo", "hi", "n_pred", "n_hit", "observed"}` with
  `lo`/`hi` = the predicted-probability range inside the bucket and
  `observed = n_hit / n_pred`. The `n_pred` of the bins sums to
  `n_outcomes`. `brier` = mean squared error against the realised label
  (`None` when nothing could be scored, with a `reason`).
  `slope` = `stats.calibration_slope` (`None` when it is undefined, with a
  `reason`). `records` carries the individual predictions for auditing.
* **Uncertainty.** The **day** interval is `stats.day_cluster_mean`
  (reused). The **coin** interval is a coin-block bootstrap written here:
  resample whole coins with replacement, keep **all** of a resampled
  coin's rows, take the pooled ratio mean, and read the 2.5 %/97.5 %
  percentiles with `stats.day_cluster_mean`'s own index convention
  (`ms[int(0.025·(B−1))]`, `ms[int(0.975·(B−1))]`). `t_day` is
  `stats.day_cluster_t`; `t_coin` is the **coin-cluster analogue** of it
  (the same linearized ratio estimator applied to coin sums and counts).
  **An interval is `None` with a reason when its cluster count is below 3**
  (`day-clusters<3` / `coin-clusters<3`) **and also when the bootstrap
  produced a degenerate zero-width interval** (`day-interval-degenerate` /
  `coin-interval-degenerate`) — a zero-width interval is not evidence of
  certainty. All reasons are listed in `reasons`.
* **Baselines.** Three references, each with its own `n` and a
  `denominator` string:
  1. `unconditional` — the base rate over **all** labelled events in the
     window (no rule, no conditioning);
  2. `rule_without_features` — the base rate over the **rule's own** events
     (the rule's selection is the only conditioning; no extra feature gate);
  3. `unconditional_on_rule_events` — the same population as (2) reported
     as the unconditioned rate, so a reader can see the conditioning effect
     (identical to (2) in this workstream, by construction);
  4. `matched_placebo` — `placebos` synthetic event sets matched on
     (coin, time-of-day bucket, liquidity tier) but taken from a
     **different date**, reported as `mean`, `sd`, `p05`, `p95`, `min`,
     `max`, the `observed` rate and the fraction of placebos at least as
     extreme (`p_value`).
  The base rate is TP over the **resolved** labels (TP+SL+TIME);
  `terminal_rate` (TP/(TP+SL)) is reported next to it.
  **Placebo limitation (documented in the module):** the platform's event
  objects carry pre-computed labels but no bar path, so a synthetic event
  time cannot be re-labelled here. Each placebo event therefore takes the
  label of a **matched donor from another date** (same coin, same
  time-of-day bucket, same liquidity tier). The number of same-day
  fallbacks (`n_same_day_fallback`) and cross-date donors
  (`n_cross_date_donors`) are reported.

### 2.3 `proto/quant/family_test.py` (new, 169 lines)

```python
FAMILY_TEST_VERSION = "family-v1"

def correct(hypotheses, *, method="holm", alpha=0.05, B=1000, seed=7) -> dict
```

* `hypotheses` is a list of `{"id", "p", "n", "metric"}` — a **declared
  family**. The bootstrap method additionally needs `values` and `days` per
  member (the per-event numbers and their day labels).
* `method="holm"` → Holm–Bonferroni step-down:
  `p_adj(i) = max_{j≤i} min(1, (m−j+1)·p(j))` (running maximum ⇒ monotone
  in the raw p and `p_adj ≥ p` for every member), rejection flags, family
  size, `n_rejected`, family-level `family_p`.
* `method="bootstrap"` → a **max-t** family correction: whole days are
  resampled jointly across the family's members and the family's largest
  studentized statistic is compared with its own null distribution. This
  **reuses `stats.spa_max_t`** (the same machinery as the 288-configuration
  grid evaluation) — it is not re-implemented. Produces `family_p`,
  `best_id`, `n_bootstrap`. Each member's `p_adj` is
  `max(own p, family p)`, so `p_adj ≥ p` always holds and no member is
  credited with more evidence than the family correction allows.
* **Refusals (with a reason, never an exception):** empty family
  (`empty-family: …`), an invalid p-value (`invalid-p: …`), an unknown
  method (`unknown-method: …`), a bootstrap family without per-member
  `values`/`days`.
* **Guarantees tested:** `p_adj ≥ p` per member; the adjusted p of a fixed
  member never decreases when the family grows; a family of size 1 returns
  that member's own p-value (both methods); the bootstrap family p does not
  decrease when the family grows by members that do not displace the current
  best; the family size is reported.
* Members are returned **sorted by raw p** so the monotone adjusted p-values
  are visible in the output order.

### 2.4 `tests/test_ws_b_outcomes.py` (new, 530 lines)

Self-checking script in the house style (prints `PASS`/`FAIL`, ends with
`ALL PASS`, `sys.exit(1)` on failure). 11 sections, 79 checks, all
synthetic: hand-built events (`types.SimpleNamespace` with the same
attribute names as `research.Event`) and a hand-built funding fixture in
`tempfile.mkdtemp()` (gzip settlement CSV, epoch-aligned 8 h cycle).
No archive is read, no `data/` path is written.

### 2.5 `TASK-B-REPORT.md` (new) — this file.

---

## 3. Tests (real commands, real output)

### 3.1 Focused — the new suite

```
$ cd /tmp/opencode/wt-b && python3 tests/test_ws_b_outcomes.py
=== 1. cause taxonomy: exhaustive and mutually exclusive ===
  PASS  every selected event produces an outcome
  PASS  cause counts sum exactly to the selected denominator
  PASS  causes are counted in their own buckets
  PASS  coverage = n_resolved / n_selected
  PASS  n_censored is zero here and n_invalidated is zero
  PASS  every cause is one of the declared CAUSES
  PASS  a TP outcome carries its gross and a non-None exit

=== 2. censoring is real: end of sample and data loss ===
  PASS  a horizon past the sample end is CENSORED, not TIME
  PASS  censored events are counted, not dropped
  PASS  censored events carry no gross and no net
  PASS  the censoring reason names the end of sample
  PASS  censored events are excluded from the resolved denominator
  PASS  an unresolvable label is CENSORED with a data-loss reason
  PASS  a marked data loss names its cause
  PASS  a censored event is still counted in n_selected

=== 3. invalidation: precedence and no fabricated return ===
  PASS  invalidation takes precedence over TP/SL/TIME
  PASS  an invalidation without an exit offset has no gross
  PASS  an invalidation is still counted in n_selected
  PASS  an invalidation with an exit offset measures the gross to that point

=== 4. cost separation: friction, funding, net ===
  PASS  the funding cost equals the hand-calculated boundaries
  PASS  the friction is the configured round trip
  PASS  net_r = (gross_log - friction - funding) / dd EXACTLY
  PASS  the funding actually changes the net number
  PASS  n_net_evaluable counts the outcomes with a net number
  PASS  unattributable funding yields funding=None and net_r=None
  PASS  the reason names the funding cause
  PASS  a friction-only number is never presented as net
  PASS  the event still counts in n_selected

=== 5. conservative barrier ordering ===
  PASS  the barrier layer resolves a same-bar tie as SL
  PASS  the outcome layer keeps the conservative SL
  PASS  the fill bar's stop is terminal (a later target is ignored)

=== 6. distributions and expected return ===
  PASS  the distribution names its denominator
  PASS  the mean is over the non-missing values only
  PASS  the hit rate is over TERMINAL outcomes only
  PASS  gross/friction/funding means are reported separately
  PASS  percentiles use linear interpolation between order statistics
  PASS  a friction-only view exists and is never called net
  PASS  expected return without days falls back to the plain mean
  PASS  expected return with days uses the day cluster

=== 7. chronological probability and calibration ===
  PASS  the estimator predicts after the training prefix
  PASS  every prediction that had a label is scored
  PASS  every calibration bin carries its denominators
  PASS  the bins' denominators sum to the scored outcomes
  PASS  the brier score is reported
  PASS  the calibration slope is reported or explained
  PASS  the estimator learned the bucket structure
  PASS  too few training events return empties with a reason

=== 8. uncertainty: day cluster AND coin cluster ===
  PASS  both intervals are present
  PASS  the cluster counts are reported
  PASS  the coin-cluster interval is wider than the day-cluster interval
  PASS  fewer than 3 clusters gives None intervals with a reason
  PASS  with only 3 coins the coin interval is still reported

=== 9. baselines on the same event set ===
  PASS  the placebo distribution is reproducible under a fixed seed
  PASS  the placebo percentiles are ordered
  PASS  the placebo distribution is not degenerate
  PASS  every baseline carries n and a denominator label
  PASS  the unconditional baseline is computed on the same event set
  PASS  the placebo reports the observed value and the extreme fraction

=== 10. family-level multiple testing ===
  PASS  the family size is reported
  PASS  every adjusted p is at least its raw p
  PASS  Holm adjustment is monotone in the raw p
  PASS  Holm rejects the two strongest members at alpha=0.05
  PASS  a family of one returns that member's own p-value
  PASS  an empty family is refused with a reason
  PASS  enlarging the family never lowers an existing member's adjusted p
  PASS  the bootstrap family correction is reproducible
  PASS  the bootstrap reports n_bootstrap and a family p in (0, 1]
  PASS  enlarging the family does not lower the bootstrap family p
  PASS  every adjusted p is at least its raw p in the bootstrap method too
  PASS  a family of one returns that member's own p-value (bootstrap)

=== 11. all five causes at once, and the net denominator ===
  PASS  all five causes are present and exclusive
  PASS  the five causes still sum to the denominator
  PASS  the resolved denominator excludes the censored event
  PASS  the invalidated event is reported separately
  PASS  n_net_evaluable is the net number's own denominator
  PASS  the net distribution names its missing count
  PASS  the friction-only view covers every event that has a gross
  PASS  a minimal event with a scalar label resolves as TP
  PASS  an event without a stored exit offset falls back to the horizon

ALL PASS
```

### 3.2 Focused — the suites that must not regress

```
$ python3 tests/test_quant.py
...
  PASS  registered ids are unique and include controls

ALL PASS

$ python3 tests/test_quant_funding_regressions.py
...
  PASS  a boundary with no snapshot in its OWN regime window -> incomplete

ALL PASS (funding regressions)
```

### 3.3 Full offline suite

```
$ python3 tests/run_all.py
PASS  test_adapter.py  [ALL PASS]
... (82 files)
PASS  test_ws5_eval_harness.py  [ALL PASS]
PASS  test_ws_b_outcomes.py  [ALL PASS]

ALL SUITES PASS (82 files)
```

(`run_all.py` prints one line per file; the tail is reproduced above. Exit
code 0, wall time 5 m 28 s.)

---

## 4. Worked numerics (so a human can check the arithmetic)

All figures below were produced by an ad-hoc demo script (kept outside the
repo, in `/tmp/opencode/ws_b_demo.py`) that imports the committed modules.
Fixture: 5-minute bars (`BAR_S=300`), `T0 = 1_700_000_100`, geometry
`T48_ks1_R1`, rule `breakout_start[rvol>=4]/cont`, `qv24 = 5e6`,
`sigma = 0.002`, and a synthetic settlement archive with an epoch-aligned
8 h cycle at rate `0.0005`.

### 4.1 Cause counts (6 events: TP, SL, TIME, INVALIDATED, 2 × CENSORED)

```
causes          : {'TP': 1, 'SL': 1, 'TIME': 1, 'INVALIDATED': 1, 'CENSORED': 2}
n_selected      : 6  n_rule_selected: 6
n_resolved      : 4  n_censored: 2
n_invalidated   : 1  n_measured: 3
n_net_evaluable : 3  coverage: 0.666667
sum(causes)     : 6
 TP           gross=0.012    friction=0.0022  funding=0.0  net_r= 0.7072540797572916  reason=None
 SL           gross=-0.013   friction=0.0022  funding=0.0  net_r=-1.096965511460289   reason=None
 TIME         gross=0.0021   friction=0.0022  funding=0.0  net_r=-0.0072168783648703105 reason=None
 INVALIDATED  gross=None     friction=0.0022  funding=None net_r=None  reason=invalidation:no-exit
 CENSORED     gross=None     friction=0.0022  funding=None net_r=None  reason=censored:end-of-sample
 CENSORED     gross=None     friction=0.0022  funding=None net_r=None  reason=censored:end-of-sample
```

Check: `1+1+1+1+2 = 6 = n_selected`; `n_resolved + n_censored = 4 + 2 = 6`;
`coverage = 4/6 = 0.666667`; `n_measured = 3` (the invalidated event has no
exit, so it carries no gross); `n_net_evaluable = 3` (the three resolved
events have attributed funding).

### 4.2 `net_r` reconciliation (one TIME event)

```
entry open_ts   : 1700000400        exit_ts: 1700014800
funding boundaries inside (open, exit): [1700006400]
hand funding    : 0.0005 * 1 = 0.0005
friction        : 0.0022            (= 2*(fee 0.0005 + slip 0.0006) for qv24 = 5e6)
dd              : 0.013856406460551017   (= 1.0 * 0.002 * sqrt(48))
gross_log       : 0.01
(gross - friction - funding)/dd = 0.5268321206355336
reported net_r  : 0.5268321206355336      EXACT match: True
friction-only   : 0.5629165124598852      (different from net: True)
```

Arithmetic by hand: `0.01 − 0.0022 − 0.0005 = 0.0073`;
`0.0073 / 0.013856406460551017 = 0.5268321206355336` ✓.
The friction-only value is `0.0078/0.013856406460551017 = 0.5629165124598852`
— a different number, i.e. the funding is really in the net figure.

### 4.3 Unattributable funding (same event, coin with no archive rows)

```
funding: None  net_r: None  reason: funding:missing
n_selected: 1  n_net_evaluable: 0
friction-only view of the same outcome: 0.5629165124598852   (never labelled net)
```

The event is still in `n_selected`; the net number is `None` with a reason,
and the friction-only figure is only reachable through the explicitly named
`"friction_net_r"` key.

### 4.4 Calibration bins (160 synthetic events, 80 % hit rate in tod bucket 0, `min_train=30`, `step=1`)

```
n_predictions: 130   n_outcomes: 130
brier: 0.185674      slope: 1.109552
bins:
  lo=0.2121 hi=0.2334 n_pred= 26 n_hit= 11 observed=0.4231
  lo=0.2340 hi=0.2524 n_pred= 26 n_hit=  4 observed=0.1538
  lo=0.2531 hi=0.3030 n_pred= 26 n_hit=  3 observed=0.1154
  lo=0.3034 hi=0.6270 n_pred= 26 n_hit=  9 observed=0.3462
  lo=0.6324 hi=0.7514 n_pred= 26 n_hit= 19 observed=0.7308
sum(n_pred): 130
```

Check: `26·5 = 130 = n_outcomes`; the observed rate rises with the
predicted rate (0.1154 → 0.7308) and the slope is ≈ 1.11 (a well
calibrated predictor would be 1.0).

### 4.5 Placebo distribution (60 events, 2 coins, seed 7, 200 placebos)

```
unconditional                rate=0.566667 n=60 terminal_rate=0.566667
    denominator: all labelled events in the window (no rule, no feature gate)
rule_without_features        rate=0.566667 n=60 terminal_rate=0.566667
    denominator: rule-selected labelled events in the window (no feature gate)
unconditional_on_rule_events rate=0.566667 n=60 terminal_rate=0.566667
matched_placebo mean=0.568583 sd=0.062141 p05=0.466667 p95=0.683333
    observed=0.566667 p_value=0.5300 n_placebos=200
    cross-date donors=12000 same-day fallback=0
```

Check: `p05 (0.466667) ≤ mean (0.568583) ≤ p95 (0.683333)`; the observed
rule rate sits inside the placebo bulk (p ≈ 0.53), i.e. the fixture has no
signal and the placebo says so; every placebo donor came from a different
date (0 same-day fallbacks).

### 4.6 Uncertainty — day cluster vs coin cluster (5 coins × 40 days × 4 rows, σ_coin = 1.06, σ_day = 2.0, σ_noise = 0.5)

```
n: 800   mean: 0.5521
day  CI: -0.1366 .. 1.2021   width 1.3387   t 1.6043
coin CI: -0.2594 .. 1.3460   width 1.6054   t 1.2084
clusters: {'day': 40, 'coin': 5}   reasons: []
coin interval wider than day interval: True
```

Why the coin interval is wider here: the day bootstrap resamples days that
each contain all five coins, so the common daily shock is averaged over five
coins (variance ≈ `σ_day²/5`), while the coin bootstrap resamples five
coins, each of which keeps the full per-coin effect (variance ≈ `σ_coin²`).
With σ_coin² = 1.06 > σ_day²/5 = 0.8 the coin interval is the wider one,
even though the coins are strongly correlated within a day
(corr ≈ `σ_day²/(σ_day²+σ_coin²+σ_noise²)` = 4/5.06 ≈ 0.79). This is the
ordering the acceptance criteria ask to be asserted, and it is a property
of the fixture, not a hard-coded ordering.

### 4.7 Family correction (Holm, family of 3)

```
family_size: 3   n_rejected: 2   family_p: 0.003
  a  p=0.001  p_adj=0.003  rejected=True
  b  p=0.02   p_adj=0.04   rejected=True
  c  p=0.3    p_adj=0.3    rejected=False
empty family: empty-family: a declared family must contain at least one hypothesis
```

Check: `p_adj(a) = 3·0.001 = 0.003`; `p_adj(b) = max(0.003, 2·0.02) = 0.04`;
`p_adj(c) = max(0.04, 1·0.30) = 0.30`; every `p_adj ≥ p`; the adjusted
values are monotone in the raw ones; a family of 1 returns `p` unchanged.

---

## 5. Concerns / deviations

1. **Deviation — the "unconditional" baseline's denominator.** The brief
   describes the unconditional baseline as "the base rate over all selected
   events (no conditioning at all)" and, in the same sentence, "the
   rule_without_features baseline — the same rule evaluated with the new
   feature gate disabled (i.e. the rule's own events, no extra filter)".
   Read literally, both are over the *same* population and would be the
   same number. I implemented the reading that keeps all three informative
   and each with its own denominator: `unconditional` = every labelled
   event in the window (no rule, no conditioning), `rule_without_features`
   = the rule's own events, plus `unconditional_on_rule_events` = the
   unconditioned rate on the rule's population (identical to
   `rule_without_features` in this workstream, because the rule's selection
   is the only conditioning that exists here). All three carry `n` and a
   `denominator` string, and the acceptance check "the unconditional
   baseline is computed on the same events" holds whenever the window
   contains only the rule's events (as in the fixture). If the orchestrator
   wants `unconditional` to mean "the rule's own events, unconditioned",
   that is the `unconditional_on_rule_events` key today.
2. **The placebo is a donor resample, not a re-labelled synthetic event.**
   The event objects carry pre-computed labels but no bar path, so a
   synthetic event *time* cannot be re-labelled inside this layer without
   re-implementing the barrier labelling (forbidden) or reading the archive
   (not allowed in the measurement layer's tests). Each placebo event
   therefore inherits the label of a matched donor from a **different
   date** (same coin / time-of-day bucket / liquidity tier). The marginal
   distribution is preserved and the temporal alignment with the original
   signal is broken, but the placebo's outcomes are real outcomes from
   elsewhere rather than freshly labelled ones. `n_same_day_fallback` and
   `n_cross_date_donors` are reported so the reader can see how much of
   the placebo is cross-date.
3. **`net_r` is `None` whenever funding is not attributable — including
   when no funding archive is configured at all.** This follows the brief
   literally ("when `funding_cost` returns `incomplete=True` or
   `missing=True`, … `net_r` is `None`"), and `funding_cost` reports
   `missing=True` when neither archive has rows for the coin (which is
   also the case when no archive is configured). Consequence: a friction-only
   run has an **empty** `net_r` distribution, and the friction-only figure
   must be read through the explicitly named `"friction_net_r"` key. The
   reason string distinguishes the two cases (`funding:not-configured` vs
   `funding:missing`), and `n_net_evaluable` makes the net denominator
   explicit. The brief's own discipline ("a funding-adjusted figure is
   never labelled friction-only") is the reason I did **not** make
   `net_r` silently fall back to a friction-only number.
4. **Censoring by sample end is conservative and label-blind.** An event
   whose *full* horizon extends past `sample_end_ts` is CENSORED even if
   its barrier was hit inside the sample (see §2.1). This follows the
   brief's definition and avoids trusting a label that may have been
   computed on bars beyond the declared sample. If the orchestrator wants
   the "hit inside the sample is still reported" behaviour, it is a
   two-line change (compare the label's exit ts with `sample_end_ts`
   instead of the horizon end).
5. **Data-loss detection is partly marker-driven.** The event objects
   produced by `research.collect_events` never carry an unlabelled slot
   (a candidate without a full path is dropped before it becomes an
   event), so "a missing bar run" cannot be detected from the object alone.
   The layer therefore detects it through `ev.censored` /
   `ev.censored_reason` / `ev.data_loss` / `ev.bars_available` markers and
   through an unresolvable label slot. A future collector that keeps
   unlabelled candidates only has to set one of those markers.
6. **Performance: funding archives are read per event.**
   `cost_attribution.funding_cost` reads the coin's gzip archives on every
   call and I may not modify it; the harness memoises identical holding
   windows inside one `resolve_competing` call, but a run over tens of
   thousands of events will still re-read the archives many times (the
   same characteristic as the existing `eval_harness.funding_adjusted_config`).
   A batch/vectorised funding API would be a worthwhile follow-up; it is not
   a correctness issue.
7. **`expected_return` and `family_test.correct` have extra keyword-only
   parameters** (`days`, `B`, `seed`) beyond the brief's signature. The
   brief itself requires `days` to be passed as an argument to
   `expected_return`, and the bootstrap method needs a resample count and a
   seed to be reproducible. All are keyword-only with defaults, so the
   brief's calls still work unchanged.
8. **Not done (deliberately, out of scope):** the layer is not wired into
   any CLI, report or service path, it promotes nothing, it does not enable
   ACTIONABLE output, it does not touch the holdout, and it did not re-run
   or retune the 288-configuration evaluation. The 288-config grid and the
   one-shot holdout remain untouched.

---

## 6. Commits

| hash | message |
|---|---|
| `5cc2408` | `feat(quant): WS-B competing outcomes, inference and family-level multiple testing` |

The report itself is committed as the child of `5cc2408` (branch HEAD);
its hash is reported to the orchestrator in the return message (a file
cannot contain its own commit hash).

```
$ git log --oneline feature/ws-b-outcomes --not main
5cc2408 feat(quant): WS-B competing outcomes, inference and family-level multiple testing
<hash>  docs(quant): WS-B task report (competing outcomes & inference)
```

Nothing was pushed; the branch is local to this worktree.
