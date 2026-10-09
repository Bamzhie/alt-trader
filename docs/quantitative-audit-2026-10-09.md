# Alt Radar Quantitative Audit

**Audit date:** 2026-10-09  
**Scope:** Existing signal-generation pipeline in this repository  
**Mode:** Initial pass was read-only. This follow-up verified subsequent fixes; no application source changes or external API calls were performed.

> **Follow-up status (2026-10-09):** Commit `a91885a` addresses F-01 through F-06 and F-09; subsequent commit `f39c3c0` clarifies the hypothetical entry band. I verified the current code paths and ran `python3 tests/run_all.py`: all 16 offline suites passed. The runner intentionally excludes the live-network `tests/test_app.py`. F-07/F-08 outcome and execution semantics remain deliberately frozen during the measurement week. The working tree also contains an uncommitted `gui/model.py` change to render `MTF` and `DATA` notes in the GUI detail view; that user change is preserved.

## 1. Executive summary

Alt Radar is a read-only futures signal screener. It discovers MEXC USDT contracts, combines MEXC candles and order-book data with a Bybit open-interest input for shared symbols, then emits a scorecard with LONG, SHORT, or NEUTRAL direction. It does not place orders.

The implementation has useful safeguards: adapters check response envelopes and row/column shapes; raw timestamp order is checked; the scorecard has an explicit neutral state; vetoes are surfaced; and the planner rejects stop levels on the wrong side of entry. The indicator library is mostly composed of recognizable formulas with explicit, testable conventions.

The fixes materially improve data and calculation correctness. The adapters now drop forming candles and validate full OHLC invariants; MEXC requests interval-aware history; MACD aligns EMA series by common time indices; plans receive card funding; and MTF/ticker-skew warnings are surfaced. Signals still do not support profitability claims: outcome records remain close-to-close and pre-cost, while fill and execution assumptions are incomplete.

### Highest-priority remaining concerns

1. **Medium — outcome measurements are not execution P&L (F-07, deferred).** Outcomes use future closes, omit costs, and do not model intrabar stops/targets.
2. **Medium — the plan still has no fill model (F-08, deferred).** The entry band is now documented as hypothetical; it must not be read as an executable quote.
3. **Medium — ticker/candle skew is mitigated, not eliminated (F-02).** TTL is 180 seconds and skew over 1% warns, but the warning does not block scoring and source timestamps are not persisted.
4. **Low — closed-candle filtering uses local wall-clock time.** No exchange-time offset is measured.
5. **Medium — exchange costs and contract rules remain unverified.** Planner fee/leverage assumptions need official MEXC verification before execution modeling.

### Paper trading and evidence

The software is read-only, so the audit found no path that can place an order. Logging signals in a paper journal is reasonable only if the current limitations are clearly recorded. Completed-candle handling is now implemented; source timestamp alignment and outcome/execution realism still limit paper-trading performance claims.

No predictive value or profitability has been demonstrated in the repository. The design documentation calls the tier-2 signals unvalidated and says the score threshold is a placeholder pending measured outcomes. There is no backtest engine or complete historical signal dataset in the repository.

## 2. Repository and evidence inspected

The primary path is implemented across:

- `proto/mexc.py` — MEXC public REST adapter and minimum-notional conversion.
- `proto/bybit.py` — Bybit public linear-perpetual adapter and open-interest history.
- `proto/scan.py` — universe construction, scan requests, cross-venue OI wiring, and per-coin analysis.
- `proto/indicators.py` — pure indicator calculations.
- `proto/scorer.py` — component scores, vetoes, composite score, and direction.
- `proto/planner.py` — hypothetical entry/stop/target, sizing, leverage, and cost display.
- `proto/app.py` — cache lifecycle, scan scheduling, persistence, and plan generation.
- `proto/store.py`, `proto/collector.py`, `proto/outcomes.py`, `proto/report.py` — signal history and forward outcomes.
- `tests/`, `docs/GUI_MANUAL.md`, and the two design specifications under `docs/superpowers/specs/`.

Recent commit history was inspected. Recent changes concern GUI snapshots, planner warnings, outcomes, and logging; the repository history reviewed here does not establish a validated trading edge.

## 3. Actual signal pipeline

