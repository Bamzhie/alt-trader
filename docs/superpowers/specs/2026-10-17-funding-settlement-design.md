# WS4 Settlement History (venue-reported) — Design

**Status:** Approved direction, spec for implementation (2026-10-17)
**Date:** 2026-10-17
**Parent roadmap:** `docs/roadmap-status.md` WS4 (settlement history; mark/index, book/spread, OI remain separate items)
**Supersedes:** schedule-anchored derivation sketch (2026-10-16 chat design) — derivation would have estimated; the venue reports settled rates, so no estimation exists in this design.

## Goal

Archive MEXC's venue-reported funding settlement history per coin and
serve it read-only, giving future WS5 cost research exact settled rates
with settle timestamps — never estimates, never filled gaps.

## Source (verified)

- Official MEXC contract API docs ("Get contract funding rate
  history"): `GET /api/v1/contract/funding_rate/history` with
  `symbol` (required), `page_num` (required), `page_size` (required,
  max 1000). Public market endpoint — no authentication. Documented
  rate limit 20 times / 2 seconds. Response envelope
  `{success, code, data}` with
  `data = {pageSize, totalCount, totalPage, currentPage, resultList}`.
- Live probe 2026-10-17 (one public request, `BTC_USDT`, page 1,
  size 3): `success=true, code=0`; rows
  `{symbol, fundingRate, settleTime, collectCycle}`, e.g.
  `fundingRate: 4.2e-05` (scientific notation occurs),
  `settleTime: 1792166400000` (milliseconds), `collectCycle: 8`.
  Consecutive rows spaced exactly 28,800,000 ms (8h).
- `collectCycle` appears per row in live responses though absent from
  the docs example: recorded as row context, never assumed uniform.

## Architecture

```text
venue /funding_rate/history (paged, per coin)
  → proto/settlements.py (fetch + validate + idempotent append)
  → data/funding_settlements/<COIN>.csv.gz
  → GET /settlements?coin=&limit= (read-only, mirrors /funding)
```

New module `proto/settlements.py` (stdlib only), reusing the archive
idiom of `proto/funding.py` and `proto/collector.py` (append-only
gzip CSV, sorted-on-read, deduped key, never rewritten) and the
`mexc._get` transport (retries, no keys). New service route beside
`_handle_funding`; no changes to `/funding`, bars, quant, episodes,
or scoring.

## Archive schema

`data/funding_settlements/<COIN>.csv.gz`, header
`settle_ts,settled_rate,cycle_hours`:

- `settle_ts`: venue `settleTime` converted ms → UTC seconds (integer
  division; the row is rejected if `settleTime` is not integer-like).
  The venue's timestamp meaning and 8h grid are preserved, never
  re-based to observation time.
- `settled_rate`: venue `fundingRate` as float; must be finite
  (NaN/inf/non-numeric → row rejected and counted, never zero).
  Scientific notation accepted (standard float parsing).
- `cycle_hours`: venue per-row `collectCycle` when present and a
  positive integer, else null-equivalent empty field (row still kept —
  the rate/settlement is venue-stated; the cycle is context). A coin
  whose cycle changes over time archives correctly because every row
  carries its own context.

Coin set and naming mirror `proto/funding.collect_funding` exactly
(USDT perps, `canon`/`is_synthetic` exclusion), so settlement files
match bar and funding-observation names coin for coin.

## Collection and backfill

- `fetch_history(symbol, page_num, page_size=1000)` (in `mexc.py`,
  beside `funding_rate`): one page; validates envelope
  (`success`/`code`), pagination echo, per-row `symbol` match
  against the request (mismatch → row counted malformed), shapes
  above. Transport/venue errors propagate (no silent empty).
- `collect_coin(data_dir, coin, symbol, max_pages)`: pages 1..N until
  `currentPage >= totalPage` or `max_pages` hit (a hit is reported,
  never silently stops early — the caller sees `truncated: true`);
  idempotent append deduped by `settle_ts` (re-fetching records
  nothing new). Returns `{coin, new, skipped_malformed, pages,
  truncated}`.
- `backfill(data_dir, max_pages_per_coin, coins=None)`: every eligible
  coin (or an explicit list); per-coin failures counted, never abort
  the run. CLI: `python3 -m proto.settlements backfill [--data-dir]
  [--coins A,B] [--max-pages N] [--json]`.
- Forward incremental: `collect_recent(data_dir)` fetches page 1 per
  coin and keeps rows newer than the archived max (same dedupe path).
  Daemon wiring on the collect cadence with a 24h throttle file
  (`data/funding_settlements/.last_collect`, epoch seconds; skip when
  younger than 86400s), guarded exactly like funding (a settlement
  failure prints and never fails the bars sweep). Rationale for daily:
  3 settles/day/coin make sub-daily polling pure venue load.
- Out-of-order pages/rows: sorted in memory before append; duplicate
  `settle_ts` keeps the first-seen row and counts the conflict (same
  philosophy as `bar_quality` duplicates — reported, never merged).
- No estimation anywhere: no `rate_applied`, no interpolation, no
  observation backfill. Gaps stay gaps (auditable, see below).

## Read API

`GET /settlements?coin=<COIN>&limit=<N>`, bearer-gated like `/funding`:

- Limits mirror `/funding` exactly: default `"1000"`, integers 1–5000,
  non-integer/out-of-range → `400 BAD_REQUEST` (never clamp).
- Coin allowlist `^[A-Za-z0-9_-]+$` before filesystem access (400);
  absent/empty coin → 400; archive absent → 404
  (`no settlements for {coin}`); present-but-unreadable → 503
  (`settlements unavailable`); zero-row file → 200 with `rows: []`,
  `count: 0`, null `data_as_of`/`last_ts`.
- Response data (exact keys): `{coin, rows: [{settle_ts,
  settled_rate, cycle_hours}], count, data_as_of, last_ts}` with
  `count == len(rows)` post-slice and `data_as_of == last_ts`.
- `funding_dir`-style injection: `settlements_dir` resolved once at
  `serve()` time (default sibling `data/funding_settlements`,
  overridable the same way); tests seed tmp dirs. No cache (files are
  small: ~3 rows/day).

## Audit and honesty

- `bar_quality --tf-sec 28800` audits the 8h settle grid per coin
  (deviations surface as gaps/off-grid, never filled).
- Distinguish explicitly: "no settlements returned" (venue empty page
  → zero new rows, reported), transport error (propagates, counted),
  malformed rows (counted per category), coin-without-archive (404).
- Source semantics documented in `docs/quant-integration.md`? No —
  in the new `docs/ws4-settlement-history.md` (schema, commands,
  route contract, limitations, no-estimation rule).

## Scope boundaries

- Read-only venue use, no keys, no account endpoints, no trading code.
- Settlement data is NOT fed into scoring, setups, episode costs,
  promotion gates, or quant emission (availability for future cost
  research only — WS5 wires costs, not this increment).
- No mark/index, book/spread, OI, UI, mock-service, or `/funding`
  semantic changes.

## Verification criteria

1. Parser/validation tests: envelope/pagination/duplicates, empty
   history, malformed rows, non-finite numerics, ms conversion,
   out-of-order rows, symbol mismatch, transport/rate-limit errors —
   all mocked (loopback tripwire), real validator behavior.
2. Archive tests in tmp dirs: append/idempotence (re-collect records
   zero new), no rewrite (byte-hash of pre-existing fixture unchanged
   after appends), production `data/` never touched.
3. API tests (loopback service, seeded tmp dir): auth, shape/order/
   slice, limits, 404/503/400s, empty archive, traversal, envelope,
   and `/funding`-route separation (funding fixtures unaffected and
   vice versa).
4. `python3 tests/run_all.py` green; no frontend changes so no
   app checks.
5. Live-shape conformance: the committed parser accepts the verbatim
   2026-10-17 probe response (pinned as a fixture).
