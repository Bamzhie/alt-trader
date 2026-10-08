"""Outcome resolver tests: symmetric returns, roundtrip."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proto.outcomes import signed_return
FAILURES = []
def check(n,c,d=""):
    print(f"  {'PASS' if c else 'FAIL'}  {n}"+("" if c else f"  {d}"))
    if not c: FAILURES.append(n)
check("long +10 mirrors", abs(signed_return(100,110,"LONG")-10.0)<1e-9)
check("short -10 mirrors", abs(signed_return(100,90,"SHORT")-10.0)<1e-9, str(signed_return(100,90,"SHORT")))
check("long loss negative", signed_return(100,90,"LONG")<0)
check("short loss negative", signed_return(100,110,"SHORT")<0)
print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED")
sys.exit(1 if FAILURES else 0)