```text
MEXC ticker/detail snapshots (app refresh interval: up to 180 seconds)
  ├─ contract universe, last price, 24h quote volume, 24h move, funding, min notional
  └─ for each scan, per coin:
       MEXC 5m klines + MEXC depth + MEXC 1h and 4h klines
       Bybit shared-symbol lookup + Bybit open-interest change when available
         ↓
       indicators on returned completed candles (adapter filtering uses local clock)
         ↓
       vetoes: 24h quote volume, spread, 1h/24h move, history length
         ↓
       magnitude score from volume/price, book, OI/funding
       × earlyness factor + optional fixed 8 point MTF agreement bonus
         ↓
       magnitude-weighted composite lean
         ↓
       LONG if lean > +0.15; SHORT if lean < −0.15; otherwise NEUTRAL
         ↓
       optional plan from another MEXC 5m fetch, swing reference, and defaults
```

The flow is visible in `proto/scan.py:379–444`, `proto/scorer.py:241–305`, and `proto/app.py:97–139, 159–170`.

MEXC provides the price, candles, ticker fields, and book used for the card. Bybit is not used as a second price/candle confirmation source in this path. It supplies open-interest change for shared symbols. When OI is absent, unavailable, or fails, the component switches to a funding-only calculation. The Bybit OI percentage is paired with an approximate notional computed using MEXC’s ticker price (`proto/scan.py:242–266`); that notional is a cross-venue estimate.

## 4. Market-data integrity

### 4.1 Adapter parsing, timestamps, and ordering

The MEXC adapter unwraps the `{success, code, data}` envelope, expects columnar kline fields, checks required keys and equal column lengths, and rejects non-increasing raw timestamps (`proto/mexc.py:32–52, 87–142`). It treats MEXC kline timestamps as seconds.

The Bybit adapter checks the response envelope, expects kline rows newest-first, validates raw descending timestamp order, converts milliseconds to seconds, and sorts bars oldest-first (`proto/bybit.py:29–43, 112–174`). It configures the `linear` category, which is consistent with USDT-margined perpetuals on that venue. The application’s Bybit usage is OI only; it does not compare Bybit candle prices against MEXC prices.

Both adapters reject non-finite OHLCV values and candles whose open/close fall outside the low/high range (`proto/mexc.py:151–160`; `proto/bybit.py:175–184`). They reject duplicate/out-of-order raw timestamps. They do not validate full interval continuity, and candle closure relies on local wall-clock time.

### 4.2 Candle closure — F-01 fixed

Both adapters now remove the latest bar when `bar.ts + interval_seconds > time.time()` (`proto/mexc.py:141–150`; `proto/bybit.py:168–173`). This prevents scoring a forming candle under an accurate local clock. The implementation does not measure exchange-server time offset, so clock skew remains a limitation. The latest retained completed bar feeds indicators and the 1-hour movement calculation (`proto/scan.py:416–419`).

### 4.3 Cached ticker fields combined with fresh candles and book — F-02 mitigated

`App` now sets `UNIVERSE_TTL = 180` seconds (`proto/app.py:38`) and refreshes after expiry (`proto/app.py:76–90`). The scanner still reads ticker `lastPrice`, `amount24`, `riseFallRate`, `fundingRate`, and `maxFundingRate` from that snapshot while fetching klines and depth per scan. It now appends a `DATA ticker/candle skew` note if ticker price differs from latest completed candle close by more than 1% (`proto/scan.py:430–442`).

This reduces cache age and exposes large price discrepancies, but does not establish exact timestamp alignment. The 1% rule is a warning, not a veto; it compares price to candle close rather than book midpoint, and source timestamps are not persisted. Funding and volume remain from the ticker snapshot without independent freshness checks.

### 4.4 MEXC history window — F-04 fixed

`mexc.klines` now derives the request window from the interval duration, adds one spare interval for boundary coverage, removes the forming bar, and truncates the result to the requested count (`proto/mexc.py:94–104, 141–150`). This corrects the previous `limit×3600` behavior. Actual history can still be shorter when an instrument lacks sufficient history; the scan treats insufficient higher-timeframe history as unavailable.

### 4.5 Cross-exchange mapping and OI

