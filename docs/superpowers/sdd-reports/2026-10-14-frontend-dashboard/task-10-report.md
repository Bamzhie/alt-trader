# Task 10 report — bottom row (Market Movers, Recent Analysis, Activity Log) + full Dashboard wiring

**Status: DONE**
**Commit:** `515a271` — feat(dashboard): movers, recent analysis, activity log, full page wiring

## What I implemented

Three new components plus the full-page Dashboard grid, per the task brief's
Interfaces section:

### `app/src/components/MarketMovers.tsx`
- `MarketMovers({ movers, tab })`. Tabs **Gainers / Losers / High Volume** rendered as a
  segmented control with `aria-pressed`; the active tab comes from the store's `moversTab`
  (the caller passes it in as `tab`) and a click writes `setMoversTab`.
- Rows: `LetterBadge` + ticker + name + signed 24h % (`+4.87%`, `-3.62%`), colored by the
  `long`/`short` tokens; a true 0.00% stays muted. Row click = `selectCoin` only, no POST.
- Gainers = `change_24h_pct > 0`, sorted descending; losers = `< 0`, sorted ascending
  (biggest loss first). A 0.00% reading is on neither tab rather than being rounded in.
- Empty per-tab state: "no gainers on this sweep" / "no losers on this sweep".
- **High Volume tab is an honest unavailability, not a fabricated ranking.** The
  `/snapshot` contract's `Mover` is `{coin, name, change_24h_pct}` — there is no
  quote-volume measure to rank by, so the tab renders "volume ranking unavailable — the
  snapshot carries no quote-volume measure". Ranking by 24h % and calling it volume would
  have been an invented ordering. (The spec §4 "Market segment" filter handles the same
  gap the same way, ranking by each setup's own `volume_pct` reading; `movers` rows carry
  no such reading.) Flagged below as a spec-vs-contract gap.

### `app/src/components/RecentAnalysis.tsx`
- `RecentAnalysis({ entries })`. Last **5** transitions, newest first (sorted by `ts`
  descending so the panel's contract does not depend on the service's ordering), each with
  time-ago + coin + the same `SignalPill` direction the signals table uses.
- Time-ago is measured against the store's `dataAsOf` (the snapshot's `data_as_of`), **never
  the wall clock** — a relative stamp drifting with render time would misdate the data. No
  reference stamp yet → "unavailable", never "0m ago".
- Empty → "no transitions yet". Row click = `selectCoin` only.

### `app/src/components/ActivityLog.tsx`
- `ActivityLog({ entries })`. Journal rows newest first, capped at **8** displayed, with the
  cap **disclosed** ("showing 8 of 10") — silent truncation would read as "nothing else
  happened".
- Each row carries its journal `kind` as a label (emission / invalidation / expiry /
  sweep-complete) so a sweep summary is distinguishable from a per-coin transition.
- Coin-bearing rows are clickable (`selectCoin`); `sweep-complete` has `coin: null` and is
  plain text. Time-ago against `dataAsOf`, "unavailable" with no reference stamp.
- Empty → the brief's literal **"no activity yet"**.

### `app/src/pages/Dashboard.tsx` (wiring)
- Replaced the three `RegionPlaceholder` regions with the real components; the bottom row is
  `grid gap-4 lg:grid-cols-3`.
- Reads `moversTab` from the store and passes it to `MarketMovers`.
- Removed the now-unused `RegionPlaceholder` helper and updated the file header comment.
- Page now renders: header + data-as-of → StatCards → FilterBar → Top Signals →
  Market Overview + Coin Detail (`lg:grid-cols-2`) → bottom row.

## TDD evidence

### RED — `cd app && npm run test -- tests/test_bottom_row.test.tsx`
```
 FAIL  tests/test_bottom_row.test.tsx [ tests/test_bottom_row.test.tsx ]
Error: Failed to resolve import "../src/components/ActivityLog" from
"tests/test_bottom_row.test.tsx". Does the file exist?
      File: tests/test_bottom_row.test.tsx:20:28
 Test Files  1 failed (1)
      Tests   no tests
```
(The suite could not even collect: all three components were missing.)

### GREEN — `cd app && npm run test`
```
 ✓ tests/test_bottom_row.test.tsx (25 tests) 2108ms
 ✓ tests/test_dashboard.test.tsx ...
 Test Files  11 passed (11)
      Tests  166 passed (166)
```
### `npm run typecheck` — clean (no output)
### `npm run lint` — clean (`eslint . --max-warnings 0`, no output)
### `npm run build` — succeeds
```
✓ 61 modules transformed.
dist/assets/index-BfTHXK0-.js   346.60 kB │ gzip: 111.11 kB
✓ built in 2.51s
```

## Files changed

| File | Change |
|---|---|
| `app/src/components/MarketMovers.tsx` | new |
| `app/src/components/RecentAnalysis.tsx` | new |
| `app/src/components/ActivityLog.tsx` | new |
| `app/src/pages/Dashboard.tsx` | wired bottom row, dropped `RegionPlaceholder`, reads `moversTab` |
| `app/tests/test_bottom_row.test.tsx` | new — 25 tests |
| `app/tests/test_dashboard.test.tsx` | 1 existing assertion re-scoped (see below) |

