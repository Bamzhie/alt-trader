# TASK B report — Workstream B: competing outcomes & inference

**Status: DONE_WITH_CONCERNS** (fix round 1 applied — every review finding
is fixed; what remains are the *documented* deviations listed in §5, which
are intentional choices, not defects). The round-0 report is preserved
below and the round-1 changes are appended in the "Fix round 1" section
at the end.

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

def cause_incidence(outcomes, horizon_bars) -> dict
```

`cause_incidence` was added in fix round 1 (the cumulative-incidence view
of the same taxonomy) and is part of the public surface.

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
* **Expected return.** The reported interval follows the **same rule** as
  the cluster intervals in `inference.uncertainty`: an interval whose
  cluster count is below 3 is `None` with a reason — never a thin or
  degenerate interval presented as if it were a cluster interval.
  * `days` supplied, aligned, **≥ 3 distinct days** → the interval is
    `stats.day_cluster_mean` (day-block bootstrap) and the statistic is
    `stats.day_cluster_t`; the reported `se` is the matching analytic
    cluster standard error (`mean / t`). `cluster="day"`. A **degenerate**
    result (the interval collapses, `lo >= hi`) is suppressed like a
    sub-threshold cluster count: `ci_lo`/`ci_hi` are `None` with a reason
    and the statistic is suppressed with it.
  * `days` supplied, aligned, **< 3 distinct days** → **no interval**
    (`ci_lo`/`ci_hi` are `None`) and a reason naming the cluster count;
    `cluster="row"` (the se/t are row-level) and the row-level interval is
    reported separately as `row_ci_lo`/`row_ci_hi`.
  * no day labels, or labels that do not align with the evaluated
    outcomes → the plain mean with the row-level standard error and its
    normal-approximation interval, `cluster="row"`, and a reason that
    distinguishes "no day labels supplied" from "day labels do not
    align".
  `row_ci_lo`/`row_ci_hi` are always present, so the row-level view is
  never lost when the cluster interval is suppressed.

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
  `(ts, coin)`; the first `min_train` form the **training prefix** — they
  are never predicted, but they are what the first prediction is fitted
  on — then the model is refitted and the next `step` events are
  predicted, and those events join the prefix (expanding window). The
  fitted model is the **label rate** (probability of a target hit):
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
  The equal-count split is `model.bucket_table`'s rule, but
  `bucket_table` itself cannot be used as-is: it returns
  `(mean_pred, mean_y, n)` tuples and never exposes the realised labels,
  so it cannot report the per-bucket hit count (`n_hit`) or the
  predicted-probability range (`lo`/`hi`) the calibration table needs.
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
  certainty. **A suppressed interval takes its statistic with it**: the
  matching `t_day`/`t_coin` is `None` as well, so a `t` is never reported
  next to an interval that was not reported. All reasons are listed in
  `reasons`.
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
     `max`, the `observed` rate, the number of placebos at least as
     extreme (`n_extreme`) and the corrected p-value.
  The base rate is TP over the **resolved** labels (TP+SL+TIME);
  `terminal_rate` (TP/(TP+SL)) is reported next to it.
  **Denominator rule (the same as the outcome layer):** the event set is
  the rule-selected, **tradeable** events — an event with no cost model is
  not a trade and is excluded here too, counted in `n_untradeable`. This
  makes `baselines(...)["n_selected"]` literally equal to
  `resolve_competing(...)["n_selected"]` for the same inputs.
  **Placebo mechanism and its limit (documented in the module):** the
  platform's event objects carry pre-computed labels but no bar path, so a
  synthetic event time cannot be re-labelled here. Each placebo event
  therefore takes the label of a **matched donor from another date** (same
  coin, same time-of-day bucket, same liquidity tier). The number of
  same-day fallbacks (`n_same_day_fallback`) and cross-date donors
  (`n_cross_date_donors`) are reported.
  **What the placebo does and does not test:** the donor pool is itself
  made of rule events, so the placebo **retains the rule's average edge**.
  It breaks the *temporal alignment* of the signal and therefore detects
  **date-specific deviations** — it does **not** test the rule's edge from
  scratch, and a rule whose edge is stable across dates is not
  distinguished from the placebo by it. The p-value uses the
  `(cnt + 1) / (B + 1)` correction `stats.spa_max_t` uses, so it is never
  exactly zero, and the reported denominator `n_per_placebo` is the number
  of donors actually used per draw (not the number of selected events).

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
PASS  test_app_watcher.py  [ALL PASS]
PASS  test_backfill.py  [ALL PASS]
... (78 more files)
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
brier: 0.171264      slope: 1.190606
bins:
  lo=0.2035 hi=0.2198 n_pred= 26 n_hit= 14 observed=0.5385
  lo=0.2201 hi=0.2307 n_pred= 26 n_hit=  3 observed=0.1154
  lo=0.2307 hi=0.2503 n_pred= 26 n_hit=  2 observed=0.0769
  lo=0.2511 hi=0.6677 n_pred= 26 n_hit=  7 observed=0.2692
  lo=0.6726 hi=0.7505 n_pred= 26 n_hit= 20 observed=0.7692
sum(n_pred): 130
```

