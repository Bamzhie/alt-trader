"""Functional tests for the PySide6 / Qt-Widgets desktop GUI (tasks Q2 + Q4).

Offscreen by construction: ``QT_QPA_PLATFORM=offscreen`` is set at the very
top, BEFORE PySide6 is imported, so every window here builds display-free.

Interpreter guard: the system python3 has no PySide6. The file then prints
a loud SKIP line and exits 0 — ``tests/run_all.py`` globs ``test_*.py``
(minus test_app.py) and counts exit 0 as a pass, so this skip guard is what
keeps that suite green under the system interpreter.

Coverage (mirrors tests/test_gui.py's intent, Qt shapes, plus the Q4
mockup-recreation additions):
  1 build        window constructs offscreen with auto_scan=False + temp db,
                 both worker lanes retired; top bar (badges, scan UTC,
                 countdown, venue pill, gear), rail, toolbar groups (no
                 Budget group), settings page fields, initial values; the
                 ctor's offline "stats" job round-trips queue -> poll
  2 table        fake proto.scorer Scorecards: row count/order, exact cells
                 (tkinter parity), direction tint + green ▲/red ▼, WATCH
                 text and foreground, UNVALIDATED amber pill on FLAGS,
                 vetoed split with humanized REASON + comma-joined codes
  3 search       Find text narrows rows; Enter with a match selects it (no
                 lookup job); Enter without a match queues a lookup job —
                 asserted on the queue, the retired worker never runs it
  4 sort/dir     Dir combo filters, Sort combo reorders, a real header click
                 re-sorts (Qt-only path), _CellItem numeric comparison
  5 segments     Watchlist / Top 10 / New are in-page tabs with live rows;
                 selecting a current-scan coin updates the detail pane.
  6 outcomes     Daily and cumulative cohorts, direction/horizon rows, and
                 explicit plan denominators, including the zero shape
  7 snapshot     close writes .last_entries (proto/snapshot), relaunch
                 repopulates instantly; the plain close() path writes too
  8 tripwire     socket.socket / create_connection / getaddrinfo /
                 gethostbyname all raise — and ALLOW_NETWORK stays False:
                 there are NO live tests in this file
  9 theme        dark navy palette: dark surfaces, bright ink, real contrast
 10 rail/pages   rail buttons + gear switch the QStackedWidget; watchlist,
                 logs (log-file tail) and settings pages are functional
 11 detail       rebuilt detail pane: header, subtitle, tiles, key-value
                 rows with REAL values and honest pending states; NO
                 confidence metric, NO 24h high/low anywhere
 12 veto reason  gui.model.veto_reason is pure + humanizes the REASON column
 13 leverage cap dropdown writes the planner cap end-to-end: cache cleared,
                 plan jobs carry the cap, stale-cap results refused
 14 data tools   Export writes the visible table to CSV (temp file); Copy
                 composes the shared-model detail text (clipboard assert
                 skipped offscreen — handler + non-empty text asserted)
 15 events       in-app event ring buffer: colored dots, max 50, fed by
                 scan/resolve/collect completions + failures

Network policy: ZERO network. The tripwire is armed before PySide6 is even
imported; cards are fake Scorecard objects; stores are temp-dir SQLite
files; both worker lanes are retired (None sentinel + join) right after
each window's construction — the ctor's offline "stats" job sits in front
of the sentinel, so the real queue -> poll -> render path still runs, but
no scan/plan/collect/resolve/lookup job can ever execute.

Run:  .venv-qt/bin/python tests/test_qtgui.py   -> ALL PASS, exit 0
      python3 tests/test_qtgui.py               -> SKIP, exit 0
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
    from PySide6.QtCore import Qt, QRect
    from PySide6.QtGui import QShortcut
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (QApplication, QGroupBox, QLabel,
                                   QListWidget, QPushButton, QTableWidget)
except ImportError:
    # run_all.py needs "OK"/"ALL PASS" in the output to count exit 0 as a pass.
    print("SKIP  tests/test_qtgui.py — PySide6 is not installed in this "
          "interpreter; Qt GUI tests not run. OK (clean skip, exit 0)")
    sys.exit(0)

# ---- repo pieces ----------------------------------------------------------
from gui import model
from gui.app import (DIR_CHOICES, POLL_MS, SIGNAL_COLUMNS, SIGNAL_HEADINGS,
                     SORT_CHOICES, TICK_MS)
from proto import planner as pl
from proto import snapshot as snap
from proto.planner import MAX_LEVERAGE
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
DIR_COL = SIGNAL_COLUMNS.index("dir")
FLAGS_COL = SIGNAL_COLUMNS.index("flags")


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


def kv_table_text(table):
    return "\n".join(f"{table.item(r, 0).text()}: "
                     f"{table.item(r, 1).text()}"
                     for r in range(table.rowCount()))


def bg_name(table, r, c):
    return table.item(r, c).background().color().name()


def fg_name(table, r, c):
    return table.item(r, c).foreground().color().name()


def kv_map(win):
    """The detail pane's SIGNAL DETAILS table as a {key: value} dict."""
    return {win.det_kv.item(r, 0).text(): win.det_kv.item(r, 1).text()
            for r in range(win.det_kv.rowCount())}


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
    check("top-bar title", "ALT RADAR" in labels, str(labels[:6]))
    check("top-bar subtitle",
          "MEXC Perpetual Futures Scanner" in labels)
    check("READ-ONLY badge", " READ-ONLY " in labels)
    check("TIER-2 badge", "TIER-2 UNVALIDATED" in labels)
    for text in ("Stake $", "Dir", "Sort", "Flag threshold"):
        check(f"label {text!r}", text in labels)
    check("settings coins label", "Coins (universe + scan size)" in labels)
    check("settings interval label", "Interval s (auto-scan)" in labels)
    check("settings db label", "DB path" in labels)
    check("settings leverage label", "Leverage cap" in labels)

    groups = [g.title() for g in win.findChildren(QGroupBox)]
    for text in ("Scan", "Find", "View", "Signals", "Outcomes"):
        check(f"group {text!r}", text in groups, str(groups))
    check("no Budget group (mockup omission — coins/interval live in Settings)",
          "Budget" not in groups, str(groups))
    check("vetoed group", any(t.startswith("VETOED — excluded from ranking")
                              for t in groups), str(groups))
    check("detail group", any(t.startswith("Detail — selected coin") for t in groups))
    check("recent events moved out of scanner", "Recent events" not in groups,
          str(groups))
    check("event history remains on Logs page",
          "RECENT IN-APP EVENTS" in labels, str(labels))
    check("maintenance group", "Maintenance" in groups, str(groups))

    btns = [b.text() for b in win.findChildren(QPushButton)]
    for text in ("Scan now", "Resume auto-scan", "Collect bars",
                 "Resolve outcomes", "Refresh", "Resolve pending outcomes",
                 "Export signals",
                 "Copy selected detail", "Refresh stats"):
        check(f"button {text!r}", text in btns, str(btns))
    check("Scan now is the accent primary",
          win.btn_scan.objectName() == "btnPrimary")
    check("stake Apply button kept", win.btn_apply_stake is not None)
    check("threshold Apply button kept", win.btn_apply_threshold is not None)

    dir_items = tuple(win.cmb_dir.itemText(i) for i in range(win.cmb_dir.count()))
    sort_items = tuple(win.cmb_sort.itemText(i) for i in range(win.cmb_sort.count()))
    check("Dir combo choices", dir_items == DIR_CHOICES, str(dir_items))
    check("Sort combo choices", sort_items == SORT_CHOICES, str(sort_items))
    lev_items = tuple(win.cmb_lev.itemText(i) for i in range(win.cmb_lev.count()))
    check("leverage cap choices 10x/20x/50x",
          lev_items == ("10x", "20x", "50x"), str(lev_items))
    check("initial leverage cap", win.cmb_lev.currentText() == "50x"
          and win.leverage_cap == MAX_LEVERAGE)
    thr_presets = tuple(win.cmb_threshold.itemText(i)
                        for i in range(win.cmb_threshold.count()))
    check("threshold presets include the current 24",
          "24" in thr_presets and "40" in thr_presets, str(thr_presets))
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
    check("stake has a single Settings field",
          win.ed_stake.text() == "0.1")
    check("threshold has a single Settings field",
          win.ed_threshold.text() == "24")
    check("settings db path", win.ed_db.text() == win.db, win.ed_db.text())
    check("leverage has a single Settings control",
          win.cmb_lev.currentText() == "50x")
    check("find placeholder", "venue-wide" in win.ed_search.placeholderText(),
          win.ed_search.placeholderText())
    check("Ctrl+Q shortcut present", len(win.findChildren(QShortcut)) >= 1)

    # -- top-bar live widgets ----------------------------------------------
    check("countdown format (paused with auto-scan off)",
          win.lbl_countdown.text() == "Next scan: paused",
          win.lbl_countdown.text())
    win.auto_scan = True
    win.last_scan_ts = time.time()
    win._render_topbar()
    check("countdown format mm:ss when armed",
          re.fullmatch(r"Next scan: \d{2}:\d{2}",
                       win.lbl_countdown.text()) is not None,
          win.lbl_countdown.text())
    check("scan UTC stamp after a scan time is set",
          re.fullmatch(r"Scan: \d{2}:\d{2}:\d{2} UTC",
                       win.lbl_scan_utc.text()) is not None,
          win.lbl_scan_utc.text())
    check("venue pill starts unknown", win.lbl_venue.text() == "MEXC –",
          win.lbl_venue.text())

    # -- rail ----------------------------------------------------------------
    check("rail has 5 page buttons", len(win.rail_buttons) == 5,
          str(list(win.rail_buttons)))
    check("rail starts on Scanner",
          win.current_page() == "scanner"
          and win.rail_buttons["scanner"].isChecked())
    check("gear button present", win.btn_gear is not None)

    for i, col in enumerate(SIGNAL_COLUMNS):
        got = win.tree.horizontalHeaderItem(i).text()
        check(f"heading {col}", got == SIGNAL_HEADINGS[col], got)
    check("signals table starts empty", win.tree.rowCount() == 0)
    check("vetoed table starts empty", win.tree_vetoed.rowCount() == 0)
    veto_heads = [win.tree_vetoed.horizontalHeaderItem(i).text()
                  for i in range(win.tree_vetoed.columnCount())]
    check("vetoed table has REASON column",
          veto_heads == ["DIR", "COIN", "SCORE", "REASON", "VETO CODES"],
          str(veto_heads))
    check("detail placeholder",
          win.lbl_det_sub.text() == "select a row for the full breakdown",
          win.lbl_det_sub.text())
    check("detail key-value table starts empty", win.det_kv.rowCount() == 0)
    check("analyst note pane is read-only", win.detail.isReadOnly())
    check("watchlist page uses three in-page lists",
          win.watch_tabs.count() == 3)
    check("outcomes page has daily and cumulative views",
          [win.outcomes_page_tabs.tabText(i)
           for i in range(win.outcomes_page_tabs.count())]
          == ["Daily · 24h", "Cumulative"])
    check("outcomes dock has 2 tabs",
          [win.out_tabs.tabText(i) for i in range(win.out_tabs.count())]
          == ["Summary", "Performance"])
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
    outcomes = kv_table_text(win.tbl_outcomes_summary)
    check("zero daily cohort row", "Rolling last 24 hours" in outcomes,
          outcomes)
    check("zero daily observations", "0 · 0 distinct coins" in outcomes,
          outcomes)


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
                   "+12.5%", "+0.50", "0.80", "70.0", "–", "WATCH"], str(row0))
    check("BBB row cells (tkinter parity)",
          row1 == ["2", "▼", "BBB", "0.000012", "-1.5%", "$600K", "-0.0200%",
                   "n/a", "-0.30", "0.20", "40.0", "–", "UNVALIDATED"], str(row1))
    check("rank cell carries the row's coin",
          win.tree.item(0, 0).data(Qt.ItemDataRole.UserRole) == "AAA")
    check("LONG row tint", bg_name(win.tree, 0, 0) == qtheme.LONG_BG,
          bg_name(win.tree, 0, 0))
    check("SHORT row band tint",
          bg_name(win.tree, 1, 0) == qtheme.SHORT_BG_ALT,
          bg_name(win.tree, 1, 0))
    check("tints differ", bg_name(win.tree, 0, 0) != bg_name(win.tree, 1, 0))
    check("LONG arrow is green", fg_name(win.tree, 0, DIR_COL) == qtheme.LONG_FG,
          fg_name(win.tree, 0, DIR_COL))
    check("SHORT arrow is red", fg_name(win.tree, 1, DIR_COL) == qtheme.SHORT_FG,
          fg_name(win.tree, 1, DIR_COL))
    check("WATCH text where due", row0[-1] == "WATCH", row0[-1])
    check("WATCH foreground on the row",
          fg_name(win.tree, 0, FLAGS_COL) == qtheme.WATCH_FG,
          fg_name(win.tree, 0, FLAGS_COL))
    check("UNVALIDATED FLAGS cell is an amber pill (bg)",
          bg_name(win.tree, 1, FLAGS_COL) == qtheme.PILL_UNVALIDATED_BG,
          bg_name(win.tree, 1, FLAGS_COL))
    check("UNVALIDATED FLAGS cell is an amber pill (fg)",
          fg_name(win.tree, 1, FLAGS_COL) == qtheme.PILL_UNVALIDATED_FG,
          fg_name(win.tree, 1, FLAGS_COL))
    check("header counts", "shown 2 · vetoed 0" in win.lbl_header.text(),
          win.lbl_header.text())

    # -- vetoed split with humanized REASON ---------------------------------
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
    check("DDD veto cells (unknown code = raw fallback)",
          v0 == ["▲", "DDD", "30.0", "stale", "stale"], str(v0))
    check("CCC veto cells (primary code humanized)",
          v1 == ["▲", "CCC", "20.0", "Late move", "late_move,thin_book"],
          str(v1))
    check("vetoed band tint (row 0)",
          bg_name(win.tree_vetoed, 0, 0) == qtheme.VETO_BG_ALT,
          bg_name(win.tree_vetoed, 0, 0))
    check("vetoed band tint (row 1)",
          bg_name(win.tree_vetoed, 1, 0) == qtheme.VETO_BG,
          bg_name(win.tree_vetoed, 1, 0))
    check("vetoed foreground",
          fg_name(win.tree_vetoed, 0, 4) == qtheme.VETO_FG,
          fg_name(win.tree_vetoed, 0, 4))
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
          win.lbl_det_coin.text() == "QNT"
          and "QNT" in win._compose_detail_text())
    check("plan fetch queued on the plan lane", "QNT" in win._plans_pending)
    plan_jobs = drain(win._plan_jobs)
    check("plan job shape (stake + leverage cap ride along)",
          plan_jobs == [{"coin": "QNT", "stake": win.stake,
                         "leverage_cap": win.leverage_cap}], str(plan_jobs))

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
# 5. WATCHLIST PAGE
# ==========================================================================

