"""Task 5: daemon embedding + single scanner lifecycle.

Self-checking script (stdlib only). Talks to 127.0.0.1 ONLY: an egress
tripwire wraps socket connects and fails any connect outside loopback.
Lockfile/singleton paths always live under a temp dir — never the real
runtime dir.

Pins:
- the scan loop calls record_sweep_completed once per completed sweep
  (plus a dashboard_snapshot row); a failed sweep records nothing.
- a second instance refuses via the fcntl lockfile (exit code + message,
  no duplicate scanning).
- SIGTERM stops loop, server, sessions in order; the port is freed and
  the DB stays consistent.
- proto.daemon is a thin wrapper (no copied loop body) with all flags
  preserved; the systemd unit ExecStarts proto.service.

Run:  python3 tests/test_service_lifecycle.py   -> ALL PASS, exit 0
"""

import http.client
import io
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# ---- egress tripwire: loopback-only (allows 127.0.0.1 / localhost / ::1) --
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class NetworkTripwire(AssertionError):
    """A test tried to connect outside loopback — instant failure."""


_RealSocket = socket.socket


class _GuardedSocket(_RealSocket):
    def connect(self, address, *args, **kw):
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOOPBACK_HOSTS:
            raise NetworkTripwire(
                f"NETWORK TRIPWIRE: connect to non-loopback {host!r} blocked")
        return super().connect(address, *args, **kw)


_RealCreateConnection = socket.create_connection


def _guarded_create_connection(address, *args, **kw):
    host = address[0] if isinstance(address, tuple) else address
    if host not in LOOPBACK_HOSTS:
        raise NetworkTripwire(
            f"NETWORK TRIPWIRE: create_connection to non-loopback {host!r}")
    return _RealCreateConnection(address, *args, **kw)


socket.socket = _GuardedSocket
socket.create_connection = _guarded_create_connection

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


HUGE = 10 ** 12  # cadence that never fires inside a test


def daemon_args(db, **kw):
    d = dict(stake=0.10, coins=150, interval=5, db=db, log_threshold=24.0,
             use_v3=False, collect_every=HUGE, resolve_every=HUGE,
             measure_every=HUGE, iterations=2, no_scan=False)
    d.update(kw)
    return SimpleNamespace(**d)


class FakeApp:
    """Offline stand-in for proto.app.App: no network, temp-DB Store."""

    fail = False

    def __init__(self, args):
        from proto.store import Store
        self.args = args
        self.cards = []
        self.failed = 0
        self.uni = []
        self.tk = {}
        self.det = {}
        self.universe_error = None
        self.store = Store(args.db)

    def maybe_refresh_universe(self):
        return None

    def scan_once(self):
        if FakeApp.fail:
            raise RuntimeError("boom")
        self.cards = []


def sweep_event_count(svc):
    with svc._EVENT_LOCK:
        return sum(1 for e in svc._EVENTS if e["kind"] == "sweep.completed")


def snapshot_count(db):
    if not os.path.exists(db):
        return 0
    conn = sqlite3.connect(db, timeout=5)
    try:
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM dashboard_snapshot").fetchone()[0]
        except sqlite3.Error:
            return 0
    finally:
        conn.close()


def service_argv(lock, db, token="lifecycle-test-token", **kw):
    argv = ["--lock-file", lock, "--port", "0", "--token", token,
            "--db", db, "--no-scan", "--iterations", "1",
            "--collect-every", str(HUGE), "--resolve-every", str(HUGE),
            "--measure-every", str(HUGE), "--interval", "60"]
    for key, value in kw.items():
        argv += [f"--{key}", str(value)]
    return argv


