# Alt Radar v1 — Design Spec

**Date:** 2026-10-07
**Status:** Design approved by user, pending spec review
**Path:** Architectural (new project, multi-subsystem)

---

## 1. Purpose

A local Linux application that scans crypto USDT perpetual futures across
Bybit, OKX, and MEXC for coins showing **early** evidence of buyer or seller
activity — before that activity is obvious in price. For qualifying coins it
emits a complete trade plan — direction, entry, stop loss, take profit,
leverage, position size, and expected costs — for the operator to review and
execute manually.

**It never places orders and never holds API keys.**

### 1.1 Primary Objective

**Surface early, directional signals on alt perpetuals that carry profit
potential.** The objective is timely, asymmetric detection — noticing abnormal
buyer/seller activity while it is still actionable, and acting on it correctly
in either direction.

**Direction-symmetry is a first-class requirement.** Short setups are as
legitimate as long setups. The scanner must not be structurally biased toward
either. This is a correctness property of the scoring design (§3.3), not a
feature.

### 1.2 What Is Explicitly Not an Objective

- **No hard-coded return target.** There is no 500% figure, no target multiple,
  no "big mover" filter, and no code path tuned to find a specific outcome.
  Move magnitude is an *emergent property* of surfacing activity early — never
  an input to scoring.
- **No ranking by expected return.** Coins rank by *signal quality* (evidence
  of early activity), not by predicted upside.

The scanner finds things worth trading. Whether a given signal becomes +5% or
+500% is determined by the market afterwards, not by the software. A large
move is a lucky outcome of a correct early read; hard-coding the magnitude
would invert evidence and turn a detection tool into a slot machine.

### Non-goals (v1)

- Order placement or API key custody
- News / X / Telegram scraping (deferred; must beat baseline to justify itself)
- Telegram or mobile notifications (deferred, free when added)
- Automatic leverage or position management

---

## 2. Verified Environment Constraints

All figures below were measured on the target machine on 2026-10-07, not assumed.

### 2.1 Network

| Measure | Value | Consequence |
|---|---|---|
| TLS handshake floor | ~150 ms | Every HTTPS call pays this |
| Full REST round trip | 750–940 ms | Polling is slow |
| Routing | US (Ashburn) edge | ~200 ms each way from operator |
| Gate.io REST | 1880 ms | Not viable; excluded |

**Strategic consequence:** sub-second scalping is impossible from this host.
The scanner optimizes for **coverage and precision over a 30 min–4 h horizon**,
matching the operator's stated 5m/15m/1H/4H/1D decision timeframes. Latency is
explicitly out of scope.

### 2.2 Venue Capabilities

| Venue | USDT perps | Alts (non-major) | WebSocket | REST latency |
|---|---|---|---|---|
| Bybit | 788 | 783 | Yes — live trades @ ~490 ms | 750 ms |
| OKX | 485 | 480 | Yes — live trades @ ~440 ms | 919 ms |
| MEXC | 1,082 | 1,077 | Endpoint not yet located | 839 ms |
| **Union (deduped)** | **1,351** | — | — | — |

- 296 coins are listed on all three venues → cross-venue confirmation available.
- 145 MEXC-only real alts (incl. long-tail degen names) → widest discovery surface.
- 383 synthetic equity/commodity tickers (AAPLSTOCK, XAU, SPX, USOIL…) must be
  excluded from the universe.

### 2.3 Historical Candle Depth

**Verified limits.** MEXC caps requests at 2,000 bars per call and does not page
deeper. Bybit and OKX accept `start`/`end` parameters, but **paging was not
verified to work** — a probe using millisecond parameters returned a single row
per call. Deep history on those venues is therefore **unconfirmed** and must be
verified during implementation before anything depends on it.

| Timeframe | MEXC | Bybit | OKX |
|---|---|---|---|
| 5m | **6.9 days (2,000 bar cap)** | unverified | unverified |
| 15m | 20.8 days | unverified | unverified |
| 1H | 83 days | unverified | unverified |
| 4H | 333 days | unverified | unverified |
| 1D | 1,999 days | unverified | unverified |

