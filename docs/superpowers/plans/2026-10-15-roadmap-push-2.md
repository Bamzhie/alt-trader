# Roadmap Push 2: Bounded Remainders (R1–R4) + WS5-1 Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four bounded remainders (live-service deployment verification, scan-phase durations, stale-coins investigation, WS7 decision log) and build the WS5 increment-1 hypothesis registry, keeping every other roadmap workstream visibly outstanding.

**Architecture:** Ops-first (R1 deploys already-committed code, no new code), then small additive changes only: monotonic phase timers on the existing `scan_once` blocks, a new stdlib validator for an append-only decision log, and the spec'd registry reader/validator plus one example hypothesis. No ingestion, archive, service-route, or UI changes.

**Tech Stack:** Python 3.10+ stdlib only; systemd user service ops; self-checking `tests/test_*.py` scripts aggregated by `tests/run_all.py`.

**Spec:** `docs/superpowers/specs/2026-10-15-hypothesis-registry-design.md` (approved; Tasks 4–6 argue from it, so it travels with this plan — executors read both). Background: `docs/roadmap-status.md`, `docs/quant-analytics-system-proposal.md` §5.

## Global Constraints

- Python 3.10+; stdlib only for the core — zero pip dependencies.
- Read-only stays read-only — no order placement, no key storage, no execution code, ever.
- No invented data: unavailable is explicit `None`, never a zero and never a guess.
- Timestamps are UTC-seconds integers (registry window dates are UTC `YYYY-MM-DD` day boundaries per the spec).
- Tests are self-checking `tests/test_*.py` scripts printing `ALL PASS`; `python3 tests/run_all.py` must stay green.
- Never sort or rewrite the raw archives (`data/bars/`, `data/funding/`).

## Outstanding workstreams (NOT in this plan — kept visible)

- WS3 beyond R2/R3: 1m/venue-probe design stage; provisional-bar tier (decided against, needs per-bar markers + scorer re-validation to reopen).
- WS4 beyond funding observations: funding SETTLEMENT history, mark/index archive, book/spread history, OI series, service route for the funding series.
- WS5 beyond increment 1: evaluation harness, estimators, competing outcomes, calibrated probabilities (proposal §3–§5).
- WS6 evidence UI: sample-n / CI / calibration / coverage / validation-status display (blocked on WS5 outputs).
- WS7 beyond the decision log: champion/candidate persistence, outcome-based comparison (blocked on WS5).

## Review Focus

- Restart while the old service is still shutting down: the new process must refuse via `scanner.lock` rather than double-scan, and the operator must confirm exactly one owner afterwards — pinned by Task 1's post-restart lock check.
- Empty registry directory: `load_and_validate` on zero files must return `([], [])`, not an error — pinned by Task 5's empty-dir test.
- Non-UTF8 or CRLF hypothesis file: must yield a `parse:` error for that file while other files still validate — pinned by Task 5's encoding test.
- `scan_once` with an empty universe: phases still recorded (`score_s` ≈ 0), no crash — pinned by Task 2's empty-universe test.
- Duplicate decision id appended to the log: validator must flag `duplicate-id` naming both line numbers — pinned by Task 4's duplicate test.

---

### Task 1 (R1): Deploy-verify the live service (ops, no code)

**Files:** none (operational task; evidence recorded in `docs/roadmap-status.md` in Task 6).

**Interfaces:**
- Consumes: commits `80991bb`–`983c083` (already on `main`).
- Produces: a live backend running current code (precondition for all later verification).

- [ ] **Step 1: Restart the user service**

Run: `systemctl --user restart altradar.service`
Expected: exit 0.

- [ ] **Step 2: Confirm exactly one scanner owner**

Run: `systemctl --user is-active altradar.service && ss -tlnp | grep 18741`
Expected: `active` and exactly one listener on `127.0.0.1:18741`. If the restart raced a slow shutdown (old process still holding `scanner.lock`), the new process refuses — wait 70s (RestartSec 60 + margin) and re-check rather than forcing anything.

- [ ] **Step 3: Verify `/scanner-health` is live**

Run: `TOKEN=$(cat ~/.config/altradar/service.token); curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18741/scanner-health`
Expected: `200` envelope with `data.status` `healthy` (first sweep may still be running — `restarting`/`healthy` both acceptable; `failed` is not).

- [ ] **Step 4: Verify the funding archive accumulates against a baseline**

