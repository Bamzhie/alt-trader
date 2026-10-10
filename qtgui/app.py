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
import os
import queue
import time
from types import SimpleNamespace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush, QKeySequence, QPainter, QPixmap, \
    QShortcut, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QButtonGroup, QComboBox, \
    QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, \
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPlainTextEdit, \
    QPushButton, QSpinBox, QSplitter, QStatusBar, QStackedWidget, QTableWidget, \
    QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget

# --- reuse: worker layer + shared display constants (tkinter-free classes) --
from gui.app import ACTIVITY, DIR_CHOICES, POLL_MS, SIGNAL_ANCHORS, \
    SIGNAL_COLUMNS, SIGNAL_HEADINGS, SIGNAL_WIDTHS, SORT_CHOICES, \
    STATS_EVERY_S, TICK_MS, PlanWorker, Worker
# --- reuse: ALL pure display/logic functions (never forked) ---
from gui import model
from proto import picks as picks_mod
from proto import report as report_mod
from proto.planner import MAX_LEVERAGE

from . import theme as _theme

# Names stay importable for tests/callers, mirroring gui/app.py.
COLOR_ERROR = _theme.ERROR_FG

# Column indexes derived from the shared column tuple (never hard-coded).
COL_COIN = SIGNAL_COLUMNS.index("coin")

# Columns whose fresh header click opens best-first (descending); text
# columns and rank open ascending. Repeat clicks toggle (Qt default).
DESC_FIRST = {"price", "ch24", "vol24", "funding", "oi", "lean", "early",
              "score"}

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
        self.digest = {"picks": [], "watch": [], "new": []}
        self._plan_cache = {}           # coin -> {"plan": Plan|None, "err": str|None}
        self._plans_pending = set()
        self._busy = set()              # job kinds in flight
        self._closing = False
        self._lookup_goto_scanner = False  # watchlist lookup: show on Scanner
        self._launch_snapshot_at = 0.0
        self._has_live_scan = False
        self._refilling = False         # blocks selection signals during fill
        self._hdr_section = None        # header-sort state (fresh-click order)
        self.events = []                # (ts, level, text) ring buffer, max 50

        self._jobs = queue.Queue()      # UI -> worker
        self._results = queue.Queue()   # worker -> UI
        self._plan_jobs = queue.Queue()  # UI -> plan lane (never scan-blocked)

        self._build_topbar()
        self._build_pages()
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
        self.lbl_countdown = QLabel("Next scan: --:--")
        self.lbl_countdown.setObjectName("headerStats")
        lay.addWidget(self.lbl_countdown)
        self.lbl_venue = QLabel("MEXC –")
        self.lbl_venue.setObjectName("badgeVenue")
        lay.addWidget(self.lbl_venue)
        self.btn_gear = QPushButton("⚙")
        self.btn_gear.setFixedWidth(28)
        self.btn_gear.setToolTip("Open Settings")
        self.btn_gear.clicked.connect(lambda: self._switch_page("settings"))
        lay.addWidget(self.btn_gear)
        self._central_layout.addWidget(bar)

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
        rail.setFixedWidth(88)
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
        lv.addWidget(vet_box)

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
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([780, 560])
        parent_lay.addWidget(splitter, 1)
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
        self._render_countdown()

    # ------------------------------------------------------------ rendering
    def _render_all(self):
        self._render_table()
        self._render_header()
        self._render_topbar()
        self._render_vetoed()
        self._render_detail()
        self._render_watchlist()
        self._render_outcomes()
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
        """Scan UTC stamp, live countdown, venue pill (top-bar widgets)."""
        if self.last_scan_ts:
            self.lbl_scan_utc.setText(
                "Scan: " + time.strftime("%H:%M:%S",
                                         time.gmtime(self.last_scan_ts)) + " UTC")
        else:
            self.lbl_scan_utc.setText("Scan: --:--:--")
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
