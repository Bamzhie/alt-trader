# Grok review — directional initiation score (proposal)

**Date:** 2026-10-10
**Status:** Approved by the operator 2026-10-10 and implemented as a shadow score (see "Implementation notes" at the end). The live v2 score, flag, ranking, plans and episode rules are unchanged.
**Read:** `docs/quantitative-audit-2026-10-09.md`, both design specs, `proto/scorer.py`, `proto/indicators.py`, `proto/scan.py`, `proto/bybit.py`, `proto/outcomes.py`, `proto/measurement.py`, `proto/episode_report.py`, `tests/test_scorer.py`.

## Decision requested

Approve or reject a **shadow score**, rule id `score-v3-dis`, logged beside the live v2 score.

The live flag, the `score >= 24` gate, episode membership, planner fills, and the frozen outcome rows stay on the current rules. v3 is ranked in the UI only after the pre-registered comparison in section 6 passes on data that this change did not fit.

This is a better *definition* of the signal the specs already describe (early one-sided flow). It is not a claim of edge. The repository still has no out-of-sample result.

## What is already in good shape

Keep these. They are the measurement system, and the proposal sits on top of them.

- Read-only. No order path, no keys.
- Completed-bar filtering, OHLC checks, interval-aware MEXC history, MACD tail alignment. The 2026-10-09 audit's F-01, F-03, F-04, F-05, F-06, F-09 are fixed in the current tree. Section 8 of that audit still says a forming bar may be scored; that sentence is stale relative to section 4.2.
- Vetoes are explicit and still logged: 24h quote volume under $250k, spread over 3%, 1h move at or above 12%, 24h move at or above 35%, fewer than 30 bars.
- Base volume for expansion and OBV, so a falling price does not shrink the magnitude.
- Episode identity, coin-cluster bootstrap, and the refusal to print net P&L. That reporting contract stays.

The live score itself is an uncalibrated rank of unsigned activity. The audit already says that. The defects below are why a different formula is worth shadowing, rather than waiting to fit the current weights.

## Defects in the current score

### 1. Open interest treats long liquidation as new shorts

`oi_funding_component` sets full lean when `sign(price change) == sign(OI change)`, and half lean otherwise. The comment calls the equal-sign case "new longs or new shorts".

Opening a short **increases** open interest, the same way opening a long does. The design spec says this in section 4: rising OI with falling price means shorts are still being added. The code does the opposite.

Computed from the current function, funding too small to move the lean (`0.0001` vs cap `0.0018`), `|OI change| = 4%`:

| Case | What the market did | Lean the code emits | Agreement flag |
|---|---|---:|---:|
| Price +1%, OI +4% | New longs | **+1.00** | +1 |
| Price −1%, OI +4% | New shorts | **−0.50** | −1 |
| Price +1%, OI −4% | Short covering | +0.50 | −1 |
| Price −1%, OI −4% | Long liquidation | **−1.00** | +1 |

New shorts, the strong bearish case, outrank nothing. Long liquidation, an unwind, gets the full bearish lean. The bullish side is accidentally right (new longs get +1, short covering gets +0.5), so the book is tilted long on flow.

`tests/test_scorer.py` locks this in. It mirrors by negating price **and** OI together, and requires equal lean magnitude. That is algebraic symmetry. The economic mirror of "price up, OI up" is "price down, OI up".

There is a second mismatch in the inputs. `bybit.oi_state` returns the percent change from the oldest to the newest of 25 hourly samples, a span of up to 24 hours. `scan.analyse_one` pairs that with `change_1h`, the last 12 completed 5-minute closes. A 1-hour reversal against a 24-hour OI build is labeled with the wrong quadrant.

### 2. Earlyness taxes the breakout

```
earlyness = 1 − (0.65 × proximity_to_either_extreme + 0.35 × volume_expansion)
score     = 100 × weighted_magnitude × (0.55 + 0.45 × earlyness)
```

Proximity uses the last 48 bars **including** the current bar. A close that just made the high of that window has proximity 1. Volume expansion, the main abnormality term inside the volume/price magnitude (weight 0.40), is also inside the penalty.

