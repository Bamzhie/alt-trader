# Task 3 Report: Typed API client + envelope + error codes

**Status: DONE**
**Commit: 6be4583 — feat(api): typed client with envelope unwrap and error codes**

## What I implemented

- **`app/src/api/types.ts`** — all contract types mirroring the exact shapes
  `app/dev/mock_service.py` emits (verified against `app/dev/fixtures.json`):
  `Envelope<T>`, `ErrorCode`, `BarTimeframe` (5m|15m|1H|4H|1D — no 1m),
  `Direction`, `SetupState`, `SetupInputs`, `SetupCriterion`, `Setup`,
  `SetupsPage`, `SetupsQuery`, `Snapshot` (with `comparison_24h`,
  `snapshot_history`, `coins_without_setup`, `market_overview`, `movers`,
  `recent_analysis`, `activity`, `supported_timeframes`, `event_kinds`,
  section-level `unavailable`), `Bar`, `Bars`, `Metrics` (with section-level
  `unavailable: {reason, missing}`), `TradePlan` (with `entry_zone`,
  `reference_*` note fields, indicators — no leverage/liquidation), `Event`.
- **`app/src/api/client.ts`** — `createClient(opts)` returning `ApiClient`
  with `healthz`, `readyz`, `snapshot`, `setups`, `setup`, `bars`, `metrics`,
  `tradeplan`, `pause`, `resume`, `disableTrigger`, `enableTrigger`;
  `ApiError` class; `baseUrl` defaults to `"/api"` (Vite proxy);
  envelope unwrap (`ok` → `data`, else throw `ApiError` with the server's
  code; fetch-throw or non-envelope body → `NETWORK`); bearer auth header on
  every authed request; query-string builder that drops undefined params;
  commands POST JSON bodies (`{ rule }` for trigger toggles).
- **`app/tests/test_client.test.ts`** — 26 tests, all via `vi.stubGlobal("fetch")`
  (never touches the network; runs under the socket tripwire in `tests/setup.ts`).

## TDD evidence

### RED — test first, verified failing for the right reason
`cd app && npm run test -- tests/test_client.test.ts`
```
FAIL  tests/test_client.test.ts [ tests/test_client.test.ts ]
Error: Failed to resolve import "../src/api/client" from "tests/test_client.test.ts". Does the file exist?
```
Fails because the module does not exist yet (feature missing), not a typo.

### GREEN — implemented, verified passing
`cd app && npm run test -- tests/test_client.test.ts`
```
✓ tests/test_client.test.ts (26 tests) 36ms
 Test Files  1 passed (1)
      Tests  26 passed (26)
```

### Full-suite verification (per TDD: a green single file is not a green suite)
`cd app && npm run test`
```
✓ tests/test_client.test.ts (26 tests)
✓ tests/scaffold.test.tsx (1 test)
 Test Files  3 passed (3)
      Tests  37 passed (37)
```

### Typecheck
`cd app && npm run typecheck` — clean (after removing an unused local and an
unused test import caught by `noUnusedLocals`).

### Lint
`cd app && npm run lint` — clean (`--max-warnings 0`).

## Design decisions

- **`vi.stubGlobal("fetch")`** chosen over starting the live mock server, per the
  brief's own default: client tests must never hit the network, and the socket
  tripwire would block non-loopback anyway. Tests assert on request construction
  (URL, method, Authorization header, JSON body) as well as unwrap/error mapping.
- **`healthz()`/`readyz()` never throw.** Health probes are liveness signals:
  `healthz` is auth-free and deliberately non-envelope on the mock, so it returns
  `false` on any transport/probe failure; `readyz` treats the server's
  `UNAVAILABLE` "first sweep in progress" as `false` (a normal state), and
  re-throws any other `ApiError`. Documented in the interface.