Run before the restart: `ls data/funding/ 2>/dev/null | wc -l; du -sb data/funding/ 2>/dev/null`. Record both numbers. After the restart and one collect cadence (~5–10 min; poll, do not change cadence), re-run both.
Expected: post-restart count and byte total both exceed the pre-restart baseline (growth, not mere existence — a stale directory carried over from an old process must not pass). If `data/funding/` was absent pre-restart, the baseline is explicitly `(absent)` and post-restart must show `> 0` files with header `ts,rate,cap` (spot-check one file).

### Task 2 (R2): Scan-phase durations in `scan_once`

**Files:**
- Modify: `proto/app.py` (`ScannerApp.scan_once`, ~lines 109–170: time the three existing blocks — universe refresh, `score_universe` fetch+score, signal/plan log loop — with `time.monotonic`, storing `self.last_scan_phases`)
- Test: `tests/test_scan_phases.py` (new; self-checking script style)

**Interfaces:**
- Consumes: `ScannerApp.scan_once` signature unchanged (still returns `None`; phases are read off the instance).
- Produces: `self.last_scan_phases: dict[str, float]` with keys `universe_refresh_s`, `score_s`, `log_s`, `total_s` (all `>= 0`, `total_s` measured around the whole body, monotonic clock so NTP steps cannot skew it). Daemon log line for the phases is optional polish inside this task, not a new interface.