Check: `26·5 = 130 = n_outcomes`; the observed rate rises with the
predicted rate (0.0769 → 0.7692) and the slope is ≈ 1.19 (a well
calibrated predictor would be 1.0). *(These numbers changed in fix round 1:
the training prefix now seeds the estimator, so the first predictions are
fitted on 30 observations instead of none.)*

**The training prefix is real (fix round 1 probe).** 40 events whose first
30 are all hits, `min_train=30`:

```
first prediction p = 0.986979   (an empty training set would give 0.5)
n_train = 40                     (the prefix plus the 10 predicted events)
n_predictions = 10, n_outcomes = 10   (the prefix itself is never predicted)
```

*(Corrected in the final review: the figure quoted here was `0.995455`,
which does not reproduce from the committed fixture.  The value above is
the one the committed fixture actually produces — the same 40-event probe
as `tests/test_ws_b_outcomes.py` — and `n_train` / `n_predictions` /
`n_outcomes` reproduce as quoted.)*

### 4.5 Placebo distribution (60 events, 2 coins, seed 7, 200 placebos)

```
unconditional                rate=0.566667 n=60 terminal_rate=0.566667
    denominator: all labelled, tradeable events in the window (no rule, no feature gate)
rule_without_features        rate=0.566667 n=60 terminal_rate=0.566667
    denominator: rule-selected, tradeable, labelled events in the window (no feature gate)
unconditional_on_rule_events rate=0.566667 n=60 terminal_rate=0.566667
matched_placebo mean=0.568583 sd=0.062141 p05=0.466667 p95=0.683333
    observed=0.566667 p_value=0.5323 n_extreme=106 n_placebos=200 n_per_placebo=60.0
    cross-date donors=12000 same-day fallback=0
```

Check: `p05 (0.466667) ≤ mean (0.568583) ≤ p95 (0.683333)`;
`p_value = (106 + 1) / (200 + 1) = 0.5323` (the `(cnt+1)/(B+1)` correction
`stats.spa_max_t` uses, so the bootstrap p-value is never exactly zero);
`n_per_placebo = 60.0` is the number of donors actually used per draw;
every placebo donor came from a different date (0 same-day fallbacks).

**What this does and does not show.** The placebo's mean (0.5686) sits on
top of the observed rule rate (0.5667) — *by construction*, because the
donor pool is itself made of the rule's events, so the placebo **retains
the rule's average edge**. The placebo breaks the temporal alignment of
the signal, so it detects **date-specific deviations**; it does **not**
test the rule's edge from scratch, and a rule whose edge is stable across
dates is not distinguished from the placebo by it. The p-value of 0.53
therefore says: "the observed rate is not unusual among matched
cross-date draws" — it is **not** a test of the rule's edge against a
signal-free null, and must not be read as one.

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

### 4.8 Expected return — the cluster-count contract (fix round 1)

Six outcomes (TP, SL, TIME, INVALIDATED, 2 × CENSORED); the three with a
gross are the evaluated ones.

```
no days            cluster=row   ci=(-0.013880, 0.014613)  row_ci=(-0.013880, 0.014613)  t=0.05044
    reason: no day labels supplied: row-level interval
2 distinct days    cluster=row   ci=(None, None)            row_ci=(-0.013880, 0.014613)  t=0.05044
    reason: day-cluster interval suppressed: only 2 distinct days (< 3); the se/t are
            row-level and the row-level interval is row_ci_lo..row_ci_hi
3 distinct days    cluster=day   ci=(-0.013, 0.012)         row_ci=(-0.013880, 0.014613)  t=0.05044
    reason: None
```

