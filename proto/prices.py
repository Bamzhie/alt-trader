"""WS4: mark / index price history archive.

MEXC publishes public kline history for the INDEX PRICE
(``/kline/index_price/{symbol}``) and the FAIR / mark PRICE
(``/kline/fair_price/{symbol}``). This module archives exactly those
candles — venue open/high/low/close at the venue candle time — with no
estimation, no interpolation, and no filled gaps.

Only CLOSED hourly candles are stored (the still-forming hour is dropped,
the same rule the bar collector applies). ``vol``/``amount`` are not
stored: the venue returns ``0.0`` for these computed series, and a zero
there is not a measurement.

Contract mirrors ``proto/collector.py`` / ``proto/funding.py``: appends
are gzip CSV per coin per series, sorted on read, deduped by ``ts``, and
never rewritten. Malformed candles are skipped and counted per category;
transport errors propagate so callers can isolate them.
"""

import argparse
import csv
import gzip
import json
import math
import os
import time

from .ratelimit import RollingRateLimiter

SERIES = ("mark", "index")
PRICES_DIR = "data/prices"
HEADER = ("ts", "o", "h", "l", "c")

# Fixed hourly grid (Min60). 2000 candles/request ≈ 83 days, cheap per day.
INTERVAL = "1H"
INTERVAL_SECS = 3600
# Documented per-request candle cap for the venue's kline endpoints.
MAX_WINDOW_S = 2000 * INTERVAL_SECS

# Daemon collect throttle + bounded lookback (like settlements: hourly
# series do not need sub-daily polling of the whole universe).
COLLECT_EVERY_S = 86400
RECENT_LOOKBACK_S = 48 * 3600
THROTTLE_FILE = ".last_collect"

# MEXC documents the index/fair kline endpoints at 20 requests / 2
# seconds — a DISTINCT cap from scan.LIMITER (20 req/s for bars/depth).
PRICE_RATE = 20
PRICE_WINDOW_S = 2.0


class PriceError(RuntimeError):
    pass


PRICE_LIMITER = RollingRateLimiter(PRICE_RATE, PRICE_WINDOW_S)


def _check_series(series):
    if series not in SERIES:
        raise PriceError(f"unknown price series {series!r}; known: "
                         f"{sorted(SERIES)}")


def _series_dir(data_dir, series):
    _check_series(series)
    return os.path.join(data_dir or PRICES_DIR, series)


def _path(data_dir, series, coin):
    return os.path.join(_series_dir(data_dir, series), f"{coin}.csv.gz")


def _as_price(value):
    if isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return num if (math.isfinite(num) and num > 0) else None