def t_segments():
    win = make_window()
    win.set_cards([mkcard("AAA", score=70),
                   mkcard("BBB", score=40, min_notional=50.0),
                   mkcard("CCC", score=60, direction="SHORT")])

    check("watchlist tabs are in-page",
          [win.watch_tabs.tabText(i).split(" (")[0]
           for i in range(win.watch_tabs.count())]
          == ["Watchlist", "Top 10", "New coins"])
    win.digest = {
        "picks": [mkcard("AAA", score=70, lean=0.5, price=1.0)],
        "watch": [mkcard("BBB", score=40, min_notional=50.0)],
        "new": [{"coin": "CCC", "first_seen": 1_700_000_000,
                 "score": 60.0, "direction": "SHORT", "ts": 1_700_000_000}],
    }

    win._render_watchlist()
    check("Top 10 row in page", win.tbl_top_picks.rowCount() == 1,
          str(win.tbl_top_picks.rowCount()))
    check("Top 10 row cells",
          row_cells(win.tbl_top_picks, 0)
          == ["AAA", "▲", "70.0", model.format_price(1.0), "+0.50"],
          str(row_cells(win.tbl_top_picks, 0)))
    check("watchlist row in page", win.tbl_watchlist.rowCount() == 1,
          str(win.tbl_watchlist.rowCount()))
    check("new coin row in page", win.tbl_new_listings.rowCount() == 1,
          str(win.tbl_new_listings.rowCount()))
    check("new coin row cells",
          row_cells(win.tbl_new_listings, 0)
          == ["CCC", time.strftime("%m-%d %H:%M", time.localtime(1_700_000_000)),
              "60.0", "▼"], str(row_cells(win.tbl_new_listings, 0)))

    win.tbl_top_picks.selectRow(0)
    check("pick jumps the main selection", win.selected_coin == "AAA",
          str(win.selected_coin))
    sel = win.tree.selectionModel().selectedRows()
    check("main table row follows the pick",
          len(sel) == 1
          and win.tree.item(sel[0].row(), 0).data(Qt.ItemDataRole.UserRole)
          == "AAA", str([i.row() for i in sel]))
    check("detail shows the picked coin",
          win.lbl_det_coin.text() == "AAA"
          and "AAA" in win._compose_detail_text())
    plan_jobs = [j for j in drain(win._plan_jobs) if j.get("coin") == "AAA"]
    check("pick queues the plan fetch",
          plan_jobs == [{"coin": "AAA", "stake": win.stake,
                         "leverage_cap": win.leverage_cap}], str(plan_jobs))

    win._open_coin_from_list(win.tbl_top_picks)
    check("double-click action routes to Scanner",
          win.current_page() == "scanner", win.current_page())


