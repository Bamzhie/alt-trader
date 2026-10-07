# Alt Radar v1 — Design Spec

**Date:** 2026-10-07
**Status:** Design approved by user, pending spec review
**Path:** Architectural (new project, multi-subsystem)

---

## 1. Purpose

A local Linux application that scans 1,351 crypto USDT perpetual futures across
Bybit, OKX, and MEXC for coins showing early momentum driven by real buyer/seller
activity. For qualifying coins it emits a complete trade plan — entry, stop loss,
take profit, leverage, position size, and expected costs — for the operator to
review and execute manually.

**It never places orders and never holds API keys.**

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

MEXC caps requests at 2,000 bars and does not page deeper. Bybit and OKX page
arbitrarily deep via `start`/`end`.

| Timeframe | MEXC | Bybit | OKX |
|---|---|---|---|
| 5m | **6.9 days** | deep | deep |
| 15m | 20.8 days | deep | deep |
| 1H | 83 days | deep | deep |
| 4H | 333 days | deep | deep |
| 1D | 1,999 days | deep | deep |

**Consequence:** Bybit and OKX carry all historical analysis and indicator
warm-up. MEXC is used for live tickers, order books, funding, and long-tail
listing coverage. This directly justifies the multi-venue decision.

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
- **Not already vertical** — e.g. +80% in 1h is an untradeable chase, not an entry

**Denominators.** The universe figure (1,082) counts USDT-quoted MEXC perps.
The min-notional figure (1,101 of 1,183) was measured across *all* MEXC
contracts including non-USDT quotes, which is why its denominator differs. Both
are correct within their own scope; the screener filters on USDT perps only.

### 3.3 Scorer

Weighted composite of three signals, each scored 0–100:

| Signal | Weight | Components |
|---|---|---|
| Volume / price expansion | 40% | Volume vs own trailing median, price position in range, breakout confirmation, multi-timeframe alignment |
| Order-book imbalance | 30% | Bid/ask notional skew, thin asks (squeeze fuel), walls |
| OI / funding divergence | 30% | OI rising with price = new longs; OI rising while price stalls = trapped shorts; extreme funding = crowded |

Weights sum to 100. Score = weighted sum, then **hard vetoes** applied (§3.2).
Every contribution is retained for display so the operator can see *why* a coin
flagged and disagree. Weights are configuration, not code constants, and are
revisable against outcome-log data (§6).

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

For each coin above threshold:

- **Direction** — LONG/SHORT with the supporting rationale
- **Entry** — limit band inside the spread, not market
- **Stop loss** — below/above structure (swing low/high), never a fixed %
- **TP1 / TP2** — at 2R and 5R, with partial-close and trailing guidance
- **Leverage** — computed from stake and stop distance (§4 leverage safety rule),
  bounded by the operator's 20x–50x band
- **Position size** — derived from current stake and max-loss-per-trade
- **Costs** — taker fees both sides + estimated funding drag to target
- **Break-even move %** — the price change needed to cover costs
- **R:R** and **max loss** in stake currency
- **Warnings** — counter-trend alignment, min-notional block, insufficient
  stake for the stop distance, wide spread

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

SQLite:

- `signal_log` — timestamp, coin, venue, score, per-signal contributions, plan
- `outcome_log` — forward returns at 1h/4h/24h/7d for every signal

`outcome_log` is what makes the system measurable: it allows scoring each
indicator against realised results rather than assumption, and is the
prerequisite for any future claim about which formulas work.

---

## 7. TUI

Local terminal UI, stdlib only:

- Live ranked table, updates in place
- Columns: rank, coin, price, 24h %, volume, funding, OI trend, spread,
  VOL/BOOK/OI subscores, total score
- **Vetoed-this-scan section** — rejections shown with reasons, so filtering is
  inspectable rather than invisible
- Detail view on Enter: full indicator breakdown with weights and contributions,
  trade plan, warnings
- Keys: `↑↓` select, `f` filter, `s` sort, `p` pause, `q` quit
- Header shows feed health, coin count, scan cadence, stake, compounding state

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
- **Planner tests** — leverage never exceeds the computed safe bound; size never
  exceeds stake; costs always reported
- **Integration** — live smoke test against all three venues, read-only
- **Regression** — signal/outcome logs replayed to confirm scoring stability

---

## 10. Explicit Non-Claims

Stated plainly so the design is not read as promising more than it delivers:

1. **The scanner does not predict price.** It surfaces coins with unusual,
   measurable buyer/seller activity and structures a trade plan.
2. **"+500% coins" is a discovery target, not an expected outcome per trade.**
   Large multiple moves on low-liquidity alts do occur. Their frequency is low,
   and losing signals frequently draw down substantially first. The system's
   value is in separating the rare strong setup from the frequent noise — which
   is an empirical question the outcome log is designed to answer, not something
   this document asserts.
3. **No backtest has been run.** Validation is §9 and the outcome log. Any
   indicator's usefulness on these venues, at these costs, is to be measured,
   not assumed.
4. **Latency precludes scalping** from this host (§2.1).

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