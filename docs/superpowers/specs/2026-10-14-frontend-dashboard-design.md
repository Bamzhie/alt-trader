# Frontend Dashboard Design — React (Vite) sub-project 1: Dashboard page + mock service

**Date:** 2026-10-14
**Status:** Spec (sections approved in chat 2026-10-14)
**Sources:** operator migration prompt; dashboard mockup (user-supplied image); `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md` (binding); `docs/superpowers/specs/2026-10-13-trader-dashboard-design.md` (binding overrides); `docs/superpowers/plans/2026-10-14-headless-service.md` (service contract); backend inventory: `proto/daemon.py`, `proto/watcher.py`, `proto/store.py`, `proto/scan.py`, `proto/mexc.py`, `proto/indicators.py`, `proto/planner.py`.
**Scope:** New `app/` folder — Vite + React + TS + Tailwind + shadcn/ui frontend, dashboard page only, against a stdlib mock service serving the documented HTTP+SSE contract with fixture data. Six-page shell present; Watchlist/Signals/Analysis/Trades/Settings are honest stub frames. Tauri shell, packaging, and the real `proto/service.py` are separate sub-projects. README rules apply (read-only, no orders/keys, no invented data, no edge/probability claims, tests with no network).

**Decomposition:** This is sub-project 1 of the Tauri/React frontend program. Dashboard only. Sub-project 2+: Watchlist, Signals, Analysis, Trades, Settings pages (each its own spec → plan → build cycle), then the Tauri shell + packaging plan.

## 1. Repository layout

```
app/
├── src-tauri/                 (added in the packaging sub-project — NOT built here)
├── src/
│   ├── main.tsx               Vite entry
│   ├── App.tsx                shell: sidebar nav + route outlet + connection banner
│   ├── api/
│   │   ├── client.ts          fetch wrapper: bearer token, envelope unwrap, error codes
│   │   ├── types.ts           TS types mirroring the spec envelope / routes / events
│   │   └── sse.ts             SSE hook: Last-Event-ID reconnect + snapshot reconcile
│   ├── store/                 Zustand: selection, filters, tab, connection status
│   ├── components/            shadcn/ui primitives + shared (Sparkline, EvidenceBar, LetterBadge, …)
│   ├── pages/
│   │   ├── Dashboard.tsx      (functional — this sub-project)
│   │   └── Watchlist.tsx, Signals.tsx, Analysis.tsx, Trades.tsx, Settings.tsx (stub frames)
│   └── theme.css              Tailwind tokens = the 12 spec hexes as CSS vars (dark)
├── tests/                     Vitest: mocked fetch, no network
├── package.json, vite.config.ts, tailwind.config.ts, tsconfig.json
└── dev/
    ├── mock_service.py        stdlib mock server on 127.0.0.1:18741
    ├── fixtures.json          fixture data derived from the mockup, overrides applied
    └── serve.sh               starts the mock server
```

## 2. App shell

- Left sidebar: Dashboard (active), Watchlist, Signals, Analysis, Trades, Settings. Sidebar footer: API status dot + text ("Connected" / "Starting…"), Exchange: MEXC, Account: Futures.
- Top bar: ALT RADAR logo + "MEXC Perpetual Futures Scanner"; SCANNING pill (from `/healthz`); UTC clock; bell/gear placeholders (non-functional icons).
- Connection state driven by `/healthz` (alive) + `/readyz` (ready): "starting — first sweep in progress" when alive-but-not-ready; "connected" only when ready. Mock exposes a dev toggle to exercise the not-ready path.
- The five non-dashboard routes render as real page frames with an honest "coming in a later release" empty state. No fake data on stubs; stub tests cover render + null-safety only.

## 3. Mock service (`dev/mock_service.py`)

- Stdlib `http.server` on `127.0.0.1:18741`; bearer token printed once to stdout (dev: a fixed dev token is fine; the contract shape is what matters).
- Implements the documented contract exactly: `/healthz` (no auth), `/readyz`, `/snapshot` (with `snapshot_event_id`), `/setups?state=&limit=&cursor=`, `/setup/:id`, `/bars?coin=&tf=` (5m/15m/1H/4H/1D closed only; 1m → 400), `/metrics?coin=` (RSI/MACD/EMA50/EMA200/volume; explicit `null` when unavailable), `/tradeplan/:id` (100U reference), `/commands/{pause,resume,disable-trigger,enable-trigger}` (idempotent), `/events` (SSE, monotonic ids, ring buffer 500 / 30 min; `setup.emitted/invalidated/expired/resolved`, `monitoring.unavailable/ok`, `sweep.completed`; stale cursor → `resync`).
- Envelope `{ok, data, error:{code,message}}`; timestamps UTC-seconds ints; error codes `UNAUTH/BAD_REQUEST/NOT_FOUND/UNAVAILABLE/RATE_LIMITED`; section-level `unavailable: reason` supported.
- Fixtures (`dev/fixtures.json`) derived from the mockup image with overrides applied: BTC/ETH/SOL/DOGE/AVAX/LINK setups, movers, activity log, market overview, bars/indicators/trade plans for BTC. Built to exercise: an empty-setup coin ("no setup yet" + blocking reason), a setup with a missing indicator input (`null`), and a 24h-comparison-unavailable snapshot pair. The real `proto/service.py` can later replace the mock behind the identical client with zero frontend changes; the Vite dev proxy points at `127.0.0.1:18741`.
- Mock-only dev affordances: `/dev/toggle-ready` to flip not-ready, `/dev/inject-event` to push a transition mid-session (used by SSE reconcile tests against the real contract shape). These are dev-only and documented as such.

