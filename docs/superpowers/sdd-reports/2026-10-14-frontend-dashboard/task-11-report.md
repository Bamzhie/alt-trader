# Task 11 Report — Full verification, manual checklist, README

**Status:** DONE_WITH_CONCERNS

**Commit:** `036d1d1` (docs(app): dashboard sub-project README with manual checklist)

## 1. Full verification suite (real output)

Run from `app/`:

```
$ npm run test
 ✓ tests/test_components.test.tsx (18 tests) 212ms
 ✓ tests/test_dashboard.test.tsx (20 tests) 1781ms
   ✓ sparklines > closed-bar sparklines survive the StrictMode effect race 310ms
 ✓ tests/test_bottom_row.test.tsx (25 tests) 1802ms
   ✓ bottom row renders all three regions 318ms
 ✓ tests/test_stat_cards.test.tsx (12 tests) 143ms
 ✓ tests/scaffold.test.tsx (1 test) 59ms

 Test Files  11 passed (11)
      Tests  166 passed (166)
   Duration  4.30s

$ npm run typecheck
> tsc --noEmit
(no output — clean)

$ npm run lint
> eslint . --max-warnings 0
(no output — clean)

$ npm run build
> tsc --noEmit && vite build
vite v5.4.21 building for production...
✓ 61 modules transformed.
dist/index.html                   0.44 kB │ gzip:   0.28 kB
dist/assets/index-DyuhLk0l.css   10.79 kB │ gzip:   3.02 kB
dist/assets/index-BfTHXK0-.js   346.60 kB │ gzip: 110.11 kB
✓ built in 2.03s
```

All green; `dist/` produced. One pre-existing benign stderr warning during
tests ("An update to Dashboard … was not wrapped in act(...)") — it comes
from the async snapshot fetch in `scaffold.test.tsx`, does not fail anything,
and was present in the Task 10 run.

Additional suite (not in the brief but in the repo):

```
$ cd app/dev && python3 -m unittest test_mock_service
Ran 48 tests in 0.656s
OK
```

## 2. Manual pass against the mock service

Servers: mock on `127.0.0.1:18741` (`bash app/dev/serve.sh`), dev server on
`127.0.0.1:5173` (`npm run dev`), inspected in a real browser via DOM reads
and CDP pixel inspection. Fixtures: 150 eligible / 132 evaluated, 6 active
setups, 98.4% coverage, data-as-of `2026-10-14 00:00:00 UTC`, dev token
`dev-token`.

### Region-by-region results