`canon()` strips common quote/perpetual suffixes (`proto/scan.py:90–98`). The Bybit map is built from returned linear tickers and keyed by this canonical symbol (`proto/scan.py:212–239`). This is a simple mapping; no per-symbol contract metadata or underlying-asset equivalence check is made before requesting OI. Symbol collision or unusual listing conventions could map instruments that share a canonical base name but do not represent equivalent contracts.

Bybit’s `oi_state` uses the oldest and newest usable samples from the returned set and reports percentage change (`proto/bybit.py:175–212`). It does not verify the sample span or spacing, so fewer or irregular samples may represent a different elapsed interval from the intended roughly 24 hours. Transport failures are surfaced in Bybit health and degrade to funding-only; that fallback is explicit for the OI path.

MEXC supplies book skew and volume while Bybit supplies OI. Those are not independent confirmations by default: OI and price are mechanically related to the same market regime, and book imbalance is only a momentary snapshot. The repository contains no evidence that the cross-venue combination improves predictive performance.

### 4.6 Rate limits, retries, and errors

Both adapters retry transport/API/JSON errors up to three attempts using exponential delays (`proto/mexc.py:32–52`; `proto/bybit.py:29–51`). Per-coin MEXC primary fetch failures are recorded by `analyse_one` and excluded from returned scorecards (`proto/scan.py:393–446`). Bybit OI failures are counted in venue health and degrade to funding-only.

`tf_lean` now marks MEXC degraded when a request raises (`proto/scan.py:363–379`), and the caller appends an `MTF 1H/4H unavailable` note when a timeframe has no lean (`proto/scan.py:461–464`). A card still scores without that bonus, but missing evidence is distinguished from neutral evidence. The current workspace `gui/model.py` change renders `MTF` and `DATA` notes in GUI details; it is uncommitted and was preserved.

The scanner has a thread-safe 20-requests-per-second limiter around gated per-coin requests (`proto/scan.py:18–60, 357–360`). Adapter retries occur inside the gated call, so retry attempts are not separately admitted through that limiter. I did not make live requests to assess actual exchange rate-limit behavior.

## 5. Indicator and mathematics audit

No external indicator library is used. The following describes the actual formulas in `proto/indicators.py`.

| Calculation | Current implementation | Assessment |
|---|---|---|
| SMA (`:32–42`) | Rolling arithmetic mean, returns empty before `n` values. | Standard and correctly indexed for ordinary finite input. |
| EMA (`:45–54`) | Seeds with SMA of first `n`; recursive `k=2/(n+1)`. | Common convention; initialization differs from libraries using first observation or longer prehistory. Tests do not assert independent reference values. |
| EMA relationship (`:59–75`) | `(EMA_fast − EMA_slow)/(2×ATR14)`, clipped to ±1. | A normalized gap heuristic, not a standard crossover. Both EMAs end at the last candle, but their SMA seeds occur at different indices; for long arrays, initialization effects decay. |
| Donchian position (`:78–90`) | Maps last close into prior `n` bar high-low range from −1 to +1. | Correctly excludes current bar from reference extrema. Flat range returns zero. |
| MACD histogram (`:93–116`) | EMA12−EMA26, EMA9 signal; histogram divided by ATR14 and multiplied by 4, clipped. | The normalization is custom. The prior implementation paired EMA arrays from index 0 despite different SMA seed indices. Current code aligns their common tails (`:98–109`), with a new explicit hand-computation regression test. The defect is fixed. |
| RSI (`:114–132`) | SMA seed of first 14 gains/losses, then Wilder recurrence; returns 0–100. | A valid Wilder convention with consistent indexing. Flat series returns 50; gain-only series returns 100. |
| ATR (`:135–146`) | True range begins at bar 1; SMA seed of first `n` ranges, then Wilder smoothing. | Standard convention and indexing. It needs `n+1` bars. The live pipeline now rejects non-finite and internally inconsistent candle OHLCV in both adapters before indicator calculation. |
| ATR percent (`:149–155`) | ATR divided by latest close. | Consistent fraction units; no division when close ≤ 0. Used as a magnitude contributor, so volatile markets can score higher regardless of direction. |
| Stochastic (`:158–167`) | Calculates close position for each of the last `n` bars against one shared recent range, then averages the last `k_smooth` values. | A custom, non-standard calculation; it is not wired into scoring and is not a current signal-engine defect. |
| OBV slope (`:172–193`) | Last 20 close-change signs weight base volume; divide signed total by total volume. | A disclosed bounded signed-volume heuristic, not conventional cumulative OBV plus regression slope. This is a definition choice, not a confirmed math defect. |
| Volume expansion (`:196–224`) | Recent 3-bar mean base volume / trailing median; map ratio 1→0 and 4→1, clamp. | Direction-neutral custom anomaly metric. It depends on base-volume units and can be sensitive to contract/unit changes. |
| Book skew (`:229–242`) | Top 20 levels’ price×quantity sums; `(bid−ask)/(bid+ask)`. | Symmetric as tested. It is not a fill-impact or depth-through-stop estimate. Assumes best-first levels and meaningful quantity units. |
| Book thinness (`:244–254`) | `1 − smaller-side-notional/larger-side-notional`, clamped. | A side imbalance proxy. It calls imbalance “thinness” even when both sides are deep; absolute liquidity is not measured. |
| Earlyness (`:259–288`) | `1 − (0.65×proximity-to-either-extreme + 0.35×volume_expansion)`. | Custom strategy feature, not a direct estimator of move age. It penalizes price near either range extreme, potentially penalizing persistent trends as well as exhausted moves. No predictive validation is available. |
| Swing high/low (`:293–308`) | Most recent unique local extremum with `left=2`, `right=2`; only examines points confirmed by two later bars. | Uses no future array entries after the latest available bar. A pivot is only known after the two right-side bars; the live calculation respects that by searching only confirmed points. |

