"""ALT RADAR desktop GUI — PySide6 / Qt Widgets port of gui/ (tkinter).

Layout: top bar (logo · badges · scan UTC · live countdown · venue pill ·
settings gear) · left icon rail switching Scanner / Watchlist / Outcomes /
Logs / Settings · compact toolbar · signals table · vetoed section · rebuilt
detail pane · daily outcomes dock. Watchlist and outcomes use in-page tabs;
the full outcomes page separates rolling 24-hour from cumulative cohorts.
Dark navy terminal palette (qtgui/theme.py).

Reuse contract (task Q1 brief, still in force): this module contains NO
scanner logic.
  * gui/model.py     — every pure function (filters, sort, format, row data,
                       outcome summary, validation, veto_reason) is imported
                       and called, never forked.
  * gui/app.py       — Worker / PlanWorker (queue + proto threading layer)
                       and the shared display constants are imported as-is.
  * proto/*          — untouched backend, same message dicts as tkinter
                       (except the leverage-cap plumbing the Q4 brief
                       authorises: cap threads planner.compute_leverage).
  * proto/snapshot.py— close-time `.last_entries` save, identical call.

Threading contract (same as tkinter, Qt edition):
  * The MAIN thread owns every Qt widget; worker threads never touch them.
  * ONE daemon worker runs scans / collect / resolve / lookup and ALL
    SQLite (a sqlite3 connection is thread-bound); PlanWorker is a second
    daemon lane so plan fetches never queue behind a ~50s scan.
  * Worker -> UI results travel through queue.Queue, drained by a QTimer
    on the main thread (the Qt replacement for Tk's after() poll);
    UI -> worker commands travel through a second queue.

Honesty contract (Q4 brief): every control does something real; no metric
is displayed that the backend does not compute (no confidence %, no 24h
high/low, no budget mapping, no timers-per-trade).

Read-only: no order code path exists, no keys are held.
"""

import csv
import math
import os
import queue
import sqlite3
import threading
import time
from types import SimpleNamespace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush, QKeySequence, QPainter, QPixmap, \
    QShortcut, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QButtonGroup, QCheckBox, \
    QComboBox, QDockWidget, QFileDialog, QFrame, QGridLayout, QGroupBox, \
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, \
    QMainWindow, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, \
    QStatusBar, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, \
    QTextEdit, QVBoxLayout, QWidget

# --- reuse: worker layer + shared display constants (tkinter-free classes) --
from gui.app import ACTIVITY, DIR_CHOICES, POLL_MS, SIGNAL_ANCHORS, \
    SIGNAL_COLUMNS, SIGNAL_HEADINGS, SIGNAL_WIDTHS, SORT_CHOICES, \
    STATS_EVERY_S, TICK_MS, PlanWorker, Worker
# --- reuse: ALL pure display/logic functions (never forked) ---
from gui import model
from proto import indicators as indicators_mod
from proto import picks as picks_mod
from proto import report as report_mod
from proto import watcher_feed as watcher_feed_mod
from proto import store as store_mod
from proto.watcher import CUSUM_RULE_ID, KILL_HORIZONS, N_GRID, T1_RULE_ID
from proto.setup_copy import assert_clean, blocking_reason, summarize_setup
from proto.watcher_view import RESEARCH_BANNER, SILENCE_VALID, STATE_WORDS
from proto.planner import MAX_LEVERAGE, reference_plan

from . import theme as _theme

# Names stay importable for tests/callers, mirroring gui/app.py.
COLOR_ERROR = _theme.ERROR_FG

# Column indexes derived from the shared column tuple (never hard-coded).
COL_COIN = SIGNAL_COLUMNS.index("coin")

# Columns whose fresh header click opens best-first (descending); text
# columns and rank open ascending. Repeat clicks toggle (Qt default).
DESC_FIRST = {"price", "ch24", "vol24", "funding", "oi", "lean", "early",
              "score", "shadow"}

# Leverage-cap dropdown choices (planner plumbing: cap threads
# compute_leverage; default 50x = the venue maximum = today's behaviour).
LEVERAGE_CHOICES = (10, 20, 50)

# Flag-threshold presets offered next to the free-entry field (the mockup's
# "Score >= 70" becomes presets; the entry keeps arbitrary values).
THRESHOLD_PRESETS = (40, 30, 24, 15)

# Rail pages, in display order: (key, label, icon glyph).
RAIL_PAGES = (("scanner", "Scanner", "◉"), ("watchlist", "Watchlist", "★"),
              ("outcomes", "Outcomes", "▦"), ("logs", "Logs", "≡"),
              ("settings", "Settings", "⚙"))

# In-app event ring buffer size (recent-logs lists, Q4 brief: max 50).
EVENTS_MAX = 50

# 3-region grid breakpoint: below this window width the right detail
# dock collapses (the scanner page's in-page Detail group stays
# reachable as the drawer fallback); at/above it the dock is shown.
LAYOUT_NARROW_PX = 1100

# Watcher setup dock columns (watcher UI Task 5): setup id + rule/coin/dir +
# the STATE_WORDS verb verbatim + the entry/stop/tgt trio. Header text
# matches the TUI watcher screen columns one-for-one (parity test pins
# this); state cells are never reworded (the shared view-model owns them).
WATCHER_COLUMNS = ("id", "rule", "coin", "dir", "state", "entry", "stop",
                   "tgt")
WATCHER_HEADINGS = {"id": "ID", "rule": "RULE", "coin": "COIN", "dir": "DIR",
                    "state": "STATE", "entry": "ENTRY", "stop": "STOP",
                    "tgt": "TGT"}
WATCHER_WIDTHS = {"id": 170, "rule": 120, "coin": 90, "dir": 60,
                  "state": 110, "entry": 100, "stop": 100, "tgt": 100}


# ---- dashboard (Task 5: stat cards + Top Signals table, additive) ----
# Filter vocab (spec section 3): direction All/Long/Short, market All /
# Top 20 by 24h quote volume / Tail, horizon 4h (N=48) / 24h (N=288) with
# a 4h default, sort newest (default) / expiry / 24h%.
DASH_DIRS = ("All", "Long", "Short")
DASH_MARKETS = ("All", "Top 20", "Tail")
DASH_HORIZONS = ("4h", "24h")
DASH_DEFAULT_HORIZON = "4h"
DASH_SORTS = ("Newest", "Expiry", "24h%")

# Top Signals columns: one row per active setup. Deliberately NO tier or
# score column (spec 2089540 removed them); direction rides a LONG/SHORT
# pill, analysis is the 2-line rule-evidence copy, evidence names the
# matched rule evidence, expiry is a countdown.
DASH_TABLE_COLUMNS = ("coin", "price", "ch24", "analysis", "evidence",
                      "dir", "expiry")
DASH_TABLE_HEADINGS = ("COIN", "PRICE", "24H%", "ANALYSIS", "EVIDENCE",
                       "DIR", "EXPIRY")
DASH_TABLE_WIDTHS = {"coin": 90, "price": 90, "ch24": 70, "analysis": 320,
                     "evidence": 220, "dir": 80, "expiry": 110}

# The watcher geometry grid runs on 5-minute bars; a setup's expiry stamp
# is src_bar_ts + N_bars * 300s (logged row values only, never inferred).
DASH_BAR_S = 300
DASH_DAY_S = 86400

# dashboard_snapshot columns, in DDL order.
_DASH_SNAP_COLS = ("ts", "eligible_count", "evaluated_count",
                   "active_setups", "new_setups_24h",
                   "monitoring_coverage")

# watcher_events kinds (Task 2 journal); the dashboard reads the journal
# read-only for the activity feed.
_DASH_EVENT_KINDS = ("emission", "invalidation", "expiry",
                     "sweep-complete")

# Withheld-comparison copy (spec section 1: no prior snapshot covering
# now-24h -> show unavailable, never an inferred value).
DASH_NO_COMPARISON = "comparison unavailable"


def _dash_open_ro(db_path):
    """Read-only sqlite handle, or None when the DB cannot be opened."""
    if not db_path or not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True,
                               timeout=2.0)
        conn.execute("SELECT 1")
        return conn
    except Exception:
        return None


def dashboard_cut_ts(db_path):
    """Active-epoch cut_ts for read-time cohort filtering, or None.

    None before any operator cut (no filtering) and on any unreadable
    DB — callers degrade to unfiltered reads, never a raise.
    """
    conn = _dash_open_ro(db_path)
    if conn is None:
        return None
    try:
        try:
            epoch = store_mod.active_epoch(conn)
        except Exception:
            return None
        if not epoch:
            return None
        return epoch.get("cut_ts")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _dash_post_cut(rows, cut_ts):
    """Keep rows with ts >= cut_ts (Task 2 carry: journal/snapshot reads
    are filtered at read time so pre-cut and post-cut cohorts never
    pool). No active cut -> rows unchanged."""
    if cut_ts is None:
        return list(rows)
    try:
        cut = int(cut_ts)
    except (TypeError, ValueError):
        return list(rows)
    return [r for r in rows if r.get("ts") is not None
            and int(r["ts"]) >= cut]


def dashboard_snapshot_pair(db_path, now=None):
    """(latest, prior_or_None) from dashboard_snapshot, post-cut only.

    Same pair rule as proto/watcher_journal.snapshot_pair (prior = the
    nearest snapshot at or before now-24h, excluding the latest row) but
    read-only (never creates tables) and filtered to ts >= the active
    epoch's cut_ts at read time. (None, None) on a fresh/missing DB.
    """
    now = int(time.time() if now is None else now)
    conn = _dash_open_ro(db_path)
    if conn is None:
        return (None, None)
    try:
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        except Exception:
            return (None, None)
        if "dashboard_snapshot" not in tables:
            return (None, None)
        cut_ts = dashboard_cut_ts(db_path)
        try:
            cur = conn.execute(
                f"SELECT {', '.join(_DASH_SNAP_COLS)} FROM dashboard_snapshot"
                " ORDER BY ts ASC, rowid ASC")
            rows = [dict(zip(_DASH_SNAP_COLS, r)) for r in cur.fetchall()]
        except Exception:
            return (None, None)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    rows = _dash_post_cut(rows, cut_ts)
    if not rows:
        return (None, None)
    latest = rows[-1]
    prior = None
    for row in rows[:-1]:
        if int(row["ts"]) <= now - DASH_DAY_S:
            prior = row
    return (latest, prior)


def dashboard_recent_events(db_path, limit=50):
    """Newest-first watcher_events rows, post-cut only (read-only).

    Shared read path for the activity feed: journal rows filtered by ts
    vs the active cut at read time, newest first, capped at *limit*.
    [] on a fresh/missing DB — never a raise.
    """
    conn = _dash_open_ro(db_path)
    if conn is None:
        return []
    try:
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        except Exception:
            return []
        if "watcher_events" not in tables:
            return []
        cut_ts = dashboard_cut_ts(db_path)
        try:
            cur = conn.execute(
                "SELECT ts, kind, coin, text FROM watcher_events"
                " ORDER BY ts DESC, rowid DESC")
            rows = [{"ts": r[0], "kind": r[1], "coin": r[2], "text": r[3]}
                    for r in cur.fetchall()
                    if r[1] in _DASH_EVENT_KINDS]
        except Exception:
            return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    rows = _dash_post_cut(rows, cut_ts)
    rows.sort(key=lambda r: int(r["ts"]), reverse=True)
    return rows[:max(0, int(limit))]


def dashboard_setup_meta(db_path):
    """{setup id: {N_bars, src_bar_ts}} from watcher_setup (read-only).

    The shared panel detail dicts do not carry the geometry-grid N_bars
    the horizon filter needs, so the dashboard reads those two columns
    directly (SELECT only, never a write). {} on a fresh/missing DB —
    never a raise; rows then render unknown-bucket/expiry-unavailable
    copy instead of vanishing.
    """
    conn = _dash_open_ro(db_path)
    if conn is None:
        return {}
    try:
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        except Exception:
            return {}
        if "watcher_setup" not in tables:
            return {}
        try:
            cols = {r[1] for r in
                    conn.execute("PRAGMA table_info(watcher_setup)")}
        except Exception:
            return {}
        if not {"id", "N_bars", "src_bar_ts"} <= cols:
            return {}
        try:
            cur = conn.execute(
                "SELECT id, N_bars, src_bar_ts FROM watcher_setup")
            return {r[0]: {"N_bars": r[1], "src_bar_ts": r[2]}
                    for r in cur.fetchall()}
        except Exception:
            return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _dash_delta(cur, old):
    """Signed day-over-day delta text, or DASH_NO_COMPARISON."""
    if cur is None or old is None:
        return DASH_NO_COMPARISON
    diff = cur - old
    if diff == 0:
        return "no change vs prior day"
    sign = "+" if diff > 0 else ""
    if isinstance(cur, float) or isinstance(old, float):
        return f"{sign}{diff:.1f} pts vs prior day"
    return f"{sign}{diff:d} vs prior day"


def format_dashboard_cards(latest, prior):
    """Four (title, value, sub) stat cards from a snapshot pair.

    Eligible vs evaluated counts are shown separately (never called
    "scanned"). Every sub is either a day-over-day delta or
    DASH_NO_COMPARISON — never an inferred value. All strings pass
    proto/setup_copy.assert_clean (fail-closed: raises on banned
    wording instead of rendering it).
    """
    if latest is None:
        cards = [("Active Setups", "—", DASH_NO_COMPARISON),
                 ("Eligible Perps", "—", DASH_NO_COMPARISON),
                 ("Monitoring Coverage", "unavailable",
                  DASH_NO_COMPARISON),
                 ("New Setups 24h", "—", DASH_NO_COMPARISON)]
    else:
        cov = latest.get("monitoring_coverage")
        cov_text = f"{cov * 100.0:.1f}%" if cov is not None \
            else "unavailable"
        old_cov = prior.get("monitoring_coverage") if prior else None
        cards = [
            ("Active Setups", str(latest.get("active_setups", "—")),
             _dash_delta(latest.get("active_setups"),
                         prior.get("active_setups") if prior else None)),
            ("Eligible Perps",
             f"eligible {latest.get('eligible_count', '—')} · "
             f"evaluated {latest.get('evaluated_count', '—')}",
             _dash_delta(latest.get("eligible_count"),
                         prior.get("eligible_count") if prior else None)),
            ("Monitoring Coverage", cov_text,
             _dash_delta(cov, old_cov)
             if cov is not None and old_cov is not None
             else DASH_NO_COMPARISON),
            ("New Setups 24h", str(latest.get("new_setups_24h", "—")),
             _dash_delta(latest.get("new_setups_24h"),
                         prior.get("new_setups_24h") if prior else None)),
        ]
    for title, value, sub in cards:
        assert_clean(title)
        assert_clean(value)
        assert_clean(sub)
    return cards


def setup_horizon_bucket(setup):
    """Exact-match horizon bucket from the geometry grid: 48->'4h',
    288->'24h', anything else->None.

    Unknown-bucket rows are shown under both horizon filters (an
    unclassifiable active setup is never hidden by a filter).
    """
    try:
        n = setup.get("N_bars")
    except AttributeError:
        return None
    if n is None:
        return None
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    if n == 48:
        return "4h"
    if n == 288:
        return "24h"
    return None


def setup_expiry_ts(setup):
    """Expiry stamp from logged row values (src_bar_ts + N_bars*300s)."""
    try:
        src = setup.get("src_bar_ts")
        n = setup.get("N_bars")
    except AttributeError:
        return None
    if src is None or n is None:
        return None
    try:
        return int(src) + int(n) * DASH_BAR_S
    except (TypeError, ValueError):
        return None


def _dash_duration(seconds):
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_expiry(expiry_ts, now=None):
    """Countdown text for an expiry stamp ('3h 12m left' / 'expired…')."""
    if expiry_ts is None:
        return "expiry unavailable"
    now = int(time.time() if now is None else now)
    delta = int(expiry_ts) - now
    if delta < 0:
        return f"expired {_dash_duration(-delta)} ago"
    return f"{_dash_duration(delta)} left"


def build_dashboard_rows(panel, market=None, now=None, meta=None):
    """One row-dict per ACTIVE setup, in panel order.

    *panel* is a proto/watcher_view.WatcherPanel (or None -> []). *market*
    maps coin -> {name, price, change_24h_pct, quote_vol_24h} (offline
    quotes; absent coins render n/a, never an invented quote). *meta* maps
    setup id -> {N_bars, src_bar_ts} (see dashboard_setup_meta): the
    shared detail dicts do not carry N_bars, which the horizon bucket and
    expiry countdown need; without it those cells render honest
    unknown-bucket/expiry-unavailable copy. Analysis lines come from
    proto/setup_copy.summarize_setup (rule criteria + computed inputs
    only); the evidence cell cites the rule, direction, STATE_WORDS verb
    verbatim, and any missing-input/monitoring notes.
    """
    market = market or {}
    meta = meta or {}
    now = int(time.time() if now is None else now)
    if panel is None:
        return []
    by_id = getattr(panel, "by_id", None) or {}
    groups = getattr(panel, "groups", None) or {}
    rows = []
    for sid in list(groups.get("active", [])):
        detail = by_id.get(sid) or {}
        coin = detail.get("coin", "?")
        quote = market.get(coin) or {}
        setup = {"rule_id": detail.get("rule", "?"),
                 "direction": detail.get("direction", "?"),
                 "criteria": detail.get("criteria", []),
                 "confirmations": detail.get("confirmations", [])}
        inputs = {}
        if detail.get("monitoring") is not None:
            inputs["monitoring"] = detail["monitoring"]
        for missing in detail.get("missing") or []:
            inputs[str(missing)] = None
        stamp = detail.get("last_bar_ts", detail.get("src_bar_ts"))
        if stamp is not None:
            inputs["data_as_of"] = stamp
        line1, line2 = summarize_setup(setup, inputs)
        state = detail.get("state", STATE_WORDS["active"])
        evidence = f"{state} · {setup['rule_id']} {setup['direction']}"
        missing = detail.get("missing") or []
        if missing:
            evidence += f"; missing {', '.join(str(m) for m in missing)}"
        if detail.get("monitoring") == "unavailable":
            evidence += "; monitoring unavailable"
        hint = (meta.get(sid) or {}) if isinstance(meta, dict) else {}
        n_bars = detail.get("N_bars")
        if n_bars is None:
            n_bars = hint.get("N_bars")
        src_ts = detail.get("src_bar_ts")
        if src_ts is None:
            src_ts = hint.get("src_bar_ts")
        expiry_ts = setup_expiry_ts({"src_bar_ts": src_ts,
                                     "N_bars": n_bars})
        horizon = setup_horizon_bucket({"N_bars": n_bars})
        row = {"id": sid, "coin": coin,
               "name": quote.get("name", coin),
               "direction": str(setup["direction"]).upper(),
               "price": quote.get("price"),
               "change_24h_pct": quote.get("change_24h_pct"),
               "quote_vol_24h": quote.get("quote_vol_24h"),
               "analysis": f"{line1}\n{line2}", "evidence": evidence,
               "expiry_ts": expiry_ts,
               "expiry": format_expiry(expiry_ts, now=now),
               "horizon": horizon,
               "src_bar_ts": detail.get("src_bar_ts") or 0}
        assert_clean(row["analysis"])
        assert_clean(row["evidence"])
        assert_clean(row["expiry"])
        rows.append(row)
    return rows


def filter_dashboard_rows(rows, dir="All", market="All", horizon="4h",
                           sort="Newest"):
    """Apply the dashboard filters (pure; returns a new list)."""
    out = [r for r in (rows or [])
           if dir == "All" or r.get("direction") == dir.upper()]
    ranked = sorted(out, key=lambda r: (r.get("quote_vol_24h") is None,
                                        -(r.get("quote_vol_24h") or 0.0)))
    if market == "Top 20":
        out = ranked[:20]
    elif market == "Tail":
        out = ranked[20:]
    else:
        out = list(out)
    if horizon in DASH_HORIZONS:
        out = [r for r in out
               if r.get("horizon") in (None, horizon)]
    if sort == "Expiry":
        out.sort(key=lambda r: (r.get("expiry_ts") is None,
                                r.get("expiry_ts") or 0))
    elif sort == "24h%":
        out.sort(key=lambda r: (r.get("change_24h_pct") is None,
                                -(r.get("change_24h_pct") or 0.0)))
    else:  # "Newest" (default): newest setup first.
        out.sort(key=lambda r: -(r.get("src_bar_ts") or 0))
    return out


def cards_to_market(cards):
    """Sweep Scorecards -> dash_market_data quotes (real venue numbers).

    Each card already carries the sweep's ticker snapshot (price from
    lastPrice, 24h% from riseFallRate, quote volume from amount24), so
    the dashboard table/filter/sort go live with venue numbers. Cards
    without a usable positive price are skipped; unusable 24h%/volume
    degrade to None (rendered n/a) — quotes are never invented.
    """
    market = {}
    for c in cards or []:
        coin = getattr(c, "coin", None)
        if not coin:
            continue
        try:
            price = float(getattr(c, "price", None))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or price <= 0:
            continue
        try:
            chg = float(getattr(c, "change_24h_pct", None))
            if not math.isfinite(chg):
                chg = None
        except (TypeError, ValueError):
            chg = None
        try:
            vol = float(getattr(c, "quote_vol_24h", None))
            if not math.isfinite(vol):
                vol = None
        except (TypeError, ValueError):
            vol = None
        name = getattr(c, "name", None) or str(coin)
        market[str(coin)] = {"name": name, "price": price,
                             "change_24h_pct": chg, "quote_vol_24h": vol}
    return market


# ---- coin detail (Task 6: timeframe chart + evidence tabs, additive) ----
# Bar intervals offered on the chart. 1m is unsupported by the venue
# adapter (proto/mexc.INTERVALS has no 1m key), so it is never a tab.
DETAIL_TIMEFRAMES = ("5m", "15m", "1H", "4H", "1D")
DETAIL_BAR_LIMIT = 200
DETAIL_NO_BARS = "bars unavailable"


def fetch_detail_bars(symbol, interval, limit=DETAIL_BAR_LIMIT):
    """Live klines for the detail chart (app code paths ONLY, never tests).

    Closed bars only — proto/mexc.klines already drops the still-forming
    bar, so the chart's right edge is the last closed bar.
    """
    from proto import mexc as mexc_mod
    return mexc_mod.klines(symbol, interval, limit=limit)


def detail_metrics(bars):
    """RSI/MACD/EMA50/EMA200/quote-volume from closed bars (pure).

    Returns None fields when bars are missing or too short — callers
    render honest unavailable copy, never a raise, never a guess.
    """
    empty = {"rsi": None, "macd_hist": None, "ema50": None,
             "ema200": None, "vol_quote": None, "last_ts": None}
    if not bars:
        return dict(empty)
    try:
        closes = indicators_mod.closes(bars)
        e50 = indicators_mod.ema(closes, 50)
        e200 = indicators_mod.ema(closes, 200)
        vols = indicators_mod.quote_volume(bars)
        return {"rsi": indicators_mod.rsi(bars),
                "macd_hist": indicators_mod.macd_histogram(bars),
                "ema50": e50[-1] if e50 else None,
                "ema200": e200[-1] if e200 else None,
                "vol_quote": vols[-1] if vols else None,
                "last_ts": bars[-1].get("ts")}
    except Exception:
        return dict(empty)


def format_detail_metrics(metrics):
    """Metrics-tab lines from detail_metrics() (all assert_clean)."""
    metrics = metrics or {}
    lines = []
    rsi = metrics.get("rsi")
    lines.append(f"RSI(14) {rsi:.1f}" if rsi is not None
                 else "RSI unavailable — not enough closed bars")
    macd = metrics.get("macd_hist")
    lines.append(f"MACD histogram {macd:+.3f}" if macd is not None
                 else "MACD unavailable — not enough closed bars")
    e50 = metrics.get("ema50")
    lines.append(f"EMA50 {e50:.8g}" if e50 is not None
                 else "EMA50 unavailable — not enough closed bars")
    e200 = metrics.get("ema200")
    lines.append(f"EMA200 {e200:.8g}" if e200 is not None
                 else "EMA200 unavailable — not enough closed bars")
    vol = metrics.get("vol_quote")
    lines.append(f"volume (quote) {vol:.8g}" if vol is not None
                 else "volume unavailable — no closed bars")
    last_ts = metrics.get("last_ts")
    lines.append(f"last closed bar {last_ts}" if last_ts is not None
                 else "last closed bar unavailable")
    for line in lines:
        assert_clean(line)
    return lines


