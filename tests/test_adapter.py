"""Offline tests for the MEXC adapter. No network - fixtures captured from live responses."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import mexc

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


print("=== canonicalization ===")

def canon(sym):
    """Normalize any venue symbol to a bare coin name."""
    import re
    s = sym.upper().strip()
    s = re.sub(r"[-_]?(USDT|USDC|USD)[\-_]?(PERP|SWAP)?$", "", s)
    s = re.sub(r"[\-_](PERP|SWAP|PRP)$", "", s)
    s = re.sub(r"(PERP|SWAP|PRP)$", "", s)
    return s.strip("_-")

check("BTCUSDT -> BTC", canon("BTCUSDT") == "BTC", canon("BTCUSDT"))
check("BTC-USDT-SWAP -> BTC", canon("BTC-USDT-SWAP") == "BTC", canon("BTC-USDT-SWAP"))
check("BTC_USDT -> BTC", canon("BTC_USDT") == "BTC", canon("BTC_USDT"))
check("1000BONK_USDT -> 1000BONK", canon("1000BONK_USDT") == "1000BONK", canon("1000BONK_USDT"))
check("ETH-PERP -> ETH", canon("ETH-PERP") == "ETH", canon("ETH-PERP"))

SYNTH_MARKERS = ("STOCK", "XAU", "XAG", "USOIL", "SOXL", "SPX", "NDX", "GLD", "SLV")
def is_synthetic(coin):
    return any(t in coin for t in SYNTH_MARKERS)

check("AAPLSTOCK is synthetic", is_synthetic(canon("AAPLSTOCK_USDT")))
check("XAU is synthetic", is_synthetic(canon("XAU_USDT")))
check("PEPE is not synthetic", not is_synthetic(canon("PEPE_USDT")))
check("CHEEMS is not synthetic", not is_synthetic(canon("CHEEMS_USDT")))

print("\n=== min notional arithmetic (measured values) ===")
# BTC: minVol 1 x contractSize 0.0001 x 83186.1 = 8.3186
n = mexc.min_notional("BTC_USDT", {"minVol": 1, "contractSize": 0.0001}, 83186.1)
check("BTC min notional ~8.32", abs(n - 8.31861) < 0.001, f"got {n}")
# QNT: minVol 1 x 0.01 x 251.30 = 2.513
n = mexc.min_notional("QNT_USDT", {"minVol": 1, "contractSize": 0.01}, 251.30)
check("QNT min notional ~2.513", abs(n - 2.513) < 0.001, f"got {n}")
check("missing fields -> None", mexc.min_notional("X", {}, 1.0) is None)

print("\n=== bar validation rejects junk ===")
# Simulated columnar payload missing 'vol' - the exact bug we hit live.
try:
    class FakeMexc:
        @staticmethod
        def _get(path, **kw):
            return {"time": [1, 2], "open": [1, 1], "high": [1, 1], "low": [1, 1],
                    "close": [1, 1], "amount": [1, 1]}  # 'vol' absent
    orig = mexc._get
    mexc._get = FakeMexc._get
    try:
        mexc.klines("X_USDT")
        check("missing 'vol' column rejected", False, "no exception raised")
    except mexc.MexcError as e:
        check("missing 'vol' column rejected", True)
    finally:
        mexc._get = orig
except Exception as e:
    check("missing 'vol' column rejected", False, str(e))

print("\n=== bar validation rejects non-monotonic timestamps ===")
try:
    class FakeMexc2:
        @staticmethod
        def _get(path, **kw):
            return {"time": [200, 100], "open": [1, 1], "high": [1, 1], "low": [1, 1],
                    "close": [1, 1], "vol": [1, 1], "amount": [1, 1]}
    orig = mexc._get
    mexc._get = FakeMexc2._get
    try:
        mexc.klines("X_USDT")
        check("non-monotonic ts rejected", False, "no exception")
    except mexc.MexcError:
        check("non-monotonic ts rejected", True)
    finally:
        mexc._get = orig
except Exception as e:
    check("non-monotonic ts rejected", False, str(e))

print("\n=== ragged column rejection ===")
try:
    class FakeMexc3:
        @staticmethod
        def _get(path, **kw):
            return {"time": [1, 2], "open": [1], "high": [1, 1], "low": [1, 1],
                    "close": [1, 1], "vol": [1, 1], "amount": [1, 1]}
    orig = mexc._get
    mexc._get = FakeMexc3._get
    try:
        mexc.klines("X_USDT")
        check("ragged columns rejected", False, "no exception")
    except mexc.MexcError:
        check("ragged columns rejected", True)
    finally:
        mexc._get = orig
except Exception as e:
    check("ragged columns rejected", False, str(e))

print("\n=== good payload parses correctly ===")
try:
    class FakeMexc4:
        @staticmethod
        def _get(path, **kw):
            # Ascending, as MEXC actually returns.
            return {"time": [100, 200, 300],
                    "open": [1.0, 2.0, 3.0], "high": [1.5, 2.5, 3.5],
                    "low": [0.5, 1.5, 2.5], "close": [1.1, 2.2, 3.2],
                    "vol": [10, 20, 30], "amount": [11, 44, 96]}
    orig = mexc._get
    mexc._get = FakeMexc4._get
    bars = mexc.klines("X_USDT")
    mexc._get = orig
    check("three bars parsed", len(bars) == 3, f"got {len(bars)}")
    check("timestamps ascending", [b["ts"] for b in bars] == [100, 200, 300],
          str([b["ts"] for b in bars]))
    check("values map correctly", bars[0]["o"] == 1.0 and bars[0]["c"] == 1.1,
          str(bars[0]))
    check("vol and amount distinct", bars[0]["vol"] == 10.0 and bars[0]["amount"] == 11.0,
          str(bars[0]))
except Exception as e:
    check("good payload parses", False, str(e))

print("\n=== window is interval-aware and truncated to limit ===")
try:
    seen = {}

    class FakeMexcW:
        @staticmethod
        def _get(path, **kw):
            seen["path"] = path
            n = 5
            return {"time": list(range(100, 100 + n)),
                    "open": [1.0] * n, "high": [1.5] * n, "low": [0.5] * n,
                    "close": [1.1] * n, "vol": [10] * n, "amount": [11] * n}
    orig = mexc._get
    mexc._get = FakeMexcW._get
    try:
        bars = mexc.klines("X_USDT", "5m", limit=3)
    finally:
        mexc._get = orig
    check("truncated to limit (keeps most recent)",
          [b["ts"] for b in bars] == [102, 103, 104], str([b["ts"] for b in bars]))
    import re as _re
    m = _re.search(r"start=(\d+)&end=(\d+)", seen["path"])
    span = int(m.group(2)) - int(m.group(1))
    check("5m window spans limit*300s (+1 spare), not limit*3600s",
          span == 3 * 300 + 300, f"span={span}")
except Exception as e:
    check("interval-aware window", False, str(e))

print("\n=== still-forming bar is dropped, history kept ===")
try:
    import time as _t
    now = int(_t.time())

    class FakeMexcF:
        @staticmethod
        def _get(path, **kw):
            # two closed 5m bars + one forming right now
            return {"time": [now - 900, now - 600, now - 60],
                    "open": [1.0] * 3, "high": [1.5] * 3, "low": [0.5] * 3,
                    "close": [1.1] * 3, "vol": [10] * 3, "amount": [11] * 3}
    orig = mexc._get
    mexc._get = FakeMexcF._get
    try:
        bars = mexc.klines("X_USDT", "5m", limit=200)
    finally:
        mexc._get = orig
    check("forming bar dropped",
          [b["ts"] for b in bars] == [now - 900, now - 600],
          str([b["ts"] for b in bars]))
except Exception as e:
    check("forming bar dropped", False, str(e))

print("\n=== non-finite and inconsistent bars rejected ===")
try:
    class FakeMexcN:
        which = "nan"

        @staticmethod
        def _get(path, **kw):
            import math as _m
            if FakeMexcN.which == "nan":
                cl = [1.1, float("nan"), 3.2]
            else:  # open outside [low, high], but high >= low
                cl = [1.1, 2.2, 2.6]
            hi = [1.5, 2.5, 2.8] if FakeMexcN.which != "nan" else [1.5, 2.5, 3.5]
            return {"time": [100, 200, 300],
                    "open": [1.0, 2.0, 3.0], "high": hi, "low": [0.5, 1.5, 2.5],
                    "close": cl, "vol": [10, 20, 30], "amount": [11, 44, 96]}
    orig = mexc._get
    mexc._get = FakeMexcN._get
    try:
        FakeMexcN.which = "nan"
        try:
            mexc.klines("X_USDT")
            check("NaN close rejected", False, "silently accepted")
        except mexc.MexcError:
            check("NaN close rejected", True)
        FakeMexcN.which = "inconsistent"
        try:
            mexc.klines("X_USDT")
            check("open outside [low, high] rejected", False, "silently accepted")
        except mexc.MexcError:
            check("open outside [low, high] rejected", True)
    finally:
        mexc._get = orig
except Exception as e:
    check("bar invariance checks", False, str(e))

print("\n=== out-of-order payload is rejected, not silently sorted ===")
try:
    class FakeMexc5:
        @staticmethod
        def _get(path, **kw):
            return {"time": [300, 100, 200],
                    "open": [3.0, 1.0, 2.0], "high": [3.5, 1.5, 2.5],
                    "low": [2.5, 0.5, 1.5], "close": [3.2, 1.1, 2.2],
                    "vol": [30, 10, 20], "amount": [96, 11, 44]}
    orig = mexc._get
    mexc._get = FakeMexc5._get
    try:
        mexc.klines("X_USDT")
        check("out-of-order rejected", False, "silently accepted")
    except mexc.MexcError:
        check("out-of-order rejected", True)
    finally:
        mexc._get = orig
except Exception as e:
    check("out-of-order rejected", False, str(e))

print("\n=== empty result is not an error ===")
try:
    class FakeMexc6:
        @staticmethod
        def _get(path, **kw):
            return {}
    orig = mexc._get
    mexc._get = FakeMexc6._get
    check("empty -> []", mexc.klines("X_USDT") == [])
    mexc._get = orig
except Exception as e:
    check("empty -> []", False, str(e))

print("\n=== min notional feasibility at $0.10 stake ===")
STAKE = 0.10
cases = [("BTC_USDT", 1, 0.0001, 83186.1), ("ETH_USDT", 1, 0.01, 2568.26),
         ("QNT_USDT", 1, 0.01, 251.30), ("NEAR_USDT", 1, 1.0, 5.361),
         ("CHEEMS_USDT", 1, 100000.0, 0.000005364)]
for sym, mv, cs, px in cases:
    nn = mexc.min_notional(sym, {"minVol": mv, "contractSize": cs}, px)
    ok = nn <= STAKE
    print(f"  {sym:<14} min_notional=${nn:<12.5f} tradeable@$0.10: {'YES' if ok else 'NO'}")

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)