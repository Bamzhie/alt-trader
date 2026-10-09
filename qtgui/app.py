"""ALT RADAR desktop GUI — PySide6 / Qt Widgets port of gui/ (tkinter).

Layout parity with the tkinter app: header · toolbar (two grouped rows) ·
signals table · vetoed section · detail pane · outcomes panel · status bar.

Reuse contract (task Q1 brief): this module contains NO scanner logic.
  * gui/model.py     — every pure function (filters, sort, format, row data,
                       detail text, outcome summary, validation) is imported
                       and called, never forked.
  * gui/app.py       — Worker / PlanWorker (queue + proto threading layer)
                       and the shared display constants are imported as-is.
  * proto/*          — untouched backend, same message dicts as tkinter.
  * proto/snapshot.py— close-time `.last_entries` save, identical call.

Threading contract (same as tkinter, Qt edition):
  * The MAIN thread owns every Qt widget; worker threads never touch them.
  * ONE daemon worker runs scans / collect / resolve / lookup and ALL
    SQLite (a sqlite3 connection is thread-bound); PlanWorker is a second
    daemon lane so plan fetches never queue behind a ~50s scan.
  * Worker -> UI results travel through queue.Queue, drained by a QTimer
    on the main thread (the Qt replacement for Tk's after() poll);
    UI -> worker commands travel through a second queue.

Read-only: no order code path exists, no keys are held.
"""

import queue
import time
from types import SimpleNamespace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush, QKeySequence, QShortcut, \
    QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QGroupBox, \
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QSpinBox, \
    QSplitter, QStatusBar, QTableWidget, QTableWidgetItem, QTextEdit, \
    QVBoxLayout, QWidget

# --- reuse: worker layer + shared display constants (tkinter-free classes) --
from gui.app import ACTIVITY, DIR_CHOICES, POLL_MS, SIGNAL_ANCHORS, \
    SIGNAL_COLUMNS, SIGNAL_HEADINGS, SIGNAL_WIDTHS, SORT_CHOICES, \
    STATS_EVERY_S, TICK_MS, PlanWorker, Worker
# --- reuse: ALL pure display/logic functions (never forked) ---
from gui import model
from proto import report as report_mod

from . import theme as _theme

# Names stay importable for tests/callers, mirroring gui/app.py.
COLOR_ERROR = _theme.ERROR_FG

# Column indexes derived from the shared column tuple (never hard-coded).
COL_COIN = SIGNAL_COLUMNS.index("coin")