**Correction:** an earlier draft of this spec claimed Bybit/OKX "page
arbitrarily deep". Measurement did not support it. The claim is withdrawn.

**Consequence:** MEXC supplies all confirmed historical depth for v1. Bybit and
OKX contribute live market data (tickers, books, OI, funding) and WebSocket
push, and may contribute history once paging is confirmed. This reinforces the
multi-venue choice on *live data* grounds, not historical ones.

### 2.4 Capital Constraint

- Starting stake: **$0.10**, compounded as profit accumulates.
- Min tradeable notional on MEXC ranges **$0.0004 to $4.96** (median $0.45);
  1,101 of 1,183 contracts are ≤ $5. But the *majors are not*:
  BTC $8.32, SOL $11.59, ETH $25.68.
- BTC/ETH/SOL are therefore **permanently untradeable** at small stakes.

**Consequence:** the universe must be size-aware. Each coin's min notional is
fetched once and cached; coins above the current stake are surfaced as
WATCH-ONLY rather than dropped, and become actionable as stake compounds.

### 2.5 Leverage and Funding

- Operator trades **20x default, 50x maximum**.
- Funding caps per 8h settle: Bybit ±0.333%, OKX ±0.375%, MEXC ±0.18%.
- At 50x, one MEXC funding settle at cap costs **9% of margin**.

**Consequence:** carry cost is a first-class input to the trade plan, not an
afterthought. See §5.4.

---

## 3. Architecture

```
feeds ──► canonicalizer ──► screener ──► scorer ──► planner ──► TUI
 │                                                          └─► SQLite
 └── Bybit WS / OKX WS / MEXC REST (adapters, uniform interface)
```

Each unit has one purpose, a documented interface, and is independently testable.
A consumer never needs to read another unit's internals.

### 3.1 Feed Layer

One adapter per venue behind a uniform interface:

- `symbols()` → canonical coin list
- `ticker(symbol)` → price, 24h change, 24h volume, funding
- `candles(symbol, timeframe, limit)` → OHLCV
- `order_book(symbol, depth)` → `[price, qty, order_count]` rows
- `open_interest(symbol)` → current + history where available

WebSocket where available (Bybit, OKX); REST polling for MEXC. Reconnect with
exponential backoff and subscription replay. A degraded venue is downgraded to
REST rather than dropped.

**Canonicalization is required and was validated during design:**
`BTCUSDT` / `BTC-USDT-SWAP` / `BTC_USDT` → `BTC`; MEXC multiplier prefixes
`1000BONK` → `BONK`; synthetic tickers filtered out.

### 3.2 Screener

Cheap disqualifying filters first, before any expensive computation:

- 24h quote volume above floor
- Non-empty order book, spread below threshold
- Not a synthetic equity/commodity contract
- Min notional ≤ current stake (else WATCH-ONLY)
- **Not already vertical** — a coin that has already run substantially is no
  longer an early signal, and may be an untradeable chase. This veto rejects
  *late* signals; it does not reward move size.

**Denominators.** The universe figure (1,082) counts USDT-quoted MEXC perps.
The min-notional figure (1,101 of 1,183) was measured across *all* MEXC
contracts including non-USDT quotes, which is why its denominator differs. Both
are correct within their own scope; the screener filters on USDT perps only.

### 3.3 Scorer

Scores **signal quality** — the strength of evidence that unusual buyer/seller
activity is present *and early*. It never scores predicted return.

Each of three signals produces two values: a **magnitude** (how strong is the
abnormality) and a **directional lean** (−1 bearish … +1 bullish). Direction is
derived from evidence, then the components are combined.

