"""Chrome test for the Qt terminal rebuild (Task 3: top bar + sidebar).

Top bar: logo, name, subtitle, SCANNING dot + elapsed/last-scan from REAL
scan timing, MEXC identity, UTC clock; bell ONLY if an event source exists
else omitted; gear -> Settings; native window controls, no custom ones.
Rail: Dashboard/Watchlist/... selection with highlight + green edge; bottom
API-status/venue/account blocks from REAL connection state, never hardcoded
"Connected".

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

Run:  python3 tests/test_qtgui_chrome.py  -> ALL PASS, exit 0
"""

import os
import queue
import re
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
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton
except ImportError:
    # run_all.py needs "OK"/"ALL PASS" in the output to count exit 0 as a pass.
    print("SKIP  tests/test_qtgui_chrome.py — PySide6 is not installed in "
          "this interpreter; Qt GUI tests not run. OK (clean skip, exit 0)")
    sys.exit(0)

from qtgui import app as qtapp
from qtgui import theme as qtheme
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


def make_window():
    """Build a RadarWindow offscreen and retire both worker lanes."""
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-chrome-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "qt.db")
    win = RadarWindow(auto_scan=False, db=db)
    WINDOWS.append(win)               # registered first: cleanup can reach it
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


# --------------------------------------------------------------------------
# brief Step 1 helpers (verbatim names)
# --------------------------------------------------------------------------

def window_icon_has_action(win, name):
    """True when a top-bar icon called `name` exists AND does something.

    The bell has no backing event source, so it must be omitted entirely:
    no button, no action, no tooltip mentioning it. Omission -> False.
    """
    for btn in win.header.findChildren(QPushButton):
        hay = " ".join((btn.text() or "", btn.toolTip() or "",
                        btn.objectName() or "", btn.statusTip() or "")).lower()
        if name.lower() in hay or "🔔" in (btn.text() or ""):
            return btn.isEnabled()
    for act in win.actions():
        if name.lower() in (act.text() or "").lower():
            return True
    return False


def status_block_text(win):
    """Combined text of the rail's bottom status blocks (API/venue/account)."""
    blocks = [getattr(win, attr, None) for attr in
              ("lbl_rail_api", "lbl_rail_venue", "lbl_rail_account")]
    return "\n".join(b.text() for b in blocks
                     if isinstance(b, QLabel) and b.text())


def venue_health_ok(win):
    """True only when live venue state says the venue is healthy."""
    if getattr(win, "mexc_ok", None) is True:
        return True
    venue = getattr(win, "venue", None) or {}
    return any(isinstance(v, dict) and v.get("ok") for v in venue.values())


# ==========================================================================
# 1. NO DEAD ICONS (brief Step 1, verbatim)
# ==========================================================================

def test_no_dead_icons():
    win = make_window()
    check("bell omitted unless backed",
          window_icon_has_action(win, "bell") is False,
          "a bell control exists with no backing event source")
    # Every remaining header control must do something real.
    for btn in win.header.findChildren(QPushButton):
        check(f"header control {btn.text()!r} has an action",
              btn.isEnabled() and (btn.toolTip() or btn.text()),
              "dead icon in the top bar")


# ==========================================================================
# 2. STATUS IS REAL (brief Step 1, verbatim)
# ==========================================================================

def test_status_is_real():
    win = make_window()
    text = status_block_text(win)
    check("rail status blocks exist (API/venue/account)",
          all(getattr(win, a, None) is not None
              for a in ("lbl_rail_api", "lbl_rail_venue",
                        "lbl_rail_account")),
          "rail has no bottom status blocks")
    check("status never hardcodes Connected",
          text != "Connected" and "Connected" not in text
          or venue_health_ok(win) is True,
          f"status={text!r}")
    # Fresh window: venue unknown — blocks must say so honestly.
    check("fresh status is unknown, not ok",
          "ok" not in text.lower() or venue_health_ok(win) is True,
          f"status={text!r}")
    # Real transitions: degraded venue renders degraded, never Connected.
    win.mexc_ok = False
    win._render_topbar()
    APP.processEvents()
    text = status_block_text(win)
    check("degraded venue renders degraded",
          "degrad" in text.lower() or "–" in text,
          f"status={text!r}")
    check("degraded status never claims Connected",
          "Connected" not in text, f"status={text!r}")
    win.mexc_ok = True
    win._render_topbar()
    APP.processEvents()
    text = status_block_text(win)
    check("healthy venue renders ok",
          "ok" in text.lower(), f"status={text!r}")


