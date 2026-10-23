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
from proto.research import status
from proto.research import sync
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
# Valid OHLC, differing close: rejected by the duplicate rule, not valid_bar.
raw += gzip.compress(f"{dup[0]},{dup[1]},{dup[2]},{dup[3]},100.25,{dup[5]},{dup[6]}\n"
                     .encode(), mtime=0)
with open(btc_path, "wb") as f:
    f.write(raw)
bad_digest = source_digest(src)
msg = raises_sync(src, db)
check("conflicting duplicate raises", msg is not None, "no exception")
check("error names the coin", msg is not None and "BTC" in msg, str(msg))
check("error is the duplicate rule",
      msg is not None and "conflicting duplicate ts 300" in msg, str(msg))
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

print("\n=== amount-only change hits the mirrored-value guard ===")
amount_src = new_dir(tmp, "amount_conflict")
amount_path = os.path.join(amount_src, "BTC.csv.gz")
amount_rows = bars(100.0)
write_gz(amount_path, [amount_rows])
amount_db = os.path.join(tmp, "db_amount", "research.db")
sync_bars(amount_src, amount_db)
# New file bytes, no in-file duplicate, same OHLCV except one amount.
write_gz(amount_path, [[(t, o, h, l, c, v, a + 1.0 if t == 600 else a)
                        for t, o, h, l, c, v, a in amount_rows]])
new_amount_digest = source_digest(amount_src)
msg = raises_sync(amount_src, amount_db)
check("mirrored amount conflict raises", msg is not None, "no exception")
check("error is the mirrored-value guard",
      msg is not None and "conflicting values for mirrored ts 600" in msg, str(msg))
check("mirrored amount unchanged",
      query(amount_db, "SELECT amount FROM bar WHERE coin = 'BTC' AND ts = 600")
      == [(1111.0,)],
      str(query(amount_db, "SELECT amount FROM bar WHERE coin='BTC' AND ts=600")))
check("amount conflict inserted no rows",
      query(amount_db, "SELECT COUNT(*) FROM bar")[0][0] == 3)
check("amount conflict added no provenance",
      query(amount_db, "SELECT COUNT(*) FROM bar_file")[0][0] == 1)
check("amount conflict recorded one failed run",
      query(amount_db,
            "SELECT COUNT(*) FROM sync_run WHERE status = 'failed'")[0][0] == 1)
check("amount conflict created no snapshot",
      query(amount_db, "SELECT COUNT(*) FROM source_snapshot")[0][0] == 1
      and query(amount_db,
                "SELECT COUNT(*) FROM source_snapshot WHERE source_digest = ?",
                (new_amount_digest,))[0][0] == 0)

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

print("\n=== a later corrupt file rolls back the whole import ===")
rollback_src = new_dir(tmp, "rollback")
write_gz(os.path.join(rollback_src, "AAA.csv.gz"), [bars(10.0)])
with open(os.path.join(rollback_src, "BBB.csv.gz"), "wb") as f:
    f.write(b"broken gzip bytes")
rollback_db = os.path.join(tmp, "db_rollback", "research.db")
msg = raises_sync(rollback_src, rollback_db)
check("rollback sync raises", msg is not None, "no exception")
check("rollback leaves zero bars",
      query(rollback_db, "SELECT COUNT(*) FROM bar")[0][0] == 0)
check("rollback leaves zero provenance",
      query(rollback_db, "SELECT COUNT(*) FROM bar_file")[0][0] == 0)
check("rollback leaves zero snapshots",
      query(rollback_db, "SELECT COUNT(*) FROM source_snapshot")[0][0] == 0)
check("rollback records one failed run",
      query(rollback_db,
            "SELECT COUNT(*) FROM sync_run WHERE status = 'failed'")[0][0] == 1)

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
    "header only": HEADER,
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
empty_db = os.path.join(tmp, "db6", "research.db")
check("empty source raises", raises_sync(empty, empty_db) is not None, "allowed")
check("empty source recorded failed run",
      query(empty_db,
            "SELECT COUNT(*) FROM sync_run WHERE status = 'failed'")[0][0] == 1,
      str(query(empty_db, "SELECT status, error FROM sync_run")))

missing = os.path.join(tmp, "never_existed")
missing_db = os.path.join(tmp, "db_missing", "research.db")
check("missing source raises", raises_sync(missing, missing_db) is not None,
      "allowed")
check("missing source recorded failed run",
      query(missing_db,
            "SELECT COUNT(*) FROM sync_run WHERE status = 'failed'")[0][0] == 1,
      str(query(missing_db, "SELECT status, error FROM sync_run")))

