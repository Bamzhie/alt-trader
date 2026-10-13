# ALT RADAR — Dashboard (React frontend)

Read-only dashboard for the ALT RADAR MEXC perpetual-futures scanner. Vite +
React + TypeScript + Tailwind, talking to a stdlib mock service that
implements the documented HTTP+SSE contract
(`docs/superpowers/specs/2026-10-14-tauri-desktop-design.md` §1) with static
fixtures. When the real `proto/service.py` lands, it drops in behind the
identical client with zero frontend changes.

**Hard rules enforced everywhere in this app:**

- READ-ONLY. No orders, no keys, no execution code. The only POSTs are the
  four idempotent watcher commands (pause / resume / enable-trigger /
  disable-trigger), and the Dashboard never issues them.
- No confidence / probability / tier field anywhere. Evidence renders as
  pass/fail criterion chips, never a numeric score.
- No 1m timeframe. 5m / 15m / 1H / 4H / 1D only; `1m` is a `BAD_REQUEST`.
- No leverage or liquidation field anywhere.
- No TOTAL row in Market Overview — BTC + ETH only, per the mockup.
- Timestamps are UTC-seconds integers, rendered as `YYYY-MM-DD HH:MM:SS UTC`.
- Unavailable is an explicit `null` rendered as "unavailable" — never a zero
  and never a guess. The 24h delta renders "unavailable" when no snapshot
  sits at or before now-24h.
- Wording is "eligible" / "evaluated" — never "scanned".
- All colors come from `var(--*)` theme tokens (`src/theme.css`). No raw hex
  in component code (the chart library's token fallbacks in
  `CandleChart.tsx` are the only exception, and they resolve to real tokens
  at runtime).
- Tests mock `fetch` and stub no network — a loopback tripwire in
  `tests/setup.ts` fails any test that touches a non-loopback host.
- Both servers bind `127.0.0.1` only. The scanner UI is never exposed.

## Quick start

```bash
# Terminal 1 — the mock service (DEV ONLY, static fixtures, not live data)
bash app/dev/serve.sh                 # 127.0.0.1:18741
# PORT=18999 bash app/dev/serve.sh    # override the port

# Terminal 2 — the frontend
cd app
npm install        # first time only
npm run dev        # 127.0.0.1:5173, proxies /api -> 127.0.0.1:18741
```

Open <http://127.0.0.1:5173/>.

**Dev bearer token: `dev-token`** — the mock service's default token, printed
once to stdout at startup. The frontend hard-codes it in `DEV_TOKEN`
(`src/pages/Dashboard.tsx`) because the Dashboard only ever GETs; the
production service injects a per-launch token the shell captures, so this
constant is dev-scoped and is replaced when the Tauri shell lands. It is
never written to the mock's request log.

Other commands:

```bash
npm run test        # vitest run — 166 tests, all mocked fetch
npm run typecheck   # tsc --noEmit
npm run lint        # eslint --max-warnings 0
npm run build       # tsc --noEmit && vite build -> dist/
npm run preview     # serve the built dist/ (still needs the mock for data)

# The mock service has its own test suite (stdlib unittest, no pytest):
cd app/dev && python3 -m unittest test_mock_service    # 48 tests
```

## HTTP + SSE contract summary

Envelope on every JSON route: `{"ok": true, "data": …}` or
`{"ok": false, "data": null, "error": {"code", "message"}}`.
Error codes: `UNAUTH`, `BAD_REQUEST`, `NOT_FOUND`, `UNAVAILABLE`,
`RATE_LIMITED`, plus `NETWORK` synthesized client-side on transport failure.
Every authed route needs `Authorization: Bearer <token>`.

| Route | Notes |
| --- | --- |
| `GET /healthz` | liveness, auth-free, non-envelope `{"ok":true,"alive":true}` |
| `GET /readyz` | readiness; `503 UNAVAILABLE` while the first sweep runs |
| `GET /snapshot` | `snapshot_event_id`, `ts`, `data_as_of`, `eligible_count`, `evaluated_count`, `active_setups`, `monitoring_coverage`, `new_setups_24h`, `comparison_24h`, `setups[]`, `market_overview[]`, `movers[]`, `recent_analysis[]`, `activity[]`, `coins_without_setup[]` |
| `GET /setups?state=&limit=&cursor=` | states `active`/`emitted`/`expired`/`invalidated`/`resolved`; default limit 100, max 500 |
| `GET /setup/:id` | 404 `NOT_FOUND` for a coin with no setup |
| `GET /bars?coin=&tf=` | closed bars only; `tf` ∈ 5m/15m/1H/4H/1D; `1m` → `BAD_REQUEST` |
| `GET /metrics?coin=` | `rsi`, `macd`, `ema50`, `ema200`, `volume_pct`; explicit `null` when unavailable |
| `GET /tradeplan/:id` | `entry_zone`, `stop`, `tp1`, `tp2`, `reference_notional_usdt: 100` — reference size, not a position; no leverage key |
| `GET /events` | SSE, monotonic zero-padded ids, ring buffer 500 events / 30 min, 15s keepalive; honors `Last-Event-ID`; below-floor cursor → `resync` event; kinds `setup.emitted`, `setup.invalidated`, `setup.expired`, `setup.resolved`, `monitoring.unavailable`, `monitoring.ok`, `sweep.completed` |
| `POST /commands/{pause,resume,disable-trigger,enable-trigger}` | idempotent; `BAD_REQUEST` on unknown rule |
| `GET /dev/toggle-ready` | DEV ONLY — flips readiness (exercises the alive-but-not-ready state) |
| `GET /dev/inject-event?kind=` | DEV ONLY — pushes a transition into the SSE stream mid-session |

