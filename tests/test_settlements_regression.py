"""WS4 regression tests for the four post-review findings.

Self-checking script: python3 tests/test_settlements_regression.py

Covers:
  1. Daemon + /settlements route share the configured settlements_dir
     (or both fall back to the same default).
  2. parse_page rejects a currentPage that doesn't match the requested page.
  3. Universe backfill/collect_recent pace venue requests through scan.LIMITER
     (no unbounded bursts).
  4. Archive reads (read_settlements) reject non-finite settled rates and
     invalid cycle values so the API never emits non-standard JSON.
"""

import gzip
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

FAILURES = []
CHECKS = 0


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    cond = bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)
    return cond


# ---------------------------------------------------------------------------
# Finding 2: currentPage must match the requested page
# ---------------------------------------------------------------------------
def page_with(rows, currentPage=1, totalPage=1, totalCount=None, **kw):
    d = {"pageSize": len(rows), "totalPage": totalPage,
         "currentPage": currentPage, "resultList": rows}
    if totalCount is not None:
        d["totalCount"] = totalCount
    else:
        d["totalCount"] = len(rows)
    d.update(kw)
    return d


def srow(sym="BTC_USDT", rate=0.0001, ts=1792166400000, cycle=8):
    return {"symbol": sym, "fundingRate": rate, "settleTime": ts,
            "collectCycle": cycle}


print("=== Finding 2: currentPage must match requested page ===")
from proto import settlements as smod

# A page that echoes back the wrong currentPage must raise, not silently
# archive out-of-order data.
try:
    smod.parse_page("BTC_USDT", page_with(
        [srow(ts=1792166400000)], currentPage=2, totalPage=5,
        totalCount=10), expected_page=1)
    check("mismatched currentPage -> SettlementError", False, "no error raised")
except smod.SettlementError as e:
    check("mismatched currentPage -> SettlementError",
          "currentPage 2 != requested page 1" in str(e), str(e)[:80])

# A matching currentPage parses normally.
rows, info, skipped = smod.parse_page(
    "BTC_USDT", page_with([srow(ts=1792166400000)], currentPage=1,
                          totalPage=5, totalCount=10), expected_page=1)
check("matching currentPage parses OK",
      len(rows) == 1 and info["page"] == 1, str((rows, info)))

# Without expected_page (legacy callers), no check fires — backward compat.
rows2, _, _ = smod.parse_page(
    "BTC_USDT", page_with([srow(ts=1792166400000)], currentPage=99,
                          totalPage=99, totalCount=99))
check("no expected_page -> no mismatch check (backward compat)",
      len(rows2) == 1, str(rows2))

# collect_coin must pass expected_page=page for each request.
from proto import mexc as mexmod

PAGES_MISMATCH = {
    1: page_with([srow(ts=1792166400000), srow(ts=1792137600000)],
                 currentPage=2, totalPage=2, totalCount=3),
}


def _fake_history_mismatch(symbol, page_num, page_size=1000):
    return dict(PAGES_MISMATCH[page_num],
                resultList=list(PAGES_MISMATCH[page_num]["resultList"]))


mexmod.funding_history = _fake_history_mismatch
try:
    tmp = tempfile.mkdtemp(prefix="regress-mismatch-")
    try:
        smod.collect_coin(tmp, "BTC", "BTC_USDT", max_pages=10)
        check("collect_coin surfaces currentPage mismatch", False,
              "no error")
    except smod.SettlementError:
        check("collect_coin surfaces currentPage mismatch", True)
    except Exception as e:
        check("collect_coin surfaces currentPage mismatch", False,
              f"got {type(e).__name__}: {e}")
finally:
    real_history = getattr(mexmod, "funding_history")
    if real_history is _fake_history_mismatch:
        del mexmod.funding_history
    else:
        mexmod.funding_history = real_history


# ---------------------------------------------------------------------------
# Finding 3: rate limiter enforced during universe backfill + collect_recent
# ---------------------------------------------------------------------------
print("\n=== Finding 3: rate limiter paced during backfill/collect_recent ===")
from proto.scan import RateLimiter

# A counting limiter that records every acquire.
acquire_count = {"n": 0}
real_limiter = RateLimiter.__new__(RateLimiter)


