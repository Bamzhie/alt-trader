"""Offline tests for the Bybit v5 public REST adapter. No network.

Fixtures mirror real v5 responses verified against api.bybit.com:
  - envelope {retCode, retMsg, result}; retCode must be 0
  - klines: result.list rows [startMs, open, high, low, close, volume, turnover],
    strictly NEWEST FIRST, millisecond timestamps
  - order book: [price, qty] pairs, best-first both sides
  - open interest: newest-first objects {openInterest, timestamp}
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import bybit

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def with_get(fake):
    """Install a fake _get (no network). Returns a restore callable."""
    orig = bybit._get
    bybit._get = fake

    def restore():
        bybit._get = orig
    return restore


# Real shape: rows of 7 columns, strictly newest first, ms timestamps.
KLINES_GOOD = {"list": [
    ["300000", "3.0", "3.5", "2.5", "3.2", "30", "96"],
    ["200000", "2.0", "2.5", "1.5", "2.2", "20", "44"],
    ["100000", "1.0", "1.5", "0.5", "1.1", "10", "11"],
]}


def test_good_kline_payload_parses():
    print("=== good kline payload -> 3 bars, ascending ===")
    restore = with_get(lambda path, **kw: KLINES_GOOD)
    try:
        bars = bybit.klines("XUSDT", "5m", limit=3)
    finally:
        restore()
    check("three bars parsed", len(bars) == 3, str(len(bars)))
    check("ascending ts (ms normalized to seconds)",
          [b["ts"] for b in bars] == [100, 200, 300],
          str([b["ts"] for b in bars]))
    check("values map correctly",
          bars[0]["o"] == 1.0 and bars[0]["h"] == 1.5
          and bars[0]["l"] == 0.5 and bars[0]["c"] == 1.1, str(bars[0]))
    check("vol and amount distinct",
          bars[0]["vol"] == 10.0 and bars[0]["amount"] == 11.0, str(bars[0]))
    check("key set matches mexc shape",
          set(bars[0]) == {"ts", "o", "h", "l", "c", "vol", "amount"},
          str(sorted(bars[0])))


def test_missing_column_rejected():
    print("=== kline row missing a column -> BybitError ===")
    payload = {"list": [
        ["300000", "3.0", "3.5", "2.5", "3.2", "30"],       # turnover absent
        ["200000", "2.0", "2.5", "1.5", "2.2", "20", "44"],
    ]}
    restore = with_get(lambda path, **kw: payload)
    try:
        try:
            bybit.klines("XUSDT", "5m", limit=3)
            check("missing column rejected", False, "no exception raised")
        except bybit.BybitError:
            check("missing column rejected", True)
    finally:
        restore()


def test_ragged_rows_rejected():
    print("=== ragged kline rows -> BybitError ===")
    payload = {"list": [
        ["300000", "3", "3", "3", "3", "3", "3"],
        ["200000", "2", "2", "2", "2", "2", "2", "2"],   # 8 fields vs 7
    ]}
    restore = with_get(lambda path, **kw: payload)
    try:
        try:
            bybit.klines("XUSDT", "5m", limit=3)
            check("ragged rows rejected", False, "no exception raised")
        except bybit.BybitError:
            check("ragged rows rejected", True)
    finally:
        restore()


def test_non_monotonic_payload_rejected():
    """v5 is strictly reverse-chronological; ascending (MEXC-style) rows must
    be rejected on the RAW payload, not silently 'fixed' by the sort."""
    print("=== wrong-order payload rejected before sort ===")
    payload = {"list": [
        ["100000", "1.0", "1.5", "0.5", "1.1", "10", "11"],
        ["200000", "2.0", "2.5", "1.5", "2.2", "20", "44"],
        ["300000", "3.0", "3.5", "2.5", "3.2", "30", "96"],
    ]}
    restore = with_get(lambda path, **kw: payload)
    try:
        try:
            bybit.klines("XUSDT", "5m", limit=3)
            check("non-monotonic ts rejected", False, "silently accepted")
        except bybit.BybitError:
            check("non-monotonic ts rejected", True)
    finally:
        restore()


def test_implausible_bar_rejected():
    print("=== implausible bar (h < l) -> BybitError ===")
    payload = {"list": [
        ["300000", "2.0", "1.5", "2.5", "2.2", "20", "44"],   # high < low
        ["200000", "1.0", "1.5", "0.5", "1.1", "10", "11"],
    ]}
    restore = with_get(lambda path, **kw: payload)
    try:
        try:
            bybit.klines("XUSDT", "5m", limit=3)
            check("implausible bar rejected", False, "no exception raised")
        except bybit.BybitError:
            check("implausible bar rejected", True)
    finally:
        restore()


def test_empty_klines_not_an_error():
    print("=== empty kline payload -> [] ===")
    for payload in ({}, {"list": []}):
        restore = with_get(lambda path, p=payload, **kw: p)
        try:
            ok = bybit.klines("XUSDT", "5m", limit=3) == []
        finally:
            restore()
        check(f"empty {sorted(payload)} -> []", ok)


def test_unknown_interval_and_limit_clamp():
    print("=== unknown interval raises ValueError; limit clamps to 1..1000 ===")
    try:
        bybit.klines("XUSDT", "2m", limit=10)
        check("unknown interval rejected", False, "no exception")
    except ValueError:
        check("unknown interval rejected", True)

    paths = []
    restore = with_get(lambda path, **kw: paths.append(path) or {})
    try:
        bybit.klines("XUSDT", "5m", limit=5000)
        bybit.klines("XUSDT", "5m", limit=0)
    finally:
        restore()
    check("limit 5000 clamps to 1000", "limit=1000" in paths[0], str(paths))
    check("limit 0 clamps to 1", "limit=1" in paths[1], str(paths))


def test_envelope_check():
    print("=== envelope: retCode 0 unwraps result; else BybitError ===")
    import json as _json

    class FakeResp:
        def __init__(self, obj):
            self._body = _json.dumps(obj).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._body

    orig = bybit.urllib.request.urlopen
    try:
        bybit.urllib.request.urlopen = (
            lambda req, timeout=None: FakeResp({"retCode": 0, "retMsg": "OK",
                                                "result": {"list": []}}))
        check("retCode 0 unwraps result", bybit._get("/x") == {"list": []})

        bybit.urllib.request.urlopen = (
            lambda req, timeout=None: FakeResp({"retCode": 10001,
                                                "retMsg": "param error",
                                                "result": {}}))
        try:
            bybit._get("/x", retries=1)
            check("bad envelope rejected", False, "no exception")
        except bybit.BybitError:
            check("bad envelope rejected", True)
    finally:
        bybit.urllib.request.urlopen = orig


def test_tickers_maps_by_symbol():
    print("=== tickers -> {symbol: row} ===")
    payload = {"list": [{"symbol": "BTCUSDT", "lastPrice": "1"},
                        {"symbol": "ETHUSDT", "lastPrice": "2"}]}
    restore = with_get(lambda path, **kw: payload)
    try:
        rows = bybit.tickers()
    finally:
        restore()
    check("keyed by symbol", sorted(rows) == ["BTCUSDT", "ETHUSDT"], str(sorted(rows)))
    check("row passthrough", rows["ETHUSDT"]["lastPrice"] == "2", str(rows["ETHUSDT"]))


def test_details_paginates_cursor():
    print("=== details follows nextPageCursor across pages ===")
    calls, pages = [], {
        False: {"list": [{"symbol": "AUSDT"}], "nextPageCursor": "cur1"},
        True: {"list": [{"symbol": "BUSDT"}], "nextPageCursor": ""},
    }

    def fake(path, **kw):
        calls.append(path)
        return pages["cursor=" in path]

    restore = with_get(fake)
    try:
        rows = bybit.details()
    finally:
        restore()
    check("both pages merged", sorted(rows) == ["AUSDT", "BUSDT"], str(sorted(rows)))
    check("two requests", len(calls) == 2, str(calls))
    check("cursor passed on page 2", "cursor=cur1" in calls[1], str(calls))


def test_depth_pairs_parsed():
    print("=== depth -> (bids, asks) of (price, qty) floats ===")
    # v5 orderbook result uses short keys: s, b (bids), a (asks).
    payload = {"b": [["100.5", "1.5"], ["100.4", "2.0"]],
               "a": [["100.6", "3.0"], ["100.7", "4.0"]]}
    seen = {}

    def fake(path, **kw):
        seen["path"] = path
        return payload

    restore = with_get(fake)
    try:
        bids, asks = bybit.depth("XUSDT", limit=20)
    finally:
        restore()
    check("bids parsed", bids == [(100.5, 1.5), (100.4, 2.0)], str(bids))
    check("asks parsed", asks == [(100.6, 3.0), (100.7, 4.0)], str(asks))
    check("limit 20 snapped up to allowed 50", "limit=50" in seen["path"],
          seen["path"])


def test_funding_parses():
    print("=== funding -> {rate, cap} ===")
    restore = with_get(lambda path, **kw: {
        "list": [{"symbol": "BTCUSDT", "fundingRate": "0.00004085",
                  "fundingCap": "0.00333"}]})
    try:
        f = bybit.funding("BTCUSDT")
    finally:
        restore()
    check("rate parsed", abs(f["rate"] - 0.00004085) < 1e-12, str(f))
    check("cap parsed", abs(f["cap"] - 0.00333) < 1e-12, str(f))
    check("keys == {rate, cap}", set(f) == {"rate", "cap"}, str(sorted(f)))

    restore = with_get(lambda path, **kw: {
        "list": [{"symbol": "XUSDT", "fundingRate": "-0.0001"}]})
    try:
        f = bybit.funding("XUSDT")
    finally:
        restore()
    check("missing cap falls back to default", f["cap"] == bybit.FUNDING_CAP_DEFAULT,
          str(f))

    restore = with_get(lambda path, **kw: {"list": []})
    try:
        try:
            bybit.funding("NOPEUSDT")
            check("empty funding row rejected", False, "no exception")
        except bybit.BybitError:
            check("empty funding row rejected", True)
    finally:
        restore()


def test_oi_change_percent():
    print("=== oi_change: oldest->newest percent; no data -> None ===")
    payload = {"list": [
        {"openInterest": "110", "timestamp": "300000"},   # newest first
        {"openInterest": "105", "timestamp": "200000"},
        {"openInterest": "100", "timestamp": "100000"},
    ]}
    restore = with_get(lambda path, **kw: payload)
    try:
        pct = bybit.oi_change("XUSDT")
    finally:
        restore()
    check("+10% over the window", pct is not None and abs(pct - 10.0) < 1e-9,
          str(pct))

    restore = with_get(lambda path, **kw: {"list": [{"openInterest": "100",
                                                     "timestamp": "1"}]})
    try:
        pct = bybit.oi_change("XUSDT")
    finally:
        restore()
    check("single sample -> None", pct is None, str(pct))

    restore = with_get(lambda path, **kw: {"list": []})
    try:
        pct = bybit.oi_change("XUSDT")
    finally:
        restore()
    check("no samples -> None", pct is None, str(pct))

    restore = with_get(lambda path, **kw: (_ for _ in ()).throw(
        bybit.BybitError("venue down")))
    try:
        try:
            pct = bybit.oi_change("XUSDT")
            check("BybitError propagates (degraded venue stays visible)", False,
                  f"got {pct}")
        except bybit.BybitError:
            check("BybitError propagates (degraded venue stays visible)", True)
    finally:
        restore()


def run(fn):
    print(f"--- {fn.__name__}")
    try:
        fn()
    except Exception as e:
        print(f"  FAIL  {fn.__name__}  raised {type(e).__name__}: {e}")
        FAILURES.append(fn.__name__)


for t in (test_good_kline_payload_parses,
          test_missing_column_rejected,
          test_ragged_rows_rejected,
          test_non_monotonic_payload_rejected,
          test_implausible_bar_rejected,
          test_empty_klines_not_an_error,
          test_unknown_interval_and_limit_clamp,
          test_envelope_check,
          test_tickers_maps_by_symbol,
          test_details_paginates_cursor,
          test_depth_pairs_parsed,
          test_funding_parses,
          test_oi_change_percent):
    run(t)

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
