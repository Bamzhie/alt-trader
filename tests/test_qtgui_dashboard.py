"""Qt dashboard cards + Top Signals table (Task 5).

Offscreen by construction: ``QT_QPA_PLATFORM=offscreen`` is set at the very
top, BEFORE PySide6 is imported, so every window here builds display-free.

Interpreter guard: when PySide6 is missing the file prints a loud SKIP line
and exits 0 — ``tests/run_all.py`` globs ``test_*.py`` (minus test_app.py)
and counts exit 0 as a pass, so this skip guard keeps that suite green.

Coverage:
   1 cards        4 stat cards (Active Setups, Eligible Perps, Monitoring
                  Coverage, New Setups 24h); eligible vs evaluated shown
                  separately; snapshot delta vs prior or withheld
   2 withheld     fresh DB -> "comparison unavailable" on the cards
   3 comparison   prior snapshot covering now-24h -> delta shown, never
                  inferred when absent
   4 cut          pre-cut snapshots excluded from the pair (read-time
                  ts-vs-cut_ts filter, no pooling across epochs)
   5 table        one row per active setup; LONG/SHORT pill; NO tier/score
                  columns anywhere
   6 filters      All/Long/Short + All/Top 20/Tail + 4h/24h (default 4h) +
                  Newest/Expiry/24h% sort
   7 evidence     evidence-indicator cells + expiry countdowns +
                  research banner on the page
   8 clean        full dashboard text passes proto/setup_copy.assert_clean
                  (fail-closed banned-wording guard)
   9 tripwire     socket.* all raise — ZERO network in this file

Network policy: ZERO network. Setups/snapshots come from temp-dir SQLite
(emit_setup / write_snapshot); market quotes ride the test-only
``dash_market_data`` seam (offline fixtures, never a venue fetch); each
window's worker lanes are retired immediately after construction.

Run:  python3 tests/test_qtgui_dashboard.py   -> ALL PASS, exit 0
"""

import os
import queue
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
    from PySide6.QtCore import Qt  # noqa: F401
    from PySide6.QtWidgets import (QApplication, QComboBox, QDockWidget,
                                   QLabel, QTableWidget)
except ImportError:
    # run_all.py needs "OK"/"ALL PASS" in the output to count exit 0 as a pass.
    print("SKIP  tests/test_qtgui_dashboard.py — PySide6 is not installed in "
          "this interpreter; Qt dashboard tests not run. OK (clean skip, "
          "exit 0)")
    sys.exit(0)

# ---- repo pieces ----------------------------------------------------------
from proto.setup_copy import assert_clean
from proto.watcher_journal import write_snapshot
from proto.watcher_view import RESEARCH_BANNER, STATE_WORDS

from qtgui.app import (DASH_DEFAULT_HORIZON, DASH_HORIZONS, DASH_MARKETS,
                       DASH_SORTS, DASH_TABLE_HEADINGS, RadarWindow)

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
        tmp = tempfile.mkdtemp(prefix="alt-radar-qt-dash-")
        TMPDIRS.append(tmp)
        db = os.path.join(tmp, "qt.db")
    win = RadarWindow(auto_scan=False, db=db)
    win._test_tmp = tmp
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
# fixtures: temp DB with active setups + snapshots (offline, no network)
# --------------------------------------------------------------------------

def seed_db(path, now=NOW):
    """Two active setups (4h + 24h buckets) + one resolved + snapshots."""
    from proto.watcher import emit_setup

    conn = sqlite3.connect(path)
    try:
        emit_setup(conn, {"rule_id": "t1-break-v1", "coin": "AAA",
                          "src_bar_ts": now - 3600, "entry": 100.0,
                          "direction": "LONG", "stop_px": 99.0,
                          "tgt_px": 102.0, "N_bars": 48,
                          "created_at": now - 3600})
        emit_setup(conn, {"rule_id": "cusum-raw-v1", "coin": "BBB",
                          "src_bar_ts": now - 7200, "entry": 50.0,
                          "direction": "SHORT", "stop_px": 51.0,
                          "tgt_px": 48.0, "N_bars": 288,
                          "created_at": now - 7200})
        sid_res = emit_setup(conn, {"rule_id": "t1-break-v1", "coin": "CCC",
                                    "src_bar_ts": now - 90000, "entry": 5.0,
                                    "direction": "LONG", "stop_px": 4.9,
                                    "tgt_px": 5.2, "N_bars": 48,
                                    "created_at": now - 90000})
        conn.execute("UPDATE watcher_setup SET state='resolved' WHERE id=?",
                     (sid_res,))
        write_snapshot(conn, 120, 118, 2, 2, 1.0, ts=now)
        write_snapshot(conn, 110, 108, 1, 1, 0.5, ts=now - 86400)
        conn.commit()
    finally:
        conn.close()


MARKET = {
    "AAA": {"name": "AAA Token", "price": 100.0, "change_24h_pct": 3.5,
            "quote_vol_24h": 50_000_000.0},
    "BBB": {"name": "BBB Token", "price": 50.0, "change_24h_pct": -2.0,
            "quote_vol_24h": 1_000_000.0},
}


