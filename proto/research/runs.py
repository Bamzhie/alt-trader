"""Append-only registry for backtest runs and their terminal events.

A run's identity row and its ``started`` event are inserted before any
execution, so an abandoned attempt is still visible. Status changes are
appended as new ``run_event`` rows; no row is ever updated or deleted.
"""

import hashlib
import json
import os
import subprocess
import time
import uuid

from proto.quant.prereg import git_rev
from proto.research import store

RUN_KINDS = ("exploratory", "tuning", "benchmark", "rerun")
TERMINAL_STATUSES = ("completed", "failed", "interrupted")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


class RunError(Exception):
    """Raised when a run request is invalid or would rewrite history."""


def _now():
    return int(time.time())


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256(obj):
    return hashlib.sha256(_canonical(obj).encode()).hexdigest()


def _code_dirty(root):
    try:
        out = subprocess.run(["git", "-C", root, "status", "--porcelain"],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return 1
    if out.returncode != 0:
        return 1
    return 1 if out.stdout.strip() else 0


def start_run(db_path, *, kind, snapshot_id, sample_id, config, assumptions,
              params=None, code_rev=None, code_dirty=None, parent_run_id=None,
              label=None, notes=None):
    """Insert the run identity and its ``started`` event; return the run id."""
    if kind not in RUN_KINDS:
        raise RunError(f"unknown run kind: {kind!r}")
    if not isinstance(config, dict):
        raise RunError("config must be a JSON object")
    version = config.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RunError("config needs a non-empty string version")
    if params is None:
        params = {}
    config_json = _canonical(config)
    assumptions_json = _canonical(assumptions)
    params_json = _canonical(params)
    if code_rev is None:
        code_rev = git_rev(_REPO_ROOT) or "unknown"
    if code_dirty is None:
        code_dirty = _code_dirty(_REPO_ROOT)
    run_id = f"run-{uuid.uuid4().hex}"
    ts = _now()

    conn = store.connect(db_path)
    try:
        store.ensure_run_event_guard(conn)
        if conn.execute("SELECT 1 FROM source_snapshot WHERE snapshot_id = ?",
                        (snapshot_id,)).fetchone() is None:
            raise RunError(f"unknown snapshot: {snapshot_id}")
        sample = conn.execute(
            "SELECT universe_id, seed FROM sample WHERE sample_id = ?",
            (sample_id,)).fetchone()
        if sample is None:
            raise RunError(f"unknown sample: {sample_id}")
        universe_id, seed = sample
        with conn:
            conn.execute(
                "INSERT INTO backtest_run VALUES (" + ",".join("?" * 18) + ")",
                (run_id, ts, kind, label, parent_run_id, snapshot_id,
                 sample_id, universe_id, seed, code_rev, int(code_dirty),
                 version, config_json, _sha256(config), assumptions_json,
                 _sha256(assumptions), params_json, notes))
            conn.execute(
                "INSERT INTO run_event VALUES (?,?,?,?,?,?,?)",
                (run_id, 1, ts, "started", None, None, None))
        return run_id
    finally:
        conn.close()


def finish_run(db_path, run_id, *, status, outputs=None, error=None,
               artifacts=None):
    """Append a terminal event; return it. Refuses any rewrite of history."""
    if status not in TERMINAL_STATUSES:
        raise RunError(f"status is not terminal: {status!r}")
    if status == "completed" and not isinstance(outputs, dict):
        raise RunError("completed requires a JSON object outputs")
    if status in ("failed", "interrupted") and (
            not isinstance(error, str) or not error.strip()):
        raise RunError(f"{status} requires a non-empty error")

    conn = store.connect(db_path)
    try:
        store.ensure_run_event_guard(conn)
        if conn.execute("SELECT 1 FROM backtest_run WHERE run_id = ?",
                        (run_id,)).fetchone() is None:
            raise RunError(f"unknown run: {run_id}")
        last = conn.execute(
            "SELECT seq, status FROM run_event WHERE run_id = ? "
            "ORDER BY seq DESC LIMIT 1", (run_id,)).fetchone()
        if last is None:
            raise RunError(f"run has no started event: {run_id}")
        seq, last_status = last
        if last_status in TERMINAL_STATUSES:
            raise RunError(f"run already finished: {run_id} ({last_status})")
        ts = _now()
        with conn:
            conn.execute(
                "INSERT INTO run_event VALUES (?,?,?,?,?,?,?)",
                (run_id, seq + 1, ts, status,
                 _canonical(outputs) if outputs is not None else None,
                 error,
                 _canonical(artifacts) if artifacts is not None else None))
        return {"run_id": run_id, "seq": seq + 1, "ts": ts, "status": status,
                "outputs": outputs, "error": error, "artifacts": artifacts}
    finally:
        conn.close()


def run_status(db_path, run_id):
    """Return the identity row (JSON fields parsed), latest status, events."""
    conn = store.connect(db_path, readonly=True)
    try:
        cur = conn.execute("SELECT * FROM backtest_run WHERE run_id = ?",
                           (run_id,))
        row = cur.fetchone()
        if row is None:
            raise RunError(f"unknown run: {run_id}")
        record = dict(zip((d[0] for d in cur.description), row))
        for key in ("config", "assumptions", "params"):
            record[key] = json.loads(record.pop(f"{key}_json"))
        events = [
            {"seq": seq, "ts": ts, "status": status,
             "outputs": json.loads(outputs) if outputs is not None else None,
             "error": error,
             "artifacts": json.loads(artifacts) if artifacts is not None else None}
            for seq, ts, status, outputs, error, artifacts in conn.execute(
                "SELECT seq, ts, status, outputs_json, error, artifacts_json "
                "FROM run_event WHERE run_id = ? ORDER BY seq", (run_id,))
        ]
        record["events"] = events
        record["status"] = events[-1]["status"] if events else None
        return record
    finally:
        conn.close()


def list_runs(db_path, *, status=None, kind=None):
    """List run identities with their latest event status, newest last."""
    conn = store.connect(db_path, readonly=True)
    try:
        cur = conn.execute(
            "SELECT r.*, (SELECT e.status FROM run_event e "
            "WHERE e.run_id = r.run_id ORDER BY e.seq DESC LIMIT 1) "
            "AS latest_status FROM backtest_run r "
            "ORDER BY r.created_ts, r.run_id")
        names = [d[0] for d in cur.description]
        result = []
        for row in cur.fetchall():
            record = dict(zip(names, row))
            record["status"] = record.pop("latest_status")
            if status is not None and record["status"] != status:
                continue
            if kind is not None and record["kind"] != kind:
                continue
            result.append(record)
        return result
    finally:
        conn.close()


def benchmark_eligible(db_path, run_id):
    """True only for a benchmark kind whose latest status is completed.

    Any failed/interrupted event in the chain disqualifies the run, even if a
    later event claims completion.
    """
    try:
        run = run_status(db_path, run_id)
    except RunError:
        return False
    if run["kind"] != "benchmark" or run["status"] != "completed":
        return False
    return not any(e["status"] in ("failed", "interrupted")
                   for e in run["events"])
