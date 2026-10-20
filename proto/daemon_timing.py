"""Per-stage timing and persisted cadence for the collection daemon (WS-F).

Two small JSON sidecars live next to the signals DB (never inside the
archives themselves):

``.timing.json``
    The last few loop iterations with each stage's measured wall-clock
    duration and its OUTCOME (``ok`` / ``skipped`` / ``failed`` /
    ``not_due`` / ``partial``).  Written atomically (temp file in the same
    directory, then ``os.replace``) so a crash can never leave a
    half-written file behind.  The last 5 iterations are kept, newest last,
    so the file stays small.

``.cadence.json``
    The per-stage due-times: ``{"last_run_ts": int, "interval_s": int}`` per
    stage.  A stage is due when ``now - last_run_ts >= interval_s``; a
    stage with no persisted entry (first boot) is due immediately.

Contract (deliberately conservative):

* A stage that FAILED is never recorded as ``ok`` and is never marked as
  run, so it retries on the next iteration.
* A stage that only PARTIALLY completed is recorded as ``partial`` with
  its per-coin error count.  Like the archive throttles
  (``proto/settlements.py`` / ``proto/prices.py``) it is still marked as
  run, because the successful part is real work that must not be repeated
  every sweep.
* The cadence file is written ONLY when a stage actually completes, so the
  first boot after this change behaves exactly like today (everything runs)
  and every later restart resumes instead of repeating the work.
* Nothing here ever writes to the production archives: the sidecars sit
  beside the DB, and every write goes through ``atomic_write_json``.

**The first-sweep state is PROCESS-LOCAL.**  ``TimingLog.first_sweep``
starts at ``pending`` in every fresh process: it is NOT persisted (the
sidecar holds the iterations, not the sweep state) and it RESETS TO
``pending`` on every restart.  It therefore means "has THIS process
completed its first sweep since it started", never "has the system ever
completed a sweep".  A consumer that needs the system-wide fact must read
the persisted sweep records (or the service's own ``sweep_count`` /
``last_sweep_ts``), not this block.
"""

import json
import os
import tempfile
import threading
import time

# The stages of one loop iteration, in the order the daemon runs them.
STAGES = ("scan", "collect", "funding", "settlements", "prices", "resolve",
          "measure", "quant")

# Every outcome a stage may report.  "ok" is the ONLY outcome that means
# "this stage did all of its work".
OUTCOMES = ("ok", "skipped", "failed", "not_due", "partial")

# How many iterations the timing sidecar keeps.
KEEP_ITERATIONS = 5

TIMING_FILE = ".timing.json"
CADENCE_FILE = ".cadence.json"


def sidecar_path(db_path, name):
    """Path of a sidecar file that lives next to the signals DB."""
    d = os.path.dirname(os.path.abspath(db_path)) if db_path else ""
    return os.path.join(d or ".", name)


def db_key(db_path):
    """The absolute DB path a sidecar belongs to ("" when unknown)."""
    if not db_path:
        return ""
    try:
        return os.path.abspath(db_path)
    except (OSError, TypeError):
        return str(db_path)


def atomic_write_json(path, payload):
    """Write *payload* as JSON to *path* atomically (temp file + replace).

    The temp file lives in the target directory (so the rename is on the
    same filesystem), it is removed when anything goes wrong, and the
    parent directory is fsynced after the rename so the replacement is
    crash-durable (a rename that is not fsynced can be lost by a power
    cut even though the file content was fsynced).
    """
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # Make the rename itself durable.
    try:
        dfd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass
    return path


