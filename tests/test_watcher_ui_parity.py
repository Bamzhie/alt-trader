"""Cross-surface watcher parity (Task 6).

Self-checking script: `python3 tests/test_watcher_ui_parity.py`.

Pins what the carried rulings require, deriving every surface's text
from its REAL render path — never duplicated literals:

  TUI : proto.app.App.draw_watcher / draw_watcher_detail -> FakeStdscr
  tk  : gui.model.watcher_detail_text (the exact pure function the
        tkinter pane renders) + RadarGUI._watcher_detail_full_text
        called unbound on a namespace (no display needed)
  Qt  : offscreen RadarWindow._on_watcher -> QTextEdit.toPlainText
        (QT_QPA_PLATFORM=offscreen; clean skip without PySide6)

Checks:
  1. all surfaces render STATE_WORDS verbs byte-identical
     (tui_state_text == qt_state_text == tk_state_text == word)
  2. the paused notice reads on all surfaces
  3. unknown states default to active + composite spellings
     (resolved_stop / expired_time / ...) resolve identically
     everywhere (Task 1 tolerance, pinned here)
  4. the CORRELATED OPENS section is byte-identical to
     proto.watcher_report's shape on all three detail panes and on
     the TUI table (shared proto.watcher_feed.correlated_lines).

Network policy: ZERO network (temp-dir SQLite via emit_setup only).
"""

import os
import re
import sqlite3
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from proto import app as appmod  # noqa: E402
from proto import watcher_feed as feed_mod  # noqa: E402
from proto import watcher_report as report_mod  # noqa: E402
from proto.watcher_view import STATE_WORDS, build_panel  # noqa: E402
from gui import model as gui_model  # noqa: E402  (pure: no Tk, no network)

FAILURES = []
CHECKS = 0


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    cond = bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)
    return cond


STATE_LINE = re.compile(r"^  state (\S+)", re.M)


def state_word_in(text):
    m = STATE_LINE.search(text or "")
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# fixtures: temp DBs only (no network, no venue)
# --------------------------------------------------------------------------

def temp_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def seed_single_db(state):
    """Temp DB with one setup forced to `state` (arbitrary strings ok)."""
    from proto.watcher import emit_setup

    path = temp_path()
    conn = sqlite3.connect(path)
    try:
        sid = emit_setup(conn, {"rule_id": "t1-break-v1", "coin": "AAA",
                                "src_bar_ts": 1000, "entry": 100.0,
                                "direction": "LONG", "stop_px": 99.0,
                                "tgt_px": 102.0})
        conn.execute("UPDATE watcher_setup SET state=? WHERE id=?",
                     (state, sid))
        conn.commit()
    finally:
        conn.close()
    return path, sid


def seed_correlated_db():
    """Temp DB: two active same-coin setups (one n=2 group) + resolved."""
    from proto.watcher import emit_setup

    path = temp_path()
    conn = sqlite3.connect(path)
    try:
        sid_a1 = emit_setup(
            conn, {"rule_id": "t1-break-v1", "coin": "BBB",
                   "src_bar_ts": 2000, "entry": 100.0,
                   "direction": "SHORT", "stop_px": 101.0,
                   "tgt_px": 98.0})
        # Same (coin, rule) 198000s later: past the 24h emit cooldown, so
        # both rows persist (verified pattern from test_qtgui_watcher.py).
        sid_a2 = emit_setup(
            conn, {"rule_id": "t1-break-v1", "coin": "BBB",
                   "src_bar_ts": 200000, "entry": 110.0,
                   "direction": "LONG", "stop_px": 109.0,
                   "tgt_px": 112.0})
        sid_res = emit_setup(
            conn, {"rule_id": "t1-break-v1", "coin": "AAA",
                   "src_bar_ts": 1000, "entry": 50.0,
                   "direction": "LONG", "stop_px": 49.0,
                   "tgt_px": 52.0})
        conn.execute("UPDATE watcher_setup SET state='resolved' WHERE id=?",
                     (sid_res,))
        conn.commit()
    finally:
        conn.close()
    assert len({sid_a1, sid_a2, sid_res}) == 3, (sid_a1, sid_a2, sid_res)
    return path, sid_a1, sid_a2, sid_res