def _as_ts(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 and value.is_integer() else None
    if isinstance(value, str):
        try:
            num = int(value.strip(), 10)
        except (TypeError, ValueError):
            return None
        return num if num > 0 else None
    return None


def parse_candles(symbol, rows, *, interval_secs=INTERVAL_SECS, now=None):
    """Validate candle rows; return ``(rows, skipped)``.

    ``rows`` are ``{ts, o, h, l, c}`` mappings (from ``mexc.price_klines``).
    Rejects and counts: ``bad_ts`` (non-integer/non-positive time),
    ``bad_price`` (non-finite, non-numeric, or non-positive OHLC),
    ``bad_ohlc`` (high below the body, low above the body, or low > high),
    ``duplicate_ts`` (first row wins). Closed candles only: a candle whose
    interval has not finished by ``now`` is dropped (not counted — it is
    expected, not malformed). Never invents data.
    """
    now = int(time.time()) if now is None else int(now)
    out = []
    skipped = {}
    seen = set()

    def skip(category):
        skipped[category] = skipped.get(category, 0) + 1

    for r in rows:
        if not isinstance(r, dict):
            skip("malformed_row")
            continue
        ts = _as_ts(r.get("ts"))
        if ts is None:
            skip("bad_ts")
            continue
        o = _as_price(r.get("o"))
        h = _as_price(r.get("h"))
        l = _as_price(r.get("l"))
        c = _as_price(r.get("c"))
        if o is None or h is None or l is None or c is None:
            skip("bad_price")
            continue
        if h < max(o, c) or l > min(o, c) or l > h:
            skip("bad_ohlc")
            continue
        if ts in seen:
            skip("duplicate_ts")
            continue
        seen.add(ts)
        out.append({"ts": ts, "o": o, "h": h, "l": l, "c": c})
    out.sort(key=lambda r: r["ts"])
    # Drop the still-forming candle (mutable until its interval closes).
    while out and out[-1]["ts"] + interval_secs > now:
        out.pop()
    return out, skipped


def read_prices(data_dir, series, coin):
    """Every archived candle for one coin+series, chronological.

    Invalid values are skipped, never emitted; a missing file reads [].
    """
    p = _path(data_dir, series, coin)
    if not os.path.exists(p):
        return []
    out = []
    with gzip.open(p, "rt", newline="") as f:
        for r in csv.DictReader(f):
            try:
                ts = int(r["ts"])
                o = _as_price(r.get("o"))
                h = _as_price(r.get("h"))
                l = _as_price(r.get("l"))
                c = _as_price(r.get("c"))
            except (KeyError, TypeError, ValueError):
                continue
            if ts <= 0 or o is None or h is None or l is None or c is None:
                continue
            out.append({"ts": ts, "o": o, "h": h, "l": l, "c": c})
    out.sort(key=lambda r: r["ts"])
    return out


def append_prices(data_dir, series, coin, rows):
    """Append candles; return the count of NEW rows.

    Dedupes by ts both against what is already on disk and within the
    supplied batch (first row wins on conflict), writes in append order,
    never rewrites. Re-fetching the same window records nothing.
    """
    _check_series(series)
    os.makedirs(_series_dir(data_dir, series), exist_ok=True)
    seen = {r["ts"] for r in read_prices(data_dir, series, coin)}
    # Dedupe within the batch too: a duplicate ts in the same call would
    # otherwise be appended twice (the archive `seen` set only covers what
    # is already on disk). First row wins, consistent with parse_page's
    # duplicate_ts policy.
    batch_seen = set()
    new = []
    for r in sorted(rows, key=lambda r: r["ts"]):
        ts = r["ts"]
        if ts in seen or ts in batch_seen:
            continue
        batch_seen.add(ts)
        new.append(r)
    if not new:
        return 0
    p = _path(data_dir, series, coin)
    exists = os.path.exists(p)
    with gzip.open(p, "at", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(HEADER))
        if not exists:
            w.writeheader()
        for r in new:
            w.writerow({"ts": r["ts"], "o": r["o"], "h": r["h"],
                        "l": r["l"], "c": r["c"]})
    return len(new)


def _merge_skipped(into, skipped):
    for category, n in skipped.items():
        into[category] = into.get(category, 0) + n
    return into


def collect_coin(data_dir, series, coin, symbol, *, start, end, now=None):
    """Fetch one bounded window for one coin+series; idempotent append.

    Returns ``{series, coin, new, skipped}``. Transport errors propagate.
    """
    from . import mexc
    _check_series(series)
    PRICE_LIMITER.acquire()
    raw = mexc.price_klines(series, symbol, INTERVAL, int(start), int(end))
    rows, skipped = parse_candles(symbol, raw, now=now)
    new = append_prices(data_dir, series, coin, rows)
    return {"series": series, "coin": coin, "new": new, "skipped": skipped}


def _universe_coins():
    """Eligible coin->symbol map, same filter as the funding archive."""
    from .universe import canon, is_synthetic
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


def backfill(data_dir=PRICES_DIR, series=SERIES, days=30, coins=None,
             window_s=MAX_WINDOW_S, max_requests_per_coin=10, now=None):
    """Fetch history for the universe (or explicit map), paging backward.

    Per-coin failures are captured as ``{..., "new": 0, "error"}`` and
    never abort the run. Returns ``{series: {coin: result}}``.
    """
    from . import mexc
    now = int(time.time()) if now is None else int(now)
    horizon = now - max(0, int(days)) * 86400
    window_s = max(INTERVAL_SECS, int(window_s))
    mapping = dict(coins) if coins is not None else _universe_coins()
    out = {}
    for s in series:
        _check_series(s)
        out[s] = {}
        for coin, symbol in sorted(mapping.items()):
            try:
                new_total = 0
                skipped_total = {}
                requests = 0
                truncated = False
                end = now
                while end > horizon:
                    start = max(horizon, end - window_s)
                    PRICE_LIMITER.acquire()
                    raw = mexc.price_klines(s, symbol, INTERVAL,
                                            int(start), int(end))
                    rows, skipped = parse_candles(symbol, raw, now=now)
                    _merge_skipped(skipped_total, skipped)
                    new_total += append_prices(data_dir, s, coin, rows)
                    requests += 1
                    if start <= horizon:
                        break
                    end = start - 1
                    if requests >= max(1, int(max_requests_per_coin)):
                        truncated = end > horizon
                        break
                out[s][coin] = {"series": s, "coin": coin, "new": new_total,
                                "skipped": skipped_total,
                                "requests": requests, "truncated": truncated}
            except Exception as e:
                out[s][coin] = {"series": s, "coin": coin, "new": 0,
                                "error": f"{type(e).__name__}: {e}"}
    return out


def collect_recent(data_dir=PRICES_DIR, series=SERIES, coins=None, now=None,
                   lookback_s=RECENT_LOOKBACK_S):
    """Incremental pass: one recent window per coin+series, idempotent.

    Self-heals gaps shorter than ``lookback_s``; deeper gaps need an
    explicit backfill. Returns ``{series: {coin: result}}``.
    """
    from . import mexc
    now = int(time.time()) if now is None else int(now)
    start = max(0, now - int(lookback_s))
    mapping = dict(coins) if coins is not None else _universe_coins()
    out = {}
    for s in series:
        _check_series(s)
        out[s] = {}
        for coin, symbol in sorted(mapping.items()):
            try:
                PRICE_LIMITER.acquire()
                raw = mexc.price_klines(s, symbol, INTERVAL, start, now)
                rows, skipped = parse_candles(symbol, raw, now=now)
                new = append_prices(data_dir, s, coin, rows)
                out[s][coin] = {"series": s, "coin": coin, "new": new,
                                "skipped": skipped}
            except Exception as e:
                out[s][coin] = {"series": s, "coin": coin, "new": 0,
                                "error": f"{type(e).__name__}: {e}"}
    return out


def _throttle_due(data_dir, every_s=COLLECT_EVERY_S, now=None):
    """Daily throttle for the daemon hook; True means collect now."""
    if data_dir is None:
        data_dir = PRICES_DIR
    now = int(time.time()) if now is None else int(now)
    try:
        with open(os.path.join(data_dir, THROTTLE_FILE)) as f:
            last = int(f.read().strip().split()[0])
    except (OSError, ValueError, IndexError):
        return True
    return now - last >= every_s


def _throttle_mark(data_dir, now=None):
    if data_dir is None:
        data_dir = PRICES_DIR
    now = int(time.time()) if now is None else int(now)
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, THROTTLE_FILE), "w") as f:
        f.write(f"{now}\n")


