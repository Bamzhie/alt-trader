"""Task 2: guarded --quant-shadow daemon hook (sweep-neutral).

Self-checking script (stdlib only, offline). Pins:
- build_parser exposes --quant-shadow/--quant-db/--quant-bars/--quant-equity
  with defaults off / data/quant.db / data/bars / 1000.0.
- Without --quant-shadow no watcher is constructed.
- A watcher whose step() raises every iteration never breaks the loop:
  sweeps still complete, on_sweep still fires with correct counts, and the
  failure is logged (never raised).
- A counting watcher steps exactly once per iteration.

Run:  python3 tests/test_quant_shadow_hook.py   -> ALL PASS, exit 0
"""

import io
import os
import sys
import tempfile
from contextlib import contextmanager, redirect_stdout
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

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


HUGE = 10 ** 12


def daemon_args(db, tmp, **kw):
    d = dict(stake=0.10, coins=150, interval=5, db=db, log_threshold=24.0,
             use_v3=False, collect_every=HUGE, resolve_every=HUGE,
             measure_every=HUGE, iterations=2, no_scan=False,
             quant_shadow=False,
             quant_db=os.path.join(tmp, "quant.db"),
             quant_bars=os.path.join(tmp, "bars"),
             quant_equity=1000.0)
    d.update(kw)
    return SimpleNamespace(**d)


class FakeApp:
    """Offline stand-in: scans succeed, 2 eligible / 0 evaluated."""

    def __init__(self, args):
        from proto.store import Store
        self.store = Store(args.db)
        self.cards = []
        self.failed = 0
        self.uni = ["AAA", "BBB"]
        self.tk = {}
        self.det = {}
        self.universe_error = None

    def maybe_refresh_universe(self):
        return None

    def scan_once(self):
        self.cards = []


@contextmanager
def patched_app(dmod):
    real = dmod.App
    dmod.App = FakeApp
    try:
        yield
    finally:
        dmod.App = real


@contextmanager
def patched_watcher(cls):
    from proto.quant import engine as qeng
    real = qeng.Watcher
    qeng.Watcher = cls
    try:
        yield
    finally:
        qeng.Watcher = real


