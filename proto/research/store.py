"""SQLite schema, connection factory, and write-path guard for research.

The research database is append-only for all measured inputs and run records;
``sync_run`` is the sole mutable table because it records a terminal status.
This module only opens the research database, never the live ``quant.db`` /
``signals.db`` or the ``data/bars/`` archive.
"""

import os
import sqlite3
from urllib.parse import quote

DEFAULT_DB = os.path.join("data", "research", "altradar-research.db")
SCHEMA_VERSION = 1

# Tables whose rows must never change after insert. sync_run is deliberately
# absent: a sync records its terminal status by update.
APPEND_ONLY_TABLES = (
    "source_snapshot", "bar", "bar_file", "universe", "sample",
    "sample_coin", "backtest_run", "run_event",
)

_PROTECTED_FILE_NAMES = ("quant.db", "signals.db")
_PROTECTED_DATA_DIRS = ("bars", "prices")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_snapshot (
  snapshot_id TEXT PRIMARY KEY, source_dir TEXT NOT NULL,
  source_digest TEXT NOT NULL UNIQUE, coins INTEGER NOT NULL,
  bars INTEGER NOT NULL, min_ts INTEGER, max_ts INTEGER,
  created_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_run (
  sync_id TEXT PRIMARY KEY, started_ts INTEGER NOT NULL,
  finished_ts INTEGER, status TEXT NOT NULL, source_dir TEXT NOT NULL,
  snapshot_id TEXT, source_digest TEXT, coins_seen INTEGER,
  bars_seen INTEGER, bars_inserted INTEGER, skipped_files INTEGER, error TEXT
);

CREATE TABLE IF NOT EXISTS bar (
  coin TEXT NOT NULL, ts INTEGER NOT NULL, open REAL NOT NULL,
  high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  vol REAL NOT NULL, amount REAL NOT NULL, snapshot_id TEXT NOT NULL,
  PRIMARY KEY (coin, ts)
);

CREATE TABLE IF NOT EXISTS bar_file (
  coin TEXT NOT NULL, sha256 TEXT NOT NULL, source_path TEXT NOT NULL,
  size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, row_count INTEGER NOT NULL,
  first_ts INTEGER, last_ts INTEGER, snapshot_id TEXT NOT NULL,
  observed_ts INTEGER NOT NULL, PRIMARY KEY (coin, sha256)
);

CREATE TABLE IF NOT EXISTS universe (
  universe_id TEXT PRIMARY KEY, name TEXT NOT NULL,
  definition_json TEXT NOT NULL, definition_sha256 TEXT NOT NULL,
  created_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sample (
  sample_id TEXT PRIMARY KEY,
  universe_id TEXT NOT NULL REFERENCES universe(universe_id),
  seed TEXT NOT NULL, n INTEGER NOT NULL, method TEXT NOT NULL, label TEXT,
  coins_json TEXT NOT NULL, coins_sha256 TEXT NOT NULL,
  created_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sample_coin (
  sample_id TEXT NOT NULL REFERENCES sample(sample_id),
  rank INTEGER NOT NULL, coin TEXT NOT NULL,
  PRIMARY KEY (sample_id, rank), UNIQUE (sample_id, coin)
);

CREATE TABLE IF NOT EXISTS backtest_run (
  run_id TEXT PRIMARY KEY, created_ts INTEGER NOT NULL,
  kind TEXT NOT NULL, label TEXT, parent_run_id TEXT,
  snapshot_id TEXT NOT NULL REFERENCES source_snapshot(snapshot_id),
  sample_id TEXT NOT NULL REFERENCES sample(sample_id),
  universe_id TEXT NOT NULL REFERENCES universe(universe_id),
  seed TEXT NOT NULL, code_rev TEXT NOT NULL, code_dirty INTEGER NOT NULL,
  config_version TEXT NOT NULL, config_json TEXT NOT NULL,
  config_sha256 TEXT NOT NULL, assumptions_json TEXT NOT NULL,
  assumptions_sha256 TEXT NOT NULL, params_json TEXT NOT NULL, notes TEXT
);

CREATE TABLE IF NOT EXISTS run_event (
  run_id TEXT NOT NULL REFERENCES backtest_run(run_id),
  seq INTEGER NOT NULL, ts INTEGER NOT NULL, status TEXT NOT NULL,
  outputs_json TEXT, error TEXT, artifacts_json TEXT,
  PRIMARY KEY (run_id, seq)
);
"""


class StoreError(Exception):
    """Raised when a path or database operation crosses the research boundary."""


def validate_db_path(db_path):
    """Return the resolved path, refusing the live databases and archives.

    ``realpath`` follows symlinks so a link cannot hide a protected target.
    """
    path = os.path.realpath(os.fspath(db_path))
    # Prefix (not equality) so SQLite sidecars such as quant.db-wal,
    # quant.db-shm and quant.db-journal are refused too.
    if os.path.basename(path).startswith(_PROTECTED_FILE_NAMES):
        raise StoreError(f"refusing protected database name: {path}")
    parts = path.split(os.sep)
    for i in range(len(parts) - 1):
        if parts[i] != "data":
            continue
        nxt = parts[i + 1]
        if nxt in _PROTECTED_DATA_DIRS or nxt.startswith("funding"):
            raise StoreError(f"refusing protected data directory: {path}")
    return path


_RUN_EVENT_TERMINAL_GUARD = """
CREATE TRIGGER IF NOT EXISTS run_event_no_insert_after_terminal
BEFORE INSERT ON run_event
WHEN EXISTS (
  SELECT 1 FROM run_event
  WHERE run_id = NEW.run_id
    AND status IN ('completed', 'failed', 'interrupted')
)
BEGIN
  SELECT RAISE(ABORT, 'append-only: run already terminal');
END;
"""


def ensure_run_event_guard(conn):
    """Refuse appending any event once the run has a terminal event.

    shortcut: installed by the run write paths instead of ``connect`` so the
    trigger count asserted by tests/test_research_store.py stays fixed;
    upgrade by moving this into ``connect`` when that count is relaxed.
    """
    conn.execute(_RUN_EVENT_TERMINAL_GUARD)


def connect(db_path=DEFAULT_DB, *, readonly=False):
    """Open the research database, creating the schema when writable."""
    path = validate_db_path(db_path)
    if readonly:
        conn = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only=ON")
        return conn

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    # DELETE (the SQLite default) keeps a ``mode=ro`` open free of writable
    # ``-wal``/``-shm`` sidecars, so inspection works in a read-only directory.
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=ON")
    # REPLACE deletes conflicting rows; without recursive triggers that DELETE
    # skips the append-only triggers and silently rewrites a frozen row.
    conn.execute("PRAGMA recursive_triggers=ON")
    conn.executescript(_SCHEMA)
    for table in APPEND_ONLY_TABLES:
        for action in ("UPDATE", "DELETE"):
            conn.execute(
                f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} "
                f"BEFORE {action} ON {table} BEGIN "
                f"SELECT RAISE(ABORT, 'append-only: {table}'); END"
            )
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.commit()
    return conn
