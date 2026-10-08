"""
Bybit v5 contract REST adapter.

Shapes verified against the live public API (read-only, no keys):
  - Every response is an envelope {retCode, retMsg, result}. Check retCode == 0.
  - Klines are ROWS: [startMs, open, high, low, close, volume, turnover],
    strictly NEWEST FIRST (reverse chronological), millisecond timestamps.
  - Order book result uses short keys: b (bids) / a (asks), each row
    [price, qty], best-first both sides.
  - Ticker rows carry fundingRate/fundingCap, so funding is one cheap request.
  - Bar output matches proto/mexc.py exactly: {ts,o,h,l,c,vol,amount}, oldest
    first, ts in SECONDS (v5 milliseconds are normalized on the way out).
"""

import json
import time
import urllib.error
import urllib.request

BASE = "https://api.bybit.com/v5/market"
UA = {"User-Agent": "Mozilla/5.0"}
CATEGORY = "linear"  # USDT-margined perps; MEXC-side discovery is USDT too

# Measured interval names -> (v5 interval, seconds)
INTERVALS = {
    "5m": ("5", 300),
    "15m": ("15", 900),
    "30m": ("30", 1800),
    "1H": ("60", 3600),
    "4H": ("240", 14400),
    "1D": ("D", 86400),
}
MAX_BARS = 1000              # v5 kline limit cap
KLINE_COLS = 7               # startMs, o, h, l, c, vol, turnover
FUNDING_CAP_DEFAULT = 0.00333  # per-settle cap (v1 spec: Bybit +/-0.333%)
ORDER_LIMITS = (1, 50, 200, 500)  # v5 orderbook accepts only these limits


class BybitError(RuntimeError):
    pass


def _get(path, timeout=20, retries=3):
    url = f"{BASE}{path}"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                env = json.load(r)
            if env.get("retCode") != 0:
                raise BybitError(
                    f"api error code={env.get('retCode')} msg={env.get('retMsg')!r}")
            return env["result"]
        except (urllib.error.URLError, urllib.error.HTTPError, BybitError,
                json.JSONDecodeError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(0.4 * (2 ** attempt))
    raise BybitError(f"{path}: {type(last).__name__}: {last}")


def tickers():
    """All linear perp tickers in ONE request. Returns {symbol: row}."""
    d = _get(f"/tickers?category={CATEGORY}", timeout=30)
    return {r["symbol"]: r for r in (d.get("list") or [])}


def details():
    """All linear perp metadata, following nextPageCursor to exhaustion."""
    rows, cursor, pages = [], "", 0
    while pages < 10:  # ~800 instruments fit in 10k rows; guard against loops
        path = f"/instruments-info?category={CATEGORY}&limit=1000"
        if cursor:
            path += f"&cursor={cursor}"
        d = _get(path, timeout=30)
        rows.extend(d.get("list") or [])
        cursor = d.get("nextPageCursor") or ""
        pages += 1
        if not cursor:
            break
    return {r["symbol"]: r for r in rows}


def funding(symbol):
    """Latest funding rate and per-settle cap for one symbol."""
    d = _get(f"/tickers?category={CATEGORY}&symbol={symbol}")
    rows = d.get("list") or []
    if not rows:
        raise BybitError(f"{symbol}: no ticker row")
    row = rows[0]
    return {
        "rate": float(row.get("fundingRate") or 0.0),
        "cap": float(row.get("fundingCap") or FUNDING_CAP_DEFAULT),
    }


def depth(symbol, limit=20):
    """
    Order book. v5 returns short keys with [price, qty] rows: b/bids,
    a/asks, best-first both sides. Only 1/50/200/500 limits are accepted,
    so the requested limit rounds UP to the nearest allowed value.
    Returns (bids, asks) with each side a list of (price, qty), best-first.
    """
    lim = next((l for l in ORDER_LIMITS if l >= limit), ORDER_LIMITS[-1])
    d = _get(f"/orderbook?category={CATEGORY}&symbol={symbol}&limit={lim}")
    bids = [(float(r[0]), float(r[1])) for r in (d.get("b") or [])]
    asks = [(float(r[0]), float(r[1])) for r in (d.get("a") or [])]
    return bids, asks


def klines(symbol, interval="5m", limit=200):
    """
    OHLCV bars, oldest first, same shape as proto/mexc.py:
    {ts, o, h, l, c, vol, amount} with ts in seconds.
    v5 returns rows newest first with millisecond timestamps; both are
    normalized here. Validates shape before returning - never hand back
    junk to the scorer.
    """
    if interval not in INTERVALS:
        raise ValueError(f"unknown interval {interval!r}; known: {sorted(INTERVALS)}")
    bybit_iv, _ = INTERVALS[interval]
    limit = max(1, min(limit, MAX_BARS))

    d = _get(f"/kline?category={CATEGORY}&symbol={symbol}"
             f"&interval={bybit_iv}&limit={limit}")
    rows = (d or {}).get("list") or []
    if not rows:
        return []

    # Row contract: every row carries all columns, and all rows agree.
    for i, r in enumerate(rows):
        if not isinstance(r, (list, tuple)) or len(r) < KLINE_COLS:
            got = len(r) if isinstance(r, (list, tuple)) else type(r).__name__
            raise BybitError(
                f"{symbol} {interval}: missing column at row {i} "
                f"(width {got}, want {KLINE_COLS})")
    widths = {len(r) for r in rows}
    if len(widths) != 1:
        raise BybitError(
            f"{symbol} {interval}: ragged rows, widths {sorted(widths)}")

    # v5 is strictly reverse-chronological. Validate the RAW order before we
    # sort. Checking monotonicity after sorting is self-satisfying and can
    # never fail.
    n = len(rows)
    for i in range(1, n):
        if int(rows[i][0]) >= int(rows[i - 1][0]):
            raise BybitError(
                f"{symbol} {interval}: non-monotonic timestamps "
                f"({rows[i - 1][0]} -> {rows[i][0]}) in payload"
            )

    bars = [
        {
            "ts": int(r[0]) // 1000,   # v5 ms -> seconds, matching mexc bars
            "o": float(r[1]),
            "h": float(r[2]),
            "l": float(r[3]),
            "c": float(r[4]),
            "vol": float(r[5]),        # base volume
            "amount": float(r[6]),     # turnover = quote volume
        }
        for r in rows
    ]
    bars.sort(key=lambda b: b["ts"])

    # Sanity on the parsed values themselves.
    for b in bars:
        if b["c"] <= 0 or b["h"] < b["l"]:
            raise BybitError(f"{symbol}: implausible bar {b}")
    return bars


def oi_change(symbol, window="1h", points=25):
    """
    Percent change in open interest over ~24h (1h grid, `points` samples).
    None means "no usable data" and is the designed funding-only path
    downstream; transport/API failures raise BybitError so a degraded
    venue stays visible to the caller.
    """
    d = _get(f"/open-interest?category={CATEGORY}&symbol={symbol}"
             f"&intervalTime={window}&limit={points}")
    samples = []
    for r in (d.get("list") or []):
        try:
            oi = float(r.get("openInterest"))
            ts = int(r.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if oi > 0:
            samples.append((ts, oi))
    if len(samples) < 2:
        return None
    oldest = min(samples)   # (ts, oi): ordered by timestamp
    newest = max(samples)
    return (newest[1] - oldest[1]) / oldest[1] * 100.0
