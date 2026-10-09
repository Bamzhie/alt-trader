"""Offline suite aggregator: every script test except the live test_app scan.

Each tests/test_*.py is a self-checking script (not unittest-discoverable:
they call sys.exit). This runner executes each in a subprocess and reports
pass/fail per file. tests/test_app.py is excluded — its live-scan section
needs venue network; run it explicitly when online.

Usage: python3 tests/run_all.py
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OFFLINE = sorted(
    f for f in os.listdir(HERE)
    if f.startswith("test_") and f.endswith(".py") and f != "test_app.py"
)

failures = []
for name in OFFLINE:
    path = os.path.join(HERE, name)
    try:
        r = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, timeout=600)
    except subprocess.TimeoutExpired:
        print(f"FAIL  {name}  (timeout)")
        failures.append(name)
        continue
    out = (r.stdout + r.stderr).strip().splitlines() or ["(no output)"]
    tail = out[-1]
    ok = r.returncode == 0 and any(s in (r.stdout + r.stderr)
                                  for s in ("ALL PASS", "OK"))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  [{tail}]")
    if not ok:
        failures.append(name)
        print(r.stdout[-2000:])
        print(r.stderr[-2000:], file=sys.stderr)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print(f"ALL SUITES PASS ({len(OFFLINE)} files)")