def _collect_outcome(out):
    """Classify a collect_recent result for throttle marking.

    Success is defined by whether at least one coin result completed
    **without** an ``error`` key, regardless of whether ``new`` is zero.
    This correctly handles the idempotent re-run case: a collect_recent
    that succeeds but records zero new rows (everything already archived)
    is still a successful pass, so the throttle is marked and we avoid
    re-pulling the entire universe every sweep.

    Returns ``("success", n_new)`` when at least one coin has no error
    key (even if all recorded zero new rows). Returns
    ``("error", 0)`` when every coin errored or no coins were attempted,
    so the next sweep retries.
    """
    total_new = 0
    any_success = False
    any_attempted = False
    for series_results in out.values():
        for coin, res in series_results.items():
            any_attempted = True
            if "error" not in res:
                any_success = True
                total_new += res.get("new", 0)
    if not any_attempted:
        return ("error", 0)
    return ("success" if any_success else "error", total_new)


def maybe_collect(data_dir=None, series=SERIES, every_s=COLLECT_EVERY_S,
                  now=None):
    """Daemon entry: collect when the daily throttle is due, else ``{}``.

    Throttle semantics on failure:
    - **Total failure** (every coin errored, or nothing was attempted):
      the throttle file is NOT marked, so the next daemon sweep retries
      the collection immediately (subject to the per-sweep cadence). This
      prevents a wholesale venue outage from silencing recovery for a full
      day.
    - **Partial failure** (some coins errored, others recorded rows):
      the throttle IS marked — successful rows are preserved, and the
      errored coins will be retried on the next backfill/collect-recent
      run. Marking avoids re-pulling the entire universe on every sweep
      when the archive is already growing.
    - **Success** (at least one coin recorded rows): throttle marked as
      before.
    """
    if data_dir is None:
        data_dir = PRICES_DIR
    if not _throttle_due(data_dir, every_s, now):
        return {}
    out = collect_recent(data_dir, series=series, now=now)
    outcome, n_new = _collect_outcome(out)
    if outcome == "error":
        # Do NOT mark the throttle on total failure — retry next sweep.
        return out
    _throttle_mark(data_dir, now)
    return out


def _parse_series_arg(text):
    out = []
    for item in (text or "").split(","):
        item = item.strip()
        if not item:
            continue
        _check_series(item)
        if item not in out:
            out.append(item)
    if not out:
        raise PriceError("--series must name at least one of mark,index")
    return tuple(out)


def main(argv=None):
    """CLI: ``python3 -m proto.prices backfill|collect-recent``."""
    ap = argparse.ArgumentParser(
        description="MEXC mark/index price history archive")
    ap.add_argument("command", choices=("backfill", "collect-recent"))
    ap.add_argument("--data-dir", default=PRICES_DIR)
    ap.add_argument("--series", default="mark,index",
                    help="comma-separated series (mark,index)")
    ap.add_argument("--coins", default=None,
                    help="comma-separated COIN[:SYMBOL] list (default: universe)")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    series = _parse_series_arg(args.series)
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
        out = backfill(args.data_dir, series, args.days, coins)
    else:
        out = collect_recent(args.data_dir, series, coins)
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        total = sum(r.get("new", 0)
                    for s in out.values() for r in s.values())
        errs = sum(1 for s in out.values() for r in s.values()
                   if "error" in r)
        ncoins = len({c for s in out.values() for c in s})
        print(f"prices: {ncoins} coins x {len(out)} series, "
              f"{total} new rows, {errs} errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
