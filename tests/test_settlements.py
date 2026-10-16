"""WS4 venue-reported funding settlement history tests.

Self-checking script: python3 tests/test_settlements.py
Covers proto/settlements.py + mexc.funding_history:
  1. verbatim 2026-10-17 probe response parses (ms->s, rate, cycle)
  2. validation: non-finite/scientific rates, symbol mismatch, missing
     fields, bad pagination echo, out-of-order rows, duplicate settle_ts
  3. transport/rate-limit errors propagate (never silent empty)
  4. archive append/idempotence in tmp dirs; no rewrite (hash check);
     production data/ untouched
  5. backfill per-coin-failure isolation; truncated flag on max_pages;
     collect_recent keeps only rows newer than archived max
"""

import gzip
import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import settlements as smod

FAILURES = []


def check(n, c, d=""):
    print(f"  {'PASS' if c else 'FAIL'}  {n}" + ("" if c else f"  {d}"))
    if not c:
        FAILURES.append(n)


# Verbatim shape of the 2026-10-17 live probe (BTC_USDT, page 1, size 3).
PROBE_DATA = {
    "pageSize": 3, "totalCount": 2931, "totalPage": 977, "currentPage": 1,
    "resultList": [
        {"symbol": "BTC_USDT", "fundingRate": 4.2e-05,
         "settleTime": 1792166400000, "collectCycle": 8},
        {"symbol": "BTC_USDT", "fundingRate": 6.8e-05,
         "settleTime": 1792137600000, "collectCycle": 8},
        {"symbol": "BTC_USDT", "fundingRate": 5.3e-05,
         "settleTime": 1792108800000, "collectCycle": 8},
    ],
}

print("=== case 1: verbatim probe response parses ===")
rows, info, skipped = smod.parse_page("BTC_USDT", PROBE_DATA)
check("3 rows parsed", len(rows) == 3, str(rows))
check("ms->s conversion exact, ascending",
      [r["settle_ts"] for r in rows] == [1792108800, 1792137600, 1792166400],
      str(rows))
check("scientific-notation rate kept",
      rows[2]["settled_rate"] == 4.2e-05, str(rows[2]))
check("cycle context recorded",
      [r["cycle_hours"] for r in rows] == [8, 8, 8], str(rows))
check("pagination echoed",
      info == {"page": 1, "total_pages": 977, "total_count": 2931}, str(info))
check("nothing skipped", skipped == {}, str(skipped))

print("\n=== case 2: validation rejects, never invents ===")


def page_with(rows, **kw):
    d = {"pageSize": len(rows), "totalCount": len(rows), "totalPage": 1,
         "currentPage": 1, "resultList": rows}
    d.update(kw)
    return d


def srow(sym="BTC_USDT", rate=0.0001, ts=1792166400000, cycle=8):
    r = {"symbol": sym, "fundingRate": rate, "settleTime": ts}
    if cycle is not None:
        r["collectCycle"] = cycle
    return r


rows, _, skipped = smod.parse_page(
    "BTC_USDT", page_with([srow(rate=float("nan")), srow(rate=float("inf")),
                           srow(rate="bogus"), srow(rate=None)]))
check("non-finite/non-numeric rates rejected, counted",
      rows == [] and skipped.get("bad_rate") == 4, str((rows, skipped)))

rows, _, skipped = smod.parse_page(
    "BTC_USDT", page_with([srow(sym="ETH_USDT"), srow()]))
check("symbol mismatch rejected, counted",
      len(rows) == 1 and skipped.get("symbol_mismatch") == 1,
      str((rows, skipped)))

rows, _, skipped = smod.parse_page(
    "BTC_USDT", page_with([{"symbol": "BTC_USDT"}, srow()]))
check("missing fields rejected per field, counted",
      len(rows) == 1 and skipped.get("bad_settle_time") == 1
      and skipped.get("bad_rate") == 1, str((rows, skipped)))

rows, _, skipped = smod.parse_page(
    "BTC_USDT", page_with([{"symbol": "BTC_USDT", "fundingRate": 0.0001,
                             "settleTime": 1792166400000}]))
check("absent collectCycle keeps row with null context",
      len(rows) == 1 and rows[0]["cycle_hours"] is None, str(rows))

rows, _, skipped = smod.parse_page(
    "BTC_USDT", page_with([srow(ts=1792108800000), srow(ts=1792166400000),
                           srow(ts=1792166400000, rate=0.0002)]))