The original indicator tests checked symmetry, bounds and qualitative behavior. The follow-up adds a MACD hand-computation regression test. Adapter suites now exercise finite-value and full-OHLC validation. Coverage does not establish predictive validity or independently validate every custom feature; stochastic is not used by scoring.

## 6. Signal-generation logic

### 6.1 Vetoes and scoring

The veto thresholds in `proto/scorer.py:26–30, 216–238` are:

- 24h quote volume below $250,000 USDT.
- Spread above 3%.
- Absolute 1h movement at or above 12%.
- Absolute 24h movement at or above 35%.
- Fewer than 30 bars.

Vetoed cards remain scoreable when at least 30 bars exist, but cannot be actionable. The application logs flagged and shadow rows when logging is enabled (`proto/app.py:113–125`). This supports later analysis of filtered signals.

Scoring weights (`proto/scorer.py:19–24`) are 0.40 volume/price, 0.30 book, and 0.30 OI/funding. The score uses each component’s **magnitude**, not its directional lean. Earlyness multiplies the base by `0.55 + 0.45×earlyness` (`:274–279`), so even zero earlyness preserves 55% of the base. If all three timeframe leans have absolute value strictly greater than 0.15 and the same sign, 8 points are added, capped at 100 (`:280–289`).

Direction uses a different magnitude-weighted blend (`:291–300`). LONG requires lean strictly greater than +0.15; SHORT strictly less than −0.15; otherwise NEUTRAL. The score can therefore be high while direction is neutral, and a score of 24 is a logging threshold, not a calibrated probability.

The long and short rules are broadly symmetric in code. The fixed vetoes use absolute movement, and the tests assert mirrored scoring behavior. The underlying features and evidence are not guaranteed symmetric under all market microstructure or contract conditions.

### 6.2 Confidence and repeated signals

The UI describes score as signal quality; it is not a confidence percentage with empirical calibration. There is no state-transition requirement before the same direction is emitted on a later scan. The database logs each scan card, so repeated observations of the same setup may be highly correlated. Any analysis must cluster or otherwise account for repeated coin/time observations and should not treat them as independent trades.

No signal expiry or setup invalidation state is maintained. A card represents the latest scan; old DB rows remain historical. The close-time GUI snapshot is a display snapshot, not a market-data freshness guarantee.

## 7. Futures-specific plan audit

### 7.1 Levels and risk sizing

`plan_levels` sets a price band of ±0.1% around current price (`proto/planner.py:57–90`). For a long, stop is the supplied swing low, TP1 is price plus twice the price-to-stop distance, TP2 is five times that distance; shorts mirror those formulas. It validates ordering around the band. The app obtains a swing from another 5m fetch and falls back to the last candle low/high if no swing exists (`proto/app.py:159–170`).

