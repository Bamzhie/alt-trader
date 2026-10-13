# Task 9 report: Market Overview + Coin detail panel (chart, tabs, trade plan)

## What was implemented

**New files (all in `app/`):**

- `src/components/MarketOverview.tsx` — BTC + ETH rows only (badge, ticker, name,
  price with null → "unavailable", signed 24h % with two decimals, mini Sparkline).
  Row click calls `store.selectCoin` only. **No TOTAL row** — the contract exposes
  no portfolio total, so none is computed. Pure render: the caller passes the
  snapshot's `market_overview` rows; optional `closesByCoin` feeds the sparkline.
- `src/components/CoinDetail.tsx` — header (badge, "COIN - Name", price, 24h %,
  SignalPill, RSI-14 readout, reference-size line), timeframe tabs whose options
  array is literally `["5m","15m","1H","4H","1D"]` (1m absent), `CandleChart`,
  and Analysis / Trade Plan / Metrics tabs. Reads `setup`/`tradeplan`/`metrics`
  per coin and `bars` per (coin, timeframe) through the Task 3 client (GETs only).
  - no-setup coin → panel stays mounted and shows "no setup yet" + the blocking
    reason (real reason passed from the snapshot's `coins_without_setup`;
    generic copy only as fallback, never invented).
  - bars failure → "bars unavailable — <error>" replaces just the chart region;
    the analysis tab still renders.
  - `rsi: null` → indicator row renders "unavailable", never 0.
  - Trade Plan tab: entry zone, stop, TP1, TP2, key indicators, the literal
    `reference_size_note` line ("100 USDT reference notional — reference size,
    not a position"), footer "Signal valid for <ts>" + "Next update in <ts>".
    No leverage key anywhere.
  - EMA50/EMA200 chart overlays are computed over the bar closes (the contract
    exposes no EMA series endpoint — noted below as a deviation).
- `src/components/CandleChart.tsx` — Lightweight Charts (v5) on a div ref:
  candlesticks (closed bars only), EMA50/EMA200 line overlays (null entries
  skipped, never drawn as 0), volume histogram on its own scale, `autoSize`
  for resize handling. Empty bars → no series created, empty frame. Colors are
  resolved from theme CSS vars at mount (`tokenValue`); jsdom (no canvas) →
  guard returns early so no chart DOM is created.

**Modified:**

- `src/theme.css` — added `--ema50` (#eab308) and `--ema200` (#8b5cf6) tokens
  (chart overlay colors; hexes stay in theme.css only).
- `src/pages/Dashboard.tsx` — the two Task 8 placeholder regions (Market
  Overview / Coin Detail) now render the real components; the snapshot's
  `market_overview` rows and the selected coin's blocking reason are wired in.
- `package.json` — added `"lightweight-charts": "5.2.1"` (exact pin, as
  required). Installed via `npm install lightweight-charts@^5.2.9`; resolved
  and pinned to **5.2.1**.
- `tests/test_dashboard.test.tsx` — the Task 8 tests rendered the placeholders;
  with the real panel mounted, "BTC"/"ETH"/prices now also appear in the
  overview. Adjustments: `getAllByText` where the overview duplicates text,
  the sparkline empty-frame assertion scoped to `tbody`, the deferred-bars
  stub now only defers 5m fetches (the detail panel's timeframe bars resolve
  immediately), and the snapshot-only stub answers the detail panel's
  bars/metrics/setup/tradeplan GETs with valid empty/404 envelopes.
- `package-lock.json` — lightweight-charts 5.2.1 lock entry.

## TDD evidence

**RED** — `cd app && npm run test -- tests/test_detail.test.tsx`:

```
Error: Failed to resolve import "../src/components/MarketOverview" from
"tests/test_detail.test.tsx". Does the file exist?
 Test Files  1 failed (1)
      Tests  no tests
```

(the four brief tests could not even collect — components did not exist)

**GREEN** — same command after implementation:

```
✓ tests/test_detail.test.tsx (13 tests) 1037ms
 Test Files  1 passed (1)
      Tests  13 passed (13)
```

Full suite after wiring into the Dashboard (Task 8 tests re-scoped as above):

```
$ npm run test
 Test Files  10 passed (10)
      Tests  138 passed (138)
```

`npm run typecheck` clean, `npm run lint` clean, `npm run build` succeeds.

## Test coverage in test_detail.test.tsx

The four brief Step-1 tests verbatim, plus: overview rows + no-TOTAL guard,
row-click selection with zero POSTs, header composition, timeframe tabs omit
1m and refetch bars on switch, Trade Plan levels/indicators/footer, Metrics
tab values, banned-words guard (vwap/confidence/probability/leverage/
liquidation/conviction), and the "select a coin" empty state.

## Self-review findings (fixed during implementation)

1. **Chart mount crashed in jsdom** — Lightweight Charts parsed
   `var(--muted)` (jsdom returns the raw var() string from getComputedStyle)
   and threw inside canvas/matchMedia-less render. Fixed: `tokenValue` falls
   back when the computed value is still a `var()` expression, and the effect
   guards on canvas + matchMedia availability so the empty frame stays and no
   chart DOM is created under jsdom.
2. **Hook-order lint error** — `useMemo` was called after the
   `selectedCoin === null` early return. Hoisted above the return.
3. **`4h` vs `4H`** — `BarTimeframe` is `"4H"` (uppercase H); the horizon
   strings on setups/plans are `"4h"`. Tabs now use the contract casing.
4. **MACD truncation** — first `formatLevel` (2 decimals) collapsed MACD
   0.0211 to "0.02"; added `formatNumber` (significant decimals) for
   indicator values while price levels keep the 2-decimal level format.
5. **Blocking reason was hardcoded** — wired the real reason from the
   snapshot's `coins_without_setup` via an optional prop.

## Concerns / deviations

- **EMA overlay computation**: the contract exposes no per-bar EMA series
  (only scalar `ema50`/`ema200` on metrics), and the brief's `CandleChart`
  interface requires `ema50: (number|null)[]`. CoinDetail computes the EMA
  series over the bar closes in the browser. This is a *display* derivation
  of the same indicators the backend reports, not signal computation — but
  it is technically "the frontend computing an indicator". Flagging for the
  reviewer: if this violates the "rendering only" rule, the alternative is
  dropping the overlays or moving EMA series into the bars payload (contract
  change, out of scope).
- **Reference-size line and RSI render in the header as well as the Trade
  Plan tab**: the brief's tests find the reference-notional line without
  clicking a tab, so the header carries `reference_size_note` and an
  "RSI 14 <value|unavailable>" readout. The Trade Plan tab repeats them
  (scoped assertions in the test use `within(pane)`).
- MarketOverview's optional `closesByCoin` prop is not fed by the Dashboard
  yet (the dashboard's `useClosedCloses` cache is scoped to the filtered
  table coins); overview sparklines render the empty frame until a later
  wiring pass — no fabricated data either way.