# ==========================================================================
# 6. OUTCOMES (dock tabs + full page)
# ==========================================================================

STATS_FIXTURE = {"rows": 10, "flagged": 2, "coins": 5, "longs": 4,
                 "shorts": 3, "outcomes": 3}
OUTCOME_FIXTURE = {
    "counts": {"1h": 1, "4h": 2, "24h": 0, "7d": 0}, "total": 3,
    "direction": {"LONG": {"count": 2, "avg_return": 5.5, "hit_rate": 0.5},
                  "SHORT": {"count": 1, "avg_return": -2.0,
                            "hit_rate": 0.0}},
}
HORIZON_FIXTURE = lambda: {
    h: {"resolved": 1, "wins": 1, "losses": 0, "pct_won": 100.0,
        "avg_return": 2.0} for h in model.HORIZONS}
HIT24_FIXTURE = {"window_hours": 24, "signals": 7, "coins": 4,
                 "by_horizon": HORIZON_FIXTURE(),
                 "by_direction_horizon": {
                     d: HORIZON_FIXTURE() for d in ("LONG", "SHORT")},
                 "overall": {"resolved": 4, "wins": 3, "losses": 1,
                             "pct_won": 75.0, "avg_return": 1.25}}
PLAN_FIXTURE = {"planned": 10, "stop_hit": 3, "tp1_hit": 5, "tp2_hit": 1,
                "stop_pct": 30.0, "tp1_pct": 50.0, "tp2_pct": 10.0,
                "terminal": 2, "terminal_pct": 20.0, "measured": 10,
                "plans_logged": 13, "coins": 4,
                "t_stop_hit": 1, "t_tp1_hit": 1, "t_tp2_hit": 0,
                "t_stop_pct": 50.0, "t_tp1_pct": 50.0, "t_tp2_pct": 0.0}