The plan uses stop distance from band midpoint to derive leverage and position size. It caps notional by both risk budget and `stake×leverage`; displayed max loss is notional times stop fraction (`proto/planner.py:93–116, 147–162`). This calculation is internally consistent as a simplified notional model. It is not a MEXC liquidation model: maintenance tiers, liquidation fees, funding margin effects, contract-specific quantity units, and adverse stop execution are not represented.

`plan_levels` does not choose a valid stop; it expects the caller’s swing reference to be on the correct side and raises otherwise. The fallback to the current candle’s low/high is not a confirmed structural pivot.

### 7.2 Fees, spread, slippage, funding

The planner hard-codes `TAKER_FEE_PCT = 0.05` percent per side (`proto/planner.py:15–17`) and models two-sided taker fees. The GUI manual now calls the entry a hypothetical limit band with no fill model (`docs/GUI_MANUAL.md:221–224`). The formatter in `proto/planner.py:242` still labels it “inside spread”; that text overstates what the planner verifies. The band is not derived from a contemporaneous spread and no fill probability is modeled. The scorer’s spread comes from best bid/ask (`proto/scan.py:421–426`) but the planner does not consume it. The formatter wording remains a deferred F-08 follow-up; the app manual is clarified.

The planner has a direction-aware funding sign convention and estimates settlements from `hold_hours/8` (`proto/planner.py:164–174`). The app now passes the card funding rate (`proto/app.py:169–170`), fixing the previous zero-funding omission. The rate and cap remain sourced from the cached ticker row, and the settlement cycle is hard-coded to eight hours. Current official MEXC fees, contract parameters, funding settlement rules, and maintenance margin tiers were not verified in this audit; they must not be represented as verified exchange specifications.

No quantity rounding, tick-size rounding, minimum step, bid/ask depth cost, spread crossing, slippage, stop-market gap, or partial-fill model exists in the planner. The minimum-notional calculation uses `minVol × contractSize × lastPrice` (`proto/mexc.py:145–154`), but this was not validated against current official contract metadata semantics.

## 8. Look-ahead, repainting, and outcome timing

### Confirmed or possible issues

- **Confirmed — unfinished latest bar may be included.** Adapters do not remove it and scan formulas use the final element. The result may change as the candle forms.
- **No confirmed future-array access in swing detection.** Swing logic requires two bars to the right and only searches confirmed candidates that already have those bars.
- **No historical signal rewriting was found.** Signal rows are inserted with timestamp and component values. The data snapshot is incomplete, however: it does not persist all candles, each input’s observation timestamp, or code/config version alongside every signal.
- **Potentially misleading outcome alignment.** `resolve_pending` selects bars whose start timestamp is strictly greater than signal timestamp (`proto/outcomes.py:70`). This avoids using a candle whose bar start predates signal time. Returns are then measured from the signal’s ticker price to future bar closes (`:73–75`). If signal time occurs partway through a bar, the first subsequent bar may start nearly five minutes later; effective horizon length therefore varies with signal phase.
- **Outcome excursion metric is close-only.** `max_fav`/`max_adv` are extrema over signed close returns, not high/low intrabar excursions. A candle can cross a stop/target and close back without being counted as a hit.
- **No cost deductions.** Outcome returns omit fees, slippage, spread, funding, and fill assumptions.

The outcome collector stores 5m bars and deduplicates by timestamp (`proto/collector.py:20–34`). Its errors are swallowed per coin during full-universe collection (`:45–55`), so collection coverage needs independent monitoring before outcome completeness is assumed.

## 9. Predictive value assessment

The repository contains no backtest engine, no walk-forward parameter evaluation, and no performance dataset sufficient to make a predictive claim. `proto/outcomes.py` measures forward price movement from logged signals; it does not model realistic fills or net returns. The design specs explicitly state that tier-2 signals are unvalidated and thresholds remain placeholders.

Mathematical correctness, software correctness, predictive value, and net profitability are separate claims:

1. **Mathematical correctness:** the MACD alignment defect is fixed and pinned against a hand calculation. Other scored formulas follow documented conventions or are disclosed custom heuristics; stochastic is not wired into scoring.
2. **Software correctness:** adapter parsing, candle filtering, interval-specific history, invalid OHLC rejection, and MTF failure observability are improved or fixed. Exact source timestamp coverage remains incomplete.
3. **Predictive value:** not established by this repository.
4. **Net profitability:** not established; costs and execution assumptions are incomplete.

