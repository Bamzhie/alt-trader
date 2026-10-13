"""Watcher event study + N selection + kill gate (Task 4).

Self-checking script: `python3 tests/test_watcher_eventstudy.py`.
Covers cumret (dir*ln(ch/c0)), event_score ((mean_c-mean_p)/SD_c,
SD_c==0 -> 0.0), select_N (argmax over {12,48,96,288}), kill_check
(True only when gross<=0 at all {12,48,288} AND net<=0 at all three).
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import watcher

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def close(a, b, tol=1e-12):
    return abs(a - b) <= tol


print("=== cumret ===")
check("long up = +ln(ch/c0)",
      close(watcher.cumret(100.0, 110.0, "LONG"), math.log(110.0 / 100.0)),
      str(watcher.cumret(100.0, 110.0, "LONG")))
check("short up = -ln(ch/c0)",
      close(watcher.cumret(100.0, 110.0, "SHORT"), -math.log(110.0 / 100.0)),
      str(watcher.cumret(100.0, 110.0, "SHORT")))
check("flat = 0.0",
      watcher.cumret(100.0, 100.0, "LONG") == 0.0
      and watcher.cumret(100.0, 100.0, "SHORT") == 0.0)
check("long/short mirror",
      close(watcher.cumret(100.0, 105.0, "LONG"),
            -watcher.cumret(100.0, 105.0, "SHORT")))

print("\n=== event_score ===")
# Hand value: cand=[0.02,0.04], plac=[0.01,0.01].
# mean_c=0.03, mean_p=0.01, SD_c(pop)=0.01 -> score=2.0.
cand = [0.02, 0.04]
plac = [0.01, 0.01]
got = watcher.event_score(cand, plac)
check("placebo-subtraction hand value (0.03-0.01)/0.01 == 2.0",
      close(got, 2.0), str(got))
check("zero variance -> 0.0 not NaN",
      watcher.event_score([0.05, 0.05, 0.05], [0.01, 0.02]) == 0.0
      and not math.isnan(watcher.event_score([0.05, 0.05, 0.05], [0.01, 0.02]))),
check("negative edge: cand below placebo",
      watcher.event_score([0.0, 0.0, 0.02], [0.05, 0.05, 0.05]) < 0.0)
check("empty cand -> 0.0",
      watcher.event_score([], [0.01]) == 0.0)

print("\n=== select_N ===")
curves = {12: 0.5, 48: 1.8, 96: 1.2, 288: -0.3}
check("peak at 48 picked", watcher.select_N(curves) == 48,
      str(watcher.select_N(curves)))
check("peak at 12 picked",
      watcher.select_N({12: 3.0, 48: 1.0, 96: 0.5, 288: 0.1}) == 12)
check("peak at 288 picked",
      watcher.select_N({12: -1.0, 48: -0.5, 96: 0.0, 288: 0.7}) == 288)
check("extra keys ignored, argmax over fixed set",
      watcher.select_N({12: 0.1, 48: 0.2, 96: 0.3, 288: 0.25, 24: 99.0}) == 96)

print("\n=== kill_check ===")
allneg_g = {12: -0.1, 48: -0.2, 288: 0.0}
allneg_n = {12: -0.05, 48: 0.0, 288: -0.3}
check("kill True only when all six <= 0",
      watcher.kill_check(allneg_g, allneg_n) is True)
check("mixed: one gross horizon positive -> False",
      watcher.kill_check({12: 0.5, 48: -0.2, 288: -0.1}, allneg_n) is False)
check("mixed: one net horizon positive -> False",
      watcher.kill_check(allneg_g, {12: -0.1, 48: 0.01, 288: -0.2}) is False)
check("all positive -> False (no kill)",
      watcher.kill_check({12: 0.1, 48: 0.2, 288: 0.3},
                         {12: 0.05, 48: 0.1, 288: 0.2}) is False)
check("gross all<=0 but net positive -> False",
      watcher.kill_check(allneg_g, {12: 0.1, 48: 0.1, 288: 0.1}) is False)
check("returns strict bool",
      isinstance(watcher.kill_check(allneg_g, allneg_n), bool))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
