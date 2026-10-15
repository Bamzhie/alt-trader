"""Scan-phase durations on scan_once (WS3 R2).

Self-checking script: `python3 tests/test_scan_phases.py`.
Stubs the network boundary (score_universe in proto.app namespace) and
covers: phases dict has exactly the four keys, all >= 0,
total_s >= score_s; empty universe records phases with score_s ~= 0
and no crash.
"""

import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proto.app as appmod

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def make_app(**kw):
    args = types.SimpleNamespace(
        db=":memory:",
        coins=10,
        stake=0.10,
        log_threshold=24.0,
        write_logs=False,
        use_v3=False,
    )
    for k, v in kw.items():
        setattr(args, k, v)
    app = appmod.App(args)
    # Keep the universe cache fresh so no network refresh fires.
    app._uni_at = time.time()
    return app


EXPECTED_KEYS = {"universe_refresh_s", "score_s", "log_s", "total_s"}

print("=== populated universe ===")
app = make_app()
app.uni = [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH")]
app.det = {}
app.tk = {}
orig = appmod.score_universe
appmod.score_universe = lambda *a, **k: []
try:
    ret = app.scan_once()
finally:
    appmod.score_universe = orig

check("scan_once returns None", ret is None, repr(ret))
phases = getattr(app, "last_scan_phases", None)
check("last_scan_phases exists and is a dict",
      isinstance(phases, dict), repr(phases))
if isinstance(phases, dict):
    check("exactly the four keys", set(phases.keys()) == EXPECTED_KEYS,
          repr(sorted(phases.keys())))
    check("all values >= 0",
          all(isinstance(v, float) and v >= 0 for v in phases.values()),
          repr(phases))
    check("total_s >= score_s",
          phases.get("total_s", -1) >= phases.get("score_s", float("inf")),
          repr(phases))
    check("total_s >= universe_refresh_s + score_s + log_s (approx)",
          phases.get("total_s", -1) + 1e-6
          >= phases.get("universe_refresh_s", 0)
          + phases.get("score_s", 0) + phases.get("log_s", 0),
          repr(phases))
else:
    for n in ("exactly the four keys", "all values >= 0",
              "total_s >= score_s",
              "total_s >= universe_refresh_s + score_s + log_s (approx)"):
        check(n, False, "no phases dict")

print("\n=== empty universe ===")
app2 = make_app()
app2.uni = []
app2.det = {}
app2.tk = {}
orig2 = appmod.score_universe
appmod.score_universe = lambda *a, **k: []
try:
    try:
        app2.scan_once()
        crashed = None
    except Exception as e:  # noqa: BLE001 - the check is the no-crash proof
        crashed = e
finally:
    appmod.score_universe = orig2
check("empty universe: no crash", crashed is None, repr(crashed))
ph2 = getattr(app2, "last_scan_phases", None)
check("empty universe: phases recorded", isinstance(ph2, dict), repr(ph2))
if isinstance(ph2, dict):
    check("empty universe: exactly the four keys",
          set(ph2.keys()) == EXPECTED_KEYS, repr(sorted(ph2.keys())))
    check("empty universe: all values >= 0",
          all(isinstance(v, float) and v >= 0 for v in ph2.values()),
          repr(ph2))
    check("empty universe: score_s ~= 0 (< 1s)",
          ph2.get("score_s", float("inf")) < 1.0, repr(ph2))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
