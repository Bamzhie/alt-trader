"""ALT RADAR desktop GUI (tkinter, stdlib-only, read-only).

Layout: header · toolbar · signals table · vetoed section · detail pane ·
outcomes panel · status bar (task G1 brief).

Threading contract (brief):
  * The MAIN thread owns every Tk widget; nothing here is ever touched from
    a worker thread.
  * ONE daemon worker thread runs all long / side-effecting work: network
    scans, plan fetches, the collector, the outcome resolver, and ALL SQLite
    (a sqlite3 connection is thread-bound, so the Store — and the proto.app
    App that owns it — live on the worker).
  * Worker → UI results travel through a queue.Queue drained by an after()
    poll; UI → worker commands travel through a second queue. The worker
    never imports or touches tkinter.

Read-only: no order code path exists in this app, and no keys are held.
"""

import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from types import SimpleNamespace

from proto import collector
from proto import outcomes as outcomes_mod
from proto import report as report_mod
from proto import scan as scanmod
from proto.app import App as ProtoApp

from . import model

# Sort keys offered by the toolbar combo — the same keys model/SORT_KEYS
# (proto.app.SORT_KEYS) accept; tuple() of the shared dict, never a fork.
SORT_CHOICES = tuple(model.SORT_KEYS)
DIR_CHOICES = ("Both", "Long", "Short")

POLL_MS = 120      # worker-result drain period
TICK_MS = 500      # auto-scan scheduler period

# Row colour tags (brief: long green-tint, short red-tint, vetoed grey,
# WATCH orange flag text). ttk.Treeview tags are ROW-level — Tk has no
# per-cell tags — so an orange WATCH colours the row's text.
TAG_LONG, TAG_SHORT, TAG_VETOED, TAG_WATCH = "long", "short", "vetoed", "watch"
COLOR_LONG = "#e8f5e9"
COLOR_SHORT = "#ffebee"
COLOR_VETOED = "#f0f0f0"
COLOR_WATCH = "#c2410c"
COLOR_ERROR = "#b91c1c"

SIGNAL_COLUMNS = ("rank", "dir", "coin", "price", "ch24", "vol24", "funding",
                  "oi", "lean", "early", "score", "flags")
SIGNAL_HEADINGS = {"rank": "#", "dir": "DIR", "coin": "COIN", "price": "PRICE",
                   "ch24": "24h%", "vol24": "VOL24", "funding": "FUND%",
                   "oi": "OIΔ%", "lean": "LEAN", "early": "EARLY",
                   "score": "SCORE", "flags": "FLAGS"}
SIGNAL_WIDTHS = {"rank": 44, "dir": 40, "coin": 92, "price": 104, "ch24": 74,
                 "vol24": 84, "funding": 84, "oi": 150, "lean": 62,
                 "early": 64, "score": 64, "flags": 210}
SIGNAL_ANCHORS = {"rank": "center", "dir": "center", "coin": "w",
                  "flags": "w"}

ACTIVITY = {"scan": "scanning", "collect": "collecting",
            "resolve": "resolving", "stats": "refreshing stats"}


