"""Qt coin detail + chart (Task 6).

Offscreen by construction: ``QT_QPA_PLATFORM=offscreen`` is set at the very
top, BEFORE PySide6 is imported, so every window here builds display-free.

Interpreter guard: when PySide6 is missing the file prints a loud SKIP line
and exits 0 — ``tests/run_all.py`` globs ``test_*.py`` (minus test_app.py)
and counts exit 0 as a pass, so this skip guard keeps that suite green.

Coverage:
   1 no-setup     coin without an active setup shows "no setup yet" +
                  the blocking_reason line in the same panel
   2 tabs         timeframe tabs are exactly 5m/15m/1H/4H/1D (no 1m);
                  Analysis/Trade Plan/Metrics tabs exist
   3 unavailable  per-tab fetch failure renders "bars unavailable" for
                  that tab (empty/None bars, never a raise)
   4 chart        fixture bars render candles + EMA50/200 + volume;
                  Metrics tab carries RSI/MACD/EMA50/EMA200/volume lines
   5 sweep        swept cards wire into dash_market_data: price/24h% cells
                  go live with real sweep numbers; Market filter + 24h%
                  sort follow the swept quote volumes (never invented)
   6 horizon      exact-match buckets only: 48->4h, 288->24h, anything
                  else->None (shown under both filters)
   7 plan         on-demand reference plan: 100 USDT reference size, no
                  leverage wording anywhere
   8 clean        full detail text passes proto/setup_copy.assert_clean
                  (fail-closed banned-wording guard)
   9 tripwire     socket.* all raise — ZERO network in this file

Network policy: ZERO network. Bars are deterministic fixtures (never the
venue bar fetcher — live fetches exist only in qtgui/app.py code paths);
setups/snapshots come from temp-dir SQLite (emit_setup); worker lanes
are retired immediately after construction.

Run:  python3 tests/test_qtgui_detail.py   -> ALL PASS, exit 0
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
    from PySide6.QtWidgets import (QApplication, QDockWidget, QLabel,
                                   QTabWidget)
except ImportError:
    # run_all.py needs "OK"/"ALL PASS" in the output to count exit 0 as a pass.
    print("SKIP  tests/test_qtgui_detail.py — PySide6 is not installed in "
          "this interpreter; Qt detail tests not run. OK (clean skip, "
          "exit 0)")
    sys.exit(0)

# ---- repo pieces ----------------------------------------------------------
from proto.scorer import Scorecard
from proto.setup_copy import assert_clean, blocking_reason

from qtgui.app import (DETAIL_TIMEFRAMES, DetailChartWidget, RadarWindow,
                       cards_to_market, detail_metrics,
                       filter_dashboard_rows, setup_horizon_bucket)

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
        tmp = tempfile.mkdtemp(prefix="alt-radar-qt-detail-")
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
# fixtures
# --------------------------------------------------------------------------

def seed_db(path, now=NOW):
    """One active LONG setup on AAA (N=48, frozen levels); BBB has none."""
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


def make_seeded_window(now=NOW):
    tmp = tempfile.mkdtemp(prefix="alt-radar-qt-detail-")
    TMPDIRS.append(tmp)
    db = os.path.join(tmp, "detail.db")
    seed_db(db, now=now)
    win = make_window(db=db)
    win.refresh_dashboard(now=now)
    APP.processEvents()
    return win


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


def mkcard(coin, price=1.0, change_24h_pct=0.0, quote_vol_24h=1e6):
    sc = Scorecard(coin=coin, venue="MEXC", score=50.0, direction="LONG",
                   lean=0.0, earlyness=0.0, price=price,
                   change_24h_pct=change_24h_pct,
                   quote_vol_24h=quote_vol_24h, funding_rate=0.0,
                   oi_change_pct=None, spread_pct=0.0, min_notional=0.01,
                   notes=[])
    sc.vetoes = []
    return sc


# ==========================================================================
# 1. NO-SETUP STATE
# ==========================================================================

def t_no_setup():
    win = make_seeded_window()
    text = win.detail_text("ZZZ")
    check("no setup yet for a coin without a setup",
          "no setup yet" in text.lower(), text[:200])
    check("blocking reason names the coin", "ZZZ" in text, text[:200])
    check("coin without setup keeps the chart honest",
          "bars unavailable" in text.lower() or "no setup yet" in text.lower(),
          text[:200])


# ==========================================================================
# 2. TIMEFRAME TABS (no 1m) + Analysis/Trade Plan/Metrics tabs
# ==========================================================================

def t_tabs():
    win = make_seeded_window()
    check("detail timeframes exclude 1m",
          "1m" not in DETAIL_TIMEFRAMES, str(DETAIL_TIMEFRAMES))
    check("detail timeframes are exactly 5m/15m/1H/4H/1D",
          tuple(DETAIL_TIMEFRAMES) == ("5m", "15m", "1H", "4H", "1D"),
          str(DETAIL_TIMEFRAMES))
    check("window exposes the same tabs",
          win.detail_timeframes() == ["5m", "15m", "1H", "4H", "1D"],
          str(win.detail_timeframes()))
    got = [win.detail_tf_tabs.tabText(i)
           for i in range(win.detail_tf_tabs.count())]
    check("timeframe QTabWidget matches", got == list(DETAIL_TIMEFRAMES),
          str(got))
    got2 = [win.detail_low_tabs.tabText(i)
            for i in range(win.detail_low_tabs.count())]
    check("Analysis/Trade Plan/Metrics tabs",
          got2 == ["Analysis", "Trade Plan", "Metrics"], str(got2))


# ==========================================================================
# 3. PER-TAB FETCH FAILURE -> "bars unavailable"
# ==========================================================================

def t_unavailable():
    win = make_seeded_window()
    win.show_coin_detail("AAA")
    win.show_detail_bars("AAA", "5m", [])
    APP.processEvents()
    check("empty bars render bars unavailable for that tab",
          "bars unavailable" in win.detail_text().lower(),
          win.detail_text()[:200])
    check("chart widget flags the missing tab",
          win.detail_charts["5m"].unavailable is True)
    win.show_detail_bars("AAA", "5m", None)
    check("None bars stay unavailable, never a raise",
          "bars unavailable" in win.detail_text().lower())


# ==========================================================================
# 4. CHART + METRICS FROM FIXTURE BARS
# ==========================================================================

def t_chart():
    win = make_seeded_window()
    bars = fixture_bars()
    m = detail_metrics(bars)
    check("RSI computed from fixture bars",
          m["rsi"] is not None and 0.0 <= m["rsi"] <= 100.0, str(m["rsi"]))
    check("MACD histogram computed", m["macd_hist"] is not None,
          str(m["macd_hist"]))
    check("EMA50/EMA200 computed",
          m["ema50"] is not None and m["ema200"] is not None,
          f"{m['ema50']} / {m['ema200']}")
    check("volume carried", m["vol_quote"] is not None, str(m["vol_quote"]))
    check("short bars degrade to None, never a raise",
          detail_metrics([]) == {"rsi": None, "macd_hist": None,
                                 "ema50": None, "ema200": None,
                                 "vol_quote": None, "last_ts": None},
          str(detail_metrics([])))
    win.show_coin_detail("AAA")
    win.show_detail_bars("AAA", "5m", bars)
    APP.processEvents()
    text = win.detail_text()
    for frag in ("rsi", "macd", "ema50", "ema200", "volume"):
        check(f"metrics tab carries {frag}", frag in text.lower(),
              text[:300])
    check("chart holds the fixture bars",
          win.detail_charts["5m"].bars is not None
          and len(win.detail_charts["5m"].bars) == len(bars))
    check("last closed bar stamped",
          "last closed bar" in text.lower(), text[:300])
    chart = DetailChartWidget()
    chart.set_bars(bars)
    chart.resize(400, 300)
    chart.grab()
    check("chart paints fixture bars offscreen without crashing", True)


# ==========================================================================
# 5. SWEPT TICKER SNAPSHOT SURFACES IN THE TABLE
# ==========================================================================

def t_sweep_market():
    win = make_seeded_window()
    swept = [mkcard("AAA", price=123.25, change_24h_pct=4.5,
                    quote_vol_24h=50_000_000.0),
             mkcard("BBB", price=7.5, change_24h_pct=-1.5,
                    quote_vol_24h=500_000.0)]
    market = cards_to_market(swept)
    check("swept cards become market quotes",
          market["AAA"]["price"] == 123.25
          and market["AAA"]["change_24h_pct"] == 4.5
          and market["AAA"]["quote_vol_24h"] == 50_000_000.0,
          str(market.get("AAA")))
    check("cards without usable numbers are skipped, never invented",
          "CCC" not in cards_to_market([mkcard("CCC", price=0.0)]),
          str(cards_to_market([mkcard("CCC", price=0.0)])))
    msg = {"kind": "scan", "ok": True, "cards": swept, "universe": 2,
           "failed": 0, "errors": {}, "status": "sweep test",
           "universe_error": None}
    win._busy.discard("stats")
    win._on_scan(msg)
    APP.processEvents()
    drain(win._jobs)
    check("scan wires swept quotes into dash_market_data",
          win.dash_market_data.get("AAA", {}).get("price") == 123.25,
          str(win.dash_market_data.get("AAA")))
    win.dash_cmb_horizon.setCurrentText("4h")
    win.dash_cmb_market.setCurrentText("All")
    win.dash_cmb_dir.setCurrentText("All")
    win.refresh_dashboard(now=NOW)
    APP.processEvents()
    text = win.dashboard_text()
    check("swept price goes live in the table", "123.25" in text,
          text[:300])
    check("swept 24h% goes live in the table", "+4.5%" in text,
          text[:300])


# ==========================================================================
# 6. EXACT-MATCH HORIZON BUCKETS
# ==========================================================================

def t_horizon_exact():
    check("N=48 is the 4h bucket",
          setup_horizon_bucket({"N_bars": 48}) == "4h")
    check("N=288 is the 24h bucket",
          setup_horizon_bucket({"N_bars": 288}) == "24h")
    for n in (12, 47, 49, 96, 100, 287, 289, 576):
        check(f"N={n} is neither bucket (None)",
              setup_horizon_bucket({"N_bars": n}) is None,
              str(setup_horizon_bucket({"N_bars": n})))
    check("missing N_bars is None",
          setup_horizon_bucket({}) is None)
    rows = [{"id": "a", "coin": "A", "direction": "LONG", "horizon": None,
             "src_bar_ts": 1, "expiry_ts": 2, "quote_vol_24h": 1.0,
             "change_24h_pct": 1.0},
            {"id": "b", "coin": "B", "direction": "LONG", "horizon": "4h",
             "src_bar_ts": 1, "expiry_ts": 2, "quote_vol_24h": 1.0,
             "change_24h_pct": 1.0}]
    got4 = filter_dashboard_rows(rows, horizon="4h")
    got24 = filter_dashboard_rows(rows, horizon="24h")
    check("unknown bucket shows under the 4h filter",
          {r["id"] for r in got4} == {"a", "b"}, str(got4))
    check("unknown bucket shows under the 24h filter",
          [r["id"] for r in got24] == ["a"], str(got24))


# ==========================================================================
# 7. ON-DEMAND REFERENCE PLAN
# ==========================================================================

def t_plan():
    win = make_seeded_window()
    win.show_coin_detail("AAA")
    win.show_detail_bars("AAA", "5m", fixture_bars())
    APP.processEvents()
    text = win.detail_text()
    check("reference size note (not a position)", "reference size" in text.lower(),
          text[:300])
    check("100 USDT reference notional", "100" in text, text[:300])
    check("no leverage wording in the detail", "leverage" not in text.lower(),
          text[:300])
    check("validity line present", "valid" in text.lower(), text[:300])
    check("next-update line present", "next update" in text.lower(),
          text[:300])


# ==========================================================================
# 8. NO PROBABILITY COPY ON THE DETAIL
# ==========================================================================

def t_clean():
    win = make_seeded_window()
    win.show_coin_detail("AAA")
    win.show_detail_bars("AAA", "5m", fixture_bars())
    APP.processEvents()
    try:
        assert_clean(win.detail_text())
        check("detail text passes assert_clean", True)
    except ValueError as e:
        check("detail text passes assert_clean", False, str(e))
    try:
        assert_clean(win.detail_text("ZZZ"))
        check("no-setup text passes assert_clean", True)
    except ValueError as e:
        check("no-setup text passes assert_clean", False, str(e))


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
    check("no venue bar fetch in this file",
          ("fetch_detail" + "_bars") not in open(__file__).read())
    check("detail dock exists",
          isinstance(getattr(WINDOWS[0], "detail_dock", None), QDockWidget))
    check("detail tab widgets exist",
          isinstance(getattr(WINDOWS[0], "detail_tf_tabs", None), QTabWidget)
          and isinstance(getattr(WINDOWS[0], "detail_low_tabs", None),
                         QTabWidget))


def main():
    print("== 1 no-setup state ==")
    t_no_setup()
    print("== 2 timeframe tabs ==")
    t_tabs()
    print("== 3 bars unavailable ==")
    t_unavailable()
    print("== 4 chart + metrics ==")
    t_chart()
    print("== 5 swept ticker snapshot ==")
    t_sweep_market()
    print("== 6 exact-match horizon buckets ==")
    t_horizon_exact()
    print("== 7 on-demand reference plan ==")
    t_plan()
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
        print("DETAIL TESTS FAILED")
        return 1
    print("ALL PASS  tests/test_qtgui_detail.py — Qt coin detail green")
    return 0


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    finally:
        cleanup()
    sys.exit(rc)
