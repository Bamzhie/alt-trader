# Stage 1: Bar-Archive Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a read-only audit of the 5m bar archives (duplicates, order, gaps, staleness, coverage) that never sorts or rewrites them.

**Architecture:** New stdlib-only module `proto/bar_quality.py` with a pure in-memory audit core, thin streaming file wrappers (one gzip pass, no materialized sort), and a CLI for human reporting. No changes to ingestion, service, or display in this stage — observe and report first; fixes ride later stages with audit data in hand.

**Tech Stack:** Python 3.10+ stdlib only (`csv`, `gzip`, `os`, `argparse`, `json`, `time`).

**Spec:** `docs/roadmap-status.md` (Workstream 1 entry + product goal). Non-goals stated there: no ingestion rewrite, no service route, no UI.

## Global Constraints

- Python 3.10+; stdlib only for the core — zero pip dependencies.
- Read-only stays read-only — no order placement, no key storage, no execution code, ever.
- No invented data: unavailable is explicit `None`, never a zero and never a guess.
- Timestamps are UTC-seconds integers.
- Tests are self-checking `tests/test_*.py` scripts printing `ALL PASS`; `python3 tests/run_all.py` must stay green.
- Never sort or rewrite the raw archives; audit reads stream in file order.

## Review Focus

- 27k-row real archives (BTC ~27k rows): the file audit must stream in one pass and match the in-memory audit exactly — pinned by Task 2's large-file parity test.
- Off-grid gaps (span not an exact multiple of 300s): must be reported with `missing: None`, never an invented count — pinned by Task 1's off-grid test.
- Empty archive / missing file: explicit empty result (`count: 0`, all lists empty, coverage `None`), never an exception — pinned by Task 2's empty test.
- Malformed rows (non-integer ts): skipped and counted in `invalid_rows`, file audit completes — pinned by Task 2's malformed-row test.
- Duplicate ts with differing OHLC: flagged `conflicting: true` (which row wins is a later-stage decision, not taken here) — pinned by Task 1's conflict test.

---

### Task 1: In-memory audit core

**Files:**
- Create: `proto/bar_quality.py`
- Test: `tests/test_bar_quality.py` (new; self-checking script style per `tests/test_watcher_triggers.py:14-24` — `check(name, cond)` helper, print `ALL PASS`, non-zero exit on failure)

**Interfaces:**
- Consumes: nothing (pure function over caller-supplied mappings).
- Produces (used by Tasks 2–3, exact shapes):
  - `audit_bar_sequence(bars, *, tf_sec=300, now=None) -> dict` where `bars` is an iterable of mappings with at least `"ts"`. `now` defaults to `time.time()`.
  - Result dict keys and types: `count: int`, `first_ts: int | None`, `last_ts: int | None`, `invalid_rows: int`, `duplicates: list[{ts: int, occurrences: int, conflicting: bool}]` (first-repeat order, `conflicting` true when any of o/h/l/c/vol/amount differs between same-ts rows), `out_of_order: list[{index: int, ts: int, prev_ts: int}]` (first 50, file-order index), `out_of_order_count: int`, `gaps: list[{from_ts: int, to_ts: int, span_s: int, missing: int | None}]` (first 50; `missing = span_s // tf_sec - 1` only when `span_s % tf_sec == 0`, else `None`), `missing_count: int` (sum of non-None), `age_s: int | None`, `stale: bool` (`now - last_ts > 2 * tf_sec`; `stale=False` when empty), `coverage: {first_ts, last_ts, span_s, unique: int, expected: int | None, ratio: float | None}` (`expected = span_s // tf_sec + 1` only when `span_s % tf_sec == 0`, else `None`).

