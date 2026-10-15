# WS4e: Funding Read Route — Design

**Status:** Proposed for review (2026-10-16)
**Date:** 2026-10-16
**Parent roadmap:** `docs/roadmap-status.md` (Workstream 4; first funded increment is WS4e per 2026-10-16 decomposition: route, settlement, mark/index, book/spread, OI)
**Consumes:** `proto/funding.py` observation archives (`data/funding/<COIN>.csv.gz`, schema `ts,rate,cap`, sorted-on-read, deduped by ts)

## Goal

Serve the already-collected funding observation series over the
authenticated loopback API so WS6 evidence display (and any research
consumer) can read point-in-time funding without touching the
collector, the archives, or the venue.

## Non-goals (explicit)

- No UI, no frontend API client, no mock-service route (all ride WS6
  display work when evidence needs are known).
- No settlement derivation, no backfill, no mark/index/book/OI work.
- No changes to ingestion, existing routes, archive formats, or
  cadences.

## Architecture

One new authed route handled beside `_handle_bars`, sharing its
plumbing idioms and nothing else:

```text
GET /funding?coin=BTC&limit=1000
  → bearer gate (same as /bars)
  → coin validation → archive lookup in funding_dir
  → proto/funding.read_funding (sorted-on-read contract)
  → envelope, ascending rows, last-`limit` slice
```

## Endpoint contract

- `GET /funding?coin=<COIN>&limit=<N>`, bearer token required
  (identical gate to `/bars`; `GET /healthz` stays the only authed
  exception — unchanged).
- `coin` is REQUIRED: absent or empty → `400 BAD_REQUEST`
  (`coin is required`), mirroring `/bars` (`proto/service.py:1029`).
- Rows ascending by `ts` (archive read order; `read_funding`
  guarantees chronological), sliced to the last `limit` rows.
- Response data (exact keys, no extras):
  `{coin, rows: [{ts, rate, cap}], count, data_as_of, last_ts}`
  where `data_as_of == last_ts` (mirroring `/bars`), and both are
  `null` when the archive holds zero rows.

## Limit behavior (mirrors `/bars` exactly)

- Default `"1000"`, minimum 1, maximum 5000; non-integer →
  `400 BAD_REQUEST` (`limit must be an integer`); out of range →
  `400 BAD_REQUEST` (`limit must be between 1 and 5000`).
  Same values as `/bars` (`proto/service.py:1045-1054`) deliberately:
  one client idiom across series routes, and 1000 funding rows cover
  months at one row per collect cycle.

## Coin validation and archive cases

- Safe-coin allowlist checked BEFORE filesystem access:
  `^[A-Za-z0-9_-]+$`, else `400 BAD_REQUEST`
  (`invalid coin name`). This is stricter than `/bars` (which joins
  the name unchecked) — justified: it closes path traversal for the
  new route without touching `/bars` behavior.
- Archive file absent → `404 NOT_FOUND` (`no funding for {coin}`),
  mirroring `/bars` 404 semantics. A coin never observed and a coin
  observed-then-gone are indistinguishable here by design; both mean
  "no series to serve".
- Archive present but unreadable (corrupt gzip, permissions) → `503
  UNAVAILABLE` (`funding unavailable`), mirroring `/bars` 503. Never
  a 500, never a stack trace, never partial rows.
- Archive present with zero data rows → `200` with `rows: []`,
  `count: 0`, `data_as_of: null`, `last_ts: null` (explicit emptiness,
  never an error and never invented points).

## Funding-directory injection and defaults

- The handler receives `funding_dir` the same way `_handle_bars`
  receives its data dir (resolved once at `serve()` time, not per
  request). Default: sibling of the configured bars data dir
  (`data/funding` beside `data/bars`, matching `proto/funding.py`
  `FUNDING_DIR`), overridable by the same configuration surface that
  sets the bars dir. Tests inject a tmp dir — no production paths in
  test code.

## Read path and performance

- Direct `read_funding` per request, NO cache. Funding files hold one
  row per collect cycle (hundreds of rows, kilobytes) — unlike 27k-row
  bar files that justified `_BAR_READ_CACHE`. If load ever demands
  it, caching follows the same mtime+size pattern; not this increment.
- CORS/origin handling identical to existing authed routes; no new
  origins, no weakening.

## Testing

- Python service-level tests with a seeded tmp funding dir (injected
  funding_dir): auth gate, 200 shape/order/slice, default/limit
  clamp/integer errors, missing coin 404, corrupt-file 503,
  empty-archive 200-with-nulls, traversal-name 400, envelope shape.
- `python3 tests/run_all.py` stays green (auto-discovery by
  `test_*.py` naming; no aggregator change).

## Scope boundaries

- Read-only: the route never writes archives, never calls the venue.
- Does not alter `promotion.py`, the registry, the daemon, or any
  existing route's behavior (the stricter coin check applies to the
  new route only).

## Verification criteria

1. New service tests → ALL PASS (gate, shape, limits, 404/503/400
   paths, traversal rejection).
2. `python3 tests/run_all.py` stays green.
3. Live check post-deploy: seeded coin reads back its rows ascending
   through the real service with bearer auth (R1-style ops verify, no
   code).
