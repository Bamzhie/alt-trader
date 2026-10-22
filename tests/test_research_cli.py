"""Local inspection CLI and read-only status view (Task 5).

Self-checking script: python3 tests/test_research_cli.py

Covers: argparse help and the JSON error contract, `status` on a missing
database (exit 1, file not created), refusal of production database sidecar
names, sync -> status with matching counts and an unchanged database
fingerprint, idempotent `sample create`, the start/finish/show/list run
round-trip (including failed and interrupted runs), status counts and recent
runs, a run with no event, read-only commands leaving the database directory
untouched (including a read-only directory), the import isolation boundary,
and a guard proving every CLI path used here is inside the test temp dir.
No test opens or writes the repository `data/` tree.
"""

import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from proto.research import runs, store

FAILURES = []

BASE_TS = 1_700_000_100  # a multiple of 300, so sync accepts it
PATH_FLAGS = {
    "--db", "--source", "--universe-file", "--config-file",
    "--assumptions-file", "--params-file", "--outputs-file", "--artifacts-file",
}
SEEN_PATHS = set()


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def run_cli(*args):
    """Run the CLI, recording every path argument it is given."""
    for i, arg in enumerate(args[:-1]):
        if arg in PATH_FLAGS:
            SEEN_PATHS.add(os.path.realpath(args[i + 1]))
    return subprocess.run(
        [sys.executable, "-m", "proto.research", *args],
        cwd=ROOT, capture_output=True, text=True)


def ok_json(*args):
    """Run a command, require exit 0, `ok: true`, and one JSON object."""
    label = " ".join(args)
    proc = run_cli(*args)
    check(f"exit 0: {label}", proc.returncode == 0,
          (proc.stderr or proc.stdout).strip()[:300])
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        check(f"one JSON object: {label}", False, repr(proc.stdout)[:300])
        return {}
    check(f"ok is true: {label}", data.get("ok") is True, data)
    return data


def error_json(*args):
    """Run a command expected to fail; return its stderr JSON object."""
    label = " ".join(args)
    proc = run_cli(*args)
    check(f"exit 1: {label}", proc.returncode == 1,
          f"exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}")
    check(f"stdout empty: {label}", proc.stdout.strip() == "",
          repr(proc.stdout)[:200])
    try:
        return json.loads(proc.stderr)
    except ValueError:
        check(f"JSON error on stderr: {label}", False, repr(proc.stderr)[:300])
        return {}


