"""5m forward bar collector. Starts the Tier-2 validation clock."""
import csv, gzip, os
from concurrent.futures import ThreadPoolExecutor

HEADER = ["ts","o","h","l","c","vol","amount"]
COLLECT_WORKERS = 8

def _path(data_dir, coin):
    return os.path.join(data_dir, f"{coin}.csv.gz")

def read_bars(data_dir, coin):
    p = _path(data_dir, coin)
    if not os.path.exists(p):
        return []
    out = []
    with gzip.open(p, "rt", newline="") as f:
        for r in csv.DictReader(f):
            out.append({"ts": int(r["ts"]), "o": float(r["o"]), "h": float(r["h"]),
                        "l": float(r["l"]), "c": float(r["c"]),
                        "vol": float(r["vol"]), "amount": float(r["amount"])})
    return out

def append_bars(data_dir, coin, bars):
    os.makedirs(data_dir, exist_ok=True)
    seen = {b["ts"] for b in read_bars(data_dir, coin)}
    new = [b for b in sorted(bars, key=lambda b: b["ts"]) if b["ts"] not in seen]
    if not new:
        return 0
    p = _path(data_dir, coin)
    exists = os.path.exists(p)
    with gzip.open(p, "at", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        if not exists:
            w.writeheader()
        for b in new:
            w.writerow({k: b[k] for k in HEADER})
    return len(new)

def collect_full_universe(data_dir="data/bars", limit_per_coin=200,
                          max_workers=COLLECT_WORKERS):
    """Fetch 5m bars for EVERY universe coin, regardless of scan rotation.

    build_universe(stake=inf, budget=None) is uncapped (spec SS5: the
    collector covers the full universe, not the 150-coin scan rotation), so a
    coin that leaves rotation keeps accumulating bars for outcome resolution.
    """
    from .scan import build_universe
    from . import mexc
    uni = build_universe(float("inf"), budget=None)

    def collect_one(item):
        sym, coin = item
        try:
            # Share the scan/measurement request cap so collector workers
            # cannot create an unbounded venue burst.
            from .scan import LIMITER
            LIMITER.acquire()
            bars = mexc.klines(sym, "5m", limit=limit_per_coin)
        except Exception:
            return None
        try:
            return coin, append_bars(data_dir, coin, bars)
        except Exception:
            return None

    counts = {}
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        for result in pool.map(collect_one, uni):
            if result is not None:
                coin, count = result
                counts[coin] = count
    return counts
