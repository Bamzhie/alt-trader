# WS4e Funding Read Route Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the collected funding observation series at an authenticated `GET /funding` route mirroring `/bars` idioms.

**Architecture:** One new handler method beside `_handle_bars` in `proto/service.py` (same bearer gate, envelope, and 400/404/503 shapes), with `funding_dir` resolved once at `serve()` time as the sibling of the bars data dir and injected for tests. Direct `proto/funding.read_funding` per request, no cache. No ingestion, archive, mock, or UI changes.

**Tech Stack:** Python 3.10+ stdlib only; loopback-only self-checking service tests (`tests/test_*.py` + `tests/run_all.py`).

**Spec:** `docs/superpowers/specs/2026-10-16-funding-read-route-design.md` (approved; executors read both — on any brief-vs-spec conflict the SPEC wins).

## Global Constraints

- Python 3.10+; stdlib only for the core — zero pip dependencies.
- Read-only stays read-only — no order placement, no key storage, no execution code, ever.
- No invented data: empty is explicit (`rows: []`, `count: 0`, nulls), never zeros or guesses.
- Tests are self-checking `tests/test_*.py` scripts printing `ALL PASS` (loopback-only egress like `tests/test_service_lifecycle.py:39-75`); `python3 tests/run_all.py` must stay green.
- Never sort or rewrite the raw archives (`data/bars/`, `data/funding/`).

## Review Focus

- Traversal coin names (`..`, `a/b`, URL-encoded dots): rejected 400 before filesystem access, never served or escaped — pinned by Task 1's traversal test.
- Limit edges (`0`, `-1`, `5001`, `"abc"`, absent): 400/400/400/400/default-1000 — pinned by Task 1's limit tests.
- Truncated/corrupt gzip archive: 503 `funding unavailable`, never a 500 or traceback — pinned by Task 1's corrupt-file test.
- Header-only archive: 200 with `rows: []`, `count: 0`, null `data_as_of`/`last_ts` — pinned by Task 1's empty-archive test.
- Missing/wrong bearer token: 401 UNAUTH through the same gate as `/bars` — pinned by Task 1's auth test.

---

### Task 1: `GET /funding` handler + funding_dir wiring + service tests

**Files:**
- Modify: `proto/service.py` (`_handle_funding` beside `_handle_bars` ~line 1029; route dispatch beside line 666; `funding_dir` resolution at `serve()` time beside the bars data-dir plumbing)
- Test: `tests/test_funding_route.py` (new; loopback service against a seeded tmp funding dir, tripwire pattern from `tests/test_service_lifecycle.py:39-75`)

**Interfaces:**
- Consumes: `proto/funding.read_funding(data_dir, coin) -> list` (chronological; raises on unreadable files); existing `_deny(code, msg)`, `envelope(ok, data)`, bearer gate.
- Produces: `GET /funding?coin=&limit=` with exact response data `{coin, rows: [{ts, rate, cap}], count, data_as_of, last_ts}` (`count == len(rows)` post-slice, `data_as_of == last_ts`); `funding_dir` default = sibling of the configured bars data dir, injectable (tests seed tmp dirs, never production paths).

- [ ] **Step 1: Write the failing test.** In `tests/test_funding_route.py` (repo `check`/`FAILURES`/`ALL PASS` pattern, loopback-only): seeded tmp dir with a 1205-row coin, a header-only coin, and a corrupt coin; cover:
  - auth gate 401 (missing/wrong token);
  - 200 shape, ascending order, `count == len(rows)`, `data_as_of == last_ts`;
  - real default-1000 slice: no `limit` param over the 1205-row coin returns exactly the last 1000 rows (`count == 1000`, first row ts == 206th archived ts);
  - explicit `limit=2` returns the last 2 rows;
  - limits: `0`, `-5`, `5001`, `"abc"` each → 400 (reject invalid AND out-of-range; never clamp); exact messages `limit must be an integer` / `limit must be between 1 and 5000`;
  - missing coin (param absent) AND empty coin (`coin=`) → 400 with exact message `coin is required`;
  - well-formed but unarchived coin → 404 with exact message `no funding for {coin}`;
  - corrupt file → 503 with exact message `funding unavailable`;
  - empty archive → 200 with `rows: []`, `count: 0`, null `data_as_of`/`last_ts`;
  - traversal names (`..`, `a/b`, `%2e%2e`, `%2f`) → 400 with exact message `invalid coin name`, PLUS a filesystem tripwire: snapshot the tmp parent dir listing before/after the traversal requests and assert it is identical (no read or write escaped `funding_dir`);
  - envelope shape `{ok, data, error}` on success and all error paths.
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_funding_route.py`. Expected: FAIL (route answers 404 `no such route` — no handler yet; never a pass).
- [ ] **Step 3: Implement `_handle_funding` + route + funding_dir wiring in `proto/service.py`.** Order: bearer gate → `coin` required (400) → allowlist `^[A-Za-z0-9_-]+$` (400) → archive existence (404) → limit parse/clamp-to-400 (default `"1000"`, 1–5000) → `read_funding` in try/except (503) → ascending rows, last-`limit` slice, `count == len(rows)`. No cache; origin handling identical to authed routes; the stricter coin check applies to this route only (`/bars` untouched).
- [ ] **Step 4: Run test to verify it passes.** Run: `python3 tests/test_funding_route.py` then `python3 tests/run_all.py`. Expected: `ALL PASS` then `ALL SUITES PASS` (count = previous + 1).
- [ ] **Step 5: Commit.** `git add proto/service.py tests/test_funding_route.py && git commit -m "feat(WS4e): GET /funding read route over observation archive"`

### Task 2: Full verification + status + live check

**Files:**
- Modify: `docs/roadmap-status.md` (WS4 section: WS4e delivered line only; Outstanding list carried forward verbatim-or-truer)
- Test: none new (reuses Task 1 suite + live service)

**Interfaces:**
- Consumes: Task 1's merged route.
- Produces: green suite evidence + truthful status + live confirmation.

- [ ] **Step 1: Run the full suite.** Run: `python3 tests/run_all.py`. Expected: `ALL SUITES PASS` (previous count + 1).
- [ ] **Step 2: Live check against the deployed service.** Do NOT restart the service in this session without explicit user go-ahead: if the running process predates the Task 1 commit, record "pending deploy" in the status update and stop — no restart, no force. Only if the process already contains Task 1, `curl` an actually-observed coin with bearer auth; expect 200, ascending rows, `count == len(rows)`.
- [ ] **Step 3: Update `docs/roadmap-status.md` and commit.** WS4e delivered line (commit sha, what was verified incl. live or pending-deploy); carry the Outstanding list (settlement, mark/index, book/spread, OI series, WS5 estimators, WS6 UI, WS7 persistence) forward untouched. `git add docs/roadmap-status.md && git commit -m "docs(WS4e): funding read route delivered"`