### Smallest practical evaluation

1. Persist full score inputs at decision time: ticker/candle/book/OI timestamps, latest candle closure status, component values, signal price, versioned configuration, and code revision.
2. Build chronological MEXC histories with symbol listing/delisting and missing-data coverage recorded. Do not backfill decisions with data that was unavailable at the original timestamp.
3. Evaluate close-confirmed signals using an entry after the decision, with realistic MEXC spread, fees, slippage, funding settlement, quantity rounding, and stop/target execution assumptions.
4. Keep training/threshold selection, validation, and out-of-sample periods separate. Freeze the rules before the out-of-sample period.
5. Report LONG and SHORT separately and stratify by coin, timeframe, regime, and score band when samples allow. Report trade count, win rate, expectancy, profit factor, average win/loss, maximum drawdown, and uncertainty intervals.
6. Compare with no-trade and simple baseline strategies; test threshold and cost sensitivity. Treat repeated scans of the same setup as correlated observations, not independent trades.
7. Retain delisted/unavailable symbols and unresolved outcomes in the coverage accounting; do not silently exclude them.

## 10. Altcoin-specific robustness

The code filters on 24h quote volume, spread, and large recent movement. Book skew uses only a fixed top-20-level sum, so it does not establish executable depth for a particular order size. There is no liquidity deterioration monitoring, market-impact estimate, listing-age minimum, or BTC/ETH regime filter. Funding is used as a crowding heuristic, but no extreme-funding veto is implemented. Newly listed contracts may satisfy the 30-bar gate while having limited indicator warm-up or unstable volume baselines.

The 24h and 1h late-move vetoes can prevent some chase entries but cannot reliably identify pump-and-dump behavior or failed breakouts. These are strategy hypotheses to evaluate, not defects that can be fixed by adding more indicators without evidence.

## 11. Deterministic walkthroughs

The following examples were computed during the audit from synthetic deterministic fixtures, not live market observations. Each uses 120 constructed bars, $1,000,000 quote volume, 0.1% spread, zero funding and no OI, and no 1h/4h leans (so no MTF bonus). The generated rise/fall paths remain below the code’s 1h/24h late-move veto thresholds.

| Fixture | VOL magnitude / lean | BOOK magnitude / lean | OI magnitude / lean | Earlyness | Score | Composite lean | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| Rising prices with bid-heavy book | 0.579 / +0.905 | 0.539 / +0.599 | 0 / 0 | 0.143 | 24.2 | +0.779 | LONG |
| Falling mirror with ask-heavy book | 0.581 / −0.907 | 0.541 / −0.601 | 0 / 0 | 0.141 | 24.2 | −0.781 | SHORT |
| Flat oscillation with balanced book | 0.324 / +0.086 | 0 / 0 | 0 / 0 | 0.373 | 9.3 | +0.086 | NEUTRAL |

These values demonstrate code behavior only. They do not demonstrate signal efficacy. The scorer’s actual formulas are at `proto/scorer.py:122–155, 241–305`.

## 12. Findings register

Severity definitions follow the task brief: Critical invalidates results or risks severe unintended behavior; High is a major data, calculation, or decision defect; Medium is meaningful robustness/reliability risk; Low is a smaller inconsistency or maintainability issue.

