"""Design tokens + QSS theme for the ALT RADAR Qt port (PySide6 Widgets).

Dark trading-terminal language (rebuild spec §3): near-black blue app
surround (#030B15), navy cards (#071522), hairline borders (#142A3D),
bright ink text (#E6F0FF). The ONE accent is teal (#00D6AD) — it fills
the primary Scan button and marks LONG; SHORT is red (#FF4D5A); amber
(#f59e0b) is reserved for UNVALIDATED / Watch / warnings; violet
(#8B5CF6) is the secondary accent (selected-rail edge); blue (#38bdf8)
is links + info dots. These direction and status hues are SEMANTIC
(never decoration). Mono numerals in every data pane. Fusion is the
base style; this file layers a QSS stylesheet and a dark QPalette on
top of it.

No per-file hex outside this module — every color qtgui/app.py uses is a
named constant here. Contrast invariants (enforced by tests/test_qtgui.py):
body text >= 7:1 on panels, muted >= 4.5:1, selection >= 4.5:1.

The row-tag vocabulary (long / long_alt / short / short_alt / plain /
vetoed / watch) is SHARED with gui/model.row_tags, which returns names
declared in gui/theme.py — those names are imported, never re-declared, so
the tag keys can never drift between the two front ends.
"""

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

from gui.theme import (  # shared tag vocabulary with gui/model.row_tags
    TAG_LONG, TAG_LONG_ALT, TAG_SHORT, TAG_SHORT_ALT,
    TAG_PLAIN, TAG_PLAIN_ALT, TAG_VETOED, TAG_VETOED_ALT, TAG_WATCH,
)

# ---- spec palette (rebuild §3, exact — the ONLY hardcoded hexes) ----
BG_MAIN = "#030B15"       # app surround
BG_SIDEBAR = "#06111F"    # rail / banded rows
BG_CARD = "#071522"       # panel / table base
BG_ELEVATED = "#091A2A"   # headers / raised controls / tiles
BORDER = "#142A3D"        # hairline borders
INK = "#E6F0FF"           # bright ink body text (>= 7:1 on panels)
INK_DIM = "#8EA8C4"       # secondary text (>= 4.5:1 everywhere)
INK_MUT = "#627B96"       # tertiary ink: section labels, scroll thumbs,
                          # combo arrows — large/decorative use only (see
                          # TEXT_MUTED note below)
ACC_POS = "#00D6AD"       # THE accent: primary Scan fill + LONG
ACC_2 = "#8B5CF6"         # secondary accent: selected-rail edge
ACC_NEG = "#FF4D5A"       # SHORT + errors
GRID = "#14273A"          # table gridlines

# ---- legacy aliases (every name referenced elsewhere keeps compiling) ----
WINDOW_BG = BG_MAIN       # app surround
SURFACE = BG_CARD         # panel / table base
SURFACE_ALT = BG_SIDEBAR  # banded row (navy variant)
HEADER_BG = BG_ELEVATED   # table header + raised controls
HEADER_HOVER = "#0e2337"  # hover lift, one step above BG_ELEVATED
BORDER_STRONG = "#24405c" # control outlines, one step above BORDER
TEXT = INK                # bright ink body text (>= 7:1 on panels)
TEXT_DIM = INK_DIM        # secondary text (>= 4.5:1 everywhere)
# NOTE: the spec's INK_MUT (#627B96) reads 4.2:1 on BG_CARD — below the
# brief's own >= 4.5:1 muted floor — so muted BODY text stays on INK_DIM
# (7.5:1). INK_MUT is reserved for large/decorative use (section labels,
# scrollbars) where the floor does not apply.
TEXT_MUTED = INK_DIM
SELECT_BG = "#1b3a57"     # selection (white text on top, ~11:1)
SELECT_FG = "#ffffff"
ACCENT = ACC_POS          # THE accent: primary Scan fill + links/focus
ACCENT_FG = BG_MAIN       # text on accent-filled buttons (near-black on teal)

