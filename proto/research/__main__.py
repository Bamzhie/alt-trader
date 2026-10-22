"""Local operator CLI: python3 -m proto.research <command>

  sync            mirror data/bars into the research database (reads the
                  source, never writes it)
  status          read-only counts, latest snapshot/sync, run summary
  sample create   record a frozen deterministic sample from a universe file
  run start       insert a run identity and its `started` event before any
                  execution
  run finish      append a terminal event (completed|failed|interrupted)
  run show        read-only view of one run and its events
  run list        read-only list of runs, optionally filtered

Every command prints one JSON object on stdout. A failure prints
{"ok": false, "error": "..."} on stderr and exits 1.
"""

import argparse
import json
import sys

from . import runs, status, store, sync, universe


class CLIError(Exception):
    """Raised for unreadable input files and unknown commands."""


class _Parser(argparse.ArgumentParser):
    """Keep the JSON error contract even for usage errors."""

    def error(self, message):
        _emit({"ok": False, "error": message}, stream=sys.stderr)
        raise SystemExit(1)


def _load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise CLIError(f"cannot read {path}: {exc}") from exc


def _emit(obj, stream=sys.stdout):
    print(json.dumps(obj, sort_keys=True), file=stream)


def _database(parser):
    parser.add_argument("--db", default=store.DEFAULT_DB,
                        help="research database (default: %(default)s)")


def _parser():
    ap = _Parser(
        prog="python3 -m proto.research", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sync",
                       help="mirror the bars archive (read-only source)")
    p.add_argument("--source", required=True,
                   help="the <COIN>.csv.gz archive; it is only ever read")
    _database(p)

    _database(sub.add_parser("status", help="read-only database summary"))

    p = sub.add_parser("sample", help="frozen universe samples")
    sample_sub = p.add_subparsers(dest="sample_cmd", required=True)
    create = sample_sub.add_parser("create", help="record or replay a sample")
    create.add_argument("--universe-file", required=True)
    create.add_argument("--seed", required=True)
    create.add_argument("--n", type=int, required=True)
    create.add_argument("--label", default=None)
    _database(create)

    p = sub.add_parser("run", help="append-only backtest run registry")
    run_sub = p.add_subparsers(dest="run_cmd", required=True)
    start = run_sub.add_parser("start",
                              help="record the attempt before execution")
    start.add_argument("--kind", required=True, choices=runs.RUN_KINDS)
    start.add_argument("--snapshot", required=True)
    start.add_argument("--sample", required=True)
    start.add_argument("--config-file", required=True)
    start.add_argument("--assumptions-file", required=True)
    start.add_argument("--params-file", default=None)
    start.add_argument("--parent", default=None)
    start.add_argument("--label", default=None)
    start.add_argument("--notes", default=None)
    _database(start)

    finish = run_sub.add_parser("finish", help="append a terminal event")
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--status", required=True,
                        choices=runs.TERMINAL_STATUSES)
    finish.add_argument("--outputs-file", default=None)
    finish.add_argument("--error", default=None)
    finish.add_argument("--artifacts-file", default=None)
    _database(finish)

    show = run_sub.add_parser("show", help="view one run (read-only)")
    show.add_argument("--run-id", required=True)
    _database(show)

    listing = run_sub.add_parser("list", help="list runs (read-only)")
    listing.add_argument("--status", default=None)
    listing.add_argument("--kind", default=None)
    _database(listing)
    return ap


def _run_command(a):
    if a.run_cmd == "start":
        return {"run_id": runs.start_run(
            a.db, kind=a.kind, snapshot_id=a.snapshot, sample_id=a.sample,
            config=_load_json(a.config_file),
            assumptions=_load_json(a.assumptions_file),
            params=_load_json(a.params_file) if a.params_file else None,
            parent_run_id=a.parent, label=a.label, notes=a.notes)}
    if a.run_cmd == "finish":
        return runs.finish_run(
            a.db, a.run_id, status=a.status,
            outputs=_load_json(a.outputs_file) if a.outputs_file else None,
            error=a.error,
            artifacts=(_load_json(a.artifacts_file) if a.artifacts_file
                       else None))
    if a.run_cmd == "show":
        return runs.run_status(a.db, a.run_id)
    return {"runs": runs.list_runs(a.db, status=a.status, kind=a.kind)}


def main(argv=None):
    a = _parser().parse_args(argv)
    try:
        if a.cmd == "sync":
            result = sync.sync_bars(a.source, a.db)
        elif a.cmd == "status":
            result = status.db_status(a.db)
        elif a.cmd == "sample":
            result = universe.record_sample(
                a.db, a.universe_file, a.seed, a.n, label=a.label)
        elif a.cmd == "run":
            result = _run_command(a)
        else:  # pragma: no cover - argparse rejects an unknown command
            raise CLIError(f"unknown command: {a.cmd}")
    except Exception as exc:
        _emit({"ok": False, "error": str(exc) or exc.__class__.__name__},
              stream=sys.stderr)
        return 1
    _emit({"ok": True, **result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