class _TrackingLimiter:
    def __init__(self, inner):
        self._inner = inner

    def acquire(self):
        acquire_count["n"] += 1
        return self._inner.acquire()


# Patch the LIMITER that settlements imports via `from .scan import LIMITER`.
real_limiter_obj = None
from proto import scan as scanmod
real_limiter_obj = scanmod.LIMITER
tracking = _TrackingLimiter(real_limiter_obj)
scanmod.LIMITER = tracking
try:
    # collect_recent with one coin must call the limiter exactly once
    # (one venue request per coin on page 1).
    def _recent_one_page(sym, pg, sz=1000):
        if pg != 1:
            return {}
        return page_with([srow(ts=1792195200000)], currentPage=1,
                         totalPage=1, totalCount=1)

    mexmod.funding_history = _recent_one_page
    acquire_count["n"] = 0
    tmp3 = tempfile.mkdtemp(prefix="regress-limiter-")
    smod.collect_recent(tmp3, coins={"BTC": "BTC_USDT"})
    check("collect_recent acquires limiter once per coin",
          acquire_count["n"] == 1, f"got {acquire_count['n']}")

    # backfill with two coins must acquire the limiter at least once per
    # coin (one page each in the mock), proving no unbounded burst.
    acquire_count["n"] = 0
    tmp4 = tempfile.mkdtemp(prefix="regress-limiter-bf-")

    BF_PAGES = {
        1: page_with([srow(ts=1792166400000)], currentPage=1,
                     totalPage=1, totalCount=1),
    }

    def _fake_history_bf(sym, pg, sz=1000):
        return dict(BF_PAGES[pg], resultList=list(BF_PAGES[pg]["resultList"]))

    mexmod.funding_history = _fake_history_bf
    smod.backfill(tmp4, max_pages_per_coin=1,
                  coins={"BTC": "BTC_USDT", "ETH": "ETH_USDT"})
    check("backfill acquires limiter per coin (no unbounded burst)",
          acquire_count["n"] >= 2, f"got {acquire_count['n']}")
finally:
    scanmod.LIMITER = real_limiter_obj
    if real_history is None:
        del mexmod.funding_history
    else:
        mexmod.funding_history = real_history


# ---------------------------------------------------------------------------
# Finding 4: read_settlements rejects non-finite rates and invalid cycles
# ---------------------------------------------------------------------------
print("\n=== Finding 4: read_settlements rejects non-finite/invalid rows ===")

tmp5 = tempfile.mkdtemp(prefix="regress-read-")
coin = "BAD"
# Craft a gzip archive with NaN, Infinity, "bogus", and invalid cycle values.
path = os.path.join(tmp5, f"{coin}.csv.gz")
with gzip.open(path, "wt", newline="") as f:
    f.write("settle_ts,settled_rate,cycle_hours\n")
    f.write("1792108800,nan,\n")
    f.write("1792137600,inf,8\n")
    f.write("1792166400,bogus,8\n")
    f.write("1792195200,0.0001,notanumber\n")  # valid rate, bad cycle
    f.write("1792219200,0.0002,-3\n")           # valid rate, bad cycle (negative)
    f.write("1792228800,0.0003,8\n")           # all-good row

rows = smod.read_settlements(tmp5, coin)
ts_list = [r["settle_ts"] for r in rows]
# Rows with non-finite/non-numeric rates are skipped entirely (never
# emitted), so they cannot produce NaN/Infinity in the API JSON.
check("non-finite rate (nan) row skipped",
      1792108800 not in ts_list, str(ts_list))
check("non-finite rate (inf) row skipped",
      1792137600 not in ts_list, str(ts_list))
check("non-numeric rate (bogus) row skipped",
      1792166400 not in ts_list, str(ts_list))

# Rows with a valid rate but invalid cycle context are KEPT (cycle is
# context, not data) but cycle_hours is normalized to None — never the
# raw invalid string.
surviving = {r["settle_ts"]: r for r in rows}
check("valid-rate / invalid-cycle row kept with cycle=None",
      1792195200 in ts_list
      and surviving[1792195200]["cycle_hours"] is None
      and surviving[1792195200]["settled_rate"] == 0.0001,
      str(surviving.get(1792195200)))
check("valid-rate / negative-cycle row kept with cycle=None",
      1792219200 in ts_list
      and surviving[1792219200]["cycle_hours"] is None,
      str(surviving.get(1792219200)))