def write_bars(source, coin, timestamps):
    rows = ["ts,o,h,l,c,vol,amount"]
    for i, ts in enumerate(timestamps):
        o = 1.0 + i * 0.01
        rows.append(f"{ts},{o},{o + 0.1:.4f},{o - 0.1:.4f},{o + 0.05:.4f},"
                    f"{10 + i},{10 + i}")
    with gzip.open(os.path.join(source, f"{coin}.csv.gz"), "wt",
                   encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return path


def fingerprint(path):
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    stat = os.stat(path)
    return digest, stat.st_size, stat.st_mtime_ns


def directory_state(path):
    state = {}
    for name in os.listdir(path):
        stat = os.stat(os.path.join(path, name))
        state[name] = (stat.st_size, stat.st_mtime_ns)
    return state


tmp = tempfile.mkdtemp()
source = os.path.join(tmp, "bars")
os.makedirs(source)
db = os.path.join(tmp, "research.db")

print("=== --help ===")
proc = run_cli("--help")
check("--help exits 0", proc.returncode == 0, proc.stderr.strip()[:300])
check("--help lists subcommands",
      all(word in proc.stdout for word in ("sync", "status", "sample", "run")),
      proc.stdout[:300])

print("\n=== usage errors keep the JSON contract ===")
proc = run_cli()
check("missing subcommand exits 1", proc.returncode == 1, proc.returncode)
try:
    usage = json.loads(proc.stderr)
except ValueError:
    usage = {}
check("usage error is JSON",
      usage.get("ok") is False and isinstance(usage.get("error"), str),
      proc.stderr[:300])
no_source_db = os.path.join(tmp, "no-source.db")
err = error_json("sync", "--db", no_source_db)
check("sync without --source is a JSON error", err.get("ok") is False, err)
check("sync without --source creates nothing",
      not os.path.exists(no_source_db))

print("\n=== status refuses a missing database without creating it ===")
missing = os.path.join(tmp, "absent.db")
err = error_json("status", "--db", missing)
check("error object shape", err.get("ok") is False
      and isinstance(err.get("error"), str) and err["error"], err)
check("missing database not created", not os.path.exists(missing))

print("\n=== production database sidecars are refused ===")
for name in ("quant.db-wal", "quant.db-shm", "quant.db-journal",
             "signals.db-wal", "signals.db-shm", "signals.db-journal"):
    blocked = os.path.join(tmp, name)
    err = error_json("status", "--db", blocked)
    check(f"{name} refused", err.get("ok") is False
          and "protected" in str(err.get("error", "")), err)
    check(f"{name} not created", not os.path.exists(blocked))

print("\n=== sync -> status ===")
write_bars(source, "BTC", [BASE_TS, BASE_TS + 300, BASE_TS + 600])
write_bars(source, "ETH", [BASE_TS, BASE_TS + 300])
synced = ok_json("sync", "--source", source, "--db", db)
check("sync completed", synced.get("status") == "completed", synced)
check("sync coins", synced.get("coins") == 2, synced.get("coins"))
check("sync bars", synced.get("bars") == 5, synced.get("bars"))
check("sync bars_inserted", synced.get("bars_inserted") == 5,
      synced.get("bars_inserted"))
check("sync snapshot id", isinstance(synced.get("snapshot_id"), str)
      and len(synced["snapshot_id"]) == 64, synced.get("snapshot_id"))
check("database created at --db", os.path.isfile(db))

before = fingerprint(db)
view = ok_json("status", "--db", db)
after = fingerprint(db)
check("status leaves the database bytes untouched", before == after,
      f"{before} -> {after}")
check("schema_version", view.get("schema_version") == 1,
      view.get("schema_version"))
check("db path reported", os.path.realpath(view.get("db", "")) ==
      os.path.realpath(db), view.get("db"))
check("bars coins", view.get("bars", {}).get("coins") == 2, view.get("bars"))
check("bars rows", view.get("bars", {}).get("rows") == 5, view.get("bars"))
check("bars min_ts", view.get("bars", {}).get("min_ts") == BASE_TS,
      view.get("bars"))
check("bars max_ts", view.get("bars", {}).get("max_ts") == BASE_TS + 600,
      view.get("bars"))
snapshot = view.get("latest_snapshot") or {}
check("latest_snapshot id", snapshot.get("snapshot_id") == synced["snapshot_id"],
      snapshot)
check("latest_snapshot coins", snapshot.get("coins") == 2, snapshot)
check("latest_snapshot bars", snapshot.get("bars") == 5, snapshot)
latest_sync = view.get("latest_sync") or {}
check("latest_sync id", latest_sync.get("sync_id") == synced["sync_id"],
      latest_sync)
check("latest_sync status", latest_sync.get("status") == "completed", latest_sync)
check("empty run counts",
      view.get("runs") == {"started": 0, "completed": 0, "failed": 0,
                           "interrupted": 0}, view.get("runs"))
check("benchmark_completed starts at 0", view.get("benchmark_completed") == 0,
      view.get("benchmark_completed"))
check("recent_runs empty", view.get("recent_runs") == [], view.get("recent_runs"))

print("\n=== sample create is idempotent ===")
universe_file = write_json(os.path.join(tmp, "universe.json"), {
    "name": "cli-fixture",
    "coins": ["BTC", "ETH", "SOL", "ADA"],
})
sample = ok_json("sample", "create", "--universe-file", universe_file,
                 "--seed", "2026-10-22", "--n", "2", "--db", db)
sample_id = sample.get("sample_id")
check("sample id shape", isinstance(sample_id, str)
      and sample_id.startswith("smp-"), sample_id)
check("sample has 2 coins", len(sample.get("coins") or []) == 2, sample)
check("sample coins are from the universe",
      set(sample.get("coins") or []) <= {"BTC", "ETH", "SOL", "ADA"}, sample)
again = ok_json("sample", "create", "--universe-file", universe_file,
                "--seed", "2026-10-22", "--n", "2", "--db", db)
check("repeat returns the same sample id", again.get("sample_id") == sample_id,
      f"{sample_id} -> {again.get('sample_id')}")
check("repeat returns the same coins", again.get("coins") == sample.get("coins"))

print("\n=== run start -> finish -> show -> list ===")
config_file = write_json(os.path.join(tmp, "config.json"),
                         {"version": "cli-cfg-1", "window": 20})
assumptions_file = write_json(os.path.join(tmp, "assumptions.json"),
                              {"fee_bps": 5, "slippage_bps": 1})
outputs_file = write_json(os.path.join(tmp, "outputs.json"),
                          {"sharpe": 1.2, "trades": 3})
artifacts_file = write_json(os.path.join(tmp, "artifacts.json"),
                            [{"name": "equity", "path": "runs/equity.csv"}])


def start_run():
    return ok_json(
        "run", "start", "--kind", "benchmark",
        "--snapshot", synced["snapshot_id"], "--sample", sample_id,
        "--config-file", config_file,
        "--assumptions-file", assumptions_file,
        "--label", "cli-smoke", "--db", db).get("run_id")


run_id = start_run()
check("run id shape", isinstance(run_id, str) and run_id.startswith("run-")
      and len(run_id) == 36, run_id)

shown = ok_json("run", "show", "--run-id", run_id, "--db", db)
check("started run visible", shown.get("status") == "started", shown.get("status"))
check("started run has one event", len(shown.get("events") or []) == 1)
check("config version recorded", shown.get("config_version") == "cli-cfg-1",
      shown.get("config_version"))
check("config file parsed", shown.get("config") ==
      {"version": "cli-cfg-1", "window": 20}, shown.get("config"))
check("assumptions file parsed", shown.get("assumptions") ==
      {"fee_bps": 5, "slippage_bps": 1}, shown.get("assumptions"))
check("label recorded", shown.get("label") == "cli-smoke", shown.get("label"))

finished = ok_json("run", "finish", "--run-id", run_id,
                   "--status", "completed", "--outputs-file", outputs_file,
                   "--artifacts-file", artifacts_file, "--db", db)
check("finish status", finished.get("status") == "completed", finished)
check("finish outputs parsed", finished.get("outputs") ==
      {"sharpe": 1.2, "trades": 3}, finished.get("outputs"))
check("finish artifacts parsed", finished.get("artifacts") ==
      [{"name": "equity", "path": "runs/equity.csv"}],
      finished.get("artifacts"))

shown = ok_json("run", "show", "--run-id", run_id, "--db", db)
check("completed status visible", shown.get("status") == "completed")
check("two events retained", len(shown.get("events") or []) == 2)

listed = ok_json("run", "list", "--db", db)
check("one run listed", [r.get("run_id") for r in listed.get("runs", [])]
      == [run_id], listed)

print("\n=== failed and interrupted runs are listed and not eligible ===")
failed_id = start_run()
failed = ok_json("run", "finish", "--run-id", failed_id,
                 "--status", "failed", "--error", "boom", "--db", db)
check("failed finish", failed.get("status") == "failed", failed)
check("failed error retained", failed.get("error") == "boom", failed)

interrupted_id = start_run()
interrupted = ok_json("run", "finish", "--run-id", interrupted_id,
                      "--status", "interrupted", "--error", "ctrl-c", "--db", db)
check("interrupted finish", interrupted.get("status") == "interrupted",
      interrupted)
check("interrupted error retained", interrupted.get("error") == "ctrl-c",
      interrupted)

listed = ok_json("run", "list", "--db", db)
by_id = {r.get("run_id"): r.get("status") for r in listed.get("runs", [])}
check("all runs listed", by_id == {run_id: "completed", failed_id: "failed",
                                   interrupted_id: "interrupted"}, by_id)
only_failed = ok_json("run", "list", "--status", "failed", "--db", db)
check("status filter", [r.get("run_id") for r in only_failed.get("runs", [])]
      == [failed_id], only_failed)
by_kind = ok_json("run", "list", "--kind", "benchmark", "--db", db)
check("kind filter", {r.get("run_id") for r in by_kind.get("runs", [])}
      == {run_id, failed_id, interrupted_id}, by_kind)
check("completed benchmark eligible",
      runs.benchmark_eligible(db, run_id) is True)
check("failed benchmark not eligible",
      runs.benchmark_eligible(db, failed_id) is False)
check("interrupted benchmark not eligible",
      runs.benchmark_eligible(db, interrupted_id) is False)

print("\n=== status counts after runs ===")
view = ok_json("status", "--db", db)
check("run counts by status",
      view.get("runs") == {"started": 0, "completed": 1, "failed": 1,
                           "interrupted": 1}, view.get("runs"))
check("benchmark_completed counts only the completed run",
      view.get("benchmark_completed") == 1, view.get("benchmark_completed"))
check("recent_runs lists every run",
      {r.get("run_id") for r in view.get("recent_runs", [])}
      == {run_id, failed_id, interrupted_id}, view.get("recent_runs"))

print("\n=== status tolerates a run with no event ===")
orphan_db = os.path.join(tmp, "orphan.db")
conn = store.connect(orphan_db)
conn.execute("INSERT INTO source_snapshot VALUES (?,?,?,?,?,?,?,?)",
             ("snap-o", "data/bars", "digest-o", 1, 1, 0, 0, 1))
conn.execute("INSERT INTO universe VALUES (?,?,?,?,?)",
             ("uni-o", "test", "{}", "def-o", 1))
conn.execute("INSERT INTO sample VALUES (?,?,?,?,?,?,?,?,?)",
             ("smp-o", "uni-o", "seed-o", 1, "sha256-rank-v1", None,
              '["BTC"]', "coins-o", 1))
conn.execute("INSERT INTO backtest_run VALUES (" + ",".join("?" * 18) + ")",
             ("run-orphan", 1, "exploratory", None, None, "snap-o", "smp-o",
              "uni-o", "seed-o", "rev", 0, "v1", "{}", "cfg-o", "{}", "asm-o",
              "{}", None))
conn.commit()
conn.close()
view = ok_json("status", "--db", orphan_db)
check("no-event run is counted under unknown",
      view.get("runs") == {"started": 0, "completed": 0, "failed": 0,
                           "interrupted": 0, "unknown": 1}, view.get("runs"))
check("no-event run has a null status in recent_runs",
      view.get("recent_runs", [{}])[0].get("status", "missing") is None,
      view.get("recent_runs"))

print("\n=== read-only commands leave the database directory untouched ===")
dir_before = directory_state(tmp)
db_before = fingerprint(db)
for label, args in (
    ("status", ("status", "--db", db)),
    ("run show", ("run", "show", "--run-id", run_id, "--db", db)),
    ("run list", ("run", "list", "--db", db)),
):
    ok_json(*args)
check("no sidecar or file changes from read-only commands",
      directory_state(tmp) == dir_before,
      f"{dir_before} -> {directory_state(tmp)}")
check("database bytes unchanged by read-only commands",
      fingerprint(db) == db_before)

print("\n=== status works in a read-only directory ===")
ro_dir = os.path.join(tmp, "readonly-dir")
os.makedirs(ro_dir)
ro_db = os.path.join(ro_dir, "research.db")
ok_json("sync", "--source", source, "--db", ro_db)
os.chmod(ro_dir, 0o500)
try:
    view = ok_json("status", "--db", ro_db)
    check("status succeeds in a read-only directory",
          view.get("schema_version") == 1, view)
    check("read-only directory keeps only the database",
          sorted(os.listdir(ro_dir)) == ["research.db"], os.listdir(ro_dir))
finally:
    os.chmod(ro_dir, 0o700)

print("\n=== read-only commands never create a database ===")
for label, args in (
    ("run show", ("run", "show", "--run-id", "run-missing")),
    ("run list", ("run", "list")),
):
    absent = os.path.join(tmp, f"absent-{label.replace(' ', '-')}.db")
    error_json(*args, "--db", absent)
    check(f"{label} on a missing db does not create it",
          not os.path.exists(absent))

print("\n=== import isolation ===")
forbidden = (
    "proto.quant.engine", "proto.quant.qstore", "proto.quant.signals",
    "proto.quant.selector", "proto.quant.__main__", "proto.app",
    "proto.daemon", "proto.service", "proto.store", "proto.measurement",
    "proto.watcher",
)
code = (
    "import proto.research, proto.research.__main__, sys; "
    f"forbidden = {forbidden!r}; "
    "found = [m for m in forbidden if m in sys.modules]; "
    "assert not found, found"
)
proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                      capture_output=True, text=True)
check("importing proto.research and its CLI keeps emission modules out",
      proc.returncode == 0, proc.stderr.strip()[:300])

print("\n=== CLI tests use only temp paths ===")
real_tmp = os.path.realpath(tmp)
repo_data = os.path.join(os.path.realpath(ROOT), "data")
check("every CLI path argument was recorded", bool(SEEN_PATHS), SEEN_PATHS)
check("every CLI path is under the test temp dir",
      all(p == real_tmp or p.startswith(real_tmp + os.sep)
          for p in SEEN_PATHS),
      sorted(p for p in SEEN_PATHS if not p.startswith(real_tmp + os.sep)))
check("no CLI path targets the repository data tree",
      not any(p == repo_data or p.startswith(repo_data + os.sep)
              for p in SEEN_PATHS), sorted(SEEN_PATHS))
check("the repository default database was never a CLI target",
      os.path.realpath(os.path.join(ROOT, store.DEFAULT_DB)) not in SEEN_PATHS)

print("\n" + ("ALL PASS" if not FAILURES
              else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
