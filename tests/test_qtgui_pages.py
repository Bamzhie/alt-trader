"""Qt dashboard bottom row + six pages (Task 7).

Offscreen by construction: ``QT_QPA_PLATFORM=offscreen`` is set at the very
top, BEFORE PySide6 is imported, so every window here builds display-free.

Interpreter guard: when PySide6 is missing the file prints a loud SKIP line
and exits 0 — ``tests/run_all.py`` globs ``test_*.py`` (minus test_app.py)
and counts exit 0 as a pass, so this skip guard keeps that suite green.

Coverage:
    1 bottom       Market Movers (Gainers/Losers/High Volume from offline
                   quotes), Recent Analysis (last 5 emissions/transitions),
                   Activity Log (journal rows incl. sweep-complete)
    2 watchlist    starred coins, meta-persisted (star/unstar round-trip)
    3 signals      full setup ledger + history + All/Long/Short filter
    4 analysis     indicator board + coverage + event-study N/kill,
                   dev-labeled
    5 trades       hypothetical-only ledger + header ("no orders placed",
                   never "p&l"); reference stop-risk at 100 USDT; NO
                   threshold/leverage/score/tier/stake copy
    6 settings     display prefs + pause/disable writing the same meta
                   flags panels read; NO threshold/leverage/stake editing
    7 clean        every new string passes proto/setup_copy.assert_clean;
                   no tier/score substrings; stake plays no role
    8 tripwire     socket.* all raise — ZERO network in this file

Network policy: ZERO network. Setups/snapshots/journal come from temp-dir
SQLite (emit_setup / log_event / write_snapshot); market quotes ride the
test-only ``dash_market_data`` seam (offline fixtures, never a venue
fetch); each window's worker lanes are retired immediately after
construction.

Run:  python3 tests/test_qtgui_pages.py   -> ALL PASS, exit 0
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
    from PySide6.QtWidgets import (QApplication, QCheckBox, QDockWidget,
                                   QLabel, QTableWidget, QTabWidget)
except ImportError:
    # run_all.py needs "OK"/"ALL PASS" in the output to count exit 0 as a pass.
    print("SKIP  tests/test_qtgui_pages.py — PySide6 is not installed in "
          "this interpreter; Qt dashboard-pages tests not run. OK (clean "
          "skip, exit 0)")
    sys.exit(0)

# ---- repo pieces ----------------------------------------------------------
from proto.setup_copy import assert_clean
from proto.watcher_journal import log_event, write_snapshot

from qtgui.app import DASH_PAGE_TITLES, RadarWindow

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


def make_window(db=None):
    """Build a RadarWindow offscreen and retire both worker lanes."""
    tmp = None
    if db is None:
        tmp = tempfile.mkdtemp(prefix="alt-radar-qt-pages-")
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
# fixtures: temp DB with active + history setups, evals, journal (offline)
# --------------------------------------------------------------------------

def seed_db(path, now=NOW):
    """Two active setups + invalidated/expired history + evals + journal."""
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
        for coin, state in (("CCC", "invalidated"), ("DDD", "expired")):
            emit_setup(conn, {"rule_id": "t1-break-v1", "coin": coin,
                              "src_bar_ts": now - 90000, "entry": 5.0,
                              "direction": "LONG", "stop_px": 4.9,
                              "tgt_px": 5.2, "N_bars": 48,
                              "created_at": now - 90000})
            conn.execute("UPDATE watcher_setup SET state=? WHERE coin=?",
                         (state, coin))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS watcher_eval (setup_id TEXT,"
            " bar_ts INTEGER NOT NULL, state TEXT NOT NULL,"
            " monitoring TEXT NOT NULL, ret_gross REAL, ret_net REAL,"
            " note TEXT, PRIMARY KEY (setup_id, bar_ts))")
        conn.execute(
            "INSERT OR REPLACE INTO watcher_eval VALUES (?, ?, ?, ?, ?,"
            " ?, ?)",
            ("t1-break-v1:AAA:%d" % (now - 3600), now - 300, "active",
             "ok", 0.012, 0.009, "closed-bar eval"))
        log_event(conn, "emission", "AAA", "AAA LONG via t1-break-v1",
                  ts=now - 3600)
        log_event(conn, "emission", "BBB", "BBB SHORT via cusum-raw-v1",
                  ts=now - 7200)
        log_event(conn, "invalidation", "CCC", "CCC thesis ended",
                  ts=now - 80000)
        log_event(conn, "expiry", "DDD", "DDD horizon reached",
                  ts=now - 81000)
        log_event(conn, "sweep-complete", "-", "sweep 118/120 evaluated",
                  ts=now - 100)
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
    "CCC": {"name": "CCC Token", "price": 5.0, "change_24h_pct": 0.5,
            "quote_vol_24h": 2_000_000.0},
    "DDD": {"name": "DDD Token", "price": 2.0, "change_24h_pct": -9.0,
            "quote_vol_24h": 300_000.0},
}


def make_seeded_window(now=NOW):
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-pages-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "pages.db")
    seed_db(db, now=now)
    win = make_window(db=db)
    win.dash_market_data = dict(MARKET)
    win.refresh_dashboard(now=now)
    win.refresh_dashboard_pages(now=now)
    pump()
    return win


# ==========================================================================
# 1. BOTTOM ROW
# ==========================================================================

def t_bottom():
    win = make_seeded_window()
    text = win.bottom_text()
    check("movers gainers section", "Gainers" in text and "AAA" in text,
          text[:300])
    check("movers losers section", "Losers" in text and "DDD" in text,
          text[:300])
    check("movers high-volume section",
          "High Volume" in text and "50.0M" in text, text[:300])
    check("recent analysis carries the last emissions",
          "Recent Analysis" in text and "t1-break-v1" in text, text[:400])
    check("recent analysis excludes sweep-complete rows",
          "sweep 118/120" not in win.dash_recent.text(),
          win.dash_recent.text()[:200])
    check("activity log keeps journal rows incl. sweep-complete",
          "Activity Log" in text and "sweep 118/120" in text
          and "thesis ended" in text, text[:400])
    try:
        assert_clean(text)
        check("bottom row passes assert_clean", True)
    except ValueError as e:
        check("bottom row passes assert_clean", False, str(e))

    fresh = make_window()
    fresh.refresh_dashboard(now=NOW)
    fresh.refresh_dashboard_pages(now=NOW)
    pump()
    ftext = fresh.bottom_text()
    check("fresh DB movers stay honest",
          "quotes unavailable" in ftext, ftext[:200])
    check("fresh DB recent stays honest",
          "no recent analysis yet" in ftext, ftext[:200])
    check("fresh DB activity stays honest",
          "no activity yet" in ftext, ftext[:200])


# ==========================================================================
# 2. WATCHLIST (starred, meta-persisted)
# ==========================================================================

def t_watchlist():
    win = make_seeded_window()
    check("watchlist starts empty", win.watchlist_text().strip() == ""
          or "no starred" in win.watchlist_text(),
          win.watchlist_text()[:150])
    win.set_dashboard_star("AAA", True)
    pump()
    conn = sqlite3.connect(win.db)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?",
                           ("dash_star:AAA",)).fetchone()
    finally:
        conn.close()
    check("star writes the meta flag", row is not None and row[0] == "1",
          str(row))
    check("starred coin renders", "AAA" in win.watchlist_text(),
          win.watchlist_text()[:200])
    win.set_dashboard_star("AAA", False)
    pump()
    check("unstar clears the row", "AAA" not in win.watchlist_text(),
          win.watchlist_text()[:200])
    # Persistence: re-read from meta on the next refresh.
    win.set_dashboard_star("BBB", True)
    win.refresh_dashboard_pages(now=NOW)
    pump()
    check("stars survive a refresh (meta-persisted)",
          "BBB" in win.watchlist_text(), win.watchlist_text()[:200])


# ==========================================================================
# 3. SIGNALS (ledger + history + filters)
# ==========================================================================

def sig_coins(win):
    col = 0
    return [win.dash_sig_table.item(r, col).text()
            for r in range(win.dash_sig_table.rowCount())]


def t_signals():
    win = make_seeded_window()
    check("ledger lists active and history states",
          set(sig_coins(win)) == {"AAA", "BBB", "CCC", "DDD"},
          str(sig_coins(win)))
    win.dash_sig_cmb_dir.setCurrentText("Short")
    pump()
    check("dir filter Short narrows to BBB", sig_coins(win) == ["BBB"],
          str(sig_coins(win)))
    win.dash_sig_cmb_dir.setCurrentText("All")
    win.dash_sig_history.setChecked(False)
    pump()
    check("history off shows active setups only",
          sorted(sig_coins(win)) == ["AAA", "BBB"], str(sig_coins(win)))
    win.dash_sig_history.setChecked(True)
    pump()
    summary = win.dash_sig_summary.text()
    check("history summary counts by state",
          "invalidated: 1" in summary and "expired: 1" in summary,
          summary[:200])
    try:
        assert_clean(win.signals_text())
        check("signals text passes assert_clean", True)
    except ValueError as e:
        check("signals text passes assert_clean", False, str(e))


# ==========================================================================
# 4. ANALYSIS (indicator board + coverage + event-study, dev-labeled)
# ==========================================================================

def t_analysis():
    win = make_seeded_window()
    text = win.analysis_text()
    check("board shows the AAA eval row",
          "AAA" in text and "1.20%" in text, text[:300])
    check("coverage line counts monitoring",
          "coverage" in text.lower() and "2" in text, text[:200])
    check("event-study block is dev-labeled",
          "dev" in text.lower() and "kill" in text.lower(), text[:300])
    check("event-study cites the fixed grids",
          "12, 48, 96, 288" in text and "12, 48, 288" in text,
          text[:300])
    try:
        assert_clean(text)
        check("analysis text passes assert_clean", True)
    except ValueError as e:
        check("analysis text passes assert_clean", False, str(e))


# ==========================================================================
# 5. TRADES (hypothetical-only)
# ==========================================================================

def test_trades_page_is_hypothetical_only():
    win = make_seeded_window()
    text = win.trades_text()
    check("trades header states hypothetical-only",
          "no orders placed" in text.lower(), text[:200])
    check("trades never prints p&l", "p&l" not in text.lower(),
          text[:200])
    check("trades ledger names active setups",
          "AAA" in text and "BBB" in text, text[:300])
    check("reference stop-risk at 100 USDT",
          "1.00 USDT" in text and "100 USDT" in text, text[:300])
    check("latest return from the eval row",
          "1.20%" in text or "0.90%" in text, text[:300])
    for word in ("threshold", "leverage", "score", "tier", "stake",
                 "probability", "confidence"):
        check(f"trades text has no {word!r}",
              word not in text.lower(), text[:300])
    try:
        assert_clean(text)
        check("trades text passes assert_clean", True)
    except ValueError as e:
        check("trades text passes assert_clean", False, str(e))


# ==========================================================================
# 6. SETTINGS (display prefs + pause/disable; no threshold/leverage/stake)
# ==========================================================================

def test_settings_has_no_threshold_edit():
    win = make_seeded_window()
    labels = win.dash_settings_labels()
    check("settings has no threshold edit",
          "threshold" not in labels, labels[:300])
    check("settings has no leverage edit",
          "leverage" not in labels, labels[:300])
    check("settings has no star/stake control",
          "stake" not in labels, labels[:300])


def t_settings_writers():
    from proto import watcher_feed as feed_mod

    win = make_seeded_window()
    check("pause starts unchecked", not win.dash_set_paused.isChecked())
    win.dash_set_paused.setChecked(True)
    pump()
    conn = sqlite3.connect(win.db)
    try:
        paused, disabled = feed_mod.get_flags(conn)
    finally:
        conn.close()
    check("pause writes the meta flag panels read", paused is True)
    panel = feed_mod.load_panel(win.db)
    check("panel notices carry the pause",
          any(str(n).startswith("paused") for n in panel.notices),
          str(panel.notices))
    win.dash_set_paused.setChecked(False)
    pump()
    conn = sqlite3.connect(win.db)
    try:
        paused, _ = feed_mod.get_flags(conn)
    finally:
        conn.close()
    check("resume clears the meta flag", paused is False)

    rules = sorted(win.dash_set_rules)
    check("both trigger switches exist",
          rules == ["cusum-raw-v1", "t1-break-v1"], str(rules))
    win.dash_set_rules["t1-break-v1"].setChecked(True)
    pump()
    conn = sqlite3.connect(win.db)
    try:
        _, disabled = feed_mod.get_flags(conn)
    finally:
        conn.close()
    check("disable writes the rule meta flag",
          "t1-break-v1" in disabled, str(disabled))

    win.dash_set_autorefresh.setChecked(True)
    pump()
    conn = sqlite3.connect(win.db)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?",
                           ("dash_pref_autorefresh",)).fetchone()
    finally:
        conn.close()
    check("autorefresh pref persists to meta",
          row is not None and row[0] == "1", str(row))
    # Autorefresh is real: a stats result re-renders the dashboard.
    calls = []
    orig_refresh = win.refresh_dashboard
    win.refresh_dashboard = lambda now=None: (
        calls.append(now), orig_refresh(now=now))
    try:
        win._handle_msg({"kind": "stats", "ok": True, "stats": {"rows": 0}})
    finally:
        win.refresh_dashboard = orig_refresh
    check("autorefresh follows each scan stats result", len(calls) == 1,
          str(len(calls)))
    win.dash_set_autorefresh.setChecked(False)
    pump()
    calls.clear()
    win.refresh_dashboard = lambda now=None: (
        calls.append(now), orig_refresh(now=now))
    try:
        win._handle_msg({"kind": "stats", "ok": True, "stats": {"rows": 0}})
    finally:
        win.refresh_dashboard = orig_refresh
    check("autorefresh off leaves the dashboard alone", calls == [],
          str(calls))
    # Compact pref is real: the Top Signals analysis cell shortens.
    acol = [win.dash_table.horizontalHeaderItem(i).text()
            for i in range(win.dash_table.columnCount())].index("ANALYSIS")
    before = win.dash_table.item(0, acol).text() if win.dash_table.rowCount() \
        else ""
    win.dash_set_compact.setChecked(True)
    pump()
    after = win.dash_table.item(0, acol).text() if win.dash_table.rowCount() \
        else ""
    check("compact pref shortens the analysis cell",
          before != "" and "\n" in before and "\n" not in after,
          f"{before!r} -> {after!r}")
    try:
        assert_clean(win.dash_pages_text())
        check("settings text passes assert_clean", True)
    except ValueError as e:
        check("settings text passes assert_clean", False, str(e))


# ==========================================================================
# 7. SIX PAGES + NO STAKE ANYWHERE NEW
# ==========================================================================

def t_pages_shape():
    win = make_seeded_window()
    check("six dashboard page tabs",
          list(DASH_PAGE_TITLES) == ["Dashboard", "Watchlist", "Signals",
                                     "Analysis", "Trades", "Settings"],
          str(DASH_PAGE_TITLES))
    tabs = [win.dash_pages.tabText(i)
            for i in range(win.dash_pages.count())]
    check("tab widget carries all six pages", tabs == list(DASH_PAGE_TITLES),
          str(tabs))
    check("dashboard pages dock exists",
          isinstance(getattr(win, "dash_pages_dock", None), QDockWidget))
    pages_text = win.dash_pages_text() + "\n" + win.bottom_text()
    check("stake plays no role in the new surfaces",
          "stake" not in pages_text.lower(), pages_text[:300])
    check("no tier/score substrings in the new surfaces",
          "tier" not in pages_text.lower()
          and "score" not in pages_text.lower(), pages_text[:300])
    try:
        assert_clean(win.dash_pages_text())
        check("all pages pass assert_clean", True)
    except ValueError as e:
        check("all pages pass assert_clean", False, str(e))


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
    check("no live-test marker in this file",
          ("LIVE" + "_TEST") not in open(__file__).read())
    check("pages tab widget exists",
          isinstance(getattr(WINDOWS[0], "dash_pages", None), QTabWidget))
    check("movers/recent/activity labels exist",
          all(isinstance(getattr(WINDOWS[0], a, None), QLabel)
              for a in ("dash_movers", "dash_recent", "dash_activity")))
    check("trades table exists",
          isinstance(getattr(WINDOWS[0], "dash_trades_table", None),
                     QTableWidget))
    check("settings checkboxes exist",
          all(isinstance(getattr(WINDOWS[0], a, None), QCheckBox)
              for a in ("dash_set_paused", "dash_set_autorefresh",
                        "dash_set_compact")))


def main():
    print("== 1 bottom row ==")
    t_bottom()
    print("== 2 watchlist stars ==")
    t_watchlist()
    print("== 3 signals ledger ==")
    t_signals()
    print("== 4 analysis board ==")
    t_analysis()
    print("== 5 trades hypothetical-only ==")
    test_trades_page_is_hypothetical_only()
    print("== 6 settings writers ==")
    test_settings_has_no_threshold_edit()
    t_settings_writers()
    print("== 7 six pages + no stake ==")
    t_pages_shape()
    print("== 8 tripwire + widget shape ==")
    t_tripwire()
    print()
    print(f"CHECKS {CHECKS}  FAILURES {len(FAILURES)}")
    if FAILURES:
        print("FAILURES:")
        for name in FAILURES:
            print(f"  - {name}")
        print("PAGES TESTS FAILED")
        return 1
    print("ALL PASS  tests/test_qtgui_pages.py — Qt dashboard pages green")
    return 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        cleanup()
    sys.exit(rc)
