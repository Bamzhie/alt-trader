"""Historical volume/price-leg replay: does the VP leg have edge anywhere?

Full score replay is impossible historically (no archived order books), but
volume_price_component() needs only bars — so the 40%-weight leg replays
exactly over the backfilled months. Samples every 48 bars (4h) per coin with
60 bars of warmup; forward signed 4h/24h returns by lean sign; regimes from
trailing 7d BTC drift (up >+5%, down <-5%, else chop).

Usage: python3 -m proto.replay [--coins 60] [--step 48]
"""

import argparse
import os

from . import collector as colmod
from .scorer import volume_price_component

DATA_DIR = "data/bars"
WARMUP = 60
HISTORY = 200           # bars of context per sample (matches live depth)
HORIZONS = {"4h": 48, "24h": 288}
BTC_COIN = "BTC"


def read_bars(data_dir, coin):
    rows = colmod.read_bars(data_dir, coin)
    return sorted(rows, key=lambda b: b["ts"])


def regime_at(btc, ts):
    """up / down / chop / unknown from trailing 7d BTC drift ending at ts."""
    week = 7 * 86400
    closes = [(t, c) for t, c in btc if ts - week <= t <= ts]
    if len(closes) < 2:
        return "unknown"
    drift = (closes[-1][1] - closes[0][1]) / closes[0][1] * 100
    if drift > 5.0:
        return "up"
    if drift < -5.0:
        return "down"
    return "chop"


def replay_coin(bars, horizons=HORIZONS, step=48):
    """Yield (ts, lean, mag, {horizon: signed_ret|None}) through history."""
    n = len(bars)
    for t in range(WARMUP, n, step):
        window = bars[max(0, t - HISTORY):t]
        try:
            mag, lean, _ = volume_price_component(window)
        except Exception:
            continue
        if abs(lean) <= 0.15 or not bars[t - 1]["c"] > 0:
            continue
        rets = {}
        for h, need in horizons.items():
            if t + need < n and bars[t + need]["c"] > 0:
                r = ((bars[t + need]["c"] - bars[t - 1]["c"])
                     / bars[t - 1]["c"] * 100)
                rets[h] = r if lean > 0 else -r
            else:
                rets[h] = None
        yield bars[t - 1]["ts"], lean, mag, rets


def main():
    ap = argparse.ArgumentParser(description="VP-leg historical replay")
    ap.add_argument("--coins", type=int, default=60)
    ap.add_argument("--step", type=int, default=48)
    ap.add_argument("--data-dir", default=DATA_DIR)
    args = ap.parse_args()

    coins = sorted(
        f[:-7] for f in os.listdir(args.data_dir) if f.endswith(".csv.gz"))
    btc = [(b["ts"], b["c"]) for b in read_bars(args.data_dir, BTC_COIN)]
    if args.coins:
        pick = max(1, len(coins) // args.coins)
        coins = coins[::pick][:args.coins]
    print(f"replay: {len(coins)} coins, btc bars {len(btc)}", flush=True)

    acc = {}
    n_samples = 0
    for i, coin in enumerate(coins):
        try:
            bars = read_bars(args.data_dir, coin)
        except Exception:
            continue
        if len(bars) < WARMUP + max(HORIZONS.values()):
            continue
        for ts, lean, mag, rets in replay_coin(bars, step=args.step):
            reg = regime_at(btc, ts)
            for h, r in rets.items():
                if r is None:
                    continue
                key = (reg, "long" if lean > 0 else "short", h)
                a = acc.setdefault(key, [0, 0, 0.0])
                a[0] += 1
                a[1] += 1 if r > 0 else 0
                a[2] += r
                n_samples += 1
        if (i + 1) % 10 == 0 or i + 1 == len(coins):
            print(f"  [{i+1}/{len(coins)}] samples {n_samples}", flush=True)

    print(f"\nVP LEG REPLAY · {n_samples} samples · "
          f"signed returns by regime/side/horizon", flush=True)
    print(f"{'regime':<8}{'side':<7}{'hor':<5}{'n':>8}{'win%':>7}{'avg':>8}",
          flush=True)
    for (reg, side, h) in sorted(acc):
        n, w, s = acc[(reg, side, h)]
        print(f"{reg:<8}{side:<7}{h:<5}{n:>8}{100*w/n:>6.1f}%{s/n:>+7.2f}%",
              flush=True)


if __name__ == "__main__":
    main()