def detail_setup_for_coin(panel, coin):
    """(setup id, detail dict) of the ACTIVE setup for *coin*, or (None,
    None). Selection is by coin over the panel's active group."""
    if panel is None or coin is None:
        return (None, None)
    by_id = getattr(panel, "by_id", None) or {}
    groups = getattr(panel, "groups", None) or {}
    for sid in groups.get("active", []):
        detail = by_id.get(sid) or {}
        if detail.get("coin") == coin:
            return (sid, detail)
    return (None, None)


def detail_blocking_ctx(panel, coin):
    """blocking_reason() ctx for a coin with no active setup (pure).

    Only stamps the last-evaluation time from the panel (last eval bar,
    else the panel as-of stamp); every other branch of blocking_reason
    keeps its honest default. Never raises.
    """
    ctx = {}
    try:
        if panel is not None:
            last = getattr(panel, "last_bar_ts", None)
            if last is None:
                last = getattr(panel, "asof_ts", None)
            if last:
                ctx["last_eval_ts"] = last
    except Exception:
        pass
    return ctx


def detail_reference_plan_text(detail):
    """On-demand reference-plan lines from a setup's frozen levels.

    Entry/stop/target are the logged row values only; the notional is
    the fixed 100 USDT reference (reference size, not a position). No
    leverage, no liquidation claims — planner.reference_plan carries
    neither. Missing levels render honest unavailable copy.
    """
    lines = []
    try:
        entry = float((detail or {}).get("entry"))
        stop = float((detail or {}).get("stop_px"))
        tgt = float((detail or {}).get("tgt_px"))
    except (TypeError, ValueError):
        entry = stop = tgt = None
    if entry is None or stop is None or tgt is None:
        lines = ["reference plan unavailable — "
                 "frozen entry/stop/target incomplete"]
    else:
        plan = reference_plan({"entry_low": entry, "entry_high": entry,
                               "stop": stop, "tp1": tgt, "tp2": tgt})
        lines = ["reference size, not a position",
                 f"reference notional {plan['notional']:.0f} USDT",
                 f"entry {entry:.8g}",
                 f"stop {stop:.8g}",
                 f"target {tgt:.8g}",
                 f"reference stop-risk {plan['stop_risk']:.2f} USDT"]
    for line in lines:
        assert_clean(line)
    return "\n".join(lines)


def watcher_correlated_lines(db_path):
    """CORRELATED OPENS section lines for the Qt watcher detail pane.

    Task 6: thin pointer to the ONE shared helper
    (``proto.watcher_feed.correlated_lines``), which every watcher
    surface renders verbatim — TUI table + detail, tkinter detail, and
    this dock. Shape/wording stay byte-identical to
    ``format_watcher_report``'s CORRELATED OPENS section. A
    missing/locked DB yields [] (no section), never a raise.
    """
    return watcher_feed_mod.correlated_lines(db_path)

_QT_ANCHOR = {
    "center": Qt.AlignmentFlag.AlignCenter,
    "w": Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
}


def _align(anchor):
    """SIGNAL_ANCHORS tkinter anchor -> Qt alignment (shared table config)."""
    return _QT_ANCHOR.get(anchor,
                          Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


def _num(value):
    """Numeric sort payload via the shared parser (None = untrusted text)."""
    return model._to_float(value)


def _logo_pixmap(size=26):
    """Radar logo mark: concentric rings + sweep dot, accent on transparent."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor(_theme.ACCENT))
        half = size // 2
        for r in (half - 2, half - 7, half - 12):
            if r > 0:
                p.drawEllipse(half - r, half - r, 2 * r, 2 * r)
        p.drawEllipse(half + 4, half - 7, 4, 4)   # the blip
    finally:
        p.end()
    return pm


class _CellItem(QTableWidgetItem):
    """Table cell carrying its numeric sort value + row tint colors.

    Header clicks sort through __lt__, so numeric columns compare as
    numbers ("400.0" never loses to "90.0" as text). Background/foreground
    come from the shared row-tag rule (model.row_tags -> qtgui.theme).
    """

    def __init__(self, text, number=None, align=None, background=None,
                 foreground=None):
        super().__init__(str(text))
        self._number = number
        self.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        if align is not None:
            self.setTextAlignment(align)
        if background:
            self.setBackground(QBrush(QColor(background)))
        if foreground:
            self.setForeground(QBrush(QColor(foreground)))

    def __lt__(self, other):
        a = getattr(self, "_number", None)
        b = getattr(other, "_number", None)
        if a is not None and b is not None:
            return a < b
        if a is not None:
            return True        # real numbers sort before "n/a" cells
        if b is not None:
            return False
        return self.text().casefold() < str(other.text()).casefold()


class DetailChartWidget(QWidget):
    """Candlestick chart with EMA50/200 overlays + volume band (Task 6).

    Bars are closed-bar dicts ({ts,o,h,l,c,vol,amount}, oldest first);
    tests feed deterministic fixtures, the app feeds live mexc.klines.
    Empty/None bars set the unavailable flag and paint the honest
    "bars unavailable" copy for that tab — never a raise, never a gap
    drawn as data.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bars = None
        self.unavailable = True
        self.setMinimumHeight(180)

    def set_bars(self, bars):
        if bars:
            self.bars = list(bars)
            self.unavailable = False
        else:
            self.bars = None
            self.unavailable = True
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), QColor(_theme.SURFACE))
            if self.unavailable or not self.bars:
                p.setPen(QColor(_theme.TEXT_DIM))
                p.drawText(self.rect(),
                           Qt.AlignmentFlag.AlignCenter, DETAIL_NO_BARS)
                return
            bars = self.bars
            n = len(bars)
            w, h = max(1, self.width()), max(1, self.height())
            pad = 6
            price_h = int((h - 2 * pad) * 0.72)
            vol_top = pad + price_h + 4
            vol_h = max(8, h - vol_top - pad)
            plot_w = max(1, w - 2 * pad)
            closes = [b["c"] for b in bars]
            e50 = indicators_mod.ema(closes, 50)
            e200 = indicators_mod.ema(closes, 200)
            lo = min([b["l"] for b in bars]
                     + ([e for e in (e50 + e200) if e] or [min(closes)]))
            hi = max([b["h"] for b in bars]
                     + ([e for e in (e50 + e200) if e] or [max(closes)]))
            span = (hi - lo) or 1.0
            vmax = max([float(b.get("amount") or 0.0) for b in bars]
                       + [1.0])

            def px(i):
                return pad + plot_w * (i + 0.5) / n

            def py(v):
                return pad + price_h * (1.0 - (v - lo) / span)

            cw = max(1.0, plot_w / n * 0.6)
            for i, b in enumerate(bars):
                up = b["c"] >= b["o"]
                col = QColor(_theme.LONG_FG if up else _theme.SHORT_FG)
                p.setPen(col)
                x = px(i)
                p.drawLine(int(x), int(py(b["h"])), int(x), int(py(b["l"])))
                top, bot = int(py(max(b["o"], b["c"]))), int(
                    py(min(b["o"], b["c"])))
                p.fillRect(int(x - cw / 2), top, int(cw),
                           max(1, bot - top), col)
                v = float(b.get("amount") or 0.0)
                vh = vol_h * v / vmax
                p.fillRect(int(x - cw / 2), int(vol_top + vol_h - vh),
                           int(cw), max(1, int(vh)),
                           QColor(_theme.TEXT_DIM))

            def poly(series, color):
                if not series:
                    return
                off = n - len(series)
                p.setPen(QColor(color))
                prev = None
                for j, v in enumerate(series):
                    pt = (int(px(off + j)), int(py(v)))
                    if prev is not None:
                        p.drawLine(prev[0], prev[1], pt[0], pt[1])
                    prev = pt

            poly(e50, _theme.ACCENT)
            poly(e200, _theme.INFO_FG)
        finally:
            p.end()


# ---- dashboard pages (Task 7: bottom row + six pages, additive) ----
# Six dashboard pages (the Dashboard dock from Task 5 is the first page;
# the other five live as tabs in their own dock so the scanner rail,
# tables and docks above stay byte-for-byte untouched). Nothing here
# takes a stake or consults is_actionable: the stakeless model (spec
# section 2) means stake plays no role on any of these surfaces.
DASH_PAGE_TITLES = ("Dashboard", "Watchlist", "Signals", "Analysis",
                    "Trades", "Settings")

# Trades is a hypothetical-only ledger (spec section 5). The header says
# so verbatim; note the wording deliberately avoids the "P&L" token and
# every BANNED_WORDS entry (assert_clean enforced in tests).
TRADES_HEADER = "No orders placed — hypothetical outcomes only"
TRADES_SUB = ("Read-only review of setups with reference plans at 100 USDT"
              " (reference size, not a position). No orders are placed here.")

# Meta keys for dashboard-owned UI state (starred coins + display prefs).
# Pause/disable reuse the watcher_feed writers + keys (watcher_paused /
# watcher_disabled:<rule>) so Settings writes the same flags panels read.
DASH_STAR_PREFIX = "dash_star:"
DASH_PREF_AUTOREFRESH = "dash_pref_autorefresh"
DASH_PREF_COMPACT = "dash_pref_compact"

# Trigger switches offered on the dashboard Settings page: the two known
# watcher rules plus any rule_id observed in the setups table.
DASH_KNOWN_RULES = (T1_RULE_ID, CUSUM_RULE_ID)

DASH_MOVERS_N = 3
DASH_RECENT_N = 5
DASH_ACTIVITY_N = 12

DASH_NO_QUOTES = "market quotes unavailable — no scan yet"
DASH_NO_RECENT = "no recent analysis yet"
DASH_NO_ACTIVITY = "no activity yet"
DASH_NO_STARS = "no starred coins yet — add one below"

STAR_TABLE_COLUMNS = ("coin", "name", "price", "ch24", "setup")
STAR_TABLE_HEADINGS = {"coin": "COIN", "name": "NAME", "price": "PRICE",
                       "ch24": "24H%", "setup": "SETUP"}
STAR_TABLE_WIDTHS = {"coin": 80, "name": 140, "price": 90, "ch24": 70,
                     "setup": 220}

SIG_TABLE_COLUMNS = ("coin", "rule", "dir", "state", "src")
SIG_TABLE_HEADINGS = {"coin": "COIN", "rule": "RULE", "dir": "DIR",
                      "state": "STATE", "src": "SRC BAR"}
SIG_TABLE_WIDTHS = {"coin": 80, "rule": 130, "dir": 80, "state": 110,
                    "src": 130}
SIG_DIRS = ("All", "Long", "Short")

BOARD_TABLE_COLUMNS = ("setup", "evalbar", "mon", "gross", "net", "note")
BOARD_TABLE_HEADINGS = {"setup": "SETUP", "evalbar": "EVAL BAR",
                        "mon": "MONITORING", "gross": "GROSS",
                        "net": "NET", "note": "NOTE"}
BOARD_TABLE_WIDTHS = {"setup": 150, "evalbar": 110, "mon": 110,
                      "gross": 80, "net": 80, "note": 220}

TRADES_TABLE_COLUMNS = ("coin", "dir", "entry", "stop", "target", "risk",
                        "ret", "state")
TRADES_TABLE_HEADINGS = {"coin": "COIN", "dir": "DIR", "entry": "ENTRY",
                         "stop": "STOP", "target": "TARGET",
                         "risk": "STOP-RISK 100U", "ret": "LATEST RETURN",
                         "state": "STATE"}
TRADES_TABLE_WIDTHS = {"coin": 80, "dir": 80, "entry": 90, "stop": 90,
                       "target": 90, "risk": 110, "ret": 130,
                       "state": 100}


def _dash_vol_text(vol):
    """Quote-volume cell text (venue number or n/a — never invented)."""
    try:
        v = float(vol)
    except (TypeError, ValueError):
        return "n/a"
    if v != v or v < 0:
        return "n/a"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v / 1e3:.0f}K"


