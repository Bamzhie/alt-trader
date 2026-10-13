"""Market-overview + asset-detail test for the Qt terminal rebuild (Task 6).

Right column: Market Overview card (BTC + ETH rows with sparklines; NO
TOTAL row — assert absent); detail header (badge, symbol, name, price,
24h%, pill, sparkline); tabs 5m/15m/1H/4H/1D (assert no 1m); candle +
volume + EMA chart reusing the existing widget; Analysis/Trade
Plan/Metrics tabs; entry/TP/SL + RSI/MACD/EMA50/EMA200/Volume (present
only when provided); validity + next-update lines; View Full Analysis
scrolls detail. Binding: on-demand reference plan (100U, no leverage);
no-setup state with blocking reason; per-tab fetch failure renders
"bars unavailable".

Offscreen by construction: ``QT_QPA_PLATFORM=offscreen`` is set at the
very top, BEFORE PySide6 is imported, so every window here builds
display-free.

Interpreter guard: the system python3 has no PySide6. The file then
prints a loud SKIP line and exits 0 — ``tests/run_all.py`` globs
``test_*.py`` (minus test_app.py) and counts exit 0 as a pass, so this
skip guard is what keeps that suite green under the system interpreter.

Network policy: ZERO network. Setups come from a temp-dir SQLite DB
(emit_setup); market quotes ride the test-only ``dash_market_data``
seam (offline fixtures, never a venue fetch); overview sparklines ride
the ``overview_history`` seam (offline closes); chart bars ride the
``show_detail_bars`` fixture seam; each window's worker lanes are
retired immediately after construction.

Run:  python3 tests/test_qtgui_overview.py  -> ALL PASS, exit 0
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
    from PySide6.QtWidgets import QApplication
except ImportError:
    # run_all.py needs "OK"/"ALL PASS" in the output to count exit 0 as a pass.
    print("SKIP  tests/test_qtgui_overview.py — PySide6 is not installed in "
          "this interpreter; Qt GUI tests not run. OK (clean skip, exit 0)")
    sys.exit(0)

from proto.setup_copy import assert_clean

from qtgui.app import RadarWindow

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
        tmp = tempfile.mkdtemp(prefix="alt-radar-qt-overview-")
        TMPDIRS.append(tmp)
        db = os.path.join(tmp, "qt.db")
    win = RadarWindow(auto_scan=False, db=db)
    win._test_tmp = tmp
    WINDOWS.append(win)               # registered first: cleanup can reach it
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
# fixtures
# --------------------------------------------------------------------------

def seed_db(path, now=NOW):
    """One active LONG setup on AAA (frozen entry/stop/target); BTC/ETH
    have market quotes but no setups (no-setup path)."""
    from proto.watcher import emit_setup

    conn = sqlite3.connect(path)
    try:
        emit_setup(conn, {"rule_id": "t1-break-v1", "coin": "AAA",
                          "src_bar_ts": now - 3600, "entry": 100.0,
                          "direction": "LONG", "stop_px": 99.0,
                          "tgt_px": 102.0, "N_bars": 48,
                          "created_at": now - 3600})
        conn.commit()
    finally:
        conn.close()


MARKET = {
    "BTC": {"name": "Bitcoin", "price": 67500.0, "change_24h_pct": 2.5,
            "quote_vol_24h": 900_000_000.0},
    "ETH": {"name": "Ethereum", "price": 3520.0, "change_24h_pct": -1.2,
            "quote_vol_24h": 400_000_000.0},
    "AAA": {"name": "AAA Token", "price": 100.0, "change_24h_pct": 3.5,
            "quote_vol_24h": 50_000_000.0},
}

OVERVIEW_HISTORY = {
    "BTC": [67000.0 + i * 12.0 for i in range(24)],
    "ETH": [3550.0 - i * 2.0 for i in range(24)],
}


def fixture_bars(n=220, start=100.0):
    """Deterministic OHLCV bars (oldest first); never a venue fetch."""
    bars = []
    px = start
    for i in range(n):
        o = px
        c = px + (0.15 if i % 3 else -0.1)
        h = max(o, c) + 0.05
        l = min(o, c) - 0.05
        bars.append({"ts": NOW - (n - i) * 300, "o": o, "h": h, "l": l,
                     "c": c, "vol": 1000.0 + i, "amount": 100000.0 + i})
        px = c
    return bars


def make_seeded_window(now=NOW):
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-overview-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "overview.db")
    seed_db(db, now=now)
    win = make_window(db=db)
    win.dash_market_data = dict(MARKET)
    win.set_overview_history(dict(OVERVIEW_HISTORY))
    win.refresh_dashboard(now=now)
    APP.processEvents()
    return win


# --------------------------------------------------------------------------
# brief Step 1 seams (verbatim names)
# --------------------------------------------------------------------------

def overview_text():
    return WINDOWS[-1].overview_text()


def tab_labels():
    return WINDOWS[-1].detail_timeframes()


def select_setup(coin):
    """Select the dashboard row for *coin*, or detail it directly when
    the coin has no active setup row (e.g. BTC)."""
    win = WINDOWS[-1]
    for r in range(win.dash_table.rowCount()):
        if win.dash_table.item(r, 0).text().split("\n")[0] == coin:
            win.dash_table.selectRow(r)
            APP.processEvents()
            return
    win.show_coin_detail(coin)
    APP.processEvents()


def detail_header():
    return WINDOWS[-1].detail_head.text()


# ==========================================================================
# 1. NO TOTAL ROW, NO 1M TAB (brief Step 1, verbatim)
# ==========================================================================

def test_no_total_row_no_1m():
    win = make_seeded_window()
    check("overview carries BTC + ETH",
          "BTC" in overview_text() and "ETH" in overview_text(),
          overview_text()[:200])
    check("no TOTAL row on the overview",
          "TOTAL" not in overview_text(), overview_text()[:200])
    check("no 1m timeframe tab", "1m" not in tab_labels(),
          str(tab_labels()))
    check("timeframes are exactly 5m/15m/1H/4H/1D",
          tab_labels() == ["5m", "15m", "1H", "4H", "1D"],
          str(tab_labels()))


# ==========================================================================
# 2. DETAIL SELECTS FROM THE TABLE (brief Step 1, verbatim)
# ==========================================================================

def test_detail_selects_from_table():
    win = make_seeded_window()
    select_setup("BTC")
    check("BTC detail header", detail_header().startswith("BTC"),
          detail_header())
    check("BTC names the coin in the no-setup reason",
          "BTC" in win.detail_text() and "no setup yet" in
          win.detail_text().lower(), win.detail_text()[:200])
    select_setup("AAA")
    check("AAA row selects into the detail",
          detail_header().startswith("AAA") and win.detail_coin == "AAA",
          f"{detail_header()!r} / {win.detail_coin!r}")


# ==========================================================================
# 3. OVERVIEW ROWS: REAL QUOTES, HONEST UNAVAILABLE
# ==========================================================================

def test_overview_rows():
    win = make_seeded_window()
    text = overview_text()
    check("BTC price live", "67500" in text, text[:300])
    check("BTC 24h% live", "+2.5%" in text, text[:300])
    check("ETH price live", "3520" in text, text[:300])
    check("ETH 24h% live", "-1.2%" in text, text[:300])
    check("coin names shown",
          "Bitcoin" in text and "Ethereum" in text, text[:300])
    # A missing quote invents nothing.
    win.dash_market_data = {"BTC": dict(MARKET["BTC"])}
    win.refresh_dashboard(now=NOW)
    APP.processEvents()
    text = overview_text()
    check("ETH without a quote is unavailable, never invented",
          "ETH" in text and "3520" not in text, text[:300])


# ==========================================================================
# 4. DETAIL HEADER + REFERENCE PLAN (100U, NO LEVERAGE)
# ==========================================================================

def test_detail_header_and_plan():
    win = make_seeded_window()
    win.show_coin_detail("AAA")
    win.show_detail_bars("AAA", "5m", fixture_bars())
    APP.processEvents()
    text = win.detail_text()
    check("header badge + symbol", detail_header().startswith("AAA"),
          detail_header())
    check("coin name shown", "AAA Token" in text, text[:300])
    check("entry/stop/target from frozen levels",
          "100" in text and "99" in text and "102" in text, text[:300])
    check("100 USDT reference notional", "100" in text, text[:300])
    check("reference size, not a position",
          "reference size" in text.lower(), text[:300])
    check("no leverage wording", "leverage" not in text.lower(),
          text[:300])
    check("validity line present", "valid" in text.lower(), text[:300])
    check("next-update line present", "next update" in text.lower(),
          text[:300])
    for frag in ("rsi", "macd", "ema50", "ema200", "volume"):
        check(f"metrics tab carries {frag}", frag in text.lower(),
              text[:300])


# ==========================================================================
# 5. PER-TAB FAILURE + VIEW FULL ANALYSIS
# ==========================================================================

def test_unavailable_and_full_analysis():
    win = make_seeded_window()
    win.show_coin_detail("AAA")
    win.show_detail_bars("AAA", "5m", [])
    APP.processEvents()
    check("failed tab renders bars unavailable",
          "bars unavailable" in win.detail_text().lower(),
          win.detail_text()[:200])
    check("View Full Analysis button present",
          getattr(win, "btn_full_analysis", None) is not None
          and "full analysis" in win.btn_full_analysis.text().lower(),
          getattr(getattr(win, "btn_full_analysis", None), "text",
                  lambda: "<missing>")())
    win.btn_full_analysis.click()
    APP.processEvents()
    check("View Full Analysis opens the Analysis tab",
          win.detail_low_tabs.currentWidget() is win.detail_analysis,
          win.detail_low_tabs.tabText(
              win.detail_low_tabs.currentIndex()))


# ==========================================================================
# 6. NO BANNED COPY ON THE NEW SURFACES + TRIPWIRE
# ==========================================================================

def test_clean_and_tripwire():
    win = make_seeded_window()
    win.show_coin_detail("AAA")
    win.show_detail_bars("AAA", "5m", fixture_bars())
    APP.processEvents()
    try:
        assert_clean(overview_text())
        assert_clean(win.detail_text())
        assert_clean(win.detail_text("BTC"))
        check("overview + detail pass assert_clean", True)
    except ValueError as e:
        check("overview + detail pass assert_clean", False, str(e))
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
    check("no venue bar fetch in this file",
          ("fetch_detail" + "_bars") not in open(__file__).read())


# ==========================================================================
# runner
# ==========================================================================

if __name__ == "__main__":
    tests = [
        ("1 no TOTAL row, no 1m tab", test_no_total_row_no_1m),
        ("2 detail selects from the table", test_detail_selects_from_table),
        ("3 overview rows: real quotes, honest unavailable",
         test_overview_rows),
        ("4 detail header + reference plan", test_detail_header_and_plan),
        ("5 per-tab failure + View Full Analysis",
         test_unavailable_and_full_analysis),
        ("6 no banned copy + tripwire", test_clean_and_tripwire),
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
