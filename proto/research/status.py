"""Read-only operator view of the research database.

``db_status`` opens the database with ``mode=ro`` and issues no writes, so a
missing database raises instead of being created. It reports what the mirror
and registry contain - counts and identifiers - never a benchmark result:
this slice deliberately computes no strategy number.
"""

from . import runs, store

_STATUSES = ("started",) + runs.TERMINAL_STATUSES

_RECENT_RUNS = """
SELECT r.run_id, r.kind, r.label, r.created_ts, r.snapshot_id, r.sample_id,
       (SELECT e.status FROM run_event e WHERE e.run_id = r.run_id
        ORDER BY e.seq DESC LIMIT 1) AS status
FROM backtest_run r
ORDER BY r.created_ts DESC, r.run_id DESC
LIMIT 10
"""

_BENCHMARK_COMPLETED = """
SELECT COUNT(*) FROM backtest_run r
WHERE r.kind = 'benchmark'
  AND (SELECT e.status FROM run_event e WHERE e.run_id = r.run_id
       ORDER BY e.seq DESC LIMIT 1) = 'completed'
  AND NOT EXISTS (SELECT 1 FROM run_event e
                  WHERE e.run_id = r.run_id
                    AND e.status IN ('failed', 'interrupted'))
"""


def _rows(conn, sql):
    cur = conn.execute(sql)
    names = [column[0] for column in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def _one(conn, sql):
    rows = _rows(conn, sql)
    return rows[0] if rows else None


def db_status(db_path=store.DEFAULT_DB):
    """Return the inspection summary; opens read-only and never writes."""
    path = store.validate_db_path(db_path)
    conn = store.connect(path, readonly=True)
    try:
        schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
        coins, rows, min_ts, max_ts = conn.execute(
            "SELECT COUNT(DISTINCT coin), COUNT(*), MIN(ts), MAX(ts) FROM bar"
        ).fetchone()

        counts = {status: 0 for status in _STATUSES}
        for status, n in conn.execute(
            "SELECT (SELECT e.status FROM run_event e "
            "        WHERE e.run_id = r.run_id ORDER BY e.seq DESC LIMIT 1), "
            "       COUNT(*) "
            "FROM backtest_run r GROUP BY 1"
        ):
            # A run inserted outside the registry has no event; keep it visible
            # under a string key instead of a JSON null key.
            counts[status if status is not None else "unknown"] = n

        return {
            "schema_version": schema_version,
            "db": path,
            "bars": {
                "coins": coins,
                "rows": rows,
                "min_ts": min_ts,
                "max_ts": max_ts,
            },
            "latest_snapshot": _one(
                conn,
                "SELECT * FROM source_snapshot "
                "ORDER BY created_ts DESC, snapshot_id DESC LIMIT 1"),
            "latest_sync": _one(
                conn,
                "SELECT * FROM sync_run "
                "ORDER BY started_ts DESC, sync_id DESC LIMIT 1"),
            "runs": counts,
            "benchmark_completed": conn.execute(
                _BENCHMARK_COMPLETED).fetchone()[0],
            "recent_runs": _rows(conn, _RECENT_RUNS),
        }
    finally:
        conn.close()
