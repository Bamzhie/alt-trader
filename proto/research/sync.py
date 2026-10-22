"""Read-only, idempotent mirror of the ``<COIN>.csv.gz`` bars archive.

The source directory is only ever opened for reading: bytes are hashed in one
pass, then re-read for parsing, and a file whose ``(size, mtime_ns)`` moves
under us is retried once and then refused outright.  A parsed file is never
partially installed: bars, provenance and the snapshot land in one
``BEGIN IMMEDIATE`` transaction.

Duplicate timestamps inside a source file keep the first row (the same rule as
``proto.quant.bars.load_coin`` and the collector); a conflicting duplicate
aborts instead of overwriting.  Rows for a source file that later disappears
are retained on purpose - the mirror is append-only evidence, not a replica -
so a snapshot digest describes *this* source state, not the mirror contents.
"""

import csv
import gzip
import hashlib
import io
import math
import os
import re
import sqlite3
import time
import uuid
import zlib

from proto.quant import bars as quant_bars

from . import store

HEADER = ["ts", "o", "h", "l", "c", "vol", "amount"]
FILE_RE = re.compile(r"^[A-Z0-9]+\.csv\.gz$")

_TEMP_BAR = """CREATE TEMP TABLE IF NOT EXISTS incoming_bar (
  coin TEXT NOT NULL, ts INTEGER NOT NULL, open REAL NOT NULL,
  high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  vol REAL NOT NULL, amount REAL NOT NULL)"""

_CONFLICT = """SELECT COUNT(*) FROM incoming_bar i JOIN bar b
  ON b.coin = i.coin AND b.ts = i.ts
  WHERE i.open != b.open OR i.high != b.high OR i.low != b.low
     OR i.close != b.close OR i.vol != b.vol OR i.amount != b.amount"""

_INSERT_BARS = """INSERT INTO bar
  (coin, ts, "open", high, low, "close", vol, amount, snapshot_id)
  SELECT coin, ts, "open", high, low, "close", vol, amount, ?
  FROM incoming_bar i
  WHERE NOT EXISTS (SELECT 1 FROM bar b WHERE b.coin = i.coin AND b.ts = i.ts)"""

_INSERT_SNAPSHOT = """INSERT INTO source_snapshot
  (snapshot_id, source_dir, source_digest, coins, bars, min_ts, max_ts,
   created_ts) VALUES (?,?,?,?,?,?,?,?)"""