| Region | Result | Evidence |
| --- | --- | --- |
| Sidebar | PASS | 6 nav items; Dashboard active; Watchlist click renders stub "coming in a later release", Dashboard click restores the page |
| Top bar | PASS | "ALT RADAR / MEXC Perpetual Futures Scanner", SCANNING badge, live UTC clock, bell + gear; footer API status / Exchange: MEXC / Account: Futures |
| Connection banner | PASS (but see concern) | "starting — first sweep in progress" shown; verified via `GET /dev/toggle-ready` (readyz → `503 UNAVAILABLE`) |
| 4 stat cards | PASS | ACTIVE SETUPS 6; ELIGIBLE PERPS 132 of 150 "evaluated of eligible"; MONITORING COVERAGE 98.4% "of the last sweep"; NEW SETUPS 24H 6 with delta "unavailable" |
| Top Signals table | PASS | 5 rows LINK/BTC/ETH/AVAX/SOL; price, signed 24h %, sparkline, analysis, evidence chips, LONG/SHORT pill, countdown |
| Filter: Direction | PASS | All(5) → 5 rows; Long(3) → BTC/AVAX/SOL all LONG; Short(2) → LINK/ETH all SHORT |
| Filter: Market | PASS | All → 5 rows; Tail → 0 rows + "silence is valid — no active setups on this filter" |
| Filter: Horizon | PASS | 4h → 5 rows; 24h → DOGE only |
| Filter: Sort | PASS | Expiry → SOL 1h → ETH 2h30m → BTC 3h → AVAX 4h → LINK 5h; 24h % → SOL +4.87 → BTC +2.41 → AVAX +1.05 → ETH -1.18 → LINK -0.74 (absolute desc). No new network request on filter change (client-side over the single snapshot) |
| EvidenceBar chips | PASS | criterion names with ✓/✗ state + RSI/MACD/EMA 50/EMA 200/Volume +%; AVAX shows "RSI 14 unavailable" (null, not 0) |
| Market Overview | PASS | BTC 112,505 +2.41% and ETH 4,092.4 -1.18% with sparklines; **no TOTAL row**; BTC + ETH only |
| Coin detail header | PASS | "BTC - Bitcoin", 112,505, +2.41%, LONG pill, RSI 14, and "100 USDT reference notional — reference size, not a position" |
| Candle chart | PASS | canvas pixel inspection: 372x226, 15,073 painted pixels of which 8,426 green (long) and 4,562 red (short); price scale 106,000–114,000; volume histogram; grid. Closed bars only |
| Timeframe tabs | PASS | 5m/15m/1H/4H/1D each re-fetch `/bars?coin=BTC&tf=<tf>` (network log confirmed) and repaint; **1m absent**; `GET /bars?...&tf=1m` → `400 BAD_REQUEST` |
| Analysis tab | PASS | RANGE_RECLAIM_LONG, two analysis paragraphs, 4 criteria with ✓ |
| Trade Plan tab | PASS | Entry Zone 112,223.7–112,673.8; Stop 111,188.7; TP1 114,743.8; TP2 117,848.8; Key Indicators; 100 USDT line; valid-until/next-update UTC |
| Metrics tab | PASS | RSI 14 57.40, MACD 0.0211, EMA 50 110,980.20, EMA 200 108,214.70, Volume +% 138.50 |
| No-setup coin (XRP) | PASS | `/setup/XRP`, `/bars?coin=XRP`, `/metrics?coin=XRP`, `/tradeplan/tp-xrp-1` all 404 → panel renders "no setup yet" + the snapshot's blocking reason, stays mounted (unit-covered in tests/test_detail.test.tsx) |
| Market Movers | PASS | Gainers SOL +4.87 / BTC +2.41 / AVAX +1.05; Losers DOGE -3.62 / LINK -0.74; High Volume → "volume ranking unavailable — the snapshot carries no quote-volume measure" |
| Recent Analysis | PASS | 5 entries with relative time + coin + direction pill |
| Activity Log | PASS | "showing 8 of 10", newest first: SWEEP-COMPLETE, EMISSION×6, INVALIDATION, EXPIRY |
| Zero-setup state | PASS | mock restarted with an empty-`setups` fixture variant: ACTIVE SETUPS 0, counts All(0)/Long(0)/Short(0), "silence is valid", Coin Detail "select a coin from the board"; all other regions render normally; no console errors |
| Starting-connection state | PASS | `GET /dev/toggle-ready` → readyz `503 UNAVAILABLE`; banner "starting — first sweep in progress"; snapshot data still renders |
| Production build (`dist/`) | PASS | `vite preview` on 127.0.0.1:4173 re-verified sidebar, stat cards, table, overview, coin detail with a painted chart (15,073 px, 8,426 green / 4,562 red), and the full bottom row — no blank or broken panels |
| SSE contract | PASS | stream opens via the dev proxy; `Last-Event-ID: 000005` resumes at 000006; below-floor cursor emits a `resync` event; `/dev/inject-event?kind=setup.emitted&coin=XRP` appends id 000012 mid-stream; 15s keepalive comments |
| Banned words | PASS | rendered page text contains no confidence / probability / tier / TOTAL / 1m / leverage / liquidation / "scanned"; source scan likewise clean (only comments that state the rule) |
| Loopback / token | PASS | mock prints the token once to stdout; dev server binds 127.0.0.1; `/snapshot` without a bearer → `UNAUTH` |

## 3. Concern (the one real finding)

**`src/api/sse.ts` (`useEvents`) and the store's `setConnection` are never
wired into the running app.**

