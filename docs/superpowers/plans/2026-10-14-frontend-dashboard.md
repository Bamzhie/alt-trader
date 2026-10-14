# Frontend Dashboard Implementation Plan — Sub-project 1 (Dashboard page + mock service)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the ALT RADAR dashboard page as a Vite + React + TypeScript app inside `app/`, talking to a stdlib mock service that implements the documented HTTP+SSE contract, with the six-page shell present and only Dashboard functional.

**Architecture:** Web app in `app/`; `dev/mock_service.py` (stdlib `http.server`) on `127.0.0.1:18741` serves the exact contract from `2026-10-14-tauri-desktop-design.md` §1 with fixtures in `dev/fixtures.json`; the Vite dev proxy points at it. The frontend computes nothing — indicators via `/metrics`, bars via `/bars`, plans via `/tradeplan/:id`. Zustand holds selection/filters/tab/connection; a small SSE hook does subscribe-then-reconcile with `Last-Event-ID` and `snapshot_event_id`.

**Tech Stack:** Node 26 + npm; Vite; React 18 + TypeScript (strict); Tailwind (12 spec hexes as CSS vars, dark); shadcn/ui; Lightweight Charts (only charting lib); Zustand; Vitest + React Testing Library (mocked fetch, no network, socket tripwire).

**Spec:** `docs/superpowers/specs/2026-10-14-frontend-dashboard-design.md` (commit `d40820f`; executors read both) — plus the binding `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md` §1 and `docs/superpowers/specs/2026-10-13-trader-dashboard-design.md`.

## Global Constraints

- READ-ONLY frontend: no orders, no keys, no execution code. POST is used ONLY for `/commands/{pause,resume,disable-trigger,enable-trigger}` (idempotent), and only when the Settings stub becomes real — this sub-project's Dashboard never POSTs.
- NO confidence/probability/tiers anywhere; EvidenceBar criteria chips replace the confidence bar; banned words test-enforced in rendered output: VWAP, "high probability", "high conviction", probability/confidence claims, profit-certainty claims, fill/execution language.
- No 1m timeframe (bars: 5m/15m/1H/4H/1D only); no TOTAL row in Market Overview (BTC + ETH only); no leverage headroom or liquidation safety; Trade Plans are hypothetical-only at **100 USDT reference notional** ("reference size, not a position").
- Row click = selection only, never backend state. Frontend computes no indicators/levels — rendering only.
- Timestamps UTC-seconds ints; envelope `{ok, data, error:{code,message}}`; explicit `null` for unavailable; section-level `unavailable: reason`.
- `tsc --noEmit` clean, lint clean, `npm run build` succeeds; Vitest with mocked fetch and **no network** (socket tripwire fails any non-loopback connect).
- Loopback bind only (`127.0.0.1:18741`); mock dev endpoints `/dev/toggle-ready` and `/dev/inject-event` are dev-only and labeled as such.
- Universe wording: "eligible" / "evaluated", never "scanned". Close-time honesty: "data as of <ts>", last closed bar only.

## Review Focus

- **A UI reading a stale/closed-bar price as live** — the mock's `data_as_of` and bar `ts` values must render as "data as of <ts>"; Test: `test_dashboard_shows_data_as_of_stamp` in `tests/test_dashboard.test.tsx`.
- **A 24h delta inferred when no snapshot covers the 24h point** — must render "unavailable", never a computed number; Test: `test_stat_delta_unavailable_without_covering_snapshot` in `tests/test_stat_cards.test.tsx`.
- **A setup with a missing indicator input silently rendering as 0** — must render explicit `null` handling (chip "unavailable" / Analysis line naming the missing input); Test: `test_evidencebar_unavailable_input_not_zero` + `test_metrics_null_not_zero` in `tests/test_evidence.test.tsx`.
- **An SSE transition landing between subscribe and snapshot getting lost or duplicated** — Test: `test_transition_between_subscribe_and_snapshot_applied_once` in `tests/test_sse.test.ts`.
- **A banned word slipping into rendered copy** ("high conviction" from the old mockup strings); Test: `test_no_banned_words_in_rendered_dashboard` in `tests/test_dashboard.test.tsx`.
- **The zero-setup state breaking layout** — "silence is valid" with all other regions rendering; Test: `test_zero_setups_silence_is_valid` in `tests/test_dashboard.test.tsx`.

---

### Task 1: App scaffold — Vite + TS + Tailwind tokens + Vitest + shadcn