def t_outcomes():
    win = make_window()
    win._handle_msg({"kind": "stats", "ok": True, "stats": STATS_FIXTURE,
                     "outcome": OUTCOME_FIXTURE, "hit24": HIT24_FIXTURE,
                     "hit_all": HIT24_FIXTURE, "plan": PLAN_FIXTURE,
                     "plan24": PLAN_FIXTURE})

    panel = kv_table_text(win.tbl_outcomes_summary)
    check("dock explicitly uses rolling daily cohort",
          "Rolling last 24 hours" in panel
          and "7 · 4 distinct coins" in panel, panel)
    perf = kv_table_text(win.tbl_outcomes_performance)
    check("dock performance reports horizon sample sizes",
          "1H" in perf and "n=1" in perf, perf)
    check("outcomes page offers daily and cumulative cohorts",
          [win.outcomes_page_tabs.tabText(i)
           for i in range(win.outcomes_page_tabs.count())]
          == ["Daily · 24h", "Cumulative"])
    daily_context = win.lbl_outcomes_daily.text()
    check("daily context explains observations and distinct coins",
          "7 flagged scan observations across 4 distinct coins" in daily_context,
          daily_context)
    check("horizon-by-direction sample matrix has 8 rows",
          win.tbl_outcomes_daily.rowCount() == 8,
          str(win.tbl_outcomes_daily.rowCount()))
    check("TP rates show explicit measured denominator",
          row_cells(win.tbl_plans_daily, 3) == ["TP1 reached", "5", "10", "50.0%"],
          str(row_cells(win.tbl_plans_daily, 3)))
    check("terminal-only rates are separately labeled",
          "terminal only" in row_cells(win.tbl_plans_daily, 6)[0])

    header = win.lbl_header.text()
    for frag in ("logs 10 rows / 5 coins", "flagged 2", "outcomes 3"):
        check(f"header {frag!r}", frag in header, header)

    # -- zero shape: no outcomes yet, no plans resolved ----------------------
    zero_hit = {"window_hours": 24, "signals": 0, "coins": 0,
                "by_horizon": {}, "by_direction_horizon": {},
                "overall": {"resolved": 0, "wins": 0, "losses": 0,
                            "pct_won": 0.0, "avg_return": 0.0}}
    win._handle_msg({"kind": "stats", "ok": True,
                     "stats": {"rows": 0, "flagged": 0, "coins": 0,
                               "longs": 0, "shorts": 0, "outcomes": 0},
                     "outcome": model.outcome_summary(None),
                     "hit24": zero_hit, "hit_all": zero_hit,
                     "plan24": {"planned": 0, "measured": 0,
                                "plans_logged": 0, "coins": 0},
                     "plan": {"planned": 0, "measured": 0,
                              "plans_logged": 0, "coins": 0}})
    panel = kv_table_text(win.tbl_outcomes_summary)
    check("zero daily cohort is explicit", "0 · 0 distinct coins" in panel,
          panel)
    check("zero samples show pending instead of a 0% hit rate",
          all(win.tbl_outcomes_daily.item(r, 4).text() == "—"
              for r in range(win.tbl_outcomes_daily.rowCount())))

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
# 9. THEME: dark navy palette, real contrast
# ==========================================================================