| Signal | Weight | Magnitude components | Directional lean from |
|---|---|---|---|
| Volume / price expansion | 40% | Volume vs own trailing median, price position in range, breakout confirmation, multi-timeframe alignment | Price and volume expansion direction; which side the volume supports |
| Order-book imbalance | 30% | Depth magnitude, thin asks/bids, walls | Bid vs ask notional skew (bid-heavy → bullish, ask-heavy → bearish) |
| OI / funding divergence | 30% | OI change magnitude, funding extremeness | OI+price agreement (new longs vs new shorts), crowding side, funding sign |

**Symmetry requirement.** Every directional input must be evaluated so that
mirrored conditions produce mirrored scores. A configuration where bid-heavy
books and ask-heavy books both score high bullish is a bug, not a tuning
choice. Test §9 asserts this with mirrored fixtures.

**Indicator-to-role mapping.** Directional lean is taken only from signals that
actually carry direction:

| Indicator | Role |
|---|---|
| EMA relationship, Donchian position, RSI, Stochastic | directional lean (level/position) |
| OBV slope, book skew, price/breakout direction | directional lean (flow/imbalance) |
| **MACD histogram** | **magnitude only — acceleration, not direction** |
| ATR, Bollinger width, volume expansion, book thinness | magnitude only (unsigned) |
| VWAP position | directional lean (relative to fair value) |

**Correction to an earlier draft:** the MACD histogram was listed as a
directional input. Measurement contradicts this. The histogram measures the
*second derivative* — acceleration. On a smooth exponential path it is
near-zero and sign-arbitrary (measured: −0.224 rising vs −0.221 falling, both
negative), while on a real curved market it behaves correctly (measured: +0.597
rising vs −0.782 falling on mirrored choppy series). Using it for direction
would inject noise into the lean. It therefore contributes to magnitude only.

**Early-ness is a first-class input.** A setup that has already moved
substantially has, by definition, stopped being early. So each signal
contributes an *early-ness* factor: how far price has moved relative to its own
recent range and relative to its own volume baseline. A coin that already ran
scores lower on quality even if its raw abnormality is high. This is what
distinguishes detection from chasing.

**Independence of magnitude.** None of these components reference a target
return, a percentage-of-price move threshold as an *outcome*, or a desired
multiple. The screener's veto on vertical moves (§3.2) exists to avoid
untradeable entries, not to select for big moves — it rejects late signals, it
does not reward size.

**Volume basis.** Expansion and OBV use **base** volume, not quote volume.
Quote volume embeds price, so on a falling series it shrinks purely because
price fell, which would suppress downside expansion and introduce a silent
bullish bias into a signal required to be unsigned. Quote volume is used only
for cross-sectional liquidity ranking. This was found by the symmetry tests and
is guarded by a regression test.

Weights sum to 100. Score = weighted magnitude composite, then **hard vetoes**
applied (§3.2). Every contribution is retained for display so the operator can
see *why* a coin flagged and disagree. Weights are configuration, not code
constants, and are revisable against outcome-log data (§6).

### 3.4 Indicator Set

**Well-documented technical indicators** (implemented from standard definitions):

RSI, MACD, Bollinger Bands, ATR, EMA/SMA crossovers, Donchian Channels, VWAP,
OBV, volume profile, Stochastic.

**Academic factors with published evidence:**

| Factor | Rationale |
|---|---|
| Time-series momentum | Documented cross-asset effect (Jegadeesh–Titman) |
| Cross-sectional momentum | Rank coins against the peer group, not absolute return |
| Cross-sectional reversal | Short-horizon overreaction; opposes momentum at the 5m end |
| Carry (funding) | Systematic premium; extreme funding marks crowding |
| Volatility premium | ATR regime as risk qualifier |

**Selection policy — deliberate and binding.** There is no credible published
table of "community success rates" for trading formulas; such claims are
typically curve-fit and never net of fees. v1 therefore uses only indicators with
standard definitions or academic support, and **measures all of them on this
data**. SQLite records every signal and its outcome, so after a few hundred
trades the operator has empirically grounded per-indicator success rates — a
more trustworthy basis than any claimed table. Any future formula must beat the
measured baseline.

### 3.5 Timeframes

Decision timeframes: **5m, 15m, 1H, 4H, 1D**.

