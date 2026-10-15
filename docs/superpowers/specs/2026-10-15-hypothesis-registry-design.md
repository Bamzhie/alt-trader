# WS5 Increment 1: Hypothesis Registry + Split Discipline — Design

**Status:** Proposed for review (2026-10-15)
**Date:** 2026-10-15
**Parent proposal:** `docs/quant-analytics-system-proposal.md` (§5 validation harness)
**Roadmap:** `docs/roadmap-status.md` (Workstream 5, proposal-only → first increment)

## Goal

Give every quantitative claim in ALT RADAR a preregistered home: hypotheses
are written down *before* evaluation, with falsifiable predictions, explicit
chronological data windows, and success criteria carrying denominators — so
that later estimators (Hurst, competing outcomes, calibrated probabilities)
are tested against commitments, not fitted to memory.

## Non-goals (explicit)

- No estimators, no Hurst/MF-DFA, no calibrated probabilities, no competing
  outcomes — those are later WS5 increments with their own specs.
- No changes to the scanner, collector, service routes, or UI.
- No migration of the production database.

## Architecture

```text
docs/hypotheses/HYD-####-slug.md   (one tracked file per hypothesis;
                                     commit hash = preregistration proof)
            │
            ▼
proto/hypothesis_registry.py       (stdlib reader + validator, no writes)
            │
            ▼
tests/test_hypotheses.py           (every file parses; windows disciplined;
                                     one illustrative example ships green)
```

The registry is files, not tables: git history provides the audit trail
(who committed what prediction when), and rejected hypotheses stay on file
so multiple-comparison honesty is structural, not procedural. The WS7
decision log (separate bounded task) cites hypothesis ids, connecting change
control to research intent without depending on unbuilt estimators.

## Registry format

One markdown file per hypothesis under `docs/hypotheses/`, YAML
front-matter plus prose rationale. The front-matter block is the lines
between the first two `---` lines at the top of the file (Unix newlines;
a missing block is itself a parse error).

### YAML subset (stdlib-only parser)

No PyYAML dependency: the parser accepts a flat mapping only, one
`key: value` pair per line:

- Values are strings (plain or single-/double-quoted), integers
  (`comparisons: 3`), or floats (`threshold: 0.52`). Quoted values may
  contain `:` and `#`; unquoted values are cut at the first ` #`
  (trailing comment) and stripped.
- Type coercion for validation: strip one layer of matching outer quotes,
  then coerce — quoted `"0.52"` / `"3"` satisfy numeric / integer
  requirements exactly like bare values, and a bare integer satisfies a
  numeric `threshold`. "Non-empty" means the stripped, unquoted value has
  length > 0, so `""` and whitespace-only values fail.
- Keys match `[A-Za-z_][A-Za-z0-9_]*`. Indentation, nesting, lists
  (`- `, `[ ]`), multiline markers (`|`, `>`), anchors, tabs, and
  duplicate keys within one block are all parse errors.
- Full-line `#` comments and blank lines are ignored.
- Unknown keys are kept (forward-compatible) but never validated.

Parse errors are reported, never raised: the file is skipped for
validation and yields one string per offending line,
`parse: <filename>:<line>: <reason>` (e.g. `parse: HYD-0002-x.md:7:
nested value not supported`), collected in the errors list returned by
`load_and_validate`.

### Fields

- `id`: `HYD-####` (zero-padded; new hypotheses take max existing + 1).
- `title`: one line.
- `status`: `proposed | registered | testing | validated | rejected`.
  Transitions happen only by commit. `rejected` files are never deleted.
- `prediction`: falsifiable directional statement (validator requires
  non-empty; content quality is review's job, not the validator's).
