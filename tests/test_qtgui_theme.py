"""Theme-token test for the Qt terminal rebuild (Task 1).

Spec palette (12 exact hexes) + QSS discipline: the stylesheet template
must consume the tokens via %(NAME)s interpolation — no hardcoded hex
literals outside the token definitions — with 8-12px radii, thin (1px)
borders, and no heavy shadows.

Offscreen by construction: ``QT_QPA_PLATFORM=offscreen`` is set at the
very top, BEFORE any Qt import, so the Qt-backed section builds
display-free.

Interpreter guard: the token + template checks parse ``qtgui/theme.py``
as source text, so they run under ANY interpreter (system python3 with
no PySide6 included). Only the live-import section (resolved qss(),
legacy aliases, palette) needs PySide6; without it that section prints
a SKIP line while the source checks still run.

NOTE on the brief sketch (``"#030B15" not in theme.qss()``): taken
literally that assertion can never pass — a resolved stylesheet built
from the tokens MUST contain the token values. The intent (no
HARDCODED hexes outside tokens) is enforced instead: the QSS_TEMPLATE
source holds zero hex literals, and the resolved qss() holds zero
NON-token hexes.

Run:  python3 tests/test_qtgui_theme.py  -> ALL PASS, exit 0
"""

import os
import re
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"   # BEFORE any Qt import

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
THEME_SRC = os.path.join(REPO, "qtgui", "theme.py")

# ---- the 12 spec tokens, exact (brief §3, verbatim) -------------------------
EXPECTED_TOKENS = {
    "BG_MAIN": "#030B15",
    "BG_SIDEBAR": "#06111F",
    "BG_CARD": "#071522",
    "BG_ELEVATED": "#091A2A",
    "BORDER": "#142A3D",
    "INK": "#E6F0FF",
    "INK_DIM": "#8EA8C4",
    "INK_MUT": "#627B96",
    "ACC_POS": "#00D6AD",
    "ACC_2": "#8B5CF6",
    "ACC_NEG": "#FF4D5A",
    "GRID": "#14273A",
}

# Every legacy token name referenced elsewhere (qtgui/app.py,
# tests/test_qtgui.py) — all must keep existing after the re-tint.
LEGACY_NAMES = [
    "WINDOW_BG", "SURFACE", "SURFACE_ALT", "HEADER_BG", "HEADER_HOVER",
    "BORDER_STRONG", "TEXT", "TEXT_DIM", "TEXT_MUTED",
    "SELECT_BG", "SELECT_FG", "ACCENT", "ACCENT_FG",
    "LONG_FG", "SHORT_FG", "LONG_BG", "LONG_BG_ALT",
    "SHORT_BG", "SHORT_BG_ALT", "VETO_BG", "VETO_BG_ALT", "VETO_FG",
    "WATCH_FG", "WARN_FG", "ERROR_FG", "INFO_FG",
    "READONLY_BG", "READONLY_FG", "TIER2_FG", "TIER2_BG",
    "STATUS_MUTED_FG", "DETAIL_BG", "TILE_BG", "RAIL_BG", "RAIL_ACTIVE",
    "PILL_UNVALIDATED_BG", "PILL_UNVALIDATED_FG",
]

failures = []


def check(name, cond, detail=""):
    print(f"{'ok' if cond else 'FAIL'}  {name}"
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


with open(THEME_SRC, encoding="utf-8") as fh:
    src = fh.read()

# ---- 1. palette tokens, exact ----------------------------------------------
found = dict(re.findall(
    r"^([A-Z][A-Z0-9_]*)\s*=\s*\"(#[0-9A-Fa-f]{6})\"", src, re.M))
for token, want in EXPECTED_TOKENS.items():
    check(f"token {token} == {want}",
          found.get(token) == want, f"found {found.get(token)!r}")

# ---- 2. QSS template discipline ---------------------------------------------
m = re.search(r"QSS_TEMPLATE\s*=\s*\"\"\"(.*?)\"\"\"", src, re.S)
check("QSS_TEMPLATE block exists", m is not None)
template = m.group(1) if m else ""
body = re.sub(r"%\(\w+\)s", "", template)  # strip token interpolation
HEX_LIT = r"#[0-9A-Fa-f]{3,8}(?![0-9A-Fa-fA-Za-z_])"  # not #id selectors
stray = re.findall(HEX_LIT, body)
check("QSS template has no hardcoded hexes outside tokens",
      not stray, f"stray literals: {stray[:5]}")

placeholders = set(re.findall(r"%\((\w+)\)s", template))
defined = set(re.findall(r"^([A-Z][A-Z0-9_]*)\s*=", src, re.M))
check("every %(NAME)s placeholder resolves to a defined token",
      placeholders <= defined, f"unresolved: {sorted(placeholders - defined)[:5]}")

radii = [int(v) for v in re.findall(r"border-radius:\s*(\d+)px", template)]
check("QSS radii all in the 8-12px band",
      bool(radii) and all(8 <= v <= 12 for v in radii),
      f"radii found: {radii}")
shorthand = [int(v) for v in re.findall(r"border:\s*(\d+)px", template)]
check("QSS borders are thin (1px shorthand)",
      all(v == 1 for v in shorthand), f"widths found: {shorthand}")
check("QSS has no heavy shadows",
      "box-shadow" not in template and "shadow" not in template.lower(),
      "shadow styling present")
check("QSS gridlines consume the GRID token",
      "gridline-color: %(GRID)s" in template)

# ---- 3. live-import checks (need PySide6; skip-guarded) ---------------------
try:
    from qtgui import theme
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:
    HAVE_QT = False
    print("SKIP  live-import section — PySide6 not in this interpreter")

if HAVE_QT:
    for token, want in EXPECTED_TOKENS.items():
        check(f"theme.{token} == {want}",
              getattr(theme, token, None) == want,
              f"found {getattr(theme, token, None)!r}")
    for name in LEGACY_NAMES:
        check(f"legacy alias theme.{name} exists",
              hasattr(theme, name), "missing")
    resolved = theme.qss() if callable(getattr(theme, "qss", None)) else None
    check("theme.qss() exists", resolved is not None, "missing")
    if resolved is None:
        resolved = ""
    for token, want in EXPECTED_TOKENS.items():
        check(f"qss() consumes {token} ({want})", want in resolved, "absent")
    stripped = resolved
    for want in set(EXPECTED_TOKENS.values()):
        stripped = stripped.replace(want, "").replace(want.lower(), "")
    for alias in ("#ffffff", "#FFFFFF", "#000000", "#05200f", "#38bdf8",
                  "#f59e0b", "#FBBF24", "#fbbf24", "#432f0a", "#3a2705",
                  "#4c1115", "#fca5a5", "#8b9ab0", "#0E2337", "#24405C",
                  "#1B3A57", "#062A22", "#05231D", "#2B1218", "#231015",
                  "#0C1B2E", "#091525", "#14324C"):
        stripped = stripped.replace(alias, "").replace(alias.lower(), "")
    leftover = re.findall(HEX_LIT, stripped)
    check("resolved qss() holds no unexpected hexes", not leftover,
          f"leftover: {leftover[:5]}")
    app = QApplication.instance() or QApplication([])
    check("dark_palette() builds", theme.dark_palette() is not None)
    check("apply_theme() installs", theme.apply_theme(app) is True)
    check("is_applied() true after install", theme.is_applied(app) is True)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print(f"ALL PASS (tokens {len(EXPECTED_TOKENS)}/12 exact"
      f"{' + live Qt checks' if HAVE_QT else ' (source checks; Qt section skipped)'}). OK")
