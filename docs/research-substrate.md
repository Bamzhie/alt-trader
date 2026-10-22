# Research substrate (overhaul phase 1)

A local, standard-library-only store for research inputs and backtest attempts.
It mirrors the bars archive, freezes deterministic coin samples, and registers
every run before it executes. It is an evidence recorder and an inspection
surface, not a strategy engine.

- Default database: `data/research/altradar-research.db`
- Source archive: `data/bars/` — read only, never written, renamed, sorted, or
  backfilled
- Refused paths: any database whose basename starts with `quant.db` or
  `signals.db` (including `-wal`, `-shm`, and `-journal` sidecars), and any
  path under `data/bars/`, `data/prices/`, or `data/funding*/`
- Python 3.10+ standard library only; no new dependencies

## What it is for

1. **Mirror** — copy every `<COIN>.csv.gz` bar file into one local SQLite
   database, keeping each file's SHA-256, size, mtime, row count, and first/last
   timestamp as provenance. A file that changes while it is read aborts the
   sync instead of installing partial data.
2. **Sample** — record a frozen, reproducible draw from a predeclared universe
   file. The universe is the definition; the substrate infers no eligibility
   from bars or liquidity.
3. **Register** — insert a backtest attempt's identity and its `started` event
   *before* any execution, then append terminal events. Nothing is updated or
   deleted.
4. **Inspect** — print the current state of the mirror and registry, locally,
   without writing anything.

## Schema and invariants (version 1)

| Table | Purpose | Mutable? |
| --- | --- | --- |
| `source_snapshot` | one mirrored source state: digest, coins, bars, span | no |
| `sync_run` | each sync attempt and its terminal status | yes (terminal status only) |
| `bar` | mirrored OHLCV rows, primary key `(coin, ts)` | no |
| `bar_file` | per-file provenance, primary key `(coin, sha256)` | no |
| `universe` | canonical universe definition and its SHA-256 | no |
| `sample` | frozen sample: universe, seed, n, method, coins, hashes | no |
| `sample_coin` | ranked coins of a sample | no |
| `backtest_run` | run identity: snapshot, sample, config/assumption hashes, code revision | no |
| `run_event` | appended status chain (`started` → one terminal event) | no |

Invariants enforced by the database, not by convention:

- `PRAGMA user_version = 1`; the schema is created only by a writable
  `store.connect`.
- Writable connections use `PRAGMA journal_mode=DELETE` (the SQLite default).
  A read-only open therefore needs no `-wal`/`-shm` sidecar, creates no
  `-journal` file, and works when the database directory is not writable.
- Every table except `sync_run` is append-only: `UPDATE` and `DELETE` raise
  `sqlite3.IntegrityError` containing `append-only`.
- A terminal run event cannot be appended to a run that already has one, and
  `failed`/`interrupted` runs are never benchmark-eligible, even if a later
  event claims completion.
- The snapshot id is the SHA-256 of the sorted `coin:sha256` lines of the
  source; syncing the same source again returns the same snapshot and inserts
  `bars_inserted = 0`.
- A conflicting duplicate `(coin, ts)` aborts the sync; the mirrored row is
  never overwritten.
- Sample ids are `sha256(universe_id|seed|n|method)`; replaying the same
  request returns the stored coins and never redraws.
- A failed sync or run attempt is recorded, not erased.

## Commands

Run from the repository root. Every command prints one JSON object on stdout.
A failure prints `{"ok": false, "error": "..."}` on stderr and exits `1`.

```bash
# Mirror the archive (the only command that reads data/bars; it never writes it)
# PHASE 1: do not run this against data/bars yet. The owner has not authorized
# the live-archive import; use a copied fixture directory for now. The tool is
# able to perform the import once that is authorized, and `--source` refuses
# any `data/` path other than `data/bars`.
python3 -m proto.research sync --source data/bars --db data/research/altradar-research.db

# Read-only state: schema_version, db, bars{coins,rows,min_ts,max_ts},
# latest_snapshot, latest_sync, runs{counts by status}, benchmark_completed,
# recent_runs (last 10). A missing database exits 1 and is not created.
python3 -m proto.research status --db data/research/altradar-research.db

# Freeze a sample from a predeclared universe file
python3 -m proto.research sample create \
  --universe-file universe.json --seed 2026-10-22 --n 20 --label first \
  --db data/research/altradar-research.db

# Record the attempt BEFORE executing anything; returns {"ok": true, "run_id": ...}
python3 -m proto.research run start \
  --kind benchmark --snapshot <SNAPSHOT_ID> --sample <SAMPLE_ID> \
  --config-file config.json --assumptions-file assumptions.json \
  --params-file params.json --label first --notes "attempt 1" \
  --db data/research/altradar-research.db

# Append the terminal event
python3 -m proto.research run finish --run-id <RUN_ID> --status completed \
  --outputs-file outputs.json --artifacts-file artifacts.json \
  --db data/research/altradar-research.db
python3 -m proto.research run finish --run-id <RUN_ID> --status failed \
  --error "reason" --db data/research/altradar-research.db

# Read-only inspection of the registry
python3 -m proto.research run show --run-id <RUN_ID> --db data/research/altradar-research.db
python3 -m proto.research run list --status failed --kind benchmark \
  --db data/research/altradar-research.db
```