- `grep -rn "from ['\"].*api/sse" src/` → only `tests/test_sse.test.ts`.
- `grep -rn "setConnection" src/` → only the store definition itself.
- Consequence in the browser: the ConnectionBanner is permanently stuck on
  "starting — first sweep in progress" (store default `connection:
  "starting"`, never updated), and live SSE transitions never reach the
  dashboard. The snapshot-driven regions are all correct — this is the live
  path, not the data path.

Task 4 delivered the hook (tested in isolation, 166/166 green) and Task 10's
brief scope was "the full page grid" (StatCards, TopSignals, Overview,
CoinDetail, bottom row) — neither brief named the SSE wiring as a step, so
the hook landed unimported. Fixing it means editing `Dashboard.tsx` to open
`/api/events` with the client, drive `connection` off the hook's `connected`
flag, and re-snapshot on `resync` — a runtime behavior change beyond this
task's verification scope, so it is reported rather than silently patched.
Suggested follow-up (small, ~20 lines + one test):

```tsx
// in Dashboard, after the snapshot lands:
useEvents({
  url: "/api/events",
  token: DEV_TOKEN,
  snapshotEventId: snapshot?.snapshot_event_id ?? null,
  onEvent: () => {},
  onResync: () => refetchSnapshot(),
});
// and an effect: setConnection(connected ? "connected" : "starting")
```

## 4. Files created / modified

- **Created:** `app/README.md` — run instructions (serve.sh + npm run dev),
  the dev token, the full contract table, the region-by-region manual
  checklist with real values, and the "overrides applied" note
  (no confidence/TOTAL/1m/leverage).
- **Modified:** none in `src/` — this task is verification + docs only.
- The zero-setup check used a temporary fixture variant at
  `/tmp/fixtures_zero.json` (not committed); the default
  `app/dev/fixtures.json` is untouched.

## 5. Process hygiene

Both background servers (mock on 18741, dev on 5173) and the `vite preview`
on 4173 were killed after the pass. The mock's readiness flag was toggled
twice during the starting-state check and restored to `ready: true` before
shutdown.

## Fix round 1 (SSE wiring)

**Status:** DONE

**Commit:** see the reply message.

The §3 concern is resolved: the SSE hook and the store's `setConnection`
are now wired into the running app, and the wiring is pinned by an
integration test. The report's §3 suggested fix sketch was deliberately
NOT followed — it drove `connection` off the hook's `connected` flag,
which the reviewer flagged as spec-non-compliant (the mock's `/events`
route only checks auth, so a stream can open while `readyz` is false;
stream-open alone would show "connected" before the first sweep
finishes, violating §2).

### What changed

- **`app/src/pages/Dashboard.tsx`**
  - `useSnapshot` now returns a `refetch` and coalesces: while a read is
    in flight a further call arms a pending flag and exactly one extra
    read runs when the current one lands — a burst of events costs one
    extra GET, and the mount read is never duplicated.
  - New `useConnectionStatus(client, streamOpen)`: probes `healthz()`
    then `readyz()` on mount, on every stream open/close transition, and
    every 5s. not-alive → `"error"`; alive + not-ready → `"starting"`;
    alive + ready + stream open → `"connected"`; ready but stream down →
    state held (a reconnect blip is not a first sweep). Writes only on a
    real transition (zustand notifies on every `set()`).
  - `useEvents({ url: "/api/events", snapshotEventId, onEvent: refetch,
    onResync: refetch })` — an applied transition or a resync re-snapshots.
- **`app/tests/test_sse_wiring.test.tsx`** (new, 6 tests) — the RED/GREEN
  cycle: written first, watched all 6 fail (no stream opened; connection
  never left `"starting"`), then the wiring above made them pass.
- **`app/tests/sse_stub.ts`** (new) — shared stub helpers: a stream body
  that stays open, `healthzOk`/`readyzOk`/`readyzUnavailable`, and a
  minimal `stubReadOnlyFetch`. Needed because the existing stubs answered
  `/events` with the snapshot JSON envelope — the hook read it, the
  stream ended immediately, and its 1s-backoff reconnect loop spun for
  the rest of each test (2.5× suite slowdown, act() noise). The four
  App-rendering test files now answer the routes the wiring opens.
