"""Deterministic universe sampling (Task 3).

Self-checking script: python3 tests/test_research_sampling.py
Covers: canonical universe hashing, the ``sha256-rank-v1`` draw, idempotent
replay of the same sample spec, a different seed leaving the frozen sample
untouched, oversized / malformed definitions, append-only triggers, and a
read-only ``load_sample`` that never creates a missing database.
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.research import store, universe
from proto.research.universe import (
    SAMPLE_METHOD, SamplingError, load_sample, record_sample,
)

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return path


def expected_coins(coins, seed, n):
    return sorted(coins, key=lambda coin: (sha(f"{seed}|{coin}"), coin))[:n]


def scalar(db_path, sql):
    conn = store.connect(db_path)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def raises_sampling(fn):
    try:
        fn()
    except SamplingError:
        return True
    return False


tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "research.db")
uni_path = os.path.join(tmp, "universe.json")
SEED = "2026-10-22"
OTHER_SEED = "2026-10-23"

coins = [f"COIN{i:02d}" for i in range(40)]
definition = {
    "name": "phase1-synthetic-40",
    "coins": coins,
    "description": "task-3 fixture",
}
write_json(uni_path, definition)

canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
definition_sha256 = sha(canonical)
universe_id = "uni-" + definition_sha256[:20]
sample_id = "smp-" + sha(f"{universe_id}|{SEED}|20|sha256-rank-v1")[:20]
draw = expected_coins(coins, SEED, 20)

print("=== record a frozen sample ===")
check("SAMPLE_METHOD", SAMPLE_METHOD == "sha256-rank-v1", SAMPLE_METHOD)
result = record_sample(db_path, uni_path, SEED, 20)
check("result keys",
      set(result) == {"sample_id", "universe_id", "seed", "n", "method",
                      "coins", "label"},
      str(sorted(result)))
check("sample_id", result["sample_id"] == sample_id, result["sample_id"])
check("universe_id", result["universe_id"] == universe_id, result["universe_id"])
check("seed stored as text",
      result["seed"] == SEED and isinstance(result["seed"], str),
      repr(result["seed"]))
check("n", result["n"] == 20, repr(result["n"]))
check("method", result["method"] == SAMPLE_METHOD, result["method"])
check("label defaults to None", result["label"] is None, repr(result["label"]))
check("20 ranked coins",
      result["coins"] == draw and len(result["coins"]) == 20,
      str(result["coins"]))

print("\n=== stored rows ===")
conn = store.connect(db_path)
try:
    uni_rows = conn.execute(
        "SELECT name, definition_json, definition_sha256 FROM universe").fetchall()
    check("one universe row", len(uni_rows) == 1, str(uni_rows))
    name, def_json, def_sha = uni_rows[0]
    check("universe name", name == definition["name"], name)
    check("definition_json is canonical",
          def_json == canonical and json.loads(def_json) == definition,
          def_json[:80])
    check("definition_sha256", def_sha == definition_sha256, def_sha)

    smp_rows = conn.execute(
        "SELECT sample_id, universe_id, seed, n, method, label, coins_json, "
        "coins_sha256 FROM sample").fetchall()
    check("one sample row", len(smp_rows) == 1, str(smp_rows))
    sid, uid, seed_col, n_col, method_col, label_col, coins_json, coins_sha = smp_rows[0]
    check("sample row sample_id", sid == sample_id, sid)
    check("sample row universe_id", uid == universe_id, uid)
    check("sample row seed", seed_col == SEED, seed_col)
    check("sample row n", n_col == 20, repr(n_col))
    check("sample row method", method_col == SAMPLE_METHOD, method_col)
    check("sample row label", label_col is None, repr(label_col))
    check("coins_json", coins_json == json.dumps(draw, separators=(",", ":")),
          coins_json[:80])
    check("coins_sha256", coins_sha == sha(coins_json), coins_sha)

    ranked = conn.execute(
        "SELECT rank, coin FROM sample_coin ORDER BY rank").fetchall()
    check("sample_coin ranks 1..20",
          ranked == [(rank, coin) for rank, coin in enumerate(draw, 1)],
          str(ranked[:3]))
finally:
    conn.close()

check("load_sample round-trip", load_sample(db_path, sample_id) == result)

print("\n=== replay is idempotent ===")
again = record_sample(db_path, uni_path, SEED, 20, label="ignored-on-replay")
check("same sample_id", again["sample_id"] == result["sample_id"],
      again["sample_id"])
check("same ordered coins", again["coins"] == result["coins"],
      str(again["coins"]))
check("stored label unchanged", again["label"] is None, repr(again["label"]))
check("one universe row after replay",
      scalar(db_path, "SELECT COUNT(*) FROM universe") == 1)
check("one sample row after replay",
      scalar(db_path, "SELECT COUNT(*) FROM sample") == 1)
check("20 sample_coin rows after replay",
      scalar(db_path, "SELECT COUNT(*) FROM sample_coin") == 20)

print("\n=== a different seed is a new frozen sample ===")
other = record_sample(db_path, uni_path, OTHER_SEED, 20)
check("different sample_id", other["sample_id"] != result["sample_id"],
      other["sample_id"])
check("different coins", other["coins"] == expected_coins(coins, OTHER_SEED, 20),
      str(other["coins"]))
check("two sample rows", scalar(db_path, "SELECT COUNT(*) FROM sample") == 2)
check("original unchanged",
      load_sample(db_path, result["sample_id"]) == result)
check("original sample_coin unchanged",
      scalar(db_path,
             f"SELECT COUNT(*) FROM sample_coin WHERE sample_id = '{sample_id}'")
      == 20)

print("\n=== n cannot exceed the universe ===")
check("n > len(coins) raises",
      raises_sampling(lambda: record_sample(db_path, uni_path, SEED, 41)))
check("no sample added by oversized n",
      scalar(db_path, "SELECT COUNT(*) FROM sample") == 2)
check("n < 1 raises",
      raises_sampling(lambda: record_sample(db_path, uni_path, SEED, 0)))

print("\n=== invalid universe definitions are refused ===")
before = scalar(db_path, "SELECT COUNT(*) FROM universe")
invalid = [
    ("duplicate coins", {"name": "dup", "coins": ["BTC", "BTC"]}, 1),
    ("lowercase coin", {"name": "lower", "coins": ["btc"]}, 1),
    ("invalid coin characters", {"name": "chars", "coins": ["BTC-USDT"]}, 1),
    ("empty coin list", {"name": "empty", "coins": []}, 1),
    ("non-object json", ["BTC"], 1),
    ("string json", "BTC", 1),
    ("non-finite extra key",
     {"name": "nan", "coins": ["BTC"], "extra": float("nan")}, 1),
]
for i, (name, obj, n) in enumerate(invalid):
    path = write_json(os.path.join(tmp, f"bad{i}.json"), obj)
    check(name + " raises",
          raises_sampling(lambda p=path, k=n: record_sample(db_path, p, SEED, k)))

bad_syntax = os.path.join(tmp, "bad-syntax.json")
with open(bad_syntax, "w", encoding="utf-8") as fh:
    fh.write("{not json")
check("malformed json raises",
      raises_sampling(lambda: record_sample(db_path, bad_syntax, SEED, 1)))
check("no universe rows from invalid definitions",
      scalar(db_path, "SELECT COUNT(*) FROM universe") == before)

print("\n=== frozen rows cannot be rewritten ===")
conn = store.connect(db_path)
try:
    for table, action, statement in (
        ("universe", "UPDATE", "UPDATE universe SET name = 'x'"),
        ("universe", "DELETE", "DELETE FROM universe"),
        ("sample", "UPDATE", "UPDATE sample SET label = 'x'"),
        ("sample", "DELETE", "DELETE FROM sample"),
        ("sample_coin", "UPDATE", "UPDATE sample_coin SET rank = 99"),
        ("sample_coin", "DELETE", "DELETE FROM sample_coin"),
    ):
        try:
            conn.execute(statement)
            check(f"{action} {table} refused", False, "statement was allowed")
        except sqlite3.IntegrityError as exc:
            check(f"{action} {table} refused",
                  "append-only" in str(exc), str(exc))
        except sqlite3.Error as exc:
            check(f"{action} {table} refused", False, f"wrong error: {exc}")
finally:
    conn.close()
check("rows intact after refused writes",
      scalar(db_path, "SELECT COUNT(*) FROM universe") == 1
      and scalar(db_path, "SELECT COUNT(*) FROM sample") == 2)

print("\n=== contradictory stored ranks are refused, never served ===")
tampered_db = os.path.join(tmp, "tampered.db")
tampered_coins_json = json.dumps(draw, separators=(",", ":"))
conn = store.connect(tampered_db)
try:
    conn.execute(
        "INSERT INTO universe (universe_id, name, definition_json, "
        "definition_sha256, created_ts) VALUES (?,?,?,?,?)",
        (universe_id, definition["name"], canonical, definition_sha256, 1))
    conn.execute(
        "INSERT INTO sample (sample_id, universe_id, seed, n, method, label, "
        "coins_json, coins_sha256, created_ts) VALUES (?,?,?,?,?,?,?,?,?)",
        (sample_id, universe_id, SEED, 20, SAMPLE_METHOD, None,
         tampered_coins_json, sha(tampered_coins_json), 1))
    swapped = [(1, draw[1]), (2, draw[0])] + [
        (rank, coin) for rank, coin in enumerate(draw, 1) if rank > 2]
    conn.executemany(
        "INSERT INTO sample_coin (sample_id, rank, coin) VALUES (?,?,?)",
        [(sample_id, rank, coin) for rank, coin in swapped])
    conn.commit()
finally:
    conn.close()

check("contradictory ranks raise",
      raises_sampling(lambda: record_sample(tampered_db, uni_path, SEED, 20)))
check("contradictory ranks left untouched",
      scalar(tampered_db,
             f"SELECT coin FROM sample_coin WHERE sample_id = '{sample_id}' "
             "AND rank = 1") == draw[1])

print("\n=== load_sample is read-only and has no redraw API ===")
missing_db = os.path.join(tmp, "absent.db")
try:
    load_sample(missing_db, "smp-missing")
    check("load_sample missing db raises", False, "no exception")
except (sqlite3.OperationalError, store.StoreError):
    check("load_sample missing db raises", True)
check("missing db not created", not os.path.exists(missing_db))
check("unknown sample raises",
      raises_sampling(lambda: load_sample(db_path, "smp-does-not-exist")))
check("no redraw/update/delete API",
      not any(hasattr(universe, attr) for attr in (
          "update_sample", "delete_sample", "redraw_sample", "delete_universe")))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