**Files:**
- Create: `app/package.json`, `app/vite.config.ts`, `app/tsconfig.json`, `app/tailwind.config.ts`, `app/postcss.config.js`, `app/index.html`, `app/src/main.tsx`, `app/src/theme.css`, `app/src/App.tsx` (placeholder shell), `app/tests/setup.ts`, `app/vitest.config.ts`
- Test: `app/tests/scaffold.test.tsx`

**Interfaces:**
- Consumes: nothing.
- Produces: `app/` runs via `npm run dev` (Vite, port 5173, proxy `/api` → `http://127.0.0.1:18741`); `npm run test` (Vitest, jsdom); `npm run typecheck` (`tsc --noEmit`); `theme.css` exposes `--bg`, `--card`, `--border`, `--fg`, `--muted`, `--accent`, `--long`, `--short` CSS vars matching the 12 spec hexes (deep navy `#0b0e17` bg family per mockup; exact 12 values chosen by implementer from the spec/mockup, dark theme).

- [ ] **Step 1: Write the failing test**

```tsx
// app/tests/scaffold.test.tsx
import { render, screen } from "@testing-library/react";
import App from "../src/App";

test("app shell mounts", () => {
  render(<App />);
  expect(screen.getByText("ALT RADAR")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm install && npm run test`
Expected: FAIL — no App component / dev deps missing.

- [ ] **Step 3: Scaffold the app**

Vite react-ts template deps (react, react-dom, typescript strict, vite, vitest, jsdom, @testing-library/react + jest-dom, @vitejs/plugin-react); Tailwind 3 + postcss with `theme.css` tokens as CSS vars referenced by tailwind config (`colors` mapped to `var(--*)`); vitest config with `environment: "jsdom"`, setup file importing jest-dom, and a setup that installs a socket tripwire: monkey-patch `net.connect`/`dgram.createSocket` to throw unless host is `127.0.0.1`/`localhost`. `App.tsx` placeholder renders the wordmark "ALT RADAR".

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && npm run test && npm run typecheck`
Expected: PASS, typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add app/package.json app/package-lock.json app/vite.config.ts app/vitest.config.ts app/tsconfig.json app/tailwind.config.ts app/postcss.config.js app/index.html app/src app/tests app/.gitignore
git commit -m "chore(app): vite+react+ts scaffold with tailwind tokens and vitest tripwire"
```

### Task 2: Mock service — contract, fixtures, dev endpoints

**Files:**
- Create: `app/dev/mock_service.py`, `app/dev/fixtures.json`, `app/dev/serve.sh`
- Test: `app/dev/test_mock_service.py` (stdlib unittest, loopback only)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `python3 app/dev/mock_service.py --port 18741` serves: `GET /healthz` (no auth → `{"ok":true,"alive":true}`), `GET /readyz` (auth; 503 `UNAVAILABLE` when not-ready flag set), `GET /snapshot` (auth; carries `snapshot_event_id`, `eligible_count`, `evaluated_count`, `active_setups`, `monitoring_coverage`, `new_setups_24h`, `data_as_of`, `setups[]`), `GET /setups?state=&limit=&cursor=` (default 100, max 500), `GET /setup/:id`, `GET /bars?coin=&tf=` (5m/15m/1H/4H/1D closed only; 1m → 400 `BAD_REQUEST`; unknown coin → 404), `GET /metrics?coin=` (`rsi`,`macd`,`ema50`,`ema200`,`volume_pct`; explicit `null` for unavailable), `GET /tradeplan/:id` (100U reference: `entry_zone`,`stop`,`tp1`,`tp2`,`reference_notional_usdt: 100`; no leverage key), `GET /events` (SSE, monotonic ids, ring buffer 500/30min; kinds `setup.emitted/invalidated/expired/resolved`, `monitoring.unavailable/ok`, `sweep.completed`; stale cursor → `resync`), `POST /commands/{pause,resume,disable-trigger,enable-trigger}` (idempotent; 400 on unknown rule body), `GET /dev/toggle-ready` (dev-only, flips readiness), `GET /dev/inject-event?kind=` (dev-only, pushes a transition into the SSE stream).
  - Bearer auth: fixed dev token printed once to stdout; all routes except `/healthz` and `/dev/*` return 401 `UNAUTH` without it; `Authorization` never logged.
  - `app/dev/fixtures.json` contains: 6 setups (BTC/ETH/SOL/DOGE/AVAX/LINK — directions mixed Long/Short), one setup with an unavailable indicator input (`rsi: null` on METRICS_NULL coin), one coin with **no setup** ("no setup yet" + blocking reason), a snapshot pair whose older snapshot does NOT cover 24h (comparison unavailable), movers (5 gainers), recent analysis (5 entries), activity log (journal entries incl. sweep-complete), bars (closed, ~40 candles BTC 4H with `ts` ints) and trade plans.