- **`app/README.md`** — connection-banner checklist item updated (banner
  now hides when ready); "SSE contract" split into hook-level (test_sse)
  and app-level (test_sse_wiring + live) entries; the no-EMA chart
  decision recorded as a §4 deviation under "Overrides applied" (§5
  forbids frontend math and `/metrics` returns scalar EMAs, not a
  series); the stale "Known gap" section replaced with a "Connection
  banner + live SSE" section describing the wiring and its test.

  Note on README blemish #1: the "unterminated inline-code span after
  `Bearer ***`" is a display-redaction artifact, not a file defect. The
  raw bytes of line 74 are `` Every authed route needs `Authorization:
  Bearer <token>`. `` — both backticks present, backtick parity balanced
  across the document. No change was needed or made.

### Verification

```
$ npm run test
 ✓ tests/test_sse_wiring.test.tsx (6 tests)
 Test Files  12 passed (12)
      Tests  172 passed (172)

$ npm run typecheck     # tsc --noEmit — clean
$ npm run lint          # eslint --max-warnings 0 — clean
$ npm run build         # 62 modules, dist/ produced
```

Three consecutive full-suite runs were 172/172 (one earlier run showed a
single flaky failure in `test_bottom_row.test.tsx` that did not reproduce
in the next three runs; it is timing-related to the deferred-bars stub,
predates this change in shape, and is noted here for honesty).

### Manual smoke (live mock + dev server, real browser)

- Banner hidden, sidebar "API status: Connected" with `readyz` true and
  the stream open.
- `GET /dev/toggle-ready` → readyz false → banner shows "starting —
  first sweep in progress" (picked up by the 5s probe).
- Toggled back → banner gone, "API status: Connected".
- `GET /dev/inject-event?kind=sweep.completed&coin=BTC` → id `000012`
  pushed mid-stream → the app issued a fresh `/snapshot` fetch
  (instrumented counter 0 → 1), i.e. the live transition reached the
  board.
- Both servers (mock 18741, dev 5173) killed afterwards; the mock's
  readiness flag was toggled twice and restored to `ready: true`.

---

## Final review fix wave

Commit: 7308b51 (base) → this fix wave. Scope: the two FIX-BEFORE-MERGE
Important findings from the whole-branch review (9340d6b..7308b51), plus
the trivial README test-count bump. Nothing else touched — every deferred
minor was triaged can-ride by the review and is left as-is.

### Important #1 — CoinDetail state reset gated on the read that is stale

`app/src/components/CoinDetail.tsx` (single effect, dep
`[client, selectedCoin, barTimeframe]`) reset **all four** state groups
at the top of the effect, so every timeframe click cleared
setup/plan/metrics/bars. Effect: the header blanked (price →
"price unavailable", 24h → "24h unavailable", the RSI 14 readout
disappeared) and the three coin-scoped GETs (`/setup/:id`,
`/tradeplan/:id`, `/metrics?coin=`) were re-issued on each click — three
redundant requests plus a visible header flash.

**Fix:** split the effect in two, keyed on what is actually stale:

- Coin-scoped effect (dep `[client, selectedCoin]`) — resets and fetches
  setup / tradeplan / metrics. A BTC → ETH switch still clears all three
  before ETH's reads land, so no stale BTC data survives (the existing
  coin-switch regression test still covers this).
- Bars effect (dep `[client, selectedCoin, barTimeframe]`) — resets and
  fetches bars alone. A timeframe click now clears bars only; the header
  and the coin-scoped panes stay on screen and no coin-scoped GET is
  re-issued.

No previous-coin ref was needed — the split effects express the gating
directly, and both keep the `cancelled` guard so an in-flight read is
dropped when its key changes.

