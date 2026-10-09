"""`python3 -m gui` — ALT RADAR desktop GUI entry point.

Import-safe: nothing runs at import time (no Tk window, no argparse side
effects). Read-only — no orders, no keys.
"""

import sys


def build_parser():
    import argparse
    ap = argparse.ArgumentParser(
        prog="gui",
        description="ALT RADAR desktop scanner — READ-ONLY, places no orders "
                    "and holds no keys.")
    ap.add_argument("--stake", type=float, default=0.10,
                    help="USDT stake per trade (must be > 0)")
    ap.add_argument("--coins", type=int, default=150,
                    help="universe budget / scan size (10-581, default 150)")
    ap.add_argument("--interval", type=int, default=60,
                    help="auto-scan interval in seconds (>= 15)")
    ap.add_argument("--db", default="data/signals.db",
                    help="signal/outcome sqlite database")
    ap.add_argument("--log-threshold", type=float, default=24.0,
                    help="flag threshold, 0-100")
    ap.add_argument("--no-auto-scan", action="store_true",
                    help="start with auto-scan paused (Scan now still works)")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    from . import model
    from .app import RadarGUI

    # Validate every operator input at the edge: bad CLI input exits with a
    # message instead of opening a broken window (the in-GUI inputs show the
    # same messages in the status bar, never a crash).
    try:
        stake = model.validate_stake(args.stake)
        coins = model.validate_coins(args.coins)
        interval = model.validate_interval(args.interval)
        threshold = model.validate_threshold(args.log_threshold)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    gui = RadarGUI(stake=stake, coins=coins, interval=interval, db=args.db,
                   log_threshold=threshold,
                   auto_scan=not args.no_auto_scan)
    gui.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
