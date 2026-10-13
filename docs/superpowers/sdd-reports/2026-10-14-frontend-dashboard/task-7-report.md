# Task 7 report — app shell (sidebar, top bar, routes, connection banner, stub pages)

**Status:** DONE
**Commit:** `e8984be feat(shell): six-page nav, top bar, connection banner, stub pages`

## What was implemented

TDD cycle followed exactly as the brief specified: failing tests written
first, watched fail (12 of 15 failed with the old placeholder `App.tsx`),
implemented, watched pass.

### Files created / modified

- **`app/src/App.tsx`** (rewritten) — the shell. Holds the `usePage` hook
  (`"dashboard" | "watchlist" | "signals" | "analysis" | "trades" | "settings"`,
  initial `"dashboard"`), reads `connection` from the Zustand store via a
  selector (`useDashboardStore((s) => s.connection)`), and routes:
  `dashboard` → `<Dashboard />`, everything else → `<StubPage name={...} />`.
  Layout is sidebar + (top bar, connection banner, main) column.
- **`app/src/components/Sidebar.tsx`** (new) — six nav items in the brief's
  order, plain `<button>` per item (no router, no `<a href>`), active item
  marked `aria-current="page"` and painted with `text-accent` on `bg-card`;
  inactive items `text-muted`. Footer block: `API status: <state text>` /
  `Exchange: MEXC` / `Account: Futures`, where the status text is
  `Starting…` / `Connected` / `Connection error` per the store's
  `connection`. Also exports the `Page` type (shared with `App.tsx`).
- **`app/src/components/TopBar.tsx`** (new) — `ALT RADAR` wordmark +
  `MEXC Perpetual Futures Scanner` subtitle, `SCANNING` pill (accent token),
  live UTC `HH:MM:SS` clock in a `<time dateTime=…>` (1s interval, cleared
  on unmount), and bell + gear as `aria-label`ed buttons (`Notifications`,
  `Preferences`) containing inline SVG placeholders — no navigation effect.
- **`app/src/components/ConnectionBanner.tsx`** (new) — `starting` →
  `role="status"` banner reading "starting — first sweep in progress";
  `connected` → renders nothing; `error` → `role="alert"` banner reading
  "connection error — retrying automatically; showing the last data
  received". The shell stays fully usable in the error state.
- **`app/src/pages/StubPage.tsx`** (new) — `StubPage({ name })` renders a
  page frame: `<h1>{name}</h1>` + "coming in a later release".
- **`app/src/pages/Dashboard.tsx`** (new) — placeholder page frame with the
  `Dashboard` heading only; Task 8 fills the body.
- **`app/tests/test_shell.test.tsx`** (new) — 15 tests.

## TDD evidence

### RED — Step 2

Command: `cd app && npm run test -- tests/test_shell.test.tsx`

```
 ❯ tests/test_shell.test.tsx (15 tests | 12 failed) 209ms
   × shell shows all six nav items and stubs for non-dashboard pages 118ms
     → Unable to find an accessible element with the role "button" and name "Dashboard"
   × starting connection shows banner 6ms
     → Unable to find an element with the text: /first sweep in progress/i
   × dashboard is the active nav item on mount 15ms
     → Unable to find an accessible element with the role "button" and name "Dashboard"
   × nav click moves the active item and swaps the page 12ms
     → Unable to find an accessible element with the role "button" and name "Signals"
   × active nav item is painted with the accent token, inactive with muted 13ms
   × sidebar footer carries API status, exchange and account 3ms
     → Unable to find an element with the text: API status: Starting….
   × sidebar footer API status follows the connection state 3ms
   × top bar shows the wordmark, subtitle and SCANNING pill 4ms
     → Unable to find an element with the text: MEXC Perpetual Futures Scanner.
   × top bar clock renders UTC seconds and ticks 5ms
   × bell and gear are labelled placeholders that do not navigate 9ms
     → Unable to find an accessible element with the role "button" and name "Notifications"
   × error connection shows a contained fallback message 3ms
   × dashboard route renders the Dashboard page, not a stub 11ms
 Test Files  1 failed (1)
      Tests  12 failed | 3 passed (15)
```