def single_row(state, sid="r:AAA:1000", coin="AAA"):
    return {"id": sid, "rule_id": "t1-break-v1", "coin": coin,
            "direction": "LONG", "src_bar_ts": 1000, "entry": 100.0,
            "stop_px": 99.0, "tgt_px": 102.0, "state": state,
            "monitoring": "ok"}


# --------------------------------------------------------------------------
# TUI surface: real App render path -> FakeStdscr (no curses init)
# --------------------------------------------------------------------------

class FakeStdscr:
    def __init__(self, h=40, w=160):
        self.h = h
        self.w = w
        self.texts = []

    def erase(self):
        pass

    def border(self, *a):
        pass

    def addstr(self, y, x, s, *a):
        self.texts.append(s)

    def hline(self, *a):
        pass

    def refresh(self):
        pass

    def getmaxyx(self):
        return (self.h, self.w)

    @property
    def out(self):
        return "\n".join(self.texts)


class FakeArgs:
    stake = 0.10
    coins = 10
    interval = 60
    db = None
    log_threshold = 18.0
    write_logs = False


def make_tui_app(db_path):
    args = FakeArgs()
    args.db = db_path
    args.write_logs = False
    return appmod.App(args)


def tui_detail_out(state):
    """Render the TUI watcher detail for one setup in `state`."""
    path, sid = seed_single_db(state)
    try:
        app = make_tui_app(path)
        app.on_key(ord("w"))
        # Pin the id (non-active states never self-select on load).
        app.watcher_panel = feed_mod.load_panel(path, selected_id=sid)
        app.watcher_selected = sid
        app.on_key(ord("\n"))  # ENTER opens detail
        fake = FakeStdscr()
        app.draw_watcher_detail(fake, 40, 160)
        out = fake.out
        app.store.close()
        return out
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def tui_state_text(state):
    return state_word_in(tui_detail_out(state))


def tui_notices_when(paused):
    path, _sid = seed_single_db("active")
    try:
        conn = sqlite3.connect(path)
        try:
            feed_mod.set_paused(conn, paused)
        finally:
            conn.close()
        app = make_tui_app(path)
        app.watcher_panel = feed_mod.load_panel(path)
        app.watcher_selected = app.watcher_panel.selected_id
        fake = FakeStdscr()
        app.draw_watcher_detail(fake, 40, 160)
        out = fake.out
        app.store.close()
        return out
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# --------------------------------------------------------------------------
# tk surface: the exact pure function the pane renders (+ the real join)
# --------------------------------------------------------------------------

def tk_state_text(state):
    row = single_row(state)
    panel = build_panel([row], {}, selected_id=row["id"])
    return state_word_in(gui_model.watcher_detail_text(panel, panel.selected_id))


def tk_notices_when(paused):
    panel = build_panel([single_row("active")], {}, paused=paused)
    return gui_model.watcher_detail_text(panel, panel.selected_id)


def tk_full_text(db_path, sid):
    """The tkinter pane's full text via the REAL join method (unbound, so
    no display is needed): shared model text + shared correlated lines."""
    from gui.app import RadarGUI
    from types import SimpleNamespace

    panel = feed_mod.load_panel(db_path)
    ns = SimpleNamespace(watcher_panel=panel, watcher_selected=sid,
                         db=db_path)
    return RadarGUI._watcher_detail_full_text(ns)


# --------------------------------------------------------------------------
# Qt surface: offscreen window, real handler -> real widget text
# --------------------------------------------------------------------------

try:
    import PySide6  # noqa: F401
    from PySide6.QtWidgets import QApplication  # noqa: F401
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


class _QtSkipped(Exception):
    """PySide6 missing: Qt parity checks skip, never fail."""


_QT_APP = None
_QT_WIN = None


def _qt_window(db_path):
    """One offscreen RadarWindow with retired worker lanes (the
    tests/test_qtgui_watcher.py pattern: ctor jobs run, then the
    sentinel retires both lanes, so queue -> poll -> render is real
    but no scan/plan/collect/resolve job can ever execute)."""
    global _QT_APP, _QT_WIN
    if _QT_WIN is not None:
        return _QT_WIN
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from qtgui.app import RadarWindow

    _QT_APP = QApplication.instance() or QApplication([])
    win = RadarWindow(auto_scan=False, db=db_path)
    win.show()
    win._jobs.put(None)
    win._worker.join(timeout=10)
    assert not win._worker.is_alive(), "worker lane did not retire"
    win._plan_jobs.put(None)
    win._plan_worker.join(timeout=10)
    assert not win._plan_worker.is_alive(), "plan lane did not retire"
    win._tick_timer.stop()
    win._poll_timer.stop()
    _QT_WIN = win
    return win


