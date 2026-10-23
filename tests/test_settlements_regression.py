"""WS4 regression tests for the four post-review findings.

Self-checking script: python3 tests/test_settlements_regression.py

Covers:
  1. Daemon + /settlements route share the configured settlements_dir
     (or both fall back to the same default). When --data-dir is custom
     and --settlements-dir is omitted, both resolve to the sibling dir.
  2. parse_page rejects a currentPage that doesn't match the requested page.
  3. Universe backfill/collect_recent pace venue requests through the
     settlement-endpoint rate limiter (20 req / 2s rolling window, NOT
     scan.LIMITER's 20 req/s), and the window is actually enforced.
  4. Archive reads (read_settlements) reject non-finite settled rates and
     invalid cycle values so the API never emits non-standard JSON.
"""

import gzip
import json
import math
import os
import sys
import tempfile
import threading
import time

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


# ---------------------------------------------------------------------------
# Finding 3: settlement-endpoint rate limiter (20 req / 2s rolling window)
# ---------------------------------------------------------------------------
print("\n=== Finding 3: settlement-endpoint rate limiter ===")

# 3a. The limiter allows SETTLEMENT_RATE requests in the window without
# blocking, then throttles the (rate+1)th request.
lim = smod.SettlementRateLimiter(rate=5, window_s=1.0)
lim.reset()
t0 = time.monotonic()
for i in range(5):
    lim.acquire()
t1 = time.monotonic()
check("first 5 acquires are non-blocking",
      t1 - t0 < 0.5, f"elapsed={t1-t0:.3f}s")

# The 6th request must block until the window advances.
t2 = time.monotonic()
lim.acquire()
t3 = time.monotonic()
check("6th acquire blocks (window enforced)",
      t3 - t2 >= 0.9, f"elapsed={t3-t2:.3f}s")

# 3b. collect_coin and collect_recent use SETTLEMENT_LIMITER, not scan.LIMITER.
# Verify by patching SETTLEMENT_LIMITER with a non-blocking counting limiter.
acquire_count = {"n": 0}
real_settlement_limiter = smod.SETTLEMENT_LIMITER


class _NoBlockLimiter:
    """Non-blocking limiter that just counts acquires."""

    def acquire(self):
        acquire_count["n"] += 1


smod.SETTLEMENT_LIMITER = _NoBlockLimiter()

from proto import mexc as mexmod

# collect_recent with one coin must call the settlement limiter once.
def _recent_one_page(sym, pg, sz=1000):
    if pg != 1:
        return {}
    return page_with([srow(ts=1792195200000)], currentPage=1,
                     totalPage=1, totalCount=1)

mexmod.funding_history = _recent_one_page
acquire_count["n"] = 0
tmp3 = tempfile.mkdtemp(prefix="regress-limiter-")
smod.collect_recent(tmp3, coins={"BTC": "BTC_USDT"})
check("collect_recent acquires settlement limiter (not scan.LIMITER)",
      acquire_count["n"] == 1, f"got {acquire_count['n']}")

# backfill with two coins must acquire the limiter at least once per coin.
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
check("backfill acquires settlement limiter per coin",
      acquire_count["n"] >= 2, f"got {acquire_count['n']}")

smod.SETTLEMENT_LIMITER = real_settlement_limiter


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
from proto import service as svc

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
    result = smod.maybe_collect(data_dir=shared_dir)
    check("maybe_collect forwards data_dir to collect_recent",
          collected_dir["who"] == shared_dir,
          f"got {collected_dir['who']}")
finally:
    smod.collect_recent = real_recent

# The collection-only service parser must accept --settlements-dir.
svc_parser = svc.build_parser()
svc_parsed = svc_parser.parse_args(["--settlements-dir", shared_dir,
                                    "--port", "0"])
check("service parser accepts --settlements-dir",
      svc_parsed.settlements_dir == shared_dir, str(svc_parsed.settlements_dir))

# The route's default sibling matches the module's SETTLEMENTS_DIR when
# data_dir is the default data/bars.
check("route default sibling == module SETTLEMENTS_DIR for default data_dir",
      os.path.join(os.path.dirname("data/bars"),
                   "funding_settlements") == smod.SETTLEMENTS_DIR,
      f"{os.path.join(os.path.dirname('data/bars'), 'funding_settlements')} "
      f"vs {smod.SETTLEMENTS_DIR}")

# NEW: when --data-dir is custom and --settlements-dir is omitted, the
# service's _resolve_archive_dir must resolve them to the SAME sibling dir
# so the collection loop and the route agree.
custom_bars = os.path.join(tmp6, "custom_bars")
resolved = svc._resolve_archive_dir(None, custom_bars, "funding_settlements")
expected = os.path.join(os.path.dirname(custom_bars), "funding_settlements")
check("custom --data-dir + no --settlements-dir -> sibling resolved",
      resolved == expected, f"{resolved} vs {expected}")


print()
if FAILURES:
    print(f"FAIL  tests/test_settlements_regression.py  "
          f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
    sys.exit(1)
print(f"ALL PASS  tests/test_settlements_regression.py  ({CHECKS} checks)")