**Covering test** (new, `tests/test_detail.test.tsx` →
`coin detail > a timeframe click re-fetches bars only — the header and
coin-scoped panes stay put`): after BTC's data lands, a `setBarTimeframe
("4H")` must produce a `/bars?...&tf=4H` call and **no** new
`/setup/`, `/tradeplan/` or `/metrics` call (call-count equality before
and after), and the header must still read `BTC - Bitcoin`, `112,505`,
`+2.41%`, `RSI 14 …` with neither "price unavailable" nor "24h
unavailable" on screen.

### Important #2 — a failed setup read no longer renders "no setup yet"

`noSetup` was `setup.data === null && setup.error !== null`, and the
no-setup branch took precedence over the error path. A NETWORK /
UNAVAILABLE / 500 on `/setup/:id` therefore rendered "no setup yet —
no rule criteria met on the last sweep" and discarded the error
message: a fabricated rule claim out of a transport failure, exactly
what spec §5 ("feed/DB failure → contained fallback, never fabricated
data") forbids.

**Fix:**

- `Async<T>` gained a `code` field, populated by a new `failure(err)`
  helper (`err instanceof ApiError ? err.code : null`) — so the panel
  can tell a 404 from a broken read.
- `noSetup = setupFailed && setup.code === "NOT_FOUND"`; a new
  `setupFailed` branch (checked after `noSetup`, before the tab panes)
  renders **"setup unavailable — <message>"** with the real error text.
- `setupFailed` is also what feeds the branch, so a non-404 failure no
  longer reaches `AnalysisPane`'s "analysis unavailable" line through
  the no-setup path.

**Covering tests** (3 new, written RED first — all three failed against
the pre-fix component except the NOT_FOUND counterpart, which correctly
passed before and after):

- `a failed setup read renders unavailable, never the no-setup copy` —
  `UNAVAILABLE` on `/setup/:id` renders "setup unavailable" +
  "scanner feed unreachable", and neither "no setup yet" nor "no rule
  criteria met on the last sweep" appears anywhere.
- `a NETWORK failed setup read renders unavailable, not no setup yet` —
  the transport-failure shape (client-mapped `NETWORK`) renders
  "setup unavailable — network request failed".
- `a 404 NOT_FOUND still renders the no-setup copy` — the counterpart:
  only NOT_FOUND renders the no-setup copy with the blocking reason.

Test-harness support: `stubDetail` gained a `setupFails: {code, message}`
option (answers `/setup/<coin>` and `/tradeplan/<coin>` with that error
envelope) and a `calls: string[]` log of every GET url, so the
timeframe test can assert call counts.

### Trivial — README test count

`app/README.md:58` and `:106` said "166 tests"; actual was 172 at review
time and is **176** after this wave's 4 new tests (172 + 4). Both numbers
updated to 176.

### Verification

Command (from `app/`):

```
npm run test && npm run typecheck && npm run lint && npm run build
```

Output:

```
Test Files  12 passed (12)
     Tests  176 passed (176)
   Duration  4.76s

> alt-radar-dashboard@0.1.0 typecheck
> tsc --noEmit
(exit 0, no output)

> alt-radar-dashboard@0.1.0 lint
> eslint . --max-warnings 0
(exit 0, no output)

vite v5.4.21 building for production...
✓ 62 modules transformed.
dist/assets/index-1WDXJgXU.css   10.81 kB │ gzip:   3.03 kB
dist/assets/index-CZAEDOCi.js   349.85 kB │ gzip: 111.30 kB
✓ built in 1.96s
```

RED step (before applying the component fix, with the new tests in
place): `npx vitest run tests/test_detail.test.tsx` → **3 failed |
17 passed** — the two unavailable-copy tests and the timeframe
refetch test; the NOT_FOUND counterpart passed pre-fix as it should.

Also re-ran the mock service suite (untouched by this wave) as a sanity
check: `cd app/dev && python3 -m unittest test_mock_service` → **48
passed, OK**.

### Concerns

- None. The two Important findings are fixed at their root (effect
  scoping, error-code discrimination) rather than papered over, and the
  four new tests pin both regressions. House rules re-checked: no new
  copy claims facts on an error path ("setup unavailable — <message>"
  carries only the failure), no banned terms, no new network paths
  (fetch stays stubbed in tests), colors unchanged (no new styling
  beyond the existing `bg-bg-elevated` / `border-border` token classes
  reused verbatim from the no-setup block).
