"""Research store schema and safety boundary (Task 1).

Self-checking script: python3 tests/test_research_store.py
Covers: schema creation + user_version, read-only connect on a missing path,
the protected-path guard, and append-only triggers on every frozen table.
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.research import store

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TABLES = {
    "source_snapshot", "snapshot_file", "sync_run", "bar", "bar_file",
    "universe", "sample", "sample_coin", "backtest_run", "run_event",
}
APPEND_ONLY = (
    "bar", "bar_file", "source_snapshot", "snapshot_file", "universe",
    "sample", "sample_coin", "backtest_run", "run_event",
)

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def _append_only_error(conn, sql):
    """Run sql; return the error text if it was refused as append-only."""
    try:
        conn.execute(sql)
    except sqlite3.IntegrityError as exc:
        return str(exc) if "append-only" in str(exc) else f"wrong error: {exc}"
    return "statement was allowed"


tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "research.db")

print("=== defaults ===")
check("DEFAULT_DB path", store.DEFAULT_DB == os.path.join(
    "data", "research", "altradar-research.db"), store.DEFAULT_DB)
check("SCHEMA_VERSION == 2", store.SCHEMA_VERSION == 2, str(store.SCHEMA_VERSION))

print("\n=== connect creates schema ===")
conn = store.connect(db_path)
names = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type = 'table'")}
check("all tables created", TABLES <= names, str(TABLES - names))
version = conn.execute("PRAGMA user_version").fetchone()[0]
check("user_version == 2", version == 2, str(version))
check("foreign_keys on", conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1)
check("journal_mode delete",
      conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete")

print("\n=== seed one row per append-only table ===")
snap, uni, smp, run = "snap-1", "uni-1", "smp-1", "run-1"
conn.execute("INSERT INTO source_snapshot VALUES (?,?,?,?,?,?,?,?,?)",
             (snap, 1, "data/bars", "digest-1", 1, 2, 100, 200, 1))
conn.execute("INSERT INTO snapshot_file VALUES (?,?,?,?,?,?,?,?,?)",
             (snap, "BTC", "sha-1", "BTC.csv.gz", 10, 20, 2, 100, 200))
conn.execute("INSERT INTO bar VALUES (?,?,?,?,?,?,?,?,?)",
             ("BTC", 300, 1.0, 2.0, 0.5, 1.5, 10.0, 15.0, snap))
conn.execute("INSERT INTO bar_file VALUES (?,?,?,?,?,?,?,?,?,?)",
             ("BTC", "sha-1", "BTC.csv.gz", 10, 20, 2, 100, 200, snap, 1))
conn.execute("INSERT INTO universe VALUES (?,?,?,?,?)",
             (uni, "test", "{}", "def-sha", 1))
conn.execute("INSERT INTO sample VALUES (?,?,?,?,?,?,?,?,?)",
             (smp, uni, "seed", 1, "sha256-rank-v1", None, '["BTC"]', "c-sha", 1))
conn.execute("INSERT INTO sample_coin VALUES (?,?,?)", (smp, 1, "BTC"))
conn.execute("INSERT INTO backtest_run VALUES (" + ",".join("?" * 18) + ")",
             (run, 1, "benchmark", None, None, snap, smp, uni, "seed",
              "rev", 0, "v1", "{}", "cfg-sha", "{}", "asm-sha", "{}", None))
conn.execute("INSERT INTO run_event VALUES (?,?,?,?,?,?,?)",
             (run, 1, 1, "started", None, None, None))
conn.commit()

print("\n=== append-only triggers ===")
for table in APPEND_ONLY:
    upd = _append_only_error(conn, f"UPDATE {table} SET rowid = rowid")
    check(f"UPDATE {table} refused", "append-only" in upd, upd)
    dele = _append_only_error(conn, f"DELETE FROM {table}")
    check(f"DELETE {table} refused", "append-only" in dele, dele)

print("\n=== INSERT OR REPLACE cannot bypass triggers ===")
repl = _append_only_error(
    conn,
    "INSERT OR REPLACE INTO bar VALUES "
    "('BTC', 300, 9, 9, 9, 9, 9, 9, 'snap-1')",
)
check("INSERT OR REPLACE bar refused", "append-only" in repl, repl)

print("\n=== sync_run is mutable (terminal status) ===")
conn.execute("INSERT INTO sync_run (sync_id, started_ts, status, source_dir) "
             "VALUES ('sync-1', 1, 'started', 'data/bars')")
conn.execute("UPDATE sync_run SET status = 'completed', finished_ts = 2 "
             "WHERE sync_id = 'sync-1'")
check("sync_run update allowed",
      conn.execute("SELECT status, finished_ts FROM sync_run").fetchone()
      == ("completed", 2))
conn.close()

print("\n=== read-only connection ===")
before_files = set(os.listdir(tmp))
ro = store.connect(db_path, readonly=True)
check("readonly query_only",
      ro.execute("PRAGMA query_only").fetchone()[0] == 1)
check("readonly can read", ro.execute("SELECT COUNT(*) FROM bar").fetchone()[0] == 1)
try:
    ro.execute("INSERT INTO bar VALUES ('ETH', 300, 1, 1, 1, 1, 1, 1, 'x')")
    check("readonly write refused", False, "write was allowed")
except sqlite3.OperationalError as exc:
    check("readonly write refused",
          "readonly" in str(exc).lower(), str(exc))
except sqlite3.Error as exc:
    check("readonly write refused", False, f"wrong error: {exc}")
ro.close()
check("readonly creates no sidecars", set(os.listdir(tmp)) == before_files,
      f"{before_files} -> {set(os.listdir(tmp))}")

missing = os.path.join(tmp, "nope.db")
try:
    store.connect(missing, readonly=True)
    check("readonly missing path raises", False, "no exception")
except sqlite3.OperationalError as exc:
    check("readonly missing path raises",
          "unable to open" in str(exc).lower(), str(exc))
except sqlite3.Error as exc:
    check("readonly missing path raises", False, f"wrong error: {exc}")
check("readonly missing path not created", not os.path.exists(missing))

print("\n=== validate_db_path ===")
check("returns absolute path",
      store.validate_db_path("rel.db") == os.path.abspath("rel.db"))
PROTECTED = (
    "quant.db", "signals.db",
    "quant.db-wal", "quant.db-shm", "quant.db-journal",
    "signals.db-wal", "signals.db-shm", "signals.db-journal",
)
for rel in PROTECTED + (os.path.join(REPO, "data", "bars", "x.db"),):
    try:
        store.validate_db_path(os.path.join(tmp, rel) if not os.path.isabs(rel) else rel)
        check(f"refuse {rel}", False, "allowed")
    except store.StoreError:
        check(f"refuse {rel}", True)
check("allows normal path",
      store.validate_db_path(os.path.join(tmp, "ok.db")).endswith("ok.db"))

print("\n=== symlink cannot escape the protected-dir guard ===")
bars_dir = os.path.join(tmp, "real", "data", "bars")
os.makedirs(bars_dir)
bars_link = os.path.join(tmp, "bars_link")
os.symlink(bars_dir, bars_link)
try:
    store.validate_db_path(os.path.join(bars_link, "x.db"))
    check("refuse symlinked data/bars path", False, "allowed")
except store.StoreError:
    check("refuse symlinked data/bars path", True)

print("\n=== writable connect refuses protected paths ===")
for name in PROTECTED:
    protected = os.path.join(tmp, name)
    try:
        store.connect(protected)
        check(f"connect refuses {name}", False, "allowed")
    except store.StoreError:
        check(f"connect refuses {name}", True)
    check(f"connect did not create {name}", not os.path.exists(protected))

print("\n=== a different schema version is refused, not migrated ===")
old = os.path.join(tmp, "old.db")
raw = sqlite3.connect(old)
raw.execute("PRAGMA user_version=1")
raw.execute("CREATE TABLE marker (x)")
raw.commit()
raw.close()
try:
    store.connect(old)
    check("refuse schema v1", False, "allowed")
except store.StoreError:
    check("refuse schema v1", True)
raw = sqlite3.connect(old)
check("old database not migrated",
      raw.execute("PRAGMA user_version").fetchone()[0] == 1)
raw.close()
try:
    store.connect(old, readonly=True)
    check("read-only refuses schema v1", False, "allowed")
except store.StoreError:
    check("read-only refuses schema v1", True)

print("\n=== second writable connect is idempotent ===")
conn2 = store.connect(db_path)
names2 = {r[0] for r in conn2.execute(
    "SELECT name FROM sqlite_master WHERE type = 'table'")}
check("same tables after reconnect", TABLES <= names2, str(TABLES - names2))
check("user_version still 2",
      conn2.execute("PRAGMA user_version").fetchone()[0] == 2)
check("existing row intact",
      conn2.execute("SELECT COUNT(*) FROM bar").fetchone()[0] == 1)
triggers = {r[0] for r in conn2.execute(
    "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
check("no duplicate triggers",
      len(triggers) == len(APPEND_ONLY) * 2, str(sorted(triggers)))
conn2.close()

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