So the two features that define "something just started" both reduce the multiplier. The multiplier also has a floor of 0.55, so a fully mature, fully extended coin still keeps 55% of its raw magnitude. Worked numbers:

| State | Volume expansion | Near extreme | Earlyness | Multiplier |
|---|---:|---:|---:|---:|
| Fresh breakout, volume 4× median | 1 | 1 | 0.00 | 0.55 |
| Quiet, mid-range | 0 | 0.50 | 0.675 | 0.85 |

A breakout whose raw magnitude is 0.70 scores `100 × 0.70 × 0.55 = 38.5`. A quiet coin at raw magnitude 0.30 scores `25.6`. The breakout still wins, and the gap is compressed by a penalty aimed at the breakout. The 12% / 35% vetoes are already the chase filter. Earlyness repeats that job with the wrong clock: location in the range, rather than how many bars ago the impulse began.

Donchian position makes this sharper. It excludes the current bar, so a new high can be Donchian +1 (magnitude up) and earlyness 0 (score down) on the same print.

### 3. The score and the direction answer different questions

Magnitude is unsigned: volume expansion, `|Donchian|`, `|EMA gap|`, `|MACD histogram|`, ATR%, book imbalance, `|OI|`, funding extremeness. Direction is a second blend, magnitude-weighted, with a ±0.15 deadband.

Consequences that follow from the formulas:

- ATR% is 10% of the volume/price magnitude (`atr_pct / 0.05`, capped at 1). A volatile coin scores higher with no flow. The design spec lists ATR as a risk qualifier.
- Book thinness is a monotone reshape of book skew. For bids `b` and asks `a`, `|skew| = (b−a)/(b+a)` and thinness `= 1 − min/max`. With `b > a`, `|skew| = thin / (2 − thin)`. Multiplying magnitude by `(0.6 + 0.4 × thin)` does not measure depth. Absolute size never enters, and the 30% book weight is one REST snapshot of the top 20 levels.
- That snapshot can flip the composite lean. Direction is not reserved for the 5-minute structure.
- RSI is blended in after Donchian and EMA (`lean × 0.8 + rsi_dir × 0.2`). All three are price location. Flow (OBV) is diluted by a third copy of "where is price".
- Funding of either sign is subtracted from the flow lean once `|funding| / cap > 0.25`, up to 0.35. Continuation and crowding-fade are different hypotheses. One lean cannot be both, and the fade has no measured weight.

MACD-as-magnitude-only is a sound choice and stays. The design note that the histogram sign is unstable on a smooth path is correct.

## Proposed score

**Name:** directional initiation score, `score-v3-dis`.

**Hypothesis, one sentence:** rank a coin by how recently a 5-minute one-sided flow started, and add points only from evidence that supports that same side.

Weights below are a declared prior. They are frozen before any outcome is used. They are not a fit.

### Clock

- Features use completed bars only, as today.
- The OI quadrant uses a **1-hour** OI change: newest hourly sample versus the previous one, from the request `oi_state` already makes. Record the actual timestamp gap. If that gap is outside 45–90 minutes, the 1-hour OI feature is missing.
- Keep writing today's ~24-hour `oi_change_pct` unchanged, so existing rows stay comparable. Add `oi_change_1h_pct` for v3. Do not feed the 24-hour figure into the quadrant.
- Flat 1-hour price (`|change_1h| < 0.15` percentage points) does not produce a flow sign. OI can rise because both sides opened.

### Direction comes from 5-minute structure only

`L5` is the current volume/price lean with two removals:

- ATR% leaves the magnitude. It remains a displayed risk figure.
- The extra RSI blend leaves the lean. RSI stays on the card as a number.

```
L5 = clip(0.35 × Donchian + 0.35 × EMA_gap + 0.30 × OBV, −1, +1)
```

If `|L5| ≤ 0.15`, direction is NEUTRAL and `score_v3 = 0`. Book, funding, and OI cannot create a direction. They can confirm the side `L5` already has, or mark a disagreement.

### Initiation is age, not distance to the extreme