# NOTE: direction/error aliases are lowercase spellings of the ACC_POS /
# ACC_NEG colors. Qt's QColor.name() normalizes rendered colors to
# lowercase and tests/test_qtgui.py compares them as strings, so the
# aliases match that form; the canonical spec tokens above keep their
# exact uppercase values.
LONG_FG = "#00d6ad"         # LONG arrow/text — semantic direction teal
SHORT_FG = "#ff4d5a"        # SHORT arrow/text — semantic direction red
LONG_BG = "#062a22"         # LONG row tint (teal over near-black)
LONG_BG_ALT = "#05231d"
SHORT_BG = "#2b1218"        # SHORT row tint (red over near-black)
SHORT_BG_ALT = "#231015"
VETO_BG = "#0c1b2e"         # neutral navy veto rows
VETO_BG_ALT = "#091525"
VETO_FG = "#8b9ab0"
WATCH_FG = "#f59e0b"        # amber: Watch / UNVALIDATED / warnings
WARN_FG = "#f59e0b"
ERROR_FG = "#ff4d5a"       # status/error red (>= 4.5:1 on SURFACE)
INFO_FG = "#38bdf8"         # links + info dots
READONLY_BG = "#4c1115"     # red READ-ONLY badge
READONLY_FG = "#fca5a5"
TIER2_FG = "#f59e0b"        # amber tier badge
TIER2_BG = "#3a2705"
STATUS_MUTED_FG = INK_DIM
DETAIL_BG = BG_MAIN       # analyst-note box (sunken below the card)
TILE_BG = BG_ELEVATED     # stat tiles in the detail pane
RAIL_BG = BG_SIDEBAR      # left icon rail
RAIL_ACTIVE = "#14324c"
PILL_UNVALIDATED_BG = "#432f0a"   # FLAGS-cell amber pill background
PILL_UNVALIDATED_FG = "#fbbf24"

# ---- type + rhythm (mirrors gui/theme.py's role names) ----
ROW_HEIGHT = 26
BASE_POINT_SIZE = 10
MONO_POINT_SIZE = 10
DETAIL_POINT_SIZE = 10

# ---- row tags: shared names -> Qt colors (one tint per tag) ----
TAG_BG = {
    TAG_LONG: LONG_BG,
    TAG_LONG_ALT: LONG_BG_ALT,
    TAG_SHORT: SHORT_BG,
    TAG_SHORT_ALT: SHORT_BG_ALT,
    TAG_PLAIN: SURFACE,
    TAG_PLAIN_ALT: SURFACE_ALT,
    TAG_VETOED: VETO_BG,
    TAG_VETOED_ALT: VETO_BG_ALT,
}

# Foreground-only tags layered on top of a background tag.
TAG_FG = {
    TAG_WATCH: WATCH_FG,
}


def tag_background(tags):
    """Row background color for a model.row_tags() tuple (first bg tag wins)."""
    for tag in tags:
        color = TAG_BG.get(tag)
        if color is not None:
            return color
    return SURFACE


def tag_foreground(tags):
    """Row foreground color: WATCH layers over the direction tint; None = default."""
    for tag in tags:
        color = TAG_FG.get(tag)
        if color is not None:
            return color
    return None


def direction_foreground(direction):
    """Arrow/text color for a card direction (semantic, never decoration)."""
    if direction == "LONG":
        return LONG_FG
    if direction == "SHORT":
        return SHORT_FG
    return None


# ---- fonts --------------------------------------------------------------
# Numerals are mono everywhere data lives (terminal discipline); the family
# is picked from what this machine actually has, with a sane fallback.
MONO_FAMILIES = ("DejaVu Sans Mono", "JetBrains Mono", "Liberation Mono",
                 "Menlo", "Consolas", "Ubuntu Mono", "Courier New")


def _available_families():
    try:
        return set(QFontDatabase.families())
    except Exception:
        try:
            return set(QFontDatabase().families())
        except Exception:
            return set()


def mono_family():
    """First installed family from MONO_FAMILIES (never raises)."""
    families = _available_families()
    for name in MONO_FAMILIES:
        if name in families:
            return name
    return MONO_FAMILIES[0]


def mono_font(point_size=MONO_POINT_SIZE, bold=False):
    """The data font: mono numerals for tables, detail pane, readouts."""
    font = QFont(mono_family())
    font.setPointSize(point_size)
    font.setBold(bold)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def ui_font(point_size=BASE_POINT_SIZE, bold=False):
    """Control font: whatever the platform sans is (labels, buttons)."""
    font = QFont()
    font.setPointSize(point_size)
    font.setBold(bold)
    return font


# ---- palette ------------------------------------------------------------
def _qcolor(hexstr):
    return QColor(hexstr)


