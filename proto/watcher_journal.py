"""Task 2: capped event journal + dashboard snapshots.

Append-only post-cut writers alongside the watcher tables. Pure stdlib,
sqlite3 conn-taking functions, no I/O, no network.

- watcher_events(ts, kind, coin, text): EXACTLY these 4 columns (no tier
  column per the spec). Kinds: emission, invalidation, expiry,
  sweep-complete. Cap JOURNAL_CAP=200 rows, oldest pruned first.
- dashboard_snapshot(ts, eligible_count, evaluated_count, active_setups,
  new_setups_24h, monitoring_coverage): one row per completed sweep.
  History is retained so the latest snapshot keeps its 24h comparison
  point; callers show "comparison unavailable" when no prior covers
  now-24h rather than inferring a value.
"""

import time

JOURNAL_CAP = 200

DAY_S = 86400

VALID_KINDS = ("emission", "invalidation", "expiry", "sweep-complete")

WATCHER_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS watcher_events (
  ts INTEGER NOT NULL,
  kind TEXT NOT NULL,
  coin TEXT NOT NULL,
  text TEXT NOT NULL
);
"""

DASHBOARD_SNAPSHOT_DDL = """
CREATE TABLE IF NOT EXISTS dashboard_snapshot (
  ts INTEGER NOT NULL,
  eligible_count INTEGER NOT NULL,
  evaluated_count INTEGER NOT NULL,
  active_setups INTEGER NOT NULL,
  new_setups_24h INTEGER NOT NULL,
  monitoring_coverage REAL
);
"""

CREATE_IDX_EVENTS_TS = """
CREATE INDEX IF NOT EXISTS idx_watcher_events_ts ON watcher_events(ts);
"""

CREATE_IDX_SNAPSHOT_TS = """
CREATE INDEX IF NOT EXISTS idx_dashboard_snapshot_ts
ON dashboard_snapshot(ts);
"""


def ensure_journal(conn):
    """Create watcher_events + dashboard_snapshot (idempotent, additive)."""
    conn.executescript(WATCHER_EVENTS_DDL)
    conn.executescript(DASHBOARD_SNAPSHOT_DDL)
    conn.executescript(CREATE_IDX_EVENTS_TS)
    conn.executescript(CREATE_IDX_SNAPSHOT_TS)
    conn.commit()


def log_event(conn, kind, coin, text, ts=None):
    """Append one journal row; prune oldest beyond JOURNAL_CAP.

    Raises ValueError on an unknown kind. Returns the row ts.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown journal kind {kind!r}; "
                         f"expected one of {VALID_KINDS}")
    ensure_journal(conn)
    ts = int(time.time()) if ts is None else int(ts)
    conn.execute(
        "INSERT INTO watcher_events (ts, kind, coin, text) VALUES (?,?,?,?)",
        (ts, kind, coin, text))
    overflow = (conn.execute("SELECT COUNT(*) FROM watcher_events")
                .fetchone()[0] - JOURNAL_CAP)
    if overflow > 0:
        conn.execute(
            """DELETE FROM watcher_events WHERE rowid IN (
                 SELECT rowid FROM watcher_events
                 ORDER BY ts ASC, rowid ASC LIMIT ?)""",
            (overflow,))
    conn.commit()
    return ts


def _snap_row(row):
    if row is None:
        return None
    return {"ts": row[0], "eligible_count": row[1],
            "evaluated_count": row[2], "active_setups": row[3],
            "new_setups_24h": row[4], "monitoring_coverage": row[5]}


_SNAP_COLS = ("ts, eligible_count, evaluated_count, active_setups,"
              " new_setups_24h, monitoring_coverage")


def write_snapshot(conn, eligible_count, evaluated_count, active_setups,
                   new_setups_24h, monitoring_coverage, ts=None):
    """Append one completed-sweep snapshot row. Returns the row ts."""
    ensure_journal(conn)
    ts = int(time.time()) if ts is None else int(ts)
    conn.execute(
        f"INSERT INTO dashboard_snapshot ({_SNAP_COLS})"
        " VALUES (?,?,?,?,?,?)",
        (ts, eligible_count, evaluated_count, active_setups,
         new_setups_24h, monitoring_coverage))
    conn.commit()
    return ts


def snapshot_pair(conn, now):
    """(latest, prior_or_None).

    latest is the most recent snapshot row (or None when empty). prior is
    the nearest snapshot at or before now-24h excluding the latest row;
    None when no snapshot covers that point (caller shows "comparison
    unavailable", never an inferred value).
    """
    ensure_journal(conn)
    now = int(now)
    latest = conn.execute(
        f"SELECT {_SNAP_COLS} FROM dashboard_snapshot"
        " ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()
    if latest is None:
        return (None, None)
    latest_d = _snap_row(latest)
    prior = conn.execute(
        f"SELECT {_SNAP_COLS} FROM dashboard_snapshot"
        " WHERE ts <= ? AND NOT (ts = ? AND rowid = ("
        "   SELECT rowid FROM dashboard_snapshot"
        "   ORDER BY ts DESC, rowid DESC LIMIT 1))"
        " ORDER BY ts DESC, rowid DESC LIMIT 1",
        (now - DAY_S, latest_d["ts"])).fetchone()
    return (latest_d, _snap_row(prior))