## 4. Dashboard page (image replica with binding overrides)

- **Stat cards (4):** Active Setups (count + 24h delta from timestamped snapshots), Eligible Perps (`eligible_count` / `evaluated_count` shown separately — never "scanned"), Monitoring Coverage %, New Setups 24h. A delta with no covering snapshot → "unavailable", never inferred. Avg. Confidence gauge and High Conviction card are REMOVED (no probability/confidence/tiers anywhere).
- **Top Signals table:** one row per active setup — Coin (letter badge, ticker, full name), Price, 24h %, Trend (sparkline from closed 5m bars), Analysis (2-line `summarize_setup()` copy), **EvidenceBar** (criteria chips: passed criteria green, unavailable inputs muted — replaces the confidence bar), Signal (LONG/SHORT pill), Expires (countdown). Row click = selection only, never backend state. Filters: All/Long/Short, Market (All / Top 20 by 24h quote volume / Tail), Horizon (4h N=48 / 24h N=288, default 4h — a horizon filter, not a bar interval), Sort (newest default / Expiry / 24h %). Header shows "data as of <ts>".
- **Market Overview (right column):** BTC + ETH rows only (badge, name, price, 24h %, mini chart). TOTAL row REMOVED (no source).
- **Coin detail panel:** header (badge, "COIN - Name", price, 24h %, direction pill); timeframe tabs 5m/15m/1H/4H/1D (**no 1m — unsupported**); candlestick chart via **Lightweight Charts only** (no Recharts) with EMA50/EMA200 + volume; tabs Analysis / Trade Plan / Metrics. Trade Plan: analysis text, Entry Zone, Key Indicators (RSI 14 / MACD / EMA 50 / EMA 200 / Volume +%), TP1, TP2, Stop — every level labeled **"100 USDT reference notional — reference size, not a position"**, hypothetical-only, **no leverage headroom or liquidation safety** (no venue maintenance-margin/mark-price inputs). Footer: "Signal valid for X" + "Next update in Y". Coins without a setup: "no setup yet" + blocking reason in the same panel.
- **Bottom row:** Market Movers (Gainers/Losers/High Volume tabs, from tickers), Recent Analysis (last 5 emissions/transitions), Activity Log (capped structured journal: emission, invalidation, expiry, sweep-complete; newest first; cap displayed).
- **Empty state:** zero setups → "silence is valid" in the table region; all other regions still render.
- **Copy guard:** banned words test-enforced in rendered output: VWAP, "high probability", "high conviction", probability/confidence claims, profit-certainty claims, fill/execution language.

## 5. Data flow, state, and error handling

- `client.ts`: typed fetch with bearer token from the mock/real service, envelope unwrap, error-code mapping (`UNAUTH` → auth banner; `503/UNAVAILABLE` → section-level `unavailable: reason`; `RATE_LIMITED` → backoff notice; `NOT_FOUND` → empty state).
- SSE hook: opens `/events` with `Last-Event-ID`, then fetches `/snapshot`; events with id ≤ `snapshot_event_id` are dropped, newer applied; cursor below the ring-buffer floor → server `resync` → client re-snapshots. A transition landing between subscribe and snapshot is never lost or duplicated.
- Zustand store: selection, filters, active tab, connection status. Row click writes selection only.
- The frontend computes no signals, indicators, or levels — indicators come from `/metrics`, bars from `/bars`, plans from `/tradeplan/:id`. Rendering only.
- States: loading skeleton → ready; alive-not-ready → "starting — first sweep in progress"; feed/DB failure → contained fallback banner with the page frame intact; chart timeframe failure → "bars unavailable" per tab; close-time honesty throughout ("data as of", last closed bar).

## 6. Tech stack

- Vite + React 18 + TypeScript (strict) + Tailwind (tokens = 12 spec hexes as CSS vars, dark theme) + shadcn/ui components.
- Lightweight Charts for candles/sparklines (only charting lib).
- Zustand for client state; native `fetch` + a small SSE hook (TanStack Query deferred — snapshot + SSE + Zustand is sufficient for this sub-project; YAGNI).
- Vitest + React Testing Library for tests; `tsc --noEmit` + lint must pass.

## 7. Verification

- `tsc --noEmit` clean; lint clean; `npm run build` succeeds.
- Vitest (mocked fetch, **no network**, socket tripwire fails any non-loopback connect): component render for every dashboard region; row-click = selection only (assert no POST); filters/tab/selection state transitions; EvidenceBar chips render and the banned-words guard passes across every rendered string; null-safety (`rsi: null`, missing indicator input, missing setup, empty fixtures, zero-setup snapshot); SSE reconcile (transition between subscribe and snapshot applied, refresh traffic produces no events, stale cursor → resync → re-snapshot); envelope error-code handling for each code.
- Manual pass: `dev/serve.sh` + `npm run dev`, walk the dashboard region by region against the mockup and record the checklist in `app/README.md`.
- Integration (this sub-project, mocked service over real HTTP): mock server auth matrix, 1m rejection, `snapshot_event_id` reconciliation — these exercise the documented contract shape so the real service drops in unchanged.

## Open decisions

- Snapshot spacing/retention for rolling 24h comparisons (deferred from the trader-dashboard spec; UI shows "unavailable" until settled).
- Exact default port for the real service (18741 provisional; conflict scan specified there).
- `EV_hurdle`/K/tiers stay deferred — no probability source exists.
- Real `proto/service.py` build-out follows the existing 5-task headless-service plan as a parallel sub-project; the frontend contract is already pinned.