Indicators are computed per timeframe; alignment across them is a scored input.
Higher timeframes (4H, 1D) carry swing bias and risk qualification; 5m serves
entry timing and noise filtering. A counter-daily-trend setup must be labelled as
elevated risk in the output.

---

## 4. Trade Plan Output

For each coin above threshold. **The plan mirrors the detected direction; it
never defaults to LONG.**

- **Direction** — LONG or SHORT, from the scorer's directional lean (§3.3),
  with the evidence that drove it stated in words
- **Entry** — limit band inside the spread, not market
- **Stop loss** — beyond structure in the *opposing* direction (below a swing
  low for LONG, above a swing high for SHORT), never a fixed %
- **TP1 / TP2** — at 2R and 5R **along the trade direction**, with partial-close
  and trailing guidance; for SHORTs, targets sit below entry
- **Leverage** — computed from stake and stop distance (§4 leverage safety rule),
  bounded by the operator's 20x–50x band
- **Position size** — derived from current stake and max-loss-per-trade
- **Costs** — taker fees both sides + estimated funding drag to target; for
  SHORTs, note when funding pays the position rather than costing it
- **Break-even move %** — the price change needed to cover costs, signed by
  direction
- **R:R** and **max loss** in stake currency
- **Warnings** — counter-trend alignment, min-notional block, insufficient
  stake for the stop distance, wide spread, conflicting directional evidence
  (e.g. book bid-heavy while OI shows new shorts — state which signal leads and
  why, rather than silently averaging them away)

**Short-specific considerations.** A SHORT on a perp carries funding and
borrow-side risks that a LONG does not: persistent positive funding costs the
short, and rising OI with falling price indicates shorts still being added.
The planner surfaces the funding sign for the proposed direction so a
structurally sound short is not entered into a persistent funding bleed.

**Leverage safety rule.** Leverage is **computed, never defaulted**, and then
clamped to the operator's stated band of 20x–50x. At $0.10 stake a 6% stop at
20x would liquidate before the stop fills, so the planner derives max safe
leverage from stake, stop distance, and a maintenance-margin assumption. Where
the computed value falls below the band, the planner reports the safe value and
flags the trade as under-scaled rather than silently inflating it to 20x. This
is a correctness requirement, not a preference.

---

## 5. Risk and Economics

### 5.1 No order placement

The application has no order endpoints, no key storage, and no signing code.

### 5.2 Compounding

- +20% stake growth → stake increases 20%
- −30% drawdown from peak → halve stake and pause 5 trades

Tracker displayed persistently in the TUI: current stake, win rate, net P&L,
trade count.

### 5.3 Size-awareness

Min notional per coin cached and compared against live stake. Coins above stake
are WATCH-ONLY, not hidden.

### 5.4 Carry-cost modelling

Funding caps and current rates enter the plan, so a multi-settle hold shows its
true cost. At 50x a single MEXC cap settle costs 9% of margin — the planner must
surface this rather than presenting a naive target.

---

## 6. Persistence

### 6.1 Signal and Outcome Logging

SQLite:

- `signal_log` — timestamp, coin, venue, score, per-signal contributions, plan
- `outcome_log` — forward returns at 1h/4h/24h/7d for every signal

`outcome_log` is what makes the system measurable: it allows scoring each
indicator against realised results rather than assumption, and is the
prerequisite for any future claim about which formulas work.

**What the log does and does not buy.** It measures the operator's *real* hit
rate on *their* coins at *their* costs, and it calibrates thresholds against
outcomes rather than intuition. It does not retroactively validate Tier 2, and
it cannot reveal coins that were never scanned.

### 6.1a Shadow Logging — the denominator

A log containing only flagged signals answers *"were my signals good?"* It
cannot answer *"what did I miss?"* Because the collector records only what it
records, coins removed by a veto are unrecoverable — a vetoed coin that later
pumped leaves no trace.

**Requirement.** Every candidate that clears the cheap filters is logged,
including:
- coins below the display threshold
- coins vetoed by the late/vertical filter (with the veto reason recorded)
- coins whose directional lean was ambiguous