def dark_palette():
    """Fusion-compatible dark QPalette (base roles + disabled states)."""
    p = QPalette()
    active = [
        (QPalette.ColorRole.Window, WINDOW_BG),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, SURFACE),
        (QPalette.ColorRole.AlternateBase, SURFACE_ALT),
        (QPalette.ColorRole.ToolTipBase, HEADER_BG),
        (QPalette.ColorRole.ToolTipText, TEXT),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.Button, HEADER_BG),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.BrightText, TEXT),
        (QPalette.ColorRole.Link, INFO_FG),
        (QPalette.ColorRole.LinkVisited, INFO_FG),
        (QPalette.ColorRole.Highlight, SELECT_BG),
        (QPalette.ColorRole.HighlightedText, SELECT_FG),
        (QPalette.ColorRole.PlaceholderText, TEXT_MUTED),
        (QPalette.ColorRole.Light, BORDER_STRONG),
        (QPalette.ColorRole.Midlight, BORDER),
        (QPalette.ColorRole.Mid, BORDER_STRONG),
        (QPalette.ColorRole.Dark, HEADER_BG),
        (QPalette.ColorRole.Shadow, "#000000"),
    ]
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role, hexstr in active:
            p.setColor(group, role, _qcolor(hexstr))
    for role, hexstr in active:
        # disabled: dim, never invisible
        p.setColor(QPalette.ColorGroup.Disabled, role, _qcolor(TEXT_MUTED))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight,
               _qcolor(SURFACE_ALT))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText,
               _qcolor(TEXT_MUTED))
    return p


