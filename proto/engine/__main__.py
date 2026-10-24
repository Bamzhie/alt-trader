"""CLI for proto.engine: python3 -m proto.engine <command>

Phase 1 commands:
  baseline   deterministic stride-grid baseline calibration (read-only)

This module does NOT write to data/ or create any database.
"""
import argparse
import json
import os
import sys

from .baseline import baseline_random_entry, _format_table, _compute_window_bounds
from . import baseline as B_mod
from proto.research import store


def _resolve_and_check_out(path):
    """Resolve the --out path and reject protected targets.

    Reuses proto.research.store.validate_db_path (realpath, so a symlink
    cannot hide a protected target): it refuses the live database names
    (sidecar prefixes included) and any data/<bars|prices|funding*> path.
    On top of that this guard refuses the research database's own directory
    and file name -- the engine never writes near the research store -- and
    ANY resolved destination under a `data` directory root (a `data` path
    component followed by at least one more component, at any depth): the
    data/ tree is collector-managed input, never engine output.

    Callers must run this BEFORE any data access or file open.
    """
    try:
        real = store.validate_db_path(path)
    except store.StoreError as exc:
        raise ValueError(str(exc)) from None
    parts = real.split(os.sep)
    # Defense in depth (subsumed by the data-root check below, but the
    # messages stay more specific for the research store's own paths).
    for i in range(len(parts) - 1):
        if parts[i] == "data" and parts[i + 1] == "research":
            raise ValueError(f"refusing --out path under the research database directory: {real}")
    if os.path.basename(real).startswith(os.path.basename(store.DEFAULT_DB)):
        raise ValueError(f"refusing --out path matching the research database: {real}")
    for i in range(len(parts) - 1):
        if parts[i] == "data":
            raise ValueError(f"refusing --out path under the data root: {real}")
    return real


def _emit(obj, stream=None):
    print(json.dumps(obj, sort_keys=True, indent=2, default=str),
          file=stream or sys.stdout)


def _baseline(args):
    # The --out guard runs FIRST: no data read, no bounds computation, no
    # file open may happen before the output path is proven safe, and a
    # refusal must create nothing.
    real_out = None
    if args.out:
        try:
            real_out = _resolve_and_check_out(args.out)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    data_dir = args.data
    leverages = tuple(int(x) for x in args.leverage.split(","))
    horizons_days = tuple(int(x) for x in args.horizon_days.split(","))

    # When --coins is given, use exactly that subset.
    # When --coins is omitted, let baseline_random_entry pick from the
    # liquid universe (uncapped by default; --max-coins is a diagnostic cap).
    coins = None
    if args.coins:
        all_coins = set(B_mod._list_coins(data_dir))
        coins = [c for c in args.coins.split(",") if c.strip() in all_coins]
        if not coins:
            _emit({"ok": False, "error": "no matching coins found in data dir"},
                  stream=sys.stderr)
            return 1

    # Window boundaries: BTC-anchored, pre-holdout = dev + validation.
    try:
        bounds = _compute_window_bounds(data_dir, args.window)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    result = baseline_random_entry(
        data_dir, coins,
        leverages=leverages,
        horizons_days=horizons_days,
        seed=args.seed,
        stride=args.stride,
        t_lo=bounds["t_lo"], t_hi=bounds["val_end_ts"],
        holdout_start_ts=bounds["holdout_start_ts"],
        min_qv24=args.min_qv24,
        max_coins=args.max_coins,
    )

    # Add metadata
    result["command"] = "baseline"
    result["data_dir"] = data_dir
    result["window"] = bounds["window"]
    result["window_bounds"] = bounds
    result["sampling"] = (f"deterministic stride-grid, every {args.stride} bars, "
                          "global phase, both sides")
    if bounds["window"] == "full":
        result["full_window_includes_holdout"] = True
    result["n_coins"] = len(result.get("coins", []))

    if real_out is not None:
        with open(real_out, "w") as fh:
            json.dump(result, fh, sort_keys=True, indent=2, default=str)
        print(f"Wrote baseline JSON to {real_out}", file=sys.stderr)

    print(_format_table(result), file=sys.stderr)
    _emit(result)
    return 0


def _parser():
    ap = argparse.ArgumentParser(
        prog="python3 -m proto.engine",
        description="Frozen benchmark engine for the alt-radar quant substrate.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("baseline",
                        help="deterministic stride-grid baseline calibration (read-only)",
                        description="Deterministic stride-grid baseline "
                                    "calibration (read-only); default window "
                                    "ends before the holdout; --window full "
                                    "includes it for calibration only.")
    p.add_argument("--data", required=True,
                    help="path to the bar archive (read-only)")
    p.add_argument("--leverage", default="10,20,25",
                    help="comma-separated leverages (default: %(default)s)")
    p.add_argument("--horizon-days", default="1,3,7,14",
                    help="comma-separated horizon days (default: %(default)s)")
    p.add_argument("--window", choices=("pre-holdout", "full"),
                    default="pre-holdout",
                    help="calendar window to measure; 'full' spans the entire "
                         "archive including the sealed holdout (calibration/"
                         "diagnostic only, never evidence) (default: %(default)s)")
    p.add_argument("--seed", type=int, default=1,
                    help="seed for pass-count simulation (default: %(default)s)")
    p.add_argument("--stride", type=int, default=12,
                    help="entry stride in bars on the global grid phase "
                         "(entries every <stride> bars, same phase for all "
                         "coins) (default: %(default)s)")
    p.add_argument("--min-qv24", type=float, default=None,
                    help="minimum 24h quote volume floor (default: from config)")
    p.add_argument("--coins", default=None,
                    help="comma-separated subset of coins (default: all liquid)")
    p.add_argument("--max-coins", type=int, default=None,
                    help="diagnostic cap on liquid coins to evaluate "
                         "(default: no cap — the full eligible universe)")
    p.add_argument("--out", default=None,
                    help="write JSON output to this path (never under data/)")
    p.set_defaults(func=_baseline)

    return ap


def main(argv=None):
    ap = _parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
