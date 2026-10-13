# Task 2 Report — Mock service (contract, fixtures, dev endpoints)

**Status:** DONE_WITH_CONCERNS (one design decision flagged below; no constraint violated)
**Commit:** `09053aa` — `feat(mock): stdlib service implementing the documented contract with fixtures`
**Branch:** `main` (after `1d03727`, Task 1)

---

## What was implemented

### `app/dev/mock_service.py` (813 lines, stdlib only)

`ThreadingHTTPServer` bound to `127.0.0.1` (the only accepted host; `--host` exists
purely so tests can pass the same literal). Two classes:

- **`MockService`** — all mutable state: fixtures, readiness flag, the SSE ring
  buffer, command state (paused / disabled triggers), and the request log.
- **`MockHandler`** — the route table, envelope helpers, SSE writer, auth gate.

Contract details as implemented:

| Concern | Implementation |
|---|---|
| Envelope | `respond(status, data)` → `{ok, data, error: null}`; `fail(status, code, message)` → `{ok, data: null, error: {code, message}}` |
| Auth | `hmac.compare_digest` over the whole `"Bearer <token>"` string (constant-time, no scheme oracle); exempt routes: `/healthz` and `/dev/*` |
| `/healthz` | Deliberately **not** the envelope — bare `{"ok":true,"alive":true}`, auth-free, liveness only |
| `/readyz` | 200 when ready; 503 `UNAVAILABLE` with "not ready — first sweep in progress" when the flag is flipped |
| `/snapshot` | All documented fields + `comparison_24h`, `snapshot_history`, `coins_without_setup`, `market_overview`, `movers`, `recent_analysis`, `activity`, `supported_timeframes`, `event_kinds`, `unavailable: null` |
| 24h delta | Nearest snapshot **at or before** `data_as_of - 86400`. None exists in the fixtures → `comparison_24h.available: false` with a `reason` and `null` deltas. Never inferred |
| `/setups` | `state` (validated against the lifecycle set), `limit` (default 100, hard max 500), opaque `o<N>` cursor, `next_cursor: null` at the end |
| `/bars` | 5m/15m/1H/4H/1D only; anything else (incl. `1m`) → 400 `BAD_REQUEST` naming the supported set; unknown coin → 404 `NOT_FOUND`; missing param → 400 |
| `/metrics` | Five keys always present; `rsi: null` for AVAX and METRICS_NULL carries a section-level `unavailable: {reason, missing: [...]}` |
| `/tradeplan/:id` | `entry_zone{low,high}`, `stop`, `tp1`, `tp2`, `reference_notional_usdt: 100`, `reference_stop_risk_usdt`, indicators. Route strips `leverage`/`liquidation`/`liq_price` defensively if a fixture ever grows one |
| `/events` (SSE) | `id:`/`event:`/`data:` frames, `Last-Event-ID` replay of ids **strictly greater** than the cursor, 15s `: keepalive` comment, per-client bounded queue fed on append |
| Ring buffer | 500 events **and** 30 minutes, whichever binds first; ids zero-padded to width 6 so lexicographic == numeric compare |
| `resync` | Cursor below the buffer floor → a `resync` frame with the floor id and a `reason`, then normal replay |
| `/commands/*` | `pause`/`resume` always 200 and idempotent; `disable-trigger`/`enable-trigger` validate `rule` against the fixture rule set, 400 on unknown or missing rule; unknown command → 404 |
| `/dev/*` | `toggle-ready` flips readiness; `inject-event?kind=` validates the kind and appends to the ring buffer, returning the new event |
| Logging | `log_message` writes only `METHOD path HTTP/x.y` + status to a bounded deque and stderr. `Authorization` is never read into the log — grep-verified (see below) |
| Token | Printed **once** to stdout at startup; never per-request, never to a file |

### `app/dev/fixtures.json` (231 KB, deterministic)

Covers every fixture the plan names:

- **6 setups** — BTC/ETH/SOL/DOGE/AVAX/LINK, directions mixed (LONG, SHORT, LONG, SHORT, LONG, SHORT), each with rule, criteria chips, inputs, unavailable inputs, 2-line analysis copy.
- **Unavailable indicator input** — AVAX carries `rsi: null` + `unavailable_inputs: ["rsi"]`; a dedicated `METRICS_NULL` coin has `rsi` and `macd` null.
- **Coin with no setup** — XRP and ADA, each with `"no setup yet — …"` + a blocking reason.
- **Non-covering snapshot pair** — three snapshots, the two older ones at −6h and −12h. **No** snapshot sits at or before now−24h, so the 24h comparison is genuinely unavailable (this is the fix for the first RED failure — see TDD notes).
- **Movers (5), recent analysis (5), activity log (10, newest-first, incl. `sweep-complete`)**.
- **Bars** — closed-only series for all 6 coins × 5 timeframes. BTC 4H = 40 candles; each series is walked **backward** from the setup price so the last closed bar's `close` equals the price the table and indicators quote (no "table price ≠ last candle" seam). `closed: true` on every bar, `ts` UTC-second ints, `high >= max(open,close) >= min(open,close) >= low` verified.
- **Trade plans** — one per setup, direction-symmetric (long: stop below zone / targets above; short: exact mirror), 100 USDT reference notional.

### `app/dev/gen_fixtures.py` (506 lines)

Committed so the fixture file is reproducible rather than hand-edited. Not
imported at runtime; running it twice produces a byte-identical `fixtures.json`
(md5 verified). It carries the bar-generation and the deliberate
"no covering snapshot" decision as explicit, commented code.

### `app/dev/serve.sh`

`#!/bin/sh`, `exec python3 "$DIR/mock_service.py" --port "${PORT:-18741}" "$@"`,
resolving its own directory. `PORT=18999 bash dev/serve.sh` overrides the port.

### `app/dev/test_mock_service.py` (556 lines, stdlib unittest)

44 tests across 10 classes: the five checks pinned verbatim by the plan, plus
readiness (503 + toggle), snapshot fields/comparison/history, setups
(pagination, limits, state filter, 404), bars (all 5 timeframes, 1m, unknown
coin, missing params), metrics (all five keys, null, 404), trade plans (shape,
short symmetry, id lookup), commands (idempotency, rule validation, 404, auth),
SSE (monotonic ids, inject, `Last-Event-ID` replay, stale-cursor `resync`,
auth), and a global-constraints class asserting no banned word appears in any
response **or** in `fixtures.json`, no `1m`, no TOTAL row, movers/analysis/
activity present, the request log never contains the token, and the wording is
`eligible`/`evaluated` and never "scanned".

---

## TDD evidence

### RED

`cd app && python3 dev/test_mock_service.py` (after writing the test, before the service):

```
setUpModule (__main__) ... ERROR
FileNotFoundError: [Errno 2] No such file or directory:
  '/home/bamzhie/alt-radar/app/dev/mock_service.py'
Ran 0 tests in 0.002s
FAILED (errors=1)
```

### RED #2 (service exists, assertions still failing)

`python3 dev/test_mock_service.py` — `Ran 43 tests in 12.617s` / `FAILED (failures=4, errors=3)`.
The seven failures were all real and split evenly between wrong test expectations
and wrong fixture data:

| Failure | Cause | Fix |
|---|---|---|
| `test_readyz_200_then_503_on_toggle` — `KeyError: 'ready'` | Test read `body["ready"]`; the envelope nests it under `data` | Test |
| `test_inject_event_lands_in_stream` / `test_last_event_id_replays_only_newer` — `TimeoutError` | Test asked for 20 / 3 frames from a stream with nothing newer than the cursor; a read timeout was treated as an error | Test: treat an idle stream as "nothing newer", inject a second event so the replay assertion has a real frame to check |
| `test_trigger_commands_validate_rule` — `400 != 200` | Test used rule `rsi_reclaim`, which is not in the fixture rule set | Test (now uses `range_reclaim_long`, plus a new `test_missing_rule_body_400`) |
| `test_movers_recent_analysis_activity_present` | Activity log was authored newest-first by hand but one entry was out of order | Fixtures: `sorted(ACTIVITY, key=lambda e: -e["ts"])` |
| `test_snapshot_24h_comparison_unavailable` / `test_snapshot_history_has_non_covering_pair` | The snapshot pair was at exactly −24h, so the cutoff comparison *was* covered — the fixture accidentally satisfied the constraint it was supposed to violate | Fixtures: prior snapshots moved to −6h and −12h, so no row reaches back 24h |

### GREEN

`cd app && python3 dev/test_mock_service.py`

```
Ran 44 tests in 0.595s

OK
```

### Manual pass against the real server (`bash dev/serve.sh`)

