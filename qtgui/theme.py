"""Design tokens + QSS theme for the ALT RADAR Qt port (PySide6 Widgets).

Dark trading-terminal language, ONE theme only (decided in the Q1 brief):
near-black zinc surfaces, a single restrained accent, semantic LONG green /
SHORT red kept as direction tints (never decoration), mono numerals in every
data pane. Fusion is the base style; this file layers a QSS stylesheet and a
dark palette on top of it.

No per-file hex outside this module — every color qtgui/app.py uses is a
named constant here (the tkinter gui/theme.py enforces the same rule with
TestNoHardcodedColors for its palette).

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

# ---- palette (base + semantics) ----
WINDOW_BG = "#0a0a0d"       # near-black zinc
SURFACE = "#121216"         # panel / table base
SURFACE_ALT = "#17171c"     # banded row
HEADER_BG = "#1d1d23"       # table header + raised controls
HEADER_HOVER = "#24242c"
BORDER = "#26262e"
BORDER_STRONG = "#2f2f38"
TEXT = "#d6d6dc"
TEXT_DIM = "#b6b6c0"
TEXT_MUTED = "#8a8a93"
SELECT_BG = "#1f2a3d"       # selection: accent-tinted, restrained
SELECT_FG = "#e8e8ee"
ACCENT = "#4f8ef7"          # THE accent — focus rings only

LONG_BG = "#12261b"
LONG_BG_ALT = "#0f2017"
SHORT_BG = "#2a1517"
SHORT_BG_ALT = "#231214"
VETO_BG = "#151519"
VETO_BG_ALT = "#121216"
VETO_FG = "#77777f"
WATCH_FG = "#e0a44c"
WARN_FG = "#e0a44c"
ERROR_FG = "#f26a6a"
READONLY_BG = "#2a1315"
READONLY_FG = "#ff8080"
TIER2_FG = "#d9a441"
STATUS_MUTED_FG = "#9a9aa3"
DETAIL_BG = "#0d0d11"

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
        (QPalette.ColorRole.Link, ACCENT),
        (QPalette.ColorRole.LinkVisited, ACCENT),
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
QSS = """
/* ---- base ---- */
QMainWindow, QDialog { background: %(WINDOW_BG)s; }
QWidget { color: %(TEXT)s; }
QLabel { color: %(TEXT)s; background: transparent; }
#headerTitle { font-weight: 700; color: %(TEXT)s; }
#headerStats { color: %(TEXT_DIM)s; }
#badgeReadOnly { background: %(READONLY_BG)s; color: %(READONLY_FG)s;
                 font-weight: 700; padding: 2px 7px; border-radius: 3px; }
#badgeTier2 { color: %(TIER2_FG)s; padding: 2px 5px; }

/* ---- grouped controls ---- */
QGroupBox { background: %(SURFACE)s; border: 1px solid %(BORDER)s;
            border-radius: 4px; margin-top: 9px; padding: 7px 7px 4px 7px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                   padding: 0 5px; color: %(TEXT_MUTED)s; }
QPushButton { background: %(HEADER_BG)s; border: 1px solid %(BORDER_STRONG)s;
              border-radius: 3px; padding: 3px 9px; color: %(TEXT)s; }
QPushButton:hover { background: %(HEADER_HOVER)s; border-color: %(TEXT_MUTED)s; }
QPushButton:pressed { background: %(SURFACE_ALT)s; }
QPushButton:focus { border-color: %(ACCENT)s; }
QPushButton:disabled { color: %(TEXT_MUTED)s; background: %(SURFACE)s; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: %(SURFACE_ALT)s; border: 1px solid %(BORDER_STRONG)s;
    border-radius: 3px; padding: 2px 6px; color: %(SELECT_FG)s;
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
                        border-top: 5px solid %(TEXT_MUTED)s; }
QComboBox QAbstractItemView { background: %(SURFACE_ALT)s;
    border: 1px solid %(BORDER_STRONG)s; color: %(SELECT_FG)s;
    selection-background-color: %(SELECT_BG)s;
    selection-color: %(SELECT_FG)s; outline: 0; }

/* ---- tables (item backgrounds come from row tags, not here) ---- */
QTableWidget { background: %(SURFACE)s; alternate-background-color: %(SURFACE)s;
               border: 1px solid %(BORDER)s; gridline-color: transparent;
               outline: 0; }
QTableWidget::item { padding: 0 5px; border: none; }
QTableWidget::item:selected { background: %(SELECT_BG)s;
                              color: %(SELECT_FG)s; }
QTableWidget:focus { border-color: %(BORDER_STRONG)s; }
QHeaderView::section { background: %(HEADER_BG)s; color: %(TEXT_MUTED)s;
    border: none; border-right: 1px solid %(BORDER)s;
    border-bottom: 1px solid %(BORDER)s; padding: 5px 6px;
    font-weight: 600; }
QHeaderView::section:hover { background: %(HEADER_HOVER)s;
                             color: %(TEXT)s; }
QHeaderView::section:checked { background: %(SELECT_BG)s;
                               color: %(SELECT_FG)s; }
QTableCornerButton::section { background: %(HEADER_BG)s; border: none; }

/* ---- detail pane ---- */
QTextEdit { background: %(DETAIL_BG)s; border: 1px solid %(BORDER)s;
            selection-background-color: %(SELECT_BG)s;
            selection-color: %(SELECT_FG)s; }

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
QScrollBar::handle:vertical { background: %(BORDER_STRONG)s;
    border-radius: 5px; min-height: 26px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: %(TEXT_MUTED)s; }
QScrollBar:horizontal { background: %(SURFACE)s; height: 11px; margin: 0;
                        border: none; }
QScrollBar::handle:horizontal { background: %(BORDER_STRONG)s;
    border-radius: 5px; min-width: 26px; margin: 2px; }
QScrollBar::handle:horizontal:hover { background: %(TEXT_MUTED)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: %(SURFACE)s; }

/* ---- misc ---- */
QToolTip { background: %(HEADER_BG)s; color: %(SELECT_FG)s;
           border: 1px solid %(BORDER_STRONG)s; padding: 4px 7px; }
QSplitter { background: %(WINDOW_BG)s; }
""" % {
    "WINDOW_BG": WINDOW_BG, "SURFACE": SURFACE, "SURFACE_ALT": SURFACE_ALT,
    "HEADER_BG": HEADER_BG, "HEADER_HOVER": HEADER_HOVER,
    "BORDER": BORDER, "BORDER_STRONG": BORDER_STRONG,
    "TEXT": TEXT, "TEXT_DIM": TEXT_DIM, "TEXT_MUTED": TEXT_MUTED,
    "SELECT_BG": SELECT_BG, "SELECT_FG": SELECT_FG, "ACCENT": ACCENT,
    "READONLY_BG": READONLY_BG, "READONLY_FG": READONLY_FG,
    "TIER2_FG": TIER2_FG, "DETAIL_BG": DETAIL_BG,
}


def apply_theme(app):
    """Install the dark terminal theme on a QApplication. Idempotent.

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
