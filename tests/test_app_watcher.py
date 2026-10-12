"""TUI watcher screen (Task 3).

Self-checking script: `python3 tests/test_app_watcher.py`.
Covers the `w` key toggle seam (`App.on_key` / `App.show_watcher`),
watcher table + detail rendering to a fake stdscr (no curses init),
verbatim STATE_WORDS state text, research banner, id-pinned selection,
contained "watcher data unavailable" fallback, and scanner keys/sorts/
logging untouched. ZERO network (temp-DB SQLite only).
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import app as appmod  # noqa: E402
from proto.watcher_view import RESEARCH_BANNER  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


class FakeStdscr:
    """Records addstr calls; no curses init needed."""

    def __init__(self, h=24, w=100):
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


def temp_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def fixture_db(n=1, state="resolved"):
    """Temp DB with n watcher setups (all forced to `state`)."""
    from proto.watcher import emit_setup

    path = temp_path()
    conn = sqlite3.connect(path)
    sids = []
    for i in range(n):
        sid = emit_setup(conn, {"rule_id": "t1-break-v1", "coin": f"AAA{i}",
                                "src_bar_ts": 1000 + i, "entry": 100.0,
                                "direction": "LONG", "stop_px": 99.0,
                                "tgt_px": 102.0})
        sids.append(sid)
    conn.execute("UPDATE watcher_setup SET state=?", (state,))
    conn.commit()
    conn.close()
    return path, sids


def make_app(db_path):
    args = FakeArgs()
    args.db = db_path
    args.write_logs = False
    return appmod.App(args)


# --- Brief Step 1, test 1: w key toggles the watcher screen ---
try:
    path, _ = fixture_db()
    app = make_app(path)
    app.on_key(ord("w"))
    assert app.show_watcher is True, f"show_watcher={app.show_watcher!r}"
    app.on_key(ord("w"))
    assert app.show_watcher is False, f"show_watcher={app.show_watcher!r}"
    check("w key toggles watcher screen", True)
    app.store.close()
except (AttributeError, AssertionError) as e:
    check("w key toggles watcher screen", False, f"{type(e).__name__}: {e}")

# --- Brief Step 1, test 2: detail shows verbatim state + banner ---
try:
    path, sids = fixture_db(n=1, state="resolved")
    app = make_app(path)
    app.on_key(ord("w"))
    app.on_key(ord("\n"))  # ENTER opens detail
    assert app.watcher_detail is True, f"watcher_detail={app.watcher_detail!r}"
    fake = FakeStdscr()
    app.draw_watcher_detail(fake, 24, 100)
    assert "resolved" in fake.out, f"no 'resolved' in:\n{fake.out}"
    assert "research, shadow-only" in fake.out, f"no banner in:\n{fake.out}"
    check("detail shows verbatim state + banner", True)
    app.store.close()
except (AttributeError, AssertionError) as e:
    check("detail shows verbatim state + banner", False, f"{type(e).__name__}: {e}")

# --- Table lists setups with verbatim state ---
try:
    path, sids = fixture_db(n=2, state="resolved")
    app = make_app(path)
    app.on_key(ord("w"))
    fake = FakeStdscr()
    app.draw_watcher(fake, 24, 100)
    assert "resolved" in fake.out, f"no 'resolved' in:\n{fake.out}"
    assert "AAA0" in fake.out and "AAA1" in fake.out, f"missing coins:\n{fake.out}"
    assert "research, shadow-only" in fake.out, f"no banner:\n{fake.out}"
    check("table lists setups with verbatim state", True)
    app.store.close()
except (AttributeError, AssertionError) as e:
    check("table lists setups with verbatim state", False, f"{type(e).__name__}: {e}")

# --- Selection pinned to setup id across reloads (never row index) ---
try:
    path, sids = fixture_db(n=2, state="active")
    app = make_app(path)
    app.on_key(ord("w"))
    app.watcher_selected = sids[1]
    app.refresh_watcher()
    assert app.watcher_selected == sids[1], f"lost id: {app.watcher_selected!r}"
    import curses
    app.on_key(curses.KEY_UP)
    assert app.watcher_selected == sids[0], f"no move: {app.watcher_selected!r}"
    check("selection pinned to setup id", True)
    app.store.close()
except (AttributeError, AssertionError) as e:
    check("selection pinned to setup id", False, f"{type(e).__name__}: {e}")

# --- w / ESC returns to the scanner ---
try:
    path, _ = fixture_db()
    app = make_app(path)
    app.on_key(ord("w"))
    app.on_key(ord("\n"))
    app.on_key(27)  # ESC
    assert app.show_watcher is False and app.watcher_detail is False
    check("w/ESC returns to scanner", True)
    app.store.close()
except (AttributeError, AssertionError) as e:
    check("w/ESC returns to scanner", False, f"{type(e).__name__}: {e}")

# --- Missing DB is contained: scanner-intact fallback, never a crash ---
try:
    path, _ = fixture_db()
    app = make_app(path)
    app.args.db = "/nonexistent/x.db"
    app.on_key(ord("w"))
    assert app.show_watcher is True
    fake = FakeStdscr()
    app.draw_watcher(fake, 24, 100)  # must not raise
    assert "watcher data unavailable" in fake.out, f"no fallback:\n{fake.out}"
    check("missing db renders contained fallback", True)
    app.store.close()
except (AttributeError, AssertionError) as e:
    check("missing db renders contained fallback", False, f"{type(e).__name__}: {e}")

# --- Scanner keys, sorts, logging logic untouched ---
try:
    path, _ = fixture_db()
    app = make_app(path)
    check("sort keys untouched",
          set(appmod.SORT_KEYS) == {"score", "early", "lean", "move", "vol"},
          str(sorted(appmod.SORT_KEYS)))
    check("scanner draws intact",
          callable(getattr(app, "draw_table", None))
          and callable(getattr(app, "draw_detail", None)))
    before = app.args.write_logs
    app.on_key(ord("w"))
    check("w no longer flips logging (moved to W)",
          app.args.write_logs is before, repr(app.args.write_logs))
    app.on_key(ord("W"))
    check("W toggles logging",
          app.args.write_logs is not before, repr(app.args.write_logs))
    check("research banner constant verbatim",
          RESEARCH_BANNER == "WATCHER (research, shadow-only — scanner untouched; no orders)",
          repr(RESEARCH_BANNER))
    app.store.close()
except (AttributeError, AssertionError) as e:
    check("scanner keys/sorts/logging untouched", False, f"{type(e).__name__}: {e}")

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