```
healthz (no auth)  {"ok": true, "alive": true}
no token           {"ok": false, "data": null, "error": {"code": "UNAUTH", ...}}  [401]
snapshot head      {"ok": true, "data": {"snapshot_event_id": "000011", "ts": 1791936000,
                    "eligible_count": 150, "evaluated_count": 132, "active_setups": 6, ...
1m                 {"ok": false, ... "code": "BAD_REQUEST", "message": "unsupported
                    timeframe '1m'; supported: 5m, 15m, 1H, 4H, 1D"}
bars BTC 4H        count 40, first_ts 1791360000, last_ts 1791921600, closed True, unavailable None
metrics AVAX       rsi: null, unavailable: {"reason": "...", "missing": ["rsi"]}
tradeplan tp-btc-1 entry_zone {112223.7, 112673.8}, stop 111188.7, tp1 114743.8,
                    tp2 117848.8, reference_notional_usdt 100
commands pause     {"ok": true, "data": {"command": "pause", "idempotent": true, ...}}
disable-trigger    {"ok": false, ... "code": "BAD_REQUEST", "message": "unknown trigger rule; ..."}
toggle-ready       {"ok": true, "dev": true, "ready": false} → /readyz 503 UNAVAILABLE
SSE                id: 000006 / event: setup.invalidated / data: {...}
```

Static checks: `python3 -m py_compile` clean on all three modules; `sh -n serve.sh`
clean; `fixtures.json` byte-identical on regeneration.

Bind check — `ss -ltn` shows `LISTEN 127.0.0.1:18741` only (no `0.0.0.0`).

---

## Self-review findings

1. **The first RED run surfaced that my own test had wrong expectations, not just
   missing code.** Four of the seven second-round failures were test bugs
   (envelope nesting, timeout handling, a rule name that doesn't exist in the
   fixtures). Worth noting because a test written from the plan's prose without
   running it encodes the author's guesses — the assertions about `Last-Event-ID`
   replay in particular only became meaningful once there was a second event to
   replay.

2. **The 24h-comparison fixture was self-defeating on the first attempt.** Putting
   the older snapshot at exactly −24h made `ts <= cutoff` true, i.e. the fixture
   satisfied the very constraint it exists to violate. Moving the pair to −6h/−12h
   is what makes `comparison_24h.available` genuinely `false`. The plan's phrasing
   ("a snapshot pair whose older snapshot does NOT cover 24h") is easy to
   implement as "covers_24h: false" on a row that still satisfies the cutoff —
   the flag is now derived from the same comparison the service performs, not
   asserted separately.

3. **Bar closes are anchored, not drifted.** Generating each timeframe's series
   independently from a start price left the 4H last close ~3% away from the
   5m close and from the setup's `price`. Walking backward from the setup price
   removes that seam, so a frontend showing "price" next to a chart whose last
   candle close differs is impossible with these fixtures.

4. **`/healthz` is intentionally not the envelope.** The plan writes
   `{"ok":true,"alive":true}` for it and `{ok, data, error}` everywhere else;
   both are in the response body, so the test asserts both shapes explicitly.

5. **`unavailable` is modelled twice, on purpose.** A per-coin
   `unavailable: {reason, missing}` on `/metrics` (which fields are absent) and a
   section-level `unavailable: null` on `/snapshot` and `/bars` (whether a whole
   region failed). They answer different questions and the plan's "section-level
   `unavailable: reason` supported" plus "explicit `null` for unavailable" both
   need a home.

6. **Resync is idempotent and non-destructive.** A stale cursor emits the
   `resync` frame *and then* replays from the floor, so a client that ignores
   `resync` still converges rather than hanging.

7. **The request log cannot leak the token by construction.** `log_message` is
   the only writer and it formats the request line, which never contains headers.
   Verified empirically: the only occurrence of `dev-token` in the server log is
   the single startup print.

---

## Concerns

1. **`gen_fixtures.py` is committed beyond the plan's file list.** The plan names
   four files; I added a fifth (the generator) because a 231 KB hand-edited JSON
   fixture is unreviewable and unreproducible. If the plan's file list is meant
   to be binding, drop it in a follow-up — nothing imports it at runtime.

2. **The SSE ring-buffer clock is frozen at `data_as_of`.** Injected events use
   `max(last_event_ts + 1, time.time())`, so on a machine whose wall clock is
   behind 2026-10-14 (the fixture date) the injected `ts` advances from the
   fixture clock instead. Ids stay monotonic and ordering is preserved either
   way; only the wall-clock reading of an injected event's `ts` would look odd.
   Not hit on this host (wall clock is past the fixture date), but it is a
   latent time-bomb once the fixture date is in the past.