- `universe`: coin selection rule (e.g. "all MEXC USDT perps in
  `build_universe`, BTC excluded" — BTC is context only per product goal).
- `horizon`: one of `1h | 4h | 24h | 7d` (matches outcome horizons).
- `dev_start`, `dev_end`, `val_start`, `val_end`, `holdout_start`
  (UTC `YYYY-MM-DD`; `holdout_end` optional — open means unseen future).
- `metric`: the measured quantity (e.g. `touch_rate_TP1`,
  `median_forward_return_4h`).
- `threshold`: numeric bar for `validated` (e.g. `0.52`). The pass
  criterion is always `metric >= threshold`; lower-is-better quantities
  must be framed as their complement by the author.
- `denominator`: what the metric divides by (e.g. `resolved_episodes`).
  No denominator → validation fails.
- `costs`: funding + fee assumption reference (e.g. `taker 0.05%/side,
  funding from funding archive when available else stated estimate`).
- `comparisons`: integer count of related hypotheses examined (multiple
  comparisons are declared, not hidden).
- `rule_version`, `config_hash`: refs matching `proto/measurement.py`
  versioning so an evaluation is reproducible.

## Required fields by status

Presence is validator-enforced; content quality is review's job.

| Status | Required fields |
|---|---|
| `proposed` | `id`, `title`, `status`, `prediction` (non-empty). Idea being shaped; windows and criteria may be absent. |
| `registered` | All of the above, plus `universe`, `horizon`, `dev_start`, `dev_end`, `val_start`, `val_end`, `holdout_start`, `metric`, `threshold` (numeric), `denominator` (non-empty), `costs`, `comparisons` (integer ≥ 1 — the hypothesis itself counts). |
| `testing` | All `registered` fields, plus `rule_version` and `config_hash` (the code under test is pinned before evaluation starts). |
| `validated` / `rejected` | All `testing` fields (the claim passed through testing; the validator checks presence, not truth — whether the bar was met is decided by review against the evaluation output in increment 2). |

`holdout_end` is optional at every status (open means unseen future data).
`rejected` files are never deleted.

## Split discipline (validator-enforced)

Date semantics: every window date is a UTC calendar day; windows are
half-open day intervals, so `dev_end` is the first day NOT in dev and
`[dev_start, dev_end)` never shares a day with `[val_start, val_end)`.
Differences below are whole-day counts (`(later - earlier).days`).

1. Cross-window ordering: when all four boundary endpoints are present,
   require `dev_end <= val_start <= val_end <= holdout_start`
   (chronological; no time-travel). Ordering and embargo are SEPARATE
   checks that must both pass: touching boundaries (`dev_end ==
   val_start`) satisfy ordering because intervals are half-open, yet a
   zero-day gap violates the `>= embargo_days` embargo below. Pairwise
   comparisons whose endpoint is absent are skipped (see `proposed`
   rule below) — never treated as pass or fail.
2. Within-window validity: endpoints are all-or-nothing per window — if
   either endpoint of dev/val is present, both are required; a present
   `holdout_end` requires `holdout_start`. Every window with both
   endpoints present must satisfy `start < end` strictly
   (`dev_start < dev_end`, `val_start < val_end`,
   `holdout_start < holdout_end`). A zero-length or reversed window fails
   even if cross-window ordering happens to hold (e.g. `dev_start ==
   dev_end == val_start` passes rule 1 but fails this rule).
3. Purge/embargo, computed from the hypothesis's own horizon:
   `embargo_days = 7` when `horizon` is `7d`, else `1`. Require
   `(val_start - dev_end).days >= embargo_days` and
   `(holdout_start - val_end).days >= embargo_days`, so outcome labels
   (max 7d forward) cannot leak across a boundary. Day granularity is why
   sub-day horizons still embargo a full day. Comparisons whose endpoint
   is absent are skipped, same as rule 1.
4. Required fields per the status table above (which restates: `metric`,
   numeric `threshold`, non-empty `denominator`, `costs`, `comparisons`
   from `registered` upward; `rule_version` + `config_hash` from
   `testing`). `proposed` entries are exempt from window rules for
   ABSENT endpoints only: any window endpoint that IS present is still
   type-checked, and any pair with both endpoints present is still
   subject to rules 1–3. A bare `proposed` entry with no windows passes
   all window rules; a `proposed` entry with invalid present windows
   fails.
5. `status` must be one of the five table values (`unknown-status`
   otherwise); present dates must parse as `YYYY-MM-DD` (`bad-date`
   otherwise); present `id` must match `^HYD-[0-9]{4}$` (`bad-id`
   otherwise). Duplicate ids are NOT checked here — see cross-file
   checks below.

## Components

- `proto/hypothesis_registry.py` (new, stdlib only: `os`, `re`, `datetime`):
  `load_registry(dir) -> list[dict]` (parse front-matter, keep body;
  each entry carries a reserved `_source_file` key holding its filename —
  the validator ignores all `_`-prefixed keys and implementations must
  never treat them as hypothesis fields. Files with parse errors are
  skipped here — their `parse: …` strings are surfaced by
  `load_and_validate`),
  `validate(entry) -> list[str]` (single-entry rules 1–5 above only.
  Empty = valid; each string is `<rule>: <detail>` where `<rule>` is one
  of `required-field`, `unknown-status`, `bad-date`, `bad-id`,
  `ordering`, `within-window`, `embargo`),
  `load_and_validate(dir) -> (entries, errors)` (owns ALL cross-file
  checks: duplicate `id` across files reported once as
  `duplicate-id: HYD-0001 in <file-a>, <file-b>` with every involved
  file named; per-entry violations re-emitted prefixed with their file,
  `<file>: <rule>: <detail>`; plus the aggregated per-file parse
  errors).
  An entry may report MULTIPLE rule violations at once (at-least
  semantics): an overlap fails both `ordering` and `embargo`, and
  fixtures assert the expected rule is AMONG the reported errors, never
  that it is the only one.
  Read-only; never writes, never deletes.
- `docs/hypotheses/HYD-0001-funding-quintile-drift.md` (new): one
  illustrative example, labeled illustrative in the body, demonstrating
  the full `testing`-status field set (funding-rate quintile vs 24h
  forward drift).
- `tests/test_hypotheses.py` (new, self-checking `check()` style):
  registry parses, example validates clean, and negative fixtures
  (overlap, missing embargo, missing denominator, duplicate id, bad
  status, malformed front-matter, reversed window with `dev_start` after
  `dev_end`) each fail with the expected rule AMONG the reported errors
  (at-least semantics — multi-rule failures are legal).
  The duplicate-id fixture is two files and must assert the error names
  both files. The reversed-window fixture must include `within-window`
  among its errors even though its cross-window ordering still holds.

## Data flow / error handling

File → parse → validate → report. The validator never raises on bad
content: every defect becomes a `rule: detail` string. `tests/run_all.py`
discovers the new test file by naming convention; no aggregator change.

## Scope boundaries

- Read-only process tooling: no order code, no venue calls, no DB writes.
- Does not evaluate any hypothesis (evaluation harness is WS5 increment 2).
- Does not alter `promotion.py` (it may consume registry dicts later).

## Verification criteria

1. `tests/test_hypotheses.py` → ALL PASS (positive + all seven negative
   fixtures with the expected rule among the reported errors;
   duplicate-id names both files; reversed-window includes
   `within-window`).
2. `python3 tests/run_all.py` stays green.
3. A deliberately overlapping-window fixture fails with `embargo` among
   its reported errors (RED-verified by removing the check).
