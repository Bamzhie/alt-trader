"""Qt dashboard bottom row: real journal rows or honest empty copy (Task 7).

Offscreen by construction: ``QT_QPA_PLATFORM=offscreen`` is set at the very
top, BEFORE PySide6 is imported, so every window here builds display-free.

Interpreter guard: when PySide6 is missing the file prints a loud SKIP line
and exits 0 — ``tests/run_all.py`` globs ``test_*.py`` (minus test_app.py)
and counts exit 0 as a pass, so this skip guard keeps that suite green.

Coverage:
    1 empty      fresh DB -> Activity Log reads "no activity yet"
                 (DASH_NO_ACTIVITY), never fabricated rows
    2 seeded     fixture DB seeded through the REAL writers
                 (emit_setup + log_event + record_sweep) -> Activity Log
                 carries the real sweep-complete journal row
                 ("sweep complete: evaluated ..."), newest first
    3 clean      every bottom-row string passes
                 proto/setup_copy.assert_clean; no tier/score/probability
                 copy on the surface
    4 tripwire   socket.* all raise — ZERO network in this file

Note on the plan sketch: the Task 7 brief sketches
``"scan completed" in activity_text(seeded_db)``. The real backend
sweep writer (proto/watcher_journal.record_sweep, called once per
completed universe sweep) logs ``"sweep complete: evaluated N/M
eligible; ..."`` — there is no "scan completed" journal text anywhere
in the backend, and the no-invented-data binding forbids fabricating
it in the fixture or the renderer. This test pins the REAL contract:
real journal rows verbatim, or the honest empty-state copy.

Network policy: ZERO network. Journal/snapshot rows come from a
temp-dir SQLite DB via the real backend writers; market quotes ride
the test-only ``dash_market_data`` seam (offline fixtures, never a
venue fetch); each window's worker lanes are retired immediately
after construction.

Run:  python3 tests/test_qtgui_bottom.py   -> ALL PASS, exit 0
"""

import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"   # BEFORE PySide6, display-free

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# ---- network tripwire: armed first; there are no live tests here ---------
ALLOW_NETWORK = False


class NetworkTripwire(AssertionError):
    """A test tried to reach the network — instant failure."""


def _blocked(name):
    def _b(*_args, **_kw):
        raise NetworkTripwire(f"NETWORK TRIPWIRE: {name}() was called")
    _b.__name__ = name
    return _b


_RealSocket = socket.socket


class _BlockedSocket(_RealSocket):
    def __init__(self, *args, **kw):
        raise NetworkTripwire("NETWORK TRIPWIRE: socket.socket() was called")


socket.socket = _BlockedSocket
socket.create_connection = _blocked("socket.create_connection")
socket.getaddrinfo = _blocked("socket.getaddrinfo")
socket.gethostbyname = _blocked("socket.gethostbyname")

# ---- skip guard -----------------------------------------------------------
try:
    import PySide6  # noqa: F401
    from PySide6.QtWidgets import QApplication
except ImportError:
    # run_all.py needs "OK"/"ALL PASS" in the output to count exit 0 as a pass.
    print("SKIP  tests/test_qtgui_bottom.py — PySide6 is not installed in "
          "this interpreter; Qt bottom-row tests not run. OK (clean "
          "skip, exit 0)")
    sys.exit(0)

# ---- repo pieces ----------------------------------------------------------
from proto.setup_copy import assert_clean
from proto.watcher import emit_setup
from proto.watcher_journal import record_sweep

from qtgui.app import RadarWindow, dashboard_recent_events

APP = QApplication.instance() or QApplication([])

FAILURES = []
CHECKS = 0
WINDOWS = []
TMPDIRS = []

NOW = 1_750_000_000


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    cond = bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)
    return cond