Every failure was the feature being missing (no nav buttons, no banner,
no clock), not a typo or setup error — the expected RED.

### GREEN — Step 4

Command: `cd app && npm run test -- tests/test_shell.test.tsx`

```
 ✓ tests/test_shell.test.tsx (15 tests) 499ms
 Test Files  1 passed (1)
      Tests  15 passed (15)
```

Full suite (`cd app && npm run test`), after a mid-GREEN test fix described
under self-review:

```
 ✓ tests/tripwire.test.ts (10 tests) 15ms
 ✓ tests/test_store.test.ts (18 tests) 27ms
 ✓ tests/test_components.test.tsx (18 tests) 175ms
 ✓ tests/test_sse.test.ts (5 tests) 413ms
 ✓ tests/test_client.test.ts (26 tests) 67ms
 ✓ tests/scaffold.test.tsx (1 test) 129ms
 ✓ tests/test_shell.test.tsx (15 tests) 591ms
 Test Files  7 passed (7)
      Tests  93 passed (93)
```

Additional verification run alongside GREEN:

- `npm run typecheck` — clean (`tsc --noEmit`, no output).
- `npm run lint` — clean, `--max-warnings 0`, after one fix (see
  self-review).
- `npm run build` — clean (`✓ 40 modules transformed`, `dist/assets/
  index-CR64-ACG.js 148.24 kB`).

## Self-review findings

1. **Ambiguous test matcher (fixed).** The error-banner test originally
   used `getByText(/connection error/i)`, which matched both the banner and
   the sidebar footer's `API status: Connection error` — a false pass risk
   in the other direction and a brittle query. Tightened to scope at
   `getByRole("alert")` and assert on its `textContent`.
2. **`react-refresh/only-export-components` warning (fixed).** Exporting
   `NAV_ITEMS` from `Sidebar.tsx` tripped the lint rule (max-warnings 0
   would fail CI). Made the constant module-private; only the `Page` type
   is exported (types don't trip the rule).
3. **`scaffold.test.tsx` still green.** The old test asserts
   `getByText("ALT RADAR")` — the new TopBar keeps that exact text, so the
   pre-existing test passes unchanged. No test was weakened or removed.
4. **No raw hexes.** Verified by grep over `app/src/**/*.tsx` for
   `#[0-9a-fA-F]{3,8}` — zero matches. All color comes from Tailwind token
   classes (`bg-bg`, `bg-bg-elevated`, `bg-card`, `bg-card-hover`,
   `text-fg`, `text-muted`, `text-accent`, `text-long`, `text-short`,
   `border-border`).
5. **No banned vocabulary.** A test asserts the shell chrome contains no
   `confidence|probability|conviction` and no `tier` language.
6. **Read-only.** The shell issues no requests: no `fetch`, no SSE, no
   timers other than the display clock. A test renders `<App />` with a
   stubbed `fetch` and asserts it is never called; the socket tripwire in
   `tests/setup.ts` also guards every run.

## Concerns

- **`usePage` is component-local state, not URL state.** The brief specifies
  simple state routing with no router dependency, so refreshing the page
  always lands on Dashboard. Acceptable per the brief; a router is a later
  decision if deep links are ever wanted.
- **Sidebar `Page` type lives in `Sidebar.tsx`** (shared with `App.tsx` via
  a type-only import). It works and typechecks, but a future task adding
  more pages should consider moving the union to `src/pages/types.ts` or
  the store if it ever needs to be shared more widely.
- **`ConnectionBanner`'s error text is invented** ("connection error —
  retrying automatically; showing the last data received"). The brief only
  says "error state shows contained fallback message", so the wording is
  mine; the test pins it. Easy to adjust if a later task wants exact
  copy.
- **TopBar clock uses `setInterval`, not the store's `dataAsOf`.** The
  brief asks for a "live UTC clock", so wall-clock ticking is the correct
  read; Task 8's stat footnotes will use `dataAsOf` for data freshness
  separately.