class Worker(threading.Thread):
    """The single daemon thread: owns Store + proto App, runs jobs serially."""

    def __init__(self, args, jobs, results):
        super().__init__(name="radar-gui-worker", daemon=True)
        self.args = args
        self.jobs = jobs
        self.results = results
        self.app = None

    def run(self):
        try:
            # Created HERE: sqlite3 connections are thread-bound, so the
            # Store (and App, which builds it) must live on this thread.
            self.app = ProtoApp(self.args)
        except Exception as e:
            self.results.put({"kind": "ready", "ok": False,
                              "error": f"{type(e).__name__}: {e}"})
            return
        self.results.put({"kind": "ready", "ok": True})
        while True:
            job = self.jobs.get()
            if job is None:
                break
            try:
                msg = self._handle(job)
            except Exception as e:
                msg = {"kind": job.get("cmd", "?"), "ok": False,
                       "coin": job.get("coin"),
                       "error": f"{type(e).__name__}: {e}"}
            # Venue health is snapshotted WORKER-side (scan module state is
            # written by this thread only), then shipped through the queue.
            msg["venue"] = scanmod.venue_health()
            self.results.put(msg)
        try:
            self.app.store.close()
        except Exception:
            pass

    # ---- jobs (all long / side-effecting work happens here) ----
    def _handle(self, job):
        cmd = job.get("cmd")
        app = self.app
        if cmd == "scan":
            app.args.stake = job["stake"]
            app.args.coins = job["coins"]
            app.args.universe_budget = job["coins"]
            app.args.log_threshold = job["log_threshold"]
            t0 = time.time()
            app.scan_once()                 # proto scan + score + log path
            return {"kind": "scan", "ok": True, "cards": app.cards,
                    "universe": len(app.uni), "failed": app.failed,
                    "errors": dict(app.last_errors), "status": app.status,
                    "log_errors": app.log_errors,
                    "universe_error": app.universe_error,
                    "stats": app.store.stats(), "elapsed": time.time() - t0}
        if cmd == "stats":
            return {"kind": "stats", "ok": True, "stats": app.store.stats(),
                    "outcome": model.outcome_summary(app.store),
                    "hit24": report_mod.signal_stats(app.store, 24)}
        if cmd == "resolve":
            if not app.uni:                 # need the venue symbol map first
                app.refresh_universe()
            sym_map = dict((cn, sy) for sy, cn in app.uni)
            n = outcomes_mod.resolve_pending(app.store, sym_map)
            return {"kind": "resolve", "ok": True, "resolved": n,
                    "stats": app.store.stats(),
                    "outcome": model.outcome_summary(app.store),
                    "hit24": report_mod.signal_stats(app.store, 24)}
        if cmd == "collect":
            counts = collector.collect_full_universe()
            return {"kind": "collect", "ok": True, "coins": len(counts),
                    "bars": sum(counts.values())}
        # NOTE: "plan" jobs run on PlanWorker (own lane), never here — a plan
        # queued behind a ~50s scan starved every click ("fetching…" forever).
        return {"kind": cmd, "ok": False, "error": f"unknown job: {cmd!r}"}


class PlanWorker(threading.Thread):
    """Dedicated lane for trade-plan fetches.

    The main Worker runs scans serially (~50s+ each), so plan jobs queued
    behind a scan starved: every click waited behind the running scan, and
    each finished scan wiped the cache, re-queuing the plan behind the NEXT
    scan — a perpetual "fetching…". Plans are stateless HTTP + pure compute
    (no Store access), so they safely run on their own thread and resolve
    in ~1-2s even mid-scan. Reads Worker.app (cards/uni replaced atomically
    by assignment; worst case a slightly stale symbol map).
    """

    def __init__(self, worker, jobs, results):
        super().__init__(name="radar-gui-plans", daemon=True)
        self.worker = worker
        self.jobs = jobs
        self.results = results

    def run(self):
        while True:
            job = self.jobs.get()
            if job is None:
                break
            coin = job.get("coin")
            try:
                app = self.worker.app
                if app is None:
                    raise RuntimeError("scanner still starting…")
                if job.get("stake") is not None:
                    app.args.stake = job["stake"]
                card = next((c for c in app.cards if c.coin == coin), None)
                if card is None:
                    msg = {"kind": "plan", "ok": True, "coin": coin,
                           "plan": None,
                           "err": "coin is no longer in the last scan"}
                else:
                    plan, info = app.plan_for(card)
                    msg = {"kind": "plan", "ok": True, "coin": coin,
                           "plan": plan,
                           "err": None if plan is not None else str(info)}
            except Exception as e:
                msg = {"kind": "plan", "ok": False, "coin": coin,
                       "error": f"{type(e).__name__}: {e}"}
            try:
                msg["venue"] = scanmod.venue_health()
            except Exception:
                pass
            self.results.put(msg)