| ID | Severity / status | File and lines | Evidence | Practical consequence | Follow-up / recommendation | Confidence |
|---|---|---|---|---|---|---|
| F-01 | **Fixed** | `proto/mexc.py:141–150`; `proto/bybit.py:168–173` | Both adapters remove the latest bar if its interval has not ended according to local wall-clock time. | Prevents scoring a forming candle when the local clock is accurate. | Verified in current code and adapter suites. Remaining caveat: no exchange-server time offset check. | High |
| F-02 | **Mitigated** | `proto/app.py:38, 76–90`; `proto/scan.py:430–442` | Ticker TTL is now 180 seconds; >1% ticker/candle price skew adds a `DATA` note. | Large price divergence is visible, but stale funding/volume or sub-1% skew remains possible; this is not a gate. | Verified in current code. Persist input timestamps and decide after the measurement window whether policy should change. | High |
| F-03 | **Fixed** | `proto/app.py:159–170` | App passes `funding_rate=card.funding_rate` into planner. | Normal plan cost now includes funding under existing planner assumptions. | Verified in current code; planner funding sign tests pass. | High |
| F-04 | **Fixed** | `proto/mexc.py:94–104, 141–150` | Request window uses interval seconds; bars are truncated to requested count after forming-bar removal. | Corrects timeframe-dependent over/under-fetch when enough venue history exists. | Verified in current code and adapter suite. | High |
| F-05 | **Fixed** | `proto/indicators.py:93–116` | EMA arrays align at their common tail; an explicit hand-computation regression test was added. | Removes time-index mispairing and pins the chosen calculation. | Verified in current code and indicator suite. | High |
| F-06 | **Fixed** | `proto/scan.py:363–379, 461–464`; `gui/model.py` working tree | MTF exceptions mark MEXC degraded and add an explicit timeframe-unavailable note. Current workspace GUI model renders `MTF`/`DATA` notes. | Missing higher-timeframe evidence is visible rather than silently conflated with neutral evidence. | Verified in scanner code and MTF suite. GUI rendering change is uncommitted in this checkout and was preserved. | High |
| F-07 | **Deferred** | `proto/outcomes.py:70–75`; `proto/collector.py:45–55` | Outcomes use future closes, close-only excursions, and no costs; collector suppresses per-coin exceptions. | Outcome reports are not net P&L and may miss intrabar stop/target events or coverage gaps. | Deliberately frozen during the measurement week. Revisit afterward with versioned outcome semantics and coverage reporting. | High |
| F-08 | **Deferred / wording clarified** | `proto/planner.py:57–90`; `docs/GUI_MANUAL.md:221–224` | Fixed ±0.1% band has no fill model. Current manual labels it hypothetical and says no fill model. | Do not treat the band as an executable quote or assumed fill. | Deliberately frozen during the measurement week; version any later execution model. | High |
| F-09 | **Fixed** | `proto/mexc.py:151–160`; `proto/bybit.py:175–184` | Both adapters reject non-finite OHLCV and inconsistent candle body/range. | Invalid bars are rejected before scoring. | Verified in current code and adapter suites. | High |
| F-10 | **Open verification item** | `proto/planner.py:15–27`; `proto/scan.py:427–446` | Fee/leverage settings are code constants; no current official MEXC specification was verified. Funding is sourced from cached ticker data. | A syntactically correct plan may misstate costs or constraints. | Verify official fee, contract and margin rules before using these estimates for execution decisions. | Medium |

## 13. Independent validation plan

The follow-up added or extended tests for forming-bar removal, interval-aware history/count behavior, finite and internally consistent OHLC validation, MTF failure/warning behavior, funding propagation, and MACD against a hand calculation. The offline aggregate passes. Remaining validation work, without changing the frozen outcome semantics during the measurement week, includes:

- EMA, RSI, MACD and ATR values against hand calculations with the chosen seed convention documented.
- Insufficient history at each warm-up boundary, including exactly 14/15, 26/35/37 bars, and longer stable warm-up.
- Same data returning same score, with deterministic handling of invalid values.
- Missing/stale candle coverage and local-versus-exchange clock offset.
- Zero-volume and extreme-volatility behavior across every scored feature.
- Long/short symmetry and neutral threshold boundaries at lean ±0.15.
- MTF missing, neutral, aligned, and opposed evidence.
- Fee/funding sign (already covered in planner tests), then settlement assumptions, spread/slippage and precision boundaries.
- Outcome windows where intrabar high/low crosses stops/targets and close does not, after the measurement window and with versioned outcome semantics.

Do not add a third-party indicator package as a substitute for defining conventions. Numeric reference fixtures should make the selected formula explicit.

## 14. Test execution results

The project tests are self-checking scripts. The new `tests/run_all.py` runs the offline scripts in subprocesses and intentionally excludes the live-network `tests/test_app.py`. Running `python3 tests/run_all.py` on the current checkout produced:

