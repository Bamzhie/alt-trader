# WS4 Mark / Index Price History — Design

**Status:** Design for implementation (2026-10-17)
**Date:** 2026-10-17
**Parent roadmap:** `docs/roadmap-status.md` WS4 (market-data history)
**Related:** `docs/ws4-settlement-history.md` (settlements),
`docs/quant-integration.md` (shadow-only contract)

## Goal

Archive MEXC's **fair (mark) price** and **index price** hourly candles
per coin, append-only, and serve them read-only. These give WS5 cost
research a mark price to value funding/episode notionals against, and
give basis (mark vs index) analysis a point-in-time series — never
estimated, never filled.

## Source (verified)

- Official MEXC contract API docs
  (<https://mexcdevelop.github.io/apidocs/contract_v1_en/>), section
  "Market endpoints":
  - **Get K-line data of the index price:**
    `GET /api/v1/contract/kline/index_price/{symbol}`.
  - **Get K-line data of the fair price:**
    `GET /api/v1/contract/kline/fair_price/{symbol}`.
  - Params: `symbol` (required); `interval` (default `Min1`, one of
    `Min1/Min5/Min15/Min30/Min60/Hour4/Hour8/Day1/Week1/Month1`);
    `start`, `end` (epoch **seconds**). Public — no authentication.
  - Rate limit: **20 times / 2 seconds**.
  - Response: the usual `{success, code, data}` envelope with `data`
    **columnar** (`time`, `open`, `high`, `low`, `close`, `vol`,
    `amount` arrays). Maximum **2000** candles per request; wider ranges
    are silently truncated, so history must be paged.
- Live probe 2026-10-17 (public, `BTC_USDT`, `interval=Min60`, 5h
  window): `success=true, code=0`; `data` keys
  `{time, open, high, low, close, vol, amount, realOpen, realHigh,
  realLow, realClose}`; `time` spacing exactly 3600 s. **The last candle
  was the still-forming current hour** (`time + 3600 > now`), so the
  collector must drop it. `vol`/`amount` were `0.0` (index/fair are
  computed series — no traded volume). The `real*` keys are undocumented
  extras.

## Archive schema

`data/prices/<series>/<COIN>.csv.gz` for `series` in `{mark, index}`,
header `ts,o,h,l,c`:

- `ts`: candle open time, epoch **seconds** (venue `time`).
- `o, h, l, c`: venue `open/high/low/close` for the candle, as finite
  floats.
- Only **closed** candles are archived: a candle with
  `ts + interval_secs > now` is dropped at parse time (mutable while
  forming, like the bar collector's forming-bar rule).
- `vol`/`amount` are **not** archived: the venue returns `0.0` for these
  series, and a zero there is not a measurement. The undocumented
  `real*` columns are not archived either (out of the documented
  contract; archiving unknown semantics would be inventing meaning).

`series` distinction is explicit in the path: `mark` (fair price, the
venue's mark for margin/liquidation) and `index` (the underlying index).
Nothing is merged.

Contract (mirrors `proto/collector.py` / `proto/funding.py` /
`proto/settlements.py`): append-only gzip CSV per coin, sorted on read,
deduped by `ts`, never rewritten. Malformed candles are skipped and
counted per category; transport/venue errors propagate so callers can
isolate them.

Interval is fixed at `1H` (`Min60`, 3600 s): 2000 candles/request ≈ 83
days, enough for matriculation and cheap per day; finer intervals can be
added as a separate archive later.

## Validation

Per candle, reject-and-count (never coerce):

- non-integer / non-positive `ts` → `bad_ts`;
- any of `o/h/l/c` non-finite or `<= 0` → `bad_price`;
- OHLC integrity violation (`high < max(o,c)`, `low > min(o,c)`, or
  `low > high`) → `bad_ohlc`;
- duplicate `ts` within a page → `duplicate_ts` (first wins).

Ragged columns, non-monotonic raw timestamps, or a missing required
column raise `MexcError` in the adapter (a whole response that violates
the columnar contract is a transport/shape failure, not a skippable
row), matching `mexc.klines`.

## Collection

- `mexc.price_klines(kind, symbol, interval, start, end)` — `kind` in
  `{mark, index}`; one request; columnar shape + raw-monotonic checks;
  returns `[{ts,o,h,l,c}]` (no forming-drop here; the module owns the
  `now`-relative drop so it is testable).
- `proto/prices.py`:
  - `parse_candles(symbol, rows, interval_secs, now)` → `(rows, skipped)`.
  - `read_prices(dir, series, coin)` / `append_prices(dir, series, coin,
    rows)` — the standard idempotent append (dedupe by `ts`).
  - `collect_coin(dir, series, coin, symbol, start, end, now)` — one
    bounded window; returns `{series, coin, new, skipped}`.
  - `backfill(dir, series, days, coins=None, now=None)` — pages
    **backward** in ≤2000-candle windows from `now` to `now - days`;
    per-coin failure captured, never aborts the run.
  - `collect_recent(dir, series, coins=None, now=None,
    lookback_s=RECENT_LOOKBACK_S)` — one recent window per coin
    (`[now - lookback_s, now]`), idempotent; self-heals short gaps.
  - `maybe_collect(dir, series=("mark","index"), every_s=COLLECT_EVERY_S,
    now=None)` — daemon entry with a throttle file; returns `{}` when
    not due.
- Rate limiting: every request goes through a dedicated
  `RollingRateLimiter(20, 2.0)` — the endpoint's documented cap — NOT
  `scan.LIMITER` (20 req/s for bars/depth). Centralized in
  `proto/ratelimit.py`.
- Daemon hook: on the collect cadence, `maybe_collect` is called inside
  its own try/except (a prices failure prints `prices FAILED` and never
  fails the bars sweep), throttled daily (`COLLECT_EVERY_S = 86400`) with
  a 48h lookback, like settlements. Universe = USDT perps minus
  synthetics (`canon`/`is_synthetic`), matching bars/funding/settlements.
- CLI: `python3 -m proto.prices backfill|collect-recent [--series
  mark,index] [--data-dir D] [--coins A,B] [--days N] [--json]`.

## Read API

`GET /prices?series=<mark|index>&coin=<COIN>&limit=<N>`, bearer-gated
like `/funding`:

- `series` required, must be `mark` or `index` → else `400`.
- `coin` required + allowlist `^[A-Za-z0-9_-]+$` before filesystem
  access → else `400`.
- Limits mirror `/funding`: default `"1000"`, integers 1–5000,
  non-integer/out-of-range → `400` (never clamp).
- Archive absent → `404` (`no prices for {series} {coin}`); present but
  unreadable → `503` (`prices unavailable`); zero-row file → `200` with
  `rows: []`, `count: 0`, null `data_as_of`/`last_ts`.
- Response data (exact keys): `{series, coin, rows: [{ts,o,h,l,c}],
  count, data_as_of, last_ts}` with `count == len(rows)` post-slice and
  `data_as_of == last_ts`.
- `prices_dir` resolved once at `serve()` via `_resolve_archive_dir`
  (default sibling `data/prices`; `--prices-dir` override, threaded
  through daemon parser + `_daemon_argv_for` so route and collector
  agree). No cache.

## Audit

`python3 -m proto.bar_quality --data-dir data/prices/mark --all --tf-sec
3600` (and `.../index`) audits the hourly grid; the header already uses
`ts`, so `audit_archive_file` needs no `ts_field` override. Deviations
surface as gaps/off-grid, never filled.

## Limitations / honesty

- Only closed candles; the forming hour is dropped by design.
- `vol`/`amount` are intentionally absent (no measurement for these
  series); the undocumented `real*` columns are not archived.
- History depth is bounded by the venue's retention and the 2000-candle
  per-request cap; deep backfill needs several paged requests.
- A gap longer than the daily lookback is NOT auto-healed; run `backfill`
  explicitly. Gaps stay gaps (auditable).
- Mark/index data is NOT fed into scoring, setups, episode costs,
  promotion gates, or quant emission in this increment — WS5 wires costs
  when that design lands.

## Verification criteria

1. Parser/validation tests: verbatim 2026-10-17 probe shape parses;
   forming-candle drop; ragged/missing column and non-monotonic raw raise;
   non-finite/negative price, bad OHLC, bad ts rejected and counted;
   duplicates counted; transport/rate-limit errors propagate.
2. Archive tests in tmp dirs: append/idempotence, no-rewrite hash, missing
   coin empty; production `data/` never touched.
3. Collection tests (mocked venue, loopback tripwire): bounded window,
   backward paging for backfill, per-coin failure isolation, daily
   throttle, request pacing through `PRICE_LIMITER` (not `scan.LIMITER`).
4. Daemon: failure isolation (sweep still completes); config alignment
   (`--prices-dir` accepted by daemon + service parsers, forwarded in
   `_daemon_argv_for`, sibling default).
5. API tests (loopback service, seeded tmp dirs): auth, envelope,
   shape/order/slice, limits, `series` validation, 404/503/400s, empty,
   traversal + filesystem tripwire, separation from `/funding` and
   `/settlements`, mark-vs-index separation.
6. `python3 tests/run_all.py` green; no frontend changes.
