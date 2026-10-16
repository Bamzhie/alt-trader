"""Bar loading for the quant module.

The collector appends backfill pages AFTER forward-collected rows, so a bar
file is NOT guaranteed to be time-ordered (observed on real archives: ~13
out-of-order chunks per file).  Everything here sorts and de-duplicates by
timestamp and exposes contiguous runs, so no consumer ever depends on file
order or silently bridges a gap.
"""
import csv
import gzip
import os

from .config import BAR_S


def load_coin(data_dir, coin):
    """Sorted, de-duplicated bars as parallel lists, or None if no file.

    Returns {"ts","o","h","l","c","vol","amt"} (lists).  First occurrence of
    a duplicate timestamp wins (same rule as collector.append_bars).
    """
    path = os.path.join(data_dir, f"{coin}.csv.gz")
    if not os.path.exists(path):
        return None
    rows = {}
    with gzip.open(path, "rt", newline="") as f:
        rd = csv.reader(f)
        header = next(rd, None)
        if not header or header[0] != "ts":
            return None
        for r in rd:
            try:
                ts = int(r[0])
            except (ValueError, IndexError):
                continue
            if ts in rows:
                continue
            try:
                rows[ts] = (float(r[1]), float(r[2]), float(r[3]),
                            float(r[4]), float(r[5]), float(r[6]))
            except (ValueError, IndexError):
                continue
    if not rows:
        return None
    ts = sorted(rows)
    out = {"ts": ts, "o": [], "h": [], "l": [], "c": [], "vol": [], "amt": []}
    for t in ts:
        o, h, l, c, v, a = rows[t]
        out["o"].append(o); out["h"].append(h); out["l"].append(l)
        out["c"].append(c); out["vol"].append(v); out["amt"].append(a)
    return out


def valid_bar(o, h, l, c):
    """Finite, positive, internally consistent OHLC."""
    if not (o > 0 and h > 0 and l > 0 and c > 0):
        return False
    return h >= max(o, c) - 1e-12 * h and l <= min(o, c) + 1e-12 * h and h >= l


def contiguous_runs(ts, step=BAR_S):
    """[(start_idx, end_idx_exclusive)] of gap-free stretches."""
    runs = []
    if not ts:
        return runs
    s = 0
    for i in range(1, len(ts)):
        if ts[i] - ts[i - 1] != step:
            runs.append((s, i))
            s = i
    runs.append((s, len(ts)))
    return runs


def list_coins(data_dir):
    if not os.path.isdir(data_dir):
        return []
    return sorted(f[:-7] for f in os.listdir(data_dir) if f.endswith(".csv.gz"))


def btc_returns(btc):
    """{ts: log-return} for the market factor; ts-keyed so coins with
    different start times align without index arithmetic."""
    import math
    out = {}
    c, ts = btc["c"], btc["ts"]
    for i in range(1, len(c)):
        if ts[i] - ts[i - 1] == BAR_S and c[i] > 0 and c[i - 1] > 0:
            out[ts[i]] = math.log(c[i] / c[i - 1])
    return out
