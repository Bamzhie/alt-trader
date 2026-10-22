"""Read-only bars mirror sync (Task 2).

Self-checking script: python3 tests/test_research_sync.py
Covers: fixture import == proto.quant.bars.load_coin, provenance and source
immutability, idempotent re-sync, conflicting duplicates aborting without
touching prior rows, corrupt gzip recording a failed run, multi-member gzip,
validation, and retaining bars for a disappeared source file.
"""

import gzip
import hashlib
import os
import re
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.research import store
from proto.research.sync import SyncError, sync_bars
from proto.quant.bars import load_coin

HEADER = "ts,o,h,l,c,vol,amount\n"
RESULT_KEYS = {
    "sync_id", "status", "snapshot_id", "source_digest", "coins", "bars",
    "bars_inserted", "skipped_files", "started_ts", "finished_ts",
}
FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def text(rows):
    return "".join(f"{t},{o},{h},{l},{c},{v},{a}\n" for t, o, h, l, c, v, a in rows)


def bars(base, n=3, t0=300):
    out = []
    for i in range(n):
        o = float(base + i)
        out.append((t0 + 300 * i, o, o + 1.0, o - 1.0, o + 0.5,
                    10.0 + i, (10.0 + i) * o))
    return out


def write_gz(path, groups, mtime=0):
    """Write concatenated gzip members; only the first carries the header."""
    raw = b""
    for i, rows in enumerate(groups):
        raw += gzip.compress(((HEADER if i == 0 else "") + text(rows)).encode(),
                             mtime=mtime)
    with open(path, "wb") as f:
        f.write(raw)
    return raw


def write_raw(path, body, mtime=0):
    raw = gzip.compress(body.encode(), mtime=mtime)
    with open(path, "wb") as f:
        f.write(raw)
    return raw


def state(path):
    st = os.stat(path)
    with open(path, "rb") as f:
        raw = f.read()
    return (hashlib.sha256(raw).hexdigest(), st.st_size, st.st_mtime_ns)