Flagged and shadow rows are distinguishable in the log, so hit rate is computed
both ways. Storage cost is negligible (measured in §6.3). Without this, the log
yields only a numerator and cannot support any recall or rejection-rate
analysis.

### 6.2 Two-Tier Evidence Structure

Measured: **0 of 40** sampled MEXC-only alts have enough history for any real
backtest (5m/90d, 15m/180d, 1H/1y all require 8,760–25,920 bars; none has
them). The cause is structural — those coins are absent from Bybit, OKX, Binance
perp, Binance spot, and MEXC spot, so **no free third-party history exists for
them.**

| Tier | Population | Indicator analysis | Validation status |
|---|---|---|---|
| **Tier 1** | ~1,205 coins on ≥2 venues | Full, multi-timeframe | Feasible once paging is confirmed (§2.3) |
| **Tier 2** | 146 MEXC-only degen alts | Full, from ~7 days of 5m bars | **Impossible on free data** |

**Binding requirement:** Tier 2 signals must be labelled UNVALIDATED in the TUI.
Tier 1's measured outcome record is what calibrates Tier 2. The two tiers must
never be presented as equally evidenced.

### 6.3 Historical Collector

A continuous collector accumulates 5m bars from now forward, creating the Tier 2
history that free APIs cannot supply retroactively.

**What local storage buys the operator**, in descending order of realised value:

1. **Calibrated judgment.** After a few hundred logged trades, the operator
   knows *which of their own signals earned alerts* — e.g. book imbalance paid
   out and OI divergence did not; 5m entries got chopped while 1H entries did
   not; funding bled shorts on this venue. This is judgement derived from their
   own record, and it is the primary justification for storing data locally.
2. **Threshold calibration** — score cutoffs set from measured hit rates rather
   than guesswork.
3. **Replay and regression** — reproduce any past scan to debug or re-tune.
4. **Deferred Tier 2 validation** (§6.2) — real, but arrives in months, not
   days.

**What it does not buy:** knowledge of coins never scanned or vetoed (mitigated
by §6.1a), and any retrospective validation. Both limits are structural, not
implementation gaps.

**Measured storage cost** (real MEXC payloads, ~95 B/bar JSON, 4.4× zlib
compression, all timeframes derived from 5m):

| Scope | MB/day | To 5 GB | Notes |
|---|---|---|---|
| 146 degen alts, CSV+zlib | 0.6 | 22.5 yr | effectively unbounded |
| 1,351 all coins, CSV+zlib | 5.6 | 2.4 yr | comfortably within 56.8 GB free |

**The 5 GB threshold is not a meaningful milestone** — 5m-only collection does
not approach it for years. The milestones that matter are far smaller:

| Target | Size | Reached |
|---|---|---|
| Degen 30 days | 18 MB | ~30 days from start |
| **Degen 90 days** | **55 MB** | ~90 days — first point where cross-sectional validation is credible |
| Degen 180 days | 110 MB | ~180 days |
| All coins, 90 days | 507 MB | ~90 days |

Collect 5m only: 15m/1H/4H/1D are exact aggregations of consecutive 5m bars, so
every timeframe in §3.5 costs nothing extra. Store both `vol` (base) and
`amount` (quote) — cross-sectional comparison across coins at wildly different
prices requires quote volume.

---

## 7. TUI

Local terminal UI, stdlib only:

- Live ranked table, updates in place
- Columns: rank, coin, **direction arrow (▲/▼)**, price, 24h %, volume, funding,
  OI trend, spread, VOL/BOOK/OI subscores, total score
- Coins are not sorted into separate long/short lists — a single ranked table
  with an explicit direction column, so both directions compete on signal
  quality alone
- **Vetoed-this-scan section** — rejections shown with reasons, so filtering is
  inspectable rather than invisible
- Detail view on Enter: full indicator breakdown with weights and contributions,
  directional evidence from each signal, trade plan, warnings