_INSERT_RUN = """INSERT INTO sync_run
  (sync_id, started_ts, finished_ts, status, source_dir, snapshot_id,
   source_digest, coins_seen, bars_seen, bars_inserted, skipped_files, error)
  VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""

_RUN_DONE = """UPDATE sync_run SET status = 'completed', finished_ts = ?,
  snapshot_id = ?, source_digest = ?, coins_seen = ?, bars_seen = ?,
  bars_inserted = ?, skipped_files = ?, error = NULL WHERE sync_id = ?"""

_RUN_FAILED = """UPDATE sync_run SET status = 'failed', finished_ts = ?,
  error = ? WHERE sync_id = ?"""


class SyncError(Exception):
    """Raised when the source cannot be mirrored safely and completely."""


class _Unstable(Exception):
    """A read raced a source write; retried once before SyncError."""


def _now():
    return int(time.time())


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _reason(exc):
    """Error text without repeating the path already prefixed by the caller."""
    if isinstance(exc, OSError) and exc.strerror:
        return exc.strerror
    return str(exc) or exc.__class__.__name__


def _stable_read(path):
    """Read a file whose stat must not move across the read."""
    before = os.stat(path)
    with open(path, "rb") as f:
        raw = f.read()
    after = os.stat(path)
    before_key = (before.st_size, before.st_mtime_ns)
    if before_key != (after.st_size, after.st_mtime_ns) or len(raw) != after.st_size:
        raise _Unstable("changed while reading")
    return raw, after.st_size, after.st_mtime_ns


def _read_file(path):
    """_stable_read with the single retry the contract allows."""
    error = None
    for _ in range(2):
        try:
            return _stable_read(path)
        except (OSError, _Unstable) as exc:
            error = exc
    raise SyncError(f"{path}: {_reason(error)}")


def _parse(coin, raw):
    """Parse one gzip byte string into [(ts, o, h, l, c, vol, amount)].

    gzip.decompress handles concatenated members; its OSError/EOFError/zlib
    errors are left for the caller to retry, while content problems raise
    SyncError.
    """
    data = gzip.decompress(raw)
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SyncError(f"{coin}: not UTF-8: {exc}") from exc

    reader = csv.reader(io.StringIO(content, newline=""))
    header = next(reader, None)
    if header != HEADER:
        raise SyncError(f"{coin}: expected header {','.join(HEADER)}")

    rows = {}
    for lineno, row in enumerate(reader, start=2):
        if not row:
            continue
        where = f"{coin}: line {lineno}"
        if len(row) != 7:
            raise SyncError(f"{where}: expected 7 fields, got {len(row)}")
        try:
            ts = int(row[0])
        except ValueError as exc:
            raise SyncError(f"{where}: ts is not an integer") from exc
        if ts % quant_bars.BAR_S:
            raise SyncError(f"{where}: ts {ts} is not a multiple of {quant_bars.BAR_S}")
        try:
            o, h, l, c, vol, amount = (float(x) for x in row[1:])
        except ValueError as exc:
            raise SyncError(f"{where}: non-numeric value") from exc
        if not all(math.isfinite(x) for x in (o, h, l, c, vol, amount)):
            raise SyncError(f"{where}: non-finite value")
        if not quant_bars.valid_bar(o, h, l, c):
            raise SyncError(f"{where}: invalid OHLC {o},{h},{l},{c}")
        if vol < 0 or amount < 0:
            raise SyncError(f"{where}: negative volume or amount")

        values = (o, h, l, c, vol, amount)
        if ts in rows:
            if rows[ts] != values:
                raise SyncError(f"{where}: conflicting duplicate ts {ts}")
            continue  # first wins, identical duplicate
        rows[ts] = values

    if not rows:
        raise SyncError(f"{coin}: no data rows")
    return [(ts, *rows[ts]) for ts in rows]


def _load_rows(coin, path, expected_sha):
    """Re-read + parse, retrying once on a gzip error, read error or change."""
    error = None
    for _ in range(2):
        try:
            raw, _, _ = _stable_read(path)
        except (OSError, _Unstable) as exc:
            error = exc
            continue
        if _sha256(raw) != expected_sha:
            error = "changed while syncing"
            continue
        try:
            return _parse(coin, raw)
        except (OSError, EOFError, zlib.error) as exc:  # truncated/corrupt gzip
            error = exc
            continue
    raise SyncError(f"{path}: {_reason(error)}")


def _enumerate(source_dir):
    try:
        names = sorted(os.listdir(source_dir))
    except OSError as exc:
        raise SyncError(f"{source_dir}: {_reason(exc)}") from exc
    files = []
    for name in names:
        path = os.path.join(source_dir, name)
        if not os.path.isfile(path) or not name.endswith(".csv.gz"):
            continue
        if not FILE_RE.fullmatch(name):
            raise SyncError(f"bad bar filename: {name}")
        files.append((name[: -len(".csv.gz")], path))
    if not files:
        raise SyncError(f"no <COIN>.csv.gz files in {source_dir}")
    return files


def _scan(source_dir, files):
    """Pass 1: (coin, relpath, sha256, size, mtime_ns) per source file."""
    records = []
    for coin, path in files:
        raw, size, mtime = _read_file(path)
        records.append((coin, os.path.relpath(path, source_dir), _sha256(raw),
                        size, mtime))
    return records


def _digest(records):
    text = "\n".join(f"{coin}:{sha}" for coin, _, sha, _, _ in sorted(records))
    return _sha256(text.encode())


def _inside(path, root):
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _import(conn, source_dir, records, snapshot_id, now):
    """Install new files; return (bars_inserted, bars_seen, skipped_files)."""
    conn.execute(_TEMP_BAR)
    inserted = seen = skipped = 0
    for coin, relpath, sha, size, mtime in records:
        reused = conn.execute(
            "SELECT row_count FROM bar_file WHERE coin = ? AND sha256 = ?",
            (coin, sha)).fetchone()
        if reused is not None:
            skipped += 1
            seen += reused[0]
            continue

        rows = _load_rows(coin, os.path.join(source_dir, relpath), sha)
        seen += len(rows)
        conn.execute("DELETE FROM incoming_bar")
        conn.executemany(
            "INSERT INTO incoming_bar VALUES (?,?,?,?,?,?,?,?)",
            [(coin, *row) for row in rows])

        if conn.execute(_CONFLICT).fetchone()[0]:
            ts = conn.execute(
                "SELECT i.ts FROM incoming_bar i JOIN bar b "
                "ON b.coin = i.coin AND b.ts = i.ts "
                "WHERE i.open != b.open OR i.high != b.high OR i.low != b.low "
                "OR i.close != b.close OR i.vol != b.vol OR i.amount != b.amount "
                "LIMIT 1").fetchone()[0]
            raise SyncError(f"{coin}: conflicting values for mirrored ts {ts}")

        inserted += conn.execute(_INSERT_BARS, (snapshot_id,)).rowcount
        first_ts = min(row[0] for row in rows)
        last_ts = max(row[0] for row in rows)
        conn.execute(
            "INSERT INTO bar_file VALUES (?,?,?,?,?,?,?,?,?,?)",
            (coin, sha, relpath, size, mtime, len(rows), first_ts, last_ts,
             snapshot_id, now))
    return inserted, seen, skipped


def sync_bars(source_dir, db_path=store.DEFAULT_DB):
    """Mirror every ``<COIN>.csv.gz`` under ``source_dir`` into the research DB."""
    source = os.path.realpath(os.fspath(source_dir))
    try:
        db = store.validate_db_path(db_path)
    except store.StoreError as exc:
        raise SyncError(str(exc)) from exc
    if _inside(db, source):
        raise SyncError(f"refusing database inside the source dir: {db}")
    # The engine's only historical input is data/bars; a typo must not ingest
    # another archive into an append-only mirror.
    parts = source.split(os.sep)
    for i, part in enumerate(parts):
        if part == "data" and (i + 1 == len(parts) or parts[i + 1] != "bars"):
            raise SyncError(f"refusing non-bars source: {source}")

    conn = store.connect(db)
    conn.isolation_level = None  # explicit transactions below
    try:
        return _sync(conn, source)
    finally:
        conn.close()


def _sync(conn, source):
    """Record the attempt first, so every failure after connect is auditable."""
    sync_id = "sync-" + uuid.uuid4().hex
    started = _now()
    conn.execute(_INSERT_RUN, (sync_id, started, None, "started", source,
                               None, None, None, None, None, None, None))
    conn.commit()

    try:
        if not os.path.isdir(source):
            raise SyncError(f"source dir not found: {source}")
        records = _scan(source, _enumerate(source))
        digest = _digest(records)
        coins = len(records)

        known = conn.execute(
            "SELECT bars FROM source_snapshot WHERE source_digest = ?",
            (digest,)).fetchone()
        if known is not None:
            seen = skipped = 0
            for coin, _, sha, _, _ in records:
                row = conn.execute(
                    "SELECT row_count FROM bar_file WHERE coin = ? AND sha256 = ?",
                    (coin, sha)).fetchone()
                if row is None:
                    raise SyncError(f"missing provenance for {coin} {sha}")
                skipped += 1
                seen += row[0]
            finished = _now()
            conn.execute(_RUN_DONE, (finished, digest, digest, coins, seen, 0,
                                     skipped, sync_id))
            conn.commit()
            result = _result(sync_id, "completed", digest, digest, coins,
                             known[0], 0, skipped, started, finished)
        else:
            conn.execute("BEGIN IMMEDIATE")
            inserted, seen, skipped = _import(conn, source, records, digest, _now())
            total, min_ts, max_ts = conn.execute(
                "SELECT COUNT(*), MIN(ts), MAX(ts) FROM bar").fetchone()
            conn.execute(_INSERT_SNAPSHOT, (digest, source, digest, coins, total,
                                            min_ts, max_ts, _now()))
            finished = _now()
            conn.execute(_RUN_DONE, (finished, digest, digest, coins, seen,
                                     inserted, skipped, sync_id))
            conn.commit()
            result = _result(sync_id, "completed", digest, digest, coins, total,
                             inserted, skipped, started, finished)
    except BaseException as exc:
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        # Record the attempt; a broken DB must not hide the real error.
        try:
            conn.execute(_RUN_FAILED,
                         (_now(), str(exc) or exc.__class__.__name__, sync_id))
            conn.commit()
        except sqlite3.Error:
            pass
        raise
    return result


def _result(sync_id, status, snapshot_id, digest, coins, bars, inserted,
            skipped, started, finished):
    return {
        "sync_id": sync_id,
        "status": status,
        "snapshot_id": snapshot_id,
        "source_digest": digest,
        "coins": coins,
        "bars": bars,
        "bars_inserted": inserted,
        "skipped_files": skipped,
        "started_ts": started,
        "finished_ts": finished,
    }
