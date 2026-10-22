"""Read-only context probe for the ROE feasibility report.

NOT shipped code and NOT part of the `proto.quant roe` command: it computes
the two CONTEXT numbers that the report quotes next to the base rates, so
that a reader can re-derive them:

  1. the window's drift - the mean / median / sd of the 24 h log return over
     exactly the entries the measurement samples (140,515 entries in the
     2026-10-22 run), so the reader can see whether the window was driftless;
  2. the archive's median 5-minute GK volatility, which is what the
     REGISTERED geometries are turned into ROE levels with.

It reads the bar archive and nothing else: no network, no writes, no state.
It takes about four minutes on the full archive (the bar parse dominates).

Run:  python3 scripts/roe_context_probe.py [--data data/bars] [--stride 12]
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.quant.bars import contiguous_runs, list_coins, load_coin
from proto.quant.config import BAR_S, BARS_PER_DAY, DEFAULT
from proto.quant.stream import FeatureStream


def window(data_dir):
    """The pre-holdout window the `roe` command measures."""
    btc = load_coin(data_dir, "BTC")
    if not btc:
        raise SystemExit("BTC bars required for the archive span")
    t_min = btc["ts"][0]
    n_days = (btc["ts"][-1] - t_min) / 86400.0
    bnd = DEFAULT.splits.bounds(n_days)
    return (t_min, n_days, bnd,
            t_min + bnd["dev"][0] * 86400, t_min + bnd["val"][1] * 86400)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data/bars")
    ap.add_argument("--stride", type=int, default=12)
    args = ap.parse_args(argv)

    t_min, n_days, bnd, t_lo, t_hi = window(args.data)
    print(f"archive {n_days:.2f} d; window dev {bnd['dev']} + val {bnd['val']} "
          f"(the holdout from day {bnd['holdout'][0]:.0f} is NOT read)")
    rets, sig = [], []
    coins = [c for c in list_coins(args.data) if c != "BTC"]
    for ci, coin in enumerate(coins):
        bars = load_coin(args.data, coin)
        if not bars:
            continue
        ts, amt = bars["ts"], bars["amt"]
        sel = [i for i, t in enumerate(ts) if t_lo <= t < t_hi]
        if not sel:
            continue
        last = sel[-1]
        k = last
        while k > 0 and ts[k] - ts[k - 1] == BAR_S and (last - k) < BARS_PER_DAY - 1:
            k -= 1
        if last - k + 1 < BARS_PER_DAY:
            continue
        if sum(amt[k:last + 1]) < DEFAULT.cost.min_qv24:
            continue
        # 1. the 24 h drift over the sampled entries
        for (a, z) in contiguous_runs(ts):
            start = a + BARS_PER_DAY
            start += (BARS_PER_DAY - start) % args.stride
            for i in range(start, z - BARS_PER_DAY, args.stride):
                if ts[i] < t_lo or ts[i] >= t_hi:
                    continue
                if i - a < BARS_PER_DAY:
                    continue
                if sum(amt[i + 1 - BARS_PER_DAY:i + 1]) < DEFAULT.cost.min_qv24:
                    continue
                rets.append(math.log(bars["c"][i + BARS_PER_DAY] / bars["c"][i + 1]))
        # 2. the 5-minute GK volatility over the same window
        st = FeatureStream(DEFAULT.feat)
        for i in range(len(ts)):
            if ts[i] >= t_hi:
                break
            try:
                snap = st.update(ts[i], bars["o"][i], bars["h"][i], bars["l"][i],
                                 bars["c"][i], amt[i], 0.0)
            except ValueError:
                continue
            if snap and ts[i] >= t_lo and snap.sigma > 0:
                sig.append(snap.sigma)
        if (ci + 1) % 50 == 0:
            print(f"  ... {ci + 1}/{len(coins)} coins", file=sys.stderr, flush=True)

    if not rets:
        print("no entries in the window")
        return 1
    m = sum(rets) / len(rets)
    sd = (sum((x - m) ** 2 for x in rets) / (len(rets) - 1)) ** 0.5
    srt = sorted(rets)
    print(f"entries: {len(rets)}")
    print(f"24h log return: mean {m:+.4%}  median {srt[len(srt) // 2]:+.4%}  "
          f"sd {sd:.4%}")
    if sig:
        ss = sorted(sig)
        q = lambda p: ss[int(p * (len(ss) - 1))]
        print(f"5-minute GK sigma: p10 {q(0.1):.5f}  median {q(0.5):.5f}  "
              f"p90 {q(0.9):.5f}  ({len(sig)} samples)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
