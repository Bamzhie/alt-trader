"""WS4 mark/index price history tests.

Self-checking script: python3 tests/test_prices.py
Covers proto/prices.py + proto/ratelimit.py + mexc.price_klines:
  1. adapter columnar shape/raw-monotonic validation; forming-candle and
     value validation live in the module
  2. parse_candles: forming drop, non-finite/negative prices, bad OHLC,
     bad ts, duplicates, ascending order
  3. archive append/idempotence in tmp dirs; no rewrite (hash check)
  4. collect_coin bounded window paced through PRICE_LIMITER; transport
     errors propagate
  5. backfill backward paging + per-coin failure isolation; collect_recent
     one recent window, idempotent
  6. daemon throttle + sweep isolation; config alignment
  7. bar-quality audit covers the hourly grid
"""

import gzip
import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import ratelimit as rlmod
from proto import prices as pmod
from proto import mexc as mexmod

FAILURES = []


def check(n, c, d=""):
    print(f"  {'PASS' if c else 'FAIL'}  {n}" + ("" if c else f"  {d}"))
    if not c:
        FAILURES.append(n)


def crow(ts, o=100.0, h=101.0, l=99.0, c=100.5):
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c}


BASE = 1792224000

print("=== case 1: adapter columnar validation ===")
_orig_get = mexmod._get


def _fake_get(payload):
    def _f(path, *a, **k):
        return payload
    return _f


good = {
    "time": [BASE + 2 * 3600, BASE + 3 * 3600, BASE + 4 * 3600],
    "open": [100.0, 101.0, 102.0],
    "high": [101.0, 102.0, 103.0],
    "low": [99.0, 100.0, 101.0],
    "close": [100.5, 101.5, 102.5],
    "vol": [0.0, 0.0, 0.0],
    "amount": [0.0, 0.0, 0.0],
    "realOpen": [100.0, 101.0, 102.0],
}
mexmod._get = _fake_get(good)
try:
    rows = mexmod.price_klines("mark", "BTC_USDT", "1H", BASE, BASE + 4 * 3600)
    check("mark adapter returns 3 rows", len(rows) == 3, str(rows))
    check("mark adapter keeps documented OHLC only",
          all(set(r) == {"ts", "o", "h", "l", "c"} for r in rows),
          str(rows[:1]))
    check("mark adapter ascending",
          [r["ts"] for r in rows] == [BASE + 2 * 3600, BASE + 3 * 3600,
                                      BASE + 4 * 3600], str(rows))
finally:
    mexmod._get = _orig_get

mexmod._get = _fake_get({"time": [BASE, BASE + 3600], "open": [1.0],
                         "high": [2.0], "low": [0.5], "close": [1.5]})
try:
    mexmod.price_klines("index", "BTC_USDT", "1H", BASE, BASE + 7200)
    check("ragged columns -> MexcError", False, "no error")
except mexmod.MexcError:
    check("ragged columns -> MexcError", True)
finally:
    mexmod._get = _orig_get

mexmod._get = _fake_get({"time": [BASE], "open": [1.0], "high": [2.0],
                         "low": [0.5]})
try:
    mexmod.price_klines("index", "BTC_USDT", "1H", BASE, BASE + 3600)
    check("missing close column -> MexcError", False, "no error")
except mexmod.MexcError:
    check("missing close column -> MexcError", True)
finally:
    mexmod._get = _orig_get

mexmod._get = _fake_get({"time": [BASE + 3600, BASE], "open": [1.0, 1.0],
                         "high": [2.0, 2.0], "low": [0.5, 0.5],
                         "close": [1.5, 1.5]})
try:
    mexmod.price_klines("index", "BTC_USDT", "1H", BASE, BASE + 7200)
    check("non-monotonic raw timestamps -> MexcError", False, "no error")
except mexmod.MexcError:
    check("non-monotonic raw timestamps -> MexcError", True)
finally:
    mexmod._get = _orig_get

mexmod._get = _fake_get({})
try:
    check("empty data -> []", mexmod.price_klines("index", "BTC", "1H",
                                                  BASE, BASE) == [])
finally:
    mexmod._get = _orig_get

try:
    mexmod.price_klines("bogus", "BTC_USDT", "1H", BASE, BASE)
    check("unknown kind -> ValueError", False, "no error")
except ValueError:
    check("unknown kind -> ValueError", True)