- [ ] **Step 1: Write the failing test.** In `tests/test_scan_phases.py` (repo `check`/`FAILURES`/`ALL PASS` pattern), stub the network boundary (`score_universe` in `proto.app` namespace) and cover: phases dict has exactly the four keys, all `>= 0`, `total_s >= score_s`; empty universe records phases with `score_s` ≈ 0 and no crash.
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_scan_phases.py`. Expected: FAIL (`last_scan_phases` does not exist).
- [ ] **Step 3: Implement phase timing in `proto/app.py:scan_once`.** Three monotonic spans around the existing blocks; assign `self.last_scan_phases` at the end (including on the empty-universe path). No behavior change to scoring, logging, or return value.
- [ ] **Step 4: Run test to verify it passes.** Run: `python3 tests/test_scan_phases.py`. Expected: `ALL PASS`, exit 0.
- [ ] **Step 5: Commit.** `git add proto/app.py tests/test_scan_phases.py && git commit -m "feat(WS3): record scan-phase durations on scan_once"`

### Task 3 (R3): Stale-15-coins investigation (report first)

**Files:**
- Modify: `docs/roadmap-status.md` (record findings under WS1/WS3; fix code only if a defect is found)
- Test: only if a code defect is found (new or extended test pinning it; otherwise this task ships no code)

**Interfaces:**
- Consumes: `proto/bar_quality.py:audit_archive_file` (Stage 1 tool) to list the stale coins and their `last_ts`.
- Produces: a written finding (delisted vs fetch-failure vs rotation-exclusion vs collector bug) + either a code fix with test or an explicit no-fix statement.

- [ ] **Step 1: Enumerate the stale coins.** Run: `python3 -m proto.bar_quality --data-dir data/bars --all` (or equivalent) and extract coins with `age_s > 129600` (36h). Record coin list + last_ts.
- [ ] **Step 2: Classify each coin.** Check membership in the current universe (`build_universe`), recent daemon/collect logs for fetch errors naming those coins, and MEXC availability. Classify each as delisted/universe-excluded, venue fetch failure, or unexplained.
- [ ] **Step 3: Report, fix only on defect.** Write the classification into `docs/roadmap-status.md`. If a code defect is found, fix it under TDD (failing test first) in the same commit; if the cause is venue/delisting, state no-fix explicitly.
- [ ] **Step 4: Commit.** `git add docs/roadmap-status.md [any fix + test] && git commit -m "docs(WS1/WS3): stale-coins investigation finding"`

### Task 4 (R4): WS7 append-only decision log + validator

**Files:**
- Create: `proto/decisions.py`, `docs/decisions/log.jsonl` (empty file — zero lines is valid), `tests/test_decisions.py`
- Test: `tests/test_decisions.py` (self-checking script style)

**Interfaces:**
- Consumes: the SPEC'd `load_registry(dir) -> list[dict]` shape only (Task 4 is implemented before Task 5 exists, so it must NOT import `proto.hypothesis_registry`): `known_ids` is any iterable of id strings, and the wiring against the real registry output is integration-checked in Task 6. Order is deliberate — both validators are specified from the same approved spec, and Task 6 proves they connect.
- Produces:
  - Log format: one JSON object per line in `docs/decisions/log.jsonl`: `{id: "DEC-####", ts: <UTC epoch int>, hypothesis_id: "HYD-####", decision: "promote" | "hold", reasons: [non-empty strings], checks: {object, content unchecked}, decided_by: <non-empty string>}`.
  - Append procedure (manual, human-owned — no service writes the log):
  read the existing log and assign the next id from it (`max existing
  DEC number + 1`; empty log → `DEC-0001`); validate the candidate
  object with `validate_decision` AND the proposed combined log
  (existing decisions plus the candidate) with `validate_log` BEFORE
  appending — both must be clean, otherwise fix the object and do not
  append; then append exactly one `\n`-terminated JSON line.
  Post-append, re-run `validate_log`: a failure here is REPORTED, not
  rolled back — append-only history is never rewritten, so the line
  stands and is corrected by a superseding entry. Pre-append clean +
  post-append failure indicates a validator bug, fixed separately.
  - `validate_decision(obj, known_ids) -> list[str]` — `known_ids` is
  the set of HYPOTHESIS ids (from the registry; existence check only).
  Rules: `bad-id` (decision `id` format `^DEC-[0-9]{4}$`), `bad-ts`
  (non-integer or future epoch), `unknown-hypothesis`
  (`hypothesis_id` not in `known_ids`), `bad-decision`, `empty-reasons`,
  `bad-checks` (not an object), `empty-decider`. Single-object scope:
  it does NOT check `duplicate-id` (one object cannot collide with
  itself — that check lives only in `validate_log` below).
  - `validate_log(path, known_ids) -> (decisions, errors)` — `known_ids`
  is the same hypothesis-id set, passed through to the per-line
  `validate_decision` checks. Owns the cross-line duplicate DECISION-id
  check: a repeated decision `id` is reported once as
  `duplicate-id: DEC-0003 at lines 4, 9`, both lines named.
  - Append preflight without touching the real log: `validate_log`
  reads from `path`, so the preflight copies the current log to a temp
  file, appends the candidate line to the COPY, and runs
  `validate_log(copy, known_ids)` — the real log is modified only by
  the single append step after the copy validates clean. (No alternate
  input shape for `validate_log`; temp-copy keeps one code path for
  preflight and post-append verification.)

- [ ] **Step 1: Write the failing test.** In `tests/test_decisions.py`, cover: valid promote decision validates clean; each `validate_decision` rule above fires on its fixture (no `duplicate-id` case here — single objects cannot collide); duplicate decision id across two log lines is caught ONLY by `validate_log`, naming both line numbers; empty log file validates as `([], [])`.
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_decisions.py`. Expected: FAIL (module does not exist).
- [ ] **Step 3: Implement `validate_decision` + `validate_log` in `proto/decisions.py`.** Pure stdlib (`json`, `re`, `time`); never writes the log.
- [ ] **Step 4: Run test to verify it passes.** Run: `python3 tests/test_decisions.py`. Expected: `ALL PASS`, exit 0.
- [ ] **Step 5: Commit.** `git add proto/decisions.py docs/decisions/log.jsonl tests/test_decisions.py && git commit -m "feat(WS7): append-only promotion decision log and validator"`

### Task 5 (WS5-1a): Registry reader + validator

**Files:**
- Create: `proto/hypothesis_registry.py`, `tests/test_hypotheses.py` (extended in Task 6)
- Test: `tests/test_hypotheses.py` (self-checking script style)

**Interfaces:**
- Consumes: spec `docs/superpowers/specs/2026-10-15-hypothesis-registry-design.md` (exact shapes; read it with this plan).
- Produces (exact):
  - `load_registry(dir) -> list[dict]` — front-matter per the spec's flat-YAML subset; each entry gains reserved `_source_file` (filename only; validator ignores `_`-prefixed keys); files with parse errors are skipped (errors surface via `load_and_validate`).
  - `validate(entry) -> list[str]` — spec rules 1–5 only; `<rule>: <detail>` with rule vocabulary `required-field`, `unknown-status`, `bad-date`, `bad-id`, `ordering`, `within-window`, `embargo`. At-least semantics: multiple violations per entry are all reported.
  - `load_and_validate(dir) -> (entries, errors)` — aggregates parse errors (`parse: <file>:<line>: <reason>`), per-entry violations re-emitted as `<file>: <rule>: <detail>`, and cross-file duplicates reported once as `duplicate-id: HYD-0001 in <file-a>, <file-b>`.