def t_theme():
    print("== 9 theme: dark navy palette, bright ink, real contrast")

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

    check("surface is dark navy (terminal base)", lum(qtheme.SURFACE) < 0.15,
          qtheme.SURFACE)
    check("app bg darker than panels",
          lum(qtheme.WINDOW_BG) < lum(qtheme.SURFACE), qtheme.WINDOW_BG)
    check("body text is bright ink", lum(qtheme.TEXT) > 0.7, qtheme.TEXT)
    check("body contrast ≥ 7:1",
          contrast(qtheme.TEXT, qtheme.SURFACE) >= 7.0,
          f"{contrast(qtheme.TEXT, qtheme.SURFACE):.1f}:1")
    check("muted contrast ≥ 4.5:1",
          contrast(qtheme.TEXT_MUTED, qtheme.SURFACE) >= 4.5,
          f"{contrast(qtheme.TEXT_MUTED, qtheme.SURFACE):.1f}:1")
    check("muted on banded rows ≥ 4.5:1",
          contrast(qtheme.TEXT_MUTED, qtheme.SURFACE_ALT) >= 4.5,
          f"{contrast(qtheme.TEXT_MUTED, qtheme.SURFACE_ALT):.1f}:1")
    check("selected white-on-navy ≥ 4.5:1",
          contrast(qtheme.SELECT_FG, qtheme.SELECT_BG) >= 4.5,
          f"{contrast(qtheme.SELECT_FG, qtheme.SELECT_BG):.1f}:1")
    hues = {qtheme.LONG_BG, qtheme.SHORT_BG, qtheme.VETO_BG,
            qtheme.SURFACE, qtheme.WATCH_FG, qtheme.ACCENT}
    check("direction/status hues all distinct", len(hues) == 6, str(hues))
    check("watch amber readable on both direction tints",
          contrast(qtheme.WATCH_FG, qtheme.LONG_BG) >= 4.5
          and contrast(qtheme.WATCH_FG, qtheme.SHORT_BG) >= 4.5,
          f"{contrast(qtheme.WATCH_FG, qtheme.LONG_BG):.1f}:1 / "
          f"{contrast(qtheme.WATCH_FG, qtheme.SHORT_BG):.1f}:1")
    check("error red readable on surface",
          contrast(qtheme.ERROR_FG, qtheme.SURFACE) >= 4.5,
          f"{contrast(qtheme.ERROR_FG, qtheme.SURFACE):.1f}:1")
    check("LONG arrow readable on the LONG tint",
          contrast(qtheme.LONG_FG, qtheme.LONG_BG) >= 4.5,
          f"{contrast(qtheme.LONG_FG, qtheme.LONG_BG):.1f}:1")
    check("SHORT arrow readable on the SHORT tint",
          contrast(qtheme.SHORT_FG, qtheme.SHORT_BG) >= 4.5,
          f"{contrast(qtheme.SHORT_FG, qtheme.SHORT_BG):.1f}:1")
    check("UNVALIDATED pill text readable on the pill",
          contrast(qtheme.PILL_UNVALIDATED_FG,
                   qtheme.PILL_UNVALIDATED_BG) >= 4.5,
          f"{contrast(qtheme.PILL_UNVALIDATED_FG, qtheme.PILL_UNVALIDATED_BG):.1f}:1")
    check("info blue readable on surface",
          contrast(qtheme.INFO_FG, qtheme.SURFACE) >= 4.5,
          f"{contrast(qtheme.INFO_FG, qtheme.SURFACE):.1f}:1")


# ==========================================================================
# 10. RAIL + PAGES
# ==========================================================================

def t_rail():
    win = make_window()
    names = [k for k, _l, _g in qtapp.RAIL_PAGES]
    check("rail page keys", list(win.rail_buttons) == names,
          str(list(win.rail_buttons)))
    for key in ("watchlist", "outcomes", "logs", "settings"):
        win.rail_buttons[key].click()
        check(f"rail click switches to {key}",
              win.current_page() == key
              and win.rail_buttons[key].isChecked(),
              win.current_page())
        check("exactly one rail button checked",
              sum(1 for b in win.rail_buttons.values() if b.isChecked()) == 1)
    win.btn_gear.click()
    check("gear opens the Settings page",
          win.current_page() == "settings", win.current_page())
    win._switch_page("scanner")
    check("back to Scanner", win.current_page() == "scanner")

    # -- watchlist page: picks.watch_list, real rows -------------------------
    # fits_stake is margin-based (min_notional / MAX_LEVERAGE <= stake), so
    # with stake $0.05 only AAA ($5.00 -> 0.10 margin at 50x) is blocked.
    win.ed_stake.setText("0.05")
    win._apply_stake("toolbar")
    win.set_cards([mkcard("AAA", score=70, min_notional=5.0),
                   mkcard("BBB", score=40, min_notional=0.01)])
    win._switch_page("watchlist")
    check("watchlist shows the stake-blocked coin",
          win.tbl_watchlist.rowCount() == 1
          and win.tbl_watchlist.item(0, 0).text() == "AAA",
          str(win.tbl_watchlist.rowCount()))
    check("watchlist hint shows live entry count",
          "entries ·" in win.lbl_watchlist_hint.text(),
          win.lbl_watchlist_hint.text())
    win.tbl_watchlist.selectRow(0)
    check("watchlist click opens detail on the Scanner page",
          win.current_page() == "scanner"
          and win.selected_coin == "AAA"
          and win.lbl_det_coin.text() == "AAA",
          f"{win.current_page()} / {win.selected_coin}")
    # coin outside the current scan: lookup job instead of silence
    win._switch_page("watchlist")
    win.cards = [c for c in win.cards if c.coin != "AAA"]
    win._busy.discard("lookup")
    win.tbl_watchlist.clearSelection()
    win.tbl_watchlist.selectRow(0)
    check("absent coin queues a venue-wide lookup",
          "lookup" in win._busy, str(win._busy))

    # -- logs page: tails the real log file + shows the event buffer ---------
    win._switch_page("logs")
    check("logs page tails logs/app.log (or says so honestly)",
          win.logs_view.toPlainText() != "", win.logs_view.toPlainText()[:60])
    win._push_event("info", "rail test event")
    check("logs page carries the event ring buffer",
          win.events_log_list.count() == 1
          and "rail test event" in win.events_log_list.item(0).text())

    # -- settings page: per-field Apply with validation ----------------------
    win._switch_page("settings")
    win.ed_stake.setText("0.25")
    win._apply_stake("settings")
    check("settings stake apply",
          win.stake == 0.25 and win.ed_stake.text() == "0.25",
          f"{win.stake} / {win.ed_stake.text()}")
    win.ed_stake.setText("-1")
    win._apply_stake("settings")
    check("settings stake validation",
          win.stake == 0.25 and "stake must be" in win.error_text,
          win.error_text)
    win.ed_threshold.setText("33")
    win._apply_threshold("settings")
    check("settings threshold apply", win.log_threshold == 33,
          str(win.log_threshold))
    win.sp_coins.setValue(200)
    check("settings coins apply", win.coins == 200, str(win.coins))
    win.sp_interval.setValue(90)
    check("settings interval apply", win.interval == 90, str(win.interval))
    newdb = os.path.join(win._test_tmp, "other.db")
    win.ed_db.setText(newdb)
    win._apply_db()
    check("settings db apply", win.db == newdb, win.db)
    win.ed_db.setText("")
    win._apply_db()
    check("settings db validation (empty rejected)",
          win.db == newdb and "must not be empty" in win.error_text,
          win.error_text)
    win.cmb_lev.setCurrentText("20x")
    check("settings leverage apply", win.leverage_cap == 20,
          str(win.leverage_cap))
    check("settings leverage syncs the toolbar combo",
          win.cmb_lev.currentText() == "20x", win.cmb_lev.currentText())