The frontend computes nothing: indicators come from `/metrics`, bars from
`/bars`, plans from `/tradeplan/:id`. The chart draws candles + volume only —
no EMA overlay, because that would be frontend math.

## Layout

```
app/
  dev/            mock service (stdlib), fixtures, serve.sh, python tests
  src/
    api/          typed client (envelope unwrap), SSE hook, contract types
    store/        Zustand UI state — no network access
    components/   shell + dashboard regions (all token-colored)
    pages/        Dashboard (functional) + StubPage (5 placeholder pages)
    theme.css     var(--*) design tokens
  tests/          vitest (166) — mocked fetch, loopback tripwire
```

## Manual checklist — region by region

Walked against the live mock service on `127.0.0.1:18741` via the dev server
on `127.0.0.1:5173` (a real browser, DOM-inspected; also re-verified against
the built `dist/` via `vite preview`). Fixtures:
150 eligible / 132 evaluated, 6 active setups, 98.4% coverage, data-as-of
`2026-10-14 00:00:00 UTC`.

- [x] **Sidebar** — six nav items (Dashboard / Watchlist / Signals /
      Analysis / Trades / Settings); Dashboard active; clicking Watchlist
      renders the stub "coming in a later release" and back restores the
      dashboard.
- [x] **Top bar** — product name, `SCANNING` badge, live UTC clock, bell +
      gear; footer shows `API status`, `Exchange: MEXC`, `Account: Futures`.
- [x] **Connection banner** — hidden once `/readyz` is true and the SSE
      stream is open; "starting — first sweep in progress" while `/readyz`
      is false (verified by `GET /dev/toggle-ready`, readyz then answers
      `503 UNAVAILABLE` while the snapshot still renders from the
      already-loaded data), then toggled back. Driven by `/healthz` +
      `/readyz`, not by stream-open.
- [x] **Stat cards** — ACTIVE SETUPS `6`; ELIGIBLE PERPS `132 of 150`
      ("evaluated of eligible"); MONITORING COVERAGE `98.4%` ("of the last
      sweep"); NEW SETUPS 24H `6` with the delta line reading **unavailable**
      (no snapshot at or before now-24h — never an inferred number).
- [x] **Top Signals table** — 5 rows (LINK, BTC, ETH, AVAX, SOL) with price,
      signed 24h %, TREND sparkline, ANALYSIS text, EVIDENCE chips, SIGNAL
      pill (LONG/SHORT), EXPIRES countdown.
- [x] **Filters** — Direction All(5)/Long(3)/Short(2): Long → 3 LONG rows
      (BTC, AVAX, SOL); Short → 2 SHORT rows (LINK, ETH). Market Tail → 0
      rows and the table renders **"silence is valid — no active setups on
      this filter"**. Horizon 24h → DOGE only. Sort Expiry → SOL 1h → ETH
      2h30m → BTC 3h → AVAX 4h → LINK 5h. Sort 24h % → SOL +4.87% → BTC
      +2.41% → AVAX +1.05% → ETH -1.18% → LINK -0.74% (absolute value
      descending). Filter changes issue no new requests (client-side over
      the one snapshot).
- [x] **EvidenceBar chips** — per row, criterion names with pass/fail state
      (✓ green / ✗ muted) plus RSI 14 / MACD / EMA 50 / EMA 200 / Volume +%.
      AVAX renders "RSI 14 unavailable" (null, never 0).
- [x] **Market Overview** — BTC 112,505 +2.41% and ETH 4,092.4 -1.18% with
      sparklines. **No TOTAL row**; BTC + ETH only.
- [x] **Coin detail** — header `BTC - Bitcoin`, 112,505, +2.41%, LONG pill,
      RSI 14, and the literal "100 USDT reference notional — reference size,
      not a position" line.
- [x] **Candle chart** — real canvas verified by pixel inspection: ~15k
      painted pixels, ~8.4k green (long) and ~4.6k red (short), price scale
      106,000–114,000, volume histogram, grid lines. Closed bars only.
- [x] **Timeframe tabs** — 5m / 15m / 1H / 4H / 1D; each click re-fetches
      `/bars?coin=BTC&tf=<tf>` and repaints (confirmed in the network log);
      **1m is absent from the tab list**, and `GET /bars?...&tf=1m` answers
      `400 BAD_REQUEST`.
- [x] **Analysis tab** — rule name (RANGE_RECLAIM_LONG), the two analysis
      paragraphs, and the four criteria with ✓ marks.
- [x] **Trade Plan tab** — Entry Zone 112,223.7 – 112,673.8, Stop 111,188.7,
      TP1 114,743.8, TP2 117,848.8, Key Indicators (RSI/MACD/EMA/Volume),
      the 100 USDT reference line, valid-until / next-update UTC timestamps.
- [x] **Metrics tab** — RSI 14 57.40, MACD 0.0211, EMA 50 110,980.20,
      EMA 200 108,214.70, Volume +% 138.50.
- [x] **No-setup coin** — XRP is in `coins_without_setup` with its blocking
      reason; `/setup/XRP`, `/bars?coin=XRP`, `/metrics?coin=XRP` and
      `/tradeplan/tp-xrp-1` all 404, so the panel renders "no setup yet" with
      the snapshot's blocking reason and stays mounted (unit-covered in
      `tests/test_detail.test.tsx`).
