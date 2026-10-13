# Task 5 report — Zustand store (selection, filters, tab, connection)

**Status:** DONE
**Commit:** 404ca40 `feat(store): dashboard selection/filters/tab state`

## What I implemented

`app/src/store/dashboard.ts` — a Zustand store (vanilla `create`, no middleware)
holding pure client UI state. No network code of any kind; every action is a
one-line `set()` setter.

State (all values exactly as the brief specifies):

| Field | Default | Type |
|---|---|---|
| `selectedCoin` | `null` | `string \| null` |
| `marketFilter` | `"all"` | `"all" \| "top20" \| "tail"` |
| `horizonFilter` | `"4h"` | `"4h" \| "24h"` |
| `directionFilter` | `"all"` | `"all" \| "long" \| "short"` |
| `sort` | `"newest"` | `"newest" \| "expiry" \| "change24h"` |
| `tab` | `"analysis"` | `"analysis" \| "tradeplan" \| "metrics"` |
| `barTimeframe` | `"15m"` | `BarTimeframe` (imported from `../api/types`) |
| `moversTab` | `"gainers"` | `"gainers" \| "losers" \| "volume"` |
| `connection` | `"starting"` | `"starting" \| "connected" \| "error"` |
| `dataAsOf` | `null` | `number \| null` |

Actions: `selectCoin`, `setMarketFilter`, `setHorizonFilter`,
`setDirectionFilter`, `setSort`, `setTab`, `setBarTimeframe`, `setMoversTab`,
`setConnection`, `setDataAsOf` — all pure setters, no fetch/EventSource/timers.

Named union types (`MarketFilter`, `HorizonFilter`, `DirectionFilter`,
`SortKey`, `Tab`, `MoversTab`, `ConnectionState`) are exported so later tasks
(tab components, view logic) can type props against the store instead of
re-declaring string literals. Exported interfaces `DashboardState` and
`DashboardActions` plus the combined `DashboardStore` type.

`barTimeframe` is typed via `import type { BarTimeframe } from "../api/types"`,
so the "no 1m" rule is enforced by the compiler, not by convention.

`zustand` added to `app/package.json` pinned exactly at **5.0.15**
(`npm install zustand --save-exact`), no middleware, lockfile updated.

## TDD evidence

### RED — test written first, verified failing

Test file `app/tests/test_store.test.ts` written BEFORE `src/store/dashboard.ts`
existed. Command and output:

```
$ cd app && npm run test -- tests/test_store.test.ts
 FAIL  tests/test_store.test.ts [ tests/test_store.test.ts ]
Error: Failed to resolve import "../src/store/dashboard" from "tests/test_store.test.ts". Does the file exist?
  Plugin: vite:import-analysis
 Test Files  1 failed (1)
      Tests  no tests
```

Failed for the expected reason (feature missing — module not found), not a typo.

### GREEN — after implementing the store

```
$ cd app && npm run test -- tests/test_store.test.ts
 ✓ tests/test_store.test.ts (17 tests) 12ms
 Test Files  1 passed (1)
      Tests  17 passed (17)
```

### Full suite (per the TDD skill: the project's command, not just my file)

```
$ cd app && npm test
 ✓ tests/tripwire.test.ts (10 tests)
 ✓ tests/test_store.test.ts (17 tests)
 ✓ tests/test_client.test.ts (26 tests)
 ✓ tests/scaffold.test.tsx (1 test)
 ✓ tests/test_sse.test.ts (5 tests)
 Test Files  5 passed (5)
      Tests  59 passed (59)
```

Also clean: `npm run typecheck` (tsc --noEmit, no output) and
`npm run lint` (eslint --max-warnings 0, no output).

## Files changed

- `app/src/store/dashboard.ts` — new, the store.
- `app/tests/test_store.test.ts` — new, 17 tests.
- `app/package.json` / `app/package-lock.json` — `zustand: 5.0.15` pinned.

## Tests in the file

- The brief's Step 1 tests verbatim: "row click selects coin only" (with
  `fetch` stubbed and asserted never called) and "defaults: horizon 4h, sort
  newest, direction all".
