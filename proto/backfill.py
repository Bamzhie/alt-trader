"""Historical bar backfill: months of 5m history into the collector files.

MEXC's 2000-bar figure is a per-request cap, not a history limit — start/end
select any window, so history pages backward to listing. Bars merge into the
same data/bars/*.csv.gz files the live collector writes (dedupe by ts), so
no schema change, no new DB, no resolver/collector changes: backfilled bars
are indistinguishable from collected ones, and reruns are resume-safe.

What history gives you: price/volume indicators recomputed at any past bar,
earlyness/volume-expansion on real regimes, outcome maturation (7d needs
2016 bars), regime coverage for the v2-vs-v3 comparison.
What it can NOT give you: order-book snapshots (never archived) so full v2
scores cannot be replayed historically; OI older than ~8d (Bybit endpoint
limit); funding history (not pulled).

Usage: python3 -m proto.backfill [--days 90] [--coins 20] [--workers 8]
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import collector as colmod
from . import mexc
from .mexc import INTERVALS
from .universe import build_universe

INTERVAL = "5m"
CHUNK = 2000          # venue per-request cap; one chunk ≈ 6.9 days of 5m
MAX_ERRORS_LOGGED = 20


def backfill_coin(sym, coin, oldest_ts, data_dir="data/bars"):
    """Page 5m history back to oldest_ts. Returns (new_bars, oldest_bar_ts).

    Walks end backward in CHUNK-sized steps; each chunk validated by
    mexc.klines (shape, order, invariants, closed-bar filter) and merged
    via append_bars (dedupe). Stops early when a chunk returns nothing older
    than already stored (listing reached) or on repeated venue errors.
    """
    secs = INTERVALS[INTERVAL][1]
    end = int(time.time())
    total_new = 0
    oldest_seen = None
    idle_rounds = 0
    while True:
        try:
            bars = mexc.klines(sym, INTERVAL, limit=CHUNK, end=end)
        except Exception as e:
            idle_rounds += 1
            if idle_rounds >= 3:
                return total_new, oldest_seen, f"{type(e).__name__}"
            time.sleep(2)
            continue
        idle_rounds = 0
        if not bars:
            break
        if oldest_seen is None or bars[0]["ts"] < oldest_seen:
            oldest_seen = bars[0]["ts"]
        try:
            total_new += colmod.append_bars(data_dir, coin, bars)
        except Exception as e:
            return total_new, oldest_seen, f"store: {type(e).__name__}"
        if bars[0]["ts"] <= oldest_ts:
            break
        end = bars[0]["ts"] - secs
        if end <= oldest_ts - CHUNK * secs:
            break
    return total_new, oldest_seen, None


def main():
    ap = argparse.ArgumentParser(description="backfill 5m bar history")
    ap.add_argument("--days", type=float, default=90.0)
    ap.add_argument("--coins", type=int, default=None,
                    help="limit universe (testing); default: full universe")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--data-dir", default="data/bars")
    args = ap.parse_args()

    uni = build_universe(float("inf"), budget=None)
    if args.coins:
        uni = uni[:args.coins]
    oldest_ts = int(time.time()) - int(args.days * 86400)
    print(f"backfill: {len(uni)} coins x {args.days:g}d "
          f"({args.workers} workers)", flush=True)
    t0 = time.time()
    done = new_total = 0
    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(backfill_coin, sym, coin, oldest_ts, args.data_dir):
                (sym, coin) for sym, coin in uni}
        for f in as_completed(futs):
            sym, coin = futs[f]
            try:
                new_bars, oldest, err = f.result()
            except Exception as e:  # never let one coin kill the run
                new_bars, oldest, err = 0, None, f"{type(e).__name__}: {e}"
            done += 1
            new_total += new_bars
            if err and len(errors) < MAX_ERRORS_LOGGED:
                errors.append(f"{coin}: {err}")
            if done % 25 == 0 or done == len(uni):
                print(f"  [{done}/{len(uni)}] +{new_total} bars "
                      f"{time.time()-t0:.0f}s", flush=True)
    print(f"done: {done} coins, {new_total} new bars in {time.time()-t0:.0f}s",
          flush=True)
    for e in errors:
        print(f"  ERR {e}", flush=True)


if __name__ == "__main__":
    main()
