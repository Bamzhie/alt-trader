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
   timestamp as provenance. Every snapshot also records a manifest of the exact
   file version used for every coin, so a run's snapshot can be reconstructed
   after later syncs. A file that changes while it is read aborts the sync
   instead of installing partial data.
2. **Sample** — record a frozen, reproducible draw from a predeclared universe
   file. The universe is the definition; the substrate infers no eligibility
   from bars or liquidity.
3. **Register** — insert a backtest attempt's identity and its `started` event
   *before* any execution, then append terminal events. Nothing is updated or
   deleted.
4. **Inspect** — print the current state of the mirror and registry, locally,
   without writing anything.

## Schema and invariants (version 2)

| Table | Purpose | Mutable? |
| --- | --- | --- |
| `source_snapshot` | one source state: monotonic `seq`, digest, coins, bars, span | no |
| `snapshot_file` | manifest: the exact file version used for each coin in a snapshot | no |
| `sync_run` | each sync attempt and its terminal status | yes (terminal status only) |
| `bar` | mirrored OHLCV rows, primary key `(coin, ts)` | no |
| `bar_file` | per-file provenance, primary key `(coin, sha256)` | no |
| `universe` | canonical universe definition and its SHA-256 | no |
| `sample` | frozen sample: universe, seed, n, method, coins, hashes | no |
| `sample_coin` | ranked coins of a sample | no |
| `backtest_run` | run identity: snapshot, sample, config/assumption hashes, code revision | no |
| `run_event` | appended status chain (`started` → one terminal event) | no |

Invariants enforced by the database, not by convention:

- `PRAGMA user_version = 2`; the schema is created only by a writable
  `store.connect`. A database with any other version is refused, never
  migrated; there is no v1 to v2 upgrade path because none was ever created.
- Writable connections use `PRAGMA journal_mode=DELETE` (the SQLite default).
  A read-only open therefore needs no `-wal`/`-shm` sidecar, creates no
  `-journal` file, and works when the database directory is not writable.
- Every table except `sync_run` is append-only: `UPDATE` and `DELETE` raise
  `sqlite3.IntegrityError` containing `append-only`.
- A terminal run event cannot be appended to a run that already has one.
- The snapshot id is the SHA-256 of the sorted `coin:sha256` lines of the
  source; syncing the same source again returns the same snapshot and inserts
  `bars_inserted = 0`.
- Every snapshot has one `snapshot_file` row per coin present at that moment,
  with the file's SHA-256, size, mtime, row count, and first/last timestamp.
  `source_snapshot.coins` and `.bars` are the sums over that manifest, so they
  never include retained rows from a file that has since disappeared.
- Snapshots are ordered by `seq`. A bar row records the snapshot that first
  inserted it, so `load_snapshot_bars` returns exactly the rows that snapshot
  used, even after later syncs have added more rows.
- Later syncs: a changed file must contain every previously mirrored timestamp
  for its coin (new rows are appended); an added file creates a new manifest
  entry; a disappeared file is retained in the mirror but omitted from newer
  manifests and from newer `coins`/`bars` counts. A source that drops a mirrored
  row or rewrites a value is refused, and so is a source that returns to an
  older version once the mirror has moved past it, because the mirror could no
  longer prove exact membership. Returning to a recorded state while the mirror
  still matches it is accepted as a replay of that snapshot.
- Symlinked `*.csv.gz` entries in the source are refused, and every read opens
  the file with `O_NOFOLLOW`; a link swapped in after enumeration cannot
  redirect a read outside the permitted tree.
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
# benchmark_eligible, benchmark_gate, recent_runs (last 10). A missing database
# exits 1 and is not created.
python3 -m proto.research status --db data/research/altradar-research.db

# Reconstruct one snapshot: its file manifest, optionally the exact bars it
# used for one coin (each bar is [ts, open, high, low, close, vol, amount];
# read-only)
python3 -m proto.research snapshot show --snapshot <SNAPSHOT_ID> \
  --db data/research/altradar-research.db
python3 -m proto.research snapshot show --snapshot <SNAPSHOT_ID> --coin BTC \
  --db data/research/altradar-research.db

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
  the production archive into the default database. It refuses symlinked
  `*.csv.gz` entries and a source that lost a previously mirrored row. A
  source with no `*.csv.gz` files at all is refused, so a total disappearance
  cannot be recorded as a snapshot.
- `run start` requires a `config` object with a non-empty `version`; config and
  assumptions are stored as canonical JSON with SHA-256 hashes. `--params-file`
  is optional (default `{}`), and `--parent <RUN_ID>` links a rerun.
- `status`, `snapshot show`, `run show`, and `run list` open SQLite with
  `mode=ro` and `PRAGMA query_only=ON`. They create no files (no database, no
  journal, no WAL sidecar), change neither the database bytes nor its directory,
  and work in a read-only directory.
- `benchmark_completed` counts benchmark attempts whose latest event is
  `completed` and whose chain has no failure. It is a raw count, not a result
  and not an eligibility claim.