print("\n=== case 2: parse_candles validation, never invents ===")
NOW = BASE + 4 * 3600 + 1800  # last candle (BASE+4h) still forming
rows, skipped = pmod.parse_candles("BTC_USDT", [
    crow(BASE), crow(BASE + 3600), crow(BASE + 2 * 3600),
    crow(BASE + 3 * 3600), crow(BASE + 4 * 3600)], now=NOW)
check("forming candle dropped, 4 kept",
      [r["ts"] for r in rows] == [BASE, BASE + 3600, BASE + 2 * 3600,
                                  BASE + 3 * 3600] and skipped == {},
      str((rows, skipped)))

rows, skipped = pmod.parse_candles("BTC_USDT", [
    crow(BASE, o=float("nan")), crow(BASE + 3600, c=float("inf")),
    crow(BASE + 2 * 3600, o=-1.0), crow(BASE + 3 * 3600, l=0.0)], now=NOW)
check("non-finite/negative/zero prices rejected and counted",
      rows == [] and skipped.get("bad_price") == 4, str((rows, skipped)))

rows, skipped = pmod.parse_candles("BTC_USDT", [
    crow(BASE, h=100.0),                 # high below close
    crow(BASE + 3600, l=101.0),          # low above open
    crow(BASE + 2 * 3600, l=102.0, h=100.0)], now=NOW)  # low > high
check("OHLC integrity violations rejected and counted",
      rows == [] and skipped.get("bad_ohlc") == 3, str((rows, skipped)))

rows, skipped = pmod.parse_candles("BTC_USDT", [
    {"ts": None, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5},
    {"ts": "bogus", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5},
    {"ts": 0, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5}], now=NOW)
check("bad/missing/zero ts rejected and counted",
      rows == [] and skipped.get("bad_ts") == 3, str((rows, skipped)))

rows, skipped = pmod.parse_candles("BTC_USDT", [
    crow(BASE + 3600, c=100.0), crow(BASE), crow(BASE + 3600, c=100.3)],
    now=NOW)
check("out-of-order sorted, duplicate ts keeps first + counts conflict",
      [r["ts"] for r in rows] == [BASE, BASE + 3600]
      and rows[1]["c"] == 100.0 and skipped.get("duplicate_ts") == 1,
      str((rows, skipped)))

print("\n=== case 3: archive append/idempotence, no rewrite ===")
tmp3 = tempfile.mkdtemp(prefix="prices-")
n1 = pmod.append_prices(tmp3, "mark", "BTC", [
    crow(BASE), crow(BASE + 3600)])
n2 = pmod.append_prices(tmp3, "mark", "BTC", [
    crow(BASE + 3600), crow(BASE + 2 * 3600)])
check("first append writes 2", n1 == 2, str(n1))
check("re-fetched ts deduped (1 new)", n2 == 1, str(n2))
rows3 = pmod.read_prices(tmp3, "mark", "BTC")
check("read back chronological",
      [r["ts"] for r in rows3] == [BASE, BASE + 3600, BASE + 2 * 3600],
      str(rows3))
check("read preserves OHLC values",
      rows3[0]["o"] == 100.0 and rows3[0]["c"] == 100.5, str(rows3[0]))
path3 = os.path.join(tmp3, "mark", "BTC.csv.gz")
before = hashlib.sha256(open(path3, "rb").read()).hexdigest()
_ = pmod.read_prices(tmp3, "mark", "BTC")
after = hashlib.sha256(open(path3, "rb").read()).hexdigest()
check("read never rewrites", before == after, "bytes changed")
check("missing coin reads empty",
      pmod.read_prices(tmp3, "mark", "NOPE") == [])
check("series are separate files",
      pmod.read_prices(tmp3, "index", "BTC") == [])
try:
    pmod.read_prices(tmp3, "bogus", "BTC")
    check("unknown series rejected", False, "no error")
except pmod.PriceError:
    check("unknown series rejected", True)

print("\n=== case 4: collect_coin bounded window + limiter ===")
calls = []


def _fake_klines(kind, symbol, interval, start, end, **kw):
    calls.append((kind, symbol, interval, start, end))
    if symbol == "BOOM_USDT":
        raise mexmod.MexcError("boom")
    return [crow(BASE + i * 3600) for i in range(3)]


real_klines = mexmod.price_klines
mexmod.price_klines = _fake_klines
acquire = {"n": 0}
real_limiter = pmod.PRICE_LIMITER


class _CountLimiter:
    def acquire(self):
        acquire["n"] += 1