- [ ] **Step 1: Write the failing test.** In `tests/test_hypotheses.py`, using tmp-dir fixtures (never the real `docs/hypotheses/`), cover: flat-mapping parse incl. quoted values with `:`/`#`; each of the eight parse rejections — indentation, dash-list, `[ ]`-list, `|`/`>` multiline marker, anchor, tab indentation, duplicate key in block, nested mapping (`key:` with an empty value followed by indented sub-keys) as its own case distinct from list rejection — yields a `parse:` error naming file+line; validator fixtures for ordering-touching-passes-ordering-but-fails-embargo, reversed window (assert `within-window` AMONG errors), missing denominator, bad status, duplicate id across two files (assert both filenames), non-UTF8/CRLF file (parse error, sibling file still validates), missing front-matter block and unclosed `---` block (each a `parse:` error per the approved spec — a missing block is itself a parse error; the file is skipped, its siblings still validate), empty dir → `([], [])`.
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_hypotheses.py`. Expected: FAIL (module does not exist).
- [ ] **Step 3: Implement `load_registry` + `validate` + `load_and_validate` in `proto/hypothesis_registry.py`.** Half-open day-interval semantics, `embargo_days = 7 if horizon == "7d" else 1`, whole-day `.days` differences, `proposed` exempt for absent endpoints only, coercion per spec (strip one quote layer; int satisfies numeric; non-empty = stripped length > 0; id `^HYD-[0-9]{4}$`).
- [ ] **Step 4: Run test to verify it passes.** Run: `python3 tests/test_hypotheses.py`. Expected: `ALL PASS`, exit 0. Then RED-check the embargo rule (spec criterion 3): comment out the embargo check, re-run, confirm the overlap fixture fails with `embargo` absent; restore and re-run green.
- [ ] **Step 5: Commit.** `git add proto/hypothesis_registry.py tests/test_hypotheses.py && git commit -m "feat(WS5): hypothesis registry reader and validator"`

### Task 6 (WS5-1b): Example hypothesis + full verification + status

**Files:**
- Create: `docs/hypotheses/HYD-0001-funding-quintile-drift.md`
- Modify: `tests/test_hypotheses.py` (assert the shipped example validates clean), `docs/roadmap-status.md` (record this plan's outcomes + restate the Outstanding list)

**Interfaces:**
- Consumes: Task 5's `load_and_validate`; Task 1–4 outcomes.
- Produces: green suite; example file at `testing` status demonstrating the full field set (labeled illustrative in the body; windows/values must be self-consistent and validate clean).

- [ ] **Step 1: Extend the test with the shipped example + cross-module wiring.** Assert `load_and_validate("docs/hypotheses")` returns zero errors and the example entry present with status `testing`. Assert the wiring Task 4 deferred: `known_ids = [e["id"] for e in load_registry("docs/hypotheses")]` fed into `validate_log` accepts a decision citing `HYD-0001` and rejects one citing `HYD-9999` with `unknown-hypothesis` — proving the Task 4 validator connects to real Task 5 output.
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_hypotheses.py`. Expected: FAIL (example file / `docs/hypotheses/` absent).
- [ ] **Step 3: Write `docs/hypotheses/HYD-0001-funding-quintile-drift.md`.** Full `testing`-status field set per the spec table (windows satisfying ordering + embargo for its horizon, numeric threshold with `>=` semantics, explicit denominator, costs referencing the funding archive, `comparisons >= 1`, pinned `rule_version`/`config_hash` refs); body labeled illustrative.
- [ ] **Step 4: Run the full suite.** Run: `python3 tests/run_all.py`. Expected: `ALL SUITES PASS` (file count = previous + 2: `test_scan_phases`, `test_decisions`; `test_hypotheses` was new in Task 5).
- [ ] **Step 5: Update `docs/roadmap-status.md` and commit.** Record R1–R4 + WS5-1 outcomes with what was verified, carry forward the Outstanding list verbatim-or-truer, then `git add docs/hypotheses/HYD-0001-funding-quintile-drift.md tests/test_hypotheses.py docs/roadmap-status.md && git commit -m "feat(WS5): example hypothesis, verification, and status"`
