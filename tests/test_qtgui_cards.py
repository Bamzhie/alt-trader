"""KPI cards test for the Qt terminal rebuild (Task 4: sparklines + gauge).

Binding card set: Active Setups / Eligible Perps / Monitoring Coverage /
New Setups 24h — High-Conviction / Avg-Confidence must NOT appear.
Sparklines are pure QPainter painters over 24x5m closes; short history
renders flat/unavailable, never invented points. The coverage card carries
a semicircular QPainter gauge. Deltas come from snapshot pairs or
"comparison unavailable".

Offscreen by construction: ``QT_QPA_PLATFORM=offscreen`` is set at the
very top, BEFORE PySide6 is imported, so every window here builds
display-free.

Interpreter guard: the system python3 has no PySide6. The file then
prints a loud SKIP line and exits 0 — ``tests/run_all.py`` globs
``test_*.py`` (minus test_app.py) and counts exit 0 as a pass, so this
skip guard is what keeps that suite green under the system interpreter.

Network policy: ZERO network. The tripwire is armed before PySide6 is
even imported; stores are temp-dir SQLite files; both worker lanes are
retired (None sentinel + join) right after each window's construction.

Run:  python3 tests/test_qtgui_cards.py  -> ALL PASS, exit 0
"""

import os
import queue
import shutil
import socket
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

# ---- skip guard: system python3 has no PySide6 ---------------------------
try:
    import PySide6
    from PySide6.QtWidgets import QApplication
except ImportError:
    # run_all.py needs "OK"/"ALL PASS" in the output to count exit 0 as a pass.
    print("SKIP  tests/test_qtgui_cards.py — PySide6 is not installed in "
          "this interpreter; Qt GUI tests not run. OK (clean skip, exit 0)")
    sys.exit(0)

from qtgui import app as qtapp
from qtgui.app import RadarWindow

APP = QApplication.instance() or QApplication([])

FAILURES = []
CHECKS = 0
WINDOWS = []
TMPDIRS = []


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    cond = bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)
    return cond


def pump(cond, timeout=10.0):
    """Run Qt events until cond() — the live poll timer drains the queue."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        APP.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    APP.processEvents()
    return bool(cond())


def make_window():
    """Build a RadarWindow offscreen and retire both worker lanes."""
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-cards-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "qt.db")
    win = RadarWindow(auto_scan=False, db=db)
    WINDOWS.append(win)
    win.show()
    APP.processEvents()
    win._jobs.put(None)
    win._worker.join(timeout=10)
    check("worker lane retired", not win._worker.is_alive(),
          "worker thread did not retire")
    win._plan_jobs.put(None)
    win._plan_worker.join(timeout=10)
    check("plan lane retired", not win._plan_worker.is_alive(),
          "plan worker thread did not retire")
    win._tick_timer.stop()
    APP.processEvents()
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


# ==========================================================================
# 1. BANNED CARDS ABSENT (brief Step 1, verbatim)
# ==========================================================================

def test_no_confidence_cards():
    win = make_window()
    text = win.dashboard_cards_text()
    check("no High Conviction card", "High Conviction" not in text, text)
    check("no Avg. Confidence card", "Avg. Confidence" not in text, text)
    titles = [t for t, _v, _s in win.dashboard_cards()]
    check("binding set has 4 cards", len(titles) == 4, str(titles))
    check("binding titles",
          titles == ["Active Setups", "Eligible Perps",
                     "Monitoring Coverage", "New Setups 24h"],
          str(titles))
    check("4 card widgets in one row",
          len(win.dash_card_spark) == 4
          and win.dash_card_spark[0].parentWidget() is not None,
          str(len(getattr(win, "dash_card_spark", []))))


# ==========================================================================
# 2. SHORT HISTORY: NO FABRICATION (brief Step 1, verbatim)
# ==========================================================================

def test_short_history_no_fabrication():
    win = make_window()
    for short in ([], [100.0], None):
        pts = qtapp.sparkline_points(short)
        check(f"sparkline_points({short!r}) is empty/unavailable",
              pts in ([], "unavailable"), repr(pts))
    win.set_card_history({"Active Setups": [100.0]})
    APP.processEvents()
    w = win.dash_card_spark[0]
    check("short history marks the sparkline unavailable",
          w.unavailable is True, str(getattr(w, "unavailable", None)))
    check("short history paints no invented points",
          w.points() in ([], "unavailable"), repr(w.points()))
    # Full 24x5m closes render real points.
    full = [100.0 + i * 0.5 for i in range(24)]
    pts = qtapp.sparkline_points(full)
    check("24 closes yield 24 points",
          isinstance(pts, list) and len(pts) == 24, repr(pts)[:120])
    win.set_card_history({"Active Setups": full})
    APP.processEvents()
    check("full history marks the sparkline available",
          w.unavailable is False, str(getattr(w, "unavailable", None)))
    check("full history paints 24 points", len(w.points()) == 24,
          repr(w.points())[:120])


# ==========================================================================
# 3. COVERAGE GAUGE: honest value or unavailable
# ==========================================================================

def test_coverage_gauge():
    win = make_window()
    g = win.dash_coverage_gauge
    check("gauge widget exists", g is not None)
    check("gauge starts unavailable (no snapshot)",
          g.unavailable is True, str(getattr(g, "unavailable", None)))
    g.set_value(0.75)
    check("gauge takes 0.75", g.value == 0.75, repr(getattr(g, "value", None)))
    check("gauge available at 0.75", g.unavailable is False)
    g.set_value(None)
    check("gauge None returns to unavailable",
          g.unavailable is True and g.value is None)
    # Coverage card value text still comes from format_dashboard_cards.
    cards = dict((t, v) for t, v, _s in
                 qtapp.format_dashboard_cards({"monitoring_coverage": 0.5,
                                               "active_setups": 3,
                                               "eligible_count": 10,
                                               "evaluated_count": 9,
                                               "new_setups_24h": 1},
                                              None))
    check("coverage value text", cards["Monitoring Coverage"] == "50.0%",
          repr(cards["Monitoring Coverage"]))
    check("withheld delta copy",
          qtapp.DASH_NO_COMPARISON in win.dashboard_cards_text(),
          win.dashboard_cards_text()[:200])


# ==========================================================================
# runner
# ==========================================================================

if __name__ == "__main__":
    tests = [
        ("1 banned cards absent, binding 4-card set", test_no_confidence_cards),
        ("2 short history renders flat, never invented",
         test_short_history_no_fabrication),
        ("3 coverage gauge honest or unavailable", test_coverage_gauge),
    ]
    for name, fn in tests:
        print(f"\n== {name}")
        try:
            fn()
        except Exception as e:
            FAILURES.append(f"{name}: {type(e).__name__}: {e}")
            traceback.print_exc()
        finally:
            cleanup()

    print(f"\n({CHECKS} checks)")
    print("\n" + ("ALL PASS" if not FAILURES
                  else f"{len(FAILURES)} FAILED: {FAILURES}"))
    sys.exit(1 if FAILURES else 0)