- **`ApiError.code` is `string`** (the brief's `ErrorCode` values in practice).
  Unknown/unmapped server codes pass through verbatim so a new server-side code
  surfaces its real code rather than being flattened to `NETWORK`.
- **`SetupsPage.nextCursor`** maps the wire's `next_cursor` (snake) to the
  client-API's `nextCursor` (camel), per the brief's `ApiClient` signature.

## Global constraints check

- READ-ONLY: only pause/resume/disable-trigger/enable-trigger POSTs exist; no
  order/keys/execution path anywhere. ✅
- No confidence/probability/tier field in any type or code. ✅
- `BarTimeframe` has no `"1m"`; `bars()` casts are compile-error-safe. ✅
- No leverage/liquidation field in any type. ✅
- Timestamps UTC-seconds ints; explicit nulls for unavailable; section-level
  `unavailable: {reason, missing}` on Metrics/Bars and `reason` on Snapshot. ✅
- Envelope `{ok, data, error:{code,message}}` unwrap + NETWORK mapping. ✅
- Error codes UNAUTH/BAD_REQUEST/NOT_FOUND/UNAVAILABLE/RATE_LIMITED/NETWORK all
  covered by tests. ✅
- Tests mocked fetch, no network. ✅

## Concerns

None blocking. Two notes for downstream tasks:
- `Event` (SSE) and the `/events` stream are typed but not yet consumed — Task 4+
  (SSE hook) will use `Event`/`event_kinds` from `Snapshot`.
- `ApiError.code` is `string`, not the narrow `ErrorCode` union, so exhaustive
  `switch` on code in UI error handling should have a default branch.

## Fix round 1

**Finding (Important):** `Setup.name` was typed `name: string` (required) with a
comment claiming the snapshot/setup routes attach it. Verified against the live
mock: only `_snapshot()` (mock_service.py:456-458) enriches setups with `name`;
`_setups_route()` (524-529) and `_setup_route()` (547-557) attach only
`tradeplan_id`, and the SETUPS fixtures in `dev/gen_fixtures.py` carry no `name`
key. So `client.setup(id)` and `client.setups()` handed downstream tasks a
`Setup` whose `name` is `undefined` at runtime — an invented-field class bug and
a guaranteed later UI crash.

### What I changed

- **`app/src/api/types.ts`** — `Setup.name` is now `name?: string`. The doc
  comment now states the truth: present ONLY on the `/snapshot` route
  (`mock_service.py _snapshot()` enriches it); `/setups` and `/setup/:id`
  attach just `tradeplan_id`, so treat it as possibly absent everywhere else.
  The interface header now reads "One setup as returned by /setups and
  /setup/:id". Chose the optional-field variant over a separate `SnapshotSetup`
  type — one type keeps `setups()`/`setup()`/`snapshot().setups` assignable and
  the optionality itself documents the wire asymmetry for downstream consumers.
- **`app/tests/test_client.test.ts`** — removed `name: "Ethereum"` from the
  `setup(id)` fixture (data the real `/setup/:id` never sends), with a comment
  citing `_setup_route()`. The `/snapshot` fixture keeps its `name` (that route
  does send it) — correct either way now that the field is optional.
- **New covering test** in the `setup(id)` test: `expect("name" in got).toBe(false)`
  and `expect(got.name).toBeUndefined()` — asserts the wire fidelity the type
  change documents, so a regression that re-adds `name` to the type/fixture pair
  is caught.

### TDD evidence

**RED** — `cd app && npm run test -- tests/test_client.test.ts` with the new
assertion against the old (name-carrying) fixture:
```
FAIL  tests/test_client.test.ts > createClient — request construction > setup(id) hits /setup/:id
AssertionError: expected true to be false // Object.is equality
 ❯ tests/test_client.test.ts:399:27
    397|     expect(got.coin).toBe("ETH");
    398|     expect(got.direction).toBe("SHORT");
    399|     expect("name" in got).toBe(false);
       |                           ^
    400|     expect(got.name).toBeUndefined();
 Tests  1 failed | 25 passed (26)
```
Failed for the right reason: the fixture injected a `name` the wire never sends.

**GREEN** — after the type + fixture change, `cd app && npm run test`:
```
✓ tests/tripwire.test.ts (10 tests)
✓ tests/test_client.test.ts (26 tests)
✓ tests/scaffold.test.tsx (1 test)
 Test Files  3 passed (3)
      Tests  37 passed (37)
```

**Full verification** — `cd app && npm run test && npm run typecheck && npm run lint && npm run build`, all green:
- test: 37 passed (3 files)
- typecheck: `tsc --noEmit` clean
- lint: `eslint . --max-warnings 0` clean
- build: `✓ built in 1.38s` (dist/assets/index-4wWCrBwe.js 142.81 kB)

Out of scope and untouched, as instructed: the three deferred minor findings
(including the `snapshotEnvelope()` typing nit) and any other file.
