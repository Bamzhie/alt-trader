# Task 8 Report — Dashboard page: stat cards + Top Signals table + filters + evidence

**Status:** DONE_WITH_CONCERNS (one pre-existing scaffold gap fixed in passing; one design judgment documented below)
**Commit:** `f54da7c` — feat(dashboard): stat cards, signals table, filters, evidence chips
**Date:** 2026-10-14

## What was implemented

### Files created
- `app/src/components/StatCards.tsx` — the four stat cards.
- `app/src/components/TopSignalsTable.tsx` — the Top Signals table.
- `app/src/components/FilterBar.tsx` — direction / market / horizon / sort controls.
- `app/tests/test_dashboard.test.tsx` — 17 tests (brief Step 1 verbatim + coverage).
- `app/tests/test_stat_cards.test.tsx` — 13 tests (brief Step 1 verbatim + coverage).

### Files modified
- `app/src/pages/Dashboard.tsx` — filled in (was the Task 7 placeholder).
- `app/tests/test_shell.test.tsx` — one test updated (see "Changes to existing tests").
- `app/vite.config.ts` — dev-proxy prefix fix (see "Concerns").

### Dashboard.tsx
- `useSnapshot(client)` fetches `/snapshot` once on mount via the Task 3 client; failure renders a contained "snapshot unavailable — <message>" region. On success it writes `dataAsOf` to the store.
- `useClosedCloses(client, coins)` reads closed 5m bars once per displayed coin for the sparklines; a coin whose bars never arrive keeps the empty Sparkline frame (never fabricated data). A `fetched` ref-set makes each coin a one-shot read; the effect is cancellation-safe.
- Filtering/sorting are pure client-side over `snapshot.setups` (state === "active"), driven entirely by the Task 5 store: market segment → horizon → direction counts → direction filter → sort. No filter change issues a request.
- Header renders "data as of 2026-10-14 00:00:00 UTC" (UTC-seconds int → UTC string).
- Zero active setups renders "silence is valid — no active setups on this filter" in the table region; the Market Overview / Coin Detail / Market Movers / Recent Analysis / Activity Log placeholder regions all still render (Tasks 9–11 fill them).

### StatCards.tsx
- **Active Setups** — count, plus a delta **only** when `comparison_24h.available` and `delta_active_setups !== null`. When unavailable there is no delta row at all (never an inferred number, never a second "unavailable" label).
- **Eligible Perps** — `132 of 150` with footnote "evaluated of eligible". The word "scanned" appears nowhere in rendered output.
- **Monitoring Coverage** — `98.4%` from the decimal; `null` renders "unavailable".
- **New Setups 24h** — count plus the 24h delta; renders "unavailable" when no covering snapshot exists.
- Deltas carry explicit signs: `+2`, `-3`, and a neutral `0` renders `+0` — resolving the Task 6 deferred hand-off (StatCard colors a delta by a leading "-", so a bare "0" would have rendered green).

### FilterBar.tsx
- Generic `Segment<T>` control: Direction (All/Long/Short with counts), Market (All / Top 20 / Tail), Horizon (4h / 24h), Sort (Newest / Expiry / 24h %). Reads and writes the Zustand store directly; no requests. Counts describe the market+horizon-filtered set, i.e. exactly what a direction click will show.