# Columns whose fresh header click opens best-first (descending); text
# columns and rank open ascending. Repeat clicks toggle (Qt default).
DESC_FIRST = {"price", "ch24", "vol24", "funding", "oi", "lean", "early",
              "score"}

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
        self.resize(1360, 860)
        self.setMinimumSize(1100, 640)

        # Operator config — validated at the edge, source of truth for jobs.
        self.stake = model.validate_stake(stake)
        self.coins = model.validate_coins(coins)
        self.interval = model.validate_interval(interval)
        self.log_threshold = model.validate_threshold(log_threshold)
        self.db = db
        self.auto_scan = bool(auto_scan)

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
        self.plan_stats = {"planned": 0}
        self._last_stats_ts = 0.0
        self.selected_coin = None
        self._last_errors = {}          # {coin: reason} from the last scan
        self.digest = {"picks": [], "watch": [], "new": []}
        self._plan_cache = {}           # coin -> {"plan": Plan|None, "err": str|None}
        self._plans_pending = set()
        self._busy = set()              # job kinds in flight
        self._closing = False
        self._refilling = False         # blocks selection signals during fill
        self._hdr_section = None        # header-sort state (fresh-click order)

        self._jobs = queue.Queue()      # UI -> worker
        self._results = queue.Queue()   # worker -> UI
        self._plan_jobs = queue.Queue()  # UI -> plan lane (never scan-blocked)

        self._build_header()
        self._build_toolbar()
        self._build_body()
        self._build_status()
        shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        shortcut.activated.connect(self._on_close)

        worker_args = SimpleNamespace(
            stake=self.stake, coins=self.coins, interval=self.interval,
            universe_budget=self.coins, log_threshold=self.log_threshold,
            db=self.db, write_logs=True)
        self._show_saved_snapshot()
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
        self._submit("stats")

    # -------------------------------------------------------- launch snapshot
    def _show_saved_snapshot(self):
        """Instant launch: last screen first, live scan replaces it.

        Source order (identical to tkinter): close-time `.last_entries`
        file, then the DB's latest scan cycle, then blank. Local reads
        only; never fatal.
        """
        try:
            source, cards, plans, label = model.resolve_launch_snapshot(self.db)
            if source == "empty":
                return
            self.cards = list(cards)
            self.failed = 0
            self.attempted = len(self.cards)
            for coin, entry in (plans or {}).items():
                if isinstance(entry, dict) and entry.get("plan") is not None:
                    self._plan_cache[coin] = entry
            self.scan_status = (f"showing {label} — live scan running…")
            self._render_all()
        except Exception as e:
            self.scan_status = (f"no saved signals ({type(e).__name__}) — "
                                f"waiting for the live scan…")
            try:
                self._render_status()
            except Exception:
                pass

    # ------------------------------------------------------------- widgets
    def _build_header(self):
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
        title = QLabel("ALT RADAR · MEXC perp scanner")
        title.setObjectName("headerTitle")
        title.setFont(_theme.ui_font(_theme.BASE_POINT_SIZE + 2, bold=True))
        lay.addWidget(title)
        # Permanent READ-ONLY marker (brief: no orders, no keys).
        badge_ro = QLabel(" READ-ONLY ")
        badge_ro.setObjectName("badgeReadOnly")
        badge_ro.setFont(_theme.ui_font(_theme.BASE_POINT_SIZE + 1, bold=True))
        lay.addWidget(badge_ro)
        badge_t2 = QLabel("TIER-2 UNVALIDATED")
        badge_t2.setObjectName("badgeTier2")
        lay.addWidget(badge_t2)
        self.lbl_header = QLabel("universe – · shown 0 · vetoed 0")
        self.lbl_header.setObjectName("headerStats")
        # Sans, not mono: the status string is long and must fit one line
        # at the default window width (same font role as the tkinter label).
        self.lbl_header.setFont(_theme.ui_font())
        lay.addWidget(self.lbl_header, 1)
        self._central_layout.addWidget(bar)

    def _group(self, parent_lay, text):
        box = QGroupBox(text)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)
        parent_lay.addWidget(box)
        return box

    def _build_toolbar(self):
        # Two rows of labeled groups (same grouping/frequency order as
        # tkinter): row 1 runs the scanner, row 2 views and analyses.
        row1 = QWidget()
        lay1 = QHBoxLayout(row1)
        lay1.setContentsMargins(0, 0, 0, 0)
        lay1.setSpacing(6)
        row2 = QWidget()
        lay2 = QHBoxLayout(row2)
        lay2.setContentsMargins(0, 0, 0, 0)
        lay2.setSpacing(6)
        self._central_layout.addWidget(row1)
        self._central_layout.addWidget(row2)

        # ---- row 1: run ----
        g_scan = self._group(lay1, "Scan")
        self.btn_scan = QPushButton("Scan now")
        self.btn_scan.clicked.connect(lambda: self._submit("scan"))
        g_scan.layout().addWidget(self.btn_scan)
        self.btn_auto = QPushButton(self._auto_label())
        self.btn_auto.clicked.connect(self._toggle_auto)
        g_scan.layout().addWidget(self.btn_auto)

        g_budget = self._group(lay1, "Budget")
        g_budget.layout().addWidget(QLabel("Coins"))
        self.sp_coins = QSpinBox()
        self.sp_coins.setRange(model.MIN_COINS, model.MAX_COINS)
        self.sp_coins.setValue(self.coins)
        self.sp_coins.setKeyboardTracking(False)
        self.sp_coins.setFixedWidth(74)
        self.sp_coins.valueChanged.connect(lambda *_: self._apply_coins())
        g_budget.layout().addWidget(self.sp_coins)
        g_budget.layout().addWidget(QLabel("Interval s"))
        self.sp_interval = QSpinBox()
        self.sp_interval.setRange(model.MIN_INTERVAL, 3600)
        self.sp_interval.setValue(self.interval)
        self.sp_interval.setKeyboardTracking(False)
        self.sp_interval.setFixedWidth(74)
        self.sp_interval.valueChanged.connect(lambda *_: self._apply_interval())
        g_budget.layout().addWidget(self.sp_interval)

        g_stake = self._group(lay1, "Stake")
        g_stake.layout().addWidget(QLabel("$"))
        self.ed_stake = QLineEdit(f"{self.stake:g}")
        self.ed_stake.setFixedWidth(80)
        self.ed_stake.editingFinished.connect(self._apply_stake)
        g_stake.layout().addWidget(self.ed_stake)
        btn_stake = QPushButton("Apply")
        btn_stake.setFixedWidth(64)
        btn_stake.clicked.connect(self._apply_stake)
        g_stake.layout().addWidget(btn_stake)
        self.btn_apply_stake = btn_stake

        # ---- row 2: view + analyse ----
        g_find = self._group(lay2, "Find")
        self.ed_search = QLineEdit()
        self.ed_search.setFixedWidth(150)
        self.ed_search.setPlaceholderText("coin… (Enter = venue-wide)")
        self.ed_search.textChanged.connect(lambda *_: self._on_search_change())
        self.ed_search.returnPressed.connect(self._on_search_commit)
        g_find.layout().addWidget(self.ed_search)

        g_view = self._group(lay2, "View")
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

        g_thr = self._group(lay2, "Flag threshold")
        self.ed_threshold = QLineEdit(f"{self.log_threshold:g}")
        self.ed_threshold.setFixedWidth(64)
        self.ed_threshold.editingFinished.connect(self._apply_threshold)
        g_thr.layout().addWidget(self.ed_threshold)
        btn_thr = QPushButton("Apply")
        btn_thr.setFixedWidth(64)
        btn_thr.clicked.connect(self._apply_threshold)
        g_thr.layout().addWidget(btn_thr)
        self.btn_apply_threshold = btn_thr

        g_lists = self._group(lay2, "Lists")
        self.btn_top = QPushButton("★ Top 10")
        self.btn_top.clicked.connect(self._open_picks)
        g_lists.layout().addWidget(self.btn_top)
        self.btn_watch = QPushButton("👁 Watch")
        self.btn_watch.clicked.connect(self._open_watch)
        g_lists.layout().addWidget(self.btn_watch)
        self.btn_new = QPushButton("+ New")
        self.btn_new.clicked.connect(self._open_new)
        g_lists.layout().addWidget(self.btn_new)

        g_data = self._group(lay2, "Data")
        self.btn_collect = QPushButton("Collect bars")
        self.btn_collect.clicked.connect(lambda: self._submit("collect"))
        g_data.layout().addWidget(self.btn_collect)
        self.btn_resolve = QPushButton("Resolve outcomes")
        self.btn_resolve.clicked.connect(lambda: self._submit("resolve"))
        g_data.layout().addWidget(self.btn_resolve)
        self.btn_stats = QPushButton("Refresh stats")
        self.btn_stats.clicked.connect(lambda: self._submit("stats"))
        g_data.layout().addWidget(self.btn_stats)

        lay1.addStretch(1)
        lay2.addStretch(1)

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

    def _build_body(self):
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
        veto_cols = ("dir", "coin", "score", "vetoes")
        veto_heads = {"dir": "DIR", "coin": "COIN", "score": "SCORE",
                      "vetoes": "VETO CODES"}
        veto_widths = {"dir": 44, "coin": 140, "score": 70, "vetoes": 320}
        self.tree_vetoed = self._make_table(veto_cols, veto_heads,
                                             veto_widths)
        self.veto_cols = veto_cols
        self.tree_vetoed.setMaximumHeight(_theme.ROW_HEIGHT * 6 + 30)
        self.tree_vetoed.itemSelectionChanged.connect(self._on_select)
        vv.addWidget(self.tree_vetoed)
        lv.addWidget(vet_box)

        # ---- right: detail pane + outcomes panel ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(4)

        det_box = QGroupBox("Detail — selected coin (review only, no orders)")
        dv = QVBoxLayout(det_box)
        dv.setContentsMargins(4, 4, 4, 4)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(_theme.mono_font(_theme.DETAIL_POINT_SIZE))
        self.detail.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        dv.addWidget(self.detail)
        rv.addWidget(det_box, 1)

        out_box = QGroupBox("Outcomes")
        ov = QVBoxLayout(out_box)
        ov.setContentsMargins(6, 6, 6, 6)
        self.lbl_outcomes = QLabel("no outcomes resolved yet")
        self.lbl_outcomes.setFont(_theme.mono_font())
        self.lbl_outcomes.setAlignment(Qt.AlignmentFlag.AlignLeft |
                                       Qt.AlignmentFlag.AlignTop)
        self.lbl_outcomes.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_outcomes.setWordWrap(True)
        ov.addWidget(self.lbl_outcomes)
        btn_resolve_now = QPushButton("Resolve now")
        btn_resolve_now.clicked.connect(lambda: self._submit("resolve"))
        ov.addWidget(btn_resolve_now, 0, Qt.AlignmentFlag.AlignLeft)
        rv.addWidget(out_box)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([840, 500])
        self._central_layout.addWidget(splitter, 1)
        self._set_detail_text("select a row for the full breakdown")

    def _build_status(self):
        bar = QStatusBar()
        self.setStatusBar(bar)
        inner = QWidget()
        lay = QHBoxLayout(inner)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(14)
        self.lbl_activity = QLabel("activity: idle")
        self.lbl_failed = QLabel("failed 0/0 of last scan")
        self.lbl_statusline = QLabel("")
        self.lbl_statusline.setFont(_theme.mono_font())
        lay.addWidget(self.lbl_activity)
        lay.addWidget(self.lbl_failed)
        lay.addWidget(self.lbl_statusline, 1)
        bar.addWidget(inner, 1)

    # ------------------------------------------------------ list modals
    def _open_list_modal(self, title, columns, rows):
        """Modal table dialog. Double-click/Enter jumps the main view.

        `columns`: [(key, heading, width)]; `rows`: list of dicts with at
        least "coin". Picking a row selects it in the main table (which
        fetches its plan on the plan lane) and closes the dialog — the
        tkinter Toplevel behaviour, dialog.exec() being the Qt modal loop.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(620, 420)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(8, 8, 8, 8)
        keys = [k for k, _, _ in columns]
        table = QTableWidget(len(rows), len(keys))
        table.setHorizontalHeaderLabels([h for _, h, _ in columns])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setShowGrid(False)
        table.setFont(_theme.mono_font())
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(_theme.ROW_HEIGHT)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        for i, (_, _, width) in enumerate(columns):
            header.resizeSection(i, width)
        for r, row in enumerate(rows):
            for c, key in enumerate(keys):
                text = row.get(key, "")
                item = _CellItem("" if text is None else text,
                                 align=_align("w" if key == "coin" else "e"))
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row.get("coin"))
                table.setItem(r, c, item)
        lay.addWidget(table, 1)

        def pick(*_a):
            sel = table.selectionModel().selectedRows() if table.selectionModel() else []
            if not sel:
                return
            coin = table.item(sel[0].row(), 0).data(Qt.ItemDataRole.UserRole)
            if any(c.coin == coin for c in self.cards):
                self.selected_coin = coin
                self._ensure_plan(coin)
                self._render_all()
            else:
                self._set_error(f"{coin} is not in the current table")
                self._render_status()
            dlg.accept()

        table.itemActivated.connect(pick)
        btn = QPushButton("Open in main view (double-click works too)")
        btn.clicked.connect(pick)
        lay.addWidget(btn)
        table.setFocus()
        dlg.exec()

    def _fmt_ts(self, ts):
        try:
            return time.strftime("%m-%d %H:%M", time.localtime(float(ts)))
        except (TypeError, ValueError):
            return "?"

    def _open_picks(self):
        rows = [{"coin": c.coin, "dir": model.direction_arrow(c.direction),
                 "score": f"{c.score:.1f}", "price": model.format_price(c.price),
                 "lean": f"{c.lean:+.2f}"}
                for c in self.digest.get("picks", [])]
        if not rows:
            self._set_error("no picks yet — run a scan first")
            self._render_status()
            return
        self._open_list_modal(
            "★ Top 10 picks — trade-now (actionable, above threshold)",
            [("coin", "COIN", 130), ("dir", "DIR", 50),
             ("score", "SCORE", 70), ("price", "PRICE", 110),
             ("lean", "LEAN", 70)], rows)

    def _open_watch(self):
        rows = [{"coin": c.coin, "dir": model.direction_arrow(c.direction),
                 "score": f"{c.score:.1f}",
                 "min": (f"${c.min_notional:.2f}"
                         if c.min_notional is not None else "unknown")}
                for c in self.digest.get("watch", [])]
        if not rows:
            self._set_error("watch list empty — nothing stake-blocked")
            self._render_status()
            return
        self._open_list_modal(
            "👁 Watch — blocked only by stake (tradable as stake compounds)",
            [("coin", "COIN", 130), ("dir", "DIR", 50),
             ("score", "SCORE", 70), ("min", "MIN NOTIONAL", 140)], rows)

    def _open_new(self):
        rows = [{"coin": d["coin"], "seen": self._fmt_ts(d["first_seen"]),
                 "score": (f"{d['score']:.1f}" if d["score"] is not None
                           else "n/a"),
                 "dir": {"LONG": "▲", "SHORT": "▼"}.get(d["direction"], "•")}
                for d in self.digest.get("new", [])]
        if not rows:
            self._set_error("no new listings in the last 7 days")
            self._render_status()
            return
        self._open_list_modal(
            "+ New listings — first seen within 7 days, by latest score",
            [("coin", "COIN", 130), ("seen", "FIRST SEEN", 110),
             ("score", "SCORE", 70), ("dir", "DIR", 50)], rows)

    # ------------------------------------------------------ widget helpers
    def _auto_label(self):
        return "Pause auto-scan" if self.auto_scan else "Resume auto-scan"

    def _set_error(self, msg):
        self.error_text = msg or ""
        self._render_status()

    # ------------------------------------------------------ input handlers
    def _apply_stake(self):
        try:
            v = model.validate_stake(self.ed_stake.text())
        except ValueError as e:
            self._set_error(str(e))
            self.ed_stake.setText(f"{self.stake:g}")
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

    def _apply_threshold(self):
        try:
            v = model.validate_threshold(self.ed_threshold.text())
        except ValueError as e:
            self._set_error(str(e))
            self.ed_threshold.setText(f"{self.log_threshold:g}")
            return
        self.log_threshold = v
        self.ed_threshold.setText(f"{self.log_threshold:g}")
        self._set_error("")
        self.scan_status = f"flag threshold {self.log_threshold:g} applied"
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

    def _ensure_plan(self, coin):
        """Fetch the trade plan on the plan lane (never scan-blocked)."""
        if coin in self._plan_cache or coin in self._plans_pending:
            return
        self._plans_pending.add(coin)
        self._plan_jobs.put({"coin": coin, "stake": self.stake})

    def _refresh_plan_quietly(self, coin):
        """Re-fetch a cached plan without flashing 'fetching…'.

        The old plan stays visible until the new one arrives (_on_plan
        overwrites the cache and re-renders). No-op if a fetch is in flight.
        """
        if coin in self._plans_pending:
            return
        self._plans_pending.add(coin)
        self._plan_jobs.put({"coin": coin, "stake": self.stake})

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
        self._busy.add(cmd)
        self.activity = ACTIVITY.get(cmd, cmd)
        self._jobs.put({"cmd": cmd, **params})
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
            self._set_error(f"{kind} failed: {msg.get('error', 'unknown error')}")
            if kind == "scan":
                self._on_scan_failed(msg)
            if kind == "plan":
                # Free the pending slot so the coin can be retried later.
                self._on_plan(msg)
            if kind == "ready":
                self._busy.clear()   # no jobs will ever run on a dead worker
            self._finish(kind)
            return
        handler = {"scan": self._on_scan, "stats": self._on_stats,
                   "resolve": self._on_resolve, "collect": self._on_collect,
                   "lookup": self._on_lookup,
                   "plan": self._on_plan, "ready": self._on_ready}.get(kind)
        if handler is not None:
            handler(msg)
        self._finish(kind)

    def _finish(self, kind):
        self._busy.discard(kind)
        if not self._busy:
            self.activity = "idle"
        self._render_status()

    def _on_ready(self, msg):
        self.scan_status = "ready" if msg.get("ok") else msg.get("error", "")

    def _on_scan_failed(self, msg):
        # Brief: scan failure -> status error, keep old table, venue degraded.
        self.last_scan_ts = time.time()
        self.last_scan_label = time.strftime("%H:%M:%S")
        self.mexc_ok = False
        self.scan_status = (f"scan failed — keeping previous table "
                            f"({msg.get('error', 'unknown error')})")
        self._render_header()
        self._render_status()

    def _on_scan(self, msg):
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

    def _on_stats(self, msg):
        self.stats = msg.get("stats") or self.stats
        self.outcome = msg.get("outcome") or self.outcome
        if msg.get("hit24"):
            self.hit24 = msg["hit24"]
        if msg.get("plan"):
            self.plan_stats = msg["plan"]
        self._render_header()
        self._render_outcomes()

    def _on_resolve(self, msg):
        n = int(msg.get("resolved", 0))
        self.stats = msg.get("stats") or self.stats
        self.outcome = msg.get("outcome") or self.outcome
        if msg.get("hit24"):
            self.hit24 = msg["hit24"]
        if msg.get("plan"):
            self.plan_stats = msg["plan"]
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

    # ------------------------------------------------------------ rendering
    def _render_all(self):
        self._render_table()
        self._render_header()
        self._render_vetoed()
        self._render_detail()
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
                for col, (text, number) in enumerate(cells):
                    anchor = SIGNAL_ANCHORS.get(SIGNAL_COLUMNS[col], "e")
                    item = _CellItem(text, number, _align(anchor), bg, fg)
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
                cells = ((model.direction_arrow(c.direction), None),
                         (c.coin, None),
                         (f"{c.score:.1f}", _num(c.score)),
                         (codes, None))
                bg = _theme.tag_background(
                    model.row_tags(c.direction, i, vetoed=True))
                for col, (text, number) in enumerate(cells):
                    anchor = {"dir": "center", "coin": "w", "score": "e",
                              "vetoes": "w"}[self.veto_cols[col]]
                    item = _CellItem(text, number, _align(anchor), bg,
                                     _theme.VETO_FG)
                    if col == 0:
                        item.setData(Qt.ItemDataRole.UserRole, c.coin)
                    table.setItem(i, col, item)
            self._select_coin_row(table, self.selected_coin)
        finally:
            self._refilling = False

    def _set_detail_text(self, text):
        """Mono detail text with warn/veto lines colored (tk tag parity)."""
        self.detail.setPlainText(text)
        doc = self.detail.document()
        block = doc.begin()
        while block.isValid():
            stripped = block.text().lstrip()
            if stripped.startswith("⚠") or stripped.startswith("⨯"):
                cursor = QTextCursor(block)
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

    def _render_detail(self):
        coin = self.selected_coin
        if not coin:
            self._set_detail_text("select a row for the full breakdown")
            return
        card = next((c for c in self.cards if c.coin == coin), None)
        if card is None:
            self._set_detail_text(f"{coin} is no longer in the last scan")
            return
        cached = self._plan_cache.get(coin)
        plan = cached.get("plan") if cached else None
        plan_err = cached.get("err") if cached else None
        last_error = self._last_errors.get(coin) if self._last_errors else None
        self._set_detail_text(
            model.detail_text(card, plan=plan, plan_err=plan_err,
                              stake=self.stake, last_error=last_error))

    def _render_outcomes(self):
        o = self.outcome
        counts = o.get("counts", {})
        lines = [" · ".join(f"{h} {counts.get(h, 0)}" for h in model.HORIZONS)
                 + f"   (total {o.get('total', 0)} resolved)"]
        for d in ("LONG", "SHORT"):
            e = o.get("direction", {}).get(d)
            if not e or not e.get("count"):
                lines.append(f"{d}: no resolved outcomes yet")
            else:
                lines.append(
                    f"{d}: n={e['count']} · avg signed return "
                    f"{e['avg_return']:+.2f}% · hit rate {e['hit_rate'] * 100:.0f}%")
        try:
            hit = getattr(self, "hit24", None) or report_mod.signal_stats(None, 24)
            oall = hit["overall"]
            lines.append(f"24h flagged: {hit['signals']} signals · "
                         f"{oall['resolved']} resolved · won {oall['wins']} / "
                         f"lost {oall['losses']} · {oall['pct_won']:.0f}% won · "
                         f"avg {oall['avg_return']:+.2f}%")
        except Exception:
            pass
        try:
            ps = getattr(self, "plan_stats", None) or {"planned": 0}
            if ps.get("planned"):
                lines.append(f"plans live: {ps['planned']} watched · stop "
                             f"{ps['stop_hit']} ({ps['stop_pct']:.0f}%) · TP1 "
                             f"{ps['tp1_hit']} ({ps['tp1_pct']:.0f}%) · TP2 "
                             f"{ps['tp2_hit']} ({ps['tp2_pct']:.0f}%) · "
                             f"terminal {ps.get('terminal', 0)}")
            else:
                lines.append("plans: none resolved yet")
        except Exception:
            pass
        self.lbl_outcomes.setText("\n".join(lines))

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