def _dash_ret_text(value):
    """Fractional horizon return as percent, or pending when absent."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "pending"
    if v != v:
        return "pending"
    return f"{v * 100.0:+.2f}%"


def dashboard_movers(market):
    """Split offline quotes into gainers/losers/high-volume (pure).

    *market* maps coin -> {name, price, change_24h_pct, quote_vol_24h}.
    Returns {"gainers", "losers", "high_volume"} lists of
    (coin, text) capped at DASH_MOVERS_N; coins missing the ranked
    field are skipped, never invented.
    """
    quotes = [(coin, q or {}) for coin, q in (market or {}).items()]
    with_chg = [(c, q) for c, q in quotes
                if isinstance(q.get("change_24h_pct"), (int, float))]
    with_vol = [(c, q) for c, q in quotes
                if isinstance(q.get("quote_vol_24h"), (int, float))]
    with_chg.sort(key=lambda kv: kv[1]["change_24h_pct"], reverse=True)
    with_vol.sort(key=lambda kv: kv[1]["quote_vol_24h"], reverse=True)
    gainers = [(c, f"{q['change_24h_pct']:+.1f}%") for c, q in with_chg]
    losers = list(reversed(gainers))
    vols = [(c, _dash_vol_text(q["quote_vol_24h"])) for c, q in with_vol]
    return {"gainers": gainers[:DASH_MOVERS_N],
            "losers": losers[:DASH_MOVERS_N],
            "high_volume": vols[:DASH_MOVERS_N]}


def dashboard_all_setups(db_path):
    """Every watcher_setup row, newest-first (read-only, never raises).

    The full ledger the Signals page renders: active rows plus history
    (invalidated/expired/resolved). [] on a fresh/missing DB.
    """
    conn = _dash_open_ro(db_path)
    if conn is None:
        return []
    try:
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        except Exception:
            return []
        if "watcher_setup" not in tables:
            return []
        try:
            cols = [r[1] for r in
                    conn.execute("PRAGMA table_info(watcher_setup)")]
        except Exception:
            return []
        want = [c for c in ("id", "rule_id", "coin", "direction",
                            "src_bar_ts", "entry", "stop_px", "tgt_px",
                            "N_bars", "state", "monitoring", "created_at")
                if c in cols]
        if not want or "id" not in want:
            return []
        try:
            cur = conn.execute(
                f"SELECT {', '.join(want)} FROM watcher_setup"
                " ORDER BY src_bar_ts DESC, rowid DESC")
            return [dict(zip(want, r)) for r in cur.fetchall()]
        except Exception:
            return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def dashboard_latest_evals(db_path):
    """Latest watcher_eval row per setup id (read-only, never raises)."""
    conn = _dash_open_ro(db_path)
    if conn is None:
        return {}
    try:
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        except Exception:
            return {}
        if "watcher_eval" not in tables:
            return {}
        try:
            cur = conn.execute(
                "SELECT setup_id, bar_ts, state, monitoring, ret_gross,"
                " ret_net, note FROM watcher_eval ORDER BY bar_ts")
            latest = {}
            for r in cur.fetchall():
                latest[r[0]] = {"setup_id": r[0], "bar_ts": r[1],
                                "state": r[2], "monitoring": r[3],
                                "ret_gross": r[4], "ret_net": r[5],
                                "note": r[6]}
            return latest
        except Exception:
            return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def dashboard_starred(db_path):
    """Starred coins from meta (read-only, never raises)."""
    conn = _dash_open_ro(db_path)
    if conn is None:
        return set()
    try:
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        except Exception:
            return set()
        if "meta" not in tables:
            return set()
        try:
            cur = conn.execute("SELECT key, value FROM meta WHERE key LIKE ?",
                               (DASH_STAR_PREFIX + "%",))
            return {key[len(DASH_STAR_PREFIX):] for key, value in
                    cur.fetchall()
                    if str(value).strip().lower()
                    in ("1", "true", "yes", "on")}
        except Exception:
            return set()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def dash_write_meta(db_path, key, value):
    """Contained meta write for dashboard-owned UI state (stars/prefs).

    Pause/disable go through the watcher_feed writers instead (same
    keys the panels read). Returns True on success, False when the DB
    is missing/locked — callers degrade to an honest note, never raise.
    """
    if not db_path:
        return False
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
    except Exception:
        return False
    try:
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY"
                         " KEY, value TEXT)")
            conn.execute("INSERT OR REPLACE INTO meta (key, value)"
                         " VALUES (?, ?)", (key, value))
            conn.commit()
            return True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def dash_read_pref(db_path, key):
    """Contained meta-pref read (False on a fresh/missing DB)."""
    conn = _dash_open_ro(db_path)
    if conn is None:
        return False
    try:
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        except Exception:
            return False
        if "meta" not in tables:
            return False
        try:
            row = conn.execute("SELECT value FROM meta WHERE key=?",
                               (key,)).fetchone()
        except Exception:
            return False
        return bool(row) and str(row[0]).strip().lower() in (
            "1", "true", "yes", "on")
    finally:
        try:
            conn.close()
        except Exception:
            pass


class RadarWindow(QMainWindow):
    """The ALT RADAR desktop front end. Read-only — no orders, no keys."""

    def __init__(self, stake=0.10, coins=150, interval=60,
                 db="data/signals.db", log_threshold=24.0, auto_scan=True,
                 parent=None):
        super().__init__(parent)
        # Make sure any window built through this ctor carries the theme,
        # even when a caller (test harness) forgot apply_theme().
        try:
            app = QApplication.instance()
            if app is not None and not _theme.is_applied(app):
                _theme.apply_theme(app)
        except Exception:
            pass

        self.setWindowTitle("ALT RADAR — READ-ONLY MEXC scanner")
        self.resize(1440, 920)
        # Keep both scanner columns usable on smaller screens while allowing
        # the operator to resize the window below the original wide minimum.
        self.setMinimumSize(960, 600)

        # Operator config — validated at the edge, source of truth for jobs.
        self.stake = model.validate_stake(stake)
        self.coins = model.validate_coins(coins)
        self.interval = model.validate_interval(interval)
        self.log_threshold = model.validate_threshold(log_threshold)
        self.db = db
        self.auto_scan = bool(auto_scan)
        # Leverage cap (planner plumbing): 50x default = MAX_LEVERAGE =
        # today's behaviour exactly.
        self.leverage_cap = MAX_LEVERAGE

        # Display state (written only on the main thread).
        self.cards = []
        self._ranked, self._vetoed = [], []
        self.universe = 0
        self.failed = 0
        self.attempted = 0
        self.last_scan_ts = 0.0
        self.last_scan_label = "never"
        self.scan_status = "starting…"
        self.activity = "idle"
        self.error_text = ""
        self.dir_filter = "both"
        self.sort_key = "score"
        self.venue = {}                 # {"mexc"/"bybit": {ok, fails, last_err}}
        self.mexc_ok = None             # None = not yet known
        self.stats = {"rows": 0, "flagged": 0, "coins": 0, "longs": 0,
                      "shorts": 0, "outcomes": 0}
        self.outcome = model.outcome_summary(None)
        self.hit24 = report_mod.signal_stats(None, 24)
        self.hit_all = report_mod.signal_stats(None, None)
        self.plan_stats = {"planned": 0}
        self.plan24 = {"planned": 0, "plans_logged": 0, "coins": 0}
        self._last_stats_ts = 0.0
        self.selected_coin = None
        self._last_errors = {}          # {coin: reason} from the last scan
        self.watcher_panel = None       # WatcherPanel from the worker (or None)
        self.watcher_error = None       # contained watcher failure text
        self.watcher_selected = None    # setup id, never a row index
        self._watcher_ids = []          # dock table order, parallel to rows
        self._watcher_paused = False    # re-synced from every worker panel
        self.digest = {"picks": [], "watch": [], "new": []}
        self._plan_cache = {}           # coin -> {"plan": Plan|None, "err": str|None}
        self._plans_pending = set()
        self._busy = set()              # job kinds in flight
        self._closing = False
        # Dashboard (Task 5): filter state + cached snapshot pair/rows.
        # Market quotes ride this test-only seam (offline fixtures); the
        # dock never fetches the venue itself.
        self.dash_dir = "All"
        self.dash_market = "All"
        self.dash_horizon = DASH_DEFAULT_HORIZON
        self.dash_sort = "Newest"
        self.dash_market_data = {}
        self.dash_cards_pair = (None, None)
        self.dash_rows = []
        self.dash_asof = ""
        # Dashboard pages (Task 7): bottom-row + six-page caches. Pause
        # and disable write through the watcher_feed writers so the pages
        # read the same meta flags as the panels; display prefs persist
        # under dash_pref_* keys. No stake lives on these surfaces.
        self.dash_movers_cache = {"gainers": [], "losers": [],
                                  "high_volume": []}
        self.dash_recent_cache = []
        self.dash_activity_cache = []
        self.dash_ledger = []
        self.dash_evals = {}
        self.dash_starred_cache = set()
        self.dash_paused_cache = False
        self.dash_disabled_cache = ()
        self.dash_sig_dir = "All"
        self.dash_sig_history_on = True
        self.dash_autorefresh = False
        self.dash_compact = False
        # Coin detail (Task 6): selected coin + per-(coin, interval) bar
        # cache (None = that tab's fetch failed) + in-flight tokens so a
        # late arrival never clobbers newer fixture/manual bars.
        self.detail_coin = None
        self.detail_setup_id = None
        self.detail_cache = {}
        self.detail_tokens = {}
        self.detail_seq = 0
        self.detail_text_cache = ""
        self._lookup_goto_scanner = False  # watchlist lookup: show on Scanner
        self._launch_snapshot_at = 0.0
        self._has_live_scan = False
        self._refilling = False         # blocks selection signals during fill
        self._hdr_section = None        # header-sort state (fresh-click order)
        self.events = []                # (ts, level, text) ring buffer, max 50
        self._narrow_mode = False     # 3-region grid: right dock collapsed

        self._jobs = queue.Queue()      # UI -> worker
        self._results = queue.Queue()   # worker -> UI
        self._plan_jobs = queue.Queue()  # UI -> plan lane (never scan-blocked)

        self._build_topbar()
        self._build_pages()
        self._build_watcher_dock()
        self._build_dashboard_dock()
        self._build_detail_dock()
        self._build_dashboard_pages_dock()
        self._build_status()
        shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        shortcut.activated.connect(self._on_close)

        worker_args = SimpleNamespace(
            stake=self.stake, coins=self.coins, interval=self.interval,
            universe_budget=self.coins, log_threshold=self.log_threshold,
            db=self.db, write_logs=True, leverage_cap=self.leverage_cap)
        self._show_saved_snapshot()
        self._render_outcomes()
        self._worker = Worker(worker_args, self._jobs, self._results)
        self._worker.start()
        self._plan_worker = PlanWorker(self._worker, self._plan_jobs,
                                       self._results)
        self._plan_worker.start()

        # Qt replacement for Tk's after() loop: one drain timer, one tick.
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(TICK_MS)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()
        self._render_topbar()
        # Watcher first (same order as tkinter): the ctor activity label
        # stays the stats one below, which the build test pins.
        self._submit("watcher")
        self._submit("stats")

    # -------------------------------------------------------- launch snapshot
    def _show_saved_snapshot(self):
        """Instant launch: last screen first, live scan replaces it.

        Read only the tiny close-time file here. The newest SQLite scan is
        loaded asynchronously by Worker so a busy background writer cannot
        stall window creation.
        """
        try:
            cards, plans, saved_at, label = model.load_file_snapshot(self.db)
            self._launch_snapshot_at = saved_at
            if not cards:
                return
            self.cards = list(cards)
            self.failed = 0
            self.attempted = len(self.cards)
            for coin, entry in (plans or {}).items():
                if isinstance(entry, dict) and entry.get("plan") is not None:
                    self._plan_cache[coin] = entry
            self.scan_status = (f"showing {label} — "
                                "live scan running…")
            self._render_all()
        except Exception as e:
            self.scan_status = (f"no saved signals ({type(e).__name__}) — "
                                f"waiting for the live scan…")
            try:
                self._render_status()
            except Exception:
                pass

    # ------------------------------------------------------------- widgets
    def _build_topbar(self):
        """Top bar: logo · title · badges · scan UTC · countdown · venue · gear."""
        central = QWidget(self)
        central.setObjectName("central")
        self.setCentralWidget(central)
        self._central_layout = QVBoxLayout(central)
        self._central_layout.setContentsMargins(8, 5, 8, 4)
        self._central_layout.setSpacing(4)

        bar = QWidget()
        bar.setObjectName("header")
        bar.setFixedHeight(50)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        logo = QLabel()
        logo.setPixmap(_logo_pixmap())
        lay.addWidget(logo)
        title = QLabel("ALT RADAR")
        title.setObjectName("headerTitle")
        title.setFont(_theme.ui_font(_theme.BASE_POINT_SIZE + 3, bold=True))
        lay.addWidget(title)
        subtitle = QLabel("MEXC Perpetual Futures Scanner")
        subtitle.setObjectName("headerSubtitle")
        lay.addWidget(subtitle)
        # Permanent READ-ONLY marker (brief: no orders, no keys).
        badge_ro = QLabel(" READ-ONLY ")
        badge_ro.setObjectName("badgeReadOnly")
        badge_ro.setFont(_theme.ui_font(_theme.BASE_POINT_SIZE + 1, bold=True))
        lay.addWidget(badge_ro)
        badge_t2 = QLabel("TIER-2 UNVALIDATED")
        badge_t2.setObjectName("badgeTier2")
        lay.addWidget(badge_t2)
        lay.addStretch(1)

        self.lbl_scan_utc = QLabel("Scan: --:--:--")
        self.lbl_scan_utc.setObjectName("headerStats")
        lay.addWidget(self.lbl_scan_utc)
        # SCANNING dot + elapsed: driven ONLY by real worker/scan state
        # (see _render_topbar) — never a hardcoded or invented timer.
        self.lbl_scan_dot = QLabel("● IDLE")
        self.lbl_scan_dot.setObjectName("scanDot")
        self.lbl_scan_dot.setStyleSheet(f"color: {_theme.TEXT_DIM};")
        lay.addWidget(self.lbl_scan_dot)
        self.lbl_scan_elapsed = QLabel("no scan yet")
        self.lbl_scan_elapsed.setObjectName("headerStats")
        lay.addWidget(self.lbl_scan_elapsed)
        self.lbl_countdown = QLabel("Next scan: --:--")
        self.lbl_countdown.setObjectName("headerStats")
        lay.addWidget(self.lbl_countdown)
        self.lbl_venue = QLabel("MEXC –")
        self.lbl_venue.setObjectName("badgeVenue")
        lay.addWidget(self.lbl_venue)
        # UTC clock (real UTC, refreshed on every tick/render).
        self.lbl_clock = QLabel("--:--:-- UTC")
        self.lbl_clock.setObjectName("headerStats")
        lay.addWidget(self.lbl_clock)
        self.btn_gear = QPushButton("⚙")
        self.btn_gear.setFixedWidth(28)
        self.btn_gear.setToolTip("Open Settings")
        self.btn_gear.clicked.connect(lambda: self._switch_page("settings"))
        lay.addWidget(self.btn_gear)
        self._central_layout.addWidget(bar)
        # 3-region grid anchor: the fixed-height top strip (50px).
        self.header = bar

    # --------------------------------------------------------------- rail
    def _build_pages(self):
        """Left icon rail + the QStackedWidget it switches."""
        row = QWidget()
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(4)
        self._central_layout.addWidget(row, 1)

        # ---- narrow icon rail: glyph + tiny label, exclusive selection ----
        rail = QWidget()
        rail.setObjectName("rail")
        rail.setFixedWidth(145)
        self.rail = rail
        rail_lay = QVBoxLayout(rail)
        rail_lay.setContentsMargins(3, 3, 3, 3)
        rail_lay.setSpacing(2)
        self.rail_group = QButtonGroup(self)
        self.rail_group.setExclusive(True)
        self.rail_buttons = {}
        for i, (key, label, glyph) in enumerate(RAIL_PAGES):
            btn = QPushButton(f"{glyph}\n{label}")
            btn.setObjectName("railButton")
            btn.setFont(_theme.ui_font(_theme.BASE_POINT_SIZE - 1))
            btn.setToolTip(label)
            btn.setCheckable(True)
            btn.setFixedHeight(54)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._switch_page(k))
            self.rail_group.addButton(btn, i)
            self.rail_buttons[key] = btn
            rail_lay.addWidget(btn)
        rail_lay.addStretch(1)
        # Bottom status blocks: API + venue from REAL connection state
        # (mexc_ok / self.venue, same sources as the top-bar pill), the
        # account block states the read-only invariant (no keys held) —
        # none of them ever hardcodes a "Connected" claim.
        self.lbl_rail_api = QLabel("API: MEXC –")
        self.lbl_rail_venue = QLabel("venue: Bybit –")
        self.lbl_rail_account = QLabel("account: read-only (no keys)")
        for block in (self.lbl_rail_api, self.lbl_rail_venue,
                      self.lbl_rail_account):
            block.setObjectName("railStatus")
            block.setWordWrap(True)
            block.setFont(_theme.ui_font(_theme.BASE_POINT_SIZE - 1))
            block.setStyleSheet(f"color: {_theme.TEXT_DIM};")
            rail_lay.addWidget(block)
        # Selected-page green edge (ACC_POS accent). Rail-local override
        # so qtgui/theme.py stays untouched.
        rail.setStyleSheet(
            "#railButton:checked { border-left: 2px solid %s; "
            "font-weight: 700; }" % _theme.ACC_POS)
        row_lay.addWidget(rail)

        # ---- stacked pages ----
        self.pages = QStackedWidget()
        row_lay.addWidget(self.pages, 1)
        self.page_scanner = QWidget()
        self.page_watchlist = QWidget()
        self.page_outcomes = QWidget()
        self.page_logs = QWidget()
        self.page_settings = QWidget()
        self._page_index = {"scanner": 0, "watchlist": 1, "outcomes": 2,
                            "logs": 3, "settings": 4}
        for page in (self.page_scanner, self.page_watchlist,
                     self.page_outcomes, self.page_logs, self.page_settings):
            self.pages.addWidget(page)
        self.rail_buttons["scanner"].setChecked(True)

        self._build_scanner_page()
        self._build_watchlist_page()
        self._build_outcomes_page()
        self._build_logs_page()
        self._build_settings_page()

    def _switch_page(self, key):
        """Rail/gear navigation: switch the stacked page by key."""
        idx = self._page_index.get(key)
        if idx is None:
            return
        self.pages.setCurrentIndex(idx)
        btn = self.rail_buttons.get(key)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        # Pages that read live state refresh on entry (cheap, local only).
        if key == "watchlist":
            self._render_watchlist()
        elif key == "outcomes":
            self._render_outcomes()
        elif key == "logs":
            self._refresh_log_file()

    def current_page(self):
        """Key of the visible page ('scanner' / 'watchlist' / ...)."""
        idx = self.pages.currentIndex()
        for key, i in self._page_index.items():
            if i == idx:
                return key
        return "scanner"

    # --------------------------------------------- 3-region responsive grid
    def resizeEvent(self, event):
        """Collapse/restore the right detail dock across the breakpoint."""
        super().resizeEvent(event)
        try:
            self._update_narrow_mode()
        except Exception:
            pass

    def _update_narrow_mode(self):
        """Hide the right dock below LAYOUT_NARROW_PX, show it above."""
        dock = getattr(self, "detail_dock", None)
        if dock is None:
            return
        narrow = self.width() < LAYOUT_NARROW_PX
        self._narrow_mode = narrow
        if narrow:
            dock.hide()
        else:
            dock.show()

    def drawer_available(self):
        """True while the right panel is collapsed to its drawer fallback.

        The fallback is the scanner page's in-page Detail group, which
        stays visible and reachable in narrow mode — collapsing the dock
        never strands the detail content.
        """
        return bool(getattr(self, "_narrow_mode", False))

    # ----------------------------------------------------------- toolbar
    def _group(self, parent_lay, text, stretch=0):
        box = QGroupBox(text)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)
        parent_lay.addWidget(box, stretch)
        return box

    def _build_scanner_page(self):
        """Scanner page: one toolbar row + signals/vetoed/detail/outcomes."""
        sv = QVBoxLayout(self.page_scanner)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(4)
        self._build_toolbar(sv)
        self._build_body(sv)

    def _build_toolbar(self, parent_lay):
        """One compact scanner row: scan, search, and table views."""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        parent_lay.addWidget(row)

        g_scan = self._group(lay, "Scan")
        self.btn_scan = QPushButton("Scan now")
        self.btn_scan.setObjectName("btnPrimary")
        self.btn_scan.clicked.connect(lambda: self._submit("scan"))
        g_scan.layout().addWidget(self.btn_scan)
        self.btn_auto = QPushButton(self._auto_label())
        self.btn_auto.clicked.connect(self._toggle_auto)
        g_scan.layout().addWidget(self.btn_auto)

        g_find = self._group(lay, "Find", stretch=1)
        self.ed_search = QLineEdit()
        self.ed_search.setMinimumWidth(180)
        self.ed_search.setPlaceholderText("coin… (Enter = venue-wide)")
        self.ed_search.textChanged.connect(lambda *_: self._on_search_change())
        self.ed_search.returnPressed.connect(self._on_search_commit)
        g_find.layout().addWidget(self.ed_search)

        g_view = self._group(lay, "View")
        g_view.layout().addWidget(QLabel("Dir"))
        self.cmb_dir = QComboBox()
        self.cmb_dir.addItems(list(DIR_CHOICES))
        self.cmb_dir.setFixedWidth(76)
        self.cmb_dir.currentTextChanged.connect(lambda *_: self._on_dir_change())
        g_view.layout().addWidget(self.cmb_dir)
        g_view.layout().addWidget(QLabel("Sort"))
        self.cmb_sort = QComboBox()
        self.cmb_sort.addItems(list(SORT_CHOICES))
        self.cmb_sort.setFixedWidth(84)
        self.cmb_sort.currentTextChanged.connect(lambda *_: self._on_sort_change())
        g_view.layout().addWidget(self.cmb_sort)

    def _make_table(self, columns, headings, widths):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels([headings[c] for c in columns])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setFont(_theme.mono_font())
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(_theme.ROW_HEIGHT)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        for i, col in enumerate(columns):
            header.resizeSection(i, widths[col])
        return table

    def _build_body(self, parent_lay):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ---- left: signals table + vetoed section ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)

        sig_box = QGroupBox("Signals")
        sv = QVBoxLayout(sig_box)
        sv.setContentsMargins(4, 4, 4, 4)
        self.tree = self._make_table(SIGNAL_COLUMNS, SIGNAL_HEADINGS,
                                     SIGNAL_WIDTHS)
        self.tree.setMinimumHeight(240)
        self.tree.setSortingEnabled(False)   # sort ON, but only between fills
        self.tree.horizontalHeader().sectionClicked.connect(
            self._on_header_clicked)
        self.tree.itemSelectionChanged.connect(self._on_select)
        sv.addWidget(self.tree)
        lv.addWidget(sig_box, 1)

        vet_box = QGroupBox("VETOED — excluded from ranking, shadow-logged")
        vv = QVBoxLayout(vet_box)
        vv.setContentsMargins(4, 4, 4, 4)
        veto_cols = ("dir", "coin", "score", "reason", "vetoes")
        veto_heads = {"dir": "DIR", "coin": "COIN", "score": "SCORE",
                      "reason": "REASON", "vetoes": "VETO CODES"}
        veto_widths = {"dir": 44, "coin": 140, "score": 70, "reason": 130,
                       "vetoes": 260}
        self.tree_vetoed = self._make_table(veto_cols, veto_heads,
                                             veto_widths)
        self.veto_cols = veto_cols
        self.tree_vetoed.setMaximumHeight(_theme.ROW_HEIGHT * 6 + 30)
        self.tree_vetoed.itemSelectionChanged.connect(self._on_select)
        vv.addWidget(self.tree_vetoed)
        # The veto table is intentionally not operator-facing: rejected
        # candidates are data, not a feed. The box stays in the layout so
        # the widgets remain parented (an unparented hidden widget would be
        # garbage-collected and crash the veto render paths), but hidden so
        # it takes no space. Watcher setups are monitored, not orders.
        lv.addWidget(vet_box)
        vet_box.hide()

        # ---- right: detail pane + outcomes dock ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)

        detail_outcomes = QSplitter(Qt.Orientation.Vertical)
        detail_outcomes.setChildrenCollapsible(False)
        detail_outcomes.setHandleWidth(6)
        self.detail_outcomes_splitter = detail_outcomes

        det_box = QGroupBox("Detail — selected coin (review only, no orders)")
        dv = QVBoxLayout(det_box)
        dv.setContentsMargins(6, 6, 6, 6)
        dv.setSpacing(4)

        # coin header row: name + direction badge + UNVALIDATED badge
        hdr = QHBoxLayout()
        self.lbl_det_coin = QLabel("—")
        self.lbl_det_coin.setObjectName("detailTitle")
        self.lbl_det_coin.setFont(_theme.ui_font(_theme.BASE_POINT_SIZE + 4,
                                                 bold=True))
        hdr.addWidget(self.lbl_det_coin)
        self.lbl_det_dir = QLabel("")
        self.lbl_det_dir.setObjectName("badgeDirNeutral")
        hdr.addWidget(self.lbl_det_dir)
        self.lbl_det_unval = QLabel("UNVALIDATED")
        self.lbl_det_unval.setObjectName("badgeTier2")
        self.lbl_det_unval.hide()
        hdr.addWidget(self.lbl_det_unval)
        hdr.addStretch(1)
        dv.addLayout(hdr)

        # subtitle: Base / Quote (Perpetual)
        self.lbl_det_sub = QLabel("")
        self.lbl_det_sub.setObjectName("detailSubtitle")
        dv.addWidget(self.lbl_det_sub)

        # big mono price + 24h% (green/red)
        pr = QHBoxLayout()
        self.lbl_det_price = QLabel("")
        self.lbl_det_price.setObjectName("detailPrice")
        self.lbl_det_price.setFont(_theme.mono_font(_theme.DETAIL_POINT_SIZE + 6,
                                                    bold=True))
        pr.addWidget(self.lbl_det_price)
        self.lbl_det_chg = QLabel("")
        self.lbl_det_chg.setFont(_theme.mono_font(_theme.DETAIL_POINT_SIZE + 2,
                                                  bold=True))
        pr.addWidget(self.lbl_det_chg)
        pr.addStretch(1)
        dv.addLayout(pr)

        # stat tiles: 24H VOLUME / OI / FUNDING (3 tiles; no 24h high/low —
        # not stored, never invented)
        tiles = QHBoxLayout()
        self.lbl_det_vol = self._tile(tiles, "24H VOLUME")
        self.lbl_det_oi = self._tile(tiles, "OI")
        self.lbl_det_funding = self._tile(tiles, "FUNDING")
        tiles.addStretch(1)
        dv.addLayout(tiles)

        # signal details key-value table
        lbl_kv = QLabel("SIGNAL DETAILS")
        lbl_kv.setObjectName("sectionLabel")
        dv.addWidget(lbl_kv)
        self.det_kv = QTableWidget(0, 2)
        self.det_kv.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.det_kv.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.det_kv.setShowGrid(False)
        self.det_kv.setFont(_theme.mono_font())
        self.det_kv.verticalHeader().setVisible(False)
        self.det_kv.horizontalHeader().setVisible(False)
        # Eleven key/value rows should fit in the default detail allocation.
        self.det_kv.verticalHeader().setDefaultSectionSize(22)
        self.det_kv.horizontalHeader().setStretchLastSection(True)
        self.det_kv.setMinimumHeight(22 * 11)
        dv.addWidget(self.det_kv, 3)

        # flags section
        lbl_flags_hdr = QLabel("FLAGS")
        lbl_flags_hdr.setObjectName("sectionLabel")
        dv.addWidget(lbl_flags_hdr)
        self.lbl_det_flags = QLabel("—")
        self.lbl_det_flags.setFont(_theme.mono_font())
        dv.addWidget(self.lbl_det_flags)

        # analyst note box (counter-trend/MTF/DATA warnings + veto text)
        lbl_notes = QLabel("ANALYST NOTES")
        lbl_notes.setObjectName("sectionLabel")
        dv.addWidget(lbl_notes)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(_theme.mono_font(_theme.DETAIL_POINT_SIZE))
        self.detail.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.detail.setMinimumHeight(56)
        dv.addWidget(self.detail, 1)
        detail_outcomes.addWidget(det_box)

        # ---- outcomes dock: Summary / Performance tabs + Resolve now ----
        out_box = QGroupBox("Outcomes")
        ov = QVBoxLayout(out_box)
        ov.setContentsMargins(6, 6, 6, 6)
        self.out_tabs = QTabWidget()
        sum_tab = QWidget()
        sum_lay = QVBoxLayout(sum_tab)
        sum_lay.setContentsMargins(4, 4, 4, 4)
        self.tbl_outcomes_summary = self._make_outcome_table()
        sum_lay.addWidget(self.tbl_outcomes_summary)
        self._set_outcome_rows(
            self.tbl_outcomes_summary,
            [("Window", "Rolling last 24 hours"),
             ("Flagged observations", "loading daily sample…")])
        perf_tab = QWidget()
        perf_lay = QVBoxLayout(perf_tab)
        perf_lay.setContentsMargins(4, 4, 4, 4)
        self.tbl_outcomes_performance = self._make_outcome_table()
        perf_lay.addWidget(self.tbl_outcomes_performance)
        self.out_tabs.addTab(sum_tab, "Summary")
        self.out_tabs.addTab(perf_tab, "Performance")
        ov.addWidget(self.out_tabs)
        btn_resolve_now = QPushButton("Resolve now")
        btn_resolve_now.clicked.connect(lambda: self._submit("resolve"))
        ov.addWidget(btn_resolve_now, 0, Qt.AlignmentFlag.AlignLeft)
        detail_outcomes.addWidget(out_box)
        # Coin detail is the daily-driver view; outcomes stays visible at a
        # compact quarter-height and the handle remains user-adjustable.
        detail_outcomes.setStretchFactor(0, 3)
        detail_outcomes.setStretchFactor(1, 1)
        detail_outcomes.setSizes([570, 190])
        rv.addWidget(detail_outcomes, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        # 3-region grid split: center ~65% / right ~35% of the flexible
        # area (stretch only — no absolute positioning, user-adjustable).
        splitter.setStretchFactor(0, 65)
        splitter.setStretchFactor(1, 35)
        splitter.setSizes([845, 455])
        parent_lay.addWidget(splitter, 1)
        self.body_splitter = splitter
        self._reset_detail()

    def _tile(self, parent_lay, title):
        """One stat tile (title over mono value); returns the value label."""
        frame = QFrame()
        frame.setObjectName("tile")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(8, 4, 8, 4)
        fl.setSpacing(1)
        t = QLabel(title)
        t.setObjectName("tileTitle")
        fl.addWidget(t)
        v = QLabel("n/a")
        v.setObjectName("tileValue")
        v.setFont(_theme.mono_font(_theme.DETAIL_POINT_SIZE + 1))
        fl.addWidget(v)
        parent_lay.addWidget(frame)
        return v

    def _make_outcome_table(self):
        """Compact label/value layout shared by the two outcome tabs."""
        table = QTableWidget(0, 2)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setFont(_theme.mono_font())
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(20)
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(100)
        return table

    @staticmethod
    def _set_outcome_rows(table, rows):
        """Render outcome metrics in the same aligned form as coin details."""
        table.setRowCount(len(rows))
        for row, (label, value) in enumerate(rows):
            key = _CellItem(label, align=_align("w"))
            key.setForeground(QBrush(QColor(_theme.TEXT_DIM)))
            val = _CellItem(value, align=_align("w"))
            val.setToolTip(value)
            table.setItem(row, 0, key)
            table.setItem(row, 1, val)
        table.horizontalHeader().resizeSection(
            0, max(112, int(table.width() * 0.30)))

    # ---------------------------------------------------- rail pages
    def _build_watchlist_page(self):
        """Watchlist page: watch, Top 10, and new-listing tables."""
        v = QVBoxLayout(self.page_watchlist)
        v.setContentsMargins(4, 4, 4, 4)
        self.watch_tabs = QTabWidget()
        watch_heads = {"coin": "COIN", "dir": "DIR", "score": "SCORE",
                       "min": "MIN NOTIONAL"}
        self.tbl_watchlist = self._make_table(
            ("coin", "dir", "score", "min"), watch_heads,
            {"coin": 180, "dir": 60, "score": 100, "min": 160})
        self.tbl_top_picks = self._make_table(
            ("coin", "dir", "score", "price", "lean"),
            {"coin": "COIN", "dir": "DIR", "score": "SCORE",
             "price": "PRICE", "lean": "LEAN"},
            {"coin": 180, "dir": 60, "score": 100, "price": 140,
             "lean": 100})
        self.tbl_new_listings = self._make_table(
            ("coin", "seen", "score", "dir"),
            {"coin": "COIN", "seen": "FIRST SEEN", "score": "SCORE",
             "dir": "DIR"},
            {"coin": 180, "seen": 180, "score": 100, "dir": 60})
        self._watch_tables = (self.tbl_watchlist, self.tbl_top_picks,
                              self.tbl_new_listings)
        self.watch_tabs.addTab(self.tbl_watchlist, "Watchlist")
        self.watch_tabs.addTab(self.tbl_top_picks, "Top 10")
        self.watch_tabs.addTab(self.tbl_new_listings, "New coins")
        self.watch_tabs.currentChanged.connect(self._update_watchlist_hint)
        for table in self._watch_tables:
            table.itemSelectionChanged.connect(
                lambda t=table: self._on_watch_select(t))
            table.itemDoubleClicked.connect(
                lambda _item, t=table: self._open_coin_from_list(t))
        v.addWidget(self.watch_tabs, 1)
        self.lbl_watchlist_hint = QLabel("")
        self.lbl_watchlist_hint.setObjectName("headerStats")
        self.lbl_watchlist_hint.setText(
            "Select a row to open its detail on the Scanner page")
        v.addWidget(self.lbl_watchlist_hint)

    def _build_outcomes_page(self):
        """Daily and cumulative outcomes with explicit sample denominators."""
        v = QVBoxLayout(self.page_outcomes)
        v.setContentsMargins(4, 4, 4, 4)
        self.outcomes_page_tabs = QTabWidget()
        self.tbl_outcomes_daily = self._make_outcome_matrix()
        self.tbl_plans_daily = self._make_outcome_matrix(
            ("MEASURE", "HITS", "SAMPLE", "RATE"))
        self.tbl_outcomes = self._make_outcome_matrix()
        self.tbl_plans_cumulative = self._make_outcome_matrix(
            ("MEASURE", "HITS", "SAMPLE", "RATE"))
        self.lbl_outcomes_daily = QLabel()
        self.lbl_outcomes_cumulative = QLabel()
        for label in (self.lbl_outcomes_daily, self.lbl_outcomes_cumulative):
            label.setWordWrap(True)
            label.setObjectName("headerStats")
        self.outcomes_page_tabs.addTab(self._make_outcome_cohort(
            self.lbl_outcomes_daily, self.tbl_outcomes_daily,
            self.tbl_plans_daily), "Daily · 24h")
        self.outcomes_page_tabs.addTab(self._make_outcome_cohort(
            self.lbl_outcomes_cumulative, self.tbl_outcomes,
            self.tbl_plans_cumulative), "Cumulative")
        v.addWidget(self.outcomes_page_tabs, 1)
        btn_resolve_page = QPushButton("Resolve pending outcomes")
        btn_resolve_page.clicked.connect(lambda: self._submit("resolve"))
        v.addWidget(btn_resolve_page, 0, Qt.AlignmentFlag.AlignLeft)

    @staticmethod
    def _make_outcome_matrix(headers=("HORIZON / SIDE", "RESOLVED", "WINS",
                                      "LOSSES", "HIT RATE", "AVG RETURN")):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(list(headers))
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setShowGrid(False)
        table.setFont(_theme.mono_font())
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(_theme.ROW_HEIGHT)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    @staticmethod
    def _make_outcome_cohort(context, signals, plans):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(context)
        split = QSplitter(Qt.Orientation.Horizontal)
        signal_box = QGroupBox("Signal outcomes · each horizon measured separately")
        signal_layout = QVBoxLayout(signal_box)
        signal_layout.addWidget(signals)
        plan_box = QGroupBox("Plan level touches · overlapping outcomes")
        plan_layout = QVBoxLayout(plan_box)
        plan_layout.addWidget(plans)
        split.addWidget(signal_box)
        split.addWidget(plan_box)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, 1)
        return page

    def _build_logs_page(self):
        """Logs page: tails logs/app.log + the in-app event ring buffer."""
        v = QVBoxLayout(self.page_logs)
        v.setContentsMargins(4, 4, 4, 4)
        box = QGroupBox("Logs — scanner log file + recent in-app events")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(6, 6, 6, 6)
        top = QHBoxLayout()
        lbl_file = QLabel("logs/app.log")
        lbl_file.setObjectName("headerStats")
        top.addWidget(lbl_file)
        top.addStretch(1)
        btn_refresh_log = QPushButton("Refresh")
        btn_refresh_log.clicked.connect(self._refresh_log_file)
        top.addWidget(btn_refresh_log)
        bv.addLayout(top)
        self.logs_view = QPlainTextEdit()
        self.logs_view.setReadOnly(True)
        self.logs_view.setFont(_theme.mono_font())
        self.logs_view.setMinimumHeight(180)
        bv.addWidget(self.logs_view, 3)
        lbl_events = QLabel("RECENT IN-APP EVENTS")
        lbl_events.setObjectName("sectionLabel")
        bv.addWidget(lbl_events)
        self.events_log_list = QListWidget()
        self.events_log_list.setFont(_theme.mono_font())
        bv.addWidget(self.events_log_list, 1)
        v.addWidget(box)

    def _build_settings_page(self):
        """Settings and low-frequency data actions, away from the scan row."""
        v = QVBoxLayout(self.page_settings)
        v.setContentsMargins(4, 4, 4, 4)
        box = QGroupBox("Settings — each field applies and validates on its own")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        grid.addWidget(QLabel("Stake $"), 0, 0)
        self.ed_stake = QLineEdit(f"{self.stake:g}")
        self.ed_stake.setFixedWidth(100)
        self.ed_stake.editingFinished.connect(
            lambda: self._apply_stake("settings"))
        grid.addWidget(self.ed_stake, 0, 1)
        self.btn_apply_stake = QPushButton("Apply")
        self.btn_apply_stake.clicked.connect(
            lambda: self._apply_stake("settings"))
        grid.addWidget(self.btn_apply_stake, 0, 2)

        grid.addWidget(QLabel("Flag threshold"), 1, 0)
        self.cmb_threshold = QComboBox()
        presets = list(THRESHOLD_PRESETS)
        if self.log_threshold not in presets:
            presets.append(self.log_threshold)
        self.cmb_threshold.addItems([f"{p:g}" for p in presets])
        self.cmb_threshold.setCurrentText(f"{self.log_threshold:g}")
        self.cmb_threshold.setFixedWidth(90)
        self.cmb_threshold.currentTextChanged.connect(
            lambda *_: self._apply_threshold("preset"))
        grid.addWidget(self.cmb_threshold, 1, 1)
        self.ed_threshold = QLineEdit(f"{self.log_threshold:g}")
        self.ed_threshold.setFixedWidth(80)
        self.ed_threshold.editingFinished.connect(
            lambda: self._apply_threshold("settings"))
        grid.addWidget(self.ed_threshold, 1, 2)
        self.btn_apply_threshold = QPushButton("Apply")
        self.btn_apply_threshold.clicked.connect(
            lambda: self._apply_threshold("settings"))
        grid.addWidget(self.btn_apply_threshold, 1, 3)

        grid.addWidget(QLabel("Coins (universe + scan size)"), 2, 0)
        self.sp_coins = QSpinBox()
        self.sp_coins.setRange(model.MIN_COINS, model.MAX_COINS)
        self.sp_coins.setValue(self.coins)
        self.sp_coins.setKeyboardTracking(False)
        self.sp_coins.setFixedWidth(100)
        self.sp_coins.valueChanged.connect(lambda *_: self._apply_coins())
        grid.addWidget(self.sp_coins, 2, 1)

        grid.addWidget(QLabel("Interval s (auto-scan)"), 3, 0)
        self.sp_interval = QSpinBox()
        self.sp_interval.setRange(model.MIN_INTERVAL, 3600)
        self.sp_interval.setValue(self.interval)
        self.sp_interval.setKeyboardTracking(False)
        self.sp_interval.setFixedWidth(100)
        self.sp_interval.valueChanged.connect(lambda *_: self._apply_interval())
        grid.addWidget(self.sp_interval, 3, 1)

        grid.addWidget(QLabel("DB path"), 4, 0)
        self.ed_db = QLineEdit(self.db)
        self.ed_db.editingFinished.connect(self._apply_db)
        grid.addWidget(self.ed_db, 4, 1)
        btn_db = QPushButton("Apply")
        btn_db.clicked.connect(self._apply_db)
        grid.addWidget(btn_db, 4, 2)

        grid.addWidget(QLabel("Leverage cap"), 5, 0)
        self.cmb_lev = QComboBox()
        self.cmb_lev.addItems([f"{c}x" for c in LEVERAGE_CHOICES])
        self.cmb_lev.setCurrentText(f"{self.leverage_cap}x")
        self.cmb_lev.setFixedWidth(100)
        self.cmb_lev.currentTextChanged.connect(
            lambda *_: self._apply_leverage("settings"))
        grid.addWidget(self.cmb_lev, 5, 1)

        grid.setColumnStretch(4, 1)
        v.addWidget(box, 0)

        # Background collection and resolution are occasional maintenance,
        # so keep them off the scan toolbar.
        mnt = QGroupBox("Maintenance")
        mh = QHBoxLayout(mnt)
        mh.setContentsMargins(4, 2, 4, 2)
        self.btn_collect = QPushButton("Collect bars")
        self.btn_collect.clicked.connect(lambda: self._submit("collect"))
        mh.addWidget(self.btn_collect)
        self.btn_resolve = QPushButton("Resolve outcomes")
        self.btn_resolve.clicked.connect(lambda: self._submit("resolve"))
        mh.addWidget(self.btn_resolve)
        mh.addStretch(1)
        v.addWidget(mnt, 0)

        data = self._group(v, "Data")
        self.btn_export = QPushButton("Export signals")
        self.btn_export.clicked.connect(lambda: self._export_csv())
        data.layout().addWidget(self.btn_export)
        self.btn_copy = QPushButton("Copy selected detail")
        self.btn_copy.clicked.connect(self._copy_detail_text)
        data.layout().addWidget(self.btn_copy)
        self.btn_stats = QPushButton("Refresh stats")
        self.btn_stats.clicked.connect(lambda: self._submit("stats"))
        data.layout().addWidget(self.btn_stats)
        v.addStretch(1)

    def _build_status(self):
        bar = QStatusBar()
        self.setStatusBar(bar)
        inner = QWidget()
        lay = QHBoxLayout(inner)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(14)
        self.lbl_activity = QLabel("activity: idle")
        self.lbl_failed = QLabel("failed 0/0 of last scan")
        self.lbl_header = QLabel("universe – · shown 0 · vetoed 0")
        self.lbl_header.setObjectName("headerStats")
        self.lbl_header.setFont(_theme.ui_font())
        self.lbl_statusline = QLabel("")
        self.lbl_statusline.setFont(_theme.mono_font())
        lay.addWidget(self.lbl_activity)
        lay.addWidget(self.lbl_failed)
        lay.addWidget(self.lbl_header, 1)
        lay.addWidget(self.lbl_statusline, 2)
        bar.addWidget(inner, 1)

    def _build_watcher_dock(self):
        """Watcher setup dock (additive; scanner table/sort/workers untouched).

        Lands the Task 7-deferred Qt watcher panel: a setup QTableWidget +
        a read-only detail pane, fed worker-side through the existing
        queue->QTimer poll (the shared Worker already serves the watcher
        jobs, same as tkinter). Selection is the setup id, never the row
        index, so it survives refresh reorders. Detail text comes from the
        shared model helper (STATE_WORDS verbs verbatim, research banner
        first) plus the report-shaped correlated-opens section.
        """
        dock = QDockWidget(
            "Watcher — setups (research, shadow-only — no orders)", self)
        dock.setObjectName("watcherDock")
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        table = QTableWidget(0, len(WATCHER_COLUMNS))
        table.setHorizontalHeaderLabels(
            [WATCHER_HEADINGS[c] for c in WATCHER_COLUMNS])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection)
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setFont(_theme.mono_font())
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(_theme.ROW_HEIGHT)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        for i, col in enumerate(WATCHER_COLUMNS):
            header.resizeSection(i, WATCHER_WIDTHS[col])
        table.itemSelectionChanged.connect(self._on_watcher_select)
        lay.addWidget(table, 3)
        self.watcher_table = table

        self.watcher_detail = QTextEdit()
        self.watcher_detail.setReadOnly(True)
        self.watcher_detail.setFont(
            _theme.mono_font(_theme.DETAIL_POINT_SIZE))
        self.watcher_detail.setLineWrapMode(
            QTextEdit.LineWrapMode.WidgetWidth)
        self.watcher_detail.setMinimumHeight(56)
        self.watcher_detail.setPlainText("watcher loading…")
        lay.addWidget(self.watcher_detail, 2)

        row = QHBoxLayout()
        self.btn_watcher_refresh = QPushButton("Refresh watcher")
        self.btn_watcher_refresh.clicked.connect(self.refresh_watcher)
        row.addWidget(self.btn_watcher_refresh)
        self.btn_watcher_pause = QPushButton("Pause watcher")
        self.btn_watcher_pause.clicked.connect(self._on_watcher_pause)
        row.addWidget(self.btn_watcher_pause)
        self.btn_watcher_disable = QPushButton("Disable rule")
        self.btn_watcher_disable.clicked.connect(self._on_watcher_disable)
        row.addWidget(self.btn_watcher_disable)
        row.addStretch(1)
        lay.addLayout(row)

        dock.setWidget(body)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.watcher_dock = dock
        # Brief Step 1 shape: dock.table is the setup table.
        dock.table = self.watcher_table

    # ------------------------------------------ dashboard dock (Task 5)
    def _build_dashboard_dock(self):
        """Dashboard dock (additive; rail/scanner/watcher untouched).

        Stat cards (snapshot pair or withheld comparison) + the Top
        Signals table (one row per active setup, filter combos, evidence
        cells, expiry countdowns) under the research banner. All reads
        are local + read-only; every render path is contained so a
        missing DB degrades to honest fallback copy, never a raise.
        """
        dock = QDockWidget("Active Setups — persistent signals", self)
        dock.setObjectName("dashboardDock")
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        self.dash_banner = QLabel(RESEARCH_BANNER)
        self.dash_banner.setWordWrap(True)
        self.dash_banner.setObjectName("headerStats")
        lay.addWidget(self.dash_banner)

        cards = QHBoxLayout()
        cards.setSpacing(4)
        self.dash_card_value = []
        self.dash_card_sub = []
        for title, _value, _sub in format_dashboard_cards(None, None):
            frame = QFrame()
            frame.setObjectName("tile")
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(8, 4, 8, 4)
            fl.setSpacing(1)
            t = QLabel(title)
            t.setObjectName("tileTitle")
            fl.addWidget(t)
            v = QLabel("—")
            v.setObjectName("tileValue")
            v.setFont(_theme.mono_font(_theme.DETAIL_POINT_SIZE + 1))
            fl.addWidget(v)
            s = QLabel(DASH_NO_COMPARISON)
            s.setObjectName("headerStats")
            s.setWordWrap(True)
            fl.addWidget(s)
            cards.addWidget(frame)
            self.dash_card_value.append(v)
            self.dash_card_sub.append(s)
        lay.addLayout(cards)

        filt = QHBoxLayout()
        filt.setSpacing(4)
        self.dash_cmb_dir = QComboBox()
        self.dash_cmb_dir.addItems(list(DASH_DIRS))
        self.dash_cmb_market = QComboBox()
        self.dash_cmb_market.addItems(list(DASH_MARKETS))
        self.dash_cmb_horizon = QComboBox()
        self.dash_cmb_horizon.addItems(list(DASH_HORIZONS))
        self.dash_cmb_horizon.setCurrentText(DASH_DEFAULT_HORIZON)
        self.dash_cmb_sort = QComboBox()
        self.dash_cmb_sort.addItems(list(DASH_SORTS))
        for label, combo in (("Direction", self.dash_cmb_dir),
                             ("Market", self.dash_cmb_market),
                             ("Horizon", self.dash_cmb_horizon),
                             ("Sort", self.dash_cmb_sort)):
            filt.addWidget(QLabel(label))
            filt.addWidget(combo)
            combo.currentTextChanged.connect(
                lambda *_: self._on_dash_filter())
        filt.addStretch(1)
        lay.addLayout(filt)

        sig_box = QGroupBox("Top Signals — one row per active setup")
        sv = QVBoxLayout(sig_box)
        sv.setContentsMargins(4, 4, 4, 4)
        self.dash_table = self._make_table(DASH_TABLE_COLUMNS,
                                           dict(zip(DASH_TABLE_COLUMNS,
                                                    DASH_TABLE_HEADINGS)),
                                           DASH_TABLE_WIDTHS)
        self.dash_table.setMinimumHeight(120)
        self.dash_table.itemSelectionChanged.connect(self._on_dash_select)
        sv.addWidget(self.dash_table)
        lay.addWidget(sig_box, 1)

        # Bottom row (Task 7): Market Movers (offline quotes only),
        # Recent Analysis (last 5 emissions/transitions) and Activity Log
        # (capped journal rows). Appended, never restructured.
        brow = QHBoxLayout()
        brow.setSpacing(4)
        mov_box = QGroupBox("Market Movers")
        mov_lay = QVBoxLayout(mov_box)
        mov_lay.setContentsMargins(4, 4, 4, 4)
        self.dash_movers = QLabel(DASH_NO_QUOTES)
        self.dash_movers.setWordWrap(True)
        self.dash_movers.setFont(_theme.mono_font())
        mov_lay.addWidget(self.dash_movers)
        brow.addWidget(mov_box, 1)
        rec_box = QGroupBox("Recent Analysis")
        rec_lay = QVBoxLayout(rec_box)
        rec_lay.setContentsMargins(4, 4, 4, 4)
        self.dash_recent = QLabel(DASH_NO_RECENT)
        self.dash_recent.setWordWrap(True)
        self.dash_recent.setFont(_theme.mono_font())
        rec_lay.addWidget(self.dash_recent)
        brow.addWidget(rec_box, 1)
        act_box = QGroupBox("Activity Log")
        act_lay = QVBoxLayout(act_box)
        act_lay.setContentsMargins(4, 4, 4, 4)
        self.dash_activity = QLabel(DASH_NO_ACTIVITY)
        self.dash_activity.setWordWrap(True)
        self.dash_activity.setFont(_theme.mono_font())
        act_lay.addWidget(self.dash_activity)
        brow.addWidget(act_box, 1)
        lay.addLayout(brow)

        bottom = QHBoxLayout()
        self.btn_dash_refresh = QPushButton("Refresh dashboard")
        self.btn_dash_refresh.clicked.connect(
            lambda: self.refresh_dashboard())
        bottom.addWidget(self.btn_dash_refresh)
        self.dash_asof = QLabel("")
        self.dash_asof.setObjectName("headerStats")
        self.dash_asof.setWordWrap(True)
        bottom.addWidget(self.dash_asof, 1)
        lay.addLayout(bottom)

        dock.setWidget(body)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.dashboard_dock = dock

    def _on_dash_filter(self, *_a):
        """Filter combo changed: re-render the cached rows (no I/O)."""
        self.dash_dir = self.dash_cmb_dir.currentText()
        self.dash_market = self.dash_cmb_market.currentText()
        self.dash_horizon = self.dash_cmb_horizon.currentText()
        self.dash_sort = self.dash_cmb_sort.currentText()
        self._render_dashboard()

    def dashboard_cards(self):
        """Current (title, value, sub) cards from the cached pair."""
        return format_dashboard_cards(*self.dash_cards_pair)

    def dashboard_cards_text(self):
        """Card titles + values + subs as text (test seam)."""
        return "\n".join(f"{t}\n{v}\n{s}"
                         for t, v, s in self.dashboard_cards())

    def dashboard_text(self):
        """Full dashboard page text (banner + cards + table + stamp)."""
        bits = [self.dash_banner.text(), self.dashboard_cards_text()]
        for r in range(self.dash_table.rowCount()):
            for c in range(self.dash_table.columnCount()):
                item = self.dash_table.item(r, c)
                bits.append(item.text() if item is not None else "")
        bits.append(self.dash_asof.text())
        return "\n".join(bits)

    def refresh_dashboard(self, now=None):
        """Re-read snapshots + active setups, then render (contained).

        Snapshot/journal reads are post-cut only (read-time ts-vs-cut_ts
        filter). A missing/locked DB keeps withheld-comparison cards and
        an empty table with an honest stamp — never a raise, never a
        touch of the scanner widgets.
        """
        now = int(time.time() if now is None else now)
        self._dash_last_now = now  # shared stamp: pages reuse this tick
        try:
            self.dash_cards_pair = dashboard_snapshot_pair(self.db,
                                                           now=now)
            try:
                panel = watcher_feed_mod.load_panel(self.db)
            except Exception:
                panel = None
            self.dash_rows = build_dashboard_rows(
                panel, market=self.dash_market_data, now=now,
                meta=dashboard_setup_meta(self.db))
            if panel is None:
                self.dash_asof_text = (
                    "dashboard data unavailable — scanner intact")
            elif not self.dash_rows:
                self.dash_asof_text = SILENCE_VALID
            else:
                asof = getattr(panel, "asof_ts", 0) or now
                self.dash_asof_text = f"data as of {asof}"
        except Exception:
            self.dash_cards_pair = (None, None)
            self.dash_rows = []
            self.dash_asof_text = (
                "dashboard data unavailable — scanner intact")
        self._render_dashboard()

    def _render_dashboard(self):
        """Render cached cards + filtered rows (no I/O, never raises)."""
        try:
            cards = format_dashboard_cards(*self.dash_cards_pair)
            for i, (_title, value, sub) in enumerate(cards):
                if i < len(self.dash_card_value):
                    self.dash_card_value[i].setText(value)
                    self.dash_card_sub[i].setText(sub)
            rows = filter_dashboard_rows(
                self.dash_rows, dir=self.dash_dir,
                market=self.dash_market, horizon=self.dash_horizon,
                sort=self.dash_sort)
            table = self.dash_table
            self._refilling = True
            try:
                table.setRowCount(len(rows))
                for r, row in enumerate(rows):
                    price = ("n/a" if row["price"] is None
                             else f"{row['price']:.8g}")
                    chg = ("n/a" if row["change_24h_pct"] is None
                           else f"{row['change_24h_pct']:+.1f}%")
                    arrow = {"LONG": "▲", "SHORT": "▼"}.get(
                        row["direction"], "•")
                    pill = f"{arrow} {row['direction']}"
                    # Dashboard display pref (Task 7): compact shows the
                    # first analysis line only (default: both lines).
                    analysis = row["analysis"]
                    if getattr(self, "dash_compact", False):
                        analysis = analysis.split("\n")[0]
                    cells = (
                        (f"{row['coin']}\n{row['name']}", None),
                        (price, row["price"]),
                        (chg, row["change_24h_pct"]),
                        (analysis, None),
                        (row["evidence"], None),
                        (pill, None),
                        (row["expiry"], row["expiry_ts"]),
                    )
                    dir_fg = _theme.direction_foreground(row["direction"])
                    for c, (text, number) in enumerate(cells):
                        anchor = "w"
                        if DASH_TABLE_COLUMNS[c] in ("price", "ch24",
                                                     "expiry"):
                            anchor = "e"
                        elif DASH_TABLE_COLUMNS[c] == "dir":
                            anchor = "center"
                        bg = None
                        fg = None
                        if DASH_TABLE_COLUMNS[c] == "dir" and dir_fg:
                            bg = (_theme.LONG_BG
                                  if row["direction"] == "LONG"
                                  else _theme.SHORT_BG)
                            fg = dir_fg
                        item = _CellItem(text, number, _align(anchor), bg,
                                         fg)
                        item.setToolTip(text.split("\n")[0])
                        if c == 0:
                            item.setData(Qt.ItemDataRole.UserRole,
                                         row["id"])
                        table.setItem(r, c, item)
            finally:
                self._refilling = False
            self.dash_asof.setText(getattr(self, "dash_asof_text", ""))
        except Exception:
            try:
                self.dash_asof.setText(
                    "dashboard data unavailable — scanner intact")
            except Exception:
                pass
        # The six pages render from the same caches (cheap, local only).
        try:
            self.refresh_dashboard_pages(now=getattr(self, "_dash_last_now",
                                                     None))
        except Exception:
            pass

    # --------------------------------------- dashboard pages (Task 7)
    def _build_dashboard_pages_dock(self):
        """Dashboard pages dock (additive; everything above untouched).

        Six tabs — Dashboard (summary mirror of the Task 5 dock),
        Watchlist (starred, meta-persisted), Signals (full ledger +
        history + filters), Analysis (indicator board + coverage +
        dev-labeled event-study N/kill), Trades (hypothetical-only
        ledger + header), Settings (display prefs + pause/disable via
        the watcher_feed writers; no threshold/leverage/stake editing).
        All reads are local + read-only; writes are contained meta
        writes that degrade to an honest note, never a raise.
        """
        dock = QDockWidget("Workspace — Dashboard / Watchlist / Analysis", self)
        dock.setObjectName("dashPagesDock")
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        self.dash_pages = QTabWidget()
        self.dash_tab = {}
        for title in DASH_PAGE_TITLES:
            page = QWidget()
            pv = QVBoxLayout(page)
            pv.setContentsMargins(4, 4, 4, 4)
            pv.setSpacing(4)
            self.dash_pages.addTab(page, title)
            self.dash_tab[title] = page
        lay.addWidget(self.dash_pages, 1)
        self.dash_pages.currentChanged.connect(
            lambda *_: self.refresh_dashboard_pages())

        self._build_dash_summary_tab()
        self._build_dash_star_tab()
        self._build_dash_signals_tab()
        self._build_dash_analysis_tab()
        self._build_dash_trades_tab()
        self._build_dash_settings_tab()

        dock.setWidget(body)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.dash_pages_dock = dock
        # Display prefs persist under dash_pref_* keys (fresh DB: off).
        try:
            self.dash_autorefresh = dash_read_pref(self.db,
                                                   DASH_PREF_AUTOREFRESH)
            self.dash_compact = dash_read_pref(self.db,
                                               DASH_PREF_COMPACT)
        except Exception:
            pass

    def _build_dash_summary_tab(self):
        page = self.dash_tab["Dashboard"]
        lay = page.layout()
        self.dash_summary = QLabel("—")
        self.dash_summary.setWordWrap(True)
        self.dash_summary.setFont(_theme.mono_font())
        lay.addWidget(self.dash_summary, 1)
        hint = QLabel("Full cards and the Top Signals table live in the"
                      " Dashboard dock.")
        hint.setWordWrap(True)
        hint.setObjectName("headerStats")
        lay.addWidget(hint)

    def _build_dash_star_tab(self):
        page = self.dash_tab["Watchlist"]
        lay = page.layout()
        self.dash_star_table = self._make_table(
            STAR_TABLE_COLUMNS, STAR_TABLE_HEADINGS, STAR_TABLE_WIDTHS)
        lay.addWidget(self.dash_star_table, 1)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.dash_star_edit = QLineEdit()
        self.dash_star_edit.setPlaceholderText("COIN to star")
        self.dash_star_edit.setFixedWidth(140)
        row.addWidget(self.dash_star_edit)
        self.btn_dash_star = QPushButton("Star")
        self.btn_dash_star.clicked.connect(
            lambda: self._on_dash_star(True))
        row.addWidget(self.btn_dash_star)
        self.btn_dash_unstar = QPushButton("Unstar")
        self.btn_dash_unstar.clicked.connect(
            lambda: self._on_dash_star(False))
        row.addWidget(self.btn_dash_unstar)
        self.dash_star_note = QLabel("")
        self.dash_star_note.setWordWrap(True)
        self.dash_star_note.setObjectName("headerStats")
        row.addWidget(self.dash_star_note, 1)
        lay.addLayout(row)

    def _build_dash_signals_tab(self):
        page = self.dash_tab["Signals"]
        lay = page.layout()
        filt = QHBoxLayout()
        filt.setSpacing(4)
        filt.addWidget(QLabel("Direction"))
        self.dash_sig_cmb_dir = QComboBox()
        self.dash_sig_cmb_dir.addItems(list(SIG_DIRS))
        self.dash_sig_cmb_dir.currentTextChanged.connect(
            lambda *_: self._on_dash_sig_filter())
        filt.addWidget(self.dash_sig_cmb_dir)
        self.dash_sig_history = QCheckBox("Show resolved history")
        self.dash_sig_history.setChecked(True)
        self.dash_sig_history.toggled.connect(
            lambda *_: self._on_dash_sig_filter())
        filt.addWidget(self.dash_sig_history)
        filt.addStretch(1)
        lay.addLayout(filt)
        self.dash_sig_table = self._make_table(
            SIG_TABLE_COLUMNS, SIG_TABLE_HEADINGS, SIG_TABLE_WIDTHS)
        lay.addWidget(self.dash_sig_table, 1)
        self.dash_sig_summary = QLabel("")
        self.dash_sig_summary.setWordWrap(True)
        self.dash_sig_summary.setObjectName("headerStats")
        lay.addWidget(self.dash_sig_summary)

    def _build_dash_analysis_tab(self):
        page = self.dash_tab["Analysis"]
        lay = page.layout()
        board_box = QGroupBox("Indicator board — latest closed-bar eval")
        bv = QVBoxLayout(board_box)
        bv.setContentsMargins(4, 4, 4, 4)
        self.dash_board_table = self._make_table(
            BOARD_TABLE_COLUMNS, BOARD_TABLE_HEADINGS, BOARD_TABLE_WIDTHS)
        bv.addWidget(self.dash_board_table)
        lay.addWidget(board_box, 2)
        self.dash_coverage = QLabel("")
        self.dash_coverage.setWordWrap(True)
        self.dash_coverage.setFont(_theme.mono_font())
        lay.addWidget(self.dash_coverage)
        study_box = QGroupBox("Event-study (dev)")
        sv = QVBoxLayout(study_box)
        sv.setContentsMargins(4, 4, 4, 4)
        self.dash_eventstudy = QLabel("")
        self.dash_eventstudy.setWordWrap(True)
        self.dash_eventstudy.setFont(_theme.mono_font())
        sv.addWidget(self.dash_eventstudy)
        lay.addWidget(study_box, 1)

    def _build_dash_trades_tab(self):
        page = self.dash_tab["Trades"]
        lay = page.layout()
        self.dash_trades_head = QLabel(TRADES_HEADER)
        self.dash_trades_head.setWordWrap(True)
        self.dash_trades_head.setFont(
            _theme.ui_font(_theme.BASE_POINT_SIZE + 1, bold=True))
        lay.addWidget(self.dash_trades_head)
        self.dash_trades_sub = QLabel(TRADES_SUB)
        self.dash_trades_sub.setWordWrap(True)
        self.dash_trades_sub.setObjectName("headerStats")
        lay.addWidget(self.dash_trades_sub)
        for widget in (self.dash_trades_head, self.dash_trades_sub):
            try:
                assert_clean(widget.text())
            except ValueError:
                pass
        self.dash_trades_table = self._make_table(
            TRADES_TABLE_COLUMNS, TRADES_TABLE_HEADINGS,
            TRADES_TABLE_WIDTHS)
        lay.addWidget(self.dash_trades_table, 1)

    def _build_dash_settings_tab(self):
        page = self.dash_tab["Settings"]
        lay = page.layout()
        pause_box = QGroupBox("Watcher pause")
        pv = QVBoxLayout(pause_box)
        pv.setContentsMargins(4, 4, 4, 4)
        self.dash_set_paused = QCheckBox("Pause watcher")
        self.dash_set_paused.toggled.connect(
            lambda on: self._on_dash_pause(bool(on)))
        pv.addWidget(self.dash_set_paused)
        lay.addWidget(pause_box)
        trig_box = QGroupBox("Trigger switches")
        tv = QVBoxLayout(trig_box)
        tv.setContentsMargins(4, 4, 4, 4)
        self.dash_trig_layout = tv
        self.dash_set_rules = {}
        for rule in DASH_KNOWN_RULES:
            box = QCheckBox(f"Disable {rule}")
            box.toggled.connect(
                lambda on, r=rule: self._on_dash_rule(r, bool(on)))
            tv.addWidget(box)
            self.dash_set_rules[rule] = box
        lay.addWidget(trig_box)
        disp_box = QGroupBox("Display")
        dv = QVBoxLayout(disp_box)
        dv.setContentsMargins(4, 4, 4, 4)
        self.dash_set_autorefresh = QCheckBox(
            "Refresh dashboard pages with each scan result")
        self.dash_set_autorefresh.toggled.connect(
            lambda on: self._on_dash_pref(DASH_PREF_AUTOREFRESH,
                                          bool(on)))
        dv.addWidget(self.dash_set_autorefresh)
        self.dash_set_compact = QCheckBox(
            "Compact analysis lines (first line only)")
        self.dash_set_compact.toggled.connect(
            lambda on: self._on_dash_pref(DASH_PREF_COMPACT, bool(on)))
        dv.addWidget(self.dash_set_compact)
        lay.addWidget(disp_box)
        self.dash_set_note = QLabel("")
        self.dash_set_note.setWordWrap(True)
        self.dash_set_note.setObjectName("headerStats")
        lay.addWidget(self.dash_set_note)
        lay.addStretch(1)

    # ------------------------------------------------ page data + render
    def refresh_dashboard_pages(self, now=None):
        """Re-read page data (local, read-only) and render all six pages.

        Contained: a missing/locked DB keeps honest fallback copy on
        every page, never a raise, never a touch of the scanner widgets.
        """
        if getattr(self, "dash_pages", None) is None:
            return
        now = int(time.time() if now is None else now)
        self._dash_last_now = now
        try:
            self.dash_movers_cache = dashboard_movers(
                self.dash_market_data)
            events = dashboard_recent_events(
                self.db, limit=max(DASH_ACTIVITY_N, DASH_RECENT_N))
            kinds = ("emission", "invalidation", "expiry")
            self.dash_recent_cache = [e for e in events
                                      if e.get("kind") in kinds
                                      ][:DASH_RECENT_N]
            self.dash_activity_cache = list(events)[:DASH_ACTIVITY_N]
            self.dash_ledger = dashboard_all_setups(self.db)
            self.dash_evals = dashboard_latest_evals(self.db)
            self.dash_starred_cache = dashboard_starred(self.db)
            try:
                panel = watcher_feed_mod.load_panel(self.db)
            except Exception:
                panel = None
            self._dash_panel_cache = panel
            ro = _dash_open_ro(self.db)
            try:
                if ro is None:
                    self.dash_paused_cache = False
                    self.dash_disabled_cache = ()
                else:
                    paused, disabled = watcher_feed_mod.get_flags(ro)
                    self.dash_paused_cache = bool(paused)
                    self.dash_disabled_cache = tuple(disabled or ())
            finally:
                try:
                    ro.close()
                except Exception:
                    pass
        except Exception:
            self.dash_movers_cache = {"gainers": [], "losers": [],
                                      "high_volume": []}
            self.dash_recent_cache = []
            self.dash_activity_cache = []
            self.dash_ledger = []
            self.dash_evals = {}
            self.dash_starred_cache = set()
            self._dash_panel_cache = None
        self._render_bottom()
        self._render_dash_summary()
        self._render_star_page()
        self._render_signals_page()
        self._render_analysis_page()
        self._render_trades_page()
        self._render_settings_page()

    def _render_bottom(self):
        """Bottom row: movers + recent analysis + activity log."""
        try:
            movers = self.dash_movers_cache or {}
            bits = []
            for title, key in (("Gainers", "gainers"), ("Losers", "losers"),
                               ("High Volume", "high_volume")):
                bits.append(title)
                bits.extend(f"{c} {t}" for c, t in (movers.get(key) or []))
            self.dash_movers.setText("\n".join(bits) if any(
                movers.get(k) for k in ("gainers", "losers",
                                        "high_volume"))
                else DASH_NO_QUOTES)
            if self.dash_recent_cache:
                self.dash_recent.setText("\n".join(
                    f"{e.get('kind')} {e.get('coin')} — {e.get('text')}"
                    for e in self.dash_recent_cache))
            else:
                self.dash_recent.setText(DASH_NO_RECENT)
            if self.dash_activity_cache:
                self.dash_activity.setText("\n".join(
                    f"{e.get('kind')} {e.get('coin')} — {e.get('text')}"
                    for e in self.dash_activity_cache))
            else:
                self.dash_activity.setText(DASH_NO_ACTIVITY)
            for widget in (self.dash_movers, self.dash_recent,
                           self.dash_activity):
                assert_clean(widget.text())
        except Exception:
            pass

    def bottom_text(self):
        """Bottom-row text: group titles + movers + recent + activity."""
        try:
            return "\n".join(("Market Movers", self.dash_movers.text(),
                               "Recent Analysis", self.dash_recent.text(),
                               "Activity Log", self.dash_activity.text()))
        except (AttributeError, RuntimeError):
            return ""

    def _render_dash_summary(self):
        try:
            lines = [f"{t}: {v} ({s})"
                     for t, v, s in self.dashboard_cards()]
            lines.append(getattr(self, "dash_asof_text", ""))
            text = "\n".join(lines)
            assert_clean(text)
            self.dash_summary.setText(text)
        except Exception:
            pass

    def _render_star_page(self):
        """Starred-coins table from the meta-persisted star set."""
        try:
            table = self.dash_star_table
            starred = sorted(self.dash_starred_cache or ())
            active = {}
            try:
                for row in (self.dash_rows or []):
                    if row.get("coin") in starred:
                        active[row["coin"]] = row
            except Exception:
                active = {}
            self._refilling = True
            try:
                table.setRowCount(len(starred))
                for r, coin in enumerate(starred):
                    quote = (self.dash_market_data or {}).get(coin) or {}
                    row = active.get(coin) or {}
                    price = quote.get("price")
                    chg = quote.get("change_24h_pct")
                    setup = (f"{row.get('direction', '')} via"
                             f" {row.get('id', '').split(':')[0]}"
                             if row else "no active setup")
                    cells = (
                        coin, str(quote.get("name", coin)),
                        "n/a" if price is None else f"{price:.8g}",
                        "n/a" if chg is None else f"{chg:+.1f}%",
                        setup)
                    for c, text in enumerate(cells):
                        item = _CellItem(text)
                        item.setToolTip(text.split("\n")[0])
                        table.setItem(r, c, item)
            finally:
                self._refilling = False
        except Exception:
            pass

    def watchlist_text(self):
        """Starred-page table text (test seam)."""
        try:
            bits = []
            for r in range(self.dash_star_table.rowCount()):
                for c in range(self.dash_star_table.columnCount()):
                    item = self.dash_star_table.item(r, c)
                    bits.append(item.text() if item is not None else "")
            text = "\n".join(bits)
            return text if text.strip() else DASH_NO_STARS
        except (AttributeError, RuntimeError):
            return DASH_NO_STARS

    def set_dashboard_star(self, coin, starred):
        """Star/unstar a coin (meta-persisted; contained, honest note)."""
        coin = str(coin or "").strip().upper()
        if not coin:
            try:
                self.dash_star_note.setText("enter a coin first")
            except (AttributeError, RuntimeError):
                pass
            return
        ok = dash_write_meta(self.db, DASH_STAR_PREFIX + coin,
                             "1" if starred else "0")
        try:
            self.dash_star_note.setText(
                f"{coin} {'starred' if starred else 'unstarred'}"
                if ok else f"{coin} not saved — database busy")
        except (AttributeError, RuntimeError):
            pass
        self.refresh_dashboard_pages()

    def _on_dash_star(self, starred):
        try:
            coin = self.dash_star_edit.text()
        except (AttributeError, RuntimeError):
            return
        self.set_dashboard_star(coin, starred)

    def _on_dash_sig_filter(self, *_a):
        try:
            self.dash_sig_dir = self.dash_sig_cmb_dir.currentText()
            self.dash_sig_history_on = self.dash_sig_history.isChecked()
        except (AttributeError, RuntimeError):
            return
        try:
            self._render_signals_page()
        except (AttributeError, RuntimeError):
            pass

    def _render_signals_page(self):
        """Full setup ledger + history counts (newest-first)."""
        try:
            rows = list(self.dash_ledger or [])
            if self.dash_sig_dir != "All":
                rows = [r for r in rows
                        if str(r.get("direction", "")).upper()
                        == self.dash_sig_dir.upper()]
            if not self.dash_sig_history_on:
                rows = [r for r in rows
                        if str(r.get("state", "")).lower() == "active"]
            table = self.dash_sig_table
            self._refilling = True
            try:
                table.setRowCount(len(rows))
                for r, row in enumerate(rows):
                    state = str(row.get("state", "active"))
                    src = row.get("src_bar_ts")
                    cells = (str(row.get("coin", "?")),
                             str(row.get("rule_id", "?")),
                             str(row.get("direction", "?")).upper(),
                             state,
                             "n/a" if src is None else f"src bar {src}")
                    for c, text in enumerate(cells):
                        assert_clean(text)
                        item = _CellItem(text)
                        item.setToolTip(text.split("\n")[0])
                        table.setItem(r, c, item)
            finally:
                self._refilling = False
            counts = {}
            for row in (self.dash_ledger or []):
                key = str(row.get("state", "active"))
                counts[key] = counts.get(key, 0) + 1
            summary = (f"ledger {len(self.dash_ledger or [])} setups"
                       + (" — " + " · ".join(
                           f"{k}: {counts[k]}" for k in sorted(counts))
                           if counts else ""))
            assert_clean(summary)
            self.dash_sig_summary.setText(summary)
        except Exception:
            pass

    def signals_text(self):
        """Signals-page table + summary text (test seam)."""
        try:
            bits = []
            for r in range(self.dash_sig_table.rowCount()):
                for c in range(self.dash_sig_table.columnCount()):
                    item = self.dash_sig_table.item(r, c)
                    bits.append(item.text() if item is not None else "")
            bits.append(self.dash_sig_summary.text())
            return "\n".join(bits)
        except (AttributeError, RuntimeError):
            return ""

    def _render_analysis_page(self):
        """Indicator board + coverage + dev-labeled event-study block."""
        try:
            evals = self.dash_evals or {}
            setups = {s.get("id"): s for s in (self.dash_ledger or [])
                      if s.get("id")}
            ids = sorted(setups) or sorted(evals)
            table = self.dash_board_table
            self._refilling = True
            try:
                table.setRowCount(len(ids))
                for r, sid in enumerate(ids):
                    ev = evals.get(sid) or {}
                    coin = (setups.get(sid) or {}).get("coin", sid)
                    bar = ev.get("bar_ts")
                    cells = (
                        str(coin),
                        "n/a" if bar is None else f"bar {bar}",
                        str(ev.get("monitoring", "n/a")),
                        _dash_ret_text(ev.get("ret_gross")),
                        _dash_ret_text(ev.get("ret_net")),
                        str(ev.get("note") or "n/a"))
                    for c, text in enumerate(cells):
                        assert_clean(text)
                        item = _CellItem(text)
                        item.setToolTip(text.split("\n")[0])
                        table.setItem(r, c, item)
            finally:
                self._refilling = False
            panel = getattr(self, "_dash_panel_cache", None)
            details = (getattr(panel, "by_id", None) or {}) if panel \
                else {}
            if details:
                ok = sum(1 for d in details.values()
                         if (d or {}).get("monitoring", "ok") == "ok")
                total = len(details)
                pct = (100.0 * ok / total) if total else 0.0
                unav = sum(1 for d in details.values()
                           if (d or {}).get("monitoring") == "unavailable")
                coverage = (f"monitoring coverage {ok}/{total} ok"
                            f" ({pct:.1f}%) · unavailable: {unav}")
            else:
                coverage = ("monitoring coverage unavailable — "
                            "no setups read")
            assert_clean(coverage)
            self.dash_coverage.setText(coverage)
            study = "\n".join((
                "event-study (dev only — not a result)",
                f"N grid {', '.join(str(h) for h in N_GRID)} bars of 5m"
                " (1h/4h/8h/24h);"
                f" kill horizons {', '.join(str(h) for h in KILL_HORIZONS)}",
                "no stored event-study result — rows above are marked"
                " returns, not study outcomes"))
            assert_clean(study)
            self.dash_eventstudy.setText(study)
        except Exception:
            pass

    def analysis_text(self):
        """Analysis-page text: board + coverage + event-study (test seam)."""
        try:
            bits = []
            for r in range(self.dash_board_table.rowCount()):
                for c in range(self.dash_board_table.columnCount()):
                    item = self.dash_board_table.item(r, c)
                    bits.append(item.text() if item is not None else "")
            bits.append(self.dash_coverage.text())
            bits.append(self.dash_eventstudy.text())
            return "\n".join(bits)
        except (AttributeError, RuntimeError):
            return ""

    def _render_trades_page(self):
        """Hypothetical-only ledger: setups + reference plans + returns."""
        try:
            rows = [s for s in (self.dash_ledger or [])
                    if str(s.get("state", "")).lower() == "active"]
            table = self.dash_trades_table
            self._refilling = True
            try:
                table.setRowCount(len(rows))
                for r, setup in enumerate(rows):
                    risk = "n/a"
                    try:
                        entry = float(setup.get("entry"))
                        stop = float(setup.get("stop_px"))
                        tgt = float(setup.get("tgt_px"))
                        plan = reference_plan(
                            {"entry_low": entry, "entry_high": entry,
                             "stop": stop, "tp1": tgt, "tp2": tgt})
                        risk = f"{plan['stop_risk']:.2f} USDT"
                        entry_t = f"{entry:.8g}"
                        stop_t = f"{stop:.8g}"
                        tgt_t = f"{tgt:.8g}"
                    except (TypeError, ValueError):
                        entry_t = stop_t = tgt_t = "n/a"
                    ev = (self.dash_evals or {}).get(setup.get("id")) or {}
                    gross = _dash_ret_text(ev.get("ret_gross"))
                    net = _dash_ret_text(ev.get("ret_net"))
                    ret = ("pending" if gross == "pending" and net == "pending"
                           else f"gross {gross} · net {net}")
                    cells = (str(setup.get("coin", "?")),
                             str(setup.get("direction", "?")).upper(),
                             entry_t, stop_t, tgt_t, risk, ret,
                             str(setup.get("state", "active")))
                    for c, text in enumerate(cells):
                        assert_clean(text)
                        item = _CellItem(text)
                        item.setToolTip(text.split("\n")[0])
                        table.setItem(r, c, item)
            finally:
                self._refilling = False
        except Exception:
            pass

    def trades_text(self):
        """Trades-page text: header + ledger (test seam)."""
        try:
            bits = [self.dash_trades_head.text(),
                    self.dash_trades_sub.text()]
            for r in range(self.dash_trades_table.rowCount()):
                for c in range(self.dash_trades_table.columnCount()):
                    item = self.dash_trades_table.item(r, c)
                    bits.append(item.text() if item is not None else "")
            return "\n".join(bits)
        except (AttributeError, RuntimeError):
            return TRADES_HEADER + "\n" + TRADES_SUB

    def _render_settings_page(self):
        """Sync pause/rule/pref controls from meta (signals blocked)."""
        try:
            rules = list(DASH_KNOWN_RULES)
            for setup in (self.dash_ledger or []):
                rule = setup.get("rule_id")
                if rule and rule not in rules:
                    rules.append(rule)
            for rule in rules:
                if rule not in self.dash_set_rules:
                    box = QCheckBox(f"Disable {rule}")
                    box.toggled.connect(
                        lambda on, r=rule: self._on_dash_rule(r, bool(on)))
                    try:
                        self.dash_trig_layout.addWidget(box)
                    except (AttributeError, RuntimeError):
                        pass
                    self.dash_set_rules[rule] = box
            for box in [self.dash_set_paused, self.dash_set_autorefresh,
                        self.dash_set_compact,
                        *self.dash_set_rules.values()]:
                box.blockSignals(True)
            try:
                self.dash_set_paused.setChecked(bool(
                    self.dash_paused_cache))
                for rule, box in self.dash_set_rules.items():
                    box.setChecked(rule in (self.dash_disabled_cache or ()))
                self.dash_set_autorefresh.setChecked(bool(
                    self.dash_autorefresh))
                self.dash_set_compact.setChecked(bool(self.dash_compact))
            finally:
                for box in [self.dash_set_paused,
                            self.dash_set_autorefresh,
                            self.dash_set_compact,
                            *self.dash_set_rules.values()]:
                    box.blockSignals(False)
        except (AttributeError, RuntimeError):
            pass

    def dash_settings_labels(self):
        """Settings-tab labels + control texts (test seam)."""
        try:
            page = self.dash_tab["Settings"]
            bits = [w.text() for w in page.findChildren(QLabel)]
            bits += [w.text() for w in page.findChildren(QCheckBox)]
            bits += [w.title() for w in page.findChildren(QGroupBox)]
            bits.append(self.dash_set_note.text())
            return "\n".join(bits)
        except (AttributeError, RuntimeError):
            return ""

    def dash_pages_text(self):
        """All six pages as text (test seam)."""
        try:
            return "\n".join((self.dash_summary.text(),
                               self.watchlist_text(), self.signals_text(),
                               self.analysis_text(), self.trades_text(),
                               self.dash_settings_labels()))
        except (AttributeError, RuntimeError):
            return ""

    def _dash_open_rw(self):
        """Short-timeout read-write handle for settings writes, or None."""
        if not getattr(self, "db", None):
            return None
        try:
            conn = sqlite3.connect(self.db, timeout=5.0)
            conn.execute("SELECT 1")
            return conn
        except Exception:
            return None

    def _on_dash_pause(self, paused):
        """Pause/resume switch: the watcher_feed writer, same key panels."""
        conn = self._dash_open_rw()
        note = ""
        if conn is None:
            note = "pause not saved — database unavailable"
        else:
            try:
                watcher_feed_mod.set_paused(conn, bool(paused))
                self.dash_paused_cache = bool(paused)
                note = ("watcher paused — setups listed but not acted on"
                        if paused else "watcher resumed")
            except Exception:
                note = "pause not saved — database busy"
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        try:
            self.dash_set_note.setText(note)
            assert_clean(note)
        except (AttributeError, RuntimeError, ValueError):
            pass
        self.refresh_dashboard_pages()

    def _on_dash_rule(self, rule, disabled):
        """Per-trigger switch: the watcher_feed writer, same key panels."""
        conn = self._dash_open_rw()
        note = ""
        if conn is None:
            note = "trigger switch not saved — database unavailable"
        else:
            try:
                watcher_feed_mod.set_rule_disabled(conn, rule,
                                                   bool(disabled))
                flags = set(self.dash_disabled_cache or ())
                if disabled:
                    flags.add(rule)
                else:
                    flags.discard(rule)
                self.dash_disabled_cache = tuple(sorted(flags))
                note = (f"{rule} disabled" if disabled
                        else f"{rule} enabled")
            except Exception:
                note = "trigger switch not saved — database busy"
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        try:
            self.dash_set_note.setText(note)
            assert_clean(note)
        except (AttributeError, RuntimeError, ValueError):
            pass
        self.refresh_dashboard_pages()

    def _on_dash_pref(self, key, on):
        """Display pref toggle: persist to meta, apply immediately."""
        ok = dash_write_meta(self.db, key, "1" if on else "0")
        if key == DASH_PREF_AUTOREFRESH:
            self.dash_autorefresh = bool(on)
        elif key == DASH_PREF_COMPACT:
            self.dash_compact = bool(on)
            try:
                self._render_dashboard()
            except (AttributeError, RuntimeError):
                pass
        try:
            self.dash_set_note.setText(
                "display preference saved"
                if ok else "preference not saved — database busy")
        except (AttributeError, RuntimeError):
            pass

    # ------------------------------------------------ coin detail (Task 6)
    def _build_detail_dock(self):
        """Coin detail dock (additive; rail/scanner/dashboard untouched).

        Timeframe tabs 5m/15m/1H/4H/1D (no 1m — unsupported by the venue
        adapter) with one candle+EMA50/200+volume chart each, validity +
        next-update lines, and Analysis/Trade Plan/Metrics tabs. Coins
        with no active setup show "no setup yet" + blocking reason in
        the same panel. All reads are local + read-only; chart bars
        arrive through the queue->poll path, per-tab failures render
        "bars unavailable" for that tab only.
        """
        dock = QDockWidget("Coin detail — chart + plan (research)", self)
        dock.setObjectName("detailDock")
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        self.detail_head = QLabel("—")
        self.detail_head.setObjectName("detailTitle")
        self.detail_head.setFont(_theme.ui_font(_theme.BASE_POINT_SIZE + 2,
                                                bold=True))
        lay.addWidget(self.detail_head)
        self.detail_sub = QLabel("select a coin for the full breakdown")
        self.detail_sub.setObjectName("detailSubtitle")
        self.detail_sub.setWordWrap(True)
        lay.addWidget(self.detail_sub)

        self.detail_tf_tabs = QTabWidget()
        self.detail_charts = {}
        for tf in DETAIL_TIMEFRAMES:
            chart = DetailChartWidget()
            self.detail_charts[tf] = chart
            self.detail_tf_tabs.addTab(chart, tf)
        self.detail_tf_tabs.currentChanged.connect(
            self._on_detail_tab_changed)
        lay.addWidget(self.detail_tf_tabs, 3)

        self.detail_validity = QLabel("")
        self.detail_validity.setObjectName("headerStats")
        self.detail_validity.setWordWrap(True)
        lay.addWidget(self.detail_validity)
        self.detail_next = QLabel("")
        self.detail_next.setObjectName("headerStats")
        self.detail_next.setWordWrap(True)
        lay.addWidget(self.detail_next)

        self.detail_low_tabs = QTabWidget()
        self.detail_analysis = QTextEdit()
        self.detail_plan = QTextEdit()
        self.detail_metrics_view = QTextEdit()
        for view in (self.detail_analysis, self.detail_plan,
                     self.detail_metrics_view):
            view.setReadOnly(True)
            view.setFont(_theme.mono_font(_theme.DETAIL_POINT_SIZE))
        self.detail_low_tabs.addTab(self.detail_analysis, "Analysis")
        self.detail_low_tabs.addTab(self.detail_plan, "Trade Plan")
        self.detail_low_tabs.addTab(self.detail_metrics_view, "Metrics")
        lay.addWidget(self.detail_low_tabs, 2)

        self.detail_asof = QLabel("")
        self.detail_asof.setObjectName("headerStats")
        self.detail_asof.setWordWrap(True)
        lay.addWidget(self.detail_asof)

        dock.setWidget(body)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.detail_dock = dock

    def detail_timeframes(self):
        """Chart tab intervals, in display order (test seam)."""
        return [self.detail_tf_tabs.tabText(i)
                for i in range(self.detail_tf_tabs.count())]

    def _detail_panel(self):
        """Current watcher panel, or None when the DB is unreadable."""
        try:
            return watcher_feed_mod.load_panel(self.db)
        except Exception:
            return None

    def _detail_current_interval(self):
        try:
            return self.detail_tf_tabs.tabText(
                self.detail_tf_tabs.currentIndex())
        except Exception:
            return DETAIL_TIMEFRAMES[0]

    def show_coin_detail(self, coin):
        """Select *coin* in the detail dock and render from local state.

        Setup info renders immediately (read-only panel read); the
        current timeframe tab fetches live bars on a background thread
        only when the coin has an active setup and no cached bars.
        Never raises, never blocks, never touches scanner widgets.
        """
        self.detail_coin = coin
        panel = self._detail_panel()
        _sid, _detail = detail_setup_for_coin(panel, coin)
        self.detail_setup_id = _sid
        self._render_detail_dock(panel)
        if _sid is not None:
            interval = self._detail_current_interval()
            if (coin, interval) not in self.detail_cache:
                self._fetch_detail_bars(coin, interval)

    def show_detail_bars(self, coin, interval, bars):
        """Test/fixture seam: set *bars* for (coin, interval) and render.

        Empty/None bars mark that tab "bars unavailable". Invalidates
        any in-flight fetch for the same key so a late arrival can
        never clobber these bars.
        """
        if interval not in DETAIL_TIMEFRAMES:
            return
        if coin != self.detail_coin:
            self.detail_coin = coin
            self.detail_setup_id = detail_setup_for_coin(
                self._detail_panel(), coin)[0]
        self.detail_seq += 1
        self.detail_tokens[(coin, interval)] = self.detail_seq
        self.detail_cache[(coin, interval)] = list(bars) if bars else None
        self._render_detail_dock(self._detail_panel())

    def _on_detail_tab_changed(self, *_a):
        """Timeframe tab switched: render cache or fetch live bars."""
        coin = self.detail_coin
        if not coin or self.detail_setup_id is None:
            self._render_detail_dock(self._detail_panel())
            return
        interval = self._detail_current_interval()
        if (coin, interval) not in self.detail_cache:
            self._render_detail_dock(self._detail_panel())
            self._fetch_detail_bars(coin, interval)
        else:
            self._render_detail_dock(self._detail_panel())

    def _fetch_detail_bars(self, coin, interval):
        """Fetch one tab's bars off the UI thread (live venue, app only).

        Resolves the venue symbol from a fresh bulk snapshot, then
        pulls closed-bar klines; the result re-enters through the
        queue->poll path as a detail_bars message. Tests never call
        this (fixtures ride show_detail_bars instead).
        """
        key = (coin, interval)
        if key in self.detail_tokens and key in self.detail_pending_tokens():
            return
        self.detail_seq += 1
        token = self.detail_seq
        self.detail_tokens[key] = token

        def _job():
            try:
                from proto import mexc as mexc_mod
                from proto import scan as scan_mod
                tk = mexc_mod.tickers()
                det = mexc_mod.details()
                sym = scan_mod.find_symbol(coin, tk, det)
                if sym is None:
                    self._results.put({"kind": "detail_bars", "ok": False,
                                       "coin": coin, "interval": interval,
                                       "token": token,
                                       "error": "unknown symbol"})
                    return
                bars = fetch_detail_bars(sym, interval)
                self._results.put({"kind": "detail_bars", "ok": True,
                                   "coin": coin, "interval": interval,
                                   "token": token, "bars": bars or []})
            except Exception as e:
                try:
                    self._results.put({"kind": "detail_bars", "ok": False,
                                       "coin": coin, "interval": interval,
                                       "token": token,
                                       "error": f"{type(e).__name__}: {e}"})
                except Exception:
                    pass

        thread = threading.Thread(target=_job, daemon=True,
                                  name=f"detail-bars-{coin}-{interval}")
        thread.start()

    def detail_pending_tokens(self):
        """(coin, interval) keys with an answer still outstanding."""
        return {k for k, t in self.detail_tokens.items()
                if (k not in self.detail_cache
                    or self.detail_cache[k] == "pending")}

    def _on_detail_bars(self, msg):
        """Render a background chart fetch; stale arrivals are dropped."""
        coin, interval = msg.get("coin"), msg.get("interval")
        key = (coin, interval)
        if self.detail_tokens.get(key) != msg.get("token"):
            return  # a newer request (or fixture bars) won this tab
        if msg.get("ok") and msg.get("bars"):
            self.detail_cache[key] = list(msg["bars"])
        else:
            self.detail_cache[key] = None  # per-tab "bars unavailable"
        if coin == self.detail_coin:
            try:
                self._render_detail_dock(self._detail_panel())
            except Exception:
                pass

    def _on_dash_select(self, *_a):
        """Dashboard row selected: mirror the coin into the detail dock."""
        if getattr(self, "_refilling", False):
            return
        try:
            sel = (self.dash_table.selectionModel().selectedRows()
                   if self.dash_table.selectionModel() else [])
            if not sel:
                return
            item = self.dash_table.item(sel[0].row(), 0)
            coin = item.text().split("\n")[0] if item else None
            if coin:
                self.show_coin_detail(coin)
        except Exception:
            pass

    def _render_detail_dock(self, panel=None):
        """Render the detail dock from cached state (no I/O, never raises)."""
        try:
            coin = self.detail_coin
            if panel is None and coin is not None:
                panel = self._detail_panel()
            if not coin:
                self.detail_head.setText("—")
                self.detail_sub.setText(
                    "select a coin for the full breakdown")
                self.detail_analysis.setPlainText("")
                self.detail_plan.setPlainText("")
                self.detail_metrics_view.setPlainText("")
                self.detail_validity.setText("")
                self.detail_next.setText("")
                self.detail_asof.setText("")
                for chart in self.detail_charts.values():
                    chart.set_bars(None)
                self.detail_text_cache = "select a coin"
                return
            sid, detail = detail_setup_for_coin(panel, coin)
            self.detail_setup_id = sid
            interval = self._detail_current_interval()
            cached = self.detail_cache.get((coin, interval), "pending")
            bars = cached if isinstance(cached, list) else None
            for tf, chart in self.detail_charts.items():
                entry = self.detail_cache.get((coin, tf), "pending")
                chart.set_bars(entry if isinstance(entry, list) else None)
            if detail is None:
                self.detail_head.setText(coin)
                reason = blocking_reason(
                    coin, detail_blocking_ctx(panel, coin))
                assert_clean(reason)
                self.detail_sub.setText(reason)
                self.detail_analysis.setPlainText(reason)
                self.detail_plan.setPlainText(
                    "reference plan unavailable — no active setup")
                metrics_lines = [DETAIL_NO_BARS]
                self.detail_metrics_view.setPlainText(
                    "\n".join(metrics_lines))
                self.detail_validity.setText(
                    "validity unavailable — no active setup")
                self.detail_next.setText(
                    "next update on the next completed sweep "
                    "(no active setup)")
                self.detail_asof.setText(
                    f"data as of {getattr(panel, 'asof_ts', 0) or 'unavailable'}")
            else:
                direction = str(detail.get("direction", "?")).upper()
                arrow = {"LONG": "▲", "SHORT": "▼"}.get(direction, "•")
                self.detail_head.setText(f"{coin} {arrow} {direction}")
                setup = {"rule_id": detail.get("rule", "?"),
                         "direction": direction,
                         "criteria": detail.get("criteria", []),
                         "confirmations": detail.get("confirmations", [])}
                inputs = {}
                if detail.get("monitoring") is not None:
                    inputs["monitoring"] = detail["monitoring"]
                for missing in detail.get("missing") or []:
                    inputs[str(missing)] = None
                stamp = detail.get("last_bar_ts",
                                   detail.get("src_bar_ts"))
                if stamp is not None:
                    inputs["data_as_of"] = stamp
                line1, line2 = summarize_setup(setup, inputs)
                self.detail_sub.setText(line1)
                self.detail_analysis.setPlainText(f"{line1}\n{line2}")
                self.detail_plan.setPlainText(
                    detail_reference_plan_text(detail))
                metrics = detail_metrics(bars) if bars else detail_metrics([])
                self.detail_metrics_view.setPlainText(
                    "\n".join(format_detail_metrics(metrics)))
                meta = dashboard_setup_meta(self.db)
                hint = meta.get(sid) or {}
                n_bars = hint.get("N_bars")
                src_ts = hint.get("src_bar_ts")
                expiry_ts = setup_expiry_ts({"src_bar_ts": src_ts,
                                             "N_bars": n_bars})
                if expiry_ts is None:
                    self.detail_validity.setText(
                        "validity unavailable — expiry inputs missing")
                else:
                    self.detail_validity.setText(
                        f"valid until {expiry_ts} "
                        f"({format_expiry(expiry_ts)})")
                asof = getattr(panel, "asof_ts", 0) or "unavailable"
                self.detail_next.setText(
                    "next update on the next completed sweep "
                    f"(data as of {asof})")
                self.detail_asof.setText(f"data as of {asof}")
            self.detail_text_cache = self._compose_coin_detail_text()
        except Exception:
            try:
                self.detail_asof.setText(
                    "detail unavailable — scanner intact")
                self.detail_text_cache = "detail unavailable"
            except Exception:
                pass

    def _compose_coin_detail_text(self):
        """Current detail dock as text (test seam, all assert_clean)."""
        bits = [self.detail_head.text(), self.detail_sub.text(),
                self.detail_validity.text(), self.detail_next.text()]
        interval = self._detail_current_interval()
        cached = self.detail_cache.get((self.detail_coin, interval),
                                       "pending")
        if isinstance(cached, list) and cached:
            bits.append(f"{interval} chart: {len(cached)} closed bars")
        else:
            bits.append(f"{interval} chart: {DETAIL_NO_BARS}")
        bits += [self.detail_analysis.toPlainText(),
                 self.detail_plan.toPlainText(),
                 self.detail_metrics_view.toPlainText(),
                 self.detail_asof.text()]
        text = "\n".join(bits)
        assert_clean(text)  # fail-closed: banned wording never renders
        return text

    def detail_text(self, coin=None):
        """Detail pane text; with *coin*, select it first (test seam)."""
        if coin is not None:
            self.show_coin_detail(coin)
        APP = QApplication.instance()
        if APP is not None:
            APP.processEvents()
        return self._compose_coin_detail_text()

    def _fmt_ts(self, ts):
        try:
            return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
        except (TypeError, ValueError):
            return "?"

    # ------------------------------------------------------ widget helpers
    def _auto_label(self):
        return "Pause auto-scan" if self.auto_scan else "Resume auto-scan"

    def _set_error(self, msg):
        self.error_text = msg or ""
        self._render_status()

    # ------------------------------------------------------ input handlers
    def _apply_stake(self, source="settings"):
        """Validate + apply stake from its Settings control."""
        field = self.ed_stake
        try:
            v = model.validate_stake(field.text())
        except ValueError as e:
            self._set_error(str(e))
            field.setText(f"{self.stake:g}")
            return
        self.stake = v
        self.ed_stake.setText(f"{self.stake:g}")
        self._set_error("")
        self.scan_status = f"stake ${self.stake:.2f} applied"
        # Plans embed the stake — stale ones must be refetched for the
        # current selection, and re-derived if the selection changes.
        self._plan_cache.clear()
        if self.selected_coin:
            self._ensure_plan(self.selected_coin)
        self._render_all()               # flags column depends on stake

    def _apply_threshold(self, source="settings"):
        """Validate + apply the flag threshold from Settings."""
        if source == "preset":
            raw = self.cmb_threshold.currentText()
        else:
            raw = self.ed_threshold.text()
        try:
            v = model.validate_threshold(raw)
        except ValueError as e:
            self._set_error(str(e))
            self.ed_threshold.setText(f"{self.log_threshold:g}")
            self.cmb_threshold.blockSignals(True)
            self.cmb_threshold.setCurrentText(f"{self.log_threshold:g}")
            self.cmb_threshold.blockSignals(False)
            return
        self.log_threshold = v
        self.ed_threshold.setText(f"{self.log_threshold:g}")
        if self.cmb_threshold.currentText() != f"{self.log_threshold:g}":
            self.cmb_threshold.blockSignals(True)
            items = [self.cmb_threshold.itemText(i)
                     for i in range(self.cmb_threshold.count())]
            if f"{self.log_threshold:g}" not in items:
                self.cmb_threshold.addItem(f"{self.log_threshold:g}")
            self.cmb_threshold.setCurrentText(f"{self.log_threshold:g}")
            self.cmb_threshold.blockSignals(False)
        self._set_error("")
        self.scan_status = f"flag threshold {self.log_threshold:g} applied"
        self._render_status()

    def _apply_leverage(self, source="settings"):
        """Leverage cap dropdown (planner plumbing): write cap + clear cache.

        Leverage is embedded in every plan, so cached plans go stale the
        moment the cap moves — clear the cache and refetch the selection.
        """
        combo = self.cmb_lev
        text = combo.currentText().strip().lower().rstrip("x")
        try:
            cap = int(text)
        except ValueError:
            cap = -1
        if cap not in LEVERAGE_CHOICES:
            self._set_error(f"leverage cap must be one of "
                            f"{', '.join(f'{c}x' for c in LEVERAGE_CHOICES)}, "
                            f"got {combo.currentText()!r}")
            combo.blockSignals(True)
            combo.setCurrentText(f"{self.leverage_cap}x")
            combo.blockSignals(False)
            return
        self.leverage_cap = cap
        self.cmb_lev.blockSignals(True)
        self.cmb_lev.setCurrentText(f"{cap}x")
        self.cmb_lev.blockSignals(False)
        self._set_error("")
        self.scan_status = f"leverage cap {cap}x applied — plans recompute under the cap"
        self._plan_cache.clear()
        if self.selected_coin:
            self._ensure_plan(self.selected_coin)
        self._render_status()

    def _apply_coins(self):
        try:
            v = model.validate_coins(self.sp_coins.value())
        except ValueError as e:
            self._set_error(str(e))
            self.sp_coins.blockSignals(True)
            self.sp_coins.setValue(self.coins)
            self.sp_coins.blockSignals(False)
            return
        self.coins = v
        self._set_error("")
        self.scan_status = f"coins {self.coins} applied (universe budget + scan size)"
        self._render_status()

    def _apply_interval(self):
        try:
            v = model.validate_interval(self.sp_interval.value())
        except ValueError as e:
            self._set_error(str(e))
            self.sp_interval.blockSignals(True)
            self.sp_interval.setValue(self.interval)
            self.sp_interval.blockSignals(False)
            return
        self.interval = v
        self._set_error("")
        self.scan_status = f"auto-scan interval {self.interval}s applied"
        self._render_topbar()
        self._render_status()

    def _apply_db(self):
        """DB path: applied for the close-time snapshot and next launch."""
        path = (self.ed_db.text() or "").strip()
        if not path:
            self._set_error("db path must not be empty")
            self.ed_db.setText(self.db)
            return
        self.db = path
        self.ed_db.setText(self.db)
        self._set_error("")
        self.scan_status = ("db path applied — the worker keeps its open "
                            "database; the new path is used at next launch "
                            "and for the close-time snapshot")
        self._render_status()

    def _on_dir_change(self, *_a):
        choice = self.cmb_dir.currentText()
        key = choice.strip().lower()
        if key not in model.DIR_FILTERS:
            self.cmb_dir.blockSignals(True)
            self.cmb_dir.setCurrentText("Both")
            self.cmb_dir.blockSignals(False)
            self._set_error(f"unknown direction filter: {choice!r}")
            return
        self.dir_filter = key
        self._render_table()
        self._render_header()

    def _on_sort_change(self, *_a):
        key = self.cmb_sort.currentText()
        if key not in SORT_CHOICES:
            self.cmb_sort.blockSignals(True)
            self.cmb_sort.setCurrentText("score")
            self.cmb_sort.blockSignals(False)
            self._set_error(f"unknown sort key: {key!r}")
            return
        self.sort_key = key
        self._render_table()
        self._render_header()

    def _on_search_change(self, *_a):
        self._render_table()
        self._render_header()

    def _on_search_commit(self, *_a):
        """Enter in Find: table match selects it, else venue-wide lookup.

        The table only holds the current rotation, so a coin outside it
        correctly shows an empty table — Enter scores it on demand instead
        of dead-ending (same rule as tkinter).
        """
        q = str(self.ed_search.text() or "").strip().upper()
        if not q:
            return
        for table in (self.tree, self.tree_vetoed):
            for r in range(table.rowCount()):
                item = table.item(r, 0)
                coin = item.data(Qt.ItemDataRole.UserRole) if item else None
                if coin and q in str(coin).upper():
                    # selectRow fires _on_select -> plan fetch + detail render
                    table.selectRow(r)
                    table.scrollToItem(table.item(r, 0))
                    return
        if self._submit("lookup", coin=q):
            self.scan_status = f"looking up {q} venue-wide…"
            self._render_status()

    def _on_lookup(self, msg):
        coin = msg.get("coin", "?")
        card = msg.get("card")
        if card is None:
            reason = msg.get("error") or "not listed on the venue"
            self._set_error(f"{coin}: {reason}")
            self._render_status()
            return
        if not any(c.coin == coin for c in self.cards):
            self.cards.append(card)
        elif not msg.get("cached"):
            self.cards = [card if c.coin == coin else c for c in self.cards]
        self.selected_coin = coin
        self._ensure_plan(coin)
        self._render_all()
        if getattr(self, "_lookup_goto_scanner", False):
            self._lookup_goto_scanner = False
            self._switch_page("scanner")
        if msg.get("cached"):
            self.scan_status = f"{coin} was already in the table"
        else:
            self.scan_status = f"{coin} scored on demand"
        self._render_status()

    def _toggle_auto(self):
        self.auto_scan = not self.auto_scan
        self.btn_auto.setText(self._auto_label())
        self.scan_status = ("auto-scan paused" if not self.auto_scan
                            else f"auto-scan resumed ({self.interval}s)")
        self._render_topbar()
        self._render_status()

    def _on_header_clicked(self, section):
        """Fresh click on a measure column opens best-first (descending).

        Qt's own handler sorts first (fresh section starts ascending);
        this slot normalises the very first click per column, repeat
        clicks keep Qt's toggle. Refills reset the "fresh" state, so the
        sort combo and header clicks never fight.
        """
        header = self.tree.horizontalHeader()
        fresh = section != self._hdr_section
        if fresh and 0 <= section < len(SIGNAL_COLUMNS):
            name = SIGNAL_COLUMNS[section]
            want = (Qt.SortOrder.DescendingOrder if name in DESC_FIRST
                    else Qt.SortOrder.AscendingOrder)
            if header.sortIndicatorOrder() != want:
                self.tree.sortItems(section, want)
                header.setSortIndicator(section, want)
        self._hdr_section = section

    def _on_select(self, *_a):
        if self._refilling:
            return
        src = self.sender()
        if src is self.tree:
            table, other = self.tree, self.tree_vetoed
        elif src is self.tree_vetoed:
            table, other = self.tree_vetoed, self.tree
        else:
            return
        sel = table.selectionModel().selectedRows() if table.selectionModel() else []
        if not sel:
            return
        item = table.item(sel[0].row(), 0)
        coin = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not coin:
            return
        self._refilling = True
        try:
            other.clearSelection()
        finally:
            self._refilling = False
        changed = coin != self.selected_coin
        self.selected_coin = coin
        if changed:
            self._ensure_plan(coin)
        self._render_detail()

    def _on_watch_select(self, table):
        """Selecting any in-page list row shows its detail on Scanner.

        Single click selects, fetches the plan, renders detail, and switches
        to the Scanner page where the detail pane lives. A coin outside the
        current rotation is scored venue-wide on demand instead of silently
        doing nothing.
        """
        if self._refilling:
            return
        sel = (table.selectionModel().selectedRows()
               if table.selectionModel() else [])
        if not sel:
            return
        item = table.item(sel[0].row(), 0)
        coin = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not coin:
            return
        if not any(c.coin == coin for c in self.cards):
            self._lookup_goto_scanner = True
            if self._submit("lookup", coin=coin):
                self.scan_status = f"looking up {coin} venue-wide…"
                self._render_status()
            return
        self._refilling = True
        try:
            self.tree.clearSelection()
            self.tree_vetoed.clearSelection()
            for other in self._watch_tables:
                if other is not table:
                    other.clearSelection()
        finally:
            self._refilling = False
        changed = coin != self.selected_coin
        self.selected_coin = coin
        self._refilling = True
        try:
            self._select_coin_row(self.tree, coin)
            self._select_coin_row(self.tree_vetoed, coin)
        finally:
            self._refilling = False
        if changed:
            self._ensure_plan(coin)
        self._render_detail()
        self._switch_page("scanner")

    def _open_coin_from_list(self, table):
        """Double-click a row to inspect its signal in the Scanner page."""
        sel = (table.selectionModel().selectedRows()
               if table.selectionModel() else [])
        if not sel:
            return
        item = table.item(sel[0].row(), 0)
        coin = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not coin or not any(c.coin == coin for c in self.cards):
            self._set_error(f"{coin or 'coin'} is not in the current scan")
            self._render_status()
            return
        self._on_watch_select(table)
        self._switch_page("scanner")

    def _ensure_plan(self, coin):
        """Fetch the trade plan on the plan lane (never scan-blocked)."""
        if coin in self._plan_cache or coin in self._plans_pending:
            return
        self._plans_pending.add(coin)
        self._plan_jobs.put({"coin": coin, "stake": self.stake,
                             "leverage_cap": self.leverage_cap})

    def _refresh_plan_quietly(self, coin):
        """Re-fetch a cached plan without flashing 'fetching…'.

        The old plan stays visible until the new one arrives (_on_plan
        overwrites the cache and re-renders). No-op if a fetch is in flight.
        """
        if coin in self._plans_pending:
            return
        self._plans_pending.add(coin)
        self._plan_jobs.put({"coin": coin, "stake": self.stake,
                             "leverage_cap": self.leverage_cap})

    # ------------------------------------------- watcher dock (additive only)
    @staticmethod
    def _watcher_px(value):
        return f"{value:.8g}" if isinstance(value, (int, float)) else "—"

    def refresh_watcher(self):
        """Queue a worker-side watcher reload (read-only feed)."""
        self._submit("watcher", selected_id=self.watcher_selected)

    def _on_watcher(self, msg):
        """Render a worker watcher result; failures stay contained.

        A None panel renders the "watcher data unavailable" fallback in
        the dock only — the scanner table is never touched here (mirrors
        tkinter's _on_watcher).
        """
        panel = msg.get("panel")
        self.watcher_panel = panel
        self.watcher_error = msg.get("error")
        if panel is not None:
            if panel.selected_id is not None:
                self.watcher_selected = panel.selected_id
            self._watcher_paused = any(
                str(n).startswith("paused") for n in (panel.notices or []))
        self._render_watcher()
        self.refresh_dashboard()
        if self.detail_coin is not None:
            self._render_detail_dock(panel)

    def _render_watcher(self):
        table = self.watcher_table
        self._refilling = True
        try:
            table.setRowCount(0)
            self._watcher_ids = []
            panel = self.watcher_panel
            if panel is None:
                self._set_watcher_detail_text(
                    model.watcher_detail_text(None, self.watcher_selected))
            else:
                order = model.watcher_order(panel)
                self._watcher_ids = list(order)
                table.setRowCount(len(order))
                for r, sid in enumerate(order):
                    d = (panel.by_id or {}).get(sid) or {}
                    cells = [sid, d.get("rule", "?"), d.get("coin", "?"),
                             d.get("direction", "?"), d.get("state", "?"),
                             self._watcher_px(d.get("entry")),
                             self._watcher_px(d.get("stop_px")),
                             self._watcher_px(d.get("tgt_px"))]
                    for c, text in enumerate(cells):
                        item = _CellItem(str(text))
                        item.setData(Qt.ItemDataRole.UserRole, sid)
                        item.setToolTip(str(text))
                        table.setItem(r, c, item)
                if self.watcher_selected not in (panel.by_id or {}) \
                        and order:
                    self.watcher_selected = order[0]
                if self.watcher_selected in self._watcher_ids:
                    table.selectRow(
                        self._watcher_ids.index(self.watcher_selected))
                self._set_watcher_detail_text(
                    self._watcher_detail_full_text())
        finally:
            self._refilling = False
        self._sync_watcher_pause_label()

    def _watcher_detail_full_text(self):
        """Shared model detail text + the report-shaped correlated section."""
        text = model.watcher_detail_text(self.watcher_panel,
                                         self.watcher_selected)
        if self.watcher_panel is not None:
            lines = watcher_correlated_lines(self.db)
            if lines:
                text = text + "\n\n" + "\n".join(lines)
        return text

    def _set_watcher_detail_text(self, text):
        self.watcher_detail.setPlainText(text or "")

    def _sync_watcher_pause_label(self):
        try:
            self.btn_watcher_pause.setText(
                "Resume watcher" if self._watcher_paused else "Pause watcher")
        except (AttributeError, RuntimeError):
            pass

    def _on_watcher_select(self, *_a):
        if self._refilling:
            return
        sel = (self.watcher_table.selectionModel().selectedRows()
               if self.watcher_table.selectionModel() else [])
        if not sel:
            return
        item = self.watcher_table.item(sel[0].row(), 0)
        sid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not sid:
            return
        # Selection is the setup id, never the row index, so it survives
        # refresh reorders (the flicker fix).
        self.watcher_selected = sid
        self._set_watcher_detail_text(self._watcher_detail_full_text())

    def _on_watcher_pause(self):
        """Toggle the global pause switch (worker-side Task 2 writer)."""
        self._submit("watcher_pause", paused=not self._watcher_paused,
                     selected_id=self.watcher_selected)

    def _on_watcher_disable(self):
        """Disable the selected setup's rule (worker-side Task 2 writer)."""
        panel = self.watcher_panel
        detail = ((getattr(panel, "by_id", None) or {}).get(
            self.watcher_selected) if panel is not None else None)
        rule = (detail or {}).get("rule")
        if not rule:
            self._set_error("no watcher setup selected — nothing to disable")
            return
        self._submit("watcher_disable", rule=rule, disabled=True,
                     selected_id=self.watcher_selected)

    def _shutdown(self):
        """Close-time work: snapshot, stop timers, retire worker lanes."""
        if self._closing:
            return
        self._closing = True
        try:
            from proto import snapshot as snap
            snap.save(self.db, self.cards,
                      plans=self._plan_cache,
                      meta={"stake": self.stake,
                            "threshold": self.log_threshold})
        except Exception:
            pass
        for timer in (self._poll_timer, self._tick_timer):
            try:
                timer.stop()
            except Exception:
                pass
        for q in (self._jobs, self._plan_jobs):
            try:
                q.put_nowait(None)
            except Exception:
                pass

    def _on_close(self):
        """Ctrl-Q / programmatic close (mirrors the tkinter entry point)."""
        self._shutdown()
        self.close()

    def closeEvent(self, event):
        self._shutdown()
        super().closeEvent(event)

    # -------------------------------------------------- jobs and results
    def _submit(self, cmd, **params):
        """Queue a worker job; never blocks the UI thread."""
        if cmd in self._busy:
            self.scan_status = f"{cmd} already in progress"
            self._render_status()
            return False
        if cmd == "scan":
            # Current validated operator config rides with every scan.
            params.setdefault("stake", self.stake)
            params.setdefault("coins", self.coins)
            params.setdefault("log_threshold", self.log_threshold)
            params.setdefault("leverage_cap", self.leverage_cap)
        self._busy.add(cmd)
        self.activity = ACTIVITY.get(cmd, cmd)
        self._jobs.put({"cmd": cmd, **params})
        if cmd == "scan":
            self._push_event("info", "scan queued")
        self._render_status()
        return True

    def _poll(self):
        if self._closing:
            return
        try:
            while True:
                msg = self._results.get_nowait()
                try:
                    self._handle_msg(msg)
                except Exception as e:      # a bad message never kills the loop
                    self._set_error(f"internal error: {type(e).__name__}: {e}")
        except queue.Empty:
            pass

    def _handle_msg(self, msg):
        kind = msg.get("kind")
        if msg.get("venue"):
            self.venue = dict(msg["venue"])
        if not msg.get("ok", True):
            if kind in ("watcher", "watcher_pause", "watcher_disable"):
                # Worker-side watcher failure: contained fallback in the
                # dock only, never the global scanner error path.
                self._on_watcher({"kind": kind, "ok": True, "panel": None,
                                  "error": "watcher data unavailable: "
                                  f"{msg.get('error', 'unknown error')}"})
                self._finish(kind)
                return
            if kind == "disk_snapshot":
                self._finish(kind)
                return
            self._set_error(f"{kind} failed: {msg.get('error', 'unknown error')}")
            if kind in ("scan", "resolve", "collect"):
                self._push_event("error",
                                 f"{kind} failed — "
                                 f"{msg.get('error', 'unknown error')}")
            if kind == "scan":
                self._on_scan_failed(msg)
            if kind == "plan":
                # Free the pending slot so the coin can be retried later.
                self._on_plan(msg)
            if kind == "ready":
                self._busy.clear()   # no jobs will ever run on a dead worker
            self._finish(kind)
            return
        handler = {"scan": self._on_scan,
                   "disk_snapshot": self._on_disk_snapshot,
                   "stats": self._on_stats,
                   "resolve": self._on_resolve, "collect": self._on_collect,
                   "lookup": self._on_lookup,
                   "watcher": self._on_watcher,
                   "watcher_pause": self._on_watcher,
                   "watcher_disable": self._on_watcher,
                   "detail_bars": self._on_detail_bars,
                   "plan": self._on_plan, "ready": self._on_ready}.get(kind)
        if handler is not None:
            handler(msg)
        if kind == "scan":
            self._push_event(
                "ok", f"scan completed — {len(self.cards)} cards · "
                      f"{msg.get('status', '')}")
        elif kind == "resolve":
            self._push_event(
                "ok", f"resolved {msg.get('resolved', 0)} outcome(s)"
                      + (f" · {msg.get('plans_resolved', 0)} plan(s)"
                         if msg.get("plans_resolved") else ""))
        elif kind == "collect":
            self._push_event(
                "ok", f"collected bars — {msg.get('coins', 0)} coins "
                      f"(+{msg.get('bars', 0)} new bars)")
        self._finish(kind)

    def _finish(self, kind):
        self._busy.discard(kind)
        if not self._busy:
            self.activity = "idle"
        self._render_status()

    def _on_ready(self, msg):
        self.scan_status = "ready" if msg.get("ok") else msg.get("error", "")

    def _on_scan_failed(self, msg):
        self._has_live_scan = True
        # Brief: scan failure -> status error, keep old table, venue degraded.
        self.last_scan_ts = time.time()
        self.last_scan_label = time.strftime("%H:%M:%S")
        self.mexc_ok = False
        self.scan_status = (f"scan failed — keeping previous table "
                            f"({msg.get('error', 'unknown error')})")
        self._render_topbar()
        self._render_header()
        self._render_status()

    def _on_scan(self, msg):
        self._has_live_scan = True
        self.last_scan_ts = time.time()
        self.last_scan_label = time.strftime("%H:%M:%S")
        self.universe = int(msg.get("universe", 0))
        self.stats = msg.get("stats") or self.stats
        cards = list(msg.get("cards") or [])
        self.failed = int(msg.get("failed", 0))
        self.attempted = len(cards) + self.failed
        self._last_errors = dict(msg.get("errors") or {})
        self.scan_status = msg.get("status", "")
        if msg.get("plan"):
            self.plan_stats = msg["plan"]
        if msg.get("digest"):
            self.digest = msg["digest"]
        uni_err = msg.get("universe_error")
        self.mexc_ok = self.universe > 0 and not uni_err

        if not cards and self.cards and uni_err:
            # Refresh failed and there is nothing to score: keep the old table.
            self._set_error(f"scan failed: universe unavailable ({uni_err}) — "
                            "keeping previous table")
            self.scan_status = "scan failed, showing previous table"
            self._render_all()
            return

        self.error_text = ""
        self.cards = cards
        # Sweep-loop ticker snapshot -> dash_market_data (Task 6 carry):
        # the cards already carry the sweep's real venue numbers, so the
        # dashboard price/24h%/Market-filter/sort-24h% go live with swept
        # quotes. Merge only (a failed sweep never wipes older quotes).
        try:
            for coin, quote in cards_to_market(cards).items():
                self.dash_market_data[coin] = quote
        except Exception:
            pass
        # Keep GOOD cached plans for coins still present; drop departed
        # coins AND failed lookups, so a pre-scan error refetches.
        coins = {c.coin for c in cards}
        for coin in list(self._plan_cache):
            cached = self._plan_cache[coin]
            if coin not in coins:
                del self._plan_cache[coin]
            elif (cached.get("plan") is None
                    and cached.get("err") == "coin is no longer in the last scan"):
                del self._plan_cache[coin]
        if self.selected_coin and self.selected_coin not in coins:
            self.selected_coin = None
        if self.selected_coin and self.selected_coin in self._plan_cache:
            self._refresh_plan_quietly(self.selected_coin)
        elif self.selected_coin:
            self._ensure_plan(self.selected_coin)
        if uni_err:
            self._set_error(f"universe refresh failed: {uni_err} "
                            "(scoring the cached universe)")
        self._render_all()
        # Brief item 6: the outcomes panel streams on every scan too — one
        # cheap offline DB read through the normal stats path (never
        # manual-only); guarded so it can't clobber the scan status line.
        if "stats" not in self._busy:
            self._submit("stats")

    def _on_disk_snapshot(self, msg):
        """Replace the close-time file only when SQLite has a newer scan."""
        cards = list(msg.get("cards") or [])
        saved_ts = float(msg.get("latest_ts") or 0)
        if (self._has_live_scan or not cards
                or saved_ts <= self._launch_snapshot_at):
            return
        self.cards = cards
        self._launch_snapshot_at = saved_ts
        self.failed = 0
        self.attempted = len(cards)
        self._last_errors = {}
        self.selected_coin = None
        self._plan_cache.clear()  # file plans belong to the older scan
        self.digest = msg.get("digest") or {"picks": [], "watch": [], "new": []}
        stamp = time.strftime("%H:%M", time.localtime(saved_ts))
        self.scan_status = (f"showing saved database scan {stamp} "
                            f"({len(cards)} coins) — live scan running…")
        self._render_all()

    def _on_stats(self, msg):
        self.stats = msg.get("stats") or self.stats
        self.outcome = msg.get("outcome") or self.outcome
        if msg.get("hit24"):
            self.hit24 = msg["hit24"]
        if msg.get("hit_all"):
            self.hit_all = msg["hit_all"]
        if msg.get("plan"):
            self.plan_stats = msg["plan"]
        if msg.get("plan24"):
            self.plan24 = msg["plan24"]
        self._render_header()
        self._render_outcomes()
        # Dashboard display pref (Task 7): pages follow each stats result
        # only when the user switched autorefresh on (default off).
        if getattr(self, "dash_autorefresh", False):
            self.refresh_dashboard()

    def _on_resolve(self, msg):
        n = int(msg.get("resolved", 0))
        self.stats = msg.get("stats") or self.stats
        self.outcome = msg.get("outcome") or self.outcome
        if msg.get("hit24"):
            self.hit24 = msg["hit24"]
        if msg.get("hit_all"):
            self.hit_all = msg["hit_all"]
        if msg.get("plan"):
            self.plan_stats = msg["plan"]
        if msg.get("plan24"):
            self.plan24 = msg["plan24"]
        self.scan_status = (f"resolved {n} pending outcome(s)"
                            + (f", {msg.get('plans_resolved', 0)} plan(s)"
                               if msg.get("plans_resolved") else ""))
        self._render_all()

    def _on_collect(self, msg):
        self.scan_status = (f"collected 5m bars for {msg.get('coins', 0)} coins "
                            f"(+{msg.get('bars', 0)} new bars)")
        self._render_status()

    def _on_plan(self, msg):
        coin = msg.get("coin")
        if not coin:
            return
        if coin in self._plans_pending:
            self._plans_pending.discard(coin)
        cap = msg.get("leverage_cap")
        if cap is not None and cap != self.leverage_cap:
            # The fetch raced a leverage-cap change: this plan embeds the
            # OLD cap. Never cache it — refetch under the current cap.
            if coin == self.selected_coin:
                self._ensure_plan(coin)
            return
        self._plan_cache[coin] = {"plan": msg.get("plan"),
                                  "err": msg.get("err")}
        if coin == self.selected_coin:
            self._render_detail()

    # ------------------------------------------------------ auto-scan tick
    def _tick(self):
        if self._closing:
            return
        if (self.auto_scan and "scan" not in self._busy
                and time.time() - self.last_scan_ts >= self.interval):
            self._submit("scan")
        # Stream the measurement panel: a stats job is one cheap DB read
        # (no network), so hit-rates move on their own every 30s and on
        # every scan/resolve — never a manual Refresh to see movement.
        if ("stats" not in self._busy
                and time.time() - self._last_stats_ts >= STATS_EVERY_S):
            if self._submit("stats"):
                self._last_stats_ts = time.time()
        self._render_topbar()

    # ------------------------------------------------------------ rendering
    def _render_all(self):
        self._render_table()
        self._render_header()
        self._render_topbar()
        self._render_vetoed()
        self._render_detail()
        self._render_watchlist()
        self._render_outcomes()
        self._render_dashboard()
        self._render_detail_dock()
        self._render_status()

    def _venue_text(self):
        parts = []
        mexc = self.mexc_ok
        parts.append("MEXC –" if mexc is None else
                     ("MEXC ok" if mexc else "MEXC DEGRADED"))
        st = self.venue.get("bybit")
        if st is None:
            parts.append("Bybit –")
        elif st.get("ok"):
            parts.append("Bybit ok")
        else:
            parts.append("Bybit DEGRADED")
        return " · ".join(parts)

    def _render_topbar(self):
        """Scan UTC stamp, live countdown, venue pill (top-bar widgets).

        The SCANNING dot follows the real worker lane ("scan" in flight),
        the elapsed label derives ONLY from last_scan_ts (honest "no scan
        yet" before the first scan), the clock is real UTC, and the rail's
        bottom status blocks mirror the same venue state — never a
        hardcoded connection claim. No bell: there is no backing event
        source, so omission is the correct state.
        """
        if self.last_scan_ts:
            self.lbl_scan_utc.setText(
                "Scan: " + time.strftime("%H:%M:%S",
                                         time.gmtime(self.last_scan_ts)) + " UTC")
            delta = int(time.time() - self.last_scan_ts)
            self.lbl_scan_elapsed.setText(
                "elapsed %02d:%02d" % divmod(max(0, delta), 60))
        else:
            self.lbl_scan_utc.setText("Scan: --:--:--")
            self.lbl_scan_elapsed.setText("no scan yet")
        if "scan" in self._busy:
            self.lbl_scan_dot.setText("● SCANNING")
            self.lbl_scan_dot.setStyleSheet(
                f"color: {_theme.LONG_FG};")
        else:
            self.lbl_scan_dot.setText("● IDLE")
            self.lbl_scan_dot.setStyleSheet(
                f"color: {_theme.TEXT_DIM};")
        self.lbl_clock.setText(
            time.strftime("%H:%M:%S", time.gmtime()) + " UTC")
        self._render_countdown()
        if self.mexc_ok is None:
            self.lbl_venue.setText("MEXC –")
            self.lbl_venue.setStyleSheet(f"color: {_theme.TEXT_DIM};")
        elif self.mexc_ok:
            self.lbl_venue.setText("MEXC ok")
            self.lbl_venue.setStyleSheet(f"color: {_theme.LONG_FG};")
        else:
            self.lbl_venue.setText("MEXC DEGRADED")
            self.lbl_venue.setStyleSheet(f"color: {_theme.ERROR_FG};")
        self._render_rail_status()

    def _render_rail_status(self):
        """Rail bottom blocks from real venue/scan state (never hardcoded)."""
        api = getattr(self, "lbl_rail_api", None)
        venue_lbl = getattr(self, "lbl_rail_venue", None)
        if api is not None:
            if self.mexc_ok is None:
                api.setText("API: MEXC –")
                api.setStyleSheet(f"color: {_theme.TEXT_DIM};")
            elif self.mexc_ok:
                api.setText("API: MEXC ok")
                api.setStyleSheet(f"color: {_theme.LONG_FG};")
            else:
                api.setText("API: MEXC DEGRADED")
                api.setStyleSheet(f"color: {_theme.ERROR_FG};")
        if venue_lbl is not None:
            st = (self.venue or {}).get("bybit")
            if st is None:
                venue_lbl.setText("venue: Bybit –")
                venue_lbl.setStyleSheet(f"color: {_theme.TEXT_DIM};")
            elif st.get("ok"):
                venue_lbl.setText("venue: Bybit ok")
                venue_lbl.setStyleSheet(f"color: {_theme.LONG_FG};")
            else:
                venue_lbl.setText("venue: Bybit DEGRADED")
                venue_lbl.setStyleSheet(f"color: {_theme.ERROR_FG};")

    def _countdown_text(self):
        """'Next scan: mm:ss' from interval − elapsed (or 'paused')."""
        if not self.auto_scan:
            return "Next scan: paused"
        left = int(self.interval - (time.time() - self.last_scan_ts))
        if left < 0:
            left = 0
        return "Next scan: %02d:%02d" % divmod(left, 60)

    def _render_countdown(self):
        self.lbl_countdown.setText(self._countdown_text())

    def _render_header(self):
        st = self.stats
        self.lbl_header.setText(
            f"universe {self.universe}"
            f" · shown {len(self._ranked)} · vetoed {len(self._vetoed)}"
            f" · stake ${self.stake:.2f}"
            f" · {self._venue_text()}"
            f" · last scan {self.last_scan_label}"
            f" · logs {st.get('rows', 0)} rows / {st.get('coins', 0)} coins"
            f" · flagged {st.get('flagged', 0)}"
            f" · outcomes {st.get('outcomes', 0)}")

    def _select_coin_row(self, table, coin):
        """Restore/strip the selection after a refill (signal-suppressed)."""
        if coin:
            for r in range(table.rowCount()):
                item = table.item(r, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == coin:
                    table.selectRow(r)
                    return
        table.clearSelection()

    def _render_table(self):
        table = self.tree
        self._refilling = True
        try:
            table.setSortingEnabled(False)   # fill in model order, then sort UI
            cards = model.filter_cards(self.cards, self.dir_filter)
            cards = model.filter_search(cards, self.ed_search.text())
            ranked, vetoed = model.split_vetoed(cards)
            ranked = model.sort_cards(ranked, self.sort_key)
            vetoed = model.sort_cards(vetoed, self.sort_key)
            self._ranked, self._vetoed = ranked, vetoed

            table.setRowCount(len(ranked))
            for i, c in enumerate(ranked, 1):
                flags = model.format_flags(c, self.stake)
                oi = c.oi_change_pct
                oi_n = getattr(c, "oi_notional", None)
                cells = (
                    (str(i), float(i)),
                    (model.direction_arrow(c.direction), None),
                    (c.coin, None),
                    (model.format_price(c.price), _num(c.price)),
                    (f"{c.change_24h_pct:+.1f}%", _num(c.change_24h_pct)),
                    (model.format_vol(c.quote_vol_24h),
                     _num(c.quote_vol_24h)),
                    (f"{c.funding_rate * 100:+.4f}%", _num(c.funding_rate)),
                    (model.format_oi(oi, oi_n), _num(oi)),
                    (f"{c.lean:+.2f}", _num(c.lean)),
                    (f"{c.earlyness:.2f}", _num(c.earlyness)),
                    (f"{c.score:.1f}", _num(c.score)),
                    (model.format_shadow(c),
                     _num(getattr(c, "score_v3", None))),
                    (flags, None),
                )
                tags = model.row_tags(c.direction, i,
                                      watch="WATCH" in flags.split())
                bg = _theme.tag_background(tags)
                fg = _theme.tag_foreground(tags)
                dir_fg = _theme.direction_foreground(c.direction)
                for col, (text, number) in enumerate(cells):
                    anchor = SIGNAL_ANCHORS.get(SIGNAL_COLUMNS[col], "e")
                    cell_fg = fg
                    cell_bg = bg
                    if col == SIGNAL_COLUMNS.index("dir") and dir_fg:
                        cell_fg = dir_fg          # green ▲ / red ▼
                    if (col == SIGNAL_COLUMNS.index("flags")
                            and "UNVALIDATED" in flags.split()):
                        # amber pill on the FLAGS cell (mockup language)
                        cell_bg = _theme.PILL_UNVALIDATED_BG
                        cell_fg = _theme.PILL_UNVALIDATED_FG
                    item = _CellItem(text, number, _align(anchor), cell_bg,
                                     cell_fg)
                    if col == 0:
                        item.setData(Qt.ItemDataRole.UserRole, c.coin)
                    table.setItem(i - 1, col, item)

            header = table.horizontalHeader()
            header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
            table.setSortingEnabled(True)
            table.sortItems(0, Qt.SortOrder.AscendingOrder)
            self._hdr_section = None        # next header click is "fresh"
            self._select_coin_row(table, self.selected_coin)
        finally:
            self._refilling = False

    def _render_vetoed(self):
        table = self.tree_vetoed
        self._refilling = True
        try:
            table.setRowCount(len(self._vetoed))
            for i, c in enumerate(self._vetoed):
                codes = ",".join(v.code for v in c.vetoes)
                reason = (model.veto_reason(c.vetoes[0].code)
                          if c.vetoes else "")
                cells = ((model.direction_arrow(c.direction), None),
                         (c.coin, None),
                         (f"{c.score:.1f}", _num(c.score)),
                         (reason, None),
                         (codes, None))
                bg = _theme.tag_background(
                    model.row_tags(c.direction, i, vetoed=True))
                for col, (text, number) in enumerate(cells):
                    anchor = {"dir": "center", "coin": "w", "score": "e",
                              "reason": "w", "vetoes": "w"}[self.veto_cols[col]]
                    item = _CellItem(text, number, _align(anchor), bg,
                                     _theme.VETO_FG)
                    if col == 0:
                        item.setData(Qt.ItemDataRole.UserRole, c.coin)
                    table.setItem(i, col, item)
            self._select_coin_row(table, self.selected_coin)
        finally:
            self._refilling = False

    # ---------------------------------------------------------- detail pane
    def _plan_state(self, coin):
        """(plan, plan_err) for the cached plan of `coin` (or (None, None))."""
        cached = self._plan_cache.get(coin)
        if not cached:
            return None, None
        return cached.get("plan"), cached.get("err")

    def _scan_utc_text(self):
        if self.last_scan_ts:
            return time.strftime("%H:%M:%S", time.gmtime(self.last_scan_ts)) \
                + " UTC"
        return "n/a (no live scan this session)"

    def _reset_detail(self):
        """Empty-state detail pane (placeholder, no invented values)."""
        self.lbl_det_coin.setText("—")
        self.lbl_det_dir.setText("")
        self.lbl_det_dir.setObjectName("badgeDirNeutral")
        self.lbl_det_unval.hide()
        self.lbl_det_sub.setText("select a row for the full breakdown")
        self.lbl_det_price.setText("")
        self.lbl_det_chg.setText("")
        self.lbl_det_chg.setObjectName("")
        self.lbl_det_vol.setText("n/a")
        self.lbl_det_oi.setText("n/a")
        self.lbl_det_funding.setText("n/a")
        self.det_kv.setRowCount(0)
        self.lbl_det_flags.setText("—")
        self.detail.setPlainText("select a row for the full breakdown")

    def _set_kv_rows(self, rows):
        """Fill the SIGNAL DETAILS key-value table ([key, value] pairs)."""
        self.det_kv.setRowCount(len(rows))
        for r, (key, value) in enumerate(rows):
            kitem = _CellItem(key, align=_align("w"))
            kitem.setForeground(QBrush(QColor(_theme.TEXT_DIM)))
            vitem = _CellItem(value, align=_align("w"))
            self.det_kv.setItem(r, 0, kitem)
            self.det_kv.setItem(r, 1, vitem)
        self.det_kv.horizontalHeader().resizeSection(
            0, max(120, self.det_kv.width() // 2))

    def _set_detail_text(self, text):
        """Analyst-note text with warn/veto lines colored (tk tag parity)."""
        self.detail.setPlainText(text)
        doc = self.detail.document()
        block = doc.begin()
        while block.isValid():
            stripped = block.text().lstrip()
            if stripped.startswith("⚠") or stripped.startswith("⨯"):
                cursor = self.detail.textCursor()
                cursor.setPosition(block.position())
                cursor.setPosition(block.position() + block.length() - 1,
                                   QTextCursor.MoveMode.KeepAnchor)
                fmt = QTextCharFormat()
                color = (_theme.WARN_FG if stripped.startswith("⚠")
                         else _theme.VETO_FG)
                fmt.setForeground(QColor(color))
                cursor.mergeCharFormat(fmt)
            block = block.next()
        self.detail.moveCursor(QTextCursor.MoveOperation.Start)

    def _analyst_notes(self, card, plan, plan_err, last_error):
        """Note content: vetoes + counter-trend/MTF/DATA warnings + plan
        warnings. Plain honest text — never a metric the backend lacks."""
        out = []
        for v in card.vetoes or ():
            out.append(f"⨯ {v.code}: {v.reason}")
        for n in card.notes or ():
            if isinstance(n, str) and (n.startswith("counter-trend:")
                                       or n.startswith("MTF ")
                                       or n.startswith("DATA ")):
                out.append(f"⚠ {n}")
        if last_error:
            out.append(f"⚠ last error: {last_error}")
        if plan is not None:
            for w in getattr(plan, "warnings", ()) or ():
                prefix = "⨯ " if str(w).startswith("vetoed") else "⚠ "
                out.append(prefix + str(w))
        elif plan_err:
            out.append(f"⚠ no plan: {plan_err}")
        if not out:
            return "no open notes for this signal"
        return "\n".join(out)

    def _compose_detail_text(self):
        """Full detail text via gui.model.detail_text (shared, never forked).

        This is what Copy puts on the clipboard — the exact text the
        tkinter detail pane renders, produced by the shared model.
        """
        coin = self.selected_coin
        if not coin:
            return ""
        card = next((c for c in self.cards if c.coin == coin), None)
        if card is None:
            return ""
        plan, plan_err = self._plan_state(coin)
        last_error = self._last_errors.get(coin) if self._last_errors else None
        return model.detail_text(card, plan=plan, plan_err=plan_err,
                                 stake=self.stake, last_error=last_error)

    def _render_detail(self):
        coin = self.selected_coin
        if not coin:
            self._reset_detail()
            return
        card = next((c for c in self.cards if c.coin == coin), None)
        if card is None:
            self._reset_detail()
            self.lbl_det_coin.setText(coin)
            self.lbl_det_sub.setText(f"{coin} is no longer in the last scan")
            return
        plan, plan_err = self._plan_state(coin)
        last_error = self._last_errors.get(coin) if self._last_errors else None
        flags = model.format_flags(card, self.stake)

        # header row: name + direction badge + UNVALIDATED badge
        self.lbl_det_coin.setText(card.coin)
        direction = card.direction
        arrow = model.direction_arrow(direction)
        self.lbl_det_dir.setText(f"{arrow} {direction}")
        if direction == "LONG":
            self.lbl_det_dir.setObjectName("badgeDirLong")
        elif direction == "SHORT":
            self.lbl_det_dir.setObjectName("badgeDirShort")
        else:
            self.lbl_det_dir.setObjectName("badgeDirNeutral")
        # (objectName changes take effect on the next style recompute; the
        # text always carries the truth even if a repolish is skipped.)
        try:
            self.lbl_det_dir.style().unpolish(self.lbl_det_dir)
            self.lbl_det_dir.style().polish(self.lbl_det_dir)
        except Exception:
            pass
        self.lbl_det_unval.setVisible("UNVALIDATED" in flags.split())

        # subtitle: Base / Quote (Perpetual) — every scanned pair is a USDT
        # perp (MEXC USDT-perpetual universe); never invent a quote asset.
        self.lbl_det_sub.setText(f"{card.coin} / USDT (Perpetual)")

        # big mono price + 24h%
        self.lbl_det_price.setText(model.format_price(card.price))
        chg = f"{card.change_24h_pct:+.1f}%"
        self.lbl_det_chg.setText(chg)
        if card.change_24h_pct >= 0:
            self.lbl_det_chg.setStyleSheet(f"color: {_theme.LONG_FG};")
        else:
            self.lbl_det_chg.setStyleSheet(f"color: {_theme.SHORT_FG};")

        # stat tiles: 24H VOLUME / OI (notional or n/a) / FUNDING
        self.lbl_det_vol.setText(model.format_vol(card.quote_vol_24h))
        oi = model._to_float(card.oi_change_pct)
        if oi is None:
            self.lbl_det_oi.setText("n/a")
        else:
            self.lbl_det_oi.setText(model.format_oi(oi, card.oi_notional))
        self.lbl_det_funding.setText(f"{card.funding_rate * 100:+.4f}%")

        # signal details key-value table (honest states only)
        if plan is not None:
            entry = f"{plan.entry_low:.8g} – {plan.entry_high:.8g}"
            stop = f"{plan.stop:.8g}"
            tp1 = f"{plan.tp1:.8g}"
            tp2 = f"{plan.tp2:.8g}"
            lev = f"{plan.leverage}x (cap {self.leverage_cap}x)"
        elif plan_err:
            entry = stop = tp1 = tp2 = "unavailable"
            lev = f"unavailable (cap {self.leverage_cap}x)"
        else:
            entry = stop = tp1 = tp2 = "fetching…"
            lev = f"fetching… (cap {self.leverage_cap}x)"
        early = card.earlyness
        early_q = ("Good" if early >= 0.5 else
                   "Fresh" if early >= 0.3 else "Late")
        lean_q = ("Bullish" if card.lean > 0 else
                  "Bearish" if card.lean < 0 else "Neutral")
        fund_q = ("Positive" if card.funding_rate > 0 else
                  "Negative" if card.funding_rate < 0 else "Neutral")
        self._set_kv_rows([
            ("Direction", f"{arrow} {direction}"),
            ("Score", f"{card.score:.1f} / 100"),
            ("Entry Zone", entry),
            ("Stop Loss", stop),
            ("Take Profit 1", tp1),
            ("Take Profit 2", tp2),
            ("Leverage", lev),
            ("Early Signal", f"{early:.2f} — {early_q}"),
            ("Lean", f"{card.lean:+.2f} — {lean_q}"),
            ("Funding Rate", f"{card.funding_rate * 100:+.4f}% — {fund_q}"),
            ("Time", self._scan_utc_text()),
        ])
        self.lbl_det_flags.setText(flags or "—")
        self._set_detail_text(
            self._analyst_notes(card, plan, plan_err, last_error))

    # ---------------------------------------------------------- watchlist
    def _fill_watch_table(self, table, rows):
        """Fill one Watchlist tab from (coin, display-cell) rows."""
        table.setRowCount(len(rows))
        for row, (coin, cells) in enumerate(rows):
            for col, cell in enumerate(cells):
                text, number, anchor, foreground = cell
                item = _CellItem(text, number, _align(anchor),
                                 foreground=foreground)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, coin)
                table.setItem(row, col, item)

    def _render_watchlist(self):
        self._refilling = True
        try:
            watch_rows = []
            for c in picks_mod.watch_list(self.cards, self.stake):
                min_text = (f"${c.min_notional:.2f}"
                            if c.min_notional is not None else "unknown")
                dir_fg = _theme.direction_foreground(c.direction)
                watch_rows.append((c.coin, [
                    (c.coin, None, "w", None),
                    (model.direction_arrow(c.direction), None, "center", dir_fg),
                    (f"{c.score:.1f}", _num(c.score), "e", None),
                    (min_text, _num(c.min_notional), "e", None),
                ]))

            pick_rows = []
            for c in self.digest.get("picks", []):
                pick_rows.append((c.coin, [
                    (c.coin, None, "w", None),
                    (model.direction_arrow(c.direction), None, "center",
                     _theme.direction_foreground(c.direction)),
                    (f"{c.score:.1f}", _num(c.score), "e", None),
                    (model.format_price(c.price), _num(c.price), "e", None),
                    (f"{c.lean:+.2f}", _num(c.lean), "e", None),
                ]))

            new_rows = []
            for entry in self.digest.get("new", []):
                direction = entry.get("direction")
                new_rows.append((entry["coin"], [
                    (entry["coin"], None, "w", None),
                    (self._fmt_ts(entry.get("first_seen")), None, "w", None),
                    (f"{entry['score']:.1f}"
                     if entry.get("score") is not None else "n/a",
                     _num(entry.get("score")), "e", None),
                    ({"LONG": "▲", "SHORT": "▼"}.get(direction, "•"),
                     None, "center", _theme.direction_foreground(direction)),
                ]))

            self._fill_watch_table(self.tbl_watchlist, watch_rows)
            self._fill_watch_table(self.tbl_top_picks, pick_rows)
            self._fill_watch_table(self.tbl_new_listings, new_rows)
            self.watch_tabs.setTabText(0, f"Watchlist ({len(watch_rows)})")
            self.watch_tabs.setTabText(1, f"Top 10 ({len(pick_rows)})")
            self.watch_tabs.setTabText(2, f"New coins ({len(new_rows)})")
            for table in self._watch_tables:
                self._select_coin_row(table, self.selected_coin)
            self._update_watchlist_hint()
        finally:
            self._refilling = False

    def _update_watchlist_hint(self, _index=None):
        tables = getattr(self, "_watch_tables", ())
        if not tables:
            return
        index = self.watch_tabs.currentIndex()
        table = tables[index] if 0 <= index < len(tables) else tables[0]
        self.lbl_watchlist_hint.setText(
            f"{table.rowCount()} entries · select a row to inspect · "
            "double-click to open in Scanner")

    # ---------------------------------------------------------- outcomes
    @staticmethod
    def _outcome_table_rows(table, rows):
        table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, value in enumerate(values):
                text = str(value)
                table.setItem(r, c, _CellItem(
                    text, _num(text), _align("w" if c == 0 else "e")))

    @staticmethod
    def _signal_rows(hit):
        result = []
        by_dir = hit.get("by_direction_horizon", {})
        for horizon in model.HORIZONS:
            for direction in ("LONG", "SHORT"):
                stat = by_dir.get(direction, {}).get(horizon, {})
                n = int(stat.get("resolved", 0))
                result.append((f"{horizon.upper()} · {direction}", n,
                               int(stat.get("wins", 0)),
                               int(stat.get("losses", 0)),
                               f"{stat.get('pct_won', 0.0):.1f}%" if n else "—",
                               f"{stat.get('avg_return', 0.0):+.2f}%" if n else "—"))
        return result

    @staticmethod
    def _plan_rows(stats):
        n = int(stats.get("measured", stats.get("planned", 0)))
        terminal = int(stats.get("terminal", 0))
        logged = int(stats.get("plans_logged", 0))
        rows = [("Plans logged", logged, "—", "—"),
                ("Measured plan outcomes", n, n, "—")]
        for name, key, pct in (("Stop touched", "stop_hit", "stop_pct"),
                               ("TP1 reached", "tp1_hit", "tp1_pct"),
                               ("TP2 reached", "tp2_hit", "tp2_pct")):
            rows.append((name, int(stats.get(key, 0)), n,
                         f"{stats.get(pct, 0.0):.1f}%" if n else "—"))
        rows.append(("Terminal plans (stop or TP2)", terminal, n,
                     f"{stats.get('terminal_pct', 0.0):.1f}%" if n else "—"))
        for name, key, pct in (("Stop · terminal only", "t_stop_hit", "t_stop_pct"),
                               ("TP1 · terminal only", "t_tp1_hit", "t_tp1_pct"),
                               ("TP2 · terminal only", "t_tp2_hit", "t_tp2_pct")):
            rows.append((name, int(stats.get(key, 0)), terminal,
                         f"{stats.get(pct, 0.0):.1f}%" if terminal else "—"))
        return rows

    @staticmethod
    def _cohort_context(hit, plan, label):
        return (f"{label}: {hit.get('signals', 0)} flagged scan observations "
                f"across {hit.get('coins', 0)} distinct coins. Repeated scans "
                "count as separate observations; each horizon has its own "
                "sample; cohort dates use signal time and results mature later. "
                f"Plans: {plan.get('measured', 0)} measured of "
                f"{plan.get('plans_logged', 0)} logged. Touch rates can overlap; "
                "terminal rates use stop/TP2-finished plans and are not net P&L.")

    def _render_outcomes(self):
        hit24 = getattr(self, "hit24", None) or report_mod.signal_stats(None, 24)
        hit_all = getattr(self, "hit_all", None) or report_mod.signal_stats(None, None)
        plan24 = getattr(self, "plan24", None) or {"planned": 0}
        plan_all = getattr(self, "plan_stats", None) or {"planned": 0}

        # The dock is a compact daily snapshot. Cumulative totals live only
        # on the full Outcomes page so unlike windows are never side by side.
        observed = int(hit24.get("signals", 0))
        summary_rows = [
            ("Window", "Rolling last 24 hours"),
            ("Flagged observations", f"{observed} · {hit24.get('coins', 0)} distinct coins"),
            ("Resolved horizon results",
             str(hit24.get("overall", {}).get("resolved", 0))),
            ("Plans", f"{plan24.get('measured', 0)} measured / "
                      f"{plan24.get('plans_logged', 0)} logged"),
        ]
        self._set_outcome_rows(self.tbl_outcomes_summary, summary_rows)
        performance_rows = []
        for horizon in model.HORIZONS:
            stat = hit24.get("by_horizon", {}).get(horizon, {})
            n = int(stat.get("resolved", 0))
            performance_rows.append((horizon.upper(),
                (f"{stat.get('pct_won', 0.0):.1f}% hit · "
                 f"avg {stat.get('avg_return', 0.0):+.2f}% · n={n}")
                if n else "Pending · no resolved outcomes"))
        self._set_outcome_rows(self.tbl_outcomes_performance, performance_rows)

        self.lbl_outcomes_daily.setText(
            self._cohort_context(hit24, plan24, "Rolling last 24 hours"))
        self.lbl_outcomes_cumulative.setText(
            self._cohort_context(hit_all, plan_all, "All recorded history"))
        self._outcome_table_rows(self.tbl_outcomes_daily,
                                 self._signal_rows(hit24))
        self._outcome_table_rows(self.tbl_outcomes,
                                 self._signal_rows(hit_all))
        self._outcome_table_rows(self.tbl_plans_daily,
                                 self._plan_rows(plan24))
        self._outcome_table_rows(self.tbl_plans_cumulative,
                                 self._plan_rows(plan_all))

    # ---------------------------------------------------------- data tools
    def _export_csv(self, path=None):
        """Save the visible signals table to CSV (file dialog unless given).

        'Visible' = exactly what the table shows right now: current dir
        filter, search filter and display order (incl. the rank column).
        """
        rows = self.tree.rowCount()
        if path is None:
            path, _selected = QFileDialog.getSaveFileName(
                self, "Export visible table to CSV",
                os.path.join(os.getcwd(), "alt-radar-signals.csv"),
                "CSV files (*.csv)")
        if not path:
            return False                      # cancelled in the dialog
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow([SIGNAL_HEADINGS[c] for c in SIGNAL_COLUMNS])
                for r in range(rows):
                    w.writerow([self.tree.item(r, c).text()
                                for c in range(len(SIGNAL_COLUMNS))])
        except OSError as e:
            self._set_error(f"export failed: {e}")
            self._render_status()
            return False
        self.scan_status = f"exported {rows} row(s) to {path}"
        self._render_status()
        return True

    def _copy_detail_text(self):
        """Copy the selected coin's full detail text to the clipboard."""
        text = self._compose_detail_text()
        if not text:
            self._set_error("select a coin first — nothing to copy")
            self._render_status()
            return False
        QApplication.clipboard().setText(text)
        self.scan_status = (f"detail text for {self.selected_coin} copied "
                            f"to the clipboard ({len(text)} chars)")
        self._render_status()
        return True

    # ------------------------------------------------------------- events
    def _push_event(self, level, text):
        """In-app event ring buffer (max 50) with colored-dot levels."""
        self.events.append((time.time(), level, str(text)))
        if len(self.events) > EVENTS_MAX:
            del self.events[:-EVENTS_MAX]
        self._render_events()

    def _render_events(self):
        color = {"ok": _theme.LONG_FG, "error": _theme.ERROR_FG,
                 "info": _theme.INFO_FG}
        self.events_log_list.clear()
        for ts, level, text in reversed(self.events):
            stamp = time.strftime("%H:%M:%S", time.localtime(ts))
            item = QListWidgetItem(f"● {stamp}  {text}")
            item.setForeground(QColor(color.get(level, _theme.TEXT_DIM)))
            self.events_log_list.addItem(item)

    def _refresh_log_file(self):
        """Tail logs/app.log (last 400 lines) into the Logs page viewer."""
        path = os.path.join("logs", "app.log")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()[-400:]
            text = "\n".join(lines) if lines else "(log file is empty)"
        except FileNotFoundError:
            text = (f"no log file at {path} yet — nothing has been written "
                    f"this session (the scanner logs to its SQLite DB; this "
                    f"viewer only tails the file)")
        except OSError as e:
            text = f"cannot read {path}: {e}"
        self.logs_view.setPlainText(text)

    # ------------------------------------------------------------ rendering
    def _render_status(self):
        self.lbl_activity.setText(f"activity: {self.activity}")
        self.lbl_failed.setText(
            f"failed {self.failed}/{self.attempted} of last scan")
        if self.error_text:
            self.lbl_statusline.setStyleSheet(f"color: {COLOR_ERROR};")
            self.lbl_statusline.setText(self.error_text)
        else:
            self.lbl_statusline.setStyleSheet(
                f"color: {_theme.STATUS_MUTED_FG};")
            self.lbl_statusline.setText(self.scan_status)

    # ------------------------------------------------------------- test seam
    def set_cards(self, cards, status="cards loaded (no scan)"):
        """Replace the table with `cards` and re-render synchronously.

        No worker, no network — a seam for tests and manual seeding. Plan
        fetches still only happen on row selection.
        """
        self.cards = list(cards)
        self.failed = 0
        self.attempted = len(self.cards)
        self.last_scan_ts = time.time()
        self.last_scan_label = time.strftime("%H:%M:%S")
        self.scan_status = status
        self.error_text = ""
        self._last_errors = {}
        self._plan_cache.clear()
        if self.selected_coin and self.selected_coin not in {c.coin for c in self.cards}:
            self.selected_coin = None
        self._render_all()