# ---- stylesheet ---------------------------------------------------------
# Template form: every color flows through %(NAME)s interpolation, so the
# template holds zero hex literals (enforced by tests/test_qtgui_theme.py).
# Cards/panels 10px radii, controls/pills 8px, 1px hairline borders
# everywhere, no shadows.
QSS_TEMPLATE = """
/* ---- base ---- */
QMainWindow, QDialog { background: %(WINDOW_BG)s; }
QWidget { color: %(TEXT)s; }
QLabel { color: %(TEXT)s; background: transparent; }
#headerTitle { font-weight: 700; color: %(TEXT)s; }
#headerSubtitle { color: %(TEXT_DIM)s; }
#headerStats { color: %(TEXT_DIM)s; }
#badgeReadOnly { background: %(READONLY_BG)s; color: %(READONLY_FG)s;
                 font-weight: 700; padding: 2px 7px; border-radius: 8px; }
#badgeTier2 { background: %(TIER2_BG)s; color: %(TIER2_FG)s;
              font-weight: 700; padding: 2px 6px; border-radius: 8px; }
#badgeVenue { background: %(HEADER_BG)s; color: %(TEXT_DIM)s;
              padding: 2px 8px; border-radius: 10px;
              border: 1px solid %(BORDER)s; }
#badgeVenueOk { color: %(LONG_FG)s; }
#badgeVenueBad { color: %(ACC_NEG)s; }
#badgeDirLong { background: %(LONG_BG)s; color: %(LONG_FG)s;
                font-weight: 700; padding: 2px 8px; border-radius: 8px; }
#badgeDirShort { background: %(SHORT_BG)s; color: %(SHORT_FG)s;
                 font-weight: 700; padding: 2px 8px; border-radius: 8px; }
#badgeDirNeutral { background: %(SURFACE_ALT)s; color: %(TEXT_DIM)s;
                   font-weight: 700; padding: 2px 8px; border-radius: 8px; }
#detailTitle { font-weight: 700; color: %(TEXT)s; }
#detailSubtitle { color: %(TEXT_DIM)s; }
#detailPrice { font-weight: 700; color: %(TEXT)s; }
#detailChgUp { color: %(LONG_FG)s; font-weight: 700; }
#detailChgDown { color: %(SHORT_FG)s; font-weight: 700; }
#tileTitle { color: %(TEXT_MUTED)s; font-size: 9pt; }
#tileValue { color: %(TEXT)s; font-weight: 600; }
#kvKey { color: %(TEXT_DIM)s; }
#sectionLabel { color: %(INK_MUT)s; font-weight: 600; }
#railButton { background: transparent; border: none; border-radius: 8px;
              padding: 6px 2px; color: %(TEXT_DIM)s; }
#railButton:hover { background: %(HEADER_HOVER)s; color: %(TEXT)s; }
#railButton:checked { background: %(RAIL_ACTIVE)s; color: %(TEXT)s;
                      font-weight: 700;
                      border-left: 2px solid %(ACC_2)s; }
#btnPrimary { background: %(ACCENT)s; color: %(ACCENT_FG)s;
              border: 1px solid %(ACCENT)s; border-radius: 8px;
              padding: 4px 14px; font-weight: 700; }
#btnPrimary:hover { background: %(LONG_FG)s; border-color: %(LONG_FG)s; }
#btnPrimary:pressed { background: %(ACCENT)s; }
#btnPrimary:disabled { background: %(HEADER_BG)s; color: %(TEXT_MUTED)s;
                       border-color: %(BORDER_STRONG)s; }

/* ---- grouped controls ---- */
QGroupBox { background: %(SURFACE)s; border: 1px solid %(BORDER)s;
            border-radius: 10px; margin-top: 9px; padding: 7px 7px 4px 7px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                   padding: 0 5px; color: %(TEXT_MUTED)s; }
QPushButton { background: %(HEADER_BG)s; border: 1px solid %(BORDER_STRONG)s;
              border-radius: 8px; padding: 3px 9px; color: %(TEXT)s; }
QPushButton:hover { background: %(HEADER_HOVER)s; border-color: %(TEXT_MUTED)s; }
QPushButton:pressed { background: %(SURFACE_ALT)s; }
QPushButton:focus { border-color: %(ACCENT)s; }
QPushButton:disabled { color: %(TEXT_MUTED)s; background: %(SURFACE)s; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: %(SURFACE_ALT)s; border: 1px solid %(BORDER_STRONG)s;
    border-radius: 8px; padding: 2px 6px; color: %(SELECT_FG)s;
    selection-background-color: %(SELECT_BG)s; selection-color: %(SELECT_FG)s; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: %(ACCENT)s; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {
    color: %(TEXT_MUTED)s; background: %(SURFACE)s; }
QSpinBox::up-button, QSpinBox::down-button {
    background: %(HEADER_BG)s; border-left: 1px solid %(BORDER_STRONG)s;
    width: 16px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background: %(HEADER_HOVER)s; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { image: none; border-left: 4px solid transparent;
                        border-right: 4px solid transparent;
                        border-top: 5px solid %(INK_MUT)s; }
QComboBox QAbstractItemView { background: %(SURFACE_ALT)s;
    border: 1px solid %(BORDER_STRONG)s; color: %(SELECT_FG)s;
    selection-background-color: %(SELECT_BG)s;
    selection-color: %(SELECT_FG)s; outline: 0; }

/* ---- tables (item backgrounds come from row tags, not here) ---- */
QTableWidget { background: %(SURFACE)s; alternate-background-color: %(SURFACE)s;
               border: 1px solid %(BORDER)s; gridline-color: %(GRID)s;
               outline: 0; }
QTableWidget::item { padding: 0 5px; border: none;
                     border-bottom: 1px solid %(BORDER)s; }
QTableWidget::item:selected { background: %(SELECT_BG)s;
                              color: %(SELECT_FG)s; }
QTableWidget:focus { border-color: %(BORDER_STRONG)s; }
QHeaderView::section { background: %(HEADER_BG)s; color: %(TEXT_DIM)s;
    border: none; border-right: 1px solid %(BORDER)s;
    border-bottom: 1px solid %(BORDER)s; padding: 5px 6px;
    font-weight: 600; }
QHeaderView::section:hover { background: %(HEADER_HOVER)s;
                             color: %(TEXT)s; }
QHeaderView::section:checked { background: %(SELECT_BG)s;
                               color: %(SELECT_FG)s; }
QTableCornerButton::section { background: %(HEADER_BG)s; border: none; }

/* ---- detail pane + tiles ---- */
QTextEdit, QPlainTextEdit { background: %(DETAIL_BG)s;
    border: 1px solid %(BORDER)s;
    selection-background-color: %(SELECT_BG)s;
    selection-color: %(SELECT_FG)s; }
QFrame#tile { background: %(TILE_BG)s; border: 1px solid %(BORDER)s;
              border-radius: 10px; }

/* ---- tabs (outcomes dock) ---- */
QTabWidget::pane { border: 1px solid %(BORDER)s; background: %(SURFACE)s;
                   top: -1px; }
QTabBar::tab { background: %(SURFACE_ALT)s; color: %(TEXT_DIM)s;
    border: 1px solid %(BORDER)s; border-bottom: none;
    padding: 3px 12px; margin-right: 1px; }
QTabBar::tab:selected { background: %(SURFACE)s; color: %(TEXT)s;
                        font-weight: 600; }
QTabBar::tab:hover { background: %(HEADER_HOVER)s; color: %(TEXT)s; }

/* ---- recent-events list (colored dots are item text) ---- */
QListWidget { background: %(SURFACE)s; border: 1px solid %(BORDER)s;
              outline: 0; }
QListWidget::item { padding: 2px 5px; border-bottom: 1px solid %(BORDER)s; }
QListWidget::item:selected { background: %(SELECT_BG)s;
                             color: %(SELECT_FG)s; }

/* ---- status bar ---- */
QStatusBar { background: %(SURFACE)s; border-top: 1px solid %(BORDER)s; }
QStatusBar::item { border: none; }

/* ---- splitter ---- */
QSplitter::handle { background: %(WINDOW_BG)s; }
QSplitter::handle:horizontal { width: 5px; }
QSplitter::handle:vertical { height: 5px; }
QSplitter::handle:hover { background: %(BORDER)s; }

/* ---- scrollbars ---- */
QScrollBar:vertical { background: %(SURFACE)s; width: 11px; margin: 0;
                      border: none; }
QScrollBar::handle:vertical { background: %(INK_MUT)s;
    border-radius: 8px; min-height: 26px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: %(INK_DIM)s; }
QScrollBar:horizontal { background: %(SURFACE)s; height: 11px; margin: 0;
                        border: none; }
QScrollBar::handle:horizontal { background: %(INK_MUT)s;
    border-radius: 8px; min-width: 26px; margin: 2px; }
QScrollBar::handle:horizontal:hover { background: %(INK_DIM)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: %(SURFACE)s; }

/* ---- misc ---- */
QToolTip { background: %(HEADER_BG)s; color: %(SELECT_FG)s;
           border: 1px solid %(BORDER_STRONG)s; padding: 4px 7px; }
QSplitter { background: %(WINDOW_BG)s; }
QScrollArea { background: transparent; border: none; }
"""  # end QSS_TEMPLATE
_QSS_VALUES = {
    "BG_MAIN": BG_MAIN, "BG_SIDEBAR": BG_SIDEBAR, "BG_CARD": BG_CARD,
    "BG_ELEVATED": BG_ELEVATED, "BORDER": BORDER, "INK": INK,
    "INK_DIM": INK_DIM, "INK_MUT": INK_MUT, "ACC_POS": ACC_POS,
    "ACC_2": ACC_2, "ACC_NEG": ACC_NEG, "GRID": GRID,
    "WINDOW_BG": WINDOW_BG, "SURFACE": SURFACE, "SURFACE_ALT": SURFACE_ALT,
    "HEADER_BG": HEADER_BG, "HEADER_HOVER": HEADER_HOVER,
    "BORDER_STRONG": BORDER_STRONG,
    "TEXT": TEXT, "TEXT_DIM": TEXT_DIM, "TEXT_MUTED": TEXT_MUTED,
    "SELECT_BG": SELECT_BG, "SELECT_FG": SELECT_FG, "ACCENT": ACCENT,
    "ACCENT_FG": ACCENT_FG, "LONG_FG": LONG_FG, "SHORT_FG": SHORT_FG,
    "LONG_BG": LONG_BG, "SHORT_BG": SHORT_BG, "ERROR_FG": ERROR_FG,
    "READONLY_BG": READONLY_BG, "READONLY_FG": READONLY_FG,
    "TIER2_FG": TIER2_FG, "TIER2_BG": TIER2_BG, "DETAIL_BG": DETAIL_BG,
    "TILE_BG": TILE_BG, "RAIL_BG": RAIL_BG, "RAIL_ACTIVE": RAIL_ACTIVE,
    "INFO_FG": INFO_FG,
    "PILL_UNVALIDATED_BG": PILL_UNVALIDATED_BG,
    "PILL_UNVALIDATED_FG": PILL_UNVALIDATED_FG,
}

# Resolved stylesheet (kept as a module constant for apply_theme/is_applied).
QSS = QSS_TEMPLATE % _QSS_VALUES


def qss():
    """Resolved application stylesheet, built purely from theme tokens."""
    return QSS


def apply_theme(app):
    """Install the dark navy terminal theme on a QApplication. Idempotent.

    Fusion is the base style (predictable cross-platform metrics for the
    dense tables); the QSS + palette layer on top supply the look.
    """
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(QSS)
    app.setFont(ui_font())
    return True


def is_applied(app):
    """True when this module's stylesheet is already installed on `app`."""
    try:
        return app.styleSheet().strip().startswith("/* ---- base ---- */")
    except Exception:
        return False