badname = new_dir(tmp, "badname")
write_gz(os.path.join(badname, "btc.csv.gz"), [bars(10.0)])
badname_db = os.path.join(tmp, "db7", "research.db")
check("bad filename raises", raises_sync(badname, badname_db) is not None,
      "allowed")
check("bad filename recorded failed run",
      query(badname_db,
            "SELECT COUNT(*) FROM sync_run WHERE status = 'failed'")[0][0] == 1,
      str(query(badname_db, "SELECT status, error FROM sync_run")))

print("\n=== read-side OSError / pass-1 instability become SyncError + run ===")
import proto.research.sync as syncmod

instable = new_dir(tmp, "instable")
instable_file = os.path.join(os.path.realpath(instable), "AAA.csv.gz")
write_gz(instable_file, [bars(10.0)])
instable_db = os.path.join(tmp, "db8", "research.db")
orig_read = syncmod._stable_read


def raise_oserror(path):
    raise FileNotFoundError(2, "No such file or directory", path)


def raise_unstable(path):
    raise syncmod._Unstable("changed while reading")


try:
    syncmod._stable_read = raise_oserror
    msg = raises_sync(instable, instable_db)
    syncmod._stable_read = raise_unstable
    msg2 = raises_sync(instable, instable_db)
finally:
    syncmod._stable_read = orig_read
check("read OSError raises SyncError", msg is not None, "raw OSError escaped")
check("read OSError recorded failed run",
      query(instable_db,
            "SELECT COUNT(*) FROM sync_run WHERE status = 'failed'")[0][0] >= 1,
      str(query(instable_db, "SELECT status, error FROM sync_run")))
check("read error text has the path once", msg is not None
      and msg.count(instable_file) == 1, str(msg))
check("instability raises SyncError", msg2 is not None, "raw _Unstable escaped")
check("instability recorded failed run",
      query(instable_db,
            "SELECT COUNT(*) FROM sync_run WHERE status = 'failed'")[0][0] == 2,
      str(query(instable_db, "SELECT status, error FROM sync_run")))
check("error text has the path once", msg2 is not None
      and msg2.count(instable_file) == 1, str(msg2))

print("\n=== db inside source is refused ===")
side_db = os.path.join(src, "research.db")
msg = raises_sync(src, side_db)
check("db inside source raises", msg is not None, "allowed")
check("db inside source not created", not os.path.exists(side_db))

print("\n=== non-bars data sources are refused ===")
for name in ("prices", "funding-history", "quant"):
    bad_src = os.path.join(tmp, "data", name)
    os.makedirs(bad_src, exist_ok=True)
    write_gz(os.path.join(bad_src, "BTC.csv.gz"), [bars(100.0)])
    bad_db = os.path.join(tmp, "bad-" + name, "research.db")
    msg = raises_sync(bad_src, bad_db)
    check(f"refuse data/{name} source", msg is not None, "allowed")
    check(f"data/{name} source creates no db", not os.path.exists(bad_db))

bare_db = os.path.join(tmp, "bad-bare", "research.db")
msg = raises_sync(os.path.join(tmp, "data"), bare_db)
check("refuse a bare data/ source", msg is not None, "allowed")
check("bare data/ source creates no db", not os.path.exists(bare_db))

print("\n=== snapshots are reconstructable after later syncs ===")
hist = new_dir(tmp, "history")
a_path = os.path.join(hist, "AAA.csv.gz")
b_path = os.path.join(hist, "BBB.csv.gz")
a_old = bars(100.0)
write_gz(a_path, [a_old])
write_gz(b_path, [bars(50.0)])
hist_db = os.path.join(tmp, "db-history", "research.db")
s1 = sync_bars(hist, hist_db)
snap1 = sync.load_snapshot(hist_db, s1["snapshot_id"])
check("snapshot 1 seq", snap1["seq"] == 1, str(snap1["seq"]))
check("snapshot 1 manifest coins", [f["coin"] for f in snap1["files"]]
      == ["AAA", "BBB"], str(snap1["files"]))
check("snapshot 1 manifest digests",
      all(f["sha256"] == file_sha(os.path.join(hist, f["coin"] + ".csv.gz"))
          for f in snap1["files"]), str(snap1["files"]))
check("snapshot 1 counts manifest rows", snap1["bars"] == 6, str(snap1["bars"]))
check("snapshot 1 bars for AAA", sync.load_snapshot_bars(
    hist_db, s1["snapshot_id"], "AAA") == a_old)