check("fully-valid row survives with cycle=8",
      surviving[1792228800]["cycle_hours"] == 8
      and surviving[1792228800]["settled_rate"] == 0.0003,
      str(surviving.get(1792228800)))

# Every surviving rate must be a finite float (not NaN/Infinity).
import math
check("all surviving rates are finite",
      all(math.isfinite(r["settled_rate"]) for r in rows),
      str([(r["settled_rate"]) for r in rows]))

# The API layer must not produce non-standard JSON for any rate.
serialized = json.dumps(rows, allow_nan=False)
check("JSON serialization with allow_nan=False succeeds on all rows",
      serialized is not None,
      "serialization would emit non-standard JSON")


# ---------------------------------------------------------------------------
# Finding 1: daemon + /settlements route share the configured settlements_dir
# ---------------------------------------------------------------------------
print("\n=== Finding 1: daemon and route share settlements_dir ===")
from types import SimpleNamespace


# The daemon loop must pass settlements_dir to maybe_collect when configured.
tmp6 = tempfile.mkdtemp(prefix="regress-sharedir-")
shared_dir = os.path.join(tmp6, "settlements")
os.makedirs(shared_dir, exist_ok=True)

# Pre-mark the throttle file so the daily throttle is due.
with open(os.path.join(shared_dir, ".last_collect"), "w") as f:
    f.write("1\n")

collected_dir = {"who": None}


def _spy_collect_recent(data_dir, coins=None):
    collected_dir["who"] = data_dir
    return {"BTC": {"coin": "BTC", "new": 0, "skipped": {},
                    "pages": 1, "truncated": False}}


real_recent = smod.collect_recent
smod.collect_recent = _spy_collect_recent
try:
    # maybe_collect forwards data_dir to collect_recent
    result = smod.maybe_collect(data_dir=shared_dir)
    check("maybe_collect forwards data_dir to collect_recent",
          collected_dir["who"] == shared_dir,
          f"got {collected_dir['who']}")
finally:
    smod.collect_recent = real_recent

# The daemon parser must accept --settlements-dir.
from proto import daemon as dmod
parser = dmod.build_parser()
parsed = parser.parse_args(["--settlements-dir", shared_dir, "--no-scan",
                            "--iterations", "1"])
check("daemon parser accepts --settlements-dir",
      parsed.settlements_dir == shared_dir, str(parsed.settlements_dir))

# The service parser (superset) must also accept it.
from proto import service as svc
svc_parser = svc.build_service_parser()
svc_parsed = svc_parser.parse_args(["--settlements-dir", shared_dir, "--no-scan",
                                    "--iterations", "1", "--port", "0",
                                    "--db", "/tmp/svc-test.db"])
check("service parser accepts --settlements-dir",
      svc_parsed.settlements_dir == shared_dir, str(svc_parsed.settlements_dir))

# _daemon_argv_for must forward settlements_dir as --settlements-dir.
argv = svc._daemon_argv_for(svc_parsed)
check("_daemon_argv_for forwards --settlements-dir",
      "--settlements-dir" in argv and shared_dir in argv, str(argv))

# When settlements_dir is None (default), the daemon loop falls back to
# the module default SETTLEMENTS_DIR, and the route falls back to the
# sibling-default — both resolve to data/funding_settlements.
default_parsed = parser.parse_args(["--no-scan", "--iterations", "1"])
check("daemon parser defaults --settlements-dir to None",
      default_parsed.settlements_dir is None,
      str(default_parsed.settlements_dir))

# The route's default sibling matches the module's SETTLEMENTS_DIR when
# data_dir is the default data/bars.
check("route default sibling == module SETTLEMENTS_DIR for default data_dir",
      os.path.join(os.path.dirname("data/bars"),
                   "funding_settlements") == smod.SETTLEMENTS_DIR,
      f"{os.path.join(os.path.dirname('data/bars'), 'funding_settlements')} "
      f"vs {smod.SETTLEMENTS_DIR}")


print()
if FAILURES:
    print(f"FAIL  tests/test_settlements_regression.py  "
          f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
    sys.exit(1)
print(f"ALL PASS  tests/test_settlements_regression.py  ({CHECKS} checks)")
