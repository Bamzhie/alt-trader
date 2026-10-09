"""
MEXC contract REST adapter.

Design rules learned the hard way during spec probing:
  - Every response is an envelope {success, code, data}. Unwrap and CHECK success.
  - Klines are COLUMNAR: {"time":[...], "open":[...], ...}, not row objects.
  - The volume field is "vol" (base asset) plus "amount" (quote). There is no "volume".
  - Timestamps are SECONDS for klines, but ms for ticker fields.
  - MEXC caps any kline request at 2000 bars and ignores wider ranges.
"""

import json
import math
import time
import urllib.error
import urllib.request

BASE = "https://contract.mexc.com/api/v1/contract"
UA = {"User-Agent": "Mozilla/5.0"}

# Measured interval names
INTERVALS = {
    "5m": ("Min5", 300),
    "15m": ("Min15", 900),
    "30m": ("Min30", 1800),
    "1H": ("Min60", 3600),
    "4H": ("Hour4", 14400),
    "1D": ("Day1", 86400),
}
MAX_BARS = 2000


class MexcError(RuntimeError):
    pass


def _get(path, timeout=20, retries=3):
    url = f"{BASE}{path}"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                env = json.load(r)
            if not env.get("success"):
                raise MexcError(f"api error code={env.get('code')}")
            return env["data"]
        except (urllib.error.URLError, urllib.error.HTTPError, MexcError, json.JSONDecodeError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(0.4 * (2 ** attempt))
    raise MexcError(f"{path}: {type(last).__name__}: {last}")


def tickers():
    """All contract tickers in ONE request. Returns {symbol: row}."""
    rows = _get("/ticker", timeout=30)
    return {r["symbol"]: r for r in rows}


def details():
    """All contract metadata in ONE request. Returns {symbol: row}."""
    rows = _get("/detail", timeout=30)
    return {r["symbol"]: r for r in rows}


def funding_rate(symbol):
    d = _get(f"/funding_rate/{symbol}")
    return {
        "rate": float(d.get("fundingRate") or 0.0),
        "cap": float(d.get("maxFundingRate") or 0.0018),
        "cycle_hours": int(d.get("collectCycle") or 8),
        "next_settle_ms": int(d.get("nextSettleTime") or 0),
    }


def depth(symbol, limit=20):
    """
    Order book. Rows are [price, qty, n_orders] on all three venues.
    Returns (bids, asks) with each side a list of (price, qty), best-first.
    """
    d = _get(f"/depth/{symbol}?limit={limit}")
    bids = [(float(r[0]), float(r[1])) for r in (d.get("bids") or [])]
    asks = [(float(r[0]), float(r[1])) for r in (d.get("asks") or [])]
    return bids, asks


def klines(symbol, interval="5m", limit=200):
    """
    OHLCV bars, oldest first.
    Returns list of dicts: {ts, o, h, l, c, vol, amount}
    Validates shape before returning - never hand back junk to the scorer.
    """
    if interval not in INTERVALS:
        raise ValueError(f"unknown interval {interval!r}; known: {sorted(INTERVALS)}")
    mexc_iv, secs = INTERVALS[interval]
    limit = max(1, min(limit, MAX_BARS))

    now = int(time.time())
    # Interval-aware window: limit bars of `secs` each (plus one spare bar so
    # boundary timing never short-changes coverage). The old limit*3600 asked
    # ~200h for every timeframe — ~50 bars at 4h, ~2400 at 5m.
    start = now - limit * secs - secs
    d = _get(f"/kline/{symbol}?interval={mexc_iv}&start={start}&end={now}")

    if not d:
        return []
    # Columnar -> rows, with an explicit contract on required keys.
    required = ("time", "open", "high", "low", "close", "vol", "amount")
    for k in required:
        if k not in d:
            raise MexcError(f"{symbol} {interval}: missing column {k!r} (got {sorted(d)})")

    n = len(d["time"])
    for k in required:
        if len(d[k]) != n:
            raise MexcError(f"{symbol} {interval}: ragged columns, {k} has {len(d[k])} vs {n}")

    # Validate the RAW column order before we sort. Checking monotonicity after
    # sorting is self-satisfying and can never fail.
    for i in range(1, n):
        if int(d["time"][i]) <= int(d["time"][i - 1]):
            raise MexcError(
                f"{symbol} {interval}: non-monotonic timestamps "
                f"({d['time'][i - 1]} -> {d['time'][i]}) in payload"
            )

    bars = [
        {
            "ts": int(d["time"][i]),
            "o": float(d["open"][i]),
            "h": float(d["high"][i]),
            "l": float(d["low"][i]),
            "c": float(d["close"][i]),
            "vol": float(d["vol"][i]),
            "amount": float(d["amount"][i]),
        }
        for i in range(n)
    ]
    bars.sort(key=lambda b: b["ts"])
    # Drop the still-forming bar: a candle whose interval hasn't closed has a
    # mutable close/high/low/volume, so scoring it makes signals unstable
    # mid-bar and unreproducible at close. Keep history only.
    while bars and bars[-1]["ts"] + secs > now:
        bars.pop()
    # Honor the requested count: the window above is deliberately generous,
    # so keep the most recent `limit` bars (oldest-first preserved).
    if len(bars) > limit:
        bars = bars[-limit:]

    # Sanity on the parsed values themselves: finite, internally
    # consistent (low <= body <= high), positive close.
    for b in bars:
        if (b["c"] <= 0 or b["h"] < b["l"]
                or not all(math.isfinite(b[k])
                           for k in ("o", "h", "l", "c", "vol", "amount"))
                or not (b["l"] <= min(b["o"], b["c"])
                        and max(b["o"], b["c"]) <= b["h"])):
            raise MexcError(f"{symbol}: implausible bar {b}")
    return bars


def min_notional(symbol, detail_row, last_price):
    """Minimum tradeable notional in USDT: minVol contracts x contractSize x price."""
    try:
        min_vol = float(detail_row.get("minVol") or 0)
        size = float(detail_row.get("contractSize") or 0)
        if min_vol <= 0 or size <= 0 or last_price <= 0:
            return None
        return min_vol * size * last_price
    except (TypeError, ValueError):
        return None