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
front-matter plus prose rationale. Required front-matter fields:

- `id`: `HYD-####` (zero-padded; new hypotheses take max existing + 1;
  validator checks uniqueness).
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

## Split discipline (validator-enforced)

1. Windows must order `dev_end <= val_start <= val_end <= holdout_start`
   (chronological; no time-travel).
2. Purge/embargo: `val_start - dev_end >= horizon` and
   `holdout_start - val_end >= horizon` (embargo covers the max outcome
   horizon so labels cannot leak across the boundary).
3. `metric`, numeric `threshold`, and non-empty `denominator` are all
   required for `registered` and beyond (`proposed` may omit windows while
   the idea is being shaped — the validator enforces full discipline only
   from `registered` upward).
4. Unknown statuses, duplicate ids, or unparsable dates fail validation.

## Components

- `proto/hypothesis_registry.py` (new, stdlib only: `os`, `re`, `datetime`):
  `load_registry(dir) -> list[dict]` (parse front-matter, keep body),
  `validate(entry) -> list[str]` (empty = valid; each string is a cited
  rule violation), `load_and_validate(dir) -> (entries, errors)`.
  Read-only; never writes, never deletes.
- `docs/hypotheses/HYD-0001-funding-quintile-drift.md` (new): one
  illustrative example, labeled illustrative in the body, demonstrating
  the full field set (funding-rate quintile vs 24h forward drift).
- `tests/test_hypotheses.py` (new, self-checking `check()` style):
  registry parses, example validates clean, and negative fixtures
  (overlap, missing embargo, missing denominator, duplicate id, bad
  status) each fail with the expected rule cited.

## Data flow / error handling

File → parse → validate → report. The validator never raises on bad
content: every defect becomes a `rule: detail` string. `tests/run_all.py`
discovers the new test file by naming convention; no aggregator change.

## Scope boundaries

- Read-only process tooling: no order code, no venue calls, no DB writes.
- Does not evaluate any hypothesis (evaluation harness is WS5 increment 2).
- Does not alter `promotion.py` (it may consume registry dicts later).

## Verification criteria

1. `tests/test_hypotheses.py` → ALL PASS (positive + all five negative
   fixtures citing the violated rule).
2. `python3 tests/run_all.py` stays green.
3. A deliberately overlapping-window fixture fails with the embargo rule
   cited (RED-verified by removing the check).