- [ ] **Step 1: Write the failing test**

```python
# app/dev/test_mock_service.py
import json, threading, urllib.request, urllib.error, unittest

BASE = "http://127.0.0.1:18741"

def get(path, token="dev-token"):
    req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)

class TestMockService(unittest.TestCase):
    def test_healthz_no_auth(self):
        status, body = get("/healthz", token=None)
        self.assertEqual(status, 200); self.assertTrue(body["alive"])
    def test_auth_matrix(self):
        self.assertEqual(get("/snapshot", token="wrong")[0], 401)
        self.assertEqual(get("/snapshot", token=None)[0], 401)
    def test_bars_reject_1m(self):
        status, body = get("/bars?coin=BTC&tf=1m")
        self.assertEqual(status, 400); self.assertEqual(body["error"]["code"], "BAD_REQUEST")
    def test_metrics_explicit_null(self):
        status, body = get("/metrics?coin=METRICS_NULL")
        self.assertEqual(status, 200); self.assertIsNone(body["data"]["rsi"])
    def test_tradeplan_no_leverage(self):
        status, body = get("/tradeplan/tp-btc-1")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["reference_notional_usdt"], 100)
        self.assertNotIn("leverage", body["data"])
```

Start the server in `setUpModule` on 18741 with the dev token `dev-token`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && python3 dev/test_mock_service.py`
Expected: FAIL — connection refused / no module.

- [ ] **Step 3: Implement `mock_service.py`**

Stdlib `http.server.ThreadingHTTPServer` on `127.0.0.1`; bearer compare with `hmac.compare_digest`; route table per the Interfaces block; fixtures loaded from `fixtures.json` at startup; envelope helper `respond(status, data)` / `error(status, code, message)`; SSE handler writing `id:`/`event:`/`data:` lines with a per-client ring-buffer replay honoring `Last-Event-ID`; readiness flag mutable via `/dev/toggle-ready`; inject via `/dev/inject-event` appending to the ring buffer. `serve.sh`: `#!/bin/sh` running `python3 "$(dirname "$0")/mock_service.py" --port "${PORT:-18741}"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && python3 dev/test_mock_service.py`
Expected: PASS. Also manual: `bash dev/serve.sh &` then `curl -s -H "Authorization: Bearer dev-token" http://127.0.0.1:18741/snapshot | head -c 400`.

- [ ] **Step 5: Commit**

```bash
git add app/dev/mock_service.py app/dev/fixtures.json app/dev/serve.sh app/dev/test_mock_service.py
git commit -m "feat(mock): stdlib service implementing the documented contract with fixtures"
```

### Task 3: Typed API client + envelope + error codes

**Files:**
- Create: `app/src/api/types.ts`, `app/src/api/client.ts`
- Test: `app/tests/test_client.test.ts`

**Interfaces:**
- Consumes: Task 2's contract (route shapes as documented).
- Produces:
  - `app/src/api/types.ts`: `Envelope<T> = { ok: boolean; data: T | null; error: { code: string; message: string } | null }`; `Snapshot`, `Setup`, `Bars`, `Metrics`, `TradePlan`, `Event`, `Mover`, `JournalEntry` types mirroring fixture shapes; `ErrorCode = "UNAUTH" | "BAD_REQUEST" | "NOT_FOUND" | "UNAVAILABLE" | "RATE_LIMITED" | "NETWORK"`.
  - `app/src/api/client.ts`: `createClient(opts: { baseUrl: string; token: string }): ApiClient` where `ApiClient` = `{ healthz(): Promise<boolean>; readyz(): Promise<boolean>; snapshot(): Promise<Snapshot>; setups(query: SetupsQuery): Promise<{ setups: Setup[]; nextCursor: string | null }>; setup(id: string): Promise<Setup>; bars(coin: string, tf: BarTimeframe): Promise<Bars>; metrics(coin: string): Promise<Metrics>; tradeplan(id: string): Promise<TradePlan>; pause(): Promise<void>; resume(): Promise<void>; disableTrigger(rule: string): Promise<void>; enableTrigger(rule: string): Promise<void> }` — `baseUrl` defaults to `"/api"` (Vite proxy), `BarTimeframe = "5m" | "15m" | "1H" | "4H" | "1D"` (no 1m in the type).
  - Envelope unwrap: `ok: true` → return `data`; otherwise throw `ApiError { code: ErrorCode; message: string }`; network failure → `ApiError` with code `NETWORK`.

- [ ] **Step 1: Write the failing test**

