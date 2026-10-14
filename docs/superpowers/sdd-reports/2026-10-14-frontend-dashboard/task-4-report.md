# Task 4 report: SSE hook with reconnect + snapshot reconcile

**Status:** DONE
**Branch/commits:** see git log (single commit, task-scoped files only)

## What was implemented

- `app/src/api/sse.ts` — `useEvents(input)` returning `{ connected, lastEventId }`,
  exactly the interface pinned by the brief:
  - `input`: `{ url, token, snapshotEventId: string | null, onEvent, onResync, options?: { backoffMs } }`.
  - **Fetch-based SSE reader** (not `EventSource`) — the contract requires the bearer
    token on every route and `EventSource` cannot send an `Authorization` header;
    a `ReadableStream` reader also keeps the socket tripwire armed (no real socket
    ever opened; tests drive a fake stream).
  - Frame parsing of `id:` / `event:` / `data:` lines, `\n\n`-terminated,
    `:`-comment (keepalive) frames skipped, per the exact format
    `mock_service.py _frame()` writes.
  - **Snapshot reconcile gate:** events with `id <= snapshotEventId` are dropped
    (string compare — zero-padded width-6 ids are lexicographically ordered, as
    the brief specifies). While `snapshotEventId` is `null` (snapshot in flight)
    the gate is open, so a transition arriving in the subscribe-before-snapshot
    window is applied exactly once; any later replay is swallowed by the
    watermark. This mirrors `mock_service.py`'s subscribe-before-copy
    exactly-once guarantee on the client side.
  - **Resync:** a `resync` frame calls `onResync()` (store re-snapshots); the
    frame itself is not forwarded as a transition. The stream stays open — the
    new snapshot's `snapshot_event_id` tightens the gate.
  - **Reconnect:** on stream close or fetch/read error, reconnect with
    exponential backoff (`backoffMs` base, doubling, capped 30s; default
    1000ms) carrying `Last-Event-ID` (from a ref holding the freshest cursor)
    plus the `Authorization` header. `connected` reflects the live stream.
  - Latest-wins refs for callbacks/watermark so identity-only prop changes
    never restart the connection loop.

- `app/tests/test_sse.test.ts` — 5 tests (the brief's 4 core spec tests + 1
  reconnect/backoff test), all driven by a stubbed `fetch` whose stream is a
  test-controlled `ReadableStream`. No real sockets (tripwire untouched).

## TDD evidence

**RED** — `npm run test -- tests/test_sse.test.ts` before `sse.ts` existed:

```
FAIL  tests/test_sse.test.ts [ tests/test_sse.test.ts ]
Error: Failed to resolve import "../src/api/sse" from "tests/test_sse.test.ts".
 Test Files  1 failed (1)
      Tests  no tests
```

**GREEN** — after implementing `sse.ts`:

```
 ✓ tests/test_sse.test.ts (5 tests) 382ms
 Test Files  1 passed (1)
      Tests  5 passed (5)
```

Full suite, typecheck, lint (all clean):

```
$ npm run test
 ✓ tests/tripwire.test.ts (10 tests) 14ms
 ✓ tests/test_client.test.ts (26 tests) 44ms
 ✓ tests/scaffold.test.tsx (1 test) 61ms
 ✓ tests/test_sse.test.ts (5 tests) 391ms
 Test Files  4 passed (4)
      Tests  42 passed (42)

$ npm run typecheck   # tsc --noEmit — clean
$ npm run lint        # eslint . --max-warnings 0 — clean
```

## The four core tests (named as in the brief)

1. `events <= snapshot_event_id are dropped` — emits 0001/0002 (snapshot id
   0002) then 0003: `onEvent` called once, kind `setup.emitted`.
2. `resync event triggers onResync` — 0003 (sweep.completed) then a `resync`
   frame: `onResync` once, `onEvent` once (0003 only).
3. `transition between subscribe and snapshot is applied once` — snapshot fetch
   pending (gate open), transition id 0005 delivered, snapshot then resolves
   with id 0004: `onEvent` called exactly once with id 0005.
4. `refresh traffic produces no events` — snapshot re-issued with the same id,
   keepalive comment frames only: `onEvent` never called.

Plus: `reconnect after close sends Last-Event-ID, delayed by backoff` —
server closes the stream; no reconnect before the backoff elapses; the
reconnect request carries `Last-Event-ID: 0009` and the bearer token.

## Files changed

- `app/src/api/sse.ts` (new)
- `app/tests/test_sse.test.ts` (new)

No changes to `types.ts` or `client.ts`. The resync `reason` field is parsed
locally (a `ResyncFrame` interface in `sse.ts` extends `Event` with optional
`reason`) — the shared `Event` type was left untouched, since the brief scopes
the resync handling to this task and the store does not need `reason`.

## Self-review findings

- Initial draft had a module-level ref hack and an unused generator helper —
  removed; the loop buffers with a plain `indexOf("\n\n")` scan.
- Debug detour: `new Request(input, init)` in the fetch stub silently dropped
  the `AbortSignal` (Node's Request re-wraps signals), so the hook saw
  `connected: false` forever. Fixed by reading the signal straight off `init`
  in the test stub. Production code was correct.
- The gate reads `snapshotIdRef.current` (fresh each render), so a snapshot
  resolving mid-stream immediately tightens the gate — no reconnect needed.
- `lastEventId` state is for the caller; the reconnect header uses a ref so a
  batched state update can never send a stale cursor.

## Concerns

- None blocking. Minor: backoff is untested beyond the first step (asserted
  no-early-reconnect + eventual reconnect with the header); exponential growth
  itself is straightforward arithmetic, covered by the single-step assertion.
- The store (Task 5+) owns the snapshot fetch and calls `onResync()`; this
  hook only gates and forwards — the exactly-once guarantee relies on the
  store applying each delivered event once (which the test asserts for the
  hook side).