Notes:

- `sync` requires `--source`, so a bare invocation cannot accidentally ingest
  the production archive into the default database.
- `run start` requires a `config` object with a non-empty `version`; config and
  assumptions are stored as canonical JSON with SHA-256 hashes. `--params-file`
  is optional (default `{}`), and `--parent <RUN_ID>` links a rerun.
- `status`, `run show`, and `run list` open SQLite with `mode=ro` and
  `PRAGMA query_only=ON`. They create no files (no database, no journal, no
  WAL sidecar), change neither the database bytes nor its directory, and work
  in a read-only directory.
- `benchmark_completed` is a **count of eligible attempts** (kind `benchmark`,
  latest event `completed`, no failed/interrupted event). It is not a
  performance result and carries no benchmark claim.
- A run row inserted outside the registry has no event; `status` counts it
  under `runs.unknown` and its `recent_runs` entry has `status: null`.
- Recovery: a `started` run or sync left by a crash is not eligible for
  anything. Close a run with `run finish --run-id <ID> --status interrupted
  --error "operator: process died"`; a killed sync stays `started` and a new
  sync appends a fresh record.

## Deliberately absent

This slice contains none of the following, by design:

- no trading rules, candidate generator, scanner, or signal-selection logic
- no ACC-POC detector
- no benchmark defaults: no outcome horizon, pass criterion, tie/gap policy,
  fees, slippage, leverage, or cross-margin assumption
- no benchmark result calculation, no 75% claim, no edge claim
- no scheduler, cron entry, systemd unit, or background loop
- no HTTP route, API handler, frontend, or TUI
- no service deployment or restart; no live-archive import performed or
  authorized in this phase (the tool can perform it once authorized); no live
  backtest
- no writes anywhere under `data/` except the research database created by an
  explicit `sync`/`sample`/`run` command

Run assumptions and outputs are opaque caller-supplied JSON. The substrate
stores them and reports status; it does not interpret them.

## Owner blocker: the running service emits independently

The production service (`altradar.service`) is already running and emits its
own events on its own schedule, from its own code, independent of this
substrate. It was **not** restarted, reconfigured, or touched by this work, and
this package neither reads from nor writes to it or to `quant.db`/`signals.db`.

Consequences an operator must keep in mind:

- Live emissions during this phase are **not** research-substrate output and
  are not covered by its append-only registry.
- Deploying or restarting the service to use this substrate is a separate,
  owner-authorized action. Nothing here performs it.
- A restart would recycle the 24/7 collector; until the owner approves it, the
  running process and this substrate must be treated as separate systems.

## Verification

```bash
python3 tests/test_research_store.py
python3 tests/test_research_sync.py
python3 tests/test_research_sampling.py
python3 tests/test_research_runs.py
python3 tests/test_research_cli.py
python3 tests/run_all.py
```

All tests build fixtures under `tempfile.mkdtemp()`; none writes to or opens a
production database or the `data/bars/` archive. `tests/test_research_store.py`
asserts the DELETE journal mode and that a read-only open creates no sidecar;
`tests/test_research_cli.py` records every CLI path argument and asserts they
are all inside its temp directory, that no read-only command changes the
database directory, and that `status` works in a read-only directory. It also
asserts that importing `proto.research` and `proto.research.__main__` does not
load `proto.quant.engine`, `proto.quant.qstore`, `proto.quant.signals`,
`proto.daemon`, or `proto.service`.

For a whole-slice no-write check, hash the protected paths before and after
(never run a production sync):

```bash
sha256sum data/quant.db data/signals.db data/bars/*.csv.gz \
  > /tmp/opencode/research-protected-before.sha
# ... run the tests and inspect git status ...
sha256sum -c /tmp/opencode/research-protected-before.sha
git status --short   # no data/ entry may appear
```