```ts
// app/tests/test_client.test.ts
import { createClient, ApiError } from "../src/api/client";

const client = createClient({ baseUrl: "http://127.0.0.1:18741", token: "dev-token" });

test("snapshot returns data on ok envelope", async () => {
  const snap = await client.snapshot();
  expect(snap.eligible_count).toBe(150);
  expect(typeof snap.snapshot_event_id).toBe("string");
});
test("unauthorized throws ApiError UNAUTH", async () => {
  const bad = createClient({ baseUrl: "http://127.0.0.1:18741", token: "nope" });
  await expect(bad.snapshot()).rejects.toMatchObject({ code: "UNAUTH" });
});
test("bars 1m rejected client-side by type; unknown tf server-side 400", async () => {
  await expect(client.bars("BTC", "1m" as never)).rejects.toBeInstanceOf(ApiError);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm run test -- tests/test_client.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `types.ts` and `client.ts`**

`client.ts` builds `fetch` requests with `Authorization: Bearer` header, `?` query builder, `JSON.parse` of the envelope, unwrap/throw as specified. `bars()` passes `tf` through; the type forbids `"1m"` at compile time (test casts to `never` to prove the server also 400s for robustness). Commands send JSON bodies and are idempotent by contract.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && npm run test -- tests/test_client.test.ts && npm run typecheck`
Expected: PASS (tests run against the live mock server from Task 2 started in a `beforeAll`/setup, or against a `vi.stubGlobal("fetch")` mock — implementer picks one and applies it consistently; client tests MUST NOT hit the network, so `vi.stubGlobal("fetch")` is the default choice).
Expected typecheck: clean.

- [ ] **Step 5: Commit**

```bash
git add app/src/api/types.ts app/src/api/client.ts app/tests/test_client.test.ts
git commit -m "feat(api): typed client with envelope unwrap and error codes"
```

### Task 4: SSE hook with reconnect + snapshot reconcile

**Files:**
- Create: `app/src/api/sse.ts`
- Test: `app/tests/test_sse.test.ts`

**Interfaces:**
- Consumes: Task 3's `Event`/`Snapshot` types.
- Produces:
  - `useEvents(opts: { url: string; token: string; snapshotEventId: string | null; onEvent: (e: Event) => void; onResync: () => void }): { connected: boolean; lastEventId: string | null }` — opens the stream with `Last-Event-ID`, calls `onEvent` for each event whose id is **greater than** `snapshotEventId` (string compare of monotonic ids — ids are zero-padded lexicographically comparable), and `onResync()` when a `resync` event arrives. Reconnects with backoff on close/error.

- [ ] **Step 1: Write the failing test**

```ts
// app/tests/test_sse.test.ts — uses a fake EventSource shim and mocked fetch
test("events <= snapshot_event_id are dropped", () => {
  const onEvent = vi.fn();
  // stream emits e1 (id "0001"), e2 (id "0002"); snapshot id "0002"
  mount({ snapshotEventId: "0002", onEvent });
  emit({ id: "0001", kind: "sweep.completed" });
  emit({ id: "0002", kind: "sweep.completed" });
  emit({ id: "0003", kind: "setup.emitted" });
  expect(onEvent).toHaveBeenCalledTimes(1);
  expect(onEvent.mock.calls[0][0].kind).toBe("setup.emitted");
});
test("resync event triggers onResync", () => { /* emit {kind:"resync"} → onResync called */ });
test("transition between subscribe and snapshot is applied once", () => {
  // subscribe opens; transition id "0005" arrives BEFORE snapshot fetch resolves (snapshot id "0004");
  // after snapshot resolves, event 0005 must be delivered exactly once
});
test("refresh traffic produces no events", () => { /* server emits nothing between polls */ });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm run test -- tests/test_sse.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `sse.ts`**

Native `EventSource` (with query-param token or a custom fetch-based SSE reader — implementer chooses fetch-based reader so tests avoid a real socket; the tripwire stays in place). Handles `id`/`event`/`data` frame parsing, lexicographic id comparison against `snapshotEventId`, `resync` detection, exponential-backoff reconnect, `connected` state. Exposes `lastEventId` for the store.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && npm run test -- tests/test_sse.test.ts && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/api/sse.ts app/tests/test_sse.test.ts
git commit -m "feat(sse): reconnect + snapshot reconcile hook"
```

### Task 5: Zustand store — selection, filters, connection

**Files:**
- Create: `app/src/store/dashboard.ts`
- Test: `app/tests/test_store.test.ts`