- Remaining defaults (market/tab/timeframe/movers/connection/dataAsOf/null
  selection), each setter, `selectCoin(null)` clearing, `barTimeframe` never
  `"1m"`, a "no action touches the network" test invoking every action with a
  stubbed `fetch`, an isolation test (one field changes, others untouched), and
  a key-name guard asserting no state key matches
  `confidence|probability|tier|leverage|liquidation`.
- `beforeEach` resets store state via `useDashboardStore.setState(...)` so the
  singleton cannot leak between tests (matches how Task 6+ will use it).

## Self-review findings

- `setDataAsOf` accepts `number` per the brief; the test passes a UTC-seconds
  integer (1_764_158_400) and the type contract documents UTC-seconds ints.
  No runtime validation — deliberate (read-only scanner, values come from the
  snapshot's `data_as_of`).
- The `forbidden content` test is a key-name check, not a value check; it guards
  the state shape against future regressions where someone adds a
  `confidence` field.
- No persistence middleware — the brief says no middleware and the plan's later
  tasks don't ask for localStorage survival.

## Concerns

- `zustand@5.0.15` install reported 13 pre-existing audit vulnerabilities in
  the dependency tree (5 moderate / 6 high / 2 critical). These are transitive
  dev/transitive deps, not zustand itself; `npm audit fix --force` was NOT run
  because it would make breaking changes to pinned versions established by
  Task 1. Flagging for the plan owner to decide on later.
- Store is a module singleton; multiple React roots in one page would share it.
  Fine for this app (single dashboard), noted for completeness.

## Fix round 1

**Finding fixed (Important):** The defaults tests were tautological — the
`beforeEach` reset fixture (`app/tests/test_store.test.ts:4-17`) rewrote ALL
state before every test, so the defaults assertions only read back values the
test itself had just written. A regression of the initial defaults in
`src/store/dashboard.ts` would have slipped through.

**What I changed** (test-only, `app/tests/test_store.test.ts`):

Added a `describe("initial defaults (fresh module, no reset fixture)")` block
containing one test that calls `vi.resetModules()`, then dynamically imports
`../src/store/dashboard` and asserts `freshStore.getState()` — captured before
any `setState` and unaffected by the module-level `beforeEach` fixture (which
operates on the statically imported singleton) — equals all ten initial
defaults:

- `horizonFilter: "4h"`, `sort: "newest"`, `directionFilter: "all"`,
  `marketFilter: "all"`, `tab: "analysis"`, `barTimeframe: "15m"`,
  `moversTab: "gainers"`, `connection: "starting"`, `dataAsOf: null`,
  `selectedCoin: null`.

The store's actual defaults were NOT changed. The existing reset-based tests
remain for action coverage, per the review instruction.

**RED/GREEN evidence for the guard test:**

RED probe — temporarily changed `horizonFilter: "4h"` → `"24h"` in
`src/store/dashboard.ts` and ran `npx vitest run tests/test_store.test.ts`:

```
 ❯ tests/test_store.test.ts (18 tests | 1 failed) 23ms
   × initial defaults (fresh module, no reset fixture) > getState() before any
     setState matches the store's initial defaults 11ms
     → expected '24h' to be '4h' // Object.is equality
 Tests  1 failed | 17 passed (18)
```

Note the 17 passing: the OLD fixture-based defaults tests all still passed with
the regressed store — exactly the gap the review identified. Restored
`horizonFilter: "4h"`, reran, GREEN.

**Verification (from /home/bamzhie/alt-radar/app):**

```
$ npm run test && npm run typecheck && npm run lint
 ✓ tests/test_store.test.ts (18 tests) 19ms
 ✓ tests/test_client.test.ts (26 tests)
 ✓ tests/tripwire.test.ts (10 tests)
 ✓ tests/scaffold.test.tsx (1 test)
 ✓ tests/test_sse.test.ts (5 tests)
 Test Files  5 passed (5)   Tests  60 passed (60)
> typecheck: tsc --noEmit  (clean, no output)
> lint: eslint . --max-warnings 0  (clean, no output)
```

All 60 tests pass (was 59 — the new guard test adds one), typecheck and lint
clean. The zero-fetch, forbidden-keys, no-1m, and all action tests still pass
unchanged.

**Commit:** d43858f `test(store): guard initial defaults with fresh-module test (fix review finding)` (1 file changed, 27 insertions)

**Concerns:** none.
