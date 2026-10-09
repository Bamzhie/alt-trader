"""Functional tests for the PySide6 / Qt-Widgets desktop GUI (task Q2).

Offscreen by construction: ``QT_QPA_PLATFORM=offscreen`` is set at the very
top, BEFORE PySide6 is imported, so every window here builds display-free.

Interpreter guard: the system python3 has no PySide6. The file then prints
a loud SKIP line and exits 0 — ``tests/run_all.py`` globs ``test_*.py``
(minus test_app.py) and counts exit 0 as a pass, so this skip guard is what
keeps that suite green under the system interpreter.

Coverage (mirrors tests/test_gui.py's intent, Qt shapes):
  1 build        window constructs offscreen with auto_scan=False + temp db,
                 both worker lanes retired; layout, badges, groups, controls
                 and initial values present; the ctor's offline "stats" job
                 round-trips queue -> poll -> render for real
  2 table        fake proto.scorer Scorecards: row count/order, exact cells
                 (tkinter parity), direction tint + banding, WATCH text and
                 foreground, vetoed split with comma-joined codes
  3 search       Find text narrows rows; Enter with a match selects it (no
                 lookup job); Enter without a match queues a lookup job —
                 asserted on the queue, the retired worker never runs it
  4 sort/dir     Dir combo filters, Sort combo reorders, a real header click
                 re-sorts (Qt-only path), _CellItem numeric comparison
  5 segments     Top 10 / Watch / New: empty digest = guidance + no modal,
                 seeded digest = modal rows; a pick jumps the main selection.
                 QDialog.exec() is driven by QTimer.singleShot (see below)
  6 outcomes     a stats message renders counts line, direction lines, the
                 24h hit line and the plans-live line; zero-shape too
  7 snapshot     close writes .last_entries (proto/snapshot), relaunch
                 repopulates instantly; the plain close() path writes too
  8 tripwire     socket.socket / create_connection / getaddrinfo /
                 gethostbyname all raise — and ALLOW_NETWORK stays False:
                 there are NO live tests in this file

Network policy: ZERO network. The tripwire is armed before PySide6 is even
imported; cards are fake Scorecard objects; stores are temp-dir SQLite
files; both worker lanes are retired (None sentinel + join) right after
each window's construction — the ctor's offline "stats" job sits in front
of the sentinel, so the real queue -> poll -> render path still runs, but
no scan/plan/collect/resolve/lookup job can ever execute.

Modal handling (Q1 report concern 3): list dialogs run a nested event loop
(``QDialog.exec``). Every dialog test arms ``QTimer.singleShot(0, hook)``
so the hook inspects (and closes) the dialog from INSIDE that loop, plus a
single-shot safety timer so a wedged dialog fails loudly instead of hanging
the suite.

Run:  .venv-qt/bin/python tests/test_qtgui.py   -> ALL PASS, exit 0
      python3 tests/test_qtgui.py               -> SKIP, exit 0
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


# socket.socket must stay a CLASS (ssl.py subclasses it at import), so block
# construction instead of replacing the name with a function.
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
    from PySide6.QtCore import Qt, QRect, QTimer
    from PySide6.QtGui import QShortcut
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (QApplication, QDialog, QGroupBox, QLabel,
                                   QPushButton, QTableWidget)
except ImportError:
    # run_all.py needs "OK"/"ALL PASS" in the output to count exit 0 as a pass.
    print("SKIP  tests/test_qtgui.py — PySide6 is not installed in this "
          "interpreter; Qt GUI tests not run. OK (clean skip, exit 0)")
    sys.exit(0)

# ---- repo pieces ----------------------------------------------------------
from gui import model
from gui.app import (DIR_CHOICES, POLL_MS, SIGNAL_COLUMNS, SIGNAL_HEADINGS,
                     SORT_CHOICES, TICK_MS)
from proto import snapshot as snap
from proto.scorer import Scorecard, Veto

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


# --------------------------------------------------------------------------
# shared fixtures
# --------------------------------------------------------------------------

def mkcard(coin, score=50.0, direction="LONG", venue="BYBIT", vetoes=(),
           min_notional=0.01, notes=(), lean=0.5, earlyness=0.5, price=1.0,
           change_24h_pct=0.0, quote_vol_24h=1e6, funding_rate=0.0001,
           oi_change_pct=None, spread_pct=0.1):
    """A fake scorecard — no network, no scanner, plain data."""
    sc = Scorecard(coin=coin, venue=venue, score=score, direction=direction,
                   lean=lean, earlyness=earlyness, price=price,
                   change_24h_pct=change_24h_pct, quote_vol_24h=quote_vol_24h,
                   funding_rate=funding_rate, oi_change_pct=oi_change_pct,
                   spread_pct=spread_pct, min_notional=min_notional,
                   notes=list(notes))
    sc.vetoes = [Veto(v, "test reason") for v in vetoes]
    sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    return sc


COIN_COL = SIGNAL_COLUMNS.index("coin")


def make_window(db=None):
    """Build a RadarWindow offscreen and retire both worker lanes."""
    tmp = None
    if db is None:
        tmp = tempfile.mkdtemp(prefix="alt-radar-qt-")
        TMPDIRS.append(tmp)
        db = os.path.join(tmp, "qt.db")
    win = RadarWindow(auto_scan=False, db=db)
    win._test_tmp = tmp
    WINDOWS.append(win)             # registered first: cleanup can always reach it
    # Recorded before the tick timer is stopped (see below).
    win._test_timers = (win._poll_timer.isActive(), win._tick_timer.isActive(),
                        win._poll_timer.interval(), win._tick_timer.interval())
    win.show()
    # Retire the lanes exactly like tests/test_gui.py: the ctor's offline
    # "stats" job sits in front of the sentinel, so queue -> poll -> render
    # still runs for real, but no scan/plan/collect/resolve/lookup job can
    # ever execute (=> no network beyond the tripwire).
    win._jobs.put(None)
    win._worker.join(timeout=10)
    check("worker lane retired", not win._worker.is_alive(),
          "worker thread did not retire")
    win._plan_jobs.put(None)
    win._plan_worker.join(timeout=10)
    check("plan lane retired", not win._plan_worker.is_alive(),
          "plan worker thread did not retire")
    # The tick timer would queue a spurious "stats" job on its first fire
    # (_last_stats_ts starts at 0) that the retired worker can never finish
    # -> a permanent "refreshing stats". Determinism first; the timer wiring
    # itself is asserted in the build test via win._test_timers.
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


def row_cells(table, r):
    return [table.item(r, c).text() for c in range(table.columnCount())]


def bg_name(table, r, c):
    return table.item(r, c).background().color().name()


def fg_name(table, r, c):
    return table.item(r, c).foreground().color().name()


def _visible_dialog():
    for w in APP.topLevelWidgets():
        if isinstance(w, QDialog) and w.isVisible():
            return w
    return None


def drive_modal(opener, inspector, timeout_ms=5000):
    """Run a modal dialog: inspect it from inside its own event loop.

    Returns the dialog after exec() returns. inspector(dlg) runs while the
    dialog is modal and is responsible for closing it (or leaving that to
    the finally-hook). A safety timer rejects the dialog after timeout_ms
    so a wedged modal fails the test instead of hanging the suite.
    """
    seen = {"dlg": None, "err": None, "timeout": False}

    def _safety():
        dlg = _visible_dialog()
        if dlg is not None:
            seen["timeout"] = True
            dlg.reject()

    def _hook():
        try:
            dlg = _visible_dialog()
            seen["dlg"] = dlg
            if dlg is None:
                seen["err"] = AssertionError("opener() opened no dialog")
                return
            inspector(dlg)
        except Exception as e:            # never raise inside Qt's dispatch
            seen["err"] = e
        finally:
            dlg = seen["dlg"]
            if dlg is not None and dlg.isVisible():
                dlg.reject()

    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(_safety)
    safety.start(timeout_ms)
    QTimer.singleShot(0, _hook)
    try:
        opener()                          # blocks in QDialog.exec()
    finally:
        safety.stop()
    if seen["timeout"]:
        raise AssertionError("modal dialog never became inspectable")
    if seen["err"] is not None:
        raise seen["err"]
    if seen["dlg"] is None:
        raise AssertionError("opener() opened no dialog")
    check("modal dialog closed itself", not seen["dlg"].isVisible())
    return seen["dlg"]


# ==========================================================================
# 1. BUILD
# ==========================================================================

def t_build():
    win = make_window()
    check("window title", "READ-ONLY" in win.windowTitle(), win.windowTitle())
    check("timers wired at build (poll+tick, POLL_MS/TICK_MS)",
          win._test_timers == (True, True, POLL_MS, TICK_MS),
          str(win._test_timers))
    check("temp db used",
          win._test_tmp is not None and win.db.startswith(win._test_tmp),
          win.db)
    check("auto_scan off", win.auto_scan is False)
    jobs = drain(win._jobs)
    check("no job left on the worker queue by the ctor",
          jobs == [], str(jobs))

    labels = [l.text() for l in win.findChildren(QLabel)]
    check("title label", "ALT RADAR · MEXC perp scanner" in labels, str(labels[:4]))
    check("READ-ONLY badge", " READ-ONLY " in labels)
    check("TIER-2 badge", "TIER-2 UNVALIDATED" in labels)
    for text in ("$", "Coins", "Interval s", "Dir", "Sort"):
        check(f"label {text!r}", text in labels)

    groups = [g.title() for g in win.findChildren(QGroupBox)]
    for text in ("Scan", "Budget", "Stake", "Find", "View",
                 "Flag threshold", "Lists", "Data", "Signals", "Outcomes"):
        check(f"group {text!r}", text in groups, str(groups))
    check("vetoed group", any(t.startswith("VETOED — excluded from ranking")
                              for t in groups), str(groups))
    check("detail group", any(t.startswith("Detail — selected coin") for t in groups))

    btns = [b.text() for b in win.findChildren(QPushButton)]
    for text in ("Scan now", "Resume auto-scan", "Collect bars",
                 "Resolve outcomes", "Refresh stats", "Resolve now",
                 "★ Top 10", "👁 Watch", "+ New"):
        check(f"button {text!r}", text in btns, str(btns))
    check("two Apply buttons (stake + threshold)", btns.count("Apply") == 2,
          str(btns))

    dir_items = tuple(win.cmb_dir.itemText(i) for i in range(win.cmb_dir.count()))
    sort_items = tuple(win.cmb_sort.itemText(i) for i in range(win.cmb_sort.count()))
    check("Dir combo choices", dir_items == DIR_CHOICES, str(dir_items))
    check("Sort combo choices", sort_items == SORT_CHOICES, str(sort_items))
    check("coins spinbox range",
          (win.sp_coins.minimum(), win.sp_coins.maximum())
          == (model.MIN_COINS, model.MAX_COINS))
    check("interval spinbox floor",
          win.sp_interval.minimum() == model.MIN_INTERVAL)

    check("initial stake", win.ed_stake.text() == "0.1", win.ed_stake.text())
    check("initial threshold", win.ed_threshold.text() == "24",
          win.ed_threshold.text())
    check("initial coins", win.sp_coins.value() == 150)
    check("initial interval", win.sp_interval.value() == 60)
    check("initial dir", win.cmb_dir.currentText() == "Both")
    check("initial sort", win.cmb_sort.currentText() == "score")
    check("find placeholder", "venue-wide" in win.ed_search.placeholderText(),
          win.ed_search.placeholderText())
    check("Ctrl+Q shortcut present", len(win.findChildren(QShortcut)) >= 1)

    for i, col in enumerate(SIGNAL_COLUMNS):
        got = win.tree.horizontalHeaderItem(i).text()
        check(f"heading {col}", got == SIGNAL_HEADINGS[col], got)
    check("signals table starts empty", win.tree.rowCount() == 0)
    check("vetoed table starts empty", win.tree_vetoed.rowCount() == 0)
    check("detail placeholder",
          "select a row for the full breakdown" in win.detail.toPlainText())
    check("detail pane is read-only", win.detail.isReadOnly())
    check("outcomes placeholder",
          win.lbl_outcomes.text() == "no outcomes resolved yet",
          win.lbl_outcomes.text())
    check("status shows the ctor's stats activity",
          win.lbl_activity.text() == "activity: refreshing stats",
          win.lbl_activity.text())
    check("failed counter", win.lbl_failed.text() == "failed 0/0 of last scan")
    check("status starts at 'starting'",
          win.lbl_statusline.text() == "starting…", win.lbl_statusline.text())

    # -- worker -> queue -> poll timer -> render round trip ------------------
    ok = pump(lambda: "logs 0 rows" in win.lbl_header.text())
    check("ctor stats job round-trips to the header", ok, win.lbl_header.text())
    header = win.lbl_header.text()
    for frag in ("universe 0", "shown 0", "vetoed 0", "stake $0.10",
                 "MEXC", "Bybit", "last scan never",
                 "logs 0 rows / 0 coins", "flagged 0", "outcomes 0"):
        check(f"header {frag!r}", frag in header, header)
    check("status becomes ready", win.scan_status == "ready", win.scan_status)
    check("statusline renders ready",
          win.lbl_statusline.text() == "ready", win.lbl_statusline.text())
    check("activity idle after drain",
          win.lbl_activity.text() == "activity: idle", win.lbl_activity.text())
    outcomes = win.lbl_outcomes.text()
    check("zero counts line",
          "1h 0 · 4h 0 · 24h 0 · 7d 0   (total 0 resolved)" in outcomes,
          outcomes)
    check("zero 24h line", "24h flagged: 0 signals" in outcomes, outcomes)
    check("no plans line", "plans: none resolved yet" in outcomes, outcomes)


# ==========================================================================
# 2. TABLE
# ==========================================================================

def t_table():
    win = make_window()
    cards = [
        mkcard("AAA", score=70, direction="LONG", venue="BYBIT",
               min_notional=50.0, price=1234.5, change_24h_pct=3.2,
               quote_vol_24h=4_300_000, funding_rate=0.0001,
               oi_change_pct=12.5, lean=0.5, earlyness=0.8),
        mkcard("BBB", score=40, direction="SHORT", venue="MEXC",
               min_notional=None, price=0.000012, change_24h_pct=-1.5,
               quote_vol_24h=600_000, funding_rate=-0.0002,
               oi_change_pct=None, lean=-0.3, earlyness=0.2),
    ]
    win.set_cards(cards)
    check("set_cards status", win.scan_status == "cards loaded (no scan)",
          win.scan_status)
    check("row count", win.tree.rowCount() == 2, str(win.tree.rowCount()))
    check("order = score desc",
          [win.tree.item(r, COIN_COL).text() for r in range(2)]
          == ["AAA", "BBB"])
    row0 = row_cells(win.tree, 0)
    row1 = row_cells(win.tree, 1)
    check("AAA row cells (tkinter parity)",
          row0 == ["1", "▲", "AAA", "1234.5", "+3.2%", "$4.3M", "+0.0100%",
                   "+12.5%", "+0.50", "0.80", "70.0", "WATCH"], str(row0))
    check("BBB row cells (tkinter parity)",
          row1 == ["2", "▼", "BBB", "0.000012", "-1.5%", "$600K", "-0.0200%",
                   "n/a", "-0.30", "0.20", "40.0", "UNVALIDATED"], str(row1))
    check("rank cell carries the row's coin",
          win.tree.item(0, 0).data(Qt.ItemDataRole.UserRole) == "AAA")
    check("LONG row tint", bg_name(win.tree, 0, 0) == qtheme.LONG_BG,
          bg_name(win.tree, 0, 0))
    check("SHORT row band tint",
          bg_name(win.tree, 1, 0) == qtheme.SHORT_BG_ALT,
          bg_name(win.tree, 1, 0))
    check("tints differ", bg_name(win.tree, 0, 0) != bg_name(win.tree, 1, 0))
    check("WATCH text where due", row0[-1] == "WATCH", row0[-1])
    check("WATCH foreground on the row",
          fg_name(win.tree, 0, 11) == qtheme.WATCH_FG, fg_name(win.tree, 0, 11))
    check("header counts", "shown 2 · vetoed 0" in win.lbl_header.text(),
          win.lbl_header.text())

    # -- vetoed split --------------------------------------------------------
    win.set_cards([mkcard("AAA", score=70),
                   mkcard("CCC", score=20, vetoes=("late_move", "thin_book")),
                   mkcard("DDD", score=30, vetoes=("stale",))])
    check("ranked table holds only clean rows",
          [win.tree.item(r, COIN_COL).text() for r in range(win.tree.rowCount())]
          == ["AAA"])
    check("vetoed row count", win.tree_vetoed.rowCount() == 2,
          str(win.tree_vetoed.rowCount()))
    check("vetoed order (score desc)",
          [win.tree_vetoed.item(r, 1).text() for r in range(2)]
          == ["DDD", "CCC"])
    v0 = row_cells(win.tree_vetoed, 0)
    v1 = row_cells(win.tree_vetoed, 1)
    check("DDD veto cells", v0 == ["▲", "DDD", "30.0", "stale"], str(v0))
    check("CCC veto cells",
          v1 == ["▲", "CCC", "20.0", "late_move,thin_book"], str(v1))
    check("vetoed band tint (row 0)",
          bg_name(win.tree_vetoed, 0, 0) == qtheme.VETO_BG_ALT,
          bg_name(win.tree_vetoed, 0, 0))
    check("vetoed band tint (row 1)",
          bg_name(win.tree_vetoed, 1, 0) == qtheme.VETO_BG,
          bg_name(win.tree_vetoed, 1, 0))
    check("vetoed foreground",
          fg_name(win.tree_vetoed, 0, 3) == qtheme.VETO_FG,
          fg_name(win.tree_vetoed, 0, 3))
    check("header counts after split",
          "shown 1 · vetoed 2" in win.lbl_header.text(),
          win.lbl_header.text())

    # -- empty clears --------------------------------------------------------
    win.set_cards([])
    check("empty clears the signals table", win.tree.rowCount() == 0)
    check("empty clears the vetoed table", win.tree_vetoed.rowCount() == 0)
    check("header resets", "shown 0 · vetoed 0" in win.lbl_header.text(),
          win.lbl_header.text())


# ==========================================================================
# 3. SEARCH
# ==========================================================================

def t_search():
    win = make_window()
    win.set_cards([mkcard("QNT", score=70), mkcard("BTC", score=60)])
    check("two rows before filtering", win.tree.rowCount() == 2)

    win.ed_search.setText("btc")                 # textChanged -> re-render
    check("Find narrows to one row", win.tree.rowCount() == 1,
          str(win.tree.rowCount()))
    check("Find keeps the matching coin",
          win.tree.item(0, COIN_COL).text() == "BTC")
    check("Find narrows the header count",
          "shown 1 · vetoed 0" in win.lbl_header.text(),
          win.lbl_header.text())
    win.ed_search.setText("")
    check("clearing Find restores both rows", win.tree.rowCount() == 2)

    # -- Enter WITH a table match -> select it, never a lookup ---------------
    check("no jobs before Enter", drain(win._jobs) == [])
    win.ed_search.setText("qnt")
    win.ed_search.returnPressed.emit()
    check("Enter selects the match", win.selected_coin == "QNT",
          str(win.selected_coin))
    jobs = drain(win._jobs)
    check("Enter queues no lookup job", jobs == [], str(jobs))
    check("selected row is the match",
          win.tree.item(0, 0).data(Qt.ItemDataRole.UserRole) == "QNT"
          and len(win.tree.selectionModel().selectedRows()) == 1)
    check("detail renders the selection",
          "QNT" in win.detail.toPlainText(), win.detail.toPlainText()[:80])
    check("plan fetch queued on the plan lane", "QNT" in win._plans_pending)
    plan_jobs = drain(win._plan_jobs)
    check("plan job shape",
          plan_jobs == [{"coin": "QNT", "stake": win.stake}], str(plan_jobs))

    # -- Enter WITHOUT a match -> one lookup job on the (retired) queue ------
    win.ed_search.setText("zzzzz")
    win.ed_search.returnPressed.emit()
    jobs = drain(win._jobs)
    check("Enter without match queues exactly one lookup job",
          jobs == [{"cmd": "lookup", "coin": "ZZZZZ"}], str(jobs))
    check("lookup marks the lane busy", "lookup" in win._busy,
          str(win._busy))
    check("lookup status line",
          "looking up ZZZZZ" in win.lbl_statusline.text(),
          win.lbl_statusline.text())
    check("lookup job never executed (lane still retired)",
          not win._worker.is_alive())
    check("no second lookup queued while busy",
          not drain(win._jobs) and win._submit("lookup", coin="QQQ") is False,
          str(win._jobs.qsize()))


# ==========================================================================
# 4. SORT / DIR CONTROLS
# ==========================================================================

def t_sortdir():
    win = make_window()
    win.set_cards([
        mkcard("L1", score=30, direction="LONG", quote_vol_24h=100,
               change_24h_pct=1.0),
        mkcard("S1", score=70, direction="SHORT", quote_vol_24h=1000,
               change_24h_pct=-12.0),
        mkcard("L2", score=50, direction="LONG", quote_vol_24h=5000,
               change_24h_pct=5.0),
    ])

    def coins():
        return [win.tree.item(r, COIN_COL).text()
                for r in range(win.tree.rowCount())]

    check("default order = score desc", coins() == ["S1", "L2", "L1"],
          str(coins()))

    win.cmb_dir.setCurrentText("Long")            # signal -> _on_dir_change
    check("Dir=Long state", win.dir_filter == "long", win.dir_filter)
    check("Dir=Long rows", coins() == ["L2", "L1"], str(coins()))
    check("Dir=Long header", "shown 2" in win.lbl_header.text(),
          win.lbl_header.text())
    win.cmb_dir.setCurrentText("Short")
    check("Dir=Short rows", coins() == ["S1"], str(coins()))
    win.cmb_dir.setCurrentText("Both")
    check("Dir=Both rows", coins() == ["S1", "L2", "L1"], str(coins()))

    win.cmb_sort.setCurrentText("vol")            # signal -> _on_sort_change
    check("Sort=vol state", win.sort_key == "vol", win.sort_key)
    check("Sort=vol order (vol desc)", coins() == ["L2", "S1", "L1"],
          str(coins()))
    check("rank column renumbers with the order",
          [win.tree.item(r, 0).text() for r in range(3)] == ["1", "2", "3"])
    win.cmb_sort.setCurrentText("score")
    check("Sort=score restores score order", coins() == ["S1", "L2", "L1"],
          str(coins()))

    # -- real header click (Qt-only path): fresh click on a measure column
    #    opens best-first, i.e. 24h% descending.
    col = SIGNAL_COLUMNS.index("ch24")
    hdr = win.tree.horizontalHeader()
    rect = QRect(hdr.sectionViewportPosition(col), 0,
                 hdr.sectionSize(col), hdr.height())
    QTest.mouseClick(hdr.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, rect.center())
    APP.processEvents()
    check("fresh header click on 24h% sorts descending",
          coins() == ["L2", "L1", "S1"], str(coins()))

    # -- _CellItem comparison guards (numeric never loses to text) -----------
    a = qtapp._CellItem("400.0", 400.0)
    b = qtapp._CellItem("90.0", 90.0)
    check("numeric cells compare as numbers (text would say 400.0 < 90.0)",
          (b < a) and not (a < b), "400.0 vs 90.0 not ordered numerically")
    na = qtapp._CellItem("n/a", None)
    check("untrusted text sorts after numbers",
          (a < na) and not (na < a))
    check("text cells casefold",
          qtapp._CellItem("aaa") < qtapp._CellItem("BBB"))


# ==========================================================================
# 5. SEGMENT DIALOGS
# ==========================================================================

def t_segments():
    win = make_window()
    win.set_cards([mkcard("AAA", score=70),
                   mkcard("BBB", score=40, min_notional=5.0),
                   mkcard("CCC", score=60, direction="SHORT")])

    # -- empty digest: guidance, no modal ------------------------------------
    win._open_picks()
    check("empty picks guidance", "no picks yet" in win.error_text,
          win.error_text)
    win._open_watch()
    check("empty watch guidance", "watch list empty" in win.error_text,
          win.error_text)
    win._open_new()
    check("empty new guidance", "no new listings" in win.error_text,
          win.error_text)
    check("no dialog opened for empty lists", _visible_dialog() is None)

    # -- seeded digest -------------------------------------------------------
    win.digest = {
        "picks": [mkcard("AAA", score=70, lean=0.5, price=1.0)],
        "watch": [mkcard("BBB", score=40, min_notional=5.0)],
        "new": [{"coin": "CCC", "first_seen": 1_700_000_000,
                 "score": 60.0, "direction": "SHORT", "ts": 1_700_000_000}],
    }

    def inspect_picks(dlg):
        check("Top 10 dialog title", "Top 10" in dlg.windowTitle(),
              dlg.windowTitle())
        tbl = dlg.findChild(QTableWidget)
        check("Top 10 has a table", tbl is not None)
        check("Top 10 row count", tbl.rowCount() == 1, str(tbl.rowCount()))
        check("Top 10 heading",
              tbl.horizontalHeaderItem(0).text() == "COIN")
        cells = row_cells(tbl, 0)
        check("Top 10 row cells",
              cells == ["AAA", "▲", "70.0", model.format_price(1.0), "+0.50"],
              str(cells))
        tbl.selectRow(0)
        btn = dlg.findChild(QPushButton)
        check("dialog pick button", btn is not None)
        btn.click()                              # pick() -> dlg.accept()

    drive_modal(win._open_picks, inspect_picks)
    check("pick jumps the main selection", win.selected_coin == "AAA",
          str(win.selected_coin))
    sel = win.tree.selectionModel().selectedRows()
    check("main table row follows the pick",
          len(sel) == 1
          and win.tree.item(sel[0].row(), 0).data(Qt.ItemDataRole.UserRole)
          == "AAA", str([i.row() for i in sel]))
    check("detail shows the picked coin",
          "AAA" in win.detail.toPlainText(), win.detail.toPlainText()[:80])
    plan_jobs = [j for j in drain(win._plan_jobs) if j.get("coin") == "AAA"]
    check("pick queues the plan fetch",
          plan_jobs == [{"coin": "AAA", "stake": win.stake}], str(plan_jobs))

    def inspect_watch(dlg):
        check("Watch dialog title", "Watch" in dlg.windowTitle(),
              dlg.windowTitle())
        tbl = dlg.findChild(QTableWidget)
        check("Watch row count", tbl.rowCount() == 1, str(tbl.rowCount()))
        cells = row_cells(tbl, 0)
        check("Watch row cells",
              cells == ["BBB", "▲", "40.0", "$5.00"], str(cells))

    drive_modal(win._open_watch, inspect_watch)

    def inspect_new(dlg):
        check("New dialog title", "New listings" in dlg.windowTitle(),
              dlg.windowTitle())
        tbl = dlg.findChild(QTableWidget)
        check("New row count", tbl.rowCount() == 1, str(tbl.rowCount()))
        seen = time.strftime("%m-%d %H:%M", time.localtime(1_700_000_000))
        cells = row_cells(tbl, 0)
        check("New row cells",
              cells == ["CCC", seen, "60.0", "▼"], str(cells))

    drive_modal(win._open_new, inspect_new)


# ==========================================================================
# 6. OUTCOMES PANEL
# ==========================================================================

def t_outcomes():
    win = make_window()
    stats = {"rows": 10, "flagged": 2, "coins": 5, "longs": 4,
             "shorts": 3, "outcomes": 3}
    outcome = {
        "counts": {"1h": 1, "4h": 2, "24h": 0, "7d": 0}, "total": 3,
        "direction": {"LONG": {"count": 2, "avg_return": 5.5, "hit_rate": 0.5},
                      "SHORT": {"count": 1, "avg_return": -2.0,
                                "hit_rate": 0.0}},
    }
    hit24 = {"window_hours": 24, "signals": 7,
             "by_horizon": {},
             "overall": {"resolved": 4, "wins": 3, "losses": 1,
                         "pct_won": 75.0, "avg_return": 1.25}}
    plan = {"planned": 10, "stop_hit": 3, "tp1_hit": 5, "tp2_hit": 1,
            "stop_pct": 30.0, "tp1_pct": 50.0, "tp2_pct": 10.0,
            "terminal": 2}
    win._handle_msg({"kind": "stats", "ok": True, "stats": stats,
                     "outcome": outcome, "hit24": hit24, "plan": plan})

    panel = win.lbl_outcomes.text()
    check("per-horizon counts line",
          "1h 1 · 4h 2 · 24h 0 · 7d 0   (total 3 resolved)" in panel, panel)
    check("LONG hit-rate line",
          "LONG: n=2 · avg signed return +5.50% · hit rate 50%" in panel, panel)
    check("SHORT hit-rate line",
          "SHORT: n=1 · avg signed return -2.00% · hit rate 0%" in panel, panel)
    check("24h flagged line",
          "24h flagged: 7 signals · 4 resolved · won 3 / lost 1 · 75% won · "
          "avg +1.25%" in panel, panel)
    check("plans-live line",
          "plans live: 10 watched · stop 3 (30%) · TP1 5 (50%) · TP2 1 (10%) · "
          "terminal 2" in panel, panel)
    header = win.lbl_header.text()
    for frag in ("logs 10 rows / 5 coins", "flagged 2", "outcomes 3"):
        check(f"header {frag!r}", frag in header, header)

    # -- zero shape: no outcomes yet, no plans resolved ----------------------
    zero_hit = {"window_hours": 24, "signals": 0, "by_horizon": {},
                "overall": {"resolved": 0, "wins": 0, "losses": 0,
                            "pct_won": 0.0, "avg_return": 0.0}}
    win._handle_msg({"kind": "stats", "ok": True,
                     "stats": {"rows": 0, "flagged": 0, "coins": 0,
                               "longs": 0, "shorts": 0, "outcomes": 0},
                     "outcome": model.outcome_summary(None),
                     "hit24": zero_hit, "plan": {"planned": 0}})
    panel = win.lbl_outcomes.text()
    check("zero counts line",
          "1h 0 · 4h 0 · 24h 0 · 7d 0   (total 0 resolved)" in panel, panel)
    check("zero LONG line", "LONG: no resolved outcomes yet" in panel, panel)
    check("zero SHORT line", "SHORT: no resolved outcomes yet" in panel, panel)
    check("zero 24h line", "24h flagged: 0 signals" in panel, panel)
    check("no-plans line", "plans: none resolved yet" in panel, panel)

    # -- failure surfaces in the status bar, never crashes -------------------
    win._handle_msg({"kind": "stats", "ok": False, "error": "db locked"})
    check("stats failure surfaces", win.error_text == "stats failed: db locked",
          win.error_text)
    check("statusline carries the error",
          win.lbl_statusline.text() == win.error_text,
          win.lbl_statusline.text())
    check("error styling applied",
          qtheme.ERROR_FG in win.lbl_statusline.styleSheet(),
          win.lbl_statusline.styleSheet())


# ==========================================================================
# 7. SNAPSHOT: close writes .last_entries, relaunch repopulates
# ==========================================================================

def t_snapshot():
    import pickle

    win = make_window()
    win.set_cards([mkcard("AAA", score=70), mkcard("BBB", score=40)])
    win._plan_cache["AAA"] = {"plan": None, "err": "offline test"}
    db = win.db
    win._on_close()

    check("close marks the window closing", win._closing)
    path = snap.path_for(db)
    check(".last_entries written", os.path.exists(path), path)
    check("poll timer stopped on close", not win._poll_timer.isActive())
    check("tick timer stopped on close", not win._tick_timer.isActive())
    with open(path, "rb") as f:
        payload = pickle.load(f)
    check("snapshot version", payload.get("version") == snap.VERSION,
          str(payload.get("version")))
    check("snapshot cards",
          [c.coin for c in payload.get("cards", [])] == ["AAA", "BBB"],
          str([c.coin for c in payload.get("cards", [])]))
    check("snapshot plans carried", "AAA" in (payload.get("plans") or {}))
    check("snapshot meta = stake + threshold",
          payload.get("meta") == {"stake": 0.1, "threshold": 24.0},
          str(payload.get("meta")))
    cards, plans, saved_at = snap.load(db)
    check("snapshot loads back through proto/snapshot",
          [c.coin for c in cards] == ["AAA", "BBB"] and saved_at,
          str(type(cards)))

    # -- relaunch: exact screen back, instantly, before any event loop -------
    win2 = make_window(db=db)
    check("relaunch table populated",
          [win2.tree.item(r, COIN_COL).text()
           for r in range(win2.tree.rowCount())] == ["AAA", "BBB"])
    check("relaunch status says saved",
          "showing saved" in win2.scan_status
          and "live scan running" in win2.scan_status, win2.scan_status)
    check("relaunch header counts",
          "shown 2 · vetoed 0" in win2.lbl_header.text(),
          win2.lbl_header.text())
    # ... and the first live result still replaces it (Q1 decision 6).
    ok = pump(lambda: win2.scan_status == "ready")
    check("live result replaces the saved status", ok, win2.scan_status)

    # -- plain close() (closeEvent path) writes the snapshot too -------------
    win3 = make_window(db=db)
    win3.set_cards([mkcard("CCC", score=60)])
    win3.close()
    check("closeEvent marks the window closing", win3._closing)
    cards, plans, saved_at = snap.load(db)
    check("closeEvent path rewrites .last_entries",
          [c.coin for c in cards] == ["CCC"], str([c.coin for c in cards]))


# ==========================================================================
# 8. NO-NETWORK TRIPWIRE
# ==========================================================================

def t_theme():
    print("== 9 theme: light readable palette, distinct hues, real contrast")

    def lum(hexstr):
        hexstr = hexstr.lstrip("#")
        r, g, b = (int(hexstr[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

        def lin(c):
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

    def contrast(a, b):
        la, lb = lum(a), lum(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    check("surface is light (readable base)", lum(qtheme.SURFACE) > 0.7,
          qtheme.SURFACE)
    check("body text is ink (dark on light)", lum(qtheme.TEXT) < 0.08,
          qtheme.TEXT)
    check("body contrast ≥ 7:1",
          contrast(qtheme.TEXT, qtheme.SURFACE) >= 7.0,
          f"{contrast(qtheme.TEXT, qtheme.SURFACE):.1f}:1")
    check("muted text contrast ≥ 4.5:1",
          contrast(qtheme.TEXT_MUTED, qtheme.SURFACE) >= 4.5,
          f"{contrast(qtheme.TEXT_MUTED, qtheme.SURFACE):.1f}:1")
    check("selected white-on-blue contrast ≥ 4.5:1",
          contrast(qtheme.SELECT_FG, qtheme.SELECT_BG) >= 4.5,
          f"{contrast(qtheme.SELECT_FG, qtheme.SELECT_BG):.1f}:1")
    hues = {qtheme.LONG_BG, qtheme.SHORT_BG, qtheme.VETO_BG,
            qtheme.SURFACE, qtheme.WATCH_FG, qtheme.ACCENT}
    check("direction/status hues all distinct", len(hues) == 6, str(hues))
    check("watch amber readable on surface",
          contrast(qtheme.WATCH_FG, qtheme.SURFACE) >= 4.5,
          f"{contrast(qtheme.WATCH_FG, qtheme.SURFACE):.1f}:1")
    check("error red readable on surface",
          contrast(qtheme.ERROR_FG, qtheme.SURFACE) >= 4.5,
          f"{contrast(qtheme.ERROR_FG, qtheme.SURFACE):.1f}:1")


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
    # the explicit opt-out marker this suite deliberately never sets
    check("no live-test marker in this file",
          ("LIVE" + "_TEST") not in open(__file__).read())


# ==========================================================================
# runner
# ==========================================================================

if __name__ == "__main__":
    tests = [
        ("1 build: offscreen window, temp db, retired lanes, stats round-trip",
         t_build),
        ("2 table: fake scorecards, order, tints, WATCH, vetoed split",
         t_table),
        ("3 search: narrow / Enter selects / Enter queues a lookup job",
         t_search),
        ("4 sort+dir: combos filter and reorder, header click, numeric cells",
         t_sortdir),
        ("5 segments: Top 10 / Watch / New modal dialogs", t_segments),
        ("6 outcomes: counts + 24h + plans-live from a stats message",
         t_outcomes),
        ("7 snapshot: close writes .last_entries, relaunch repopulates",
         t_snapshot),
        ("8 tripwire: sockets blocked, no live tests", t_tripwire),
        ("9 theme: light readable palette, distinct hues, real contrast",
         t_theme),
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