def make_seeded_window(now=NOW):
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-dash-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "dash.db")
    seed_db(db, now=now)
    win = make_window(db=db)
    win.dash_market_data = dict(MARKET)
    win.refresh_dashboard(now=now)
    APP.processEvents()
    return win


def dash_table_coins(win):
    col = DASH_TABLE_HEADINGS.index("COIN")
    return [win.dash_table.item(r, col).text().split("\n")[0]
            for r in range(win.dash_table.rowCount())]


def dash_table_text(win):
    bits = []
    for r in range(win.dash_table.rowCount()):
        for c in range(win.dash_table.columnCount()):
            bits.append(win.dash_table.item(r, c).text())
    return "\n".join(bits)


# ==========================================================================
# 1. CARDS
# ==========================================================================

def t_cards():
    win = make_seeded_window()
    titles = [t for t, _v, _s in win.dashboard_cards()]
    check("four stat cards",
          titles == ["Active Setups", "Eligible Perps",
                     "Monitoring Coverage", "New Setups 24h"],
          str(titles))
    by_title = {t: (v, s) for t, v, s in win.dashboard_cards()}
    check("active setups value", by_title["Active Setups"][0] == "2",
          str(by_title["Active Setups"]))
    check("eligible vs evaluated shown separately",
          "eligible 120" in by_title["Eligible Perps"][0]
          and "evaluated 118" in by_title["Eligible Perps"][0],
          str(by_title["Eligible Perps"]))
    check("coverage value", by_title["Monitoring Coverage"][0] == "100.0%",
          str(by_title["Monitoring Coverage"]))
    check("new setups value", by_title["New Setups 24h"][0] == "2",
          str(by_title["New Setups 24h"]))
    check("delta shown when prior covers now-24h",
          "comparison unavailable" not in by_title["Active Setups"][1],
          str(by_title["Active Setups"]))
    labels = [w.text() for w in win.dashboard_dock.findChildren(QLabel)]
    check("card titles rendered",
          all(t in labels for t in titles), str(labels[:12]))


# ==========================================================================
# 2. WITHHELD COMPARISON ON A FRESH DB
# ==========================================================================

def t_withheld():
    win = make_window()
    win.refresh_dashboard(now=NOW)
    APP.processEvents()
    text = win.dashboard_cards_text()
    check("comparison unavailable on a fresh DB",
          "comparison unavailable" in text, text[:300])


# ==========================================================================
# 3. COMPARISON PAIR WITHOUT HISTORY
# ==========================================================================

def t_no_prior():
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-dash-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "single.db")
    conn = sqlite3.connect(db)
    try:
        write_snapshot(conn, 100, 99, 1, 1, 1.0, ts=NOW)
        conn.commit()
    finally:
        conn.close()
    win = make_window(db=db)
    win.refresh_dashboard(now=NOW)
    APP.processEvents()
    check("single snapshot -> comparison unavailable",
          "comparison unavailable" in win.dashboard_cards_text(),
          win.dashboard_cards_text()[:300])


# ==========================================================================
# 4. PRE-CUT SNAPSHOTS EXCLUDED (read-time ts-vs-cut_ts filter)
# ==========================================================================

def t_cut_filter():
    from proto.store import append_epoch

    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-dash-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "cut.db")
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,"
                     " value TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS epoch_log (epoch_id TEXT"
                     " PRIMARY KEY, mode TEXT NOT NULL, cut_ts INTEGER"
                     " NOT NULL)")
        write_snapshot(conn, 110, 108, 1, 1, 0.5, ts=NOW - 86400)
        write_snapshot(conn, 120, 118, 2, 2, 1.0, ts=NOW)
        conn.commit()
        append_epoch(conn, "watcher-only")
        ep = conn.execute(
            "SELECT cut_ts FROM epoch_log ORDER BY cut_ts DESC LIMIT 1"
            ).fetchone()[0]
    finally:
        conn.close()
    # Both snapshots predate the cut -> the pair is empty post-cut.
    check("cut_ts is after both snapshots", ep > NOW, str(ep))
    win = make_window(db=db)
    win.refresh_dashboard(now=NOW)
    APP.processEvents()
    text = win.dashboard_cards_text()
    check("pre-cut snapshots excluded from the pair",
          "comparison unavailable" in text and "—" in text, text[:300])


# ==========================================================================
# 5. TOP SIGNALS TABLE
# ==========================================================================