### TopSignalsTable.tsx
- Columns: Coin (LetterBadge + ticker + name), Price, 24h %, Trend (Sparkline from closed 5m bars, direction from first→last close), Analysis (first two setup copy lines, clamped), Evidence (EvidenceBar criteria chips + indicator inputs labeled RSI 14 / MACD / EMA 50 / EMA 200 / Volume +% to match the trade-plan naming), Signal (SignalPill), Expires (countdown measured against the snapshot's `data_as_of`, "in Xh Ym" / "in Xd Yh" / "expired").
- Row click → `selectCoin` only. The row is also keyboard-operable (Enter/Space) and the selected row carries `aria-selected="true"`.
- 24h % carries an explicit sign; a true 0 renders neutral muted (not green).
- Zero rows → the "silence is valid" cell spanning the table.

## TDD evidence

### RED (Step 2)
`cd app && npm run test -- tests/test_dashboard.test.tsx tests/test_stat_cards.test.tsx`

```
 ❯ tests/test_dashboard.test.tsx (17 tests | 17 failed)
 ❯ tests/test_stat_cards.test.tsx (12 tests | 12 failed)   [module not found: StatCards]
 Test Files  2 failed (2)
      Tests  29 failed (29)
```
(Failures: `Unable to find an element with the text: Active Setups` — the placeholder Dashboard rendered only a heading — plus the missing-module import error for StatCards.)

### GREEN (Step 4)
`cd app && npm run test -- tests/test_dashboard.test.tsx tests/test_stat_cards.test.tsx`

```
 Test Files  2 passed (2)
      Tests  30 passed (30)
```

Full suite, typecheck, lint, build — all on the committed tree:

```
$ npm run test
 Test Files  9 passed (9)
      Tests  123 passed (123)

$ npm run typecheck
> tsc --noEmit            (clean, no output)

$ npm run lint
> eslint . --max-warnings 0   (clean, no output)

$ npm run build
✓ 49 modules transformed.
dist/assets/index-Dr6_dQqf.css    9.26 kB │ gzip:  2.75 kB
dist/assets/index-blvBiNH_.js   162.74 kB │ gzip: 52.24 kB
✓ built in 1.53s
```

### Live smoke test (browser, real mock service)
Vite dev server (5173) + `dev/mock_service.py` (18741), real Chromium:

- Dashboard rendered live fixture data: "data as of 2026-10-14 00:00:00 UTC", Active Setups 6, "132 of 150 / evaluated of eligible", Monitoring Coverage 98.4%, New Setups 24h 6 + **unavailable** (the fixture pair has no 24h-covering snapshot — correct, not inferred).
- 5 sparkline SVGs in the table, each a real 48-point polyline from BTC's closed 5m bars.
- BTC row click → exactly one `tr[aria-selected="true"]`, coin cell "B BTC Bitcoin" (selection only, no POST).
- Filters: default horizon 4h → 5 rows (LINK/BTC/ETH/AVAX/SOL); horizon 24h → DOGE only; direction Long → BTC/AVAX/SOL; sort 24h % → SOL first (|4.87|). All no-request, client-side.
- Banned-word sweep over rendered text: vwap / high probability / high conviction / confidence / probability / leverage / liquidation / scanned — all **absent**.

## Changes to existing tests

`app/tests/test_shell.test.tsx` — the Task 7 test "shell mounts without touching the network" asserted `fetch` is never called on mount, which was true only while Dashboard was a placeholder. Task 8's Interfaces mandate the snapshot read on mount, so the test was replaced with "shell mounts read-only: one snapshot GET, never a POST" — stubs fetch, asserts zero POST attempts and that `/api/snapshot` was GET. This keeps the read-only guard while matching the new contract; it is the only assertion change in the task.

## Self-review findings

1. **RED initially included a module-not-found error** for `StatCards` (expected — the file didn't exist), so the first RED run conflated "module missing" with "component missing". The RED evidence above is from the second, clean RED run against the implemented-but-wrong dashboard.
2. **`.closest("div")` locator bug in my own tests** — it returns the label's own div, not the card root, so card-scoped assertions read only the label text. Fixed to `.parentElement` (the StatCard root). Worth flagging as a test-authoring trap for later tasks.
3. **Design judgment — where "unavailable" renders.** The brief's verbatim test `expect(screen.getByText(/unavailable/i))` requires exactly one such element, but a naive reading ("both delta cards show unavailable") produces two and fails with "Found multiple elements". Resolved per the brief's own Interfaces wording — "New Setups 24h — unavailable when no covering snapshot" — the loud "unavailable" belongs to New Setups 24h; Active Setups shows its delta only when available and renders no delta row otherwise. Both behaviors are pinned by tests in both files.
4. **`signedDelta(0)` was `"0"` on first implementation**, which StatCard would have painted green (the Task 6 hand-off). Caught by the "zero delta renders an explicit sign" test; fixed to `+0`.
5. **Input chip labels** — the table labels inputs "RSI 14" / "EMA 50" / "EMA 200" / "Volume +%" to match the spec's trade-plan indicator naming (the raw keys are rsi/ema50/ema200/volume_pct). A null input still renders "unavailable" through EvidenceBar (never 0).
6. **Unused-import / strict-mode sweep** — typecheck (`noUnusedLocals`, `noUnusedParameters`) and lint (`--max-warnings 0`, react-hooks rules) are clean; the `useRef` lazy-init pattern replaced an earlier `useState(() => new Set())[0]` that worked but read poorly.

## Concerns

1. **Pre-existing scaffold gap fixed in passing: `app/vite.config.ts`.** The dev proxy forwarded `/api/snapshot` verbatim to the mock service, which serves contract routes at the root (`/snapshot`) — so the client's default `baseUrl: "/api"` (Task 3) could never reach it; the dev app would have shown "snapshot unavailable — response was not a valid envelope" forever. Added `rewrite: (path) => path.replace(/^\/api/, "")`. This touches a Task 1 file, so it is called out explicitly rather than buried. (Tests never exercised the proxy — they stub fetch — which is why Task 1's suite stayed green with the gap.)
2. **Market segment semantics.** The spec defines Market as "All / Top 20 by 24h quote volume / Tail", but the `/snapshot` contract carries no quote-volume field (grep of fixtures + mock_service confirms none). Rather than invent a number, the segment ranks by each setup's own `volume_pct` (volume vs the 20-bar median — the only volume measure the contract exposes): Top 20 = the 20 highest readings, Tail = the rest, and setups whose volume reading is `null` always land in Tail (never treated as 0). Documented in `applyMarketSegment`'s doc comment. If the real service later exposes 24h quote volume, that one function is the swap point.
3. **Sparkline bars are fetched per displayed coin.** With the fixture's 6 setups this is 6 small GETs on mount (one per coin, one-shot). Acceptable for this sub-project; if the universe grows, batching or a server-side sparkline field would be the follow-up.
4. **Dev token is a source constant.** `DEV_TOKEN = "dev-token"` in Dashboard.tsx is dev-scoped and documented as such; the production service injects a per-launch token the Tauri shell captures (never bundled). Not a leak — the mock service's token is public by design — but the constant must be replaced when the Tauri shell lands.
5. **`useSnapshot` has no polling/SSE refresh yet.** The brief scopes Task 8 to a fetch-on-mount snapshot; SSE-driven re-snapshot (Task 4's `useEvents` + `onResync`) is not wired here. The table therefore does not live-update on `setup.emitted` events — expected for this task, but the wiring point is noted for whoever does the SSE integration.

## Fix round 1

**Status:** DONE — both Important findings fixed; the five minors remain deferred.
**Commit:** `c0623da` — fix(dashboard): keep sparkline bars across StrictMode/in-flight effect races
**Date:** 2026-10-14

### Root cause (one bug, two symptoms)

`useClosedCloses` (Dashboard.tsx) mutated the `fetched` ref-set **before** the `await` (old line 89). Any cleanup firing between that mutation and the resolution therefore found the coins already "fetched" and could not retry them. Both reviewer findings are this single race, observed through two triggers:

- **Finding #1 (StrictMode):** in dev, main.tsx's `<StrictMode>` double-invokes the effect. The first run marks the coins fetched, starts the fetch, and registers a cleanup; the double-invoke's cleanup sets `cancelled = true` and the re-run sees `pending.length === 0` (coins already in `fetched`) and early-returns **without registering its own cleanup** — so the first run's resolution is dropped at `if (cancelled) return`. `closes` stays `{}` and every Sparkline renders the empty frame. (Instrumented probe confirmed the exact sequence; note the mount-time effect runs with `coins=[]` before the snapshot lands — the loss materializes on the run that finally has coins.)
- **Finding #2 (filter/sort change):** `coins` is a fresh array on every `rows` change (Dashboard.tsx:202), so changing sort/horizon mid-flight re-runs the effect; the new run sees the coins already in `fetched`, returns early, and the old run's resolution is discarded by its cleanup — those coins never retry.

### The fix

Dedupe moved **into the resolution**: `fetched.add(coin)` now runs inside the `.then` (only when not cancelled), so a coin is marked fetched only once its bars have actually landed. A dropped promise simply leaves the coin unfetched, and the next effect run retries it — covering both triggers with one change. The `pending.length === 0` early-return is now safe (that run started no fetch, so there is nothing for a cleanup to cancel); its rationale is documented in a code comment. Duplicate GETs per coin are idempotent and bounded (one extra read per re-run) — same order of requests as before, never more than the old code issued on a healthy path.

### TDD evidence

**RED** (against the pre-fix code, with the new tests added; temporary instrumentation also confirmed the failure path in the console trace: `EARLY RETURN, NO CLEANUP` → `cleanup: cancelled=true` → `resolution landed, cancelled: true`):

```
cd app && npm run test -- tests/test_dashboard.test.tsx -t "sparklines"
 ❯ tests/test_dashboard.test.tsx (20 tests | 2 failed | 18 skipped)
     → expected 0 to be greater than 0   (both new tests)
```

The tests render the app under `<React.StrictMode>` with a fetch stub whose `/bars` responses are **deferred** (parked promises), so the fetch is genuinely in flight when a store-driven re-render re-runs the effect — reproducing both the StrictMode cleanup race and the filter/sort-change race deterministically. On the old code the resolution was dropped and zero `polyline` elements rendered.

**GREEN** (after the fix):

```
npm run test -- tests/test_dashboard.test.tsx -t "sparklines"
 ✓ tests/test_dashboard.test.tsx (20 tests | 18 skipped)
 Test Files  1 passed (1)
      Tests  2 passed | 18 skipped (20)
```

Full suite + gates on the committed tree:

```
$ npm run test
 Test Files  9 passed (9)
      Tests  125 passed (125)        (123 → 125: the two new regression tests)

$ npm run typecheck
> tsc --noEmit            (clean, no output)

$ npm run lint
> eslint . --max-warnings 0   (clean, no output)

$ npm run build
✓ built in 1.40s (49 modules, index-DgQVyONg.js 162.74 kB │ gzip: 52.24 kB)
```

### New tests (tests/test_dashboard.test.tsx, `describe("sparklines")`)

- `closed-bar sparklines survive the StrictMode effect race` — renders under StrictMode with deferred `/bars`; after the snapshot lands, a store-driven re-render re-runs the effect while the fetch is in flight; settles the fetch and asserts a real `svg[aria-label='trend up'] polyline` exists and no `no trend data` empty frame remains. RED on old code (`expected 0 to be greater than 0`).
- `sparklines survive a filter change while bars are in flight` — same deferred mechanism, but the coins array changes via `setSort("change24h")` mid-flight; asserts the polyline lands and both 4h coins (BTC, ETH) were requested — no coin stranded unfetched. RED on old code.

Both stubs are GET-only: the POST branch throws, preserving the read-only tripwire. No network: the socket tripwire in tests/setup.ts guards every egress path, and all responses are `Response` objects built from the fixture.

### Constraints honored

- Fix scope limited to the two Important findings; the five minors (key={line}, role=row, DEV_TOKEN bundling, test-inventory drift, etc.) remain untouched and deferred.
- No POST path added or exercised; no banned words introduced (the full-suite banned-word test passes); no 1m/leverage/liquidation language; timestamps remain UTC-seconds ints; colors unchanged (no new styling).

### Note for the reviewer

The first RED attempt of the StrictMode test **passed against the buggy code** — the synchronous fetch stub resolved before the effect's cleanup race could fire. The tests were only made genuinely RED after switching to deferred `/bars` responses, which hold the fetch in flight across the re-render. If any future regression test touches this hook, the stub must keep the bars fetch pending to exercise the race; a resolving-immediately stub will silently pass on broken code.

## Test inventory (this task)

`tests/test_dashboard.test.tsx` (17): renders stat cards + rows with data-as-of stamp; row click selects never POSTs; no banned words; zero setups → silence is valid + other regions render; Active Setups count with no inferred delta; New Setups 24h unavailable without covering snapshot; eligible/evaluated never "scanned"; monitoring coverage %; each row's price/24h%/direction pill; evidence chips; horizon filter default 4h + narrowing; direction filter narrowing; All/Long/Short counts; sort by 24h %; data-as-of UTC format; store `dataAsOf` written.

`tests/test_stat_cards.test.tsx` (13): stat delta unavailable without covering snapshot; eligible/evaluated never "scanned"; signed delta when available; no delta row when unavailable; negative delta; **zero delta renders +0** (Task 6 hand-off); evaluated-of-eligible one line; coverage % from decimal; coverage unavailable when null; New Setups 24h count + delta; New Setups 24h delta unavailable; no banned words.
