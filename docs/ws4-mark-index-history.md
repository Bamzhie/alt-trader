# WS4 Mark / Index Price History

Venue-reported MEXC **fair (mark) price** and **index price** hourly candles,
archived append-only and served read-only. This is the operator reference:
source semantics, schema, commands, route contract, limitations, and the
no-estimation rule.

Design: `docs/superpowers/specs/2026-10-17-mark-index-history-design.md`.

## Source semantics

- Public endpoints (no authentication):
  - `GET /api/v1/contract/kline/index_price/{symbol}` — index price candles.
  - `GET /api/v1/contract/kline/fair_price/{symbol}` — fair (mark) price candles.
- Documented (official MEXC contract API docs, "Get K-line data of the index
  price" / "... of the fair price"): `symbol` required; `interval`
  (`Min1…Month1`); `start`/`end` epoch **seconds**; **2000** candles per
  request max (wider ranges truncated); rate limit **20 times / 2 seconds**.
- Envelope `{success, code, data}` with **columnar** `data`
  (`time`, `open`, `high`, `low`, `close`, `vol`, `amount`, …).
- Verified live 2026-10-17 (`BTC_USDT`, `Min60`, 5h window): `success=true,
  code=0`; `time` spacing exactly 3600 s; the **last candle was the
  still-forming current hour** (`time + 3600 > now`) so it is dropped;
  `vol`/`amount` were `0.0` (computed series — no traded volume); live rows
  also carry undocumented `realOpen/realHigh/realLow/realClose`.

## Archive schema

`data/prices/<series>/<COIN>.csv.gz`, `series` in `{mark, index}`, header
`ts,o,h,l,c`:

- `ts` — candle open time, epoch **seconds** (venue `time`).
- `o, h, l, c` — venue open/high/low/close for that candle, finite floats.
- Only **closed** candles are written: a candle with `ts + 3600 > now` is
  dropped at parse time (mutable while forming).
- `vol`/`amount` and the undocumented `real*` columns are **not** archived:
  a zero there is not a measurement, and unknown semantics are never stored.

`mark` is the venue fair price (used for margin/liquidation); `index` is the
venue index. They are separate directories and never merged.

Append-only gzip CSV per coin per series, sorted on read, deduped by `ts`,
never rewritten. A malformed candle is skipped and counted per category
(`bad_ts`, `bad_price`, `bad_ohlc`, `duplicate_ts`); transport errors
propagate so callers can isolate them.

## Interval

Fixed at `1H` (`Min60`, 3600 s): 2000 candles/request ≈ 83 days, cheap per
day. A finer interval can be added later as its own archive.

## Commands

```bash
# One-off deep backfill (universe, or explicit coins), paged backward:
python3 -m proto.prices backfill --series mark,index --days 30
python3 -m proto.prices backfill --series mark --days 7 --coins BTC,ETH,SOL

# Incremental recent-window pass (what the daemon hook calls):
python3 -m proto.prices collect-recent --series mark,index

# Audit the hourly grid (one series dir at a time):
python3 -m proto.bar_quality --data-dir data/prices/mark  --all --tf-sec 3600
python3 -m proto.bar_quality --data-dir data/prices/index --all --tf-sec 3600
```

The header already uses `ts`, so no `--ts-field` override is needed for price
archives. Gaps and off-grid candles surface as findings; they are never
filled.

## Daemon hook

```bash
python3 -m proto.service --no-scan --prices-dir data/prices
```

The daemon runs `collect_recent` on the collect cadence with a 24h throttle
file (`data/prices/.last_collect`), guarded like funding/settlements: a prices
failure prints `prices FAILED` and never fails the bars sweep. The throttle
uses total-failure-aware semantics: if every coin errors on a collect pass
(wholesale venue outage), the throttle file is NOT written, so the next sweep
retries immediately; if at least one coin succeeds, the throttle is marked and
the failing coins retry on the next explicit backfill/collect-recent run.

## Read route

`GET /prices?series=<mark|index>&coin=<COIN>&limit=<N>` (bearer-gated):

- `series` required, one of `mark`/`index`; otherwise `400`.
- `coin` required, allowlist `^[A-Za-z0-9_-]+$` applied before any filesystem
  access; otherwise `400`.
- `limit` default `1000`, integers 1–5000; out-of-range or non-integer is
  `400` (never clamped).
- Missing archive → `404`; unreadable archive → `503`; header-only archive →
  `200` with `rows: []`, `count: 0`, null `data_as_of`/`last_ts`.
- Response data: `{series, coin, rows:[{ts,o,h,l,c}], count, data_as_of,
  last_ts}`; rows ascending; `count == len(rows)`; `data_as_of == last_ts`.

`prices_dir` defaults to the `prices` sibling of the bars dir (matching
`proto.prices.PRICES_DIR`); the embedded loop and the route resolve to the
same directory via `--prices-dir`.

## Collection

- **Daemon hook**: on the collect cadence, `maybe_collect` runs a bounded
  recent window per coin/series, throttled to **daily** (`COLLECT_EVERY_S =
  86400`) with a **48 h** lookback (`RECENT_LOOKBACK_S`). It is guarded on its
  own — a prices failure prints `prices FAILED` and never fails the bars
  sweep. The throttle uses total-failure-aware semantics: if every coin
  errors on a collect pass (wholesale venue outage), the throttle file is
  NOT written, so the next sweep retries immediately; if at least one coin
  succeeds, the throttle is marked and the failing coins retry on the next
  explicit backfill/collect-recent run.
- Every venue request is paced through `RollingRateLimiter(20, 2.0)` — the
  endpoint's documented cap — not `scan.LIMITER` (20 req/s).
- Universe = USDT perps minus synthetics, the same filter as
  bars/funding/settlements.

## Limitations / honesty

- Only closed candles; the forming hour is dropped by design.
- No `vol`/`amount` (no measurement for these series).
- History depth is bounded by venue retention and the 2000-candle per-request
  cap; deep backfill needs several paged requests.
- A gap longer than the daily lookback is **not** auto-healed; run `backfill`
  explicitly. Gaps stay gaps (auditable).
- This data is **not** yet fed into scoring, setups, episode costs, promotion
  gates, or quant emission. WS5 wires cost attribution when that design lands.