def read_json(path):
    """Parsed JSON from *path*; ``{}`` when missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def format_stages(stages):
    """One-line summary of a stage map: ``scan=458.2 collect=851.0 ...``.

    Stages that did not run show ``0.0``; a stage whose outcome is not
    ``ok`` carries it in parentheses, so a failure is never printed like a
    success.
    """
    parts = []
    for name in STAGES:
        entry = stages.get(name)
        if not isinstance(entry, dict):
            # A stage that was never reached (interrupted iteration) is
            # shown as skipped, never as a silent success.
            parts.append(f"{name}=0.0(skipped)")
            continue
        dur = entry.get("duration_s")
        if not isinstance(dur, (int, float)):
            dur = 0.0
        outcome = entry.get("outcome") or "ok"
        text = f"{name}={float(dur):.1f}"
        if outcome != "ok":
            text += f"({outcome})"
        parts.append(text)
    return "stages: " + " ".join(parts)


class TimingLog:
    """Per-iteration stage timing + the first-sweep state machine.

    The daemon records one entry per stage per iteration; the log keeps the
    last :data:`KEEP_ITERATIONS` iterations on disk so a restart can still
    report what the previous run measured.
    """

    def __init__(self, path=None, keep=KEEP_ITERATIONS, now_fn=time.time,
                 db=None):
        self.path = path
        self.keep = max(1, int(keep))
        self.now_fn = now_fn
        self.db = db_key(db)
        self.iterations = []
        self.current = None
        # The first-sweep state is published to the HTTP thread, so it is
        # never mutated field by field: a NEW dict is built and swapped in
        # under the lock (a reader can only ever see a whole, consistent
        # snapshot - never "complete" with a missing completed_ts).
        self._lock = threading.Lock()
        self._first_sweep = {"state": "pending", "started_ts": None,
                             "completed_ts": None, "duration_s": None,
                             "stages": {}}
        if path:
            self.load()

    # -- persistence ----------------------------------------------------
    def load(self):
        """Adopt the persisted iterations (never invents any).

        A sidecar that belongs to ANOTHER database is ignored: the file
        lives next to the DB, and a directory may hold more than one.
        """
        data = read_json(self.path) if self.path else {}
        if self.db and data.get("db") not in ("", None, self.db):
            return self.iterations
        rows = data.get("iterations")
        if isinstance(rows, list):
            with self._lock:
                self.iterations = [r for r in rows if isinstance(r, dict)]
                self.iterations = self.iterations[-self.keep:]
        return self.iterations

    def save(self):
        """Persist the kept iterations atomically (best effort).

        A sidecar that cannot be written NEVER fails the stage that
        produced it: the measurement is still printed and still returned.
        """
        if not self.path:
            return None
        try:
            with self._lock:
                payload = {"version": 1, "db": self.db,
                           "iterations": list(self.iterations)}
            atomic_write_json(self.path, payload)
        except Exception:
            return None
        return self.path

    # -- iteration lifecycle -------------------------------------------
    def begin(self, ts=None):
        """Start a new iteration record (the previous one is kept)."""
        now = int(ts if ts is not None else self.now_fn())
        with self._lock:
            self.current = {"ts": now, "stages": {}}
            return self.current

    def record(self, stage, duration_s, outcome, **extra):
        """Record one stage's measured duration and outcome.

        The outcome is validated against :data:`OUTCOMES`; an unknown value
        is stored as ``failed`` (never silently as a success).

        The record is written UNDER THE LOCK: the HTTP thread reads the
        running iteration's stages concurrently (``last_stages``), and an
        unsynchronised write could raise "dictionary changed size during
        iteration" out of that reader.
        """
        if outcome not in OUTCOMES:
            outcome = "failed"
        entry = {"duration_s": round(float(duration_s or 0.0), 3),
                 "outcome": outcome}
        for key, value in extra.items():
            if value is not None:
                entry[key] = value
        with self._lock:
            if self.current is None:
                self.current = {"ts": int(self.now_fn()), "stages": {}}
            self.current["stages"][stage] = entry
        return entry

    def end(self, duration_s=None, ts=None):
        """Close the current iteration, persist it and return it."""
        with self._lock:
            if self.current is None:
                return None
            if duration_s is not None:
                self.current["duration_s"] = round(float(duration_s), 3)
            self.current["ts"] = int(ts if ts is not None
                                     else self.current.get("ts")
                                     or self.now_fn())
            finished = self.current
            self.current = None
            self.iterations.append(finished)
            self.iterations = self.iterations[-self.keep:]
        self.save()
        return finished

    # -- readers --------------------------------------------------------
    def last(self):
        """The last completed iteration record (or None)."""
        with self._lock:
            return self.iterations[-1] if self.iterations else None

    def last_stages(self):
        """``{stage: {duration_s, outcome}}`` of the most recent iteration.

        The RUNNING iteration wins when it has stages already (the
        durations measured so far are more useful than the previous run's),
        otherwise the last completed iteration is used.

        The snapshot is taken UNDER THE LOCK and copied out of it: the
        daemon thread mutates the running iteration's stage map while this
        reader walks it, and an unsynchronised walk could raise
        "dictionary changed size during iteration" (the caller would then
        silently lose the whole stage map).
        """
        with self._lock:
            current = self.current
            stages = dict(current["stages"]) if current else {}
            if stages:
                return stages
            last = self.iterations[-1] if self.iterations else None
            return dict((last or {}).get("stages") or {})

    def stage_durations(self):
        """``{stage: duration_s}`` of the most recent iteration.

        The same snapshot ``last_stages`` returns (the running iteration
        when it has stages, the last completed one otherwise).
        """
        return dict((k, v.get("duration_s"))
                    for k, v in self.last_stages().items())

    def summary(self):
        """The one-line summary of the last completed iteration."""
        return format_stages(self.last_stages())

    # -- first sweep ----------------------------------------------------
    @property
    def first_sweep(self):
        """The current first-sweep snapshot (read-only).

        PROCESS-LOCAL: it starts at ``pending`` in every fresh process and
        resets to ``pending`` on every restart.  It says "this process has
        completed its first sweep", NOT "the system ever completed a
        sweep" (see the module docstring).
        """
        return self.first_sweep_state()

    def mark_sweep_started(self, ts=None):
        """The first iteration started: ``pending`` -> ``in_progress``."""
        with self._lock:
            current = self._first_sweep
            if current["state"] != "pending":
                return dict(current)
            new = dict(current)
            new["state"] = "in_progress"
            new["started_ts"] = int(ts if ts is not None else self.now_fn())
            new["stages"] = dict(current.get("stages") or {})
            self._first_sweep = new
            return dict(new)

    def mark_sweep_completed(self, ts=None, duration_s=None, stages=None):
        """A sweep completed: the first-sweep state becomes ``complete``.

        Only ever called when a sweep ACTUALLY completed AND the
        iteration ended normally - a failed or interrupted iteration leaves
        the state at ``in_progress`` with ``completed_ts`` still ``None``
        (never a false "complete").

        The new state is built as a whole dict and swapped in under the
        lock, so a concurrent reader always sees a consistent snapshot.
        """
        with self._lock:
            current = self._first_sweep
            if current["state"] == "complete":
                return dict(current)
            new = dict(current)
            if new["state"] == "pending":
                new["state"] = "in_progress"
                new["started_ts"] = int(ts if ts is not None
                                        else self.now_fn())
            new["state"] = "complete"
            new["completed_ts"] = int(ts if ts is not None
                                      else self.now_fn())
            new["duration_s"] = (None if duration_s is None
                                 else round(float(duration_s), 3))
            new["stages"] = dict(stages or {})
            self._first_sweep = new
            return dict(new)

    def first_sweep_state(self):
        """The ``first_sweep`` block published by ``/scanner-health``.

        PROCESS-LOCAL: it is not persisted and it resets to ``pending`` on
        every restart.  It must never be read as "the system ever completed
        a sweep".
        """
        with self._lock:
            fs = dict(self._first_sweep)
        fs["stages"] = dict(fs.get("stages") or {})
        return fs


class Cadence:
    """Persisted per-stage due-times (``now - last_run_ts >= interval_s``).

    A stage with no entry is due immediately (first boot).  ``mark`` is
    only ever called for a stage that actually completed.
    """

    def __init__(self, path, intervals=None, now_fn=time.time, db=None):
        self.path = path
        self.intervals = dict(intervals or {})
        self.now_fn = now_fn
        self.db = db_key(db)
        self.entries = {}
        if path:
            data = read_json(path)
            stages = data.get("stages")
            if not isinstance(stages, dict):
                stages = {k: v for k, v in data.items()
                          if isinstance(v, dict) and "last_run_ts" in v}
            # A sidecar that belongs to ANOTHER database is ignored: the
            # file lives next to the DB and a directory may hold more than
            # one (tests, side-by-side deployments).
            if not self.db or data.get("db") in ("", None, self.db):
                for stage, entry in stages.items():
                    if isinstance(entry, dict):
                        self.entries[stage] = entry

    def interval(self, stage):
        """The configured interval of *stage* (0 = every iteration)."""
        return int(self.intervals.get(stage, 0) or 0)

    def due(self, stage, now=None):
        """True when *stage* must run now.

        A stage with no persisted entry counts as "never ran"
        (``last_run_ts = 0``), exactly like the process-local float it
        replaces: it is due as soon as its interval has elapsed, so the
        first boot after this change runs the work and a huge interval
        still never fires.
        """
        entry = self.entries.get(stage)
        last = 0
        if isinstance(entry, dict):
            value = entry.get("last_run_ts")
            if isinstance(value, (int, float)):
                last = value
        now_ts = now if now is not None else self.now_fn()
        return (now_ts - last) >= self.interval(stage)

    def mark(self, stage, now=None):
        """Persist *stage* as run at *now* (only after a real completion).

        A sidecar that cannot be written never fails the stage: the
        in-memory due-time is still updated for this process.
        """
        now_ts = int(now if now is not None else self.now_fn())
        self.entries[stage] = {"last_run_ts": now_ts,
                               "interval_s": self.interval(stage)}
        self.save()
        return self.entries[stage]

    def last_run_ts(self, stage):
        """When *stage* last completed (None when it never ran)."""
        entry = self.entries.get(stage) or {}
        value = entry.get("last_run_ts")
        return value if isinstance(value, (int, float)) else None

    def save(self):
        """Persist the cadence atomically (best effort)."""
        if not self.path:
            return None
        try:
            atomic_write_json(self.path, {"version": 1, "db": self.db,
                                          "stages": self.entries})
        except Exception:
            return None
        return self.path
