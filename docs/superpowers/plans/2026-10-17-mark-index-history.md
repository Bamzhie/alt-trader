# WS4 Mark/Index Price History — Implementation Plan

Spec: `docs/superpowers/specs/2026-10-17-mark-index-history-design.md` (authority).
TDD: failing tests first for each task.

## Task 1 — `proto/ratelimit.py` + `mexc.price_klines` + `proto/prices.py` + unit tests
- `RollingRateLimiter(rate, window_s)`: thread-safe sliding window, `acquire()`, `reset()`.
- `mexc.price_klines(kind, symbol, interval, start, end)`: `/kline/{fair_price|index_price}/{symbol}`,
  columnar shape + raw-monotonic validation, returns `[{ts,o,h,l,c}]`.
- `prices.py`: `parse_candles` (finite/positive, OHLC integrity, ts, forming-drop, dedupe),
  `read_prices`/`append_prices` (append-only, sorted-on-read, dedupe by ts),
  `collect_coin` (bounded window, PRICE_LIMITER), `backfill` (backward paging, per-coin isolation),
  `collect_recent` (one recent window), `maybe_collect` (daily throttle), CLI.
- Tests `tests/test_prices.py`: probe shape, forming drop, ragged/missing column,
  non-monotonic raw, bad price/OHLC/ts, duplicates, transport propagation, append/idempotence,
  no-rewrite hash, backfill paging + isolation, limiter pacing, throttle, sweep isolation,
  audit grid, dir alignment.
- Commit: `feat(WS4): mark/index price history archive`.

## Task 2 — `GET /prices` route + API tests
- Handler beside `_handle_settlements`: bearer gate, `series` ∈ {mark,index}, coin required +
  allowlist, limit 1000/1–5000/400s, 404/503/empty-200, exact keys
  `{series, coin, rows, count, data_as_of, last_ts}`, `prices_dir` injection + `--prices-dir`
  threading (daemon parser, `_DAEMON_ARGV_NAMES`, service.main resolution, serve/run_service).
- Tests `tests/test_prices_api.py` (loopback, seeded tmp): auth/shape/order/slice/series/limits/
  404/503/400s/empty/traversal + fs tripwire + separation both directions + mark-vs-index.
- Commit: `feat(WS4): prices read route`.

## Task 3 — docs + roadmap + suite + push
- Operator doc `docs/ws4-mark-index-history.md`; roadmap WS4 update; full suite; diff review;
  push; verify `ls-remote` == local HEAD. No service restart.
