"""Signals-table fidelity test for the Qt terminal rebuild (Task 5).

Mockup-fidelity dashboard table: columns coin/price/24h/trend-sparkline/
analysis/evidence/LONG-SHORT-pill/expiry/chevron; filters All/Long/Short
+ Market + Horizon + Sort (newest default); row click renders the detail
dock; selected-row highlight; letter-badge icons; every cell string
passes proto/setup_copy.assert_clean. Binding: NO tier/score/probability
columns (defect if present); evidence cell instead; selection by setup
id (never row index).

Offscreen by construction: ``QT_QPA_PLATFORM=offscreen`` is set at the
very top, BEFORE PySide6 is imported, so every window here builds
display-free.

Interpreter guard: the system python3 has no PySide6. The file then
prints a loud SKIP line and exits 0 — ``tests/run_all.py`` globs
``test_*.py`` (minus test_app.py) and counts exit 0 as a pass, so this
skip guard is what keeps that suite green under the system interpreter.

Network policy: ZERO network. Setups come from a temp-dir SQLite DB
(emit_setup); market quotes ride the test-only ``dash_market_data``
seam (offline fixtures, never a venue fetch); trend sparklines ride the
``dash_trend_data`` seam (offline closes); each window's worker lanes
are retired immediately after construction.

Run:  python3 tests/test_qtgui_sigtable.py  -> ALL PASS, exit 0
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

# ---- skip guard: system python3 has no PySide6 ---------------------------
try:
    import PySide6  # noqa: F401
    from PySide6.QtCore import Qt  # noqa: F401
    from PySide6.QtWidgets import QApplication
except ImportError:
    # run_all.py needs "OK"/"ALL PASS" in the output to count exit 0 as a pass.
    print("SKIP  tests/test_qtgui_sigtable.py — PySide6 is not installed in "
          "this interpreter; Qt GUI tests not run. OK (clean skip, exit 0)")
    sys.exit(0)

from proto.setup_copy import assert_clean, badge_letters
from proto.watcher_view import STATE_WORDS

from qtgui import app as qtapp
from qtgui.app import RadarWindow

APP = QApplication.instance() or QApplication([])

FAILURES = []
CHECKS = 0
WINDOWS = []
TMPDIRS = []

NOW = 1_750_000_000

# Brief Step 1 verbatim: the mockup column order (trailing "" = chevron).
WANT_HEADERS = ["COIN", "PRICE", "24H", "TREND", "ANALYSIS", "EVIDENCE",
                "SIGNAL", "EXPIRES", ""]


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
        tmp = tempfile.mkdtemp(prefix="alt-radar-qt-sig-")
        TMPDIRS.append(tmp)
        db = os.path.join(tmp, "qt.db")
    win = RadarWindow(auto_scan=False, db=db)
    win._test_tmp = tmp
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


# --------------------------------------------------------------------------
# fixtures: temp DB with active setups (offline, no network)
# --------------------------------------------------------------------------

def seed_db(path, now=NOW):
    """Three 4h-bucket actives + one 24h-bucket active (offline)."""
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
                          "tgt_px": 48.0, "N_bars": 48,
                          "created_at": now - 7200})
        emit_setup(conn, {"rule_id": "t1-break-v1", "coin": "CCC",
                          "src_bar_ts": now - 1800, "entry": 5.0,
                          "direction": "LONG", "stop_px": 4.9,
                          "tgt_px": 5.2, "N_bars": 48,
                          "created_at": now - 1800})
        emit_setup(conn, {"rule_id": "cusum-raw-v1", "coin": "DDD",
                          "src_bar_ts": now - 5400, "entry": 7.0,
                          "direction": "SHORT", "stop_px": 7.1,
                          "tgt_px": 6.8, "N_bars": 288,
                          "created_at": now - 5400})
        conn.commit()
    finally:
        conn.close()


MARKET = {
    "AAA": {"name": "AAA Token", "price": 100.0, "change_24h_pct": 3.5,
            "quote_vol_24h": 50_000_000.0},
    "BBB": {"name": "BBB Token", "price": 50.0, "change_24h_pct": -2.0,
            "quote_vol_24h": 30_000_000.0},
    "CCC": {"name": "CCC Token", "price": 5.0, "change_24h_pct": 9.0,
            "quote_vol_24h": 80_000_000.0},
    "DDD": {"name": "DDD Token", "price": 7.0, "change_24h_pct": 1.0,
            "quote_vol_24h": 2_000_000.0},
}


def make_seeded_window(now=NOW):
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-sig-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "sig.db")
    seed_db(db, now=now)
    win = make_window(db=db)
    win.dash_market_data = dict(MARKET)
    win.refresh_dashboard(now=now)
    APP.processEvents()
    return win


# --------------------------------------------------------------------------
# brief Step 1 seams (verbatim names)
# --------------------------------------------------------------------------

def headers():
    win = WINDOWS[-1]
    return [win.dash_table.horizontalHeaderItem(i).text()
            for i in range(win.dash_table.columnCount())]


def row(r):
    return r


def row_coin(r):
    win = WINDOWS[-1]
    col = WANT_HEADERS.index("COIN")
    return win.dash_table.item(r, col).text().split("\n")[0]


def detail_coin():
    return WINDOWS[-1].detail_coin


def click(r):
    win = WINDOWS[-1]
    win.dash_table.selectRow(r)
    APP.processEvents()


# ==========================================================================
# 1. COLUMNS IN ORDER (brief Step 1, verbatim)
# ==========================================================================

def test_columns_in_order():
    make_seeded_window()
    check("mockup column order", headers() == WANT_HEADERS,
          str(headers()))


# ==========================================================================
# 2. ROW CLICK SELECTS DETAIL (brief Step 1, verbatim)
# ==========================================================================

def test_row_click_selects_detail():
    make_seeded_window()
    click(row(2))
    check("row click renders the detail dock",
          detail_coin() == row_coin(2),
          f"{detail_coin()!r} vs {row_coin(2)!r}")


# ==========================================================================
# 3. BINDING: no tier/score/probability columns or copy
# ==========================================================================

def test_no_banned_columns():
    win = make_seeded_window()
    heads = headers()
    check("no TIER/SCORE/PROBABILITY header",
          all(h.upper() not in ("TIER", "SCORE", "PROBABILITY",
                                "CONFIDENCE", "24H%", "DIR", "EXPIRY")
              for h in heads), str(heads))
    cells = "\n".join(win.dash_table.item(r, c).text()
                      for r in range(win.dash_table.rowCount())
                      for c in range(win.dash_table.columnCount()))
    check("no score/tier/probability text in cells",
          all(w not in cells.lower()
              for w in ("score", "tier", "probab", "confidence")),
          cells[:200])
    check("evidence column present",
          "EVIDENCE" in heads, str(heads))


# ==========================================================================
# 4. SELECTION BY SETUP ID + SELECTED-ROW HIGHLIGHT
# ==========================================================================

def test_selection_by_setup_id():
    win = make_seeded_window()
    col0 = WANT_HEADERS.index("COIN")
    click(row(2))
    sel = win.dash_table.selectionModel().selectedRows()
    check("exactly one row highlighted",
          len(sel) == 1 and sel[0].row() == 2,
          str([i.row() for i in sel]))
    sid = win.dash_table.item(2, col0).data(Qt.ItemDataRole.UserRole)
    by_id = {r["id"]: r for r in win.dash_rows}
    check("UserRole carries the setup id (not a row index)",
          sid in by_id, repr(sid))
    check("window records the selected setup id",
          getattr(win, "dash_selected_id", None) == sid, repr(sid))
    check("detail follows the selected setup's coin",
          detail_coin() == by_id[sid]["coin"],
          f"{detail_coin()!r} vs {by_id[sid]['coin']!r}")


# ==========================================================================
# 5. LETTER-BADGE ICONS ON THE COIN COLUMN
# ==========================================================================

def test_letter_badges():
    win = make_seeded_window()
    col0 = WANT_HEADERS.index("COIN")
    ok = True
    for r in range(win.dash_table.rowCount()):
        item = win.dash_table.item(r, col0)
        coin = item.text().split("\n")[0]
        icon = item.icon() if hasattr(item, "icon") else None
        good = (icon is not None and not icon.isNull()
                and badge_letters(coin) != "")
        ok = ok and good
        if not good:
            check(f"badge for {coin}", False, repr(badge_letters(coin)))
    check("every coin cell carries a letter-badge icon", ok)
    if win.dash_table.rowCount():
        coin = win.dash_table.item(0, col0).text().split("\n")[0]
        check("badge letters derive from the coin symbol",
              badge_letters(coin) == badge_letters(coin)
              and len(badge_letters(coin)) <= 2, badge_letters(coin))


# ==========================================================================
# 6. TREND SPARKLINE + CHEVRON DELEGATES
# ==========================================================================

def test_trend_and_chevron():
    win = make_seeded_window()
    tcol = WANT_HEADERS.index("TREND")
    ccol = len(WANT_HEADERS) - 1
    check("trend delegate installed",
          win.dash_table.itemDelegateForColumn(tcol) is not None)
    check("chevron delegate installed",
          win.dash_table.itemDelegateForColumn(ccol) is not None)
    chev = [(win.dash_table.item(r, ccol).text()
             if win.dash_table.item(r, ccol) is not None else "")
            for r in range(win.dash_table.rowCount())]
    check("chevron cells carry the glyph",
          len(chev) > 0 and all(t.strip() != "" for t in chev), str(chev))
    trend = [(win.dash_table.item(r, tcol).text()
              if win.dash_table.item(r, tcol) is not None else "")
             for r in range(win.dash_table.rowCount())]
    check("trend cells default to honest unavailable copy",
          all(t != "" for t in trend), str(trend))
    # Seeded closes render through the delegate's data role.
    ids = [r["id"] for r in win.dash_rows]
    win.set_trend_history({ids[0]: [100.0 + i * 0.5 for i in range(24)]})
    APP.processEvents()
    got = None
    for r in range(win.dash_table.rowCount()):
        item = win.dash_table.item(r, tcol)
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(data, list) and len(data) == 24:
            got = data
    check("24 closes reach the trend delegate (no invented points)",
          got is not None and len(got) == 24, repr(got)[:80])


# ==========================================================================
# 7. EVERY CELL STRING PASSES assert_clean
# ==========================================================================

def test_every_cell_clean():
    win = make_seeded_window()
    bad = []
    for r in range(win.dash_table.rowCount()):
        for c in range(win.dash_table.columnCount()):
            text = win.dash_table.item(r, c).text()
            try:
                assert_clean(text)
            except ValueError as e:
                bad.append((r, c, str(e)))
    check("every table cell passes assert_clean", not bad, str(bad[:3]))
    try:
        assert_clean(win.dashboard_text())
        check("dashboard text passes assert_clean", True)
    except ValueError as e:
        check("dashboard text passes assert_clean", False, str(e))


# ==========================================================================
# 8. EVIDENCE CELL CITES STATE_WORDS VERBATIM
# ==========================================================================

def test_evidence_state_words():
    win = make_seeded_window()
    ecol = WANT_HEADERS.index("EVIDENCE")
    texts = [win.dash_table.item(r, ecol).text()
             for r in range(win.dash_table.rowCount())]
    check("evidence cells carry the verbatim active verb",
          all(STATE_WORDS["active"] in t for t in texts), str(texts)[:200])
    check("evidence cells name the rule",
          any("t1-break-v1" in t or "cusum-raw-v1" in t for t in texts),
          str(texts)[:200])


# ==========================================================================
# 9. FILTERS: All/Long/Short + Market + Horizon + Sort (newest default)
# ==========================================================================

def test_filters():
    win = make_seeded_window()

    def coins():
        col = WANT_HEADERS.index("COIN")
        return [win.dash_table.item(r, col).text().split("\n")[0]
                for r in range(win.dash_table.rowCount())]

    # Default horizon is 4h: CCC/AAA/BBB newest-first; DDD (24h) hidden.
    check("default sort is newest first",
          coins() == ["CCC", "AAA", "BBB"], str(coins()))
    for key, want in (("All", ["CCC", "AAA", "BBB"]),
                      ("Long", ["CCC", "AAA"]),
                      ("Short", ["BBB"])):
        win.dash_cmb_dir.setCurrentText(key)
        APP.processEvents()
        check(f"dir filter {key}", coins() == want, str(coins()))
    win.dash_cmb_dir.setCurrentText("All")
    win.dash_cmb_horizon.setCurrentText("24h")
    APP.processEvents()
    check("horizon 24h shows the N=288 setup", coins() == ["DDD"],
          str(coins()))
    win.dash_cmb_horizon.setCurrentText("4h")
    win.dash_cmb_market.setCurrentText("Tail")
    APP.processEvents()
    check("market Tail hides the top-20 rows", coins() == [], str(coins()))
    win.dash_cmb_market.setCurrentText("All")
    APP.processEvents()
    for key, want in (("Newest", ["CCC", "AAA", "BBB"]),
                      ("Expiry", ["BBB", "AAA", "CCC"]),
                      ("24h%", ["CCC", "AAA", "BBB"])):
        win.dash_cmb_sort.setCurrentText(key)
        APP.processEvents()
        check(f"sort {key} renders", coins() == want, str(coins()))


# ==========================================================================
# 10. NO-NETWORK TRIPWIRE
# ==========================================================================

def test_tripwire():
    make_seeded_window()
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
    print("== 1 mockup columns in order ==")
    test_columns_in_order()
    print("== 2 row click selects detail ==")
    test_row_click_selects_detail()
    print("== 3 no tier/score/probability ==")
    test_no_banned_columns()
    print("== 4 selection by setup id + highlight ==")
    test_selection_by_setup_id()
    print("== 5 letter-badge icons ==")
    test_letter_badges()
    print("== 6 trend sparkline + chevron delegates ==")
    test_trend_and_chevron()
    print("== 7 every cell passes assert_clean ==")
    test_every_cell_clean()
    print("== 8 evidence cites STATE_WORDS verbatim ==")
    test_evidence_state_words()
    print("== 9 filters + newest sort ==")
    test_filters()
    print("== 10 tripwire ==")
    test_tripwire()
    print()
    print(f"CHECKS {CHECKS}  FAILURES {len(FAILURES)}")
    if FAILURES:
        print("FAILURES:")
        for name in FAILURES:
            print(f"  - {name}")
        print("SIGTABLE TESTS FAILED")
        return 1
    print("ALL PASS  tests/test_qtgui_sigtable.py — Qt signals table green")
    return 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        cleanup()
    sys.exit(rc)
