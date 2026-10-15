"""WS7 append-only promotion decision log + validator (R4).

Self-checking script: `python3 tests/test_decisions.py`.
Covers validate_decision clean path, each single-object rule firing
(bad-id, bad-ts, unknown-hypothesis, bad-decision, empty-reasons,
bad-checks, empty-decider), duplicate-id caught ONLY by validate_log
with both line numbers named, and empty log validating as ([], []).
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import decisions

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


KNOWN = {"HYD-0001", "HYD-0002"}


def valid(**kw):
    obj = {"id": "DEC-0001", "ts": 1_700_000_000,
           "hypothesis_id": "HYD-0001", "decision": "promote",
           "reasons": ["cadence evidence passes"],
           "checks": {"calibration_pass": True},
           "decided_by": "operator"}
    obj.update(kw)
    return obj


def write_log(lines):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        for line in lines:
            fh.write(line + "\n")
    return path


print("=== validate_decision clean ===")
check("valid promote validates clean",
      decisions.validate_decision(valid(), KNOWN) == [],
      str(decisions.validate_decision(valid(), KNOWN)))
check("known_ids accepts any iterable (list)",
      decisions.validate_decision(valid(), ["HYD-0001", "HYD-0002"]) == [])

print("\n=== validate_decision rules ===")
check("bad-id fires", "bad-id" in decisions.validate_decision(valid(id="DEC-1"), KNOWN))
check("bad-id fires on lowercase",
      "bad-id" in decisions.validate_decision(valid(id="dec-0001"), KNOWN))
check("bad-id fires on missing id",
      "bad-id" in decisions.validate_decision(
          {k: v for k, v in valid().items() if k != "id"}, KNOWN))
check("bad-ts fires on non-integer",
      "bad-ts" in decisions.validate_decision(valid(ts="yesterday"), KNOWN))
check("bad-ts fires on bool",
      "bad-ts" in decisions.validate_decision(valid(ts=True), KNOWN))
check("bad-ts fires on future epoch",
      "bad-ts" in decisions.validate_decision(valid(ts=int(time.time()) + 3600), KNOWN))
check("unknown-hypothesis fires",
      "unknown-hypothesis" in decisions.validate_decision(
          valid(hypothesis_id="HYD-9999"), KNOWN))
check("bad-decision fires",
      "bad-decision" in decisions.validate_decision(valid(decision="maybe"), KNOWN))
check("empty-reasons fires on []",
      "empty-reasons" in decisions.validate_decision(valid(reasons=[]), KNOWN))
check("empty-reasons fires on blank string entry",
      "empty-reasons" in decisions.validate_decision(valid(reasons=[""]), KNOWN))
check("empty-reasons fires on non-list",
      "empty-reasons" in decisions.validate_decision(valid(reasons="ok"), KNOWN))
check("bad-checks fires on non-object",
      "bad-checks" in decisions.validate_decision(valid(checks=["x"]), KNOWN))
check("empty-decider fires",
      "empty-decider" in decisions.validate_decision(valid(decided_by=""), KNOWN))
check("empty-decider fires on missing decider",
      "empty-decider" in decisions.validate_decision(
          {k: v for k, v in valid().items() if k != "decided_by"}, KNOWN))

print("\n=== single-object scope: no duplicate-id ===")
all_single = []
for fixture in (valid(), valid(id="DEC-1"), valid(hypothesis_id="HYD-9999")):
    all_single.extend(decisions.validate_decision(fixture, KNOWN))
check("validate_decision never reports duplicate-id",
      not any("duplicate-id" in e for e in all_single), str(all_single))

print("\n=== validate_log duplicates + empty ===")
dup_path = write_log([json.dumps(valid(id="DEC-0003")),
                      json.dumps(valid(id="DEC-0003", hypothesis_id="HYD-0002"))])
try:
    logged, errs = decisions.validate_log(dup_path, KNOWN)
    check("both lines parsed", len(logged) == 2, str(logged))
    check("each line clean alone",
          decisions.validate_decision(logged[0], KNOWN) == []
          and decisions.validate_decision(logged[1], KNOWN) == [])
    check("duplicate-id reported once with both lines",
          errs == ["duplicate-id: DEC-0003 at lines 1, 2"], str(errs))
finally:
    os.unlink(dup_path)

empty_path = write_log([])
try:
    logged, errs = decisions.validate_log(empty_path, KNOWN)
    check("empty log validates as ([], [])", logged == [] and errs == [],
          f"{logged!r} {errs!r}")
finally:
    os.unlink(empty_path)

ok_path = write_log([json.dumps(valid(id="DEC-0001")),
                     json.dumps(valid(id="DEC-0002", hypothesis_id="HYD-0002",
                                      decision="hold",
                                      reasons=["sample size too small"]))])
try:
    logged, errs = decisions.validate_log(ok_path, KNOWN)
    check("two distinct ids validate clean", errs == [] and len(logged) == 2,
          f"{logged!r} {errs!r}")
finally:
    os.unlink(ok_path)

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