- [x] **Market Movers** — Gainers: SOL +4.87%, BTC +2.41%, AVAX +1.05%.
      Losers: DOGE -3.62%, LINK -0.74%. High Volume: "volume ranking
      unavailable — the snapshot carries no quote-volume measure" (never
      fabricated).
- [x] **Recent Analysis** — 5 entries with relative time, coin, direction
      pill (LINK SHORT 30m … DOGE SHORT 4h).
- [x] **Activity Log** — "showing 8 of 10", newest first: SWEEP-COMPLETE,
      EMISSION (LINK), INVALIDATION (ARB), EMISSION (ETH), EXPIRY (OP),
      EMISSION (AVAX/SOL/DOGE).
- [x] **Zero-setup state** — mock restarted with an empty-`setups` fixture
      variant: ACTIVE SETUPS `0`, filter counts `All (0) / Long (0) /
      Short (0)`, table shows "silence is valid", Coin Detail shows "select a
      coin from the board"; every other region still renders normally. No
      console errors.
- [x] **Production build** — `npm run build` + `vite preview` re-verified the
      same regions (stat cards, table, overview, coin detail with a painted
      chart, bottom row) with no blank or broken panels.
- [x] **SSE contract (hook level)** — stream opens through the dev proxy;
      `Last-Event-ID` resumes from the next id; a below-floor cursor emits
      a `resync` event; `/dev/inject-event?kind=setup.emitted&coin=XRP`
      appends id `000012` mid-stream; 15s keepalive comments. The hook is
      unit-tested in `tests/test_sse.test.ts`.
- [x] **SSE wiring (app level)** — the Dashboard opens `/api/events`,
      gates events on the snapshot watermark, and re-snapshots on an
      applied transition or a `resync`; the connection banner is driven by
      `/healthz` + `/readyz`, not by stream-open. Covered end to end by
      `tests/test_sse_wiring.test.tsx` (mocked fetch, no network) and
      verified live below.
- [x] **Banned-word scan** — the rendered page text contains no
      confidence / probability / tier / TOTAL / 1m / leverage / liquidation /
      "scanned".

## Overrides applied to the mockup

The user-supplied mockup is the layout reference; the following overrides are
binding and were applied deliberately:

- **No confidence score, probability, or tier** anywhere. The mockup's
  confidence bar became the EvidenceBar: pass/fail criterion chips plus the
  indicator readings, with no numeric score.
- **No TOTAL row** in Market Overview — BTC and ETH only.
- **No 1m timeframe** — the chart's timeframe tabs are 5m / 15m / 1H / 4H /
  1D; the options array literally omits 1m, and the service rejects it.
- **No leverage or liquidation** — the trade plan shows the 100 USDT
  reference notional as a reference size, never a position, and carries no
  leverage key.
- **No EMA overlay on the chart** — a §4 deviation. The spec's §4 asks for
  EMA50/EMA200 lines over the candles, but §5 forbids frontend math and
  `/metrics` returns scalar EMA values (per-coin, per-snapshot), not a
  series, so drawing them would mean computing indicators client-side. The
  chart draws candles + volume only; the scalar EMAs are read verbatim in
  the Metrics tab.
- "eligible" / "evaluated" wording replaces any "scanned" phrasing.

## Connection banner + live SSE (Task 11 fix round 1)

The hook (`src/api/sse.ts`) and the store's `setConnection` are wired into
the running app in `src/pages/Dashboard.tsx`:

- The banner is driven by `/healthz` (alive) + `/readyz` (ready) — never
  by the hook's stream-open flag. The service's `/events` route
  authenticates but does not gate on readiness, so a stream can be open
  while the first sweep is still running; treating stream-open as
  "connected" would claim readiness before any data exists.
- An applied transition (id above `snapshot_event_id`) and a `resync`
  frame both re-snapshot, so the board reflects live transitions. The
  snapshot read coalesces, so a burst of events costs one extra GET.
- `tests/test_sse_wiring.test.tsx` pins this end to end with mocked fetch
  (no network): the banner reaches "connected" only when `/readyz` is
  true, stays "starting" while `/readyz` is false even with the stream
  delivering events, flips to "error" on a `/healthz` failure, and an
  applied `sweep.completed` / `resync` re-snapshots while a below-watermark
  event does not.