def main():
    from proto import service as svc
    from proto import daemon as dmod

    tmp = tempfile.mkdtemp(prefix="svc-lifecycle-")
    try:
        # -- sweep writer: one emitter + one snapshot per completed sweep --
        db1 = os.path.join(tmp, "sweep.db")
        svc.reset_events()
        real_app = dmod.App
        dmod.App = FakeApp
        try:
            FakeApp.fail = False
            dmod.run_daemon_loop(daemon_args(db1))
            check("completed sweeps emit one event each",
                  sweep_event_count(svc) == 2,
                  f"got {sweep_event_count(svc)}")
            check("completed sweeps write one snapshot each",
                  snapshot_count(db1) == 2,
                  f"got {snapshot_count(db1)}")

            before_events = sweep_event_count(svc)
            before_snaps = snapshot_count(db1)
            FakeApp.fail = True
            dmod.run_daemon_loop(daemon_args(db1, iterations=1))
            check("failed sweep emits no event",
                  sweep_event_count(svc) == before_events,
                  f"{before_events} -> {sweep_event_count(svc)}")
            check("failed sweep writes no snapshot",
                  snapshot_count(db1) == before_snaps,
                  f"{before_snaps} -> {snapshot_count(db1)}")
        finally:
            dmod.App = real_app
            FakeApp.fail = False

        # -- single scanner: second instance refuses -----------------------
        lock = os.path.join(tmp, "locks", "scanner.lock")
        db2 = os.path.join(tmp, "second.db")
        fh = svc.acquire_scanner_lock(lock)
        try:
            check("lock path uses the temp dir, not the runtime dir",
                  os.path.abspath(lock).startswith(
                      os.path.abspath(tmp) + os.sep),
                  lock)
            second = subprocess.run(
                [sys.executable, "-m", "proto.service"]
                + service_argv(lock, db2),
                cwd=REPO, capture_output=True, text=True, timeout=120)
            check("second instance refuses (nonzero exit)",
                  second.returncode == svc.LOCK_REFUSED_EXIT,
                  f"got {second.returncode}: {second.stderr[-200:]}")
            check("second instance says already running",
                  "already running" in second.stderr.lower(),
                  second.stderr[-200:])

            err = io.StringIO()
            old_stderr = sys.stderr
            sys.stderr = err
            try:
                code = svc.main(service_argv(lock, db2))
            finally:
                sys.stderr = old_stderr
            check("in-process second service refuses with exit code",
                  code == svc.LOCK_REFUSED_EXIT, f"got {code}")
            check("refusal message names the lock",
                  "already running" in err.getvalue().lower(),
                  err.getvalue()[-200:])
            check("refused instance starts no duplicate scanning",
                  sweep_event_count(svc) == 2
                  and snapshot_count(db2) == 0,
                  f"events={sweep_event_count(svc)} snaps2={snapshot_count(db2)}")
        finally:
            fh.close()

        # lock released -> service runs once, cleanly, in-process
        svc.reset_events()
        out = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = out
        try:
            code = svc.main(service_argv(lock, db2))
        finally:
            sys.stdout = old_stdout
        check("first instance runs to completion (exit 0)", code == 0,
              f"got {code}")

        # -- clean shutdown: SIGTERM stops loop, server, sessions in order --
        lock3 = os.path.join(tmp, "locks3", "scanner.lock")
        db3 = os.path.join(tmp, "third.db")
        svc.reset_events()
        stop = threading.Event()
        old_handlers = svc.install_signal_handlers(stop)
        box = {}

        def _run():
            box["result"] = svc.run_service(
                port=0, token="shutdown-test-token", db=db3,
                data_dir=os.path.join(tmp, "bars3"), lock_path=lock3,
                daemon_argv=["--no-scan", "--interval", "60",
                             "--collect-every", str(HUGE),
                             "--resolve-every", str(HUGE),
                             "--measure-every", str(HUGE)],
                stop_event=stop, install_signals=False,
                on_serving=lambda server, port: box.setdefault(
                    "port", port))

        worker = threading.Thread(target=_run, name="svc-shutdown-test")
        worker.start()
        resp_status = None
        try:
            port = None
            deadline = time.time() + 20
            while time.time() < deadline:
                port = box.get("port")
                if port:
                    try:
                        conn = http.client.HTTPConnection(
                            "127.0.0.1", port, timeout=5)
                        conn.request("GET", "/healthz")
                        resp = conn.getresponse()
                        conn.close()
                        resp_status = resp.status
                        if resp_status == 200:
                            break
                    except OSError:
                        pass
                time.sleep(0.1)
            check("service answers /healthz before shutdown",
                  port is not None and resp_status == 200,
                  f"port={port} status={resp_status}")
            os.kill(os.getpid(), signal.SIGTERM)
            worker.join(timeout=30)
            check("shutdown finishes promptly", not worker.is_alive())
            result = box.get("result", {})
            check("shutdown order is loop, server, sessions",
                  result.get("shutdown_order")
                  == ["loop", "server", "sessions"],
                  str(result.get("shutdown_order")))
            try:
                probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                probe.settimeout(5)
                probe.connect(("127.0.0.1", port))
                probe.close()
                freed = False
            except OSError:
                freed = True
            check("port freed after shutdown", freed, f"port={port}")
            conn = sqlite3.connect(db3, timeout=5)
            try:
                integrity = conn.execute(
                    "PRAGMA integrity_check").fetchone()[0]
            finally:
                conn.close()
            check("db consistent after shutdown", integrity == "ok",
                  str(integrity))
        finally:
            stop.set()
            worker.join(timeout=30)
            svc.restore_signal_handlers(old_handlers)

        # -- daemon is a thin wrapper; flags preserved ----------------------
        daemon_src = open(os.path.join(REPO, "proto", "daemon.py"),
                          encoding="utf-8").read()
        check("daemon owns the loop (no copy in service)",
              "def run_daemon_loop" in daemon_src)
        check("daemon has no copied scan body",
              "scan_once" not in daemon_src.replace(
                  "app.scan_once()", "").replace("FakeApp", ""),
              "loop body leaked into the wrapper")
        check("daemon delegates to the service",
              "from .service import" in daemon_src
              or "from . import service" in daemon_src)
        service_src = open(os.path.join(REPO, "proto", "service.py"),
                           encoding="utf-8").read()
        check("service imports the daemon loop (move-by-import)",
              "run_daemon_loop" in service_src
              and "scan_once()" not in service_src)
        help_proc = subprocess.run(
            [sys.executable, "-m", "proto.daemon", "--help"],
            cwd=REPO, capture_output=True, text=True, timeout=120)
        for flag in ("--stake", "--coins", "--interval", "--no-scan",
                     "--collect-every", "--resolve-every", "--port"):
            check(f"daemon wrapper preserves {flag}",
                  flag in help_proc.stdout, help_proc.stdout[-200:])
        check("daemon --help exits 0", help_proc.returncode == 0,
              str(help_proc.returncode))

        # -- systemd unit points at the service ------------------------------
        unit = open(os.path.join(REPO, "altradar.service.example"),
                    encoding="utf-8").read()
        exec_lines = [ln for ln in unit.splitlines()
                      if ln.strip().startswith("ExecStart")]
        check("unit has one ExecStart", len(exec_lines) == 1,
              str(exec_lines))
        check("unit ExecStart runs proto.service",
              exec_lines and "proto.service" in exec_lines[0],
              str(exec_lines))
        check("unit no longer ExecStarts proto.daemon",
              "proto.daemon" not in unit)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # -- tripwire self-check: non-loopback connect must raise -------------
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
        s.close()
        check("tripwire blocks non-loopback connect", False,
              "connect succeeded")
    except NetworkTripwire:
        check("tripwire blocks non-loopback connect", True)
    except Exception as e:  # pragma: no cover — must never reach the network
        check("tripwire blocks non-loopback connect", False, repr(e))

    print()
    if FAILURES:
        print(f"FAIL  tests/test_service_lifecycle.py  "
              f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
        sys.exit(1)
    print(f"ALL PASS  tests/test_service_lifecycle.py  ({CHECKS} checks)")


if __name__ == "__main__":
    main()
