"""WS4: venue-reported funding settlement history archive.

MEXC reports settled funding rates with settle timestamps
(``GET /funding_rate/history``). This module archives exactly what the
venue reports — settle ts, settled rate, per-row cycle context — with
no estimation, no interpolation, and no filled gaps.

Contract mirrors ``proto/funding.py``: append-only gzip CSV per coin,
sorted on read, deduped by timestamp, never rewritten. A malformed row
is skipped and counted rather than masking good history; transport
errors propagate so callers can isolate them.
"""

import argparse
import csv
import gzip
import math
import os
import time

HEADER = ("settle_ts", "settled_rate", "cycle_hours")

# Default archive dir: a sibling of data/bars and data/funding, same
# per-coin layout.
SETTLEMENTS_DIR = "data/funding_settlements"

# Collect-cycle throttle for the daemon hook (3 settles/day/coin make
# sub-daily polling pure venue load).
COLLECT_EVERY_S = 86400
THROTTLE_FILE = ".last_collect"


class SettlementError(RuntimeError):
    pass


def _path(data_dir, coin):
    return os.path.join(data_dir, f"{coin}.csv.gz")


def _as_finite_rate(value):
    if isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return num if math.isfinite(num) else None


def _as_settle_ts(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        ms = value
    elif isinstance(value, float) and value.is_integer():
        ms = int(value)
    elif isinstance(value, str):
        try:
            ms = int(value.strip(), 10)
        except (TypeError, ValueError):
            return None
    else:
        return None
    return ms // 1000 if ms > 0 else None


def _as_cycle(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        hours = int(str(value).strip(), 10)
    except (TypeError, ValueError):
        return None
    return hours if hours > 0 else None


def parse_page(symbol, data):
    """Validate one history page; return ``(rows, info, skipped)``.

    ``rows`` are ``{settle_ts, settled_rate, cycle_hours|None}``
    (duplicates by ts keep the first row; conflicts counted).
    ``info`` is ``{page, total_pages, total_count}``. ``skipped`` maps
    rejection categories to counts. Malformed pagination raises
    ``SettlementError``. Never invents data.
    """
    if not isinstance(data, dict):
        raise SettlementError(f"history page for {symbol} is not a mapping")
    try:
        page = int(data["currentPage"])
        total_pages = int(data["totalPage"])
        total_count = int(data["totalCount"])
        result = data["resultList"]
    except (KeyError, TypeError, ValueError):
        raise SettlementError(
            f"history page for {symbol} has malformed pagination")
    if page < 1 or total_pages < 1 or total_count < 0:
        raise SettlementError(
            f"history page for {symbol} has impossible pagination: "
            f"page={page} total_pages={total_pages}")
    if page > total_pages:
        raise SettlementError(
            f"history page {page} beyond total {total_pages} for {symbol}")
    if not isinstance(result, list):
        raise SettlementError(
            f"history resultList for {symbol} is not a list")
    rows = []
    skipped = {}
    seen = set()

    def skip(category):
        skipped[category] = skipped.get(category, 0) + 1

    for entry in result:
        if not isinstance(entry, dict):
            skip("malformed_row")
            continue
        if entry.get("symbol") != symbol:
            skip("symbol_mismatch")
            continue
        ts = _as_settle_ts(entry.get("settleTime"))
        rate = _as_finite_rate(entry.get("fundingRate"))
        if ts is None or rate is None:
            if ts is None:
                skip("bad_settle_time")
            if rate is None:
                skip("bad_rate")
            continue
        cycle = _as_cycle(entry.get("collectCycle"))
        if ts in seen:
            skip("duplicate_ts")
            continue
        seen.add(ts)
        rows.append({"settle_ts": ts, "settled_rate": rate,
                     "cycle_hours": cycle})
    rows.sort(key=lambda r: r["settle_ts"])
    return rows, {"page": page, "total_pages": total_pages,
                  "total_count": total_count}, skipped


def read_settlements(data_dir, coin):
    """Every archived settlement for one coin, chronological."""
    p = _path(data_dir, coin)
    if not os.path.exists(p):
        return []
    out = []
    with gzip.open(p, "rt", newline="") as f:
        for r in csv.DictReader(f):
            try:
                out.append({
                    "settle_ts": int(r["settle_ts"]),
                    "settled_rate": float(r["settled_rate"]),
                    "cycle_hours": (int(r["cycle_hours"])
                                    if r["cycle_hours"] not in (None, "")
                                    else None),
                })
            except (KeyError, TypeError, ValueError):
                continue  # a malformed row never masks the good history
    out.sort(key=lambda r: r["settle_ts"])
    return out


def append_settlements(data_dir, coin, rows):
    """Append settlements; return the count of NEW rows.

    Dedupes by settle_ts against what is already on disk (re-fetching
    the same page records nothing), writes in append order, never
    rewrites.
    """
    os.makedirs(data_dir, exist_ok=True)
    seen = {r["settle_ts"] for r in read_settlements(data_dir, coin)}
    new = [r for r in sorted(rows, key=lambda r: r["settle_ts"])
           if r["settle_ts"] not in seen]
    if not new:
        return 0
    p = _path(data_dir, coin)
    exists = os.path.exists(p)
    with gzip.open(p, "at", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(HEADER))
        if not exists:
            w.writeheader()
        for r in new:
            w.writerow({
                "settle_ts": r["settle_ts"],
                "settled_rate": r["settled_rate"],
                "cycle_hours": ("" if r.get("cycle_hours") is None
                                else r["cycle_hours"]),
            })
    return len(new)


def _merge_skipped(into, skipped):
    for category, n in skipped.items():
        into[category] = into.get(category, 0) + n
    return into


def collect_coin(data_dir, coin, symbol, max_pages=10):
    """Fetch every history page for one coin; idempotent append.

    Returns ``{coin, new, skipped, pages, truncated}``. ``truncated``
    is True when ``max_pages`` stopped the walk early (reported, never
    silent). Transport errors propagate.
    """
    from . import mexc
    new_total = 0
    skipped_total = {}
    pages = 0
    truncated = False
    page = 1
    while True:
        data = mexc.funding_history(symbol, page, 1000)
        rows, info, skipped = parse_page(symbol, data)
        _merge_skipped(skipped_total, skipped)
        new_total += append_settlements(data_dir, coin, rows)
        pages += 1
        if info["page"] >= info["total_pages"]:
            break
        if pages >= max(1, int(max_pages)):
            truncated = True
            break
        page += 1
    return {"coin": coin, "new": new_total, "skipped": skipped_total,
            "pages": pages, "truncated": truncated}


def _universe_coins():
    """Eligible coin->symbol map, same filter as the funding archive."""
    from .scan import canon, is_synthetic
    from . import mexc
    det = mexc.details()
    out = {}
    for sym in mexc.tickers():
        if not sym.endswith("_USDT"):
            continue
        coin = canon(sym)
        if not coin or is_synthetic(coin, det.get(sym)):
            continue
        out[coin] = sym
    return out


def backfill(data_dir=SETTLEMENTS_DIR, max_pages_per_coin=3, coins=None):
    """Fetch settlement history for the universe (or explicit map).

    Per-coin failures are captured as ``{"coin", "new": 0, "error"}``
    and never abort the run. Returns ``{coin: result}``.
    """
    mapping = dict(coins) if coins is not None else _universe_coins()
    out = {}
    for coin, symbol in sorted(mapping.items()):
        try:
            out[coin] = collect_coin(data_dir, coin, symbol,
                                     max_pages=max_pages_per_coin)
        except Exception as e:
            out[coin] = {"coin": coin, "new": 0,
                         "error": f"{type(e).__name__}: {e}"}
    return out


def collect_recent(data_dir=SETTLEMENTS_DIR, coins=None):
    """Incremental pass: page 1 per coin, keep rows newer than archived.

    Same dedupe path as backfill, so re-running records nothing new.
    Returns ``{coin: collect_coin result}`` with ``pages`` always 1.
    """
    from . import mexc
    mapping = dict(coins) if coins is not None else _universe_coins()
    out = {}
    for coin, symbol in sorted(mapping.items()):
        try:
            data = mexc.funding_history(symbol, 1, 1000)
            rows, _, skipped = parse_page(symbol, data)
            new = append_settlements(data_dir, coin, rows)
            out[coin] = {"coin": coin, "new": new, "skipped": skipped,
                         "pages": 1, "truncated": False}
        except Exception as e:
            out[coin] = {"coin": coin, "new": 0,
                         "error": f"{type(e).__name__}: {e}"}
    return out


def _throttle_due(data_dir, every_s=COLLECT_EVERY_S, now=None):
    """Daily throttle for the daemon hook; True means collect now."""
    now = int(time.time()) if now is None else int(now)
    try:
        with open(os.path.join(data_dir, THROTTLE_FILE)) as f:
            last = int(f.read().strip().split()[0])
    except (OSError, ValueError, IndexError):
        return True
    return now - last >= every_s


def _throttle_mark(data_dir, now=None):
    now = int(time.time()) if now is None else int(now)
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, THROTTLE_FILE), "w") as f:
        f.write(f"{now}\n")