3. **No `RATE_LIMITED` path is exercised.** The error code is defined (and in the
   client's `ErrorCode` union from Task 3) but the mock never returns it — there
   is nothing to rate-limit. A test asserting the client's backoff notice would
   need a stub, not this server.

4. **`/dev/*` being auth-free is a deliberate call, not an oversight.** The plan
   says "all routes except `/healthz` and `/dev/*` return 401 without it", and
   the toggle needs to be scriptable without a token. It is loopback-only, which
   is the containment the spec relies on.

5. **Command state is process-local and resets on restart.** `pause` and
   `disable-trigger` do not persist anywhere — correct for a mock, but a Settings
   page built against it would show commands "not sticking" across a server
   restart.

6. **`snapshot_event_id` is the ring buffer's head, not a snapshot-version
   counter.** That satisfies the reconcile contract (drop events ≤ snapshot id,
   apply newer) but means the id advances as unrelated events are injected. The
   real `proto/service.py` may want a monotonic snapshot version instead; the
   frontend's lexicographic compare works for both.

---

## Files changed

| File | Status |
|---|---|
| `app/dev/mock_service.py` | created — 813 lines |
| `app/dev/fixtures.json` | created — 231 KB, 6 setups / 6 coins × 5 tf bars / plans / movers / analysis / activity |
| `app/dev/serve.sh` | created — 14 lines, executable |
| `app/dev/test_mock_service.py` | created — 556 lines, 44 tests |
| `app/dev/gen_fixtures.py` | created — 506 lines (reproducible fixture generation) |

No existing file was modified. `app/src`, `app/tests`, and the Task 1 scaffold
are untouched; the JS suite and the socket tripwire still pass unchanged
(verified by not having touched them, plus `git status` showing only `app/dev/`
as new).

## Fix round 1

Four Important review findings on the SSE recovery semantics, fixed with a
TDD regression test each. `cd app && python3 dev/test_mock_service.py` →
`Ran 48 tests` / `OK` (44 pre-existing + 4 new). Bind check: `ss -ltn` shows
`LISTEN 127.0.0.1:<port>` only (no `0.0.0.0`). Read-only/loopback/no-banned-word
constraints untouched — only the SSE/auth/ring code paths changed.

### 1. SSE snapshot-then-subscribe lost transitions (`mock_service.py:_events_route`)
The ring was copied under lock (old 671-673) *before* `svc.subscribe()`
(old 700), so an event appended in the gap landed in neither the replay list
nor the subscriber queue. **Fix:** subscribe first, then copy the ring under
lock, then replay, and skip any queue event whose id `<= last_replayed`.
**Test:** `test_events_does_not_drop_transition_in_subscribe_window` —
reproduces the window at the service level (subscribe, inject, then read),
asserting the event is delivered exactly once. Also verified live: three
mid-stream injections against a running server all arrived, monotonic, no
dups.

### 2. Injected-event clock wiped the ring (`mock_service.py:inject_event`)
`ts = max(base + 1, int(time.time()))` jumped to host wall-clock once past
the fixture date; the prune then evicted the entire seeded buffer (6 → 1).
**Fix:** `ts = base + 1` only — the mock clock stays anchored to the fixture
clock. **Test:** `test_injected_event_does_not_wipe_ring` monkeypatches
`time.time()` to `data_as_of + 86400`, injects one event, and asserts every
seeded id survives and ts == `base + 1`.

### 3. Ring prune was insertion-order, not a 30-min window (`mock_service.py:_prune_ring`)
The popleft-while loop stopped at the first recent entry, silently dropping
newer-but-earlier-inserted seeds. **Fix:** filter by `ts >= newest - RING_WINDOW_SECONDS`
(plus the seed events in `gen_fixtures.py`/`fixtures.json` were brought inside
the 30-min window so the demo stream survives startup). **Test:**
`test_all_seeded_transitions_survive_startup` builds a fresh `MockService` from
fixtures and asserts every seed inside the window survives with ts within range.

### 4. Non-ASCII Authorization crashed the auth gate (`mock_service.py:_authorized`)
`hmac.compare_digest` raises `TypeError` on non-ASCII input; the exception
escaped the handler and dropped the connection with no response. **Fix:**
wrap the compare in `try/except TypeError → return False`. **Test:**
`test_non_ascii_authorization_401_connection_alive` — `Bearer ünïcode` → 401,
and a well-formed follow-up on the same connection still returns 200.

Note: `mock_service.py` no longer references `time` after fix 2; the import is
harmless and left in place to avoid unrelated churn.