check("out-of-order sorted, duplicate ts keeps first + counts conflict",
      [r["settle_ts"] for r in rows] == [1792108800, 1792166400]
      and rows[1]["settled_rate"] == 0.0001
      and skipped.get("duplicate_ts") == 1, str((rows, skipped)))

try:
    smod.parse_page("BTC_USDT", {"resultList": []})
    check("bad pagination echo raises", False, "no error")
except smod.SettlementError as e:
    check("bad pagination echo raises", True, str(e)[:60])

try:
    smod.parse_page("BTC_USDT", {"pageSize": 0, "totalCount": 0,
                                 "totalPage": 0, "currentPage": 2,
                                 "resultList": []})
    check("page beyond total raises", False, "no error")
except smod.SettlementError as e:
    check("page beyond total raises", True, str(e)[:60])

print("\n=== case 3: transport errors propagate ===")
from proto import mexc as mexmod

real_history = getattr(mexmod, "funding_history", None)


def _boom(*a, **k):
    raise mexmod.MexcError("/funding_rate/history: HTTPError 429")


mexmod.funding_history = _boom
try:
    smod.collect_coin(tempfile.mkdtemp(), "BTC", "BTC_USDT")
    check("transport error propagates", False, "no error")
except mexmod.MexcError:
    check("transport error propagates", True)
finally:
    if real_history is None:
        del mexmod.funding_history
    else:
        mexmod.funding_history = real_history

print("\n=== case 4: archive append/idempotence, no rewrite ===")
tmp4 = tempfile.mkdtemp(prefix="settle-")
n1 = smod.append_settlements(tmp4, "BTC", [
    {"settle_ts": 1792166400, "settled_rate": 4.2e-05, "cycle_hours": 8},
    {"settle_ts": 1792137600, "settled_rate": 6.8e-05, "cycle_hours": 8}])
n2 = smod.append_settlements(tmp4, "BTC", [
    {"settle_ts": 1792137600, "settled_rate": 6.8e-05, "cycle_hours": 8},
    {"settle_ts": 1792108800, "settled_rate": 5.3e-05, "cycle_hours": 8}])
check("first append writes 2", n1 == 2, str(n1))
check("re-fetched ts deduped (1 new)", n2 == 1, str(n2))
rows4 = smod.read_settlements(tmp4, "BTC")
check("read back chronological",
      [r["settle_ts"] for r in rows4] == [1792108800, 1792137600, 1792166400],
      str(rows4))
path4 = os.path.join(tmp4, "BTC.csv.gz")
before = hashlib.sha256(open(path4, "rb").read()).hexdigest()
_ = smod.read_settlements(tmp4, "BTC")
after = hashlib.sha256(open(path4, "rb").read()).hexdigest()
check("read never rewrites", before == after, "bytes changed")
check("missing coin reads empty", smod.read_settlements(tmp4, "NOPE") == [])

print("\n=== case 5: collect_coin paging, truncation, backfill isolation ===")

PAGES = {
    1: {"pageSize": 2, "totalCount": 3, "totalPage": 2, "currentPage": 1,
        "resultList": [srow(ts=1792166400000), srow(ts=1792137600000)]},
    2: {"pageSize": 2, "totalCount": 3, "totalPage": 2, "currentPage": 2,
        "resultList": [srow(ts=1792108800000)]},
}


def _fake_history(symbol, page_num, page_size=1000):
    if symbol == "BOOM_USDT":
        raise mexmod.MexcError("boom")
    return dict(PAGES[page_num], resultList=list(PAGES[page_num]["resultList"]))


mexmod.funding_history = _fake_history
try:
    tmp5 = tempfile.mkdtemp(prefix="settle-collect-")
    res = smod.collect_coin(tmp5, "BTC", "BTC_USDT", max_pages=10)
    check("two pages collected without truncation",
          res == {"coin": "BTC", "new": 3, "skipped": {},
                  "pages": 2, "truncated": False}, str(res))
    res = smod.collect_coin(tmp5, "BTC", "BTC_USDT", max_pages=10)
    check("re-collect records zero new", res["new"] == 0
          and res["truncated"] is False, str(res))
    res = smod.collect_coin(tempfile.mkdtemp(), "BTC", "BTC_USDT",
                            max_pages=1)
    check("max_pages hit reports truncated, never silent",
          res["pages"] == 1 and res["truncated"] is True
          and res["new"] == 2, str(res))

    out = smod.backfill(tempfile.mkdtemp(), max_pages_per_coin=10,
                        coins={"BTC": "BTC_USDT", "BOOM": "BOOM_USDT"})
    check("backfill isolates per-coin failure",
          out["BTC"]["new"] == 3 and "error" in out["BOOM"]
          and out["BOOM"]["new"] == 0, str(out))
