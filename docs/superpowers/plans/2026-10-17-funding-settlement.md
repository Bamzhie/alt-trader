# WS4 Settlement History — Implementation Plan (concise)

Spec: `docs/superpowers/specs/2026-10-17-funding-settlement-design.md` (authority; conflicts resolve to the spec).

## Task 1 — `mexc.funding_history` + `proto/settlements.py` + unit/archive tests
- `mexc.funding_history(symbol, page_num, page_size=1000)`: envelope/pagination validation, per-row shape; transport errors propagate.
- `settlements`: `parse_page` (ms→s, finite-rate, symbol match, cycle context), `collect_coin` (paged, truncated flag, dedupe by settle_ts, conflict counting), `backfill` + `collect_recent`, CLI `backfill`/`collect-recent`, archive read/append mirroring `funding.py`.
- Tests `tests/test_settlements.py` (mocked venue, tmp dirs): parsing/pagination/duplicates, empty/malformed/non-finite/out-of-order/symbol-mismatch, transport + rate-limit errors, append/idempotence, no-rewrite hash check, verbatim 2026-10-17 probe fixture accepted.
- TDD: failing tests first. Commit: `feat(WS4): venue-reported settlement archive`.

## Task 2 — Daemon daily hook (guarded, 24h throttle file)
- Collect-cadence block with `.last_collect` throttle (skip < 86400s), own try/except printing `settlements FAILED`, never touching sweep flags.
- Tests: throttle respected (recent file → zero venue calls), stale file → collects, failure → sweep still completes.
- Commit: `feat(WS4): guarded settlement collect hook`.

## Task 3 — `GET /settlements` route + API tests
- Handler beside `_handle_funding`: bearer gate, coin required + allowlist, limit default 1000 / 1–5000 / 400s, 404/503/empty-200, exact keys `{coin, rows, count, data_as_of, last_ts}`, `settlements_dir` injection (sibling default), no cache.
- Tests `tests/test_settlements_api.py` (loopback, seeded tmp): auth/shape/order/slice/limits/404/503/400s/empty/traversal/envelope + `/funding` separation both directions.
- Commit: `feat(WS4): settlements read route`.

## Task 4 — Docs, audit note, suite, push
- `docs/ws4-settlement-history.md` (schema, commands, contract, limitations, no-estimation rule); roadmap WS4 section (settlement delivered; mark/book/OI still outstanding).
- `python3 tests/run_all.py` green; final diff review (no data mutation, no secrets, no scope creep); commit docs; `git push origin main`; verify `ls-remote` match.
- No service restart (deploy stays pending).
