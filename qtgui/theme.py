"""Design tokens + QSS theme for the ALT RADAR Qt port (PySide6 Widgets).

Light readability-first language (decided with the operator: the dark
terminal made analysis hard). Near-white surfaces, ink text, DISTINCT
hues per element type so a glance separates direction, warnings, badges,
and data: semantic LONG green / SHORT red kept as direction tints (never
decoration), amber warnings, blue selection + links, gold-tier accents.
Mono numerals in every data pane. Fusion is the base style; this file
layers a QSS stylesheet and a light palette on top of it.

No per-file hex outside this module — every color qtgui/app.py uses is a
named constant here.

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

# ---- palette (base + semantics): light, distinct, high-contrast ----
WINDOW_BG = "#e9edf2"       # cool light grey-blue app surround
SURFACE = "#ffffff"         # panel / table base
SURFACE_ALT = "#f1f4f8"     # banded row
HEADER_BG = "#dde4ec"       # table header + raised controls
HEADER_HOVER = "#ccd6e2"
BORDER = "#c3ccd8"
BORDER_STRONG = "#9fabbd"
TEXT = "#16202c"            # ink navy-black body text
TEXT_DIM = "#334052"
TEXT_MUTED = "#5d6b7e"
SELECT_BG = "#1d4ed8"       # strong blue selection (white text on top)
SELECT_FG = "#ffffff"
ACCENT = "#1d4ed8"          # THE accent — links, focus rings, primary actions

LONG_BG = "#d9f0de"         # distinct LONG green tint
LONG_BG_ALT = "#c6e8cf"
SHORT_BG = "#fbdede"        # distinct SHORT red tint
SHORT_BG_ALT = "#f3c9c5"
VETO_BG = "#eef0f3"         # neutral grey veto rows
VETO_BG_ALT = "#e2e5ea"
VETO_FG = "#5f6b7a"
WATCH_FG = "#b45309"        # dark amber (contrast-safe on white)
WARN_FG = "#b45309"
ERROR_FG = "#b91c1c"
READONLY_BG = "#fee2e2"
READONLY_FG = "#b91c1c"
TIER2_FG = "#92600a"        # bronze tier marker
STATUS_MUTED_FG = "#5d6b7e"
DETAIL_BG = "#ffffff"

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


def light_palette():
    """Fusion-compatible light QPalette (base roles + disabled states)."""
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
               border: 1px solid %(BORDER)s; gridline-color: %(BORDER)s;
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
    """Install the light readability theme on a QApplication. Idempotent.

    Fusion is the base style (predictable cross-platform metrics for the
    dense tables); the QSS + palette layer on top supply the look.
    """
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setStyleSheet(QSS)
    app.setFont(ui_font())
    return True


def is_applied(app):
    """True when this module's stylesheet is already installed on `app`."""
    try:
        return app.styleSheet().strip().startswith("/* ---- base ---- */")
    except Exception:
        return False