**Interfaces:**
- Consumes: nothing (pure store).
- Produces:
  - `useDashboardStore` with state `{ selectedCoin: string | null; marketFilter: "all" | "top20" | "tail"; horizonFilter: "4h" | "24h"; directionFilter: "all" | "long" | "short"; sort: "newest" | "expiry" | "change24h"; tab: "analysis" | "tradeplan" | "metrics"; barTimeframe: BarTimeframe; moversTab: "gainers" | "losers" | "volume"; connection: "starting" | "connected" | "error"; dataAsOf: number | null }` and actions `{ selectCoin(c: string | null); setMarketFilter(m); setHorizonFilter(h); setDirectionFilter(d); setSort(s); setTab(t); setBarTimeframe(tf); setMoversTab(m); setConnection(c); setDataAsOf(ts: number) }`.
  - `selectCoin` writes selection only — no network calls anywhere in the store (test asserts zero `fetch` invocations from any action).

- [ ] **Step 1: Write the failing test**

```ts
// app/tests/test_store.test.ts
import { useDashboardStore } from "../src/store/dashboard";
test("row click selects coin only", () => {
  const spy = vi.fn(); vi.stubGlobal("fetch", spy);
  useDashboardStore.getState().selectCoin("BTC");
  expect(useDashboardStore.getState().selectedCoin).toBe("BTC");
  expect(spy).not.toHaveBeenCalled();
});
test("defaults: horizon 4h, sort newest, direction all", () => {
  const s = useDashboardStore.getState();
  expect(s.horizonFilter).toBe("4h"); expect(s.sort).toBe("newest"); expect(s.directionFilter).toBe("all");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm run test -- tests/test_store.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `dashboard.ts`** (Zustand vanilla store, no middleware, defaults per spec; actions are pure setters.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && npm run test -- tests/test_store.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/store/dashboard.ts app/tests/test_store.test.ts
git commit -m "feat(store): dashboard selection/filters/tab state"
```

### Task 6: Shared components — LetterBadge, Sparkline, EvidenceBar, StatCard, Pill

**Files:**
- Create: `app/src/components/LetterBadge.tsx`, `app/src/components/Sparkline.tsx`, `app/src/components/EvidenceBar.tsx`, `app/src/components/StatCard.tsx`, `app/src/components/SignalPill.tsx`
- Test: `app/tests/test_components.test.tsx`

**Interfaces:**
- Consumes: Task 1 tokens.
- Produces:
  - `LetterBadge({ symbol, size? })` — drawn letter glyph, no assets/network.
  - `Sparkline({ values: number[]; up: boolean })` — inline SVG from closed-bar values; empty array → empty frame, no crash.
  - `EvidenceBar({ criteria: { name: string; passed: boolean }[]; inputs: Record<string, number | null> })` — renders criteria chips (passed = accent-green, failed = muted) and one chip per input: `null` input renders "unavailable" (never 0). Contains no numeric confidence.
  - `StatCard({ label, value, delta?: { value: string | null; available: boolean } | null, footnote?: string })` — `available: false` renders "unavailable" (never an inferred number).
  - `SignalPill({ direction: "LONG" | "SHORT" })` — long=green, short=red.

- [ ] **Step 1: Write the failing test**

```tsx
test("EvidenceBar: null input renders unavailable, not 0", () => {
  render(<EvidenceBar criteria={[{ name: "hh", passed: true }]} inputs={{ rsi: null }} />);
  expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
  expect(screen.queryByText("0")).not.toBeInTheDocument();
});
test("StatCard: unavailable delta shows unavailable", () => {
  render(<StatCard label="New Setups" value="0" delta={{ value: null, available: false }} />);
  expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
});
test("Sparkline empty values does not crash", () => {
  expect(() => render(<Sparkline values={[]} up />)).not.toThrow();
});
test("SignalPill colors", () => { render(<SignalPill direction="LONG" />); expect(/* class contains long color var */).toBeTruthy(); });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm run test -- tests/test_components.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement the five components** per Interfaces; all styled with `var(--*)` tokens; no network, no props beyond the documented signatures.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && npm run test -- tests/test_components.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/components/*.tsx app/tests/test_components.test.tsx
git commit -m "feat(components): badge, sparkline, evidence bar, stat card, signal pill"
```

### Task 7: App shell — sidebar, top bar, routes, connection banner, stub pages

**Files:**
- Create: `app/src/App.tsx` (rewrite), `app/src/components/Sidebar.tsx`, `app/src/components/TopBar.tsx`, `app/src/components/ConnectionBanner.tsx`, `app/src/pages/StubPage.tsx`, `app/src/pages/Dashboard.tsx` (empty placeholder for Task 8 to fill)
- Test: `app/tests/test_shell.test.tsx`