# ==========================================================================
# 11. DETAIL PANE: rebuilt, real values, honest states, no invented metrics
# ==========================================================================

def t_detail():
    win = make_window()
    card = mkcard("AAA", score=70, direction="LONG", venue="MEXC",
                  price=100.0,
                  change_24h_pct=3.2, quote_vol_24h=4_300_000,
                  funding_rate=0.0001, oi_change_pct=12.5,
                  lean=0.5, earlyness=0.6)
    win.set_cards([card])
    check("placeholder before selection",
          win.lbl_det_sub.text() == "select a row for the full breakdown",
          win.lbl_det_sub.text())

    win.tree.selectRow(0)
    check("coin header", win.lbl_det_coin.text() == "AAA",
          win.lbl_det_coin.text())
    check("direction badge", "▲ LONG" in win.lbl_det_dir.text(),
          win.lbl_det_dir.text())
    check("UNVALIDATED badge visible for a MEXC row",
          win.lbl_det_unval.isVisible(), "hidden")
    check("subtitle = Base / Quote (Perpetual)",
          win.lbl_det_sub.text() == "AAA / USDT (Perpetual)",
          win.lbl_det_sub.text())
    check("big mono price", win.lbl_det_price.text() == "100",
          win.lbl_det_price.text())
    check("24h% +", win.lbl_det_chg.text() == "+3.2%",
          win.lbl_det_chg.text())
    check("volume tile", win.lbl_det_vol.text() == "$4.3M",
          win.lbl_det_vol.text())
    check("OI tile without notional degrades to the percent",
          win.lbl_det_oi.text() == "+12.5%", win.lbl_det_oi.text())
    check("funding tile", win.lbl_det_funding.text() == "+0.0100%",
          win.lbl_det_funding.text())

    kv = kv_map(win)
    check("kv Direction", kv.get("Direction") == "▲ LONG", str(kv))
    check("kv Score x/100", kv.get("Score") == "70.0 / 100", str(kv))
    check("kv Early Signal + Good qualifier (>= 0.5)",
          kv.get("Early Signal") == "0.60 — Good", str(kv))
    check("kv Lean + Bullish qualifier",
          kv.get("Lean") == "+0.50 — Bullish", str(kv))
    check("kv Funding Rate + Positive qualifier",
          kv.get("Funding Rate") == "+0.0100% — Positive", str(kv))
    check("kv Time is scan UTC",
          re.fullmatch(r"\d{2}:\d{2}:\d{2} UTC", kv.get("Time", "")) is not None,
          str(kv.get("Time")))
    check("plan-pending honest state",
          kv.get("Entry Zone") == "fetching…"
          and kv.get("Leverage") == "fetching… (cap 50x)", str(kv))
    check("no Confidence metric anywhere (honesty)",
          not any("onfidence" in k or "onfidence" in v for k, v in kv.items()))
    check("no 24h High/Low anywhere (honesty)",
          "HIGH" not in " ".join(kv.keys())
          and win.lbl_det_vol.objectName() == "tileValue")
    check("flags section real",
          win.lbl_det_flags.text() == "UNVALIDATED",
          win.lbl_det_flags.text())

    # -- a REAL plan (pure planner compute — no network) fills the levels ----
    plan = pl.build_plan(card, stake=0.10, swing_ref=94.0)
    win._plan_cache["AAA"] = {"plan": plan, "err": None}
    win._render_detail()
    kv = kv_map(win)
    check("kv Entry Zone from the plan",
          kv.get("Entry Zone")
          == f"{plan.entry_low:.8g} – {plan.entry_high:.8g}", str(kv))
    check("kv Stop Loss from the plan", kv.get("Stop Loss") == f"{plan.stop:.8g}",
          str(kv))
    check("kv TP1 from the plan", kv.get("Take Profit 1") == f"{plan.tp1:.8g}",
          str(kv))
    check("kv TP2 from the plan", kv.get("Take Profit 2") == f"{plan.tp2:.8g}",
          str(kv))
    check("kv Leverage with the operator cap (mode)",
          kv.get("Leverage") == f"{plan.leverage}x (cap 50x)", str(kv))

    # -- qualifier corners ---------------------------------------------------
    win.set_cards([mkcard("FRESH", score=10, earlyness=0.4, lean=-0.3,
                          funding_rate=-0.0002, direction="SHORT")])
    win.tree.selectRow(0)
    kv = kv_map(win)
    check("kv Early Signal Fresh (>= 0.3)",
          kv.get("Early Signal") == "0.40 — Fresh", str(kv))
    check("kv Lean Bearish", kv.get("Lean") == "-0.30 — Bearish", str(kv))
    check("kv Funding Negative",
          kv.get("Funding Rate") == "-0.0200% — Negative", str(kv))
    win.set_cards([mkcard("LATE", score=10, earlyness=0.2, lean=0.0,
                          funding_rate=0.0, direction="LONG")])
    win.tree.selectRow(0)
    kv = kv_map(win)
    check("kv Early Signal Late (else)", kv.get("Early Signal") == "0.20 — Late",
          str(kv))
    check("kv Lean Neutral", kv.get("Lean") == "+0.00 — Neutral", str(kv))
    check("kv Funding Neutral",
          kv.get("Funding Rate") == "+0.0000% — Neutral", str(kv))

    # -- analyst notes carry veto + warning text ------------------------------
    # A vetoed card lands in the vetoed table, not the signals table —
    # select it there (same _on_select path drives the detail pane).
    win.set_cards([mkcard("NOTE", score=10, vetoes=("low_volume",),
                          notes=("counter-trend: price above EMA",
                                 "MTF 1H unavailable — no bonus"))])
    check("vetoed card landed in the vetoed table",
          win.tree_vetoed.rowCount() == 1, str(win.tree_vetoed.rowCount()))
    win.tree_vetoed.selectRow(0)
    notes = win.detail.toPlainText()
    check("analyst notes carry the veto text",
          "⨯ low_volume" in notes, notes)
    check("analyst notes carry the counter-trend warning",
          "⚠ counter-trend" in notes, notes)
    check("analyst notes carry the MTF warning", "⚠ MTF 1H" in notes, notes)


