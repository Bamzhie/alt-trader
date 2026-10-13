"""Offscreen dashboard renderer for visual verification (rebuild Task 7).

Builds the Qt terminal at exactly 1536x1024 offscreen against a temp
SQLite fixture DB (emit_setup seeds + the real journal writers — NO
network; a socket tripwire raises on any network attempt) and saves
one PNG per region plus the full window:

    region_topbar.png   region_rail.png     region_cards.png
    region_table.png    region_overview.png region_detail.png
    region_bottom.png   full_1536x1024.png

This is NOT shipped UI code — a dev-only verification helper. Inspect
the PNGs per region for overflow / clipping / contrast, fix, and
re-render until clean; record the checklist in docs/GUI_MANUAL.md.

Run:  python3 scripts/render_dashboard.py [--outdir DIR]
"""

import argparse
import os
import socket
import sqlite3
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


class NetworkTripwire(AssertionError):
    pass


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

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QGroupBox  # noqa: E402

from proto.watcher import emit_setup  # noqa: E402
from proto.watcher_journal import log_event, write_snapshot  # noqa: E402
from proto.watcher_journal import record_sweep  # noqa: E402
from qtgui import theme as _theme  # noqa: E402
from qtgui.app import RadarWindow  # noqa: E402

WIDTH, HEIGHT = 1536, 1024
NOW = 1_750_000_000

MARKET = {
    "AAA": {"name": "AAA Token", "price": 100.25, "change_24h_pct": 3.5,
            "quote_vol_24h": 50_000_000.0},
    "BBB": {"name": "BBB Token", "price": 50.125, "change_24h_pct": -2.0,
            "quote_vol_24h": 1_000_000.0},
    "BTC": {"name": "Bitcoin", "price": 97432.5, "change_24h_pct": 1.2,
            "quote_vol_24h": 900_000_000.0},
    "ETH": {"name": "Ethereum", "price": 3412.75, "change_24h_pct": -0.8,
            "quote_vol_24h": 400_000_000.0},
}


def seed_db(path, now=NOW):
    """Fixture DB through the real backend writers (offline)."""
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
        log_event(conn, "invalidation", "CCC", "CCC thesis ended",
                  ts=now - 80000)
        log_event(conn, "expiry", "DDD", "DDD horizon reached",
                  ts=now - 81000)
        record_sweep(conn, 120, 118)
        write_snapshot(conn, 110, 108, 1, 1, 0.5, ts=now - 86400)
        conn.commit()
    finally:
        conn.close()


def closes(base, n, step=0.002):
    """Deterministic fixture closes (offline, never venue data)."""
    out, px = [], base
    for _ in range(n):
        px *= 1.0 + step
        out.append(px)
    return out


def fixture_bars(base, n=60):
    """Deterministic closed-bar dicts for the detail chart seam."""
    bars, px = [], base
    ts = NOW - n * 300
    for _ in range(n):
        o, c = px, px * 1.001
        bars.append({"ts": ts, "o": o, "h": c * 1.001, "l": o * 0.999,
                     "c": c, "vol": 1e5, "amount": 1e5 * c})
        px, ts = c, ts + 300
    return bars


def stitch_horizontal(pixmaps, gap=4, name=""):
    """Stitch widget grabs side-by-side into one image."""
    widths = [p.width() for p in pixmaps]
    height = max([p.height() for p in pixmaps] + [1])
    total = sum(widths) + gap * (len(pixmaps) - 1)
    try:
        bg = QColor(_theme.SURFACE)
    except AttributeError:
        bg = QColor("#071522")
    img = QImage(total, height, QImage.Format.Format_ARGB32)
    img.fill(bg)
    p = QPainter(img)
    try:
        x = 0
        for pm in pixmaps:
            p.drawPixmap(x, 0, pm)
            x += pm.width() + gap
    finally:
        p.end()
    if not img.save(name):
        raise SystemExit(f"could not save {name}")
    return name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="/tmp/opencode/task7-renders")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    tmp = tempfile.mkdtemp(prefix="alt-radar-render-")
    db = os.path.join(tmp, "fixture.db")
    seed_db(db)

    app = QApplication.instance() or QApplication([])
    win = RadarWindow(auto_scan=False, db=db)
    win._jobs.put(None)
    win._worker.join(timeout=10)
    win._plan_jobs.put(None)
    win._plan_worker.join(timeout=10)
    win._tick_timer.stop()

    # Offline fixture seams only (never a venue fetch).
    win.dash_market_data = dict(MARKET)
    win.set_card_history({
        "Active Setups": closes(2.0, 24),
        "Eligible Perps": closes(118.0, 24, step=0.001),
        "Monitoring Coverage": closes(0.9, 24, step=0.0005),
        "New Setups 24h": closes(1.0, 24, step=-0.001),
    })
    win.set_trend_history({
        "t1-break-v1:AAA:%d" % (NOW - 3600): closes(100.0, 24),
        "cusum-raw-v1:BBB:%d" % (NOW - 7200): closes(50.0, 24,
                                                    step=-0.001),
    })
    win.set_overview_history({
        "BTC": closes(97000.0, 24, step=0.0005),
        "ETH": closes(3400.0, 24, step=-0.0005),
    })
    win.refresh_dashboard(now=NOW)
    win.refresh_dashboard_pages(now=NOW)
    # Detail chart via the fixture seam (show_detail_bars never fetches;
    # show_coin_detail would hit the venue — never call it here).
    win.show_detail_bars("AAA", "5m", fixture_bars(100.0))

    win.resize(WIDTH, HEIGHT)
    win.show()
    for _ in range(30):
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()
    print(f"window geometry: {win.width()}x{win.height()}")

    saved = {}

    def save(name, pixmap):
        path = os.path.join(args.outdir, name)
        if pixmap.isNull():
            print(f"WARN  {name}: null pixmap (widget hidden?)")
        if not pixmap.save(path):
            raise SystemExit(f"could not save {path}")
        saved[name] = (pixmap.width(), pixmap.height())
        print(f"saved {path} ({pixmap.width()}x{pixmap.height()})")

    save("region_topbar.png", win.header.grab())
    save("region_rail.png", win.rail.grab())

    tiles = [f for f in win.dashboard_dock.findChildren(QFrame, "tile")]
    print(f"cards tiles found: {len(tiles)}")
    stitch_horizontal([t.grab() for t in tiles],
                      name=os.path.join(args.outdir, "region_cards.png"))
    print("saved region_cards.png "
          f"({QImage(os.path.join(args.outdir, 'region_cards.png')).width()}"
          f"x{QImage(os.path.join(args.outdir, 'region_cards.png')).height()})")

    save("region_table.png", win.dash_table.grab())
    save("region_overview.png", win.overview_box.grab())
    save("region_detail.png", win.detail_dock.grab())

    want = ["Market Movers", "Recent Analysis", "Activity Log"]
    boxes = [b for b in win.dashboard_dock.findChildren(QGroupBox)
             if b.title() in want]
    boxes.sort(key=lambda b: want.index(b.title()))
    print(f"bottom boxes found: {[b.title() for b in boxes]}")
    stitch_horizontal([b.grab() for b in boxes],
                      name=os.path.join(args.outdir, "region_bottom.png"))

    save("full_1536x1024.png", win.grab())

    try:
        if not win._closing:
            win._on_close()
    except Exception:
        pass
    print("RENDER OK — no network touched (tripwire armed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