**Interfaces:**
- Consumes: Task 5 store (`connection`), Task 1 tokens.
- Produces:
  - `Sidebar` — six items (Dashboard active, Watchlist, Signals, Analysis, Trades, Settings) + footer (API status text from `connection`: "Starting…" / "Connected"; Exchange: MEXC; Account: Futures). Nav uses simple state routing (no router dep — page state in a tiny `usePage` hook in `App.tsx`).
  - `TopBar` — "ALT RADAR" wordmark + "MEXC Perpetual Futures Scanner", SCANNING pill, live UTC clock, bell + gear placeholder icons (aria-labels, non-functional).
  - `ConnectionBanner` — "starting — first sweep in progress" when `connection === "starting"`; hidden when connected; error state shows contained fallback message.
  - `StubPage({ name })` — page frame + "coming in a later release" empty state.
  - Non-dashboard routes render `StubPage`; Dashboard route renders `<Dashboard />`.

- [ ] **Step 1: Write the failing test**

```tsx
test("shell shows all six nav items and stubs for non-dashboard pages", () => {
  render(<App />);
  for (const name of ["Dashboard","Watchlist","Signals","Analysis","Trades","Settings"]) {
    expect(screen.getByRole("button", { name })).toBeInTheDocument();
  }
  fireEvent.click(screen.getByRole("button", { name: "Signals" }));
  expect(screen.getByText(/coming in a later release/i)).toBeInTheDocument();
});
test("starting connection shows banner", () => {
  useDashboardStore.setState({ connection: "starting" });
  render(<App />);
  expect(screen.getByText(/first sweep in progress/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm run test -- tests/test_shell.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement shell components** per Interfaces; `usePage` hook in `App.tsx` holds `"dashboard" | "watchlist" | ...`; sidebar click sets page; Dashboard renders `<Dashboard />` (Task 8 fills it); others render `StubPage`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && npm run test -- tests/test_shell.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/App.tsx app/src/components/Sidebar.tsx app/src/components/TopBar.tsx app/src/components/ConnectionBanner.tsx app/src/pages/StubPage.tsx app/src/pages/Dashboard.tsx app/tests/test_shell.test.tsx
git commit -m "feat(shell): six-page nav, top bar, connection banner, stub pages"
```

### Task 8: Dashboard page — stat cards + Top Signals table + filters + evidence

**Files:**
- Create: `app/src/pages/Dashboard.tsx` (fill), `app/src/components/StatCards.tsx`, `app/src/components/TopSignalsTable.tsx`, `app/src/components/FilterBar.tsx`
- Test: `app/tests/test_dashboard.test.tsx`, `app/tests/test_stat_cards.test.tsx`

**Interfaces:**
- Consumes: Task 3 client, Task 5 store, Task 6 components.
- Produces: `Dashboard.tsx` fetches `snapshot()` on mount (via a `useSnapshot` hook the implementer adds inside this task — mock fetch in tests), renders StatCards, FilterBar, TopSignalsTable, and placeholder regions for Market Overview / detail / bottom row (Tasks 9–11 fill those). Filtering/sorting happen client-side over the snapshot's `setups` array (no new requests). Table row click → `selectCoin` only.

- [ ] **Step 1: Write the failing test**

