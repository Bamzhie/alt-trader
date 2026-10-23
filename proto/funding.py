"""WS4: point-in-time funding-rate archive.

MEXC perp funding is a live, MUTATING value: the rate on a ticker row is
the rate at observation time, not a settled fact. Persisting each
observation append-only with its observation ts is what makes the rate
SERIES reconstructible point-in-time — the WS4 gap ("no funding-
settlement history" in the roadmap).

Cheap by design: funding rides the bulk ticker row
(``fundingRate``/``maxFundingRate``), so ONE ``tickers()`` request
records the whole universe — there are no per-coin funding calls, and
the request budget impact is one request per snapshot.

Contract mirrors ``proto/collector.py`` exactly: append-only on disk,
sorted on read, deduped by ts, never rewritten. A missing or malformed
row is skipped rather than masking good history.

Row shape (``<COIN>.csv.gz`` under the funding data dir)::

    ts, rate, cap

``ts`` is the observation time (epoch seconds, when the snapshot was
taken); ``rate`` the observed funding rate; ``cap`` the venue's
``maxFundingRate`` at the same instant — funding's own denominator,
recorded beside it so a later reader never has to guess it.
"""

import csv
import gzip
import os
import time

HEADER = ("ts", "rate", "cap")

# Default archive dir: a sibling of data/bars, same per-coin layout.
FUNDING_DIR = "data/funding"


def _path(data_dir, coin):
    return os.path.join(data_dir, f"{coin}.csv.gz")


def read_funding(data_dir, coin):
    """Every funding observation for one coin, chronological.

    Sorts in memory — archives keep append order on disk (the same
    read-path contract as ``proto.collector.read_bars``). Returns []
    for a missing or unreadable file.
    """
    p = _path(data_dir, coin)
    if not os.path.exists(p):
        return []
    out = []
    with gzip.open(p, "rt", newline="") as f:
        for r in csv.DictReader(f):
            try:
                out.append({
                    "ts": int(r["ts"]),
                    "rate": float(r["rate"]),
                    "cap": float(r["cap"]),
                })
            except (KeyError, TypeError, ValueError):
                continue  # a malformed row never masks the good history
    out.sort(key=lambda b: b["ts"])
    return out


def append_funding(data_dir, coin, rows):
    """Append funding observations; return the count of NEW rows.

    Dedupes by ts against what is already on disk (re-observing the same
    ts records nothing), writes in append order, never rewrites.
    """
    os.makedirs(data_dir, exist_ok=True)
    seen = {r["ts"] for r in read_funding(data_dir, coin)}
    new = [r for r in sorted(rows, key=lambda r: r["ts"])
           if r["ts"] not in seen]
    if not new:
        return 0
    p = _path(data_dir, coin)
    exists = os.path.exists(p)
    with gzip.open(p, "at", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(HEADER))
        if not exists:
            w.writeheader()
        for r in new:
            w.writerow({k: r[k] for k in HEADER})
    return len(new)


def collect_funding(data_dir=FUNDING_DIR, now=None):
    """Record ONE point-in-time funding observation per universe coin.

    Makes exactly two venue requests — ``tickers()`` (rates) and
    ``details()`` (the synthetic filter) — and records every eligible
    USDT perp. Synthetic contracts (equity/index/commodity/FX plates)
    are excluded with the same ``canon``/``is_synthetic`` filter the bar
    universe uses, so funding coin names match the bar archives.

    Every observation carries the SAME snapshot ts (one pass over one
    response) — that is what makes the series internally consistent.
    Returns ``{coin: new_rows}``. A venue failure propagates; callers
    (the daemon loop) report it without failing their own sweep.
    """
    from . import mexc
    from .universe import canon, is_synthetic

    tk = mexc.tickers()
    det = mexc.details()
    ts = int(time.time()) if now is None else int(now)
    counts = {}
    for sym, row in tk.items():
        if not sym.endswith("_USDT"):
            continue
        coin = canon(sym)
        if not coin or is_synthetic(coin, det.get(sym)):
            continue
        try:
            rate = float(row.get("fundingRate") or 0.0)
            cap = float(row.get("maxFundingRate") or 0.0018)
        except (TypeError, ValueError):
            continue
        counts[coin] = append_funding(
            data_dir, coin, [{"ts": ts, "rate": rate, "cap": cap}])
    return counts