pmod.PRICE_LIMITER = _CountLimiter()
try:
    tmp4 = tempfile.mkdtemp(prefix="prices-collect-")
    res = pmod.collect_coin(tmp4, "mark", "BTC", "BTC_USDT",
                            start=BASE, end=BASE + 2 * 3600, now=NOW)
    check("collect_coin records 3 rows",
          res == {"series": "mark", "coin": "BTC", "new": 3, "skipped": {}},
          str(res))
    check("collect_coin paces through PRICE_LIMITER",
          acquire["n"] == 1, str(acquire))
    check("collect_coin passes bounded window to the venue",
          calls[-1] == ("mark", "BTC_USDT", "1H", BASE, BASE + 2 * 3600),
          str(calls[-1]))
    res = pmod.collect_coin(tmp4, "mark", "BTC", "BTC_USDT",
                            start=BASE, end=BASE + 2 * 3600, now=NOW)
    check("re-collect records zero new", res["new"] == 0, str(res))
finally:
    mexmod.price_klines = real_klines
    pmod.PRICE_LIMITER = real_limiter

mexmod.price_klines = lambda *a, **k: (_ for _ in ()).throw(
    mexmod.MexcError("/kline: HTTPError 429"))
try:
    pmod.collect_coin(tempfile.mkdtemp(), "mark", "BTC", "BTC_USDT",
                      start=BASE, end=BASE)
    check("transport error propagates", False, "no error")
except mexmod.MexcError:
    check("transport error propagates", True)
finally:
    mexmod.price_klines = real_klines

print("\n=== case 5: backfill paging + isolation; collect_recent ===")


def _fake_klines_page(kind, symbol, interval, start, end, **kw):
    if symbol == "BOOM_USDT":
        raise mexmod.MexcError("boom")
    # return two candles inside the window
    return [crow(start), crow(min(start + 3600, end))]


mexmod.price_klines = _fake_klines_page
pmod.PRICE_LIMITER = _CountLimiter()
try:
    tmp5 = tempfile.mkdtemp(prefix="prices-backfill-")
    out = pmod.backfill(tmp5, series=("mark", "index"), days=2,
                        window_s=3 * 3600, now=NOW,
                        coins={"BTC": "BTC_USDT", "BOOM": "BOOM_USDT"})
    check("backfill returns per-series per-coin results",
          set(out) == {"mark", "index"} and set(out["mark"]) == {"BTC", "BOOM"},
          str(out))
    check("backfill pages backward more than once",
          out["mark"]["BTC"]["requests"] >= 2, str(out["mark"]["BTC"]))
    check("backfill isolates per-coin failure",
          "error" in out["mark"]["BOOM"]
          and out["mark"]["BOOM"]["new"] == 0, str(out["mark"]["BOOM"]))
    check("backfill index series also collected",
          out["index"]["BTC"]["new"] > 0, str(out["index"]["BTC"]))

    tmp5b = tempfile.mkdtemp(prefix="prices-recent-")
    res = pmod.collect_recent(tmp5b, series=("mark",),
                              coins={"BTC": "BTC_USDT"}, now=NOW,
                              lookback_s=2 * 3600)
    check("collect_recent records a recent window",
          res["mark"]["BTC"]["new"] > 0, str(res))
    res = pmod.collect_recent(tmp5b, series=("mark",),
                              coins={"BTC": "BTC_USDT"}, now=NOW,
                              lookback_s=2 * 3600)
    check("collect_recent is idempotent",
          res["mark"]["BTC"]["new"] == 0, str(res))
finally:
    mexmod.price_klines = real_klines
    pmod.PRICE_LIMITER = real_limiter

print("\n=== case 6: rate limiter window enforcement ===")
lim = rlmod.RollingRateLimiter(rate=4, window_s=1.0)
lim.reset()
import time as _t
t0 = _t.monotonic()
for _ in range(4):
    lim.acquire()
t1 = _t.monotonic()
check("first N acquires non-blocking", t1 - t0 < 0.5, f"{t1 - t0:.3f}")
t2 = _t.monotonic()
lim.acquire()
t3 = _t.monotonic()
check("(N+1)th acquire blocks (window enforced)", t3 - t2 >= 0.9,
      f"{t3 - t2:.3f}")

print("\n=== case 7: daemon throttle + sweep isolation + alignment ===")
tmp7 = tempfile.mkdtemp(prefix="prices-throttle-")
seen = []


def _fake_recent(data_dir, series=None, coins=None, now=None, lookback_s=None):
    seen.append(data_dir)
    return {"mark": {"BTC": {"series": "mark", "coin": "BTC", "new": 1,
                             "skipped": {}}}}


