"""Qt watcher dock (Task 5).

Offscreen by construction: ``QT_QPA_PLATFORM=offscreen`` is set at the very
top, BEFORE PySide6 is imported, so every window here builds display-free.

Interpreter guard: the system python3 has no PySide6. The file then prints
a loud SKIP line and exits 0 — ``tests/run_all.py`` globs ``test_*.py``
(minus test_app.py) and counts exit 0 as a pass, so this skip guard is what
keeps that suite green under the system interpreter.

Coverage (mirrors tests/test_gui_watcher.py's intent, Qt shapes):
  1 columns      watcher dock table headers match the TUI columns:
                 ID RULE COIN DIR STATE ENTRY STOP TGT
  2 verbatim     STATE cells render STATE_WORDS verbs byte-identical
                 ("resolved", never a paraphrase); detail carries the
                 research banner
  3 selection    selection is pinned to the setup id: selecting a row then
                 re-feeding a reordered panel keeps the same setup selected
  4 actions      Pause / Disable-rule buttons queue worker-side jobs
                 (writers stay worker-side); a paused panel flips the button
                 to Resume and shows the paused notice
  5 contained    a None panel renders "watcher data unavailable" with the
                 scanner table untouched
  6 correlated   the detail pane carries a CORRELATED OPENS section shaped
                 exactly like proto.watcher_report's (n=2 same-coin group)

Network policy: ZERO network. Panels come from temp-dir SQLite
(emit_setup); each window's worker lanes are retired immediately after
construction (None sentinel + join — the ctor's offline stats/watcher jobs
run before the sentinel, so the real queue -> poll -> render path is
exercised). No scan / plan / collect / resolve job can ever run.

Run:  .venv-qt/bin/python tests/test_qtgui_watcher.py   -> ALL PASS, exit 0
      python3 tests/test_qtgui_watcher.py               -> SKIP, exit 0
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
    import PySide6  # noqa: F401
    from PySide6.QtCore import Qt  # noqa: F401
    from PySide6.QtWidgets import (QApplication, QPushButton, QDockWidget,
                                   QTableWidget)
except ImportError:
    # run_all.py needs "OK"/"ALL PASS" in the output to count exit 0 as a pass.
    print("SKIP  tests/test_qtgui_watcher.py — PySide6 is not installed in "
          "this interpreter; Qt watcher tests not run. OK (clean skip, "
          "exit 0)")
    sys.exit(0)

# ---- repo pieces ----------------------------------------------------------
from proto.watcher_view import RESEARCH_BANNER, STATE_WORDS, build_panel
from proto import watcher_feed as feed_mod

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


def make_window(db=None):
    """Build a RadarWindow offscreen and retire both worker lanes."""
    tmp = None
    if db is None:
        tmp = tempfile.mkdtemp(prefix="alt-radar-qt-watcher-")
        TMPDIRS.append(tmp)
        db = os.path.join(tmp, "qt.db")
    win = RadarWindow(auto_scan=False, db=db)
    win._test_tmp = tmp
    WINDOWS.append(win)
    win.show()
    # Retire the lanes exactly like tests/test_qtgui.py: the ctor's offline
    # stats/watcher jobs sit in front of the sentinel, so queue -> poll ->
    # render still runs for real, but no other job can ever execute.
    win._jobs.put(None)
    win._worker.join(timeout=10)
    check("worker lane retired", not win._worker.is_alive(),
          "worker thread did not retire")
    win._plan_jobs.put(None)
    win._plan_worker.join(timeout=10)
    check("plan lane retired", not win._plan_worker.is_alive(),
          "plan worker thread did not retire")
    win._tick_timer.stop()
    # Drain ctor-queued results synchronously so every test starts from a
    # deterministic state: the poll timer would otherwise deliver the ctor's
    # success results at an arbitrary later moment, racing any direct
    # _on_watcher injection (the t_unavailable flake).
    win._poll()
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


def seed_watcher_db(path):
    """Temp DB: two active same-coin setups (correlated n=2) + one resolved.

    The same-(coin, rule) pair uses src bars 198000s apart, past the T1
    24h emit cooldown, so both rows persist (checked empirically: same-bar
    repeats dedupe to one id).
    """
    import sqlite3
    from proto.watcher import emit_setup

    conn = sqlite3.connect(path)
    try:
        sid_a1 = emit_setup(
            conn, {"rule_id": "t1-break-v1", "coin": "BBB",
                   "src_bar_ts": 2000, "entry": 100.0,
                   "direction": "SHORT", "stop_px": 101.0,
                   "tgt_px": 98.0})
        sid_a2 = emit_setup(
            conn, {"rule_id": "t1-break-v1", "coin": "BBB",
                   "src_bar_ts": 200000, "entry": 110.0,
                   "direction": "LONG", "stop_px": 109.0,
                   "tgt_px": 112.0})
        sid_res = emit_setup(
            conn, {"rule_id": "t1-break-v1", "coin": "AAA",
                   "src_bar_ts": 1000, "entry": 50.0,
                   "direction": "LONG", "stop_px": 49.0,
                   "tgt_px": 52.0})
        conn.execute("UPDATE watcher_setup SET state='resolved' WHERE id=?",
                     (sid_res,))
        conn.commit()
    finally:
        conn.close()
    assert len({sid_a1, sid_a2, sid_res}) == 3, (sid_a1, sid_a2, sid_res)
    return sid_a1, sid_a2, sid_res


def watcher_row(win, sid):
    """Row index of a setup id in the watcher table (None when absent)."""
    for r in range(win.watcher_table.rowCount()):
        item = win.watcher_table.item(r, 0)
        if item is not None and item.data(Qt.ItemDataRole.UserRole) == sid:
            return r
    return None


def buttons(win, text):
    return [b for b in win.findChildren(QPushButton)
            if b.text() == text and b.isVisible()]


# ==========================================================================
# 1. DOCK COLUMNS MATCH THE TUI
# ==========================================================================

def t_columns():
    win = make_window()
    check("watcher dock exists",
          isinstance(getattr(win, "watcher_dock", None), QDockWidget))
    check("watcher table exists",
          isinstance(getattr(win, "watcher_table", None), QTableWidget))
    check("dock.table alias (brief Step 1 shape)",
          getattr(win.watcher_dock, "table", None) is win.watcher_table)
    headers = [win.watcher_table.horizontalHeaderItem(i).text()
               for i in range(win.watcher_table.columnCount())]
    check("dock columns match TUI",
          headers == ["ID", "RULE", "COIN", "DIR", "STATE", "ENTRY",
                      "STOP", "TGT"], str(headers))
    check("watcher detail is read-only", win.watcher_detail.isReadOnly())
    check("pause button present", len(buttons(win, "Pause watcher")) == 1)
    check("disable button present", len(buttons(win, "Disable rule")) == 1)
    check("refresh button present",
          len(buttons(win, "Refresh watcher")) == 1)


# ==========================================================================
# 2. STATE TEXT IS VERBATIM
# ==========================================================================

def t_verbatim():
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-watcher-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "watch.db")
    _a1, _a2, sid_res = seed_watcher_db(db)
    win = make_window(db=db)
    ok = pump(lambda: win.watcher_panel is not None)
    check("worker panel reaches the dock", ok)
    if not ok:
        return
    state_col = 4
    row = watcher_row(win, sid_res)
    check("resolved setup has a table row", row is not None)
    cell = (win.watcher_table.item(row, state_col).text()
            if row is not None else None)
    check("state text is verbatim", cell == "resolved", repr(cell))
    check("verbatim matches STATE_WORDS",
          cell == STATE_WORDS["resolved"], repr(cell))
    # The detail follows the selection: select the resolved row first.
    win.watcher_table.selectRow(row)
    APP.processEvents()
    detail = win.watcher_detail.toPlainText()
    check("detail shows the resolved state",
          STATE_WORDS["resolved"] in detail, detail[:200])
    check("detail carries the research banner",
          "research, shadow-only" in detail and RESEARCH_BANNER in detail,
          detail[:200])


# ==========================================================================
# 3. SELECTION PINNED TO SETUP ID ACROSS REORDERS
# ==========================================================================

def t_selection_by_id():
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-watcher-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "watch.db")
    sid_a1, _a2, sid_res = seed_watcher_db(db)
    win = make_window(db=db)
    ok = pump(lambda: win.watcher_panel is not None)
    check("worker panel reaches the dock", ok)
    if not ok:
        return
    check("initial selection is the first active setup",
          win.watcher_selected == sid_a1,
          f"{win.watcher_selected!r} != {sid_a1!r}")
    row = watcher_row(win, sid_res)
    win.watcher_table.selectRow(row)
    APP.processEvents()
    check("row click selects by setup id",
          win.watcher_selected == sid_res, str(win.watcher_selected))
    # Re-feed the same setups in reverse input order through the real
    # handler: the selection must stay on the setup id, not the row.
    conn = feed_mod.open_ro(db)
    try:
        rows = feed_mod.fetch_setups(conn)
        evals = feed_mod.fetch_latest_evals(conn)
        paused, disabled = feed_mod.get_flags(conn)
    finally:
        conn.close()
    reordered = build_panel(list(reversed(rows)), evals,
                            selected_id=sid_res, paused=paused,
                            disabled=disabled)
    win._on_watcher({"kind": "watcher", "ok": True, "panel": reordered,
                     "selected_id": reordered.selected_id})
    APP.processEvents()
    check("selection survives the reorder",
          win.watcher_selected == sid_res, str(win.watcher_selected))
    detail = win.watcher_detail.toPlainText()
    check("detail follows the id, not the row",
          STATE_WORDS["resolved"] in detail, detail[:200])


# ==========================================================================
# 4. PAUSE / DISABLE QUEUE WORKER-SIDE JOBS
# ==========================================================================

def t_actions():
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-watcher-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "watch.db")
    seed_watcher_db(db)
    win = make_window(db=db)
    ok = pump(lambda: win.watcher_panel is not None)
    check("worker panel reaches the dock", ok)
    if not ok:
        return
    drain(win._jobs)
    buttons(win, "Pause watcher")[0].click()
    jobs = drain(win._jobs)
    check("pause queues exactly one worker job", len(jobs) == 1, str(jobs))
    check("pause job shape",
          jobs and jobs[0].get("cmd") == "watcher_pause"
          and jobs[0].get("paused") is True, str(jobs))
    # A paused panel flips the button and shows the notice.
    paused_panel = build_panel([], {}, paused=True)
    win._on_watcher({"kind": "watcher_pause", "ok": True,
                     "panel": paused_panel,
                     "selected_id": paused_panel.selected_id})
    APP.processEvents()
    check("paused panel flips the button to Resume",
          buttons(win, "Resume watcher") and not buttons(win, "Pause watcher"),
          buttons(win, "Pause watcher") and "still Pause watcher")
    check("paused notice in the detail",
          "paused" in win.watcher_detail.toPlainText(),
          win.watcher_detail.toPlainText()[:200])
    buttons(win, "Refresh watcher")[0].click()
    jobs = drain(win._jobs)
    check("refresh re-queues a watcher job",
          len(jobs) == 1 and jobs[0].get("cmd") == "watcher",
          str(jobs))
    # Reload the seeded panel (refresh above never runs: lane retired).
    win._on_watcher({"kind": "watcher", "ok": True,
                     "panel": feed_mod.load_panel(db),
                     "selected_id": None})
    APP.processEvents()
    drain(win._jobs)
    buttons(win, "Disable rule")[0].click()
    jobs = drain(win._jobs)
    check("disable queues exactly one worker job",
          len(jobs) == 1, str(jobs))
    check("disable job shape",
          jobs and jobs[0].get("cmd") == "watcher_disable"
          and jobs[0].get("rule") == "t1-break-v1"
          and jobs[0].get("disabled") is True, str(jobs))


# ==========================================================================
# 5. CONTAINED FAILURE LEAVES THE SCANNER INTACT
# ==========================================================================

def t_unavailable():
    win = make_window()
    before = win.tree.rowCount()
    win._on_watcher({"kind": "watcher", "ok": True, "panel": None,
                     "error": "watcher db missing: /nonexistent/x.db"})
    APP.processEvents()
    detail = win.watcher_detail.toPlainText()
    check("unavailable fallback names the watcher",
          "watcher data unavailable" in detail, detail[:200])
    check("fallback keeps the scanner intact promise",
          "scanner intact" in detail, detail[:200])
    check("scanner table untouched", win.tree.rowCount() == before)


# ==========================================================================
# 6. CORRELATED-OPENS LINE (report-shaped, Task 6 to unify)
# ==========================================================================

def t_correlated():
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-watcher-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "watch.db")
    seed_watcher_db(db)
    win = make_window(db=db)
    ok = pump(lambda: win.watcher_panel is not None)
    check("worker panel reaches the dock", ok)
    if not ok:
        return
    detail = win.watcher_detail.toPlainText()
    check("detail carries the CORRELATED OPENS section",
          "CORRELATED OPENS" in detail, detail[-400:])
    check("same-coin pair reads as one n=2 group (not independent bets)",
          "n=2" in detail and "not independent bets" in detail,
          detail[-400:])


def main():
    print("== 1 dock columns match TUI ==")
    t_columns()
    print("== 2 state text is verbatim ==")
    t_verbatim()
    print("== 3 selection pinned to setup id ==")
    t_selection_by_id()
    print("== 4 pause/disable queue worker jobs ==")
    t_actions()
    print("== 5 contained failure, scanner intact ==")
    t_unavailable()
    print("== 6 correlated-opens line ==")
    t_correlated()
    print()
    print(f"CHECKS {CHECKS}  FAILURES {len(FAILURES)}")
    if FAILURES:
        print("FAILURES:")
        for name in FAILURES:
            print(f"  - {name}")
        print("WATCHER DOCK TESTS FAILED")
        return 1
    print("ALL PASS  tests/test_qtgui_watcher.py — Qt watcher dock green")
    return 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        cleanup()
    sys.exit(rc)
