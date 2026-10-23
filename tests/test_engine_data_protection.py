"""Data-protection tests for proto.engine.

The engine must never touch protected data: nothing under data/bars,
data/prices, data/funding*, signals.db, quant.db, or any collector sidecar
can be written, moved, or deleted by proto.engine. New research records go
only into data/research/altradar-research.db via the existing append-only
store.

This test has two parts:
  1. Source grep: no module under proto/engine/ opens any protected path for
     writing, calls os.remove / shutil.rmtree, or writes to protected files.
  2. Runtime: the existing proto.research.store / sync boundary guards are
     exercised on a temp fixture — they must refuse protected DB names and
     non-bars source directories, and must accept the research DB path.
     (Phase 0 verifies the existing guards; Phase 6+ will exercise the engine
     cycle runner and assert it does not mutate the fixture archive.)

All fixtures are created under a temp directory — the real data/ tree is
never read or written by this test.
"""
import gzip
import os
import re
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


# ---------------------------------------------------------------------------
# Part 1: source-level boundary checks
# ---------------------------------------------------------------------------

# String literals that would indicate engine code reaching for protected data.
# The engine must read bars THROUGH the research mirror only.
PROTECTED_PATTERNS = [
    r'["\']data/bars["\']',
    r'["\']data/prices["\']',
    r'["\']data/funding',
    r'["\']signals\.db["\']',
    r'["\']quant\.db["\']',
]

# Destructive calls that engine code must never use.
PROTECTED_CALLS = [
    r"os\.remove\s*\(",
    r"os\.unlink\s*\(",
    r"shutil\.(rmtree|move|copy|copyfile)",
]


def _line_number(source, match):
    """Convert a match's character offset to a 1-based line number."""
    return source.count("\n", 0, match.start()) + 1


def scan_engine_source():
    """Grep every .py under proto/engine/ for boundary violations.

    Checks for:
      - String literals referencing protected data paths (data/bars,
        data/prices, data/funding*, signals.db, quant.db)
      - Destructive os/shutil calls (remove, unlink, rmtree, move, copy,
        copyfile)

    Note: proto.engine reads bars THROUGH the research mirror
    (proto.research.sync.load_snapshot_bars), never from data/bars directly,
    so no engine module should contain those path literals.
    """
    engine_dir = os.path.join(REPO, "proto", "engine")
    if not os.path.isdir(engine_dir):
        check("proto/engine/ exists", False, "directory not created yet")
        return []

    violations = []
    for dirpath, _dirs, files in os.walk(engine_dir):
        for f in sorted(files):
            if not f.endswith(".py"):
                continue
            path = os.path.join(dirpath, f)
            with open(path, encoding="utf-8") as fh:
                source = fh.read()
            for pat in PROTECTED_PATTERNS:
                for m in re.finditer(pat, source):
                    violations.append((f"{os.path.relpath(path, REPO)}:{_line_number(source, m)}",
                                      pat))
            for pat in PROTECTED_CALLS:
                for m in re.finditer(pat, source):
                    violations.append((f"{os.path.relpath(path, REPO)}:{_line_number(source, m)}",
                                      pat))
    return violations


print("=== Part 1: source boundary scan ===")
violations = scan_engine_source()
check("no protected paths or destructive calls in proto/engine/", not violations,
      f"found {len(violations)} violation(s): {violations[:5]}")


# ---------------------------------------------------------------------------
# Part 2: runtime boundary on a temp fixture (never the real data/ tree)
# ---------------------------------------------------------------------------

def _write_synthetic_bars(data_dir, coins=("BTC", "ETH", "SOL")):
    """Write a tiny synthetic bar archive into a temp dir."""
    os.makedirs(data_dir, exist_ok=True)
    header = "ts,o,h,l,c,vol,amount\n"
    start = 1700000000
    step = 300  # BAR_S
    for coin in coins:
        rows = [header]
        price = 100.0 if coin != "BTC" else 50000.0
        for i in range(300):
            ts = start + i * step
            rows.append(f"{ts},{price},{price*1.001},{price*0.999},{price},100,10000\n")
        path = os.path.join(data_dir, f"{coin}.csv.gz")
        with gzip.open(path, "wt") as fh:
            fh.write("".join(rows))


print("\n=== Part 2: runtime boundary on temp fixtures ===")

tmp = tempfile.mkdtemp(prefix="engine_boundary_")
try:
    # Build a synthetic data/ tree in a temp dir — NOT the real data/
    data_root = os.path.join(tmp, "data")
    bars_dir = os.path.join(data_root, "bars")
    _write_synthetic_bars(bars_dir)

    # The research DB lives under data/research/ in the temp tree. This mirrors
    # the real layout: data/bars (protected archive) + data/research/ (research DB).
    research_db = os.path.join(data_root, "research", "altradar-research.db")
    os.makedirs(os.path.dirname(research_db), exist_ok=True)

    # Verify the store guard blocks protected DB names — using temp paths
    # (not the real data/signals.db or data/quant.db).
    from proto.research import store
    blocked = False
    try:
        store.validate_db_path(os.path.join(os.path.dirname(research_db), "signals.db"))
    except store.StoreError:
        blocked = True
    check("store refuses signals.db path", blocked)

    blocked = False
    try:
        store.validate_db_path(os.path.join(os.path.dirname(research_db), "quant.db"))
    except store.StoreError:
        blocked = True
    check("store refuses quant.db path", blocked)

    blocked = False
    try:
        store.validate_db_path(bars_dir)
    except store.StoreError:
        blocked = True
    check("store refuses data/bars path", blocked)

    # Verify the research DB path itself is ACCEPTED (not protected).
    accepted = False
    try:
        store.validate_db_path(research_db)
        accepted = True
    except store.StoreError:
        pass
    check("store accepts the research DB path", accepted)

    # Verify sync refuses a source that is not under .../data/bars
    from proto.research import sync
    refused = False
    try:
        sync.sync_bars(os.path.join(data_root, "fake_archive"), research_db)
    except sync.SyncError:
        refused = True
    check("sync refuses non-standard source dir name", refused)

    # Verify a writable connect creates the research DB, then a read-only
    # connect works on the now-initialised file.
    try:
        conn = store.connect(research_db)
        conn.close()
        check("writable connect creates research DB", True)
    except Exception as exc:
        check("writable connect creates research DB", False, str(exc))

    try:
        conn = store.connect(research_db, readonly=True)
        conn.close()
        check("read-only connect on research DB", True)
    except Exception as exc:
        check("read-only connect on research DB", False, str(exc))

finally:
    shutil.rmtree(tmp, ignore_errors=True)


print(f"\n=== {'ALL PASS' if not FAILURES else 'FAIL: ' + str(len(FAILURES)) + ' failures'}: {len(FAILURES)} failures ===")
if FAILURES:
    print("Failures:", FAILURES)
    sys.exit(1)
print("All tests passed.")
sys.exit(0)