- Channel: prior 48 completed 5-minute highs and lows, current bar excluded (the Donchian window already does this).
- Break bar: the most recent bar whose close left that channel in the direction of `L5`.
- `age` = completed 5-minute bars since that break.

```
initiation = exp(−age / 18)          # if a break exists
```

| Age | Clock time | Initiation |
|---:|---|---:|
| 0 | this bar | 1.00 |
| 6 | 30 min | 0.72 |
| 12 | 1 h | 0.51 |
| 18 | 90 min | 0.37 |
| 36 | 3 h | 0.14 |
| 48 | 4 h | 0.07 |

If price has not left the channel:

- volume expansion ≥ 2.0 (ratio of 3-bar mean to trailing median ≥ 2) and `|L5| > 0.15` → `initiation = 0.50` (flow building, range still holding);
- otherwise `initiation = 0` and the v3 score is 0.

Volume expansion is no longer a lateness penalty. A 4-hour-old grind falls down the radar on age. The 12% and 35% vetoes still remove vertical chases, including a chase that is only a few bars old. That split is deliberate: this list is an onset radar. A trend watchlist would want a floor under initiation. v3 has no floor. Say so if you want one.

### Flow state, matched to the 1-hour price change

```
if 1h OI missing or 1h price flat:
    flow_state = "unavailable" or "flat"
    opening_for = 0
    opening_against = 0
elif OI_1h > 0:                              # positions opening
    flow_state = "opening"
    side = sign(price_1h)                    # +1 new longs, −1 new shorts
elif OI_1h < 0:                              # positions closing
    flow_state = "unwind"
    side = sign(price_1h)                    # covering or liquidation, weak
```

`opening_for = 1` only when `flow_state == "opening"` and `side == sign(L5)`.
`opening_against = 1` only when `flow_state == "opening"` and `side` opposes `L5`.
An unwind does not add points and does not zero the score. It is logged, because "fade the unwind" is a different hypothesis.

Economic symmetry this must satisfy, replacing the current test:

| Input | Required flow |
|---|---|
| Price +1%, OI +4% | opening, side +1 |
| Price −1%, OI +4% | opening, side −1, same magnitude |
| Price −1%, OI −4% | unwind, side −1, zero opening points |
| Price +1%, OI −4% | unwind, side +1, zero opening points |

Funding does not enter the score. Log `funding_crowd`:

- `with` when funding's sign equals `sign(L5)` and `|funding| / cap > 0.50`;
- `against` when the sign opposes and the ratio is over 0.50;
- `neutral` otherwise.

Episode reports can split on this flag later. v3 does not fade crowds and does not pay them.

### Book is a confirmation bit

`book_agree = 1` when book lean has the same sign as `L5` and `|book lean| > 0.20`. Otherwise 0. Opposition is a note (`book disagrees`). One top-of-book print cannot add 30 points and cannot flip the side.

MEXC-only names have no OI. They can still score on activity, location, and book. A name with confirming 1-hour opening flow scores higher than the same name without it. That keeps the current rule that a funding-only path must not outrank a real OI path, without the `0.35 × funding` magnitude filler.

### Formula

```
activity = clip(volume_expansion, 0, 1)     # existing map: ratio 1 → 0, ratio 4 → 1
location = clip(|L5|, 0, 1)

inner = 0.45 × activity
      + 0.25 × location
      + 0.20 × opening_for
      + 0.10 × book_agree

raw = 100 × initiation × inner
raw = raw × 0.50    if opening_against
raw = raw × 0.70    if 4H lean is strong and opposed to L5
raw = min(100, raw + 6)   if 1H and 4H leans are both strong and both match L5

score_v3 = 0  if |L5| ≤ 0.15 or initiation == 0
score_v3 = round(raw, 1) otherwise
direction_v3 = LONG if L5 > 0.15 else SHORT if L5 < −0.15 else NEUTRAL
```

Higher-timeframe leans stay on the current volume/price formula. Missing 1H or 4H data remains a note and blocks the +6. It is not treated as a zero lean. The 4H opposition multiplier stacks with `opening_against` when both are true (`0.70 × 0.50`).

Inner weights sum to 1. A full print (initiation 1, activity 1, `|L5|` 1, opening in favor, book agrees, both higher timeframes agree) scores `100 + 6 = 100` after the cap. The same print with no OI scores `80`, then 86 if higher timeframes agree. A 3-hour-old full print scores about `14` before the bonus.

`opening_against` is a halving, not a zero. A hard zero would drop those coins out of any future v3 flag and we would never measure them. The state is always logged.

### What v2 would have scored, under this prior

The audit's rising fixture was earlyness 0.14 and score 24.2, because price sat at the extreme and volume was expanded. Under v3 that path is a recent break: initiation near 1, activity high, `L5` coherent, book agrees, OI absent. Inner is about `0.45 + 0.25 + 0.10 = 0.80`, score about 80 before the higher-timeframe bonus. The same path 36 bars later scores about 14% of that, from age alone, even though it is still pinned to the high. Today's earlyness treats both prints as mature.

Flat noise with `|L5| ≤ 0.15` scores 0. The audit fixture scored 9.3 from residual magnitude.

These are formula consequences on the audit's synthetic bars. They are not a backtest.

## What stays untouched

- Live `score`, live direction, vetoes, `flag_version = 2`, the `score >= 24` flag, stake gating, planner, plan-touch rules, and `outcome_log` / episode outcome semantics.
- F-07 and F-08 stay frozen. v3 does not invent a fill model or a net P&L.
- No new indicators. Stochastic, VWAP, and Bollinger stay unwired.
- No weight search and no model fit on the collected week.
- Cross-sectional rank against the peer universe (design spec section 3.4) is a later experiment. Mixing it into v3 would make a failure unreadable.
- Contributing rule 2: logged v2 semantics do not change mid-collection.

## Logging

On every scored card, in addition to the current columns:

| Field | Meaning |
|---|---|
| `score_v3` | the shadow score, 0–100 |
| `direction_v3` | LONG / SHORT / NEUTRAL from `L5` only |
| `initiation` | the age transform |
| `flow_state` | `opening`, `unwind`, `flat`, `unavailable` |
| `oi_change_1h_pct` | matched-window OI change; null when the gap check fails |
| `opening_for`, `opening_against`, `book_agree` | 0/1 |
| `funding_crowd` | `with` / `against` / `neutral` |
| `score_rule` | constant `score-v3-dis` |

Bump `CODE_REV` on the measurement path so a cohort can require this revision. Do not reopen episodes that v2 already opened, and do not let `score_v3` change who qualifies.

GUI: show v3 as a labeled shadow column. The rank order the operator acts on remains v2 until section 6 promotes it.

## 6. Pre-registered comparison

Run this after shadow rows exist. Do not change the weights in response to it. If the prior loses, publish the loss and keep v2.

**Unit:** the episode, using the first qualifying observation's v2 score and v3 score. Repeated scans of one coin count once. Cluster bootstrap by coin, same 2,000 draws and the existing seed style in `episode_report.py`.

**Minimum before any number is quoted as a result:** 30 distinct coins and 20 episodes in that direction. Below that, counts only. This matches the reporting module's own floors.

**Split:** first half of episodes by start time is a look. The decision uses the second half only. Long and short are separate. Coins with `oi_unavailable`, unresolved horizons, and delists stay in the coverage table.

**Outcomes,** still the frozen definitions:

1. Spearman rank correlation of the score with the 4-hour signed close-to-close return. v3 beats v2 on the second half, and the coin-cluster 95% interval on v3 excludes 0, in that direction.
2. Mean 4-hour signed return, top quintile minus bottom quintile, same split. v3's spread beats v2's.
3. Two baselines on the same episodes: sign of the last 1-hour return, and sign of the 24-hour return. v3's correlation beats both on at least one direction, and does not lose to both on the other.
4. A second column, labeled as a sensitivity: the same 4-hour return minus `0.05% × 2` taker cost. This is not net P&L and it is not the promotion metric. It stops a tiny gross correlation from being read as a trade.
5. Plan first-touch (stop before target, as `plan_touches` already does) reported beside the close-to-close number. Hit rate is not the decision metric.

