"""Append-only backtest-run registry (Task 4).

Self-checking script: python3 tests/test_research_runs.py
Covers: run identity + `started` event inserted before any execution, append-only
terminal events, required completion/error payloads, double-finish refusal,
code/config/assumption provenance, snapshot/sample/universe/seed pinning,
parent links, and benchmark eligibility.
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.research import runs, store

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def raises_run_error(label, fn):
    try:
        fn()
    except runs.RunError:
        check(label, True)
        return
    except Exception as exc:  # noqa: BLE001 - surface a wrong exception type
        check(label, False, f"wrong error: {exc!r}")
        return
    check(label, False, "no exception")


tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "research.db")

print("=== seed minimal snapshot/universe/sample ===")
conn = store.connect(db_path)
conn.execute("INSERT INTO source_snapshot VALUES (?,?,?,?,?,?,?,?)",
             ("snap-a", "data/bars", "digest-a", 2, 3, 1, 2, 100))
conn.execute("INSERT INTO universe VALUES (?,?,?,?,?)",
             ("uni-a", "test", "{}", "def-sha", 100))
conn.execute("INSERT INTO sample VALUES (?,?,?,?,?,?,?,?,?)",
             ("smp-a", "uni-a", "seed-7", 2, "sha256-rank-v1", None,
              '["BTC","ETH"]', "coins-sha", 100))
conn.execute("INSERT INTO sample_coin VALUES (?,?,?)", ("smp-a", 1, "BTC"))
conn.execute("INSERT INTO sample_coin VALUES (?,?,?)", ("smp-a", 2, "ETH"))
conn.commit()
conn.close()

CONFIG = {"version": "cfg-1", "window": 20}
ASSUMPTIONS = {"fee_bps": 5, "slippage_bps": 1}
PARAMS = {"horizon": 48}
OUTPUTS = {"sharpe": 1.25, "trades": 7}
ARTIFACTS = [{"name": "equity", "path": "runs/equity.csv"},
             {"name": "trades", "path": "runs/trades.csv"}]

print("\n=== started run is visible before any finish ===")
run_id = runs.start_run(
    db_path, kind="benchmark", snapshot_id="snap-a", sample_id="smp-a",
    config=CONFIG, assumptions=ASSUMPTIONS, params=PARAMS,
    code_rev="abc1234", code_dirty=1, label="smoke", notes="first attempt",
)
check("run id shape", run_id.startswith("run-") and len(run_id) == 36, run_id)
started = runs.run_status(db_path, run_id)
check("status started", started["status"] == "started", started.get("status"))
check("one started event", len(started["events"]) == 1, started["events"])
check("event seq 1", started["events"][0]["seq"] == 1)
check("event status started", started["events"][0]["status"] == "started")
check("started outputs empty", started["events"][0]["outputs"] is None)
check("started error empty", started["events"][0]["error"] is None)
check("started artifacts empty", started["events"][0]["artifacts"] is None)

print("\n=== identity row records provenance ===")
check("kind", started["kind"] == "benchmark", started.get("kind"))
check("snapshot", started["snapshot_id"] == "snap-a")
check("sample", started["sample_id"] == "smp-a")
check("universe from sample", started["universe_id"] == "uni-a")
check("seed from sample", started["seed"] == "seed-7")
check("code_rev", started["code_rev"] == "abc1234")
check("code_dirty", started["code_dirty"] == 1)
check("config_version", started["config_version"] == "cfg-1")
check("config retained", started["config"] == CONFIG, started.get("config"))
check("assumptions retained", started["assumptions"] == ASSUMPTIONS)
check("params retained", started["params"] == PARAMS)
check("config hash", started["config_sha256"] == sha(CONFIG))
check("assumptions hash", started["assumptions_sha256"] == sha(ASSUMPTIONS))
check("label", started["label"] == "smoke")
check("notes", started["notes"] == "first attempt")
check("no parent", started["parent_run_id"] is None)
check("created_ts int", isinstance(started["created_ts"], int))
check("event ts matches created_ts",
      started["events"][0]["ts"] == started["created_ts"])

print("\n=== completed run ===")
event = runs.finish_run(db_path, run_id, status="completed",
                        outputs=OUTPUTS, artifacts=ARTIFACTS)
check("finish returns event", event["status"] == "completed", event)
check("finish seq 2", event["seq"] == 2)
check("finish outputs", event["outputs"] == OUTPUTS)
check("finish artifacts", event["artifacts"] == ARTIFACTS)
done = runs.run_status(db_path, run_id)
check("latest status completed", done["status"] == "completed")
check("two events retained", len(done["events"]) == 2)
check("finished ts not before start",
      done["events"][1]["ts"] >= done["created_ts"])
check("completed benchmark eligible",
      runs.benchmark_eligible(db_path, run_id) is True)

print("\n=== refusal rules ===")
raises_run_error("double finish refused",
                 lambda: runs.finish_run(db_path, run_id, status="failed",
                                         error="late"))
raises_run_error("terminal event unchanged",
                 lambda: runs.finish_run(db_path, run_id, status="completed",
                                         outputs={}))
raises_run_error("unknown run refused",
                 lambda: runs.finish_run(db_path, "run-missing", status="completed",
                                         outputs={}))
check("unknown run not eligible",
      runs.benchmark_eligible(db_path, "run-missing") is False)

run2 = runs.start_run(
    db_path, kind="benchmark", snapshot_id="snap-a", sample_id="smp-a",
    config=CONFIG, assumptions=ASSUMPTIONS, code_rev="abc1234", code_dirty=1,
)
raises_run_error(
    "non-terminal status refused",
    lambda: runs.finish_run(db_path, run2, status="started"))
raises_run_error(
    "completed without outputs refused",
    lambda: runs.finish_run(db_path, run2, status="completed"))
raises_run_error(
    "completed with non-object outputs refused",
    lambda: runs.finish_run(db_path, run2, status="completed", outputs=[1, 2]))
raises_run_error(
    "failed without error refused",
    lambda: runs.finish_run(db_path, run2, status="failed"))
raises_run_error(
    "failed with blank error refused",
    lambda: runs.finish_run(db_path, run2, status="failed", error="   "))
check("refusals left run started",
      runs.run_status(db_path, run2)["status"] == "started")
check("refusals appended no event", len(runs.run_status(db_path, run2)["events"]) == 1)

failed = runs.finish_run(db_path, run2, status="failed", error="boom")
check("failed error retained", failed["error"] == "boom")
check("failed outputs empty", failed["outputs"] is None)
failed_status = runs.run_status(db_path, run2)
check("failed status retained", failed_status["status"] == "failed")
check("failed benchmark not eligible",
      runs.benchmark_eligible(db_path, run2) is False)

run3 = runs.start_run(
    db_path, kind="benchmark", snapshot_id="snap-a", sample_id="smp-a",
    config=CONFIG, assumptions=ASSUMPTIONS, code_rev="abc1234", code_dirty=0,
)
runs.finish_run(db_path, run3, status="interrupted", error="ctrl-c")
check("interrupted status retained",
      runs.run_status(db_path, run3)["status"] == "interrupted")
check("interrupted benchmark not eligible",
      runs.benchmark_eligible(db_path, run3) is False)

print("\n=== rerun parent link ===")
rerun_id = runs.start_run(
    db_path, kind="rerun", snapshot_id="snap-a", sample_id="smp-a",
    config=CONFIG, assumptions=ASSUMPTIONS, params=PARAMS,
    code_rev="abc1234", code_dirty=0, parent_run_id=run2,
)
rerun = runs.run_status(db_path, rerun_id)
check("parent retained", rerun["parent_run_id"] == run2, rerun.get("parent_run_id"))
check("rerun not eligible", runs.benchmark_eligible(db_path, rerun_id) is False)

print("\n=== defaults and validation ===")
default_id = runs.start_run(
    db_path, kind="exploratory", snapshot_id="snap-a", sample_id="smp-a",
    config={"version": "v-default"}, assumptions={},
)
default_run = runs.run_status(db_path, default_id)
check("default code_rev recorded",
      isinstance(default_run["code_rev"], str) and default_run["code_rev"],
      default_run.get("code_rev"))
check("default code_dirty 0/1", default_run["code_dirty"] in (0, 1))
check("default params {}", default_run["params"] == {})
runs.finish_run(db_path, default_id, status="completed", outputs={})
check("completed exploratory not eligible",
      runs.benchmark_eligible(db_path, default_id) is False)

raises_run_error(
    "unknown kind refused",
    lambda: runs.start_run(db_path, kind="bogus", snapshot_id="snap-a",
                           sample_id="smp-a", config=CONFIG, assumptions={}))
raises_run_error(
    "unknown snapshot refused",
    lambda: runs.start_run(db_path, kind="exploratory", snapshot_id="snap-missing",
                           sample_id="smp-a", config=CONFIG, assumptions={}))
raises_run_error(
    "unknown sample refused",
    lambda: runs.start_run(db_path, kind="exploratory", snapshot_id="snap-a",
                           sample_id="smp-missing", config=CONFIG, assumptions={}))
raises_run_error(
    "config without version refused",
    lambda: runs.start_run(db_path, kind="exploratory", snapshot_id="snap-a",
                           sample_id="smp-a", config={}, assumptions={}))
raises_run_error(
    "blank config version refused",
    lambda: runs.start_run(db_path, kind="exploratory", snapshot_id="snap-a",
                           sample_id="smp-a", config={"version": " "},
                           assumptions={}))
raises_run_error(
    "non-string config version refused",
    lambda: runs.start_run(db_path, kind="exploratory", snapshot_id="snap-a",
                           sample_id="smp-a", config={"version": 7},
                           assumptions={}))
raises_run_error(
    "unknown status refused",
    lambda: runs.finish_run(db_path, rerun_id, status="weird"))

print("\n=== list_runs ===")
all_runs = runs.list_runs(db_path)
check("all runs listed", len(all_runs) == 5, len(all_runs))
check("list reports latest status",
      {r["run_id"]: r["status"] for r in all_runs}[run_id] == "completed")
by_kind = runs.list_runs(db_path, kind="benchmark")
check("kind filter", {r["run_id"] for r in by_kind} == {run_id, run2, run3},
      [r["run_id"] for r in by_kind])
by_status = runs.list_runs(db_path, status="completed")
check("status filter", {r["run_id"] for r in by_status} == {run_id, default_id},
      [r["run_id"] for r in by_status])
check("started filter", [r["run_id"] for r in runs.list_runs(db_path, status="started")]
      == [rerun_id])
check("failed filter", [r["run_id"] for r in runs.list_runs(db_path, status="failed")]
      == [run2])

print("\n=== append-only identity and events ===")
conn = store.connect(db_path)
for table, sql in (
    ("backtest_run UPDATE", "UPDATE backtest_run SET label = 'rewritten'"),
    ("backtest_run DELETE", "DELETE FROM backtest_run"),
    ("run_event UPDATE", "UPDATE run_event SET status = 'completed'"),
    ("run_event DELETE", "DELETE FROM run_event"),
):
    try:
        conn.execute(sql)
    except sqlite3.IntegrityError as exc:
        check(f"{table} refused", "append-only" in str(exc), str(exc))
    else:
        check(f"{table} refused", False, "statement was allowed")
check("identity rows intact",
      conn.execute("SELECT COUNT(*) FROM backtest_run").fetchone()[0] == 5)
check("event rows intact",
      conn.execute("SELECT COUNT(*) FROM run_event").fetchone()[0] == 9)
conn.close()

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