- [ ] **Step 1: Write the failing test.** In `tests/test_bar_quality.py`, using the `check`/`FAILURES`/`ALL PASS` pattern from `tests/test_watcher_triggers.py`, cover: clean 5m grid (zero findings, `stale=False` with `now = last_ts + 300`); duplicate ts same values (`occurrences: 2`, `conflicting: False`); duplicate ts differing close (`conflicting: True`); out-of-order pair (exact `index`/`prev_ts`); exact-multiple gap (`missing: 2` for a 3-slot jump); off-grid gap (`missing: None`, still listed); stale file (`now = last_ts + 3600`, `stale=True`); empty input (all `None`/zero, `stale=False`).
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_bar_quality.py`. Expected: FAIL (import error, `proto.bar_quality` does not exist).
- [ ] **Step 3: Implement `audit_bar_sequence` in `proto/bar_quality.py`.** Single pass, no sorting; track `prev_ts`, `seen` dict ts→(count, first-row fingerprint), unique-sorted structure only for gap computation — build the sorted unique list (sorting the compact ts set, never the archive or the rows).
- [ ] **Step 4: Run test to verify it passes.** Run: `python3 tests/test_bar_quality.py`. Expected: `ALL PASS`, exit 0.
- [ ] **Step 5: Commit.** `git add proto/bar_quality.py tests/test_bar_quality.py && git commit -m "feat: add in-memory bar-archive audit core"`

### Task 2: Streaming file audit + incoming check

**Files:**
- Modify: `proto/bar_quality.py` (append; no edits to Task 1 shapes)
- Test: `tests/test_bar_quality.py` (append new `check` blocks)

**Interfaces:**
- Consumes: `audit_bar_sequence` result shape from Task 1 (file wrapper returns it plus `coin`/`path`).
- Produces:
  - `archive_max_ts(data_dir: str, coin: str) -> tuple[int | None, int]` — one streaming `gzip`/`csv` pass, no list materialization; `(None, 0)` when the file is absent.
  - `audit_archive_file(data_dir: str, coin: str, *, tf_sec=300, now=None, max_examples=50) -> dict` — Task-1 shape plus `coin: str`, `path: str`; honors `max_examples` for the three capped lists. Missing file → Task-1 empty shape (`count: 0`), never raises.
  - `check_incoming(archive_max_ts: int | None, bars) -> dict` with keys `new: list`, `at_or_below_max: list[{ts, reason}]` (`reason` is `"duplicate_or_reorder"` — honest: indistinguishable without a full set), `internal_duplicates: list[int]`, `internal_out_of_order: list[{index, ts, prev_ts}]`. O(len(bars)), touches no archive I/O.

- [ ] **Step 1: Write the failing test.** Append blocks: round-trip a tmp `csv.gz` with known defects through `audit_archive_file` and assert equality with `audit_bar_sequence` on the same rows; 20k-row synthetic clean file parity (count/coverage equal, completes); missing file returns `count: 0` without raising; malformed ts row counted in `invalid_rows` and skipped; `check_incoming(1000, [{ts:900},{ts:1100},{ts:1100},{ts:1050}])` → 1 new, 1 at-or-below-max, internal dupe + reorder flagged; `check_incoming(None, bars)` → all new.
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_bar_quality.py`. Expected: FAIL (names do not exist).
- [ ] **Step 3: Implement the three functions in `proto/bar_quality.py`.** File functions stream with `gzip.open(p, "rt")` + `csv.DictReader`; coerce `ts` via `int()`, count failures in `invalid_rows`.
- [ ] **Step 4: Run test to verify it passes.** Run: `python3 tests/test_bar_quality.py`. Expected: `ALL PASS`, exit 0.
- [ ] **Step 5: Commit.** `git add proto/bar_quality.py tests/test_bar_quality.py && git commit -m "feat: add streaming archive audit and incoming check"`

### Task 3: CLI report

**Files:**
- Modify: `proto/bar_quality.py` (add `main(argv=None) -> int` + `if __name__ == "__main__"` gate only)
- Test: `tests/test_bar_quality.py` (append blocks calling `main()` in-process on a tmp data dir)

**Interfaces:**
- Consumes: `audit_archive_file` from Task 2.
- Produces: `main(argv=None) -> int`. Flags: `--data-dir` (default `data/bars`), `--coins A,B` or `--all`, `--max-examples N` (default 50), `--json`. Human mode prints one line per coin (`<coin> rows=<n> dupes=<n> ooo=<n> gaps=<n>(missing=<n>) age=<s|-> <STALE|ok>`) plus a `SUMMARY` line (coins, coins_with_findings, total_missing, stalest); JSON mode prints the list of per-coin dicts. Exit `0` on success even with findings (findings are data, not errors); exit `2` when the data dir is unreadable or no coins selected.

- [ ] **Step 1: Write the failing test.** Build a tmp data dir with one clean coin and one defective coin; assert `main(["--data-dir", tmp]) == 0` and JSON output parses with the defective coin showing the expected `missing_count`; assert `main(["--data-dir", "/nonexistent"]) == 2`.
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_bar_quality.py`. Expected: FAIL (`main` does not exist).
- [ ] **Step 3: Implement `main` in `proto/bar_quality.py`.** `argparse` with the exact flags above; `--all` enumerates `*.csv.gz`; default coin set is `--all` when `--coins` is absent.
- [ ] **Step 4: Run test to verify it passes.** Run: `python3 tests/test_bar_quality.py` then `python3 -m proto.bar_quality --data-dir data/bars --all | tail -n 3` (read-only against live data; SUMMARY line appears). Expected: `ALL PASS` + SUMMARY line.
- [ ] **Step 5: Commit.** `git add proto/bar_quality.py tests/test_bar_quality.py && git commit -m "feat: add bar-quality CLI report"`

### Task 4: Full-suite verification + status update

**Files:**
- Modify: `docs/roadmap-status.md` (Stage 1 entry: outcome + audit findings summary + explicit deferred items)
- Test: `python3 tests/run_all.py` (no new file; auto-discovers `tests/test_bar_quality.py`)

**Interfaces:**
- Consumes: Tasks 1–3 deliverables.
- Produces: green suite + updated stage log. No new code.

- [ ] **Step 1: Run the aggregator.** Run: `python3 tests/run_all.py`. Expected: `ALL SUITES PASS (60 files)` (59 + new).
- [ ] **Step 2: Update `docs/roadmap-status.md`.** Record Stage 1 outcome, real-archive findings from the Task-3 live run (counts only, no new claims), and the deferred items (ingestion efficiency, `_resample_bars` order-assumption at `proto/service.py:419-440`, service/UI surfacing) as input to later stages.
- [ ] **Step 3: Commit.** `git add docs/roadmap-status.md && git commit -m "docs: record Stage 1 bar-audit outcome"`