# AAA grows, CCC appears, BBB disappears.
a_new = bars(100.0, n=5)
write_gz(a_path, [a_new])
write_gz(os.path.join(hist, "CCC.csv.gz"), [bars(200.0)])
os.remove(b_path)
s2 = sync_bars(hist, hist_db)
snap2 = sync.load_snapshot(hist_db, s2["snapshot_id"])
check("snapshot 2 seq", snap2["seq"] == 2, str(snap2["seq"]))
check("snapshot 2 excludes the disappeared file",
      [f["coin"] for f in snap2["files"]] == ["AAA", "CCC"], str(snap2["files"]))
check("snapshot 2 bars count present files only", snap2["bars"] == 8,
      str(snap2["bars"]))
check("snapshot 2 manifest has AAA's new version",
      [f["sha256"] for f in snap2["files"] if f["coin"] == "AAA"]
      == [file_sha(a_path)], str(snap2["files"]))
check("snapshot 1 manifest is unchanged",
      sync.load_snapshot(hist_db, s1["snapshot_id"])["files"] == snap1["files"])
check("snapshot 1 still reconstructs the old AAA rows",
      sync.load_snapshot_bars(hist_db, s1["snapshot_id"], "AAA") == a_old)
check("snapshot 1 still reconstructs the removed BBB rows",
      sync.load_snapshot_bars(hist_db, s1["snapshot_id"], "BBB") == bars(50.0))
check("snapshot 2 excludes retained BBB bars", not any(
    f["coin"] == "BBB" for f in snap2["files"]))
try:
    sync.load_snapshot_bars(hist_db, s2["snapshot_id"], "BBB")
    check("removed coin is not part of snapshot 2", False, "returned rows")
except sync.SyncError:
    check("removed coin is not part of snapshot 2", True)
check("snapshot 2 has AAA's grown version",
      sync.load_snapshot_bars(hist_db, s2["snapshot_id"], "AAA") == a_new)
check("mirror still retains BBB as evidence",
      query(hist_db, "SELECT COUNT(*) FROM bar WHERE coin = 'BBB'")[0][0] == 3)
replay = sync_bars(hist, hist_db)
check("replay reuses snapshot 2", replay["snapshot_id"] == s2["snapshot_id"])
check("replay does not duplicate the manifest",
      query(hist_db, "SELECT COUNT(*) FROM snapshot_file WHERE snapshot_id = ?",
            (s2["snapshot_id"],))[0][0] == 2)
try:
    sync.load_snapshot(hist_db, "snap-missing")
    check("unknown snapshot refused", False, "returned")
except sync.SyncError:
    check("unknown snapshot refused", True)

print("\n=== a source that loses mirrored rows is refused ===")
write_gz(a_path, [bars(100.0, n=2)])  # drops previously mirrored timestamps
before_rows = query(hist_db, "SELECT COUNT(*) FROM bar")
before_snaps = query(hist_db, "SELECT COUNT(*) FROM source_snapshot")
msg = raises_sync(hist, hist_db)
check("dropped rows raise", msg is not None, "allowed")
check("dropped rows do not change the mirror",
      query(hist_db, "SELECT COUNT(*) FROM bar") == before_rows)
check("dropped rows do not add a snapshot",
      query(hist_db, "SELECT COUNT(*) FROM source_snapshot") == before_snaps)

print("\n=== a shrunken file version is refused ===")
write_gz(a_path, [bars(100.0, n=5)])
sync_bars(hist, hist_db)
write_gz(a_path, [bars(100.0, n=5)[:-1]])  # subsets the current version
before_snaps = query(hist_db, "SELECT COUNT(*) FROM source_snapshot")
msg = raises_sync(hist, hist_db)
check("shrunken version raises", msg is not None, "allowed")
check("shrunken version adds no snapshot",
      query(hist_db, "SELECT COUNT(*) FROM source_snapshot") == before_snaps)

print("\n=== reverting to a recorded state is refused when the mirror moved on ===")
# Restore the exact snapshot-1 file set: AAA old, BBB back, CCC removed.
write_gz(a_path, [a_old])
write_gz(b_path, [bars(50.0)])
os.remove(os.path.join(hist, "CCC.csv.gz"))
before_snaps = query(hist_db, "SELECT COUNT(*) FROM source_snapshot")
msg = raises_sync(hist, hist_db)
check("full-digest revert raises", msg is not None, "allowed")
check("full-digest revert adds no snapshot",
      query(hist_db, "SELECT COUNT(*) FROM source_snapshot") == before_snaps)
check("full-digest revert leaves the mirror intact",
      query(hist_db, "SELECT COUNT(*) FROM bar") == before_rows)

