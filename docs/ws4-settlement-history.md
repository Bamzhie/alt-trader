# WS4 Funding Settlement History

Venue-reported MEXC funding settlements, archived append-only and served
read-only. This document is the operator reference: source semantics,
schema, commands, route contract, limitations, and the no-estimation rule.

## Source semantics

- Public endpoint (no authentication):
  `GET /api/v1/contract/funding_rate/history?symbol=<SYM>&page_num=<N>&page_size=<M>`.
- Documented (official MEXC contract API docs, "Get contract funding
  rate history"): `symbol` required, `page_num` required, `page_size`
  required with maximum 1000; rate limit 20 times / 2 seconds;
  envelope `{success, code, data}` with
  `data = {pageSize, totalCount, totalPage, currentPage, resultList}`;
  rows `{symbol, fundingRate, settleTime}`.
- Verified live 2026-10-17 (`BTC_USDT`, page 1): `success=true,
  code=0`; rows additionally carry per-row `collectCycle` (absent from
  the docs example — recorded as context, never assumed uniform);
  `settleTime` in milliseconds on an exact 8h grid (28,800,000 ms
  spacing); `fundingRate` decimal, scientific notation occurs
  (`4.2e-05`).

## Archive schema

`data/funding_settlements/<COIN>.csv.gz`, header
`settle_ts,settled_rate,cycle_hours`:

- `settle_ts`: venue `settleTime` in whole UTC seconds (integer
  division of the millisecond value). The venue's timestamp meaning is
  preserved; nothing is re-based to observation time.
- `settled_rate`: venue `fundingRate` as float; must be finite.
- `cycle_hours`: venue per-row `collectCycle` when a positive integer,
  else empty (row kept — rate/settlement is venue-stated, cycle is
  context). Coins whose cycle changes archive correctly because every
  row is self-describing.

Contract (mirrors `proto/funding.py`): append-only, sorted on read,
deduped by `settle_ts` (re-fetching records nothing; same-ts conflict
keeps the first row and counts it), never rewritten. Malformed rows
are skipped and counted per category (`bad_rate`, `bad_settle_time`,
`symbol_mismatch`, `duplicate_ts`, ...); transport/venue errors
propagate to the caller (counted, never silent-empty).

Coin set and naming mirror `collect_funding` (USDT perps,
`canon`/`is_synthetic` exclusion), so settlement files match bar and
funding-observation names coin for coin.

## Commands

```bash
# Backfill the universe (or explicit coins), 3 pages/coin default:
python3 -m proto.settlements backfill --data-dir data/funding_settlements
python3 -m proto.settlements backfill --coins BTC,ETH --max-pages 5
python3 -m proto.settlements backfill --coins DOGE:DOGE_USDT --json

# Incremental pass (page 1 per coin, keeps rows newer than archived max):
python3 -m proto.settlements collect-recent --data-dir data/funding_settlements
```

The daemon runs the incremental pass on the collect cadence with a 24h
throttle file (`data/funding_settlements/.last_collect`), guarded like
funding: a settlements failure prints and never fails the bars sweep.

## Route contract

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
- `settlements_dir` resolved once at `serve()` (default
  `data/funding_settlements` sibling, `--settlements-dir` override).
  No cache (files hold ~3 rows/day).

## Audit

`python3 -m proto.bar_quality --data-dir data/funding_settlements
--all --tf-sec 28800` audits the 8h settle grid per coin — but the
settlement header uses `settle_ts`, so programmatic audits pass
`ts_field="settle_ts"` to `audit_archive_file`. Deviations surface as
gaps/off-grid findings, never filled.

## Limitations

- History starts at venue availability per coin (recent listings have
  short series — honest, not a defect).
- `collectCycle` context may be absent on some rows (kept with empty
  context).
- No settlement read exists for coins the venue never listed; 404 is
  the correct answer there.
- The route serves archived history only; it does not fetch the venue
  on demand.

## No-estimation rule

Nothing in this increment estimates, interpolates, or fills: no
`rate_applied`, no observation backfill, no schedule derivation. Gaps
stay gaps. Settlement data is NOT fed into scoring, setups, episode
costs, promotion gates, or quant emission — it is available for future
WS5 cost research, which wires costs explicitly when it lands.