real_recent = pmod.collect_recent
pmod.collect_recent = _fake_recent
try:
    out = pmod.maybe_collect(tmp7)
    check("due throttle collects", out["mark"]["BTC"]["new"] == 1
          and seen == [tmp7], str((out, seen)))
    seen.clear()
    out = pmod.maybe_collect(tmp7)
    check("fresh throttle mark skips venue", out == {} and seen == [],
          str((out, seen)))
finally:
    pmod.collect_recent = real_recent

from types import SimpleNamespace
from proto import daemon as dmod
from proto import collector as colmod
from proto import funding as fmod
from proto import settlements as smod
from proto import service as svc

real_collect = colmod.collect_full_universe
real_funding = fmod.collect_funding
real_settle = smod.maybe_collect
real_app = dmod.App
real_price_maybe = pmod.maybe_collect
colmod.collect_full_universe = lambda *a, **k: {}
fmod.collect_funding = lambda *a, **k: {}
smod.maybe_collect = lambda *a, **k: {}
sweeps = []


class _FakeApp:
    def __init__(self, args):
        pass


dmod.App = _FakeApp
boom = []


def _boom(*a, **k):
    boom.append(a)
    raise RuntimeError("prices boom")


pmod.maybe_collect = _boom
try:
    args = SimpleNamespace(stake=0.10, coins=150, interval=5,
                           db=os.path.join(tmp7, "s.db"),
                           log_threshold=24.0, use_v3=False,
                           collect_every=0, resolve_every=10 ** 12,
                           measure_every=10 ** 12, iterations=1,
                           no_scan=True, quant_shadow=False,
                           settlements_dir=None, prices_dir=None)
    dmod.run_daemon_loop(args, on_sweep=lambda e, v, **k: sweeps.append((e, v)))
    check("daemon loop consults prices collect", boom != [],
          "maybe_collect never called")
    check("prices failure never fails the sweep", sweeps != [], str(sweeps))
finally:
    colmod.collect_full_universe = real_collect
    fmod.collect_funding = real_funding
    smod.maybe_collect = real_settle
    pmod.maybe_collect = real_price_maybe
    dmod.App = real_app

parser = dmod.build_parser()
parsed = parser.parse_args(["--prices-dir", "/tmp/p", "--no-scan",
                            "--iterations", "1"])
check("daemon parser accepts --prices-dir", parsed.prices_dir == "/tmp/p",
      str(parsed.prices_dir))
sparsed = svc.build_service_parser().parse_args(
    ["--prices-dir", "/tmp/p", "--no-scan", "--iterations", "1",
     "--port", "0", "--db", "/tmp/svc-test.db"])
check("service parser accepts --prices-dir", sparsed.prices_dir == "/tmp/p",
      str(sparsed.prices_dir))
argv = svc._daemon_argv_for(sparsed)
check("_daemon_argv_for forwards --prices-dir",
      "--prices-dir" in argv and "/tmp/p" in argv, str(argv))
custom = "/tmp/custom_bars"
check("custom --data-dir resolves prices sibling",
      svc._resolve_archive_dir(None, custom, "prices")
      == os.path.join(os.path.dirname(custom), "prices"),
      "sibling mismatch")
check("route default sibling == module PRICES_DIR for default data_dir",
      os.path.join(os.path.dirname("data/bars"), "prices") == pmod.PRICES_DIR,
      f"{os.path.join(os.path.dirname('data/bars'), 'prices')} vs "
      f"{pmod.PRICES_DIR}")

print("\n=== case 8: bar-quality audit covers the hourly grid ===")
from proto.bar_quality import audit_archive_file
tmp8 = tempfile.mkdtemp(prefix="prices-audit-")
pmod.append_prices(tmp8, "mark", "BTC", [crow(BASE + i * 3600)
                                         for i in range(5)])
rep = audit_archive_file(os.path.join(tmp8, "mark"), "BTC", tf_sec=3600)
check("hourly grid audits perfect",
      rep["count"] == 5 and rep["coverage"]["ratio"] == 1.0
      and rep["missing_count"] == 0 and rep["invalid_rows"] == 0,
      str({k: rep[k] for k in ("count", "missing_count", "invalid_rows")}))

print()
if FAILURES:
    print(f"FAIL  tests/test_prices.py  ({len(FAILURES)} failed: {FAILURES})")
    sys.exit(1)
print("ALL PASS  tests/test_prices.py")