## Test coverage (25 tests in `test_bottom_row.test.tsx`)

- **Brief Step 1 (verbatim, adapted only to await the snapshot):** bottom row renders all
  three regions; activity log shows the sweep-complete entry.
- **Market Movers (6):** gainers-only listing with both directions asserted; gainers ordered
  by change descending with signed %; losers-only, biggest loss first; tab click writes the
  store and issues no POST; volume tab states unavailability and claims no leader; empty
  movers → honest per-tab empty state; row click selects, never POSTs.
- **Recent Analysis (5):** last five with time-ago/coin/direction and the 6th (oldest)
  dropped; newest-first ordering; time-ago measured against `data_as_of` not the wall clock;
  no reference stamp → "unavailable" not "0m ago"; empty → "no transitions yet".
- **Activity Log (4):** journal newest first with kind labels, cap 8 disclosed
  ("showing 8 of 10") and the two oldest absent; newest-first even when the feed is
  out of order; empty → "no activity yet"; no reference stamp → "unavailable".
- **Full page grid (4):** every region renders in the wired page; zero setups still renders
  every region ("silence is valid"); empty bottom-row payloads → honest empty states, never
  fabricated rows; no banned words anywhere in the rendered page.
- **Standalone props (3):** badge/ticker/change % per row; all three tabs with the active one
  from the store; the component issues no request of its own (`fetchMock` never called).

## Self-review findings (fixed)

1. **`test_dashboard.test.tsx` line 532 needed re-scoping.** The Task 8 test asserted
   `queryByText("SOL")` was null page-wide under the default 4h horizon filter. That held
   only because the bottom row was a placeholder; Market Movers legitimately lists SOL as a
   mover, and a mover is not subject to the horizon filter. I re-scoped the assertion (and
   its `waitFor` counterpart) to `tbody tr`, which is what the test actually means — the
   table's rows — and left the comment explaining why. No behavior of the signals table
   changed; the test still fails if SOL wrongly appears in a table row.
2. **Removed `kindLabel()`** — an identity function that added nothing.
3. **Removed an `as string` cast** on `entry.coin` by hoisting `const coin = entry.coin`
   so TypeScript narrows it inside the null check.
4. **React key uniqueness** — `ActivityLog`'s key is now `ts-kind-coin-or-index`, so two
   entries sharing a timestamp and kind cannot collide.
5. **`getByText("Dashboard")` in my own test** matched both the sidebar nav item and the
   page heading; switched to `getAllByText(...).length > 0` with a comment.

## Verified against the global constraints

- READ-ONLY: every stubbed call is a GET; the POST stub throws and every interaction test
  asserts `postCalls` is empty. `MarketMovers` issues no fetch at all.
- No confidence / probability / tier wording: grep over the three new components is clean,
  and the rendered-output banned-words test covers it end to end (adds "scanned" to the list).
- No 1m: no timeframe is rendered by any of the three components.
- No leverage / liquidation: not referenced.
- "eligible"/"evaluated" wording only: the only such string in these components is the
  fixture's own sweep-complete text ("132 of 150 eligible perps evaluated").
- Timestamps are UTC-seconds ints throughout; relative stamps derive from `data_as_of`.
- Colors via `var(--*)` tokens only — no raw hexes in any of the three files.

## Concerns

1. **Spec §4 vs the contract on High Volume (spec-vs-contract gap, needs a decision).** The
   spec asks for a High Volume tab on Market Movers, but `Mover` in `types.ts` carries only
   `coin`, `name`, `change_24h_pct` — no volume measure, and `mock_service.py` passes
   `fixtures.movers` through unchanged. I chose the honest unavailability over a fabricated
   ranking, consistent with the codebase's existing treatment of the same gap (the market
   segment filter's comment says "the /snapshot contract carries no 24h quote-volume
   ranking"). If the real service should supply a volume-ranked movers list, `Mover` gains a
   `quote_volume_24h` (or a separate `volume_movers`) field and the tab becomes a real
   ranking — a one-place change in `MarketMovers.rowsFor`. Until then the tab is a visible,
   honest gap rather than an invented ordering. **Not a blocker for Task 11.**
2. **`recent_analysis` entries are shown as-is.** The spec says "last 5 emissions/transitions"
   and the brief says "last 5 transitions"; both read as a display cap over the feed's own
   rows, which is what I implemented. If a later requirement means "filter to transitions
   only", that is a one-line predicate change.
3. **The `RecentAnalysis`/`ActivityLog` "just now" branch** (under 60s) is untestable against
   the fixed fixture clock and is not covered by a test; it is defensive only.

**Report file:** `/home/bamzhie/alt-radar/.superpowers/sdd/2026-10-14-frontend-dashboard/task-10-report.md`
