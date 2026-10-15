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
import json
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


def journal_sweep_count(db):
    if not os.path.exists(db):
        return 0
    conn = sqlite3.connect(db, timeout=5)
    try:
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM watcher_events"
                " WHERE kind='sweep-complete'").fetchone()[0]
        except sqlite3.Error:
            return 0
    finally:
        conn.close()


def latest_snapshot_counts(db):
    conn = sqlite3.connect(db, timeout=5)
    try:
        try:
            return conn.execute(
                "SELECT eligible_count, evaluated_count"
                " FROM dashboard_snapshot"
                " ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()
        except sqlite3.Error:
            return None
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

        # -- no-scan collect cycles count as sweeps (readiness) -------------
        # Sweep = one data-collection pass (scan OR collect). A no-scan
        # deployment (the shipped unit's flags) must still go ready and
        # record snapshots via its collect cycles; evaluated is honestly
        # 0, never invented. A failed collect records nothing.
        db_ns = os.path.join(tmp, "noscan.db")
        svc.reset_events()
        real_collect = dmod.colmod.collect_full_universe
        dmod.App = FakeApp
        try:
            dmod.colmod.collect_full_universe = (
                lambda *a, **k: {"BTC": 3, "ETH": 1})
            dmod.run_daemon_loop(daemon_args(db_ns, no_scan=True,
                                             iterations=1, collect_every=0))
            check("no-scan successful collect emits one sweep event",
                  sweep_event_count(svc) == 1,
                  f"got {sweep_event_count(svc)}")
            check("no-scan successful collect writes one snapshot",
                  snapshot_count(db_ns) == 1,
                  f"got {snapshot_count(db_ns)}")
            check("no-scan snapshot counts honest (2 eligible, 0 evaluated)",
                  latest_snapshot_counts(db_ns) == (2, 0),
                  f"got {latest_snapshot_counts(db_ns)}")
            check("no-scan successful collect journals sweep-complete",
                  journal_sweep_count(db_ns) == 1,
                  f"got {journal_sweep_count(db_ns)}")
            fired = []
            dmod.run_daemon_loop(daemon_args(db_ns, no_scan=True,
                                             iterations=1, collect_every=0),
                                 on_sweep=lambda eligible, evaluated:
                                 fired.append((eligible, evaluated)))
            check("no-scan successful collect marks ready (on_sweep fired)",
                  fired == [(2, 0)], f"got {fired}")

            def _collect_boom(*a, **k):
                raise RuntimeError("collect down")

            dmod.colmod.collect_full_universe = _collect_boom
            svc.reset_events()
            snaps_before = snapshot_count(db_ns)
            journal_before = journal_sweep_count(db_ns)
            fired_fail = []
            dmod.run_daemon_loop(daemon_args(db_ns, no_scan=True,
                                             iterations=1, collect_every=0),
                                 on_sweep=lambda eligible, evaluated:
                                 fired_fail.append((eligible, evaluated)))
            check("no-scan failed collect stays unready (no sweep fired)",
                  fired_fail == [], f"got {fired_fail}")
            check("no-scan failed collect writes no snapshot",
                  snapshot_count(db_ns) == snaps_before,
                  f"{snaps_before} -> {snapshot_count(db_ns)}")
            check("no-scan failed collect journals nothing",
                  journal_sweep_count(db_ns) == journal_before,
                  f"{journal_before} -> {journal_sweep_count(db_ns)}")
        finally:
            dmod.colmod.collect_full_universe = real_collect
            dmod.App = real_app
        svc.reset_events()

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

    # -- WS2: scanner resilience (focused lifecycle coverage) ---------------
    # Distinct signals: /healthz = process alive; /readyz = first sweep done;
    # /scanner-health = scanner lifecycle state. The API must stay reachable
    # while the scanner is down; a finite --iterations run that completes
    # normally is completion, not a crash, and must not be restarted.
    tmp2 = tempfile.mkdtemp(prefix="svc-resilience-")
    try:
        real_loop = dmod.run_daemon_loop
        loop_calls = {"n": 0}
        fail_mode = {"mode": "none"}  # "none" | "one" | "all"

        def _counting_loop(args, stop_event=None, on_sweep=None,
                           app_box=None):
            loop_calls["n"] += 1
            if fail_mode["mode"] == "all":
                raise RuntimeError("scanner boom")
            if fail_mode["mode"] == "one":
                # Fail exactly the first invocation of this scenario,
                # deterministically — no dependence on backoff timing.
                fail_mode["mode"] = "none"
                raise RuntimeError("scanner boom")
            return real_loop(args, stop_event=stop_event, on_sweep=on_sweep,
                             app_box=app_box)
        dmod.run_daemon_loop = _counting_loop
        try:
            # -- finite run completes once, is not restarted ---------------
            db_fin = os.path.join(tmp2, "finite.db")
            lock_fin = os.path.join(tmp2, "locks_fin", "scanner.lock")
            svc.reset_events()
            box_fin = {}
            stop_fin = threading.Event()

            def _run_finite():
                box_fin["result"] = svc.run_service(
                    port=0, token="finite-token", db=db_fin,
                    data_dir=os.path.join(tmp2, "bars_fin"),
                    lock_path=lock_fin,
                    daemon_argv=["--no-scan", "--iterations", "1",
                                 "--interval", "60",
                                 "--collect-every", str(HUGE),
                                 "--resolve-every", str(HUGE),
                                 "--measure-every", str(HUGE)],
                    stop_event=stop_fin, install_signals=False,
                    on_serving=lambda s, p: box_fin.setdefault("port", p))

            w = threading.Thread(target=_run_finite, name="svc-finite-test")
            w.start()
            port_fin = None
            deadline = time.time() + 30
            while time.time() < deadline:
                port_fin = box_fin.get("port")
                if port_fin:
                    break
                time.sleep(0.05)
            w.join(timeout=60)
            res_fin = box_fin.get("result", {})
            check("finite --iterations run completes once (no restart)",
                  loop_calls["n"] == 1, f"loop invocations: {loop_calls['n']}")
            check("finite run result ok", res_fin.get("ok") is True,
                  str(res_fin.get("loop_error")))
            check("finite run shuts down in order",
                  res_fin.get("shutdown_order")
                  == ["loop", "server", "sessions"],
                  str(res_fin.get("shutdown_order")))

            # -- scanner crash: failed state visible via API, then recover --
            db_crash = os.path.join(tmp2, "crash.db")
            lock_crash = os.path.join(tmp2, "locks_crash", "scanner.lock")
            svc.reset_events()
            loop_calls["n"] = 0  # per-scenario counter
            fail_mode["mode"] = "one"  # fail exactly the first attempt
            box_crash = {}
            stop_crash = threading.Event()
            health_seen = {}

            def _run_crash():
                box_crash["result"] = svc.run_service(
                    port=0, token="crash-token", db=db_crash,
                    data_dir=os.path.join(tmp2, "bars_crash"),
                    lock_path=lock_crash,
                    daemon_argv=["--no-scan", "--interval", "60",
                                 "--collect-every", str(HUGE),
                                 "--resolve-every", str(HUGE),
                                 "--measure-every", str(HUGE)],
                    stop_event=stop_crash, install_signals=False,
                    backoff_base_s=0.05, backoff_max_s=0.1,
                    max_scanner_retries=3,
                    on_serving=lambda s, p: box_crash.setdefault("port", p),
                    on_health=lambda getter: box_crash.setdefault(
                        "health", getter))

            w2 = threading.Thread(target=_run_crash, name="svc-crash-test")
            w2.start()
            port_crash = None
            deadline = time.time() + 30
            while time.time() < deadline:
                port_crash = box_crash.get("port")
                if port_crash:
                    break
                time.sleep(0.05)

            def _get(path, token="crash-token"):
                if not port_crash:
                    raise RuntimeError("crash-test service never bound")
                conn = http.client.HTTPConnection("127.0.0.1", port_crash,
                                                  timeout=5)
                headers = {"Authorization": f"Bearer {token}"}
                conn.request("GET", path, headers=headers)
                resp = conn.getresponse()
                body = resp.read()
                conn.close()
                return resp.status, json.loads(body.decode("utf-8"))

            # The first attempt fails (fail_mode "one" armed above); after
            # the backoff the retry succeeds and the loop parks until stop.
            failed_status = None
            failed_body = None
            deadline = time.time() + 30
            while time.time() < deadline:
                status, body = _get("/scanner-health")
                failed_status = body.get("data", {}).get("status")
                if failed_status == svc.SCANNER_FAILED:
                    failed_body = body
                    break
                time.sleep(0.02)
            check("scanner failure is readable via /scanner-health",
                  failed_status == svc.SCANNER_FAILED,
                  f"status={failed_status}")
            check("failed scanner health reports last_error",
                  ((failed_body or {}).get("data") or {}).get("last_error")
                  and "boom"
                  in str(((failed_body or {}).get("data") or {})
                         .get("last_error")),
                  str(failed_body))
            # API stays reachable while the scanner is down; readiness is
            # the distinct first-sweep signal (no sweep ever ran).
            status, body = _get("/healthz")
            check("/healthz stays 200 while scanner failed",
                  status == 200, f"status={status}")
            status, body = _get("/readyz")
            check("/readyz stays 503 while scanner failed (distinct signal)",
                  status == 503, f"status={status}")

            # First attempt failed -> recover; the retry (second invocation)
            # runs the real loop and parks. No fail_mode change needed: the
            # "one" mode already consumed itself on the first invocation.
            recovered = None
            deadline = time.time() + 30
            while time.time() < deadline:
                status, body = _get("/scanner-health")
                recovered = body.get("data", {}).get("status")
                if recovered in (svc.SCANNER_HEALTHY, svc.SCANNER_RESTARTING):
                    break
                time.sleep(0.02)
            check("scanner recovers to a non-failed state",
                  recovered in (svc.SCANNER_HEALTHY, svc.SCANNER_RESTARTING),
                  f"status={recovered}")
            getter = box_crash.get("health")
            check("on_health callback delivers a readable health getter",
                  callable(getter) and isinstance(getter(), dict)
                  and "status" in getter(), str(getter))
            check("health getter counts the crash as one restart",
                  getter().get("restart_count") == 1,
                  str(getter()))
            stop_crash.set()
            w2.join(timeout=60)
            res_crash = box_crash.get("result", {})
            check("crash/recovery run shuts down in order",
                  res_crash.get("shutdown_order")
                  == ["loop", "server", "sessions"],
                  str(res_crash.get("shutdown_order")))

            # -- retry cap exhausted: server stays up, scanner parked -------
            db_cap = os.path.join(tmp2, "cap.db")
            lock_cap = os.path.join(tmp2, "locks_cap", "scanner.lock")
            svc.reset_events()
            loop_calls["n"] = 0  # per-scenario counter
            fail_mode["mode"] = "all"  # every attempt fails
            box_cap = {}
            stop_cap = threading.Event()

            def _run_cap():
                box_cap["result"] = svc.run_service(
                    port=0, token="cap-token", db=db_cap,
                    data_dir=os.path.join(tmp2, "bars_cap"),
                    lock_path=lock_cap,
                    daemon_argv=["--no-scan", "--interval", "60",
                                 "--collect-every", str(HUGE),
                                 "--resolve-every", str(HUGE),
                                 "--measure-every", str(HUGE)],
                    stop_event=stop_cap, install_signals=False,
                    backoff_base_s=0.05, backoff_max_s=0.1,
                    max_scanner_retries=2,
                    on_serving=lambda s, p: box_cap.setdefault("port", p))

            w3 = threading.Thread(target=_run_cap, name="svc-cap-test")
            w3.start()
            port_cap = None
            deadline = time.time() + 30
            while time.time() < deadline:
                port_cap = box_cap.get("port")
                if port_cap:
                    break
                time.sleep(0.05)

            def _get_cap(path):
                conn = http.client.HTTPConnection("127.0.0.1", port_cap,
                                                  timeout=5)
                conn.request("GET", path,
                             headers={"Authorization": "Bearer cap-token"})
                resp = conn.getresponse()
                body = resp.read()
                conn.close()
                return resp.status, json.loads(body.decode("utf-8"))

            # Every attempt fails (fail_mode "all" armed above); poll until
            # the cap is exhausted and the scanner is parked FAILED.
            parked = None
            deadline = time.time() + 60
            while time.time() < deadline:
                status, body = _get_cap("/scanner-health")
                parked = body.get("data", {}).get("status")
                if parked == svc.SCANNER_FAILED and loop_calls["n"] >= 3:
                    break
                time.sleep(0.05)
            check("retry cap gives up after the capped attempts",
                  loop_calls["n"] == 3, f"loop invocations: {loop_calls['n']}")
            status, body = _get_cap("/healthz")
            check("server stays up after scanner gives up",
                  status == 200, f"status={status}")
            status, body = _get_cap("/scanner-health")
            check("parked scanner health still reports failed",
                  (body.get("data") or {}).get("status")
                  == svc.SCANNER_FAILED, str(body))
            stop_cap.set()
            w3.join(timeout=60)
            res_cap = box_cap.get("result", {})
            check("give-up run shuts down in order",
                  res_cap.get("shutdown_order")
                  == ["loop", "server", "sessions"],
                  str(res_cap.get("shutdown_order")))
        finally:
            dmod.run_daemon_loop = real_loop
            fail_mode["mode"] = "none"
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # -- unwired server: /scanner-health must not claim health ------------
    tmp3 = tempfile.mkdtemp(prefix="svc-unwired-")
    try:
        server_u = svc.serve(0, "unwired-token", lambda: {})
        try:
            port_u = server_u.server_address[1]
            conn = http.client.HTTPConnection("127.0.0.1", port_u, timeout=5)
            conn.request("GET", "/scanner-health",
                         headers={"Authorization": "Bearer unwired-token"})
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
            check("unwired /scanner-health answers 503, not 500",
                  resp.status == 503, f"status={resp.status}")
            payload = json.loads(body.decode("utf-8"))
            check("unwired /scanner-health envelope is UNAVAILABLE",
                  (payload.get("error") or {}).get("code") == "UNAVAILABLE",
                  str(payload))
        finally:
            server_u.shutdown()
            server_u.server_close()
    finally:
        shutil.rmtree(tmp3, ignore_errors=True)

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