- Keys: `↑↓` select, `f` filter, `s` sort, `d` toggle long-only/short-only/both,
  `p` pause, `q` quit
- Header shows feed health, coin count, scan cadence, stake, compounding state
- Tier 2 rows carry an explicit UNVALIDATED marker (§6.2)

---

## 8. Error Handling

- Venue unreachable → degrade to REST; if REST also fails, mark venue degraded
  and exclude from scoring rather than scoring on stale data
- WebSocket drop → exponential backoff, subscription replay, gap detection
- Malformed payloads → schema validation at the adapter boundary; reject, log,
  never score on unvalidated data *(a design-phase bug produced timestamps in
  year 58,732 from an unvalidated response — validation is mandatory)*
- Unit/timeframe unit mismatches between venues → normalize at adapter boundary
- Partial results → render with available data and label the gap explicitly

---

## 9. Testing

- **Adapter tests** — recorded fixtures per venue; symbol canonicalization,
  unit normalization, schema rejection
- **Indicator tests** — each indicator against hand-computed reference values
- **Scorer tests** — known setups produce expected score ranges; vetoes fire
- **Symmetry tests** — mirrored fixtures (bid-heavy vs ask-heavy books, OI+price
  up vs OI+price down) must produce mirrored directional leans and equal
  magnitude scores. Asymmetric results fail the build.
- **Early-ness tests** — an identical abnormality occurring later in the move
  scores lower on quality than the same abnormality at onset
- **No-magnitude-leakage test** — assert no scoring constant encodes a target
  return or move multiple; guard against regressions that reintroduce it
- **Planner tests** — leverage never exceeds the computed safe bound; size never
  exceeds stake; costs always reported; LONG and SHORT plans mirror correctly
  (stops and targets on opposite sides of entry)
- **Integration** — live smoke test against all three venues, read-only
- **Regression** — signal/outcome logs replayed to confirm scoring stability

---

## 10. Explicit Non-Claims

Stated plainly so the design is not read as promising more than it delivers:

1. **The scanner does not predict price.** It surfaces coins with unusual,
   measurable buyer/seller activity and structures a trade plan.
2. **No move magnitude is targeted or promised.** The objective (§1.1) is
   early, directional signal detection. Whether any signal becomes +5% or +500%
   is the market's outcome, not the software's target. Nothing in the scoring
   path encodes a desired return (§1.2, tested in §9).
3. **No backtest has been run.** Validation is §9 and the outcome log. Any
   indicator's usefulness on these venues, at these costs, is to be measured,
   not assumed.
4. **Latency precludes scalping** from this host (§2.1).
5. **Tier 2 coins cannot be validated with free data, ever.** 146 MEXC-only
   alts have no deep history on any free source, so their signals are
   unbacktested hypotheses. Only the operator's own accumulating log can test
   them, and that takes months.
6. **Signal quality ≠ profitability.** The score measures evidence of early
   abnormal activity. It is not a probability of profit. Until the outcome log
   demonstrates otherwise, no score threshold has any proven edge behind it.

---

## 11. Deferred Work

- News / X / Telegram ingestion — must demonstrate improvement over the v1
  baseline before inclusion
- Telegram alerting — free; viable once the local app is stable
- MEXC WebSocket — endpoint not yet located; v1 uses REST
- Auto-execution — only after sustained logged performance

---

## 12. Open Questions

1. **MEXC WebSocket endpoint** — path unknown; REST fallback in place.
2. **Threshold calibration** — initial score cutoffs are placeholders pending
   outcome-log data.
3. **Volatility floor** — whether to impose an absolute ATR floor for the
   degen tier, given the operator's stated risk appetite.
4. **Bybit/OKX paging** — unverified (§2.3). If confirmed, Tier 1 backtesting
   opens up materially and should be the first implementation task to probe.
5. **Collector start date** — the 90-day Tier 2 validation clock starts when the
   collector first runs. Every day of delay pushes that milestone back, so this
   is the highest-leverage decision still open.