**Promotion:** switch the live rank and the flag to v3 only when (1) and (2) hold on the second half for both directions, and (3) holds as written. Otherwise the live path stays v2. A later weight change is a new rule id, not an edit of `score-v3-dis`.

## Tests that change if this is approved

- Replace the OI cases in `tests/test_scorer.py`. The economic mirror is a price-sign flip with OI held positive. Price down and OI down must be an unwind, with a smaller directional contribution than price down and OI up.
- Initiation: a 2-bar breakout pinned to the window high scores above the same extremity 36 bars later. Today's earlyness test does not require that, and the current formula fails it.
- A strong opposing book does not change `direction_v3` when `|L5| > 0.15`.
- A 24-hour OI change does not enter the v3 quadrant. A 1-hour sample gap outside 45–90 minutes yields `flow_state = unavailable` and `opening_for = 0`.
- Symmetry of `L5`, book confirmation, and the opening-flow side under a mirrored book and a mirrored price path. Magnitude of `score_v3` matches; the sign of `L5` flips.
- Existing v2 score assertions stay green. Shadow fields are additive.

## Implementation boundary, after approval

Files: `proto/scorer.py` (new function, v2 function left in place), `proto/bybit.py` (also return the last-step 1-hour OI change and the sample gap), `proto/scan.py` (pass the 1-hour OI through), `proto/store.py` (new columns, no rewrite of old rows), a small shadow column in `gui/` and `qtgui/`, and the tests above.

Out of scope until you ask: changing the live sort, refitting weights, cross-sectional factors, cost-aware outcome rows, exchange-fee verification.

## Recommendation

Approve the shadow score. The OI quadrant is wrong relative to the spec's own short-side sentence, the earlyness term penalizes the state the radar is for, and the 30% book weight can appoint a direction the 5-minute structure does not have. Those three are definition errors. Fitting v2's weights on the current outcome log would calibrate the errors.

Reject, or send back, if you want the board to keep mature trends visible. In that case the initiation decay needs a floor, and that floor should be chosen before any row is written.

## Implementation notes (2026-10-10)

Implemented exactly as specified except for the points below, each a judgment call on wording the proposal left open.

1. **Break age counts from the start of the current run of channel exits.** The text defines the break bar as "the most recent bar whose close left that channel". Read literally, a steady grind makes a new 48-bar high on every bar, so each bar is itself a channel exit and the age is 0 forever. That contradicts the proposal's own statement that a 4-hour-old grind falls down the radar on age. `ind.breakout_age` therefore measures from the first bar of the latest uninterrupted run of exits. A move that broke out and then stalled at the high ages exactly as the table says (age 36 -> 0.14); a fresh exit after a dip back inside the channel starts a new run at age 0. If you want the literal reading instead, it is a one-line change (use `last` instead of `start`), and it would be a new rule id.
2. **L5 keeps the existing 20-bar Donchian position.** Only the initiation channel uses the prior 48 bars. The proposal's remark that "the Donchian window already does this" is about excluding the current bar, which holds for both.
3. **OI exactly unchanged over the hour is `flat`**, same as a flat 1-hour price. The proposal only defined OI > 0 and OI < 0.
4. **The v2 OI cases in `tests/test_scorer.py` were kept**, because v2 stays live and its assertions must stay green. The economic-mirror cases live in `tests/test_score_v3.py` against the v3 flow function.
5. **Storage.** Ten additive nullable `signal_log` columns (migration adds them to existing databases; old rows stay NULL). `oi_state` still returns the same 2-tuple; the 1-hour fields ride on its `.detail` attribute from the same single request. `CODE_REV` is now `2026-10-10-score-v3-shadow`.
6. **GUI.** A labeled `v3 SHADOW` column and a detail-pane block in both front ends. Sorting, filters, flags and the default rank order are untouched; the column is display only.
7. **Not done, as scoped:** promotion, weight changes, the section 6 comparison itself (it needs shadow rows to accumulate first), and any change to F-07/F-08 semantics.