```tsx
// app/tests/test_dashboard.test.tsx — fetch stubbed to return fixture snapshot (from fixtures.json loaded via fs or inline fixture)
test("dashboard renders stat cards and setup rows with data-as-of stamp", () => {
  render(<App />);
  expect(screen.getByText("Active Setups")).toBeInTheDocument();
  expect(screen.getByText(/data as of/i)).toBeInTheDocument();
  expect(screen.getByText("BTC")).toBeInTheDocument();
});
test("row click selects, never POSTs", () => {
  const post = vi.fn(); /* fetch stub: only GET allowed, POST throws */
  fireEvent.click(screen.getByText("BTC"));
  expect(useDashboardStore.getState().selectedCoin).toBe("BTC");
  expect(post).not.toHaveBeenCalled();
});
test("no banned words anywhere in rendered output", () => {
  const text = document.body.textContent!.toLowerCase();
  for (const w of ["vwap","high probability","high conviction","confidence","probability","leverage","liquidation"]) {
    expect(text).not.toContain(w);
  }
});
test("zero setups renders silence is valid", () => {
  /* snapshot with setups: [] */
  expect(screen.getByText(/silence is valid/i)).toBeInTheDocument();
  expect(screen.getByText("Market Movers")).toBeInTheDocument(); // other regions still render
});

// app/tests/test_stat_cards.test.tsx
test("stat delta unavailable without covering snapshot", () => {
  /* snapshot pair where older snapshot is > 24h ago */
  expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
});
test("eligible/evaluated shown separately, never 'scanned'", () => {
  expect(screen.getByText(/150/)).toBeInTheDocument();
  expect(document.body.textContent!.toLowerCase()).not.toContain("scanned");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm run test -- tests/test_dashboard.test.tsx tests/test_stat_cards.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement the dashboard region**

`Dashboard.tsx`: `useSnapshot(client)` fetch-on-mount (stub in tests) → `StatCards` (Active Setups + delta-from-snapshots with availability; Eligible Perps as `evaluated_count`/`eligible_count`; Monitoring Coverage %; New Setups 24h — unavailable when no covering snapshot), `FilterBar` (All/Long/Short counts from filtered set; Market All/Top 20/Tail; Horizon 4h/24h default 4h; Sort newest/Expiry/24h%), `TopSignalsTable` (Coin via LetterBadge, Price, 24h %, Sparkline from closed 5m bars, 2-line analysis from setup copy, EvidenceBar, SignalPill, Expires countdown, row click → selection). "data as of <ts>" header line. Zero setups → "silence is valid" row region.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && npm run test -- tests/test_dashboard.test.tsx tests/test_stat_cards.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/pages/Dashboard.tsx app/src/components/StatCards.tsx app/src/components/TopSignalsTable.tsx app/src/components/FilterBar.tsx app/tests/test_dashboard.test.tsx app/tests/test_stat_cards.test.tsx
git commit -m "feat(dashboard): stat cards, signals table, filters, evidence chips"
```

### Task 9: Market Overview + Coin detail panel (chart, tabs, trade plan)

**Files:**
- Create: `app/src/components/MarketOverview.tsx`, `app/src/components/CoinDetail.tsx`, `app/src/components/CandleChart.tsx`
- Test: `app/tests/test_detail.test.tsx`

**Interfaces:**
- Consumes: Task 3 client (`bars`, `metrics`, `tradeplan`), Task 5 store (`selectedCoin`, `tab`, `barTimeframe`), Task 6 components.
- Produces:
  - `MarketOverview` — BTC + ETH rows only (badge, name, price, 24h %, mini Sparkline); clicking a row selects the coin. No TOTAL row.
  - `CoinDetail` — header (badge, "COIN - Name", price, 24h %, SignalPill), timeframe tabs 5m/15m/1H/4H/1D (no 1m — the options array literally omits it), `CandleChart` (Lightweight Charts, EMA50/EMA200 overlays + volume histogram), tabs Analysis / Trade Plan / Metrics; Trade Plan shows entry zone, indicators, TP1, TP2, stop with the literal line "100 USDT reference notional — reference size, not a position" and NO leverage key; footer "Signal valid for X" + "Next update in Y"; coin without setup → "no setup yet" + blocking reason in the same panel; bars failure → "bars unavailable" per tab; `rsi: null` → indicator row shows unavailable (not 0).
  - `CandleChart({ bars: Bars; ema50: (number|null)[]; ema200: (number|null)[] })` — mounts Lightweight Charts on a div ref; empty bars → empty frame.

- [ ] **Step 1: Write the failing test**

```tsx
test("detail shows trade plan with reference notional and no leverage", async () => {
  /* selectedCoin=BTC; fetch stub returns bars/metrics/tradeplan fixtures */
  await screen.findByText(/reference size, not a position/i);
  expect(document.body.textContent).not.toMatch(/leverage/i);
});
test("no-setup coin shows no setup yet + blocking reason", async () => {
  /* selectedCoin=NO_SETUP coin; setup fetch 404 */
  await screen.findByText(/no setup yet/i);
});
test("bars unavailable per tab on fetch failure", async () => {
  /* bars fetch rejects → "bars unavailable" visible; analysis tab still renders */
  await screen.findByText(/bars unavailable/i);
});
test("null rsi renders unavailable not 0", async () => {
  await screen.findByText(/unavailable/i);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm run test -- tests/test_detail.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement the three components** per Interfaces; Lightweight Charts mounted via `useEffect` with resize handling; tab state from the store; all network through the Task 3 client.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && npm run test -- tests/test_detail.test.tsx && npm run typecheck`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/components/MarketOverview.tsx app/src/components/CoinDetail.tsx app/src/components/CandleChart.tsx app/tests/test_detail.test.tsx