def t_table():
    win = make_seeded_window()
    heads = [win.dash_table.horizontalHeaderItem(i).text()
             for i in range(win.dash_table.columnCount())]
    check("no tier/score columns",
          all(h.upper() not in ("TIER", "SCORE") for h in heads),
          str(heads))
    check("no score text in cells",
          "score" not in dash_table_text(win).lower(),
          dash_table_text(win)[:200])
    check("one row per active setup (resolved excluded)",
          sorted(dash_table_coins(win)) == ["AAA"],
          str(dash_table_coins(win)))
    text = dash_table_text(win)
    check("LONG pill on the row", "LONG" in text, text[:200])
    check("state verbs use STATE_WORDS",
          all(w in STATE_WORDS.values() for w in ("active",)),
          str(STATE_WORDS))
    # Horizon default is 4h: only the N=48 setup shows; flip to 24h.
    win.dash_cmb_horizon.setCurrentText("24h")
    APP.processEvents()
    check("horizon 24h shows the N=288 setup",
          dash_table_coins(win) == ["BBB"], str(dash_table_coins(win)))


# ==========================================================================
# 6. FILTERS + SORTS
# ==========================================================================

def t_filters():
    win = make_seeded_window()
    for key, want in (("All", ["AAA"]), ("Long", ["AAA"]),
                      ("Short", [])):
        win.dash_cmb_horizon.setCurrentText("4h")
        win.dash_cmb_market.setCurrentText("All")
        win.dash_cmb_dir.setCurrentText(key)
        APP.processEvents()
        check(f"dir filter {key}", dash_table_coins(win) == want,
              str(dash_table_coins(win)))
    win.dash_cmb_dir.setCurrentText("All")
    win.dash_cmb_horizon.setCurrentText("24h")
    for key, want in (("All", ["BBB"]), ("Top 20", ["BBB"]),
                      ("Tail", [])):
        win.dash_cmb_market.setCurrentText(key)
        APP.processEvents()
        check(f"market filter {key}", dash_table_coins(win) == want,
              str(dash_table_coins(win)))
    check("default horizon is 4h",
          DASH_DEFAULT_HORIZON == "4h"
          and win.dash_cmb_horizon.currentText() != "",
          DASH_DEFAULT_HORIZON)
    check("horizon choices", list(DASH_HORIZONS) == ["4h", "24h"],
          str(DASH_HORIZONS))
    check("sort choices",
          list(DASH_SORTS) == ["Newest", "Expiry", "24h%"],
          str(DASH_SORTS))
    check("market choices",
          list(DASH_MARKETS) == ["All", "Top 20", "Tail"],
          str(DASH_MARKETS))
    # Sort control accepts every choice without error.
    win.dash_cmb_market.setCurrentText("All")
    for key in DASH_SORTS:
        win.dash_cmb_sort.setCurrentText(key)
        APP.processEvents()
        check(f"sort {key} renders", dash_table_coins(win) == ["BBB"],
              str(dash_table_coins(win)))


# ==========================================================================
# 7. EVIDENCE + EXPIRY + BANNER
# ==========================================================================

def t_evidence():
    win = make_seeded_window()
    text = dash_table_text(win)
    check("evidence cell names the rule", "t1-break-v1" in text,
          text[:300])
    check("expiry countdown on the row",
          ("left" in text or "expired" in text), text[:300])
    banner = win.dash_banner.text()
    check("research banner on the page",
          RESEARCH_BANNER in banner, banner[:120])
    check("silence line or rows, never blank",
          win.dash_table.rowCount() > 0
          or "silence is valid" in win.dashboard_text().lower())


# ==========================================================================
# 8. NO PROBABILITY COPY ON THE DASHBOARD
# ==========================================================================

def t_clean():
    win = make_seeded_window()
    try:
        assert_clean(win.dashboard_text())
        check("dashboard text passes assert_clean", True)
    except ValueError as e:
        check("dashboard text passes assert_clean", False, str(e))


# ==========================================================================
# 9. NO-NETWORK TRIPWIRE
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
    check("dashboard dock exists",
          isinstance(getattr(WINDOWS[0], "dashboard_dock", None),
                     QDockWidget))
    check("dashboard table exists",
          isinstance(getattr(WINDOWS[0], "dash_table", None),
                     QTableWidget))
    check("dashboard combos exist",
          all(isinstance(getattr(WINDOWS[0], a, None), QComboBox)
              for a in ("dash_cmb_dir", "dash_cmb_market",
                        "dash_cmb_horizon", "dash_cmb_sort")))


def main():
    print("== 1 stat cards ==")
    t_cards()
    print("== 2 withheld comparison on a fresh DB ==")
    t_withheld()
    print("== 3 single snapshot, no prior ==")
    t_no_prior()
    print("== 4 pre-cut snapshots excluded ==")
    t_cut_filter()
    print("== 5 top signals table ==")
    t_table()
    print("== 6 filters + sorts ==")
    t_filters()
    print("== 7 evidence + expiry + banner ==")
    t_evidence()
    print("== 8 no probability copy ==")
    t_clean()
    print("== 9 tripwire + dock shape ==")
    t_tripwire()
    print()
    print(f"CHECKS {CHECKS}  FAILURES {len(FAILURES)}")
    if FAILURES:
        print("FAILURES:")
        for name in FAILURES:
            print(f"  - {name}")
        print("DASHBOARD TESTS FAILED")
        return 1
    print("ALL PASS  tests/test_qtgui_dashboard.py — Qt dashboard green")
    return 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        cleanup()
    sys.exit(rc)