def _qt_close():
    global _QT_WIN
    if _QT_WIN is not None:
        try:
            if not _QT_WIN._closing:
                _QT_WIN._on_close()
        except Exception:
            pass
        _QT_WIN = None


def qt_detail_out(state):
    if not QT_AVAILABLE:
        raise _QtSkipped("PySide6 not installed")
    path, sid = seed_single_db(state)
    try:
        panel = feed_mod.load_panel(path, selected_id=sid)
        win = _qt_window(path)
        win.db = path  # correlated section reads this DB
        win._on_watcher({"kind": "watcher", "ok": True, "panel": panel,
                         "selected_id": panel.selected_id})
        if _QT_APP is not None:
            _QT_APP.processEvents()
        return win.watcher_detail.toPlainText()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def qt_state_text(state):
    return state_word_in(qt_detail_out(state))


def qt_notices_when(paused):
    if not QT_AVAILABLE:
        raise _QtSkipped("PySide6 not installed")
    path, _sid = seed_single_db("active")
    try:
        conn = sqlite3.connect(path)
        try:
            feed_mod.set_paused(conn, paused)
        finally:
            conn.close()
        panel = feed_mod.load_panel(path)
        win = _qt_window(path)
        win.db = path
        win._on_watcher({"kind": "watcher", "ok": True, "panel": panel,
                         "selected_id": panel.selected_id})
        if _QT_APP is not None:
            _QT_APP.processEvents()
        return win.watcher_detail.toPlainText()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# Brief Step 1 surface loop: every surface exposes the same two seams.
class _Surface:
    def __init__(self, name, state_fn, notices_fn):
        self.name = name
        self._state_fn = state_fn
        self._notices_fn = notices_fn

    def state_text(self, state):
        return self._state_fn(state)

    def notices_when(self, paused):
        return self._notices_fn(paused)


SURFACES = (_Surface("tui", tui_state_text, tui_notices_when),
            _Surface("tk", tk_state_text, tk_notices_when),
            _Surface("qt", qt_state_text, qt_notices_when))


# ==========================================================================
# 1. all surfaces share the STATE_WORDS verbs verbatim
# ==========================================================================

def t_shared_state_words():
    for word in ("active", "invalidated", "expired", "resolved"):
        got = {}
        for surface in SURFACES:
            try:
                got[surface.name] = surface.state_text(word)
            except _QtSkipped as e:
                print(f"  SKIP  qt state_text({word}): {e}")
                got[surface.name] = word  # skip, never fail
        check(f"state {word!r} identical on all surfaces",
              got.get("tui") == got.get("tk") == got.get("qt") == word,
              str(got))


# ==========================================================================
# 2. the paused notice reads on all surfaces
# ==========================================================================

def t_pause_notice():
    for surface in SURFACES:
        try:
            text = surface.notices_when(paused=True)
        except _QtSkipped as e:
            print(f"  SKIP  qt notices_when: {e}")
            continue
        check(f"paused notice on {surface.name}",
              "paused" in (text or ""), (text or "")[:160])
    for surface in SURFACES:
        if surface.name == "qt" and not QT_AVAILABLE:
            continue
        try:
            text = surface.notices_when(paused=False)
        except _QtSkipped:
            continue
        check(f"no paused notice on {surface.name} when running",
              "paused" not in (text or ""), (text or "")[:160])


# ==========================================================================
# 3. unknown -> active default + composite tolerance, identical everywhere
# ==========================================================================

