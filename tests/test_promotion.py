"""WS7 change control: promotion gate tests.

Self-checking script: python3 tests/test_promotion.py
Covers evaluate_promotion over cadence-stats-shaped inputs:
  1. healthy cohort -> promote, with auditable checks
  2. calibration failure -> hold with the cited reason
  3. too few coins -> hold (breadth)
  4. too few attempts -> hold (sample size)
  5. majority failed/degraded attempts -> hold (honesty)
  6. missing stats -> hold, never a silent pass
  7. no confidence language anywhere in the result
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.promotion import evaluate_promotion

FAILURES = []
CHECKS = 0


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    cond = bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def _stats(coins=50, attempts=500, failures=0, degraded=0,
           cal_pass=True):
    return {
        "coins": coins,
        "attempts": attempts,
        "failures": failures,
        "degraded": degraded,
        "gaps": 0,
        "calibration": {"pass": cal_pass, "p95_ok": cal_pass,
                        "p99_ok": cal_pass,
                        "p95_limit_s": 600, "p99_limit_s": 900},
    }


print("=== case 1: healthy cohort promotes ===")
r1 = evaluate_promotion(_stats(), candidate="cfg-abc123")
check("healthy cohort -> promote", r1.decision == "promote",
      str(r1.as_dict()))
check("promote carries no reasons", r1.reasons == [], str(r1.reasons))
check("checks record calibration evidence",
      r1.checks.get("calibration_pass") is True
      and r1.checks.get("coins") == 50
      and r1.checks.get("attempts") == 500, str(r1.checks))
check("candidate identity is carried", r1.candidate == "cfg-abc123",
      str(r1.candidate))

print("\n=== case 2: calibration failure holds ===")
r2 = evaluate_promotion(_stats(cal_pass=False))
check("calibration failure -> hold", r2.decision == "hold",
      str(r2.as_dict()))
check("hold cites the calibration reason",
      any("calibration" in x for x in r2.reasons), str(r2.reasons))

print("\n=== case 3: narrow cohort holds ===")
r3 = evaluate_promotion(_stats(coins=5))
check("too few coins -> hold", r3.decision == "hold", str(r3.as_dict()))
check("hold cites breadth", any("breadth" in x for x in r3.reasons),
      str(r3.reasons))

print("\n=== case 4: thin sample holds ===")
r4 = evaluate_promotion(_stats(coins=50, attempts=10))
check("too few attempts -> hold", r4.decision == "hold", str(r4.as_dict()))
check("hold cites sample size", any("sample size" in x for x in r4.reasons),
      str(r4.reasons))

print("\n=== case 5: majority failed/degraded holds ===")
r5 = evaluate_promotion(_stats(attempts=100, failures=60, degraded=10))
check("majority failed/degraded -> hold", r5.decision == "hold",
      str(r5.as_dict()))
check("hold cites failure share",
      any("failed/degraded" in x for x in r5.reasons), str(r5.reasons))

print("\n=== case 6: missing stats never silently passes ===")
r6 = evaluate_promotion(None)
check("missing stats -> hold", r6.decision == "hold", str(r6.as_dict()))
check("missing stats cites unavailability",
      r6.reasons == ["no cadence stats available"], str(r6.reasons))

print("\n=== case 7: no confidence language ===")
BANNED = ("likely", "probably", "confidence", "probability", "chance",
          "expect", "should", "edge")
blob = str(r1.as_dict()).lower()
hits = [w for w in BANNED if w in blob]
check("result carries no confidence/probability language", not hits,
      str(hits))
check("outcome scope stated explicitly (WS5 unbuilt)",
      r1.checks.get("outcome_evidence")
      == "not evaluated (WS5 inputs unbuilt)", str(r1.checks))

print()
if FAILURES:
    print(f"FAIL  tests/test_promotion.py  "
          f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
    sys.exit(1)
print(f"ALL PASS  tests/test_promotion.py  ({CHECKS} checks)")


if __name__ == "__main__":
    pass