def main():
    from proto import daemon as dmod

    # -- flags exist with the right defaults ------------------------------
    ap = dmod.build_parser()
    d = ap.parse_args([])
    check("quant-shadow defaults off",
          getattr(d, "quant_shadow", "MISSING") is False,
          f"got {getattr(d, 'quant_shadow', 'MISSING')}")
    check("quant-db defaults to data/quant.db",
          getattr(d, "quant_db", None) == "data/quant.db",
          f"got {getattr(d, 'quant_db', 'MISSING')}")
    check("quant-bars defaults to data/bars",
          getattr(d, "quant_bars", None) == "data/bars",
          f"got {getattr(d, 'quant_bars', 'MISSING')}")
    check("quant-equity defaults to 1000.0",
          getattr(d, "quant_equity", None) == 1000.0,
          f"got {getattr(d, 'quant_equity', 'MISSING')}")

    tmp = tempfile.mkdtemp(prefix="quant-shadow-hook-")
    try:
        # -- service forwarding: quant flags reach the embedded loop -------
        # (regression: main() used to forward only the scan/collect
        # cadences, so the embedded loop never enabled the watcher).
        from proto import service as svcmod
        qdb = os.path.join(tmp, "fwd-quant.db")
        qbars = os.path.join(tmp, "fwd-bars")
        sargs = svcmod.build_service_parser().parse_args(
            ["--quant-shadow", "--quant-db", qdb, "--quant-bars", qbars,
             "--quant-equity", "2500"])
        fwd = svcmod._daemon_argv_for(sargs)
        loop_args = dmod.build_parser().parse_args(fwd)
        check("forwarding: embedded loop enables the watcher",
              getattr(loop_args, "quant_shadow", False) is True,
              f"argv={fwd}")
        check("forwarding: embedded loop uses the service quant-db",
              getattr(loop_args, "quant_db", None) == qdb,
              f"argv={fwd}")
        check("forwarding: embedded loop uses the service quant-bars",
              getattr(loop_args, "quant_bars", None) == qbars,
              f"argv={fwd}")
        check("forwarding: embedded loop uses the service quant-equity",
              getattr(loop_args, "quant_equity", None) == 2500.0,
              f"argv={fwd}")
        # Flag off: no --quant-shadow in the forwarded argv (watcher
        # stays disabled in the embedded loop, as before).
        sargs_off = svcmod.build_service_parser().parse_args([])
        check("forwarding: flag off stays off in the embedded loop",
              "--quant-shadow" not in svcmod._daemon_argv_for(sargs_off))

        # -- flag off: no watcher constructed ------------------------------
        class BoomOnInit:
            def __init__(self, *a, **k):
                raise AssertionError("watcher must not init without flag")

        db0 = os.path.join(tmp, "off.db")
        with patched_app(dmod), patched_watcher(BoomOnInit):
            fired = []
            try:
                dmod.run_daemon_loop(
                    daemon_args(db0, tmp, iterations=1),
                    on_sweep=lambda e, e2: fired.append((e, e2)))
                check("flag off: loop completes with no watcher",
                      fired == [(2, 0)], f"got {fired}")
            except AssertionError as e:
                check("flag off: loop completes with no watcher",
                      False, str(e))

        # -- raising watcher: sweeps survive, failure logged ---------------
        class RaisingWatcher:
            instances = []

            def __init__(self, *a, **k):
                RaisingWatcher.instances.append(self)

            def step(self):
                raise RuntimeError("quant down")

        db1 = os.path.join(tmp, "raise.db")
        RaisingWatcher.instances = []
        with patched_app(dmod), patched_watcher(RaisingWatcher):
            fired = []
            buf = io.StringIO()
            try:
                with redirect_stdout(buf):
                    dmod.run_daemon_loop(
                        daemon_args(db1, tmp, iterations=2,
                                    quant_shadow=True),
                        on_sweep=lambda e, e2, **k: fired.append((e, e2)))
                raised = False
            except Exception as e:  # noqa: BLE001 -- must never happen
                raised = True
                check("raising watcher: exception never propagates",
                      False, f"{type(e).__name__}: {e}")
            if not raised:
                check("raising watcher: exception never propagates", True)
            check("raising watcher: sweeps still complete",
                  fired == [(2, 0), (2, 0)], f"got {fired}")
            check("raising watcher: failure logged",
                  "quant FAILED" in buf.getvalue(),
                  buf.getvalue()[-300:])

        # -- counting watcher: one step per iteration -----------------------
        class CountingWatcher:
            calls = 0

            def __init__(self, *a, **k):
                pass

            def step(self):
                CountingWatcher.calls += 1
                return {"emitted": 0, "resolved": 0, "paused": 0}

        db2 = os.path.join(tmp, "count.db")
        CountingWatcher.calls = 0
        with patched_app(dmod), patched_watcher(CountingWatcher):
            fired = []
            dmod.run_daemon_loop(
                daemon_args(db2, tmp, iterations=2, quant_shadow=True),
                on_sweep=lambda e, e2, **k: fired.append((e, e2)))
            check("counting watcher: one step per iteration",
                  CountingWatcher.calls == 2,
                  f"got {CountingWatcher.calls}")
            check("counting watcher: sweeps unaffected",
                  fired == [(2, 0), (2, 0)], f"got {fired}")
    finally:
        pass

    print()
    if FAILURES:
        print(f"FAIL ({CHECKS} checks, {len(FAILURES)} failed): {FAILURES}")
        sys.exit(1)
    print(f"ALL PASS ({CHECKS} checks)")


if __name__ == "__main__":
    main()
