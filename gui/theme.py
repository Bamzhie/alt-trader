"""Design tokens + theme application for the ALT RADAR desktop GUI.

Single source of truth for every color, tag, font role, and spacing rhythm
used by gui/app.py. Nothing here imports tkinter at module level, so token
values stay unit-testable display-free; only apply_theme() touches Tk.

Direction (redesign-preserve, trading-terminal language):
- one neutral base (zinc-ish), single restrained accent; LONG/SHORT greens
  and reds are SEMANTIC (direction), not decoration — they stay, refined.
- density stays cockpit-high; tabular numbers always mono.
- single light theme (the shipped product); no per-screen hex outside this
  file — enforced by tests/test_gui.py::TestNoHardcodedColors.
"""

# ---- palette (base + semantics) ----
SURFACE = "#ffffff"
SURFACE_ALT = "#f4f6f8"
TEXT = "#1a1d21"
TEXT_MUTED = "#5b6470"
HEADER_BG = "#e9edf1"
SELECT_BG = "#d7e5ff"
SELECT_FG = "#1a1d21"

LONG_BG = "#e7f4ea"
LONG_BG_ALT = "#dcefe2"
SHORT_BG = "#fbe9e9"
SHORT_BG_ALT = "#f4dcdc"
VETO_BG = "#efefef"
VETO_BG_ALT = "#e4e4e4"
VETO_FG = "#8d8d8d"
WATCH_FG = "#c2410c"
WARN_FG = "#c2410c"
ERROR_FG = "#b91c1c"
READONLY_BG = "#fde8e8"
TIER2_FG = "#92400e"
STATUS_MUTED_FG = "#555555"
DETAIL_BG = "#fbfbfb"

# ---- row tags (direction tint x even/odd band) ----
TAG_LONG = "long"
TAG_LONG_ALT = "long_alt"
TAG_SHORT = "short"
TAG_SHORT_ALT = "short_alt"
TAG_PLAIN = "plain"
TAG_PLAIN_ALT = "plain_alt"
TAG_VETOED = "vetoed"
TAG_VETOED_ALT = "vetoed_alt"
TAG_WATCH = "watch"          # foreground-only, layered over the above

TAG_BACKGROUNDS = {
    TAG_LONG: LONG_BG,
    TAG_LONG_ALT: LONG_BG_ALT,
    TAG_SHORT: SHORT_BG,
    TAG_SHORT_ALT: SHORT_BG_ALT,
    TAG_PLAIN: SURFACE,
    TAG_PLAIN_ALT: SURFACE_ALT,
    TAG_VETOED: VETO_BG,
    TAG_VETOED_ALT: VETO_BG_ALT,
}

# ---- type + rhythm ----
ROW_HEIGHT = 24
GROUP_PAD_X = 6
BUTTON_PAD_X = 2


def apply_theme(root):
    """Configure ttk styles + shared widget defaults. Call once, Tk-only."""
    import tkinter.font as tkfont
    from tkinter import ttk

    try:
        root.tk.call("package", "require", "ttk")
    except Exception:
        pass
    try:
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        style = ttk.Style(root)

    ui_font = tkfont.nametofont("TkDefaultFont")
    mono_font = tkfont.nametofont("TkFixedFont")
    heading_font = ui_font.copy()
    heading_font.configure(weight="bold")

    style.configure("Treeview",
                    background=SURFACE, fieldbackground=SURFACE,
                    foreground=TEXT, font=ui_font, rowheight=ROW_HEIGHT,
                    borderwidth=0)
    style.configure("Treeview.Heading",
                    background=HEADER_BG, foreground=TEXT, font=heading_font,
                    relief="flat", padding=(6, 4, 6, 4))
    style.map("Treeview",
              background=[("selected", SELECT_BG)],
              foreground=[("selected", SELECT_FG)])
    style.configure("TButton", padding=(8, 3))
    style.configure("TEntry", padding=3)
    style.configure("TCombobox", padding=3)
    style.configure("TLabelframe", background=SURFACE)
    style.configure("TLabelframe.Label",
                    foreground=TEXT_MUTED, font=ui_font)
    style.configure("ReadOnly.TLabel",
                    foreground=ERROR_FG, background=READONLY_BG)
    style.configure("Tier2.TLabel", foreground=TIER2_FG)
    return style