class RadarGUI(tk.Tk):
    """The ALT RADAR desktop front end. Read-only — no orders, no keys."""

    def __init__(self, stake=0.10, coins=150, interval=60,
                 db="data/signals.db", log_threshold=24.0, auto_scan=True):
        super().__init__()
        self.title("ALT RADAR — READ-ONLY MEXC scanner")
        self.geometry("1360x860")
        self.minsize(1100, 640)
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
        self.selected_coin = None
        self._last_errors = {}          # {coin: reason} from the last scan
        self._plan_cache = {}           # coin -> {"plan": Plan|None, "err": str|None}
        self._plans_pending = set()
        self._busy = set()              # job kinds in flight
        self._closing = False

        self._jobs = queue.Queue()      # UI -> worker
        self._results = queue.Queue()   # worker -> UI
        self._plan_jobs = queue.Queue()  # UI -> plan lane (never scan-blocked)

        self._build_header()
        self._build_toolbar()
        self._build_body()
        self._build_status()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-q>", lambda e: self._on_close())

        worker_args = SimpleNamespace(
            stake=self.stake, coins=self.coins, interval=self.interval,
            universe_budget=self.coins, log_threshold=self.log_threshold,
            db=self.db, write_logs=True)
        self._worker = Worker(worker_args, self._jobs, self._results)
        self._worker.start()
        self._plan_worker = PlanWorker(self._worker, self._plan_jobs,
                                       self._results)
        self._plan_worker.start()

        self.after(POLL_MS, self._poll)
        self.after(TICK_MS, self._tick)
        self._submit("stats")

    # ------------------------------------------------------------- widgets
    def _build_header(self):
        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        ro_font = tkfont.nametofont("TkDefaultFont").copy()
        ro_font.configure(weight="bold",
                          size=int(tkfont.nametofont("TkDefaultFont").cget("size")) + 1)

        bar = ttk.Frame(self, padding=(8, 5, 8, 3))
        bar.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(bar, text="ALT RADAR · MEXC perp scanner",
                  font=bold).pack(side=tk.LEFT)
        # Permanent READ-ONLY marker (brief: no orders, no keys).
        ttk.Label(bar, text=" READ-ONLY ", style="ReadOnly.TLabel",
                  font=ro_font).pack(side=tk.LEFT, padx=(10, 4))
        ttk.Label(bar, text="TIER-2 UNVALIDATED",
                  style="Tier2.TLabel").pack(side=tk.LEFT, padx=(0, 10))
        self.var_header = tk.StringVar(value="universe – · shown 0 · vetoed 0")
        ttk.Label(bar, textvariable=self.var_header).pack(side=tk.LEFT, fill=tk.X, expand=True)

        style = ttk.Style(self)
        style.configure("ReadOnly.TLabel", foreground=COLOR_ERROR,
                        background="#fde8e8")
        style.configure("Tier2.TLabel", foreground="#92400e")

    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=(8, 2, 8, 5))
        bar.pack(side=tk.TOP, fill=tk.X)

        def sep():
            ttk.Separator(bar, orient=tk.VERTICAL).pack(
                side=tk.LEFT, fill=tk.Y, padx=8, pady=1)

        ttk.Label(bar, text="Stake $").pack(side=tk.LEFT)
        self.var_stake = tk.StringVar(value=f"{self.stake:g}")
        ent_stake = ttk.Entry(bar, textvariable=self.var_stake, width=8)
        ent_stake.pack(side=tk.LEFT)
        ent_stake.bind("<Return>", lambda e: self._apply_stake())
        ttk.Button(bar, text="Apply", command=self._apply_stake,
                   width=6).pack(side=tk.LEFT, padx=(3, 0))
        sep()

        ttk.Label(bar, text="Flag threshold").pack(side=tk.LEFT)
        self.var_threshold = tk.StringVar(value=f"{self.log_threshold:g}")
        ent_thr = ttk.Entry(bar, textvariable=self.var_threshold, width=6)
        ent_thr.pack(side=tk.LEFT)
        ent_thr.bind("<Return>", lambda e: self._apply_threshold())
        ttk.Button(bar, text="Apply", command=self._apply_threshold,
                   width=6).pack(side=tk.LEFT, padx=(3, 0))
        sep()

        ttk.Label(bar, text="Coins").pack(side=tk.LEFT)
        self.var_coins = tk.StringVar(value=str(self.coins))
        sp_coins = ttk.Spinbox(bar, from_=model.MIN_COINS, to=model.MAX_COINS,
                               textvariable=self.var_coins, width=6,
                               command=lambda *a: self._apply_coins())
        sp_coins.pack(side=tk.LEFT)
        self._bind_commit(sp_coins, self._apply_coins)
        sep()

        ttk.Label(bar, text="Interval s").pack(side=tk.LEFT)
        self.var_interval = tk.StringVar(value=str(self.interval))
        sp_int = ttk.Spinbox(bar, from_=model.MIN_INTERVAL, to=3600,
                             textvariable=self.var_interval, width=6,
                             command=lambda *a: self._apply_interval())
        sp_int.pack(side=tk.LEFT)
        self._bind_commit(sp_int, self._apply_interval)
        sep()

        ttk.Label(bar, text="Dir").pack(side=tk.LEFT)
        self.var_dir = tk.StringVar(value="Both")
        cb_dir = ttk.Combobox(bar, textvariable=self.var_dir,
                              values=DIR_CHOICES, state="readonly", width=6)
        cb_dir.pack(side=tk.LEFT)
        cb_dir.bind("<<ComboboxSelected>>", self._on_dir_change)
        sep()

        ttk.Label(bar, text="Sort").pack(side=tk.LEFT)
        self.var_sort = tk.StringVar(value="score")
        cb_sort = ttk.Combobox(bar, textvariable=self.var_sort,
                               values=SORT_CHOICES, state="readonly", width=7)
        cb_sort.pack(side=tk.LEFT)
        cb_sort.bind("<<ComboboxSelected>>", self._on_sort_change)
        sep()

        self.btn_scan = ttk.Button(bar, text="Scan now",
                                   command=lambda: self._submit("scan"))
        self.btn_scan.pack(side=tk.LEFT, padx=2)
        self.btn_auto = ttk.Button(bar, text=self._auto_label(),
                                   command=self._toggle_auto)
        self.btn_auto.pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Collect bars",
                   command=lambda: self._submit("collect")).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Resolve outcomes",
                   command=lambda: self._submit("resolve")).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(bar, text="Refresh stats",
                   command=lambda: self._submit("stats")).pack(
            side=tk.LEFT, padx=2)

    def _build_body(self):
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # ---- left: signals table + vetoed section ----
        left = ttk.Frame(paned)
        paned.add(left, weight=3)

        sig_frame = ttk.LabelFrame(left, text="Signals", padding=4)
        sig_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(sig_frame, columns=SIGNAL_COLUMNS,
                                 show="headings", selectmode="browse")
        for col in SIGNAL_COLUMNS:
            self.tree.heading(col, text=SIGNAL_HEADINGS[col])
            self.tree.column(col, width=SIGNAL_WIDTHS[col], stretch=(col == "flags"),
                             anchor=SIGNAL_ANCHORS.get(col, "e"))
        vsig = ttk.Scrollbar(sig_frame, orient=tk.VERTICAL,
                             command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsig.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsig.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure(TAG_LONG, background=COLOR_LONG)
        self.tree.tag_configure(TAG_SHORT, background=COLOR_SHORT)
        self.tree.tag_configure(TAG_WATCH, foreground=COLOR_WATCH)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self._bind_scroll(self.tree)

        vet_frame = ttk.LabelFrame(left, text="VETOED — excluded from ranking, "
                                              "shadow-logged", padding=4)
        vet_frame.pack(side=tk.TOP, fill=tk.X, pady=(4, 0))
        self.veto_cols = ("dir", "coin", "score", "vetoes")
        self.tree_vetoed = ttk.Treeview(vet_frame, columns=self.veto_cols,
                                        show="headings", height=5,
                                        selectmode="browse")
        for col, head, width, anchor in (
                ("dir", "DIR", 44, "center"), ("coin", "COIN", 140, "w"),
                ("score", "SCORE", 70, "e"), ("vetoes", "VETO CODES", 320, "w")):
            self.tree_vetoed.heading(col, text=head)
            self.tree_vetoed.column(col, width=width, anchor=anchor,
                                    stretch=(col == "vetoes"))
        vscr = ttk.Scrollbar(vet_frame, orient=tk.VERTICAL,
                             command=self.tree_vetoed.yview)
        self.tree_vetoed.configure(yscrollcommand=vscr.set)
        self.tree_vetoed.pack(side=tk.LEFT, fill=tk.X, expand=True)
        vscr.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_vetoed.tag_configure(TAG_VETOED, background=COLOR_VETOED,
                                       foreground="#8d8d8d")
        self.tree_vetoed.bind("<<TreeviewSelect>>", self._on_select)
        self._bind_scroll(self.tree_vetoed)

        # ---- right: detail pane + outcomes panel ----
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        det_frame = ttk.LabelFrame(right, text="Detail — selected coin "
                                               "(review only, no orders)",
                                   padding=4)
        det_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.detail = tk.Text(det_frame, wrap=tk.WORD, state=tk.DISABLED,
                              font=tkfont.nametofont("TkFixedFont"),
                              background="#fbfbfb")
        dscr = ttk.Scrollbar(det_frame, orient=tk.VERTICAL,
                             command=self.detail.yview)
        self.detail.configure(yscrollcommand=dscr.set)
        self.detail.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        dscr.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail.tag_configure("warn", foreground=COLOR_WATCH)
        self.detail.tag_configure("veto", foreground="#6b7280")
        self._bind_scroll(self.detail)
        self._set_detail_text("select a row for the full breakdown")

        out_frame = ttk.LabelFrame(right, text="Outcomes", padding=6)
        out_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.var_outcomes = tk.StringVar(value="no outcomes resolved yet")
        ttk.Label(out_frame, textvariable=self.var_outcomes,
                  justify=tk.LEFT).pack(side=tk.TOP, anchor=tk.W)
        ttk.Button(out_frame, text="Resolve now",
                   command=lambda: self._submit("resolve")).pack(
            side=tk.TOP, anchor=tk.W, pady=(5, 0))

    def _build_status(self):
        bar = ttk.Frame(self, padding=(8, 3))
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.var_activity = tk.StringVar(value="activity: idle")
        ttk.Label(bar, textvariable=self.var_activity).pack(side=tk.LEFT)
        self.var_failed = tk.StringVar(value="failed 0/0 of last scan")
        ttk.Label(bar, textvariable=self.var_failed).pack(
            side=tk.LEFT, padx=(14, 0))
        self.var_statusline = tk.StringVar(value="")
        self.lbl_statusline = ttk.Label(bar, textvariable=self.var_statusline,
                                        foreground="#555555")
        self.lbl_statusline.pack(side=tk.LEFT, fill=tk.X, expand=True,
                                 padx=(14, 0))

    # ------------------------------------------------------ widget helpers
    def _bind_commit(self, widget, commit):
        widget.bind("<Return>", lambda e: commit())
        widget.bind("<FocusOut>", lambda e: commit())

    def _bind_scroll(self, widget):
        # X11 wheel: Button-4/5 (MouseWheel fires only on some builds).
        def _wheel(e):
            widget.yview_scroll(-1 if e.num == 4 else 1, "units")
            return "break"
        widget.bind("<Button-4>", _wheel)
        widget.bind("<Button-5>", _wheel)

    def _auto_label(self):
        return "Pause auto-scan" if self.auto_scan else "Resume auto-scan"

    def _set_error(self, msg):
        self.error_text = msg or ""
        self._render_status()

    # ------------------------------------------------------ input handlers
    def _apply_stake(self):
        try:
            v = model.validate_stake(self.var_stake.get())
        except ValueError as e:
            self._set_error(str(e))
            self.var_stake.set(f"{self.stake:g}")
            return
        self.stake = v
        self.var_stake.set(f"{self.stake:g}")
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
            v = model.validate_threshold(self.var_threshold.get())
        except ValueError as e:
            self._set_error(str(e))
            self.var_threshold.set(f"{self.log_threshold:g}")
            return
        self.log_threshold = v
        self.var_threshold.set(f"{self.log_threshold:g}")
        self._set_error("")
        self.scan_status = f"flag threshold {self.log_threshold:g} applied"
        self._render_status()

    def _apply_coins(self):
        try:
            v = model.validate_coins(self.var_coins.get())
        except ValueError as e:
            self._set_error(str(e))
            self.var_coins.set(str(self.coins))
            return
        self.coins = v
        self.var_coins.set(str(self.coins))
        self._set_error("")
        self.scan_status = f"coins {self.coins} applied (universe budget + scan size)"
        self._render_status()

    def _apply_interval(self):
        try:
            v = model.validate_interval(self.var_interval.get())
        except ValueError as e:
            self._set_error(str(e))
            self.var_interval.set(str(self.interval))
            return
        self.interval = v
        self.var_interval.set(str(self.interval))
        self._set_error("")
        self.scan_status = f"auto-scan interval {self.interval}s applied"
        self._render_status()

    def _on_dir_change(self, _event=None):
        choice = str(self.var_dir.get())
        key = choice.strip().lower()
        if key not in model.DIR_FILTERS:
            self.var_dir.set("Both")
            self._set_error(f"unknown direction filter: {choice!r}")
            return
        self.dir_filter = key
        self._render_table()
        self._render_header()

    def _on_sort_change(self, _event=None):
        key = str(self.var_sort.get())
        if key not in SORT_CHOICES:
            self.var_sort.set("score")
            self._set_error(f"unknown sort key: {key!r}")
            return
        self.sort_key = key
        self._render_table()
        self._render_header()

    def _toggle_auto(self):
        self.auto_scan = not self.auto_scan
        self.btn_auto.configure(text=self._auto_label())
        self.scan_status = ("auto-scan paused" if not self.auto_scan
                            else f"auto-scan resumed ({self.interval}s)")
        self._render_status()

    def _on_select(self, event):
        sel = event.widget.selection()
        if not sel:
            return
        coin = sel[0]
        other = self.tree_vetoed if event.widget is self.tree else self.tree
        if other.selection():
            other.selection_remove(other.selection())
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

    def _on_close(self):
        self._closing = True
        for q in (self._jobs, self._plan_jobs):
            try:
                q.put_nowait(None)
            except Exception:
                pass
        self.destroy()

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
        except tk.TclError:
            return
        if not self._closing:
            self.after(POLL_MS, self._poll)

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
        # Keep cached plans for coins still present (kills the perpetual
        # "fetching…" where every scan wiped the plan behind the next scan);
        # drop coins that left the table. Silently refresh the selected coin.
        coins = {c.coin for c in cards}
        for coin in list(self._plan_cache):
            if coin not in coins:
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

    def _on_stats(self, msg):
        self.stats = msg.get("stats") or self.stats
        self.outcome = msg.get("outcome") or self.outcome
        if msg.get("hit24"):
            self.hit24 = msg["hit24"]
        self._render_header()
        self._render_outcomes()

    def _on_resolve(self, msg):
        n = int(msg.get("resolved", 0))
        self.stats = msg.get("stats") or self.stats
        self.outcome = msg.get("outcome") or self.outcome
        if msg.get("hit24"):
            self.hit24 = msg["hit24"]
        self.scan_status = f"resolved {n} pending outcome(s)"
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
        try:
            if (self.auto_scan and "scan" not in self._busy
                    and time.time() - self.last_scan_ts >= self.interval):
                self._submit("scan")
            if not self._closing:
                self.after(TICK_MS, self._tick)
        except tk.TclError:
            pass

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
        self.var_header.set(
            f"universe {self.universe}"
            f" · shown {len(self._ranked)} · vetoed {len(self._vetoed)}"
            f" · stake ${self.stake:.2f}"
            f" · {self._venue_text()}"
            f" · last scan {self.last_scan_label}"
            f" · logs {st.get('rows', 0)} rows / {st.get('coins', 0)} coins"
            f" · flagged {st.get('flagged', 0)}"
            f" · outcomes {st.get('outcomes', 0)}")

    def _render_table(self):
        tree = self.tree
        for iid in tree.get_children():
            tree.delete(iid)
        cards = model.filter_cards(self.cards, self.dir_filter)
        ranked, vetoed = model.split_vetoed(cards)
        ranked = model.sort_cards(ranked, self.sort_key)
        vetoed = model.sort_cards(vetoed, self.sort_key)
        self._ranked, self._vetoed = ranked, vetoed

        for i, c in enumerate(ranked, 1):
            flags = model.format_flags(c, self.stake)
            oi = c.oi_change_pct
            values = (
                i,
                model.direction_arrow(c.direction),
                c.coin,
                model.format_price(c.price),
                f"{c.change_24h_pct:+.1f}%",
                model.format_vol(c.quote_vol_24h),
                f"{c.funding_rate * 100:+.4f}%",
                model.format_oi(oi, getattr(c, "oi_notional", None)),                f"{c.lean:+.2f}",
                f"{c.earlyness:.2f}",
                f"{c.score:.1f}",
                flags,
            )
            tags = []
            if c.direction == "LONG":
                tags.append(TAG_LONG)
            elif c.direction == "SHORT":
                tags.append(TAG_SHORT)
            if "WATCH" in flags.split():
                tags.append(TAG_WATCH)
            tree.insert("", tk.END, iid=c.coin, values=values, tags=tuple(tags))

        if self.selected_coin and tree.exists(self.selected_coin):
            tree.selection_set(self.selected_coin)

    def _render_vetoed(self):
        tree = self.tree_vetoed
        for iid in tree.get_children():
            tree.delete(iid)
        for c in self._vetoed:
            codes = ",".join(v.code for v in c.vetoes)
            tree.insert("", tk.END, iid=c.coin,
                        values=(model.direction_arrow(c.direction), c.coin,
                                f"{c.score:.1f}", codes),
                        tags=(TAG_VETOED,))
        if self.selected_coin and tree.exists(self.selected_coin):
            tree.selection_set(self.selected_coin)

    def _set_detail_text(self, text):
        self.detail.configure(state=tk.NORMAL)
        self.detail.delete("1.0", tk.END)
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("⚠"):
                tag = "warn"
            elif stripped.startswith("⨯"):
                tag = "veto"
            else:
                tag = ""
            self.detail.insert(tk.END, line + "\n", tag)
        self.detail.configure(state=tk.DISABLED)
        self.detail.yview_moveto(0.0)

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
        self.var_outcomes.set("\n".join(lines))

    def _render_status(self):
        self.var_activity.set(f"activity: {self.activity}")
        self.var_failed.set(f"failed {self.failed}/{self.attempted} of last scan")
        # BUG FIX (G2): lbl_statusline is bound to var_statusline, and a Label
        # ignores configure(text=...) while a textvariable is active — the
        # message must be written through the variable to be visible at all.
        if self.error_text:
            self.lbl_statusline.configure(foreground=COLOR_ERROR)
            self.var_statusline.set(self.error_text)
        else:
            self.lbl_statusline.configure(foreground="#555555")
            self.var_statusline.set(self.scan_status)

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