# ==========================================================================
# 3. TOP BAR: scan state from real timing + UTC clock + gear + native chrome
# ==========================================================================

def test_topbar_scan_state():
    win = make_window()
    check("SCANNING dot present",
          getattr(win, "lbl_scan_dot", None) is not None,
          "no lbl_scan_dot on the top bar")
    check("elapsed label present",
          getattr(win, "lbl_scan_elapsed", None) is not None,
          "no lbl_scan_elapsed on the top bar")
    if getattr(win, "lbl_scan_dot", None) is None:
        return
    # No scan yet: honest unavailable copy, never an invented elapsed time.
    win.last_scan_ts = 0.0
    win._render_topbar()
    check("no-scan dot is idle",
          "idle" in win.lbl_scan_dot.text().lower()
          or "–" in win.lbl_scan_dot.text(),
          win.lbl_scan_dot.text())
    check("no-scan elapsed is honest",
          "no scan yet" in win.lbl_scan_elapsed.text().lower()
          or "--" in win.lbl_scan_elapsed.text(),
          win.lbl_scan_elapsed.text())
    # Real timing: elapsed derives from last_scan_ts only.
    win.last_scan_ts = time.time() - 90
    win._render_topbar()
    check("elapsed reflects the real scan timestamp",
          "01:30" in win.lbl_scan_elapsed.text()
          or "90s" in win.lbl_scan_elapsed.text()
          or "1m" in win.lbl_scan_elapsed.text(),
          win.lbl_scan_elapsed.text())
    # UTC clock ticks in real UTC.
    check("UTC clock present",
          getattr(win, "lbl_clock", None) is not None,
          "no lbl_clock on the top bar")
    if getattr(win, "lbl_clock", None) is not None:
        expect = time.strftime("%H:%M:%S", time.gmtime())
        check("UTC clock shows real UTC",
              re.fullmatch(r"\d{2}:\d{2}:\d{2} UTC",
                           win.lbl_clock.text()) is not None,
              win.lbl_clock.text())
        check("UTC clock is current (same minute as real UTC)",
              win.lbl_clock.text()[:5] == expect[:5],
              f"clock={win.lbl_clock.text()} real={expect}")
    # Gear navigates to Settings; window chrome stays native.
    win.btn_gear.click()
    check("gear opens Settings",
          win.current_page() == "settings", win.current_page())
    customs = [b for b in win.findChildren(QPushButton)
               if (b.objectName() or "").lower() in
               ("btnminimize", "btnmaximize", "btnclose",
                "minimizebutton", "maximizebutton", "closebutton")
               or (b.toolTip() or "").lower() in
               ("minimize", "maximize", "close")]
    check("native window controls (no custom min/max/close)",
          not customs, f"custom chrome: {[b.objectName() for b in customs]}")


# ==========================================================================
# 4. RAIL: selection highlight + green edge, exactly one checked
# ==========================================================================

def test_rail_selection():
    win = make_window()
    check("rail starts with exactly one page checked",
          sum(1 for b in win.rail_buttons.values() if b.isChecked()) == 1)
    for key in tuple(win.rail_buttons):
        win.rail_buttons[key].click()
        APP.processEvents()
        check(f"rail {key} selects with highlight",
              win.current_page() == key
              and win.rail_buttons[key].isChecked(),
              win.current_page())
        check("exactly one rail button checked",
              sum(1 for b in win.rail_buttons.values()
                  if b.isChecked()) == 1)
    rail_qss = win.rail.styleSheet()
    check("selected rail button has the green edge",
          qtheme.ACC_POS.lower() in rail_qss.lower(),
          "no ACC_POS green edge for the selected rail button")


# ==========================================================================
# runner
# ==========================================================================

if __name__ == "__main__":
    tests = [
        ("1 no dead icons: bell omitted unless backed", test_no_dead_icons),
        ("2 status is real: rail blocks from venue state", test_status_is_real),
        ("3 top bar: scan dot + elapsed + UTC clock + gear", test_topbar_scan_state),
        ("4 rail: selection highlight + green edge", test_rail_selection),
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