finally:
    if real_history is None:
        del mexmod.funding_history
    else:
        mexmod.funding_history = real_history

print("\n=== case 6: collect_recent keeps only newer rows ===")


def _fake_history_recent(symbol, page_num, page_size=1000):
    assert page_num == 1, "recent must read page 1 only"
    return {"pageSize": 2, "totalCount": 9, "totalPage": 5, "currentPage": 1,
            "resultList": [srow(ts=1792195200000), srow(ts=1792166400000)]}


mexmod.funding_history = _fake_history_recent
try:
    tmp6 = tempfile.mkdtemp(prefix="settle-recent-")
    smod.append_settlements(tmp6, "BTC", [
        {"settle_ts": 1792166400, "settled_rate": 4.2e-05, "cycle_hours": 8}])
    res = smod.collect_recent(tmp6, coins={"BTC": "BTC_USDT"})
    check("only newer-than-max recorded",
          res == {"BTC": {"coin": "BTC", "new": 1, "skipped": {},
                          "pages": 1, "truncated": False}}, str(res))
    check("archive holds both settles",
          [r["settle_ts"] for r in smod.read_settlements(tmp6, "BTC")]
          == [1792166400, 1792195200],
          str(smod.read_settlements(tmp6, "BTC")))
finally:
    if real_history is None:
        del mexmod.funding_history
    else:
        mexmod.funding_history = real_history

print("\n=== case 7: daemon throttle + failure isolation ===")
from types import SimpleNamespace

tmp7 = tempfile.mkdtemp(prefix="settle-throttle-")
calls = []


def _fake_recent(data_dir, coins=None):
    calls.append(data_dir)
    return {"BTC": {"coin": "BTC", "new": 1, "skipped": {},
                    "pages": 1, "truncated": False}}


real_recent = smod.collect_recent
smod.collect_recent = _fake_recent
try:
    out = smod.maybe_collect(tmp7)
    check("due throttle collects", out["BTC"]["new"] == 1
          and calls == [tmp7], str((out, calls)))
    calls.clear()
    out = smod.maybe_collect(tmp7)
    check("fresh throttle mark skips venue", out == {} and calls == [],
          str((out, calls)))
finally:
    smod.collect_recent = real_recent

from proto import daemon as dmod
from proto import collector as colmod
from proto import funding as fmod

real_collect = colmod.collect_full_universe
real_maybe = smod.maybe_collect
real_app = dmod.App
real_funding = fmod.collect_funding
sweeps = []
colmod.collect_full_universe = lambda *a, **k: {}
fmod.collect_funding = lambda *a, **k: {}


class _FakeApp:
    def __init__(self, args):
        pass


dmod.App = _FakeApp
boom_calls = []


def _boom_maybe(*a, **k):
    boom_calls.append(a)
    raise RuntimeError("settlements boom")


smod.maybe_collect = _boom_maybe
try:
    args = SimpleNamespace(stake=0.10, coins=150, interval=5,
                           db=os.path.join(tmp7, "s.db"),
                           log_threshold=24.0, use_v3=False,
                           collect_every=0, resolve_every=10 ** 12,
                           measure_every=10 ** 12, iterations=1,
                           no_scan=True, quant_shadow=False)
    dmod.run_daemon_loop(args, on_sweep=lambda e, v, **k: sweeps.append((e, v)))
    check("daemon loop consults settlements collect",
          boom_calls != [], "maybe_collect never called")
    check("settlements failure never fails the sweep", sweeps != [],
          str(sweeps))
finally:
    colmod.collect_full_universe = real_collect
    smod.maybe_collect = real_maybe
    dmod.App = real_app
    fmod.collect_funding = real_funding

print()
if FAILURES:
    print(f"FAIL  tests/test_settlements.py  "
          f"({len(FAILURES)} failed: {FAILURES})")
    sys.exit(1)
print("ALL PASS  tests/test_settlements.py")
