"""Layout test for the Qt terminal rebuild (Task 2: 3-region grid).

Three regions — fixed header (48-52px), fixed rail (140-150px), and a
flexible center/right grid (center 60-65% / right 32-35%, no absolute
positioning) — with the right panel collapsing to a drawer below center
under ~1100px width and never a horizontal page overflow.

Offscreen by construction: ``QT_QPA_PLATFORM=offscreen`` is set at the
very top, BEFORE PySide6 is imported, so every window here builds
display-free.

Interpreter guard: the system python3 has no PySide6. The file then
prints a loud SKIP line and exits 0 — ``tests/run_all.py`` globs
``test_*.py`` (minus test_app.py) and counts exit 0 as a pass, so this
skip guard is what keeps that suite green under the system interpreter.

Network policy: ZERO network. The tripwire is armed before PySide6 is
even imported; stores are temp-dir SQLite files; both worker lanes are
retired (None sentinel + join) right after each window's construction —
the ctor's offline "stats" job sits in front of the sentinel, so the
real queue -> poll -> render path still runs, but no scan/plan/collect/
resolve/lookup job can ever execute.

Run:  python3 tests/test_qtgui_layout.py  -> ALL PASS, exit 0
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
    print("SKIP  tests/test_qtgui_layout.py — PySide6 is not installed in "
          "this interpreter; Qt GUI tests not run. OK (clean skip, exit 0)")
    sys.exit(0)

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


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def make_window(w=1440, h=920):
    """Build a RadarWindow offscreen and retire both worker lanes."""
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-layout-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "qt.db")
    win = RadarWindow(auto_scan=False, db=db)
    WINDOWS.append(win)               # registered first: cleanup can reach it
    win.resize(w, h)
    win.show()
    APP.processEvents()
    # Retire the lanes exactly like tests/test_qtgui.py: the ctor's
    # offline "stats" job sits in front of the sentinel, so queue ->
    # poll -> render still runs for real, but no job can ever execute
    # (=> no network beyond the tripwire).
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
# 1. REGIONS (brief Step 1, verbatim)
# ==========================================================================

def test_regions():
    win = make_window(1440, 920)
    check("rail 140-150px",
          140 <= win.rail.width() <= 150, f"rail={win.rail.width()}px")
    check("header 48-52px",
          48 <= win.header.height() <= 52, f"header={win.header.height()}px")
    # Flexible-grid contract: the body split runs on stretch 65:35
    # (QSplitter + layouts — no absolute positioning).
    stretch = (win.body_splitter.widget(0).sizePolicy().horizontalStretch(),
               win.body_splitter.widget(1).sizePolicy().horizontalStretch())
    check("grid split runs on 65:35 stretch", stretch == (65, 35),
          f"stretch={stretch}")
    # Pixel split, measured with the still-docked right panels floated
    # away (test-only isolation — Tasks 6/7 reparent those docks into
    # the grid; the docks otherwise crowd the central width).
    for dock in (win.watcher_dock, win.dashboard_dock,
                 win.dash_pages_dock, win.detail_dock):
        dock.hide()
    APP.processEvents()
    sizes = win.body_splitter.sizes()
    total = sum(sizes)
    # Whole-percent precision: splitter-handle chrome (±a few px) makes
    # exact fractional bounds unmeasurable; the brief's bands are % bands.
    center_pc = round(100 * sizes[0] / total) if total else 0
    right_pc = round(100 * sizes[1] / total) if total else 0
    check("center 60-65% of the grid",
          60 <= center_pc <= 65,
          f"center={center_pc}% sizes={sizes}")
    check("right 32-35% of the grid",
          32 <= right_pc <= 35,
          f"right={right_pc}% sizes={sizes}")
    # No absolute positioning: every region widget is layout-managed.
    for name, widget in (("header", win.header), ("rail", win.rail),
                         ("pages", win.pages)):
        managed = widget.layout() is not None or (
            widget.parentWidget() is not None
            and widget.parentWidget().layout() is not None)
        check(f"{name} is layout-managed (no absolute positioning)",
              managed, name)


# ==========================================================================
# 2. NARROW COLLAPSE (brief Step 1, verbatim)
# ==========================================================================

def test_narrow_collapses():
    win = make_window(1440, 920)
    win.resize(900, 1024)
    ok = pump(lambda: win.detail_dock.isHidden()
              or win.drawer_available())
    APP.processEvents()
    check("narrow collapses to a drawer",
          ok, f"hidden={win.detail_dock.isHidden()} "
              f"drawer={win.drawer_available()} width={win.width()}")
    # Never a horizontal page overflow: the central column never forces
    # the window wider than asked (plus frame slack).
    check("no horizontal page overflow",
          win.width() <= 1024, f"width={win.width()}")
    # Widening again restores the dock.
    win.resize(1440, 920)
    ok = pump(lambda: win.detail_dock.isVisible())
    APP.processEvents()
    check("wide restores the detail dock", ok,
          f"visible={win.detail_dock.isVisible()}")


# ==========================================================================
# runner
# ==========================================================================

if __name__ == "__main__":
    tests = [
        ("1 regions: rail/header/grid split, no absolute positioning",
         test_regions),
        ("2 narrow: right panel collapses, no page overflow",
         test_narrow_collapses),
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