# ==========================================================================
# 12. VETO REASON (pure model function)
# ==========================================================================

def t_veto_reason():
    check("low_volume humanized", model.veto_reason("low_volume") == "Low volume")
    check("late_move humanized", model.veto_reason("late_move") == "Late move")
    check("wide_spread humanized",
          model.veto_reason("wide_spread") == "Wide spread")
    check("thin_history humanized",
          model.veto_reason("thin_history") == "Thin history")
    check("unknown code falls back to the raw code",
          model.veto_reason("mystery_code") == "mystery_code")
    check("non-string code is stringified, never invented",
          model.veto_reason(None) == "None")


# ==========================================================================
# 13. LEVERAGE CAP: dropdown -> planner plumbing, end-to-end
# ==========================================================================

def t_leverage_cap():
    win = make_window()
    check("default cap = MAX_LEVERAGE (today's behaviour)",
          win.leverage_cap == MAX_LEVERAGE == 50, str(win.leverage_cap))
    win.set_cards([mkcard("AAA", score=70)])
    win.tree.selectRow(0)
    check("plan fetch queued under the default cap",
          drain(win._plan_jobs) == [{"coin": "AAA", "stake": win.stake,
                                     "leverage_cap": 50}])

    win.cmb_lev.setCurrentText("10x")             # toolbar dropdown
    check("cap written", win.leverage_cap == 10, str(win.leverage_cap))
    check("Settings combo follows", win.cmb_lev.currentText() == "10x")
    check("plan cache cleared (leverage is embedded in plans)",
          win._plan_cache == {})
    check("status names the new cap",
          "leverage cap 10x" in win.scan_status, win.scan_status)
    jobs = drain(win._plan_jobs)
    # The first fetch (drained above) is still in flight, so _ensure_plan
    # does not double-queue it — the stale, old-cap result is refused and
    # re-queued right below instead.
    check("no duplicate queue while the old-cap fetch is in flight",
          jobs == [] and "AAA" in win._plans_pending,
          f"jobs={jobs} pending={win._plans_pending}")

    # an in-flight result computed under the OLD cap must be refused
    win._on_plan({"kind": "plan", "ok": True, "coin": "AAA", "plan": None,
                  "err": "stale", "leverage_cap": 50})
    check("stale-cap plan result refused (not cached)",
          "AAA" not in win._plan_cache, str(win._plan_cache))
    jobs = drain(win._plan_jobs)
    check("refetch re-queued after the refusal",
          jobs and jobs[-1]["leverage_cap"] == 10, str(jobs))
    win._on_plan({"kind": "plan", "ok": True, "coin": "AAA", "plan": None,
                  "err": "fine", "leverage_cap": 10})
    check("current-cap plan result cached", "AAA" in win._plan_cache)

    # the planner honours the cap (pure compute — see also test_planner.py)
    card = mkcard("CAP", score=70, price=100.0, direction="LONG")
    p50 = pl.build_plan(card, stake=50.0, swing_ref=99.0)
    p10 = pl.build_plan(card, stake=50.0, swing_ref=99.0, max_leverage=10)
    check("tight stop computes 50x at the default cap", p50.leverage == 50,
          str(p50.leverage))
    check("tight stop clamps to 10x under a 10x cap", p10.leverage == 10,
          str(p10.leverage))
    check("same stop under both caps", abs(p10.stop - p50.stop) < 1e-9)