def maybe_collect(data_dir=SETTLEMENTS_DIR, every_s=COLLECT_EVERY_S,
                  now=None):
    """Daemon entry: collect when the daily throttle is due, else {}."""
    if not _throttle_due(data_dir, every_s, now):
        return {}
    out = collect_recent(data_dir)
    _throttle_mark(data_dir, now)
    return out


def main(argv=None):
    """CLI: ``python3 -m proto.settlements backfill|collect-recent``."""
    import json
    ap = argparse.ArgumentParser(
        description="Venue-reported funding settlement archive")
    ap.add_argument("command", choices=("backfill", "collect-recent"))
    ap.add_argument("--data-dir", default=SETTLEMENTS_DIR)
    ap.add_argument("--coins", default=None,
                    help="comma-separated COIN[:SYMBOL] list (default: universe)")
    ap.add_argument("--max-pages", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    coins = None
    if args.coins:
        coins = {}
        for item in args.coins.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                coin, symbol = item.split(":", 1)
            else:
                coin, symbol = item, f"{item}_USDT"
            coins[coin] = symbol
    if args.command == "backfill":
        out = backfill(args.data_dir, args.max_pages, coins)
    else:
        out = collect_recent(args.data_dir, coins)
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        new = sum(r.get("new", 0) for r in out.values())
        errs = sum(1 for r in out.values() if "error" in r)
        print(f"settlements: {len(out)} coins {new} new rows, "
              f"{errs} errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