def file_sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def source_digest(src):
    parts = []
    for name in sorted(os.listdir(src)):
        if re.fullmatch(r"[A-Z0-9]+\.csv\.gz", name):
            parts.append(f"{name[:-7]}:{file_sha(os.path.join(src, name))}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def query(db, sql, args=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def mirrored(db, coin):
    return query(
        db,
        "SELECT ts, open, high, low, close, vol, amount FROM bar "
        "WHERE coin = ? ORDER BY ts", (coin,))


def canonical(src, coin):
    b = load_coin(src, coin)
    if b is None:
        return []
    return list(zip(b["ts"], b["o"], b["h"], b["l"], b["c"], b["vol"], b["amt"]))


def raises_sync(src, db):
    try:
        sync_bars(src, db)
    except SyncError as exc:
        return str(exc)
    return None


def new_dir(root, name):
    path = os.path.join(root, name)
    os.makedirs(path)
    return path


tmp = tempfile.mkdtemp()

# --- main fixture: two coins, unsorted rows with an in-file duplicate -------
src = new_dir(tmp, "source")
btc_path = os.path.join(src, "BTC.csv.gz")
eth_path = os.path.join(src, "ETH.csv.gz")
btc_rows = bars(100.0)
write_gz(btc_path, [[btc_rows[0], btc_rows[2], btc_rows[1]]])
write_gz(eth_path, [bars(50.0)])
before = {p: state(p) for p in (btc_path, eth_path)}
expected_digest = source_digest(src)
db = os.path.join(tmp, "db1", "research.db")

print("=== first sync ===")
result = sync_bars(src, db)
check("result keys", set(result) == RESULT_KEYS, str(set(result) ^ RESULT_KEYS))
check("status completed", result["status"] == "completed", result["status"])
check("coins == 2", result["coins"] == 2, str(result["coins"]))
check("bars == 6", result["bars"] == 6, str(result["bars"]))
check("bars_inserted == 6", result["bars_inserted"] == 6,
      str(result["bars_inserted"]))
check("skipped_files == 0", result["skipped_files"] == 0,
      str(result["skipped_files"]))
check("source_digest matches", result["source_digest"] == expected_digest,
      f"{result['source_digest']} != {expected_digest}")
check("snapshot_id == source_digest",
      result["snapshot_id"] == expected_digest, str(result["snapshot_id"]))
check("started/finished ordered",
      result["started_ts"] <= result["finished_ts"],
      f"{result['started_ts']} {result['finished_ts']}")

print("\n=== mirror equals canonical load_coin ===")
for coin in ("BTC", "ETH"):
    got, want = mirrored(db, coin), canonical(src, coin)
    check(f"{coin} rows == load_coin", got == want, f"{got} != {want}")

print("\n=== snapshot row ===")
snap = query(db, "SELECT snapshot_id, source_dir, source_digest, coins, bars, "
                 "min_ts, max_ts FROM source_snapshot")
check("one snapshot", len(snap) == 1, str(snap))
check("snapshot fields",
      len(snap) == 1 and snap[0][0] == expected_digest
      and snap[0][2] == expected_digest and snap[0][3] == 2
      and snap[0][4] == 6 and snap[0][5] == 300 and snap[0][6] == 900, str(snap))

print("\n=== bar_file provenance ===")
prov = query(db, "SELECT coin, sha256, source_path, size, mtime_ns, row_count, "
                 "first_ts, last_ts, snapshot_id, observed_ts FROM bar_file "
                 "ORDER BY coin")
check("two provenance rows", len(prov) == 2, str(prov))
for row in prov:
    coin = row[0]
    path = os.path.join(src, f"{coin}.csv.gz")
    st = os.stat(path)
    check(f"{coin} provenance",
          row[1] == file_sha(path) and row[2] == f"{coin}.csv.gz"
          and row[3] == st.st_size and row[4] == st.st_mtime_ns
          and row[5] == 3 and row[6] == 300 and row[7] == 900
          and row[8] == expected_digest and row[9] >= result["started_ts"],
          str(row))

print("\n=== source is read-only / byte-identical ===")
check("source state unchanged", before == {p: state(p) for p in before}, "changed")
check("no stray files in source", sorted(os.listdir(src))
      == ["BTC.csv.gz", "ETH.csv.gz"], str(os.listdir(src)))

print("\n=== second sync is idempotent ===")
again = sync_bars(src, db)
check("same snapshot_id", again["snapshot_id"] == result["snapshot_id"],
      str(again["snapshot_id"]))
check("bars_inserted == 0", again["bars_inserted"] == 0,
      str(again["bars_inserted"]))
check("skipped_files == 2", again["skipped_files"] == 2,
      str(again["skipped_files"]))
check("bar count unchanged",
      query(db, "SELECT COUNT(*) FROM bar")[0][0] == 6)
check("two sync_run rows", query(db, "SELECT COUNT(*) FROM sync_run")[0][0] == 2)
check("both completed",
      query(db, "SELECT COUNT(*) FROM sync_run WHERE status = 'completed'")[0][0]
      == 2)

print("\n=== conflicting duplicate aborts, prior rows intact ===")
with open(btc_path, "rb") as f:
    raw = f.read()
dup = btc_rows[0]
raw += gzip.compress(f"{dup[0]},{dup[1]},{dup[2]},{dup[3]},999.0,{dup[5]},{dup[6]}\n"
                     .encode(), mtime=0)
with open(btc_path, "wb") as f:
    f.write(raw)
bad_digest = source_digest(src)
msg = raises_sync(src, db)
check("conflicting duplicate raises", msg is not None, "no exception")
check("error names the coin", msg is not None and "BTC" in msg, str(msg))
check("prior BTC row unchanged",
      query(db, "SELECT close FROM bar WHERE coin = 'BTC' AND ts = 300")
      == [(100.5,)],
      str(query(db, "SELECT close FROM bar WHERE coin='BTC' AND ts=300")))
check("bar count unchanged", query(db, "SELECT COUNT(*) FROM bar")[0][0] == 6)
check("bar_file count unchanged",
      query(db, "SELECT COUNT(*) FROM bar_file")[0][0] == 2)
check("failed sync recorded",
      query(db, "SELECT COUNT(*) FROM sync_run WHERE status = 'failed'")[0][0]
      == 1)
check("failed sync has error text",
      bool(query(db, "SELECT error FROM sync_run WHERE status = 'failed'")[0][0]))
check("failed sync has no snapshot",
      query(db, "SELECT snapshot_id FROM sync_run WHERE status = 'failed'")
      == [(None,)])
check("no snapshot for bad digest",
      query(db, "SELECT COUNT(*) FROM source_snapshot WHERE source_digest = ?",
            (bad_digest,))[0][0] == 0)
check("only the original snapshot remains",
      query(db, "SELECT COUNT(*) FROM source_snapshot")[0][0] == 1)

print("\n=== corrupt gzip records a failed run, inserts nothing ===")
corrupt_src = new_dir(tmp, "corrupt")
with open(os.path.join(corrupt_src, "AAA.csv.gz"), "wb") as f:
    f.write(b"this is definitely not a gzip stream")
corrupt_db = os.path.join(tmp, "db2", "research.db")
msg = raises_sync(corrupt_src, corrupt_db)
check("corrupt gzip raises SyncError", msg is not None, "no exception")
check("no bars inserted", query(corrupt_db, "SELECT COUNT(*) FROM bar")[0][0] == 0)
check("no snapshot", query(corrupt_db, "SELECT COUNT(*) FROM source_snapshot")[0][0] == 0)
check("failed sync recorded",
      query(corrupt_db,
            "SELECT COUNT(*) FROM sync_run WHERE status = 'failed'")[0][0] == 1)

print("\n=== bit-flipped deflate bytes also raise SyncError ===")
good = gzip.compress((HEADER + text(bars(10.0))).encode(), mtime=0)
flipped = bytearray(good)
flipped[12] ^= 0xFF
with open(os.path.join(corrupt_src, "BBB.csv.gz"), "wb") as f:
    f.write(bytes(flipped))
msg = raises_sync(corrupt_src, corrupt_db)
check("bit-flipped gzip raises SyncError", msg is not None, "no exception")
check("still no bars", query(corrupt_db, "SELECT COUNT(*) FROM bar")[0][0] == 0)
check("second failed run recorded",
      query(corrupt_db,
            "SELECT COUNT(*) FROM sync_run WHERE status = 'failed'")[0][0] == 2)

print("\n=== multi-member gzip parses all members, identical dup skipped ===")
multi = new_dir(tmp, "multi")
mrows = bars(70.0, n=4)
write_gz(os.path.join(multi, "BTC.csv.gz"),
         [[mrows[0], mrows[1]], [mrows[0], mrows[2], mrows[3]]])
multi_db = os.path.join(tmp, "db3", "research.db")
mres = sync_bars(multi, multi_db)
check("all unique members parsed", mres["bars"] == 4, str(mres["bars"]))
check("multi mirror == load_coin", mirrored(multi_db, "BTC") == canonical(multi, "BTC"))
check("row_count deduplicated",
      query(multi_db, "SELECT row_count FROM bar_file")[0][0] == 4,
      str(query(multi_db, "SELECT row_count FROM bar_file")))

print("\n=== unchanged files reuse provenance; a new file imports ===")
gone = new_dir(tmp, "disappeared")
write_gz(os.path.join(gone, "BTC.csv.gz"), [bars(100.0)])
write_gz(os.path.join(gone, "ETH.csv.gz"), [bars(50.0)])
gone_db = os.path.join(tmp, "db4", "research.db")
first = sync_bars(gone, gone_db)
write_gz(os.path.join(gone, "SOL.csv.gz"), [bars(200.0)])
added = sync_bars(gone, gone_db)
check("new snapshot for the added file",
      added["snapshot_id"] != first["snapshot_id"], str(added["snapshot_id"]))
check("unchanged files skipped", added["skipped_files"] == 2,
      str(added["skipped_files"]))
check("only the new file's bars inserted", added["bars_inserted"] == 3,
      str(added["bars_inserted"]))
check("provenance rows for all three files",
      query(gone_db, "SELECT COUNT(*) FROM bar_file")[0][0] == 3)
check("new file mirror == load_coin",
      mirrored(gone_db, "SOL") == canonical(gone, "SOL"))

print("\n=== a disappeared source file is retained ===")
os.remove(os.path.join(gone, "ETH.csv.gz"))
second = sync_bars(gone, gone_db)
check("new snapshot recorded",
      second["snapshot_id"] != added["snapshot_id"]
      and second["snapshot_id"] == source_digest(gone),
      f"{second['snapshot_id']} vs {added['snapshot_id']}")
check("three snapshots", query(gone_db,
      "SELECT COUNT(*) FROM source_snapshot")[0][0] == 3)
check("disappeared coin bars retained",
      query(gone_db, "SELECT COUNT(*) FROM bar WHERE coin = 'ETH'")[0][0] == 3)
check("surviving coin unchanged",
      mirrored(gone_db, "BTC") == canonical(gone, "BTC"))
check("no rows inserted for a removal", second["bars_inserted"] == 0,
      str(second["bars_inserted"]))
check("coins counts only present files", second["coins"] == 2, str(second["coins"]))

print("\n=== parser validation ===")
val_db = os.path.join(tmp, "db5", "research.db")
cases = {
    "wrong header": "ts,open,high,low,close,vol,amount\n300,1,1,1,1,1,1\n",
    "ts not multiple of 300": HEADER + "301,1,1,1,1,1,1\n",
    "short row": HEADER + "300,1,1,1,1,1\n",
    "ts not integer": HEADER + "300.5,1,1,1,1,1,1\n",
    "non-finite": HEADER + "300,nan,2,0.5,1,1,1\n",
    "inconsistent ohlc": HEADER + "300,10,5,20,10,1,1\n",
    "negative volume": HEADER + "300,10,11,9,10,-1,1\n",
    "empty file": "",
}
for i, (name, body) in enumerate(cases.items()):
    case_dir = new_dir(tmp, f"val{i}")
    write_raw(os.path.join(case_dir, "AAA.csv.gz"), body)
    check(f"reject {name}", raises_sync(case_dir, val_db) is not None, "allowed")
check("no bars from invalid sources",
      query(val_db, "SELECT COUNT(*) FROM bar")[0][0] == 0)
check("no snapshot from invalid sources",
      query(val_db, "SELECT COUNT(*) FROM source_snapshot")[0][0] == 0)
check("one failed run per case",
      query(val_db, "SELECT COUNT(*) FROM sync_run WHERE status = 'failed'")[0][0]
      == len(cases))

print("\n=== source enumeration guard ===")
empty = new_dir(tmp, "empty")
check("empty source raises", raises_sync(empty, os.path.join(tmp, "db6.db")) is not None,
      "allowed")
badname = new_dir(tmp, "badname")
write_gz(os.path.join(badname, "btc.csv.gz"), [bars(10.0)])
check("bad filename raises", raises_sync(badname, os.path.join(tmp, "db7.db")) is not None,
      "allowed")

print("\n=== db inside source is refused ===")
side_db = os.path.join(src, "research.db")
msg = raises_sync(src, side_db)
check("db inside source raises", msg is not None, "allowed")
check("db inside source not created", not os.path.exists(side_db))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