# ==========================================================================
# 14. DATA TOOLS: Export writes CSV, Copy composes the shared detail text
# ==========================================================================

def t_data_tools():
    win = make_window()
    win.set_cards([mkcard("AAA", score=70), mkcard("BBB", score=40,
                                                   direction="SHORT")])
    tmp = tempfile.mkdtemp(prefix="alt-radar-csv-")
    TMPDIRS.append(tmp)
    path = os.path.join(tmp, "visible.csv")
    check("export writes the file", win._export_csv(path) is True)
    check("export file exists", os.path.exists(path), path)
    with open(path, newline="", encoding="utf-8") as f:
        import csv as _csv
        rows = list(_csv.reader(f))
    check("csv header = shared SIGNAL_HEADINGS",
          rows[0] == [SIGNAL_HEADINGS[c] for c in SIGNAL_COLUMNS], str(rows[0]))
    check("csv carries the visible table exactly",
          len(rows) == 3 and rows[1] == row_cells(win.tree, 0)
          and rows[2] == row_cells(win.tree, 1), str(rows))
    check("export status line",
          "exported 2 row(s) to" in win.scan_status, win.scan_status)
    check("export cancelled on an empty path", win._export_csv("") is False)

    # -- Copy: the shared model's detail text (clipboard assert skipped
    #    offscreen per the brief; the handler + the composed text are real).
    win.tree.selectRow(0)
    text = win._compose_detail_text()
    check("composed detail text is real + non-empty",
          "AAA" in text and "TRADE PLAN" in text, text[:60])
    check("copy handler runs",
          win._copy_detail_text() is True and "copied" in win.scan_status,
          win.scan_status)
    win.selected_coin = None
    win._render_detail()
    check("copy with no selection is an honest no-op",
          win._copy_detail_text() is False
          and "nothing to copy" in win.error_text, win.error_text)


# ==========================================================================
# 15. EVENTS: colored-dot ring buffer fed by job completions + failures
# ==========================================================================

def t_events():
    win = make_window()
    win._push_event("ok", "scan completed — 3 cards")
    win._push_event("error", "boom")
    win._push_event("info", "scan queued")
    check("ring buffer holds the events", len(win.events) == 3
          and win.events_log_list.count() == 3, str(len(win.events)))
    check("newest event first",
          "scan queued" in win.events_log_list.item(0).text(),
          win.events_log_list.item(0).text())
    check("info dot is blue",
          win.events_log_list.item(0).foreground().color().name()
          == qtheme.INFO_FG)
    check("error dot is red",
          win.events_log_list.item(1).foreground().color().name()
          == qtheme.ERROR_FG)
    check("ok dot is green",
          win.events_log_list.item(2).foreground().color().name()
          == qtheme.LONG_FG)
    for i in range(60):
        win._push_event("info", f"e{i}")
    check("ring buffer capped at 50", len(win.events) == 50
          and win.events_log_list.count() == 50, str(len(win.events)))

    # -- fed from job completions + failures (scan/resolve/collect) ----------
    win2 = make_window()
    win2._handle_msg({"kind": "scan", "ok": True, "cards": [mkcard("ZZZ")],
                      "universe": 1, "failed": 0, "errors": {}, "status": "s",
                      "stats": STATS_FIXTURE})
    check("scan completion feeds a green event",
          any(e[1] == "ok" and "scan completed" in e[2] for e in win2.events),
          str(win2.events))
    win2._handle_msg({"kind": "resolve", "ok": True, "resolved": 2,
                      "plans_resolved": 1, "stats": STATS_FIXTURE,
                      "outcome": OUTCOME_FIXTURE, "hit24": HIT24_FIXTURE,
                      "plan": PLAN_FIXTURE})
    check("resolve completion feeds a green event",
          any("resolved 2" in e[2] for e in win2.events), str(win2.events))
    win2._handle_msg({"kind": "collect", "ok": True, "coins": 5, "bars": 10})
    check("collect completion feeds a green event",
          any("collected bars" in e[2] for e in win2.events), str(win2.events))
    win2._handle_msg({"kind": "resolve", "ok": False, "error": "db locked"})
    check("failure feeds a red event",
          win2.events[-1][1] == "error" and "db locked" in win2.events[-1][2],
          str(win2.events[-1]))


# ==========================================================================
# runner
# ==========================================================================

if __name__ == "__main__":
    tests = [
        ("1 build: offscreen window, top bar, rail, retired lanes, round-trip",
         t_build),
        ("2 table: tints, green ▲/red ▼, UNVALIDATED pill, vetoed REASON",
         t_table),
        ("3 search: narrow / Enter selects / Enter queues a lookup job",
         t_search),
        ("4 sort+dir: combos filter and reorder, header click, numeric cells",
         t_sortdir),
        ("5 watchlist page: Watch / Top 10 / New in-page tabs", t_segments),
        ("6 outcomes: Summary + Performance tabs + full page tables",
         t_outcomes),
        ("7 snapshot: close writes .last_entries, relaunch repopulates",
         t_snapshot),
        ("8 tripwire: sockets blocked, no live tests", t_tripwire),
        ("9 theme: dark navy palette, bright ink, real contrast", t_theme),
        ("10 rail+pages: rail/gear switch pages, settings apply per field",
         t_rail),
        ("11 detail: rebuilt pane, real kv values, honest states, no invention",
         t_detail),
        ("12 veto reason: pure humanization with a raw fallback", t_veto_reason),
        ("13 leverage cap: dropdown -> cache clear -> job cap -> stale refusal",
         t_leverage_cap),
        ("14 data tools: Export writes CSV, Copy composes shared detail text",
         t_data_tools),
        ("15 events: colored-dot ring buffer fed by jobs", t_events),
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