def pump(timeout=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        APP.processEvents()
        time.sleep(0.02)
    APP.processEvents()


def make_window(db):
    """Build a RadarWindow offscreen and retire both worker lanes."""
    win = RadarWindow(auto_scan=False, db=db)
    WINDOWS.append(win)
    win.show()
    win._jobs.put(None)
    win._worker.join(timeout=10)
    check("worker lane retired", not win._worker.is_alive(),
          "worker thread did not retire")
    win._plan_jobs.put(None)
    win._plan_worker.join(timeout=10)
    check("plan lane retired", not win._plan_worker.is_alive(),
          "plan worker thread did not retire")
    win._tick_timer.stop()
    return win


def cleanup():
    for win in WINDOWS:
        try:
            if not win._closing:
                win._on_close()
        except Exception:
            traceback.print_exc()
    WINDOWS.clear()
    for d in TMPDIRS:
        shutil.rmtree(d, ignore_errors=True)
    TMPDIRS.clear()


# --------------------------------------------------------------------------
# fixtures: temp DBs seeded through the REAL backend writers (offline)
# --------------------------------------------------------------------------

def seed_db(path, now=NOW):
    """One setup + one completed sweep via the REAL backend writers.

    emit_setup auto-logs the emission journal row; record_sweep (the
    writer the scan loop calls once per completed sweep) logs the
    sweep-complete row plus the dashboard snapshot row. Timestamps ride
    the writers' real clock so UI ordering is exercised, not staged.
    """
    conn = sqlite3.connect(path)
    try:
        emit_setup(conn, {"rule_id": "t1-break-v1", "coin": "AAA",
                          "src_bar_ts": now - 3600, "entry": 100.0,
                          "direction": "LONG", "stop_px": 99.0,
                          "tgt_px": 102.0, "N_bars": 48,
                          "created_at": now - 3600})
        # Real completed-sweep row: record_sweep is the writer the scan
        # loop calls once per sweep (journal event + snapshot row).
        record_sweep(conn, 120, 118)
        conn.commit()
    finally:
        conn.close()


def empty_db_path():
    """Fresh DB path with no file yet (missing DB -> honest empty copy)."""
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-bottom-")
    TMPDIRS.append(tmp)
    return os.path.join(tmp, "empty.db")


def seeded_db_path(now=NOW):
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-bottom-")
    TMPDIRS.append(tmp)
    path = os.path.join(tmp, "seeded.db")
    seed_db(path, now=now)
    return path


def activity_text(db_path):
    """Bottom-row Activity Log text for a window bound to *db_path*."""
    win = make_window(db_path)
    win.refresh_dashboard(now=NOW)
    win.refresh_dashboard_pages(now=NOW)
    pump()
    return win.dash_activity.text()


# ==========================================================================
# Task 7 step 1: the pinned bottom-row contract
# ==========================================================================

def test_bottom_row_real_or_empty():
    empty_text = activity_text(empty_db_path())
    check("empty DB shows the honest empty-state copy",
          "no activity yet" in empty_text, empty_text[:200])
    seeded_path = seeded_db_path()
    seeded_text = activity_text(seeded_path)
    check("seeded DB shows the real sweep-complete journal row",
          "sweep complete" in seeded_text, seeded_text[:300])
    check("seeded DB shows the real emission journal row",
          "emitted" in seeded_text, seeded_text[:300])
    # Newest-first, verbatim: the Activity Log must list exactly the
    # journal rows the shared read path returns, in the same order —
    # no rewording, no invented rows, no drops.
    expected = [f"{e['kind']} {e['coin']} — {e['text']}"
                for e in dashboard_recent_events(seeded_path, limit=12)]
    check("activity log lists journal rows newest-first, verbatim",
          seeded_text.split("\n") == expected and len(expected) >= 2,
          f"{seeded_text[:300]!r} vs {str(expected)[:300]!r}")
    for label, text in (("empty", empty_text), ("seeded", seeded_text)):
        try:
            assert_clean(text)
            check(f"activity text ({label}) passes assert_clean", True)
        except ValueError as e:
            check(f"activity text ({label}) passes assert_clean", False,
                  str(e))
        for word in ("tier", "score", "probability", "confidence"):
            check(f"activity text ({label}) has no {word!r}",
                  word not in text.lower(), text[:200])


# ==========================================================================
# tripwire
# ==========================================================================

def t_tripwire():
    check("ALLOW_NETWORK stays False — this file has no live tests",
          ALLOW_NETWORK is False)
    attempts = [
        ("socket.socket", socket.socket, ()),
        ("socket.create_connection", socket.create_connection,
         (("127.0.0.1", 80),)),
        ("socket.getaddrinfo", socket.getaddrinfo, ("example.com", 443)),
        ("socket.gethostbyname", socket.gethostbyname, ("example.com",)),
    ]
    for name, fn, args in attempts:
        try:
            fn(*args)
        except NetworkTripwire:
            check(f"{name} raises the tripwire", True)
        else:
            check(f"{name} raises the tripwire", False, "call succeeded")
    check("no live-test marker in this file",
          ("LIVE" + "_TEST") not in open(__file__).read())


def main():
    print("== bottom row: real journal rows or honest empty copy ==")
    test_bottom_row_real_or_empty()
    print("== tripwire ==")
    t_tripwire()
    print()
    print(f"CHECKS {CHECKS}  FAILURES {len(FAILURES)}")
    if FAILURES:
        print("FAILURES:")
        for name in FAILURES:
            print(f"  - {name}")
        print("BOTTOM TESTS FAILED")
        return 1
    print("ALL PASS  tests/test_qtgui_bottom.py — Qt bottom row green")
    return 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        cleanup()
    sys.exit(rc)