- `benchmark_eligible` is **0 in this slice**, and `benchmark_gate` names the
  reason: the sampling design is not approved (see below). A run is eligible
  only when it is a benchmark, ends `completed`, has no failed/interrupted
  event, and the sampling gate is open. The gate is global; per-sample
  approval, and how a sample would be checked against a design, is not
  implemented and remains undecided.
- A run row inserted outside the registry has no event; `status` counts it
  under `runs.unknown` and its `recent_runs` entry has `status: null`.
- Recovery: a `started` run or sync left by a crash is not eligible for
  anything. Close a run with `run finish --run-id <ID> --status interrupted
  --error "operator: process died"`; a killed sync stays `started` and a new
  sync appends a fresh record.

## Deliberately absent

This package contains none of the following, by design:

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

## Benchmark gate: sampling quality is not approved

Reproducibility is not representativeness. `sha256-rank-v1` proves that a
sample was drawn from a predeclared universe with a recorded seed and can be
replayed exactly; it does **not** prove that the universe or the draw covers
the market. Nothing in this slice measures liquidity, volatility, or market
conditions, so no run is benchmark-eligible:

- `runs.eligibility` returns `{"eligible": false, "reason": ...}` and
  `runs.benchmark_eligible` returns `false` for every run.
- `status.benchmark_eligible` is `0` and `status.benchmark_gate` carries the
  `universe.SAMPLE_DESIGN_BLOCKED` reason.
- Do not claim the 75% benchmark is ready, evaluable, or passed. The gate is
  code, not just prose: a completed benchmark run stays ineligible.

Still to be decided before the gate can open (no defaults are assumed here):
the liquidity metric and window, the volatility metric and window, the
market-condition coverage buckets (e.g. trend/range/volatility regimes), any
minimum sample size, whether the universe itself or the draw is stratified,
the approval/review step that records the design, and how development and
unseen evaluation windows are fixed. Replacing
`universe.SAMPLE_DESIGN_BLOCKED` with `None` is the explicit, reviewed step
that opens eligibility; it must arrive with that design, not before.

## Owner blocker: the running service emits independently

The production service (`altradar.service`) is already running with
`--quant-shadow` and `--resolve-every`, and emits its own events on its own
schedule, from its own code, independent of this substrate. It may still emit
legacy rule events. It was **not** restarted, reconfigured, or touched by this
work, and this package neither reads from nor writes to it or to
`quant.db`/`signals.db`.

What an operator must not claim while that process is running:

- **The application is not rule-free.** This package is rule-free; the running
  deployment is not. Its live emissions are not research-substrate output, are
  not in this append-only registry, and do not disappear because the substrate
  has no rules.
- Live emissions are neither evidence for nor against any substrate result.
- A global "no rule emits a candidate signal" claim requires the owner to stop
  or pause the emitting service and to say when that took effect. The scanner
  loop is what emits legacy rule events, and it runs by default; if the service
  is paused in place, disabling the scanner loop (`--no-scan`, collector and
  resolver only) is the relevant flag. `--quant-shadow` only toggles the quant
  shadow watcher, so turning it off is not by itself enough. This is an
  owner-controlled operational step outside this slice; nothing here performs
  it.
- Deploying or restarting the service to use this substrate is a separate,
  owner-authorized action. A restart would recycle the 24/7 collector; until
  the owner approves it, the running process and this substrate must be
  treated as separate systems.

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
production database or the `data/bars/` archive. `tests/test_research_sync.py`
proves a snapshot reconstructs its exact bars after later syncs (growth, a new
file, a disappeared file), and that symlinked files, dropped rows, and a
revert after growth are refused. `tests/test_research_store.py` asserts the DELETE journal
mode, that a read-only open creates no sidecar, and that a v1 database is
refused rather than migrated. `tests/test_research_cli.py` records every CLI
path argument and asserts they are all inside its temp directory, that no
read-only command changes the database directory, and that `status` works in a
read-only directory. It also asserts that importing `proto.research` and
`proto.research.__main__` does not load `proto.quant.engine`,
`proto.quant.qstore`, `proto.quant.signals`, `proto.quant.selector`,
`proto.quant.__main__`, `proto.app`, `proto.daemon`, `proto.service`,
`proto.store`, `proto.measurement`, or `proto.watcher`.

For a whole-slice no-write check, hash the protected paths before and after
(never run a production sync). The live collector rewrites `data/bars/` on its
own schedule and the service writes `data/signals.db` continuously, so compare
`data/bars/` within a window with no collection cycle, and expect
`data/signals.db` to differ while the service runs: attribute those changes to
the service by observing them with no research command running:

```bash
sha256sum data/quant.db data/signals.db data/bars/*.csv.gz \
  > /tmp/opencode/research-protected-before.sha
# ... run the tests and inspect git status ...
sha256sum -c /tmp/opencode/research-protected-before.sha
git status --short   # no data/ entry may appear
```