git commit -m "feat(detail): market overview, coin detail with chart and trade plan"
```

### Task 10: Bottom row — Market Movers, Recent Analysis, Activity Log + wiring

**Files:**
- Create: `app/src/components/MarketMovers.tsx`, `app/src/components/RecentAnalysis.tsx`, `app/src/components/ActivityLog.tsx`, `app/src/pages/Dashboard.tsx` (wiring)
- Test: `app/tests/test_bottom_row.test.tsx`

**Interfaces:**
- Consumes: Task 3 client, Task 5 store (`moversTab`), snapshot data.
- Produces:
  - `MarketMovers({ movers, tab })` — tabs Gainers/Losers/High Volume from store; rows: badge, ticker, change %.
  - `RecentAnalysis({ entries })` — last 5 transitions (time ago + coin + direction).
  - `ActivityLog({ entries })` — journal rows (emission/invalidation/expiry/sweep-complete), newest first, cap displayed; empty → "no activity yet".
  - `Dashboard.tsx` renders the full page: StatCards, TopSignals, Market Overview + Coin Detail (right column), bottom row — matching the mockup's grid.

- [ ] **Step 1: Write the failing test**

```tsx
test("bottom row renders all three regions", () => {
  render(<Dashboard />);
  expect(screen.getByText("Market Movers")).toBeInTheDocument();
  expect(screen.getByText("Recent Analysis")).toBeInTheDocument();
  expect(screen.getByText("Activity Log")).toBeInTheDocument();
});
test("activity log shows sweep-complete entry", () => {
  expect(screen.getByText(/sweep complete/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd app && npm run test -- tests/test_bottom_row.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement the components and wire the full Dashboard grid** per Interfaces.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd app && npm run test && npm run typecheck && npm run lint && npm run build`
Expected: ALL PASS, build succeeds.

- [ ] **Step 5: Commit**

```bash
git add app/src/components/MarketMovers.tsx app/src/components/RecentAnalysis.tsx app/src/components/ActivityLog.tsx app/src/pages/Dashboard.tsx app/tests/test_bottom_row.test.tsx
git commit -m "feat(dashboard): movers, recent analysis, activity log, full page wiring"
```

### Task 11: Full verification, manual checklist, README

**Files:**
- Modify: `app/README.md` (create with the manual checklist + mock service instructions)
- Test: whole suite re-run

**Interfaces:**
- Consumes: everything above.
- Produces: green `npm run test`, `npm run typecheck`, `npm run lint`, `npm run build`; `app/README.md` with: how to run (`bash dev/serve.sh` + `npm run dev`), the dev token, the documented contract summary, the manual region-by-region checklist against the mockup, and the "overrides applied" note (no confidence/TOTAL/1m/leverage).

- [ ] **Step 1: Run the full suite**

Run: `cd app && npm run test && npm run typecheck && npm run lint && npm run build`
Expected: all green; `dist/` produced.

- [ ] **Step 2: Manual pass against the mock service**

Run: `bash dev/serve.sh &` then `npm run dev`, walk: sidebar/top bar, stat cards, signals table + each filter, evidence chips, market overview, coin detail (chart, each timeframe, each tab, no-setup coin via fixtures), bottom row, zero-setup state (fixtures toggle), starting-connection state (`/dev/toggle-ready`). Record results in `app/README.md`.

- [ ] **Step 3: Write `app/README.md`** with run instructions, dev token, contract summary, manual checklist results, and the overrides note.

- [ ] **Step 4: Commit**

```bash
git add app/README.md
git commit -m "docs(app): dashboard sub-project README with manual checklist"
```

## Self-Review

- **Spec coverage:** §1 layout → T1; §2 shell → T7; §3 mock service → T2; §4 dashboard regions → T8 (stats/table/filters/evidence), T9 (overview/detail/chart/tradeplan), T10 (bottom row/wiring); §5 data flow → T3 (client), T4 (SSE), T5 (store); §6 stack → T1; §7 verification → T11 + per-task tests. Zero-setup, unavailable, data-as-of, banned-words, reference-notional, no-1m all pinned to tests.
- **Step scan:** test steps pin names + assertions with exact values (150, 100 USDT, dev token); code steps pin signatures/files; dev-only endpoints labeled.
- **Type consistency:** `BarTimeframe` (no 1m) used identically in T3/T5/T9; `connection` states ("starting"/"connected"/"error") consistent T5/T7; client method names consistent across T3/T8/T9/T10; store action names pinned in T5 and referenced verbatim later.
- **Review Focus:** all six lines owned (data-as-of → T8; delta-unavailable → T8/stat cards; null-not-zero → T6/T9; SSE between-subscribe-and-snapshot → T4; banned words → T8; zero-setup layout → T8).
- **Proportion:** signatures + assertions only; component bodies left to the implementer; no transcripts.
