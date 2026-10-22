# +100% on margin: what it requires and what the archive says (2026-10-22)

**Question.** How much price move does a +100% return on margin require, what
does the stop cost at that leverage, and how often has this archive actually
delivered it?

**Answer on the current archive.** The target is *reachable* — at 20x a
5.21% move is touched inside 24 h in 26.6% of entries — but it is *not
profitable to enter at random*: the target is reached **before** the stop in
only 18.1% of entries, the cost-adjusted breakeven is 33.3%, and the mean ROE
per random entry is **−3.7% of margin**. Leverage moves the numbers around;
it does not create edge.

**This is a calculator, not a recommendation.** Nothing is promoted by it, no
event becomes actionable, and the registered rules' own evaluation is a
separate (already recorded) result — see
[§ 5. What this does NOT show](#5-what-this-does-not-show).

Everything below was measured on this repository's own bar archive with the
code in this repository. The commands are given so every number can be
re-derived.

---

## 1. What "+100% on margin" actually is

Leverage does not create edge; it re-scales a price move.

- A target worth **+R on margin** at leverage `L` is a **gross price move** of
  `R/L + c`, where `c` is the round-trip cost as a fraction of notional. At
  20x, +100% on margin is a 5.00% move before costs and **5.21%** with the
  0.21% round trip the sampled entries of this archive carry.
- The **stop** costs `stop_roe / L` on margin at that leverage: a −50% stop at
  20x is a 2.50% price move against, **2.29%** with the costs added.
- For a **driftless** price, the chance of reaching a target at distance `a`
  before a stop at distance `b` is `b/(a+b)`. The **cost-adjusted
  breakeven** hit rate is `(b+c)/(a+b)`. A "+100% / −50%" trade therefore has
  to hit its target about **33.3%** of the time just to break even — the
  signal has to beat that, not merely be "volatile".
  **Both of these are "the target is reached FIRST" probabilities** (over
  all entries). The "TP among terminal" column further down is a *different*
  rate — over the entries that hit a barrier — and is therefore not
  compared with them.
- **Liquidation headroom.** At leverage `L` the isolated-margin liquidation
  distance is about `1/L − maintenance margin` (0.5% assumed,
  `proto/quant/risk.py::MAINT_MARGIN`, **not venue-verified**). The stop must
  sit inside 80% of that distance to be survivable, or the position is
  liquidated before the stop is reached. `max_leverage_for_stop()` gives the
  highest leverage at which a given stop still clears that buffer: the 2.29%
  stop of the 20x geometry admits at most **29.8x**, the 1.79% stop of the
  25x geometry at most **36.6x**.
- Under **cross margin** (the operator's stated setup) liquidation depends on
  the whole account, so the per-trade leverage cap and the aggregate open-risk
  cap are the real protections; the isolated-margin distance above is a floor
  check only, exactly as `proto/quant/risk.py` already says.

## 2. How it was measured

```bash
python3 -m proto.quant roe --data data/bars --leverage 10,20,25 --stop-roe 0.5 \
        --stride 12 --top 15 --out /tmp/opencode/roe-preholdout.json
```

| Item | Value |
|---|---|
| Archive | `data/bars`, 583 coin archives (582 traded + BTC) |
| Archive span | 2026-07-13T02:35Z → 2026-10-21T23:45Z = **100.9 d** at the time of the run (the union span over every coin equals BTC's span). A re-run some hours later saw **101.2 d** with *identical* samples (140,515 entries): `DEFAULT.splits` uses fixed day constants, so appending bars only moves the holdout's **end** — the measured window does not move |
| Archive snapshot | BTC first bar ts `1783910100`, last bar ts `1792626300` at the time of the run. **The collector is live**: the archive grows, and because the window is anchored to the archive's *first* bar, a backfill moves the anchor and shifts the measured days |
| Window | **pre-holdout only**: dev days 6–61 + validation days 63–77. The holdout window (from day 79) is **excluded from the measurement**: no entry is sampled there and no outcome is labelled there. The archive's **timestamps** are still read — they are what places the split boundaries — but no holdout outcome is computed, and the CLI refuses any window whose label horizon would reach into the holdout |
| Splits used | `DEFAULT.splits` (dev 6–61, embargo 2 d, val 63–77, embargo 2 d, holdout 79→end) |
| Bars | 5-minute, 24 h horizon (288 bars), entry at the **open of the next bar**, stop-first on ties |
| Sampling | every 12th bar of every contiguous run, **both sides** labeled with the same geometry; a label window never crosses a timestamp gap, and neither does the 24 h liquidity window |
| Liquidity | an entry counts only when its trailing-24 h quote volume is at least **$1,000,000**; a coin contributes only when its trailing-24 h quote volume at the **end of the window** clears the same floor (that is also where its round-trip cost comes from) |
| Costs | fee 0.05 %/side + tiered slippage by trailing-24 h quote volume (an **assumption**, not a measurement); **0.13 %–0.34 %** round trip per coin, **0.21 %** sample-weighted |
| Funding | **not modeled** |
| Universe that contributed | **116 of 582** coins: 446 are below the liquidity floor at the window end and 20 have no archive or no bars in the window |
| Samples | **140,515 entries → 281,030 samples** (both sides) |
| Uncertainty | **moving-block** bootstrap over 3 consecutive UTC days (600 resamples) over the pooled per-sample rows. The label window is 24 h, so the rows of adjacent days share their price paths: resampling whole days independently would understate the uncertainty. The block length is a declared assumption and the sensitivity across 1–14 days is reported in §3 |
| Runtime | about **6–8 minutes** on this archive at three leverages and a stride of 12; the bar parse dominates, and the per-coin block bootstrap (3-day blocks, 600 resamples) adds tens of seconds |

The sampling is **unconditional**: it is not conditioned on any rule, signal
or feature, so it measures the *base rate* of the market, which is the number
any rule has to beat.

### The two context numbers (drift and volatility)

The report quotes the window's drift and the archive's median volatility next
to the base rates. Neither is produced by the `roe` command, so both have
their own read-only probe:

```bash
python3 scripts/roe_context_probe.py --data data/bars --stride 12
```

It reads the same archive and the same pre-holdout window, and prints the
24 h log return over exactly the sampled entries (the drift) and the 5-minute
GK volatility distribution (the volatility the registered geometries are
turned into ROE levels with). It writes nothing.

## 3. The measured base rates (target +100% / stop −50% on margin)

| Leverage | price move needed | stop | liquidation distance | target touched in 24 h | TP first | stop first | neither | TP among terminal (95 % block bootstrap) | driftless null | cost-adjusted breakeven | mean ROE per trade (95 % block bootstrap) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10x | 10.21 % | 4.79 % | 9.50 % | 10.2 % | 7.8 % | 28.2 % | 64.0 % | 21.7 % [19.5 %, 23.7 %] | 31.9 % | 33.3 % | **−1.4 %** [−2.2 %, −0.6 %] |
| 20x | 5.21 % | 2.29 % | 4.50 % | 26.6 % | 18.1 % | 51.5 % | 30.4 % | 26.0 % [24.0 %, 27.8 %] | 30.5 % | 33.3 % | **−3.7 %** [−5.0 %, −2.4 %] |
| 25x | 4.21 % | 1.79 % | 3.50 % | 33.6 % | 21.0 % | 58.2 % | 20.9 % | 26.5 % [24.8 %, 27.9 %] | 29.8 % | 33.3 % | **−5.2 %** [−6.5 %, −3.8 %] |

"Target touched in 24 h" counts the target being reached at *any* time inside
the horizon, stop or not; "TP first" counts only the entries where the target
came before the stop; "TP among terminal" restricts to the entries that hit
one of the two barriers.

**What the table says.**

1. **The target is reachable.** At 20x the price touches a 5.21% move inside
   24 h in 26.6% of entries. A +100% on-margin outcome is not a
   once-in-a-lifetime event — but it is reached *before the stop* in only
   18.1% of entries.
2. **The base rate is far below breakeven.** The cost-adjusted breakeven is
   the "target reached FIRST" rate that a +100%/−50% trade needs to break
   even: **33.3%** at every leverage. The measured TP-first rate is **7.8%**
   (10x), **18.1%** (20x) and **21.0%** (25x) — 12 to 26 points below it.
   **Entering at random loses money at every leverage tested**, and the mean
   ROE per trade is negative at all of them (see the interval discussion
   below for how firmly that is established).
3. **Leverage mostly costs.** The mean ROE per trade falls from −1.4% at 10x
   to −5.2% at 25x. The barrier arithmetic alone — the target pays exactly
   +100% on margin and the stop exactly −50% — gives
   `p_tp·100% − p_sl·50%` = **−7.7%** of margin at 20x; the 30.4% of entries
   that finished inside the 24 h window carry the window's drift and the
   geometry's asymmetry and make up the rest.
4. **The window was not driftless.** The mean 24 h log return over the *same*
   140,515 entries is **+0.55%** (median +0.03%, sd 7.94%) — so the drift is
   working *for* a random entry in this window, not against it. These
   numbers describe **this** window, not a law.
5. **A tighter stop does not help.** Measured at 20x with a −30% stop
   (`--stop-roe 0.3`): the breakeven falls to **23.1%**, but the TP-first rate
   falls further, from 18.1% to **13.2%**, and the TP-among-terminal rate to
   **16.0%** [14.2%, 17.8%], for a mean ROE of **−3.9%** [−5.2%, −2.5%]. The
   stop is hit first in 69.2% of entries.
6. **The driftless null is a reference, not a like-for-like comparator.**
   `p_null = b/(a+b)` is the **infinite-horizon** probability of reaching the
   target before the stop. Over a 24 h horizon 30.4% of the entries never
   touch a barrier at all, so the finite-horizon TP-first rate is not
   directly comparable to it. The comparable statement is the one in point 2:
   the TP-first rate has to clear the cost-adjusted breakeven, and it does
   not.

### How firm is the negative sign? (the interval and its dependence)

The label window is 24 h, so the rows of two adjacent days share their price
paths: a trade entered late on one day and a trade entered early the next are
**not independent**. Resampling whole UTC days independently — the repo's
`stats.day_cluster_mean` — would therefore understate the uncertainty, so the
intervals here resample **blocks of consecutive days**
(`stats.block_cluster_mean`, a moving-block bootstrap without wrap-around).
The block length is a declared assumption (3 days: enough to cover the 24 h
overlap with a day of margin), so the sensitivity is reported with it:

**mean ROE per trade, 95 % interval by block length**

| Leverage | block 1 d | block 2 d | block 3 d (reported) | block 7 d | block 14 d |
|---|---|---|---|---|---|
| 10x | [−2.1 %, −0.7 %] | [−2.2 %, −0.5 %] | [−2.2 %, −0.6 %] | [−2.3 %, −0.6 %] | [−2.1 %, −0.8 %] |
| 20x | [−4.9 %, −2.6 %] | [−5.1 %, −2.4 %] | [−5.0 %, −2.4 %] | [−4.8 %, −2.4 %] | [−4.7 %, −2.9 %] |
| 25x | [−6.3 %, −4.1 %] | [−6.4 %, −3.8 %] | [−6.5 %, −3.8 %] | [−6.2 %, −3.8 %] | [−6.1 %, −4.3 %] |

**What that establishes, and what it does not.** The interval excludes zero at
**every** block length from 1 to 14 days, at every leverage, so the negative
sign of the mean is **not** an artifact of the day-to-day sampling
variability. That is the whole of the claim: the interval is a *sampling*
interval over days. It does **not** cover the uncertainty in the assumptions
— the cost model, the 24 h horizon, the liquidity floor, the coin universe
or the window's drift — because those are stated assumptions, not estimated
parameters. The interval is stable across the whole 1–14 day range reported;
a reader who believes the dependence is longer than 14 days should read the
sign as less established than this table shows, because nothing here measures
that.

The per-coin rows carry the same block interval at the same block length.
The **TP-among-terminal** column has a different denominator (the entries
that hit a barrier) and is reported with its own interval, not compared with
the breakeven.

The per-coin rows are in the command output and in the JSON artifact; the 15
most liquid contributing coins at 20x range from ETH (TP-among-terminal
17.5%, mean ROE −2.3%) to SOL (22.9%, +0.4%). A coin that beats the pooled
base rate is a *description of that coin*, not evidence of an edge: with 116
coins and no multiplicity control, some of them beat the base rate by chance.

## 4. What changed in the app

- **`proto/quant/roe.py`** (new): the ROE arithmetic (`required_move`,
  `target_plan`, `liquidation_distance`, `max_leverage_for_stop`), the
  per-event block (`event_block`), and the measurement (`empirical_rates`,
  `summarize_rates`, `coin_report`, `pooled_from_reports`, `pooled_table`).
- **`proto/quant/stats.py`**: `block_cluster_mean()` — the moving-block
  bootstrap over consecutive UTC days that the intervals use. The repo's
  `day_cluster_mean()` resamples whole days INDEPENDENTLY, which understates
  the uncertainty whenever the label window is longer than a day; the new
  estimator carries the adjacent-day dependence into the resample and
  reproduces `day_cluster_mean()` exactly at `block=1`.
- **`python3 -m proto.quant roe`**: the read-only CLI. It measures the
  pre-holdout window only, prints the required price move, the stop's cost on
  margin, the liquidation headroom, the measured base rates with their
  denominators and moving-block intervals (plus the interval at each
  candidate block length), the per-coin rows, and can write a JSON artifact. `BTC` is the market factor, never a traded coin, so it is
  excluded even when it is named in `--coins`.
- **`proto/quant/engine.py`**: every SHADOW event now carries
  `features.roe_100` — the leverage that event's *own* target would need for
  +100% on margin, what its stop then costs on margin at that leverage, and
  whether that stop sits inside the liquidation distance. At the archive's
  median 5-minute volatility (GK sigma 0.00214) the **registered geometries
  cannot serve a +100% objective as defined**:

  | registered geometry | stop | target | leverage needed for +100% | the stop's cost on margin | inside the liquidation distance? |
  |---|---|---|---|---|---|
  | `residual_shock:h8` T144 ks3 R1 | 7.40% | 8.00% | **12.9x** | **−98.0%** | no |
  | `breakout_start:rvol4` T288 ks1.5 R1 | 5.29% | 5.59% | **18.6x** | **−102.7%** | no |

  The block is read-only arithmetic; it changes nothing about what is emitted.
- **`proto/quant/champion.py`**: `from __future__ import annotations`. The
  class defines a method named `list` and annotates with `list[...]`; under
  Python < 3.14's eager annotation evaluation the class body raises
  `TypeError: 'function' object is not subscriptable`, so the module could
  not even be imported on the Python 3.10+ the README promises. On Python
  3.14 the import survives but **any** annotation introspection
  (`__annotations__`, `typing.get_type_hints`) still raises the same error.
- **`tests/test_quant_roe.py`** (new, 86 checks): the ROE identities, the
  side symmetry of the geometry, the deterministic stop/target accounting, the
  gap handling, the coin reports and the pooled intervals, the watcher block,
  the CLI and the champion compatibility guard.
- **`scripts/roe_context_probe.py`** (new): the read-only probe that produces
  the two context numbers (the window's drift and the archive's median
  volatility) quoted in this report.

## 5. What this does NOT show

- **No rule was tested here.** The numbers above are *unconditional base
  rates*: the market's own hit rate at that geometry. They say nothing about
  any rule's edge, and they are not a substitute for one.
- **The registered rules' own result is already recorded and it is negative.**
  See `docs/implementation-reports/quant-research-2026-10-20/final-report.md`
  §4: on the dev window every registered member has a **negative** expected
  net R per trade (−0.028 R to −0.048 R), the family-level SPA p-value is
  1.0, and **validation is `insufficient`** (the window is 14 days, the
  preregistered minimum is 20). No member met the seven criteria, so **the
  one-shot holdout was not spent** and no configuration was frozen.
- **Nothing was promoted.** Every event the watcher emits stays `SHADOW`;
  `proto/quant/gates.py` and the promotion thresholds are untouched.
- **The attached patch's own base-rate table is not reproduced here.** It
  reports **26** contributing coins; the same command on the current archive
  measures **116**. The two tables agree closely on the *shares* at 20x
  (25.0% vs 26.6% touched, 17.5% vs 18.1% TP first, 51.5% vs 51.5% stop
  first) but not on the universe, so their denominators are not the same and
  the attached universe could not be reproduced from the current archive.
  The numbers in this document are the ones this code produces today, with
  their denominators.

## 6. Limitations

- **24 h horizon, 5-minute bars.** The target is a *touch* inside 288 bars.
  A different horizon or a different bar size gives different numbers.
- **Intrabar assumptions.** The labeling uses each bar's high/low, so it
  assumes the barrier was touched at that price inside the bar and that the
  entry filled at the next open — no fill model, no partial fills, no spread
  widening in stress.
- **The costs are assumptions.** The 0.05%/side taker fee is the planner's
  constant; the operator's MEXC tier was never provided, and the slippage
  tiers are an assumption about spread and impact for a small account, not a
  measurement. **Funding is not modeled at all** — on a 24 h hold, funding is
  a real cost that these numbers omit.
- **Liquidity is a trailing-24 h quote-volume proxy**, not an order book.
  The archive has no open interest, no order-book depth and no funding join
  in this measurement, and those are the most plausible sources of a
  *conditional* edge.
- **The universe is 116 coins**, and the measurement is on the pre-holdout
  window of one archive. It is a description of this market, not a law.
- **The pooled intervals resample whole blocks of consecutive UTC days**
  (3 days by default, 600 resamples), because the label window is 24 h and
  the rows of adjacent days share their price paths. The block length is a
  declared assumption and the sensitivity across 1–14 days is reported; the
  intervals are *sampling* intervals over days and cover none of the
  uncertainty in the assumptions.
- **The coin universe is selected by liquidity at the window end**, so a coin
  that was liquid during the window but illiquid at the end is dropped, and a
  coin that listed late contributes only its late entries.

## 7. What it would take to output real "significant positive signals"

1. A rule whose hit rate beats **its own base rate** by more than costs,
   confirmed on validation and then once on the sealed holdout through
   `proto.quant.gates`. The thresholds in `gates.GateSpec` do not move.
2. A separately approved stage that lets a promoted rule emit ACTIONABLE
   rows. The engine deliberately cannot do this today; that is the owner's
   decision, not a code default.
3. Inputs the bar archive lacks — open interest, order-book depth, funding
   history joins — as the most plausible source of conditional edge. None of
   them was tested here.
