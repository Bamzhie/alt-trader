"""Local inspection CLI and read-only status view (Task 5).

Self-checking script: python3 tests/test_research_cli.py

Covers: argparse help, the JSON error contract, `status` on a missing database
(exit 1, file not created), sync -> status with matching counts and an
unchanged database fingerprint, idempotent `sample create`, the
start/finish/show/list run round-trip, status counts and recent runs, the
read-only commands never creating a database, and the import isolation
boundary. Every fixture lives under tempfile.mkdtemp(); the repository
`data/` tree is never used.
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


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "proto.research", *args],
        cwd=ROOT, capture_output=True, text=True)


def ok_json(*args):
    """Run a command, require exit 0 and one JSON object on stdout."""
    label = " ".join(args)
    proc = run_cli(*args)
    check(f"exit 0: {label}", proc.returncode == 0,
          (proc.stderr or proc.stdout).strip()[:300])
    try:
        return json.loads(proc.stdout)
    except ValueError:
        check(f"one JSON object: {label}", False, repr(proc.stdout)[:300])
        return {}


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


tmp = tempfile.mkdtemp()
source = os.path.join(tmp, "bars")
os.makedirs(source)
db = os.path.join(tmp, "research.db")
default_db = os.path.join(ROOT, store.DEFAULT_DB)
default_db_before = os.path.exists(default_db)

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

print("\n=== status refuses a missing database without creating it ===")
missing = os.path.join(tmp, "absent.db")
err = error_json("status", "--db", missing)
check("error object shape", err.get("ok") is False
      and isinstance(err.get("error"), str) and err["error"], err)
check("missing database not created", not os.path.exists(missing))

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

started = ok_json(
    "run", "start", "--kind", "benchmark",
    "--snapshot", synced["snapshot_id"], "--sample", sample_id,
    "--config-file", config_file, "--assumptions-file", assumptions_file,
    "--label", "cli-smoke", "--db", db)
run_id = started.get("run_id")
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

print("\n=== failed run is listed and not eligible ===")
failed_start = ok_json(
    "run", "start", "--kind", "benchmark",
    "--snapshot", synced["snapshot_id"], "--sample", sample_id,
    "--config-file", config_file, "--assumptions-file", assumptions_file,
    "--db", db)
failed_id = failed_start.get("run_id")
failed = ok_json("run", "finish", "--run-id", failed_id,
                 "--status", "failed", "--error", "boom", "--db", db)
check("failed finish", failed.get("status") == "failed", failed)
check("failed error retained", failed.get("error") == "boom", failed)

listed = ok_json("run", "list", "--db", db)
by_id = {r.get("run_id"): r.get("status") for r in listed.get("runs", [])}
check("both runs listed", by_id == {run_id: "completed", failed_id: "failed"},
      by_id)
only_failed = ok_json("run", "list", "--status", "failed", "--db", db)
check("status filter", [r.get("run_id") for r in only_failed.get("runs", [])]
      == [failed_id], only_failed)
by_kind = ok_json("run", "list", "--kind", "benchmark", "--db", db)
check("kind filter", {r.get("run_id") for r in by_kind.get("runs", [])}
      == {run_id, failed_id}, by_kind)
check("completed benchmark eligible",
      runs.benchmark_eligible(db, run_id) is True)
check("failed benchmark not eligible",
      runs.benchmark_eligible(db, failed_id) is False)

print("\n=== status counts after runs ===")
view = ok_json("status", "--db", db)
check("run counts by status",
      view.get("runs") == {"started": 0, "completed": 1, "failed": 1,
                           "interrupted": 0}, view.get("runs"))
check("benchmark_completed counts only the completed run",
      view.get("benchmark_completed") == 1, view.get("benchmark_completed"))
check("recent_runs lists both runs",
      {r.get("run_id") for r in view.get("recent_runs", [])}
      == {run_id, failed_id}, view.get("recent_runs"))

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
forbidden = ("proto.quant.engine", "proto.quant.qstore", "proto.quant.signals",
             "proto.daemon", "proto.service")
code = (
    "import proto.research, sys; "
    f"forbidden = {forbidden!r}; "
    "found = [m for m in forbidden if m in sys.modules]; "
    "assert not found, found"
)
proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                      capture_output=True, text=True)
check("import proto.research keeps emission modules out",
      proc.returncode == 0, proc.stderr.strip()[:300])

print("\n=== repository data tree untouched ===")
check("default research db state unchanged",
      os.path.exists(default_db) == default_db_before, default_db)

print("\n" + ("ALL PASS" if not FAILURES
              else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
