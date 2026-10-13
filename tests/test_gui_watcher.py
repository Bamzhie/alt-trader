"""tkinter watcher side panel (Task 4).

Two layers in one file, following tests/test_gui.py patterns:

  MODEL  — pure helpers (no display): WatcherPanel order is active-first
           via gui.model.watcher_order, detail text renders STATE_WORDS
           verbs verbatim plus the research banner. Always runs.

  WIDGET — real RadarGUI windows are built only when Tk can open a
           display (HAS_DISPLAY gate: skip cleanly, never fail).

Network policy: ZERO network. Panels come from build_panel over
fixture dicts or temp-dir SQLite (emit_setup); each window's worker
thread is retired immediately after construction (None sentinel +
join — the ctor's offline stats/watcher jobs run before the
sentinel, so the real queue -> poll -> render path is exercised).
No scan / plan / collect / resolve job can ever run, hence no
network.

Run:  python3 tests/test_gui_watcher.py
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from proto.watcher_view import RESEARCH_BANNER, STATE_WORDS, build_panel
from gui.model import watcher_detail_text
from gui.model import watcher_order as gui_watcher_order


# --------------------------------------------------------------------------
# shared fixtures (plain dicts — no DB, no network)
# --------------------------------------------------------------------------

def fixture_rows():
    """One setup per terminal state + one monitoring-unavailable row."""
    base = {"rule_id": "t1-break-v1", "direction": "LONG", "entry": 100.0,
            "stop_px": 99.0, "tgt_px": 102.0, "monitoring": "ok"}
    rows = [
        dict(base, id="res-id-1", coin="AAA", src_bar_ts=1000,
             state="resolved"),
        dict(base, id="active-id-1", coin="BBB", src_bar_ts=2000,
             state="active"),
        dict(base, id="inv-id-1", coin="CCC", src_bar_ts=3000,
             state="invalidated"),
        dict(base, id="exp-id-1", coin="DDD", src_bar_ts=4000,
             state="expired"),
        dict(base, id="unavail-id-1", coin="EEE", src_bar_ts=5000,
             state="active", monitoring="unavailable"),
    ]
    return rows


# ==========================================================================
# MODEL LAYER — no display required
# ==========================================================================

class TestWatcherOrderModel(unittest.TestCase):
    def test_panel_lists_active_first(self):
        panel = build_panel(fixture_rows(), {}, paused=False)
        self.assertEqual(gui_watcher_order(panel),
                         ["active-id-1", "unavail-id-1", "inv-id-1",
                          "exp-id-1", "res-id-1"])

    def test_selection_survives_reorder(self):
        rows = fixture_rows()
        p1 = build_panel(list(reversed(rows)), {}, selected_id="active-id-1")
        p2 = build_panel(rows, {}, selected_id="active-id-1")
        self.assertEqual(p1.selected_id, p2.selected_id)
        self.assertEqual(p1.selected_id, "active-id-1")
        self.assertEqual(watcher_detail_text(p1, "active-id-1"),
                         watcher_detail_text(p2, "active-id-1"))

    def test_detail_shows_verbatim_state_and_banner(self):
        panel = build_panel(fixture_rows(), {}, paused=False)
        text = watcher_detail_text(panel, "res-id-1")
        self.assertIn(STATE_WORDS["resolved"], text)
        self.assertIn("research, shadow-only", text)
        self.assertIn(RESEARCH_BANNER, text)

    def test_detail_unavailable_panel_is_contained(self):
        text = watcher_detail_text(None, "active-id-1")
        self.assertIn("watcher data unavailable", text)
        self.assertIn("scanner intact", text)

    def test_detail_empty_panel_silence_valid(self):
        panel = build_panel([], {}, paused=False)
        self.assertEqual(gui_watcher_order(panel), [])
        text = watcher_detail_text(panel, None)
        self.assertIn("silence is valid", text)


# --------------------------------------------------------------------------
# display probe (import-safe: no Tk() unless the platform uses $DISPLAY)
# --------------------------------------------------------------------------

def _tk_available():
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


TK_AVAILABLE = _tk_available()


def _has_display():
    if not TK_AVAILABLE:
        return False
    if sys.platform.startswith(("linux", "freebsd")) and not os.environ.get("DISPLAY"):
        return False
    try:
        import tkinter as tk
        root = tk.Tk()
        root.destroy()
        return True
    except Exception:
        return False


HAS_DISPLAY = _has_display()

if TK_AVAILABLE:
    from tkinter import ttk
    from gui.app import RadarGUI

    class _TrackedRadarGUI(RadarGUI):
        """RadarGUI that records every after() timer id (see test_gui.py)."""

        def after(self, ms, func=None, *args):
            tid = super().after(ms, func, *args)
            ids = getattr(self, "_timer_ids", None)
            if ids is None:
                ids = self._timer_ids = []
            ids.append(tid)
            return tid
else:  # pragma: no cover - only on tkinter-less interpreters
    RadarGUI = None
    _TrackedRadarGUI = None


def seed_watcher_db(path):
    """Temp DB with one active + one resolved setup (offline, no network)."""
    import sqlite3
    from proto.watcher import emit_setup

    conn = sqlite3.connect(path)
    try:
        sid_active = emit_setup(
            conn, {"rule_id": "t1-break-v1", "coin": "BBB",
                   "src_bar_ts": 2000, "entry": 100.0,
                   "direction": "SHORT", "stop_px": 101.0,
                   "tgt_px": 98.0})
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
    return sid_active, sid_res


# ==========================================================================
# WIDGET LAYER — skipped cleanly when no display is available
# ==========================================================================

@unittest.skipUnless(HAS_DISPLAY,
                     "no usable display: DISPLAY missing or Tk cannot open one")
class TestWatcherWidgets(unittest.TestCase):
    """Real RadarGUI windows, withdrawn for the whole test, zero network."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="alt-radar-gui-watcher-")
        self.gui = None

    def tearDown(self):
        gui = self.gui
        if gui is not None and not gui._closing:
            for tid in getattr(gui, "_timer_ids", ()):
                try:
                    gui.after_cancel(tid)
                except Exception:
                    pass
            for name in ("var_header", "var_stake", "var_threshold", "var_coins",
                         "var_interval", "var_dir", "var_sort", "var_search",
                         "var_outcomes",
                         "var_activity", "var_failed", "var_statusline"):
                setattr(gui, name, None)
            gui._on_close()
        self.gui = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_gui(self, **kw):
        kw.setdefault("auto_scan", False)
        kw.setdefault("db", os.path.join(self.tmp, "gui.db"))
        gui = _TrackedRadarGUI(**kw)
        self.gui = gui
        gui.withdraw()
        gui._jobs.put(None)
        gui._worker.join(timeout=10)
        if gui._worker.is_alive():
            import traceback
            frames = sys._current_frames()
            stack = traceback.format_stack(frames.get(gui._worker.ident))
            self.fail("worker thread did not retire; stack:\n" + "".join(stack))
        gui._plan_jobs.put(None)
        gui._plan_worker.join(timeout=10)
        if gui._plan_worker.is_alive():
            self.fail("plan worker thread did not retire")
        return gui

    def pump_until(self, cond, timeout=10.0, step=0.02):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cond():
                return True
            self.gui.update()
            time.sleep(step)
        return cond()

    def buttons(self, text):
        import tkinter as tk
        out = []

        def walk(w):
            for child in w.winfo_children():
                if isinstance(child, ttk.Button) and str(child.cget("text")) == text:
                    out.append(child)
                walk(child)

        walk(self.gui)
        return out

    def _drain(self, q):
        out = []
        try:
            while True:
                out.append(q.get_nowait())
        except Exception:
            pass
        return out

    # ---- 1. widgets exist -------------------------------------------------
    def test_widget_builds_or_skips(self):
        import tkinter as tk
        gui = self.make_gui()
        self.assertIsInstance(gui.watcher_list, tk.Listbox)
        self.assertIsInstance(gui.watcher_detail, tk.Text)
        self.assertEqual(str(gui.watcher_detail.cget("state")), "disabled")

    # ---- 2. worker-fed panel lists active first ----------------------------
    def test_panel_lists_active_first_in_listbox(self):
        db = os.path.join(self.tmp, "watch.db")
        sid_active, sid_res = seed_watcher_db(db)
        gui = self.make_gui(db=db)
        self.assertTrue(
            self.pump_until(lambda: gui.watcher_panel is not None),
            "watcher result never reached the UI")
        ids = list(gui.watcher_list.get(0, "end"))
        # listbox lines lead with the setup id's coin; active (BBB) first
        self.assertTrue(ids[0].startswith("BBB"),
                        f"active setup not first: {ids!r}")
        self.assertTrue(any(line.startswith("AAA") for line in ids),
                        f"resolved setup missing: {ids!r}")
        self.assertEqual(gui.watcher_selected, sid_active)

    # ---- 3. selection drives the detail pane -------------------------------
    def test_selection_drives_detail_verbatim(self):
        db = os.path.join(self.tmp, "watch.db")
        seed_watcher_db(db)
        gui = self.make_gui(db=db)
        self.assertTrue(
            self.pump_until(lambda: gui.watcher_panel is not None),
            "watcher result never reached the UI")
        # NOTE (verified empirically on this Tk 8.6 build): a synthesized
        # <<ListboxSelect>> via event_generate never reaches Listbox
        # bindings when exportselection=False, so the selection path is
        # driven through the real bound handler after setting the
        # selection on the widget itself — the same workaround spirit as
        # test_gui.py's generated-<Return> notes. Real clicks still take
        # this path (the widget's own class binding).
        self.assertTrue(gui.watcher_list.bind("<<ListboxSelect>>"),
                        "listbox selection is not wired")
        gui.watcher_list.selection_clear(0, "end")
        gui.watcher_list.selection_set(1)
        gui._on_watcher_select(None)
        det = gui.watcher_detail.get("1.0", "end")
        self.assertIn(STATE_WORDS["resolved"], det)
        self.assertIn("research, shadow-only", det)

    # ---- 4. contained failure never blanks the scanner ----------------------
    def test_unavailable_renders_contained_fallback(self):
        gui = self.make_gui()
        before = gui.tree.get_children()
        gui._on_watcher({"kind": "watcher", "ok": True, "panel": None,
                         "error": "watcher db missing: /nonexistent/x.db"})
        det = gui.watcher_detail.get("1.0", "end")
        self.assertIn("watcher data unavailable", det)
        self.assertIn("scanner intact", det)
        self.assertEqual(gui.tree.get_children(), before)

    # ---- 5. pause/disable queue worker jobs (writers stay worker-side) -------
    def test_pause_and_disable_queue_worker_jobs(self):
        db = os.path.join(self.tmp, "watch.db")
        seed_watcher_db(db)
        gui = self.make_gui(db=db)
        self.assertTrue(
            self.pump_until(lambda: gui.watcher_panel is not None),
            "watcher result never reached the UI")
        self._drain(gui._jobs)
        self.buttons("Pause watcher")[0].invoke()
        jobs = self._drain(gui._jobs)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].get("cmd"), "watcher_pause")
        self.assertTrue(jobs[0].get("paused"))
        self.buttons("Disable rule")[0].invoke()
        jobs = self._drain(gui._jobs)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].get("cmd"), "watcher_disable")
        self.assertEqual(jobs[0].get("rule"), "t1-break-v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