print("\n=== a safe replay of a recorded state is accepted ===")
replay_dir = new_dir(tmp, "replay")
write_gz(os.path.join(replay_dir, "AAA.csv.gz"), [a_old])
write_gz(os.path.join(replay_dir, "BBB.csv.gz"), [bars(50.0)])
replay_db = os.path.join(tmp, "db-replay", "research.db")
r1 = sync_bars(replay_dir, replay_db)
os.remove(os.path.join(replay_dir, "BBB.csv.gz"))
r2 = sync_bars(replay_dir, replay_db)
write_gz(os.path.join(replay_dir, "BBB.csv.gz"), [bars(50.0)])
r3 = sync_bars(replay_dir, replay_db)
check("returning to a recorded file set reuses its snapshot",
      r3["snapshot_id"] == r1["snapshot_id"], f"{r3['snapshot_id']} vs {r1['snapshot_id']}")
check("no extra snapshot for the replay",
      query(replay_db, "SELECT COUNT(*) FROM source_snapshot")[0][0] == 2)
check("a replayed snapshot still reconstructs both coins",
      sync.load_snapshot_bars(replay_db, r3["snapshot_id"], "BBB") == bars(50.0))
view = status.db_status(replay_db)
check("latest_snapshot follows the last completed sync",
      view["latest_snapshot"]["snapshot_id"] == r3["snapshot_id"],
      f"{view['latest_snapshot']['snapshot_id']} vs {r3['snapshot_id']}")

print("\n=== a reappearing coin is snapshotted at its new version ===")
reappear = new_dir(tmp, "reappear")
ra = os.path.join(reappear, "AAA.csv.gz")
rb = os.path.join(reappear, "BBB.csv.gz")
write_gz(ra, [bars(100.0)])
write_gz(rb, [bars(50.0)])
re_db = os.path.join(tmp, "db-reappear", "research.db")
q1 = sync_bars(reappear, re_db)
os.remove(rb)
q2 = sync_bars(reappear, re_db)
write_gz(rb, [bars(50.0, n=4)])
q3 = sync_bars(reappear, re_db)
check("reappearing coin is in the new manifest",
      [f["coin"] for f in sync.load_snapshot(re_db, q3["snapshot_id"])["files"]]
      == ["AAA", "BBB"])
check("reappearing coin keeps its old rows and adds the new one",
      sync.load_snapshot_bars(re_db, q3["snapshot_id"], "BBB") == bars(50.0, n=4))
try:
    sync.load_snapshot_bars(re_db, q2["snapshot_id"], "BBB")
    check("the disappeared snapshot excludes BBB", False, "returned rows")
except sync.SyncError:
    check("the disappeared snapshot excludes BBB", True)
check("the original snapshot still has three rows",
      len(sync.load_snapshot_bars(re_db, q1["snapshot_id"], "BBB")) == 3)

print("\n=== symlinked bar files are refused ===")
links = new_dir(tmp, "links")
write_gz(os.path.join(links, "AAA.csv.gz"), [bars(100.0)])
os.symlink(os.path.join(links, "AAA.csv.gz"),
           os.path.join(links, "BBB.csv.gz"))
links_db = os.path.join(tmp, "db-links", "research.db")
msg = raises_sync(links, links_db)
check("symlink to a file inside the source refused", msg is not None, "allowed")
check("inside symlink created no snapshot",
      query(links_db, "SELECT COUNT(*) FROM source_snapshot")[0][0] == 0)

outside = new_dir(tmp, "outside")
write_gz(os.path.join(outside, "EVIL.csv.gz"), [bars(999.0)])
os.remove(os.path.join(links, "BBB.csv.gz"))
os.symlink(os.path.join(outside, "EVIL.csv.gz"),
           os.path.join(links, "BBB.csv.gz"))
links2_db = os.path.join(tmp, "db-links2", "research.db")
msg = raises_sync(links, links2_db)
check("symlink to a file outside the source refused", msg is not None, "allowed")
check("outside symlink created no snapshot",
      query(links2_db, "SELECT COUNT(*) FROM source_snapshot")[0][0] == 0)
check("outside symlink ingested no bars",
      query(links2_db, "SELECT COUNT(*) FROM bar")[0][0] == 0)

# Defense in depth: even if enumeration were bypassed or raced, the open itself
# must refuse a symlink instead of following it.
try:
    sync._read_file(os.path.join(links, "BBB.csv.gz"))
    check("direct open refuses a symlink", False, "followed it")
except sync.SyncError:
    check("direct open refuses a symlink", True)

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