def t_unknown_and_composite():
    cases = [("mystery", "active"),          # unknown defaults active
             ("resolved_stop", "resolved"),
             ("resolved/target", "resolved"),
             ("expired_time", "expired"),
             ("invalidated_partial", "invalidated")]
    for raw, expected in cases:
        got = {}
        for surface in SURFACES:
            try:
                got[surface.name] = surface.state_text(raw)
            except _QtSkipped:
                print(f"  SKIP  qt state_text({raw})")
                got[surface.name] = expected  # skip, never fail
        check(f"{raw!r} -> {expected!r} on all surfaces",
              got.get("tui") == got.get("tk") == got.get("qt") == expected,
              str(got))
    # grouping agrees: the unknown-state setup lands in "active"
    panel = build_panel([single_row("mystery", sid="r:ZZZ:9")], {})
    check("unknown state groups as active",
          panel.groups["active"] == ["r:ZZZ:9"], str(panel.groups))


# ==========================================================================
# 4. CORRELATED OPENS: one shared shape on every detail pane + TUI table
# ==========================================================================

def report_section_lines(db_path):
    """The CORRELATED block straight out of format_watcher_report."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        text = report_mod.format_watcher_report(conn)
    finally:
        conn.close()
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.startswith("CORRELATED OPENS"))
    end = next(i for i, line in enumerate(lines[start + 1:], start + 1)
               if not line.startswith(" "))
    return lines[start:end]


def t_correlated_parity():
    path, _a1, _a2, sid_res = seed_correlated_db()
    try:
        expected = feed_mod.correlated_lines(path)
        check("shared helper yields the CORRELATED header",
              expected and expected[0] == "CORRELATED OPENS "
              "(>1 active in group; not independent bets)",
              str(expected[:2]))
        check("same-coin pair reads as one n=2 group",
              any("n=2" in line for line in expected), str(expected))
        check("shared helper matches the report block byte-for-byte",
              expected == report_section_lines(path),
              f"helper={expected!r} report={report_section_lines(path)!r}")
        check("missing DB yields no section (never a raise)",
              feed_mod.correlated_lines("/nonexistent/x.db") == [])

        # TUI detail pane (real draw path)
        app = make_tui_app(path)
        app.watcher_panel = feed_mod.load_panel(path)
        app.watcher_selected = sid_res
        fake = FakeStdscr()
        app.draw_watcher_detail(fake, 40, 160)
        for line in expected:
            check(f"TUI detail carries {line.strip()[:40]!r}",
                  line in fake.out, fake.out[-300:])
        app.store.close()

        # TUI table (real draw path)
        app = make_tui_app(path)
        app.on_key(ord("w"))
        fake = FakeStdscr()
        app.draw_watcher(fake, 40, 160)
        check("TUI table carries the CORRELATED OPENS section",
              "CORRELATED OPENS" in fake.out, fake.out[-300:])
        check("TUI table marks the n=2 group",
              "n=2" in fake.out, fake.out[-300:])
        app.store.close()

        # tk detail pane (real join method, unbound: no display needed)
        full = tk_full_text(path, sid_res)
        for line in expected:
            check(f"tk detail carries {line.strip()[:40]!r}",
                  line in full, full[-300:])

        # Qt detail pane (real offscreen handler -> widget text)
        if not QT_AVAILABLE:
            print("  SKIP  Qt correlated section: PySide6 not installed")
        else:
            win = _qt_window(path)
            win.db = path
            panel = feed_mod.load_panel(path)
            win._on_watcher({"kind": "watcher", "ok": True,
                             "panel": panel, "selected_id": sid_res})
            if _QT_APP is not None:
                _QT_APP.processEvents()
            detail = win.watcher_detail.toPlainText()
            for line in expected:
                check(f"Qt detail carries {line.strip()[:40]!r}",
                      line in detail, detail[-300:])
    finally:
        _qt_close()
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    print("== 1 shared state words ==")
    t_shared_state_words()
    print("== 2 pause notice on all surfaces ==")
    t_pause_notice()
    print("== 3 unknown-default + composite tolerance ==")
    t_unknown_and_composite()
    print("== 4 correlated-opens parity ==")
    t_correlated_parity()
    print()
    print(f"CHECKS {CHECKS}  FAILURES {len(FAILURES)}")
    if FAILURES:
        print("FAILURES:")
        for name in FAILURES:
            print(f"  - {name}")
        print("WATCHER PARITY TESTS FAILED")
        return 1
    print("ALL PASS  tests/test_watcher_ui_parity.py — cross-surface parity green")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        _qt_close()
    sys.exit(rc)