Check: a two-day fixture no longer produces a *day-cluster* interval
(`cluster` is honest about the fallback, the interval is `None` and the
reason names the cluster count); a three-day fixture does.

### 4.9 Cumulative incidence by cause (fix round 1, `stats.aalen_johansen`)

The same six outcomes, horizon 48 bars:

```
TP           F(48) = 0.166667
SL           F(48) = 0.166667
TIME         F(48) = 0.166667
INVALIDATED  F(48) = 0.000000
```

Check: each of the three resolved causes is 1/6 (one failure per risk set
of 6, no censoring before the horizon); the INVALIDATED outcome had no
exit timestamp, so its event time is unknown and it is **censored** at the
horizon rather than counted as a failure — an unknown time is a
censoring, not a guess. The survival at the horizon is 1 − 0.5 = 0.5 (the
two CENSORED outcomes plus the invalidated one are still at risk).

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
2. **The placebo is a donor resample, not a re-labelled synthetic event —
   and it is NOT a signal-free null.** The event objects carry pre-computed
   labels but no bar path, so a synthetic event *time* cannot be re-labelled
   inside this layer without re-implementing the barrier labelling
   (forbidden) or reading the archive (not allowed in the measurement
   layer's tests). Each placebo event therefore inherits the label of a
   matched donor from a **different date** (same coin / time-of-day bucket
   / liquidity tier); a same-date donor is excluded, so the observed
   event's own label never leaks into its own placebo.
   **Consequence (stated in the module docstring and in §4.5):** the donor
   pool is itself made of rule events, so the placebo **retains the rule's
   average edge**. It breaks the *temporal alignment* of the signal and
   therefore detects **date-specific deviations**; it does **not** test the
   rule's edge from scratch. A rule whose edge is stable across dates is
   not distinguished from the placebo by it, and the placebo p-value must
   not be read as "the rule has no edge". `n_same_day_fallback` and
   `n_cross_date_donors` are reported so the reader can see how much of
   the placebo is cross-date.
3. **`net_r` is `None` whenever funding is not attributable — including
   when no funding archive is configured at all.** This follows the brief
   literally ("when `funding_cost` returns `incomplete=True` or
   `missing=True`, … `net_r` is `None`"), and `funding_cost` reports
   `missing=True` when neither archive has rows for the coin (which is
   also the case when no archive is configured). **Consequence: a
   friction-only run has an EMPTY `net_r` distribution** — the headline net
   metric is empty until a funding/settlement archive is configured — and
   the friction-only figure must be read through the explicitly named
   `"friction_net_r"` key. The reason string distinguishes the two cases
   (`funding:not-configured` vs `funding:missing`), and `n_net_evaluable`
   makes the net denominator explicit. The brief's own discipline ("a
   funding-adjusted figure is never labelled friction-only") is the reason
   I did **not** make `net_r` silently fall back to a friction-only number.
4. **Censoring by sample end is conservative and label-blind — and it
   discards observable outcomes.** An event whose *full* horizon extends
   past `sample_end_ts` is CENSORED even if its barrier was hit inside the
   sample (a TP on bar 1 of a 48-bar horizon is censored when the sample
   ends 10 bars later). **Consequence: the resolved denominator shrinks**
   (`coverage` falls, `n_measured` and `n_net_evaluable` fall with it) and
   the discarded events are counted in `n_censored` rather than dropped.
   This follows the brief's definition and avoids trusting a label that
   may have been computed on bars beyond the declared sample. If the
   orchestrator wants the "hit inside the sample is still reported"
   behaviour, it is a two-line change (compare the label's exit ts with
   `sample_end_ts` instead of the horizon end).
5. **Data-loss censoring is only reachable with hand-built markers.**
   The event objects produced by `research.collect_events` never carry an
   unlabelled slot (a candidate without a full path is dropped before it
   becomes an event), so "a missing bar run" cannot be detected from the
   object alone. The layer therefore detects it through `ev.censored` /
   `ev.censored_reason` / `ev.data_loss` / `ev.bars_available` markers and
   through an unresolvable label slot. A future collector that keeps
   unlabelled candidates only has to set one of those markers.
6. **Adverse excursion (MAE) is NOT implemented.** The design's risk
   measures mention adverse excursion; the research event contract
   (`research.Event`) carries no bar path and no MFE/MAE field, and the
   bar path of a past event is not available inside this measurement
   layer, so MAE cannot be computed here without either re-implementing
   the path walk (forbidden) or reading the archive. It is therefore
   **absent rather than approximated**: the risk measures the layer does
   report are the return distribution (mean, sd, percentiles, min/max),
   the hit rate, the cause shares, the cumulative incidence by cause and
   the two cluster intervals. A follow-up that stores the excursion per
   event at collection time (in the collector that owns the path) would
   make MAE/MFE computable here.
7. **Module naming — the final import path.** The plan (§1.3) named the
   contract `outcomes.resolve_competing(...)`, but `proto/outcomes.py`
   already exists on the live path (it resolves live *plans*, a different
   object) and the brief mandated `proto/quant/competing_outcomes.py`.
   The file name therefore stays as the brief specifies, and the **final
   import path is
   `proto.quant.competing_outcomes.resolve_competing(...)`** (with
   `proto.quant.inference` and `proto.quant.family_test` alongside it).
   No live-path module was renamed or re-exported.
8. **`stats.aalen_johansen` is used, not ignored.** The layer reports
   STATIC cause shares (`causes`) for the resolved denominator **and** the
   time-to-event view (`cause_incidence`, fix round 1) built on
   `aalen_johansen`: a cause that resolves early (a stop) and one that
   resolves late (a time exit) have the same share but a very different
   incidence curve, and only the incidence answers "how likely is this
   cause to have happened by bar t". The mapping is TP=1, SL=2, TIME=3,
   INVALIDATED=4, 0 = right-censored; a CENSORED outcome — and an
   INVALIDATED outcome with no exit timestamp — is censored at the
   horizon, because an unknown time is a censoring and not a guess.
9. **Performance: funding archives are read per event.**
   `cost_attribution.funding_cost` reads the coin's gzip archives on every
   call and I may not modify it; the harness memoises identical holding
   windows inside one `resolve_competing` call, but a run over tens of
   thousands of events will still re-read the archives many times (the
   same characteristic as the existing `eval_harness.funding_adjusted_config`).
   A batch/vectorised funding API would be a worthwhile follow-up; it is not
   a correctness issue.
10. **`expected_return` and `family_test.correct` have extra keyword-only
    parameters** (`days`, `B`, `seed`) beyond the brief's signature. The
    brief itself requires `days` to be passed as an argument to
    `expected_return`, and the bootstrap method needs a resample count and a
    seed to be reproducible. All are keyword-only with defaults, so the
    brief's calls still work unchanged.
11. **Not done (deliberately, out of scope):** the layer promotes nothing, it
    does not enable ACTIONABLE output, it does not touch the holdout, and
    it did not re-run or retune the 288-configuration evaluation. The
    288-config grid and the one-shot holdout remain untouched.
    *(Corrected in the final review: the claim that the layer "is not
    wired into any CLI, report or service path" is no longer true — the
    `champions compare` sub-command of `proto/quant/__main__.py` drives
    `competing_outcomes` + `inference` through `champion.compare`.  The
    sub-command is read-only: it cannot promote and it cannot spend the
    holdout.)*
12. **Denominator rule (chosen, and now consistent across the layer).**
    The event set of every entry point is the **rule-selected, TRADEABLE**
    events of the window: an event with no cost model (below the liquidity
    floor) is not a trade, so it is excluded — and counted, in
    `n_untradeable` — in `resolve_competing`, in `baselines` and in
    `chronological_probability` alike. The alternative (counting the
    untradeable events in the denominator) was rejected because a
    denominator that includes events the platform could never have taken
    overstates the sample. The consequence: for the same
    `(events, rule, geom, window, cfg)`,
    `baselines(...)["n_selected"] == resolve_competing(...)["n_selected"]`
    literally (asserted by a test), and the base rate's own denominator
    (`n`) is the subset whose LABEL could be resolved.

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
8e1f4e3 fix(quant): WS-B round 1 -- training prefix, cluster-count contract, placebo wording
d39794e docs(quant): WS-B task report (competing outcomes & inference)
5cc2408 feat(quant): WS-B competing outcomes, inference and family-level multiple testing
```

Nothing was pushed; the branch is local to this worktree.

---

# Fix round 1

**Status: DONE** (all findings addressed; the behaviour the reviewer asked
to keep was kept and is now documented).

Round-1 commits: `8e1f4e3` (the fix: `proto/quant/inference.py`,
`proto/quant/competing_outcomes.py`, `tests/test_ws_b_outcomes.py`) and
this report's own commit as the branch HEAD (its hash is reported to the
orchestrator in the return message -- a file cannot contain its own commit
hash).

## F1 (important) — the chronological estimator never used its training prefix

**Finding.** `proto/quant/inference.py:156-168` — `train = []` and
`start = min_train`, so the first predictions were fitted on an **empty**
training set (p ≡ 0.5) and the first `min_train` observations were neither
predicted nor used — silently discarded.

**Changed.**
* `proto/quant/inference.py:163-166` — the training prefix is now seeded
  before the loop:
  `train = [(it["bucket"], it["y"]) for it in items[:min_train] if it["y"] is not None]`.
  The first prediction is therefore fitted on the prefix, and the prefix
  joins the expanding window instead of being dropped.
* `proto/quant/inference.py:113-120` (docstring) — the contract "the first
  `min_train` events form the initial training prefix" now matches the
  code.

**Test that now covers it** (`tests/test_ws_b_outcomes.py`, section 7):
* "the first prediction is fitted on the training prefix" — 40 events whose
  first 30 are all hits ⇒ the first prediction must be > 0.9.
* "the training prefix is not discarded: n_train covers every event" —
  `n_train == 40` (it was 10).
* "the prefix itself is never predicted" — `n_predictions == 10`.

**Proof that the test catches the old behaviour** (the old code was
temporarily restored and the new suite re-run):

```
  FAIL  the first prediction is fitted on the training prefix   first p = 0.5
  FAIL  the training prefix is not discarded: n_train covers every event   10
```

## F2 (important) — `expected_return` reported a day-cluster interval with < 3 clusters

**Finding.** `proto/quant/competing_outcomes.py:555-576` — `use_days` only
checked that day labels existed, so `days=[0,0,1,1]` returned
`cluster="day"`, a 2-day bootstrap interval and `t=0.0`, contradicting the
docstring and the binding constraint that an interval whose cluster count
is below 3 must be `None` with a reason.

**Changed.**
* `proto/quant/competing_outcomes.py:554-637` — `expected_return` now
  follows the same rule as the cluster intervals in
  `inference.uncertainty`:
  * days supplied, aligned, **≥ 3 distinct days** → the day-cluster
    interval (`stats.day_cluster_mean`), `stats.day_cluster_t`, `se = mean/t`,
    `cluster="day"`;
  * days supplied, aligned, **< 3 distinct days** → **no interval**
    (`ci_lo`/`ci_hi` are `None`) with a reason naming the cluster count;
    `cluster="row"` (the se/t are row-level) and the row-level interval is
    reported separately as `row_ci_lo`/`row_ci_hi`;
  * no day labels, or labels that do not align → the plain mean with the
    row-level interval, `cluster="row"`, and a reason that distinguishes
    "no day labels supplied" from "day labels do not align";
  * `row_ci_lo`/`row_ci_hi` are always present, so the row-level view is
    never lost.
* The docstring was rewritten to state the contract above.

**Test that now covers it** (`tests/test_ws_b_outcomes.py`, section 6):
* "fewer than 3 day clusters gives NO interval and a reason";
* "the cluster field is honest about the fallback that was used";
* "the row-level interval is still available next to the suppressed one";
* "with 3 distinct days the day-cluster interval is reported";
* "day labels that do not align say so (not 'no day labels supplied')".
* The test that locked in the forbidden behaviour
  ("expected return with days uses the day cluster" on a 2-day fixture)
  was **corrected** to assert the new contract.

**Proof that the test catches the old behaviour** (old code temporarily
restored):

```
  FAIL  fewer than 3 day clusters gives NO interval and a reason   day-cluster t undefined: ...
  FAIL  the cluster field is honest about the fallback that was used   day
```

## F3 (important) — the placebo was described as signal-free

**Finding.** The donor pool is built from every event in the window,
including the rule's own events from other dates, so the placebo inherits
the rule's average edge and can only detect a date-specific deviation.
The docstrings and report §4.5 over-claimed ("the same marginal
distribution and no signal"; "the fixture has no signal and the placebo
says so").

**Changed (wording only — the mechanism is unchanged and was accepted).**
* `proto/quant/inference.py:21-33` (module docstring) — the placebo
  "breaks the TEMPORAL ALIGNMENT of the signal … while RETAINING the
  rule's average edge, because the donors are themselves rule events. It
  therefore tests for DATE-SPECIFIC deviations, not for the rule's edge
  from scratch; a rule whose edge is stable across dates is NOT
  distinguished from the placebo by it."
* `proto/quant/inference.py:429-452` (`baselines` docstring) — the same
  statement, plus the donor-resample limitation.
* `TASK-B-REPORT.md` §2.2 and §4.5 — the same wording; §4.5 now says
  explicitly that the p-value of 0.53 means "the observed rate is not
  unusual among matched cross-date draws" and is **not** a test of the
  rule's edge against a signal-free null.
* No test asserted the over-claim (the placebo tests are mechanism tests);
  the section-9 comment was left factual.

## F4 (minor) — dead / colliding constant

`proto/quant/inference.py:44` — `N_TOD_BUCKETS = 4` was dead and collided
by name with `proto/watcher.py:17` (`N_TOD_BUCKETS = 48`, a different
concept). **Removed** (`proto/quant/inference.py:46-48` now only defines
`TOD_BUCKET_S`); nothing referenced it.

## F5 (minor) — the calibration bucketing re-implemented `model.bucket_table`

`proto/quant/inference.py:199-212` — the equal-count split is
`model.bucket_table`'s rule, but `bucket_table` returns
`(mean_pred, mean_y, n)` tuples and never exposes the realised labels, so
it cannot report `n_hit` or the `lo`/`hi` range. **A comment saying
exactly this was added** at `proto/quant/inference.py:199-206`; the
bucketing itself is unchanged.

## F6 (minor) — `stats.aalen_johansen` was unused

**Changed: it is now used where it belongs.**
* `proto/quant/competing_outcomes.py:650-686` — new public
  `cause_incidence(outcomes, horizon_bars)` builds the cumulative
  incidence by cause on `stats.aalen_johansen` (TP=1, SL=2, TIME=3,
  INVALIDATED=4, 0 = right-censored; a CENSORED outcome — and an
  INVALIDATED outcome with no exit timestamp — is censored at the horizon,
  because an unknown time is a censoring and not a guess).
* The module docstring lists it as the time-to-event view of the same
  taxonomy.
* **Test** (section 12): "the incidence reports every competing cause",
  "the incidence at the horizon is the hand calculation" (0.25 / 0.25 /
  0.25) and "the censored unit stays at risk (it is censored, not failed)".
* The report records why the layer reports both views (concern 8).

## F7 (minor) — the placebo denominator and p-value

**Changed.**
* `proto/quant/inference.py:541-566` — `n_per_placebo` is now the number
  of donors **actually used** per draw (plus `n_per_placebo_min` /
  `n_per_placebo_max`), not the number of selected events;
  `n_donors_total` is reported as `n_cross_date_donors`.
* The placebo p-value now carries the `(cnt + 1) / (B + 1)` correction
  `stats.spa_max_t` uses, so it is never exactly zero; the raw count is
  reported as `n_extreme`.
* **Tests**: "the placebo p-value carries the (cnt+1)/(B+1) correction" and
  "the placebo denominator is the donors actually used".

## F8 (minor) — inconsistent reasons and a statistic next to a suppressed interval

**Changed.**
* `proto/quant/competing_outcomes.py:554-637` — the reason now
  distinguishes "no day labels supplied" from "day labels do not align
  with the evaluated outcomes" (the old code emitted the former in both
  cases).
* `proto/quant/inference.py:324-380` — a suppressed interval now takes its
  statistic with it: `t_day` / `t_coin` are `None` when the matching
  interval was suppressed (too few clusters **or** a degenerate
  zero-width interval).
* **Tests**: "day labels that do not align say so", "a degenerate
  (zero-width) interval is suppressed with a reason", "a suppressed
  interval takes its statistic with it", "too few clusters takes the
  statistic with it as well".

## F9 — denominator consistency between the two modules

**Finding.** `resolve_competing` dropped untradeable events from
`n_selected` while `inference.baselines` did not, so the two modules'
"same event set" denominators differed.

**Changed — the rule chosen: the event set is the rule-selected,
TRADEABLE events everywhere.** An event with no cost model (below the
liquidity floor) is not a trade, so it is excluded — and counted in
`n_untradeable` — in every entry point.
* `proto/quant/inference.py:464-479` (`baselines`): the rule-selected and
  the window-wide event sets are filtered the same way
  `resolve_competing` filters them (`cfg.cost.round_trip(qv24) is None`),
  and `n_untradeable` / `n_untradeable_window` are reported.
* `proto/quant/inference.py:241-249` (`chronological_probability`): the
  denominator vocabulary was aligned — `n_rule_selected` (the raw rule
  selection), `n_selected` (the tradeable set, the same number
  `resolve_competing` reports) and `n_untradeable`.
* **Test**: "the baselines and the outcome layer use the same event set"
  — `baselines(...)["n_selected"] == resolve_competing(...)["n_selected"]`
  and both report the same `n_untradeable`.
* The rule and its rationale are recorded in report concern 12.

## F10 — documented deviations recorded (behaviour unchanged)

Recorded in report §5 as required: **MAE/adverse excursion absent** (6 —
the event contract carries no bar path and no MFE/MAE, so it cannot be
computed here without re-implementing the path walk); **module naming**
(7 — `proto/outcomes.py` already exists on the live path, so the final
import path is `proto.quant.competing_outcomes.resolve_competing(...)`);
**data-loss censoring only reachable with hand-built markers** (5);
**label-blind end-of-sample censoring discards observable outcomes and
shrinks the resolved denominator** (4); **`net_r is None` whenever funding
is unattributable, so the headline net metric is empty without a funding
archive** (3).

## Commands and real output (fix round 1)

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
  PASS  fewer than 3 day clusters gives NO interval and a reason
  PASS  the cluster field is honest about the fallback that was used
  PASS  the row-level interval is still available next to the suppressed one
  PASS  with 3 distinct days the day-cluster interval is reported
  PASS  day labels that do not align say so (not 'no day labels supplied')

=== 7. chronological probability and calibration ===
  PASS  the estimator predicts after the training prefix
  PASS  every prediction that had a label is scored
  PASS  every calibration bin carries its denominators
  PASS  the bins' denominators sum to the scored outcomes
  PASS  the brier score is reported
  PASS  the calibration slope is reported or explained
  PASS  the estimator learned the bucket structure
  PASS  too few training events return empties with a reason
  PASS  the first prediction is fitted on the training prefix
  PASS  the training prefix is not discarded: n_train covers every event
  PASS  the prefix itself is never predicted

=== 8. uncertainty: day cluster AND coin cluster ===
  PASS  both intervals are present
  PASS  the cluster counts are reported
  PASS  the coin-cluster interval is wider than the day-cluster interval
  PASS  fewer than 3 clusters gives None intervals with a reason
  PASS  with only 3 coins the coin interval is still reported
  PASS  a degenerate (zero-width) interval is suppressed with a reason
  PASS  a suppressed interval takes its statistic with it
  PASS  too few clusters takes the statistic with it as well

=== 9. baselines on the same event set ===
  PASS  the placebo distribution is reproducible under a fixed seed
  PASS  the placebo percentiles are ordered
  PASS  the placebo distribution is not degenerate
  PASS  every baseline carries n and a denominator label
  PASS  the unconditional baseline is computed on the same event set
  PASS  the placebo reports the observed value and the extreme fraction
  PASS  the placebo p-value carries the (cnt+1)/(B+1) correction
  PASS  the placebo denominator is the donors actually used
  PASS  the baselines and the outcome layer use the same event set

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

=== 12. cumulative incidence by cause (stats.aalen_johansen) ===
  PASS  the incidence reports every competing cause
  PASS  the incidence at the horizon is the hand calculation
  PASS  the censored unit stays at risk (it is censored, not failed)

ALL PASS

$ python3 tests/test_quant.py
...
  PASS  registered ids are unique and include controls

ALL PASS

$ python3 tests/test_quant_funding_regressions.py
...
  PASS  a boundary with no snapshot in its OWN regime window -> incomplete

ALL PASS (funding regressions)

$ python3 tests/run_all.py
PASS  test_adapter.py  [ALL PASS]
PASS  test_app_watcher.py  [ALL PASS]
PASS  test_backfill.py  [ALL PASS]
... (78 more files)
PASS  test_ws5_eval_harness.py  [ALL PASS]
PASS  test_ws_b_outcomes.py  [ALL PASS]

ALL SUITES PASS (82 files)
```

(95 checks in the WS-B suite; the full-suite line is reproduced verbatim
in the run above.)