- **All 16 offline suites passed**, including expanded adapter, Bybit, MACD, MTF, and planner checks.
- The runner excludes `tests/test_app.py` because its live scan requires MEXC. In the initial audit run, the script reached the network request and failed on DNS resolution (`Temporary failure in name resolution`); it has not been rerun by the offline aggregator.
- The new runner addresses the earlier discovery mismatch by executing scripts as subprocesses rather than importing self-checking files that invoke `sys.exit`.

No live API behavior, current fee schedules, or exchange contract specifications were verified during this audit.

## 15. Prioritised remediation plan

### 1. Correctness blockers

- Keep the completed-candle policy and add exchange-time offset validation; current filtering relies on local clock.
- Persist source timestamps; current 180-second ticker TTL and >1% skew note mitigate but do not eliminate stale/misaligned inputs.
- Keep the MACD common-index alignment and hand-computation regression test.
- Keep funding passthrough; verify rate timestamp and settlement assumptions before treating plan costs as execution estimates.

### 2. Data integrity

- Keep interval-aware MEXC range selection and verify returned coverage when instruments have limited history.
- Keep forming-bar filtering and finite/full-OHLC validation covered by tests; add source freshness and timestamp checks.
- Keep explicit MTF missing-data notes and venue degradation behavior covered by tests.
- Check symbol equivalence and sample span for cross-exchange OI mapping.

### 3. Signal-logic improvements

- Keep the current score labelled as an uncalibrated ranking until outcomes support calibration.
- Evaluate fixed weights, thresholds, earlyness, and funding/OI interpretations out of sample before changing them.
- Deduplicate or cluster repeated scans when assessing signal performance.

### 4. Risk and execution realism

- Verify current official MEXC fees, contract sizing, tick/lot rules, funding settlement, and maintenance-margin tiers.
- Model spread, slippage, fill probability, stop-market gaps, and position rounding.
- Continue to label the fixed band as hypothetical; keep fill assumptions out of outcome results until separately versioned.

### 5. Backtesting and monitoring

- Persist complete timestamped inputs, decision outputs, code/config version and data-quality flags.
- Make outcomes cost-aware and specify intrabar stop/target ordering.
- Track collection coverage and unresolved outcomes by coin/timeframe.
- Run chronological train/validation/out-of-sample evaluation with long/short and regime breakdowns, baselines, sample counts, and cost sensitivity.

## 16. Final verdict

1. **Are the indicator formulas implemented correctly?** The MACD alignment defect is fixed and pinned against a hand calculation. Other scored formulas follow documented conventions or are disclosed custom heuristics. OBV/earlyness are strategy choices; stochastic is not used in scoring. Predictive validity remains unproven.
2. **Is the final LONG/SHORT decision logically consistent?** It is internally direction-symmetric: a separate composite lean sets direction, with a neutral band. Score measures magnitude/heuristics, not confidence. Contradictory evidence can still produce a high magnitude score.
3. **Is there look-ahead bias or repainting?** No future-array use was confirmed in swing detection. Forming-bar use is fixed by adapter filtering based on local clock; exchange-time offset is not checked.
4. **Is Bybit/MEXC data handled correctly?** Parsing, candle filtering, MEXC timeframe history, input validation, and MTF failure visibility are improved/fixed. Snapshot alignment remains partial: 180-second ticker TTL and >1% price-skew warning do not persist exact source timestamps or guarantee fresh funding/volume. Bybit remains an OI source, not price confirmation.
5. **Are futures-specific costs and mechanics represented adequately?** No. Funding passthrough is fixed and the band is documented as hypothetical, but exchange specifications remain unverified and fill probability, contract precision, slippage, liquidation tiers, and stop execution are not modeled adequately.
6. **Has predictive value been demonstrated?** No. There is no repository evidence of a proper out-of-sample backtest or net performance after costs.
7. **What must be fixed before paper trading?** F-01, F-03, F-04, F-05, F-06, and F-09 are fixed in current code; F-02 is mitigated. Meaningful net paper evaluation still needs source timestamping and realistic outcome/execution accounting. F-07/F-08 semantics are intentionally frozen through the measurement week; do not interpret plans as executed fills.
8. **What evidence is needed before considering live trading?** Verified current official exchange rules and costs; a reproducible out-of-sample evaluation with realistic execution assumptions; sufficient sample sizes across coins, long/short directions and regimes; and paper execution monitoring that confirms modeled fills and risk. This repository alone does not provide that evidence.
