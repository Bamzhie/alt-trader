"""WS4 point-in-time funding archive tests.

Self-checking script: python3 tests/test_funding.py
Covers proto/funding.py:
  1. append/read round-trip with dedupe by ts
  2. sort-on-read: older observation appended after newer comes back
     chronological; archive bytes unchanged (never rewritten)
  3. collect_funding: ONE tickers() request records every non-synthetic
     USDT perp with its observation ts; synthetics excluded; bad rows
     skipped; counts returned
  4. audit reuse: bar_quality audits a funding archive (ts integrity)
"""

import gzip
import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import funding as fmod
from proto.bar_quality import audit_archive_file

FAILURES = []


def check(n, c, d=""):
    print(f"  {'PASS' if c else 'FAIL'}  {n}" + ("" if c else f"  {d}"))
    if not c:
        FAILURES.append(n)


def _obs(ts, rate=0.0001, cap=0.0018):
    return {"ts": ts, "rate": rate, "cap": cap}


print("=== case 1: append/read round-trip, dedupe by ts ===")
tmp = tempfile.mkdtemp(prefix="funding-")
n1 = fmod.append_funding(tmp, "ADA", [_obs(1000), _obs(1300)])
n2 = fmod.append_funding(tmp, "ADA", [_obs(1300), _obs(1600)])
check("first append writes 2", n1 == 2, str(n1))
check("re-observed ts is deduped (1 new)", n2 == 1, str(n2))
rows = fmod.read_funding(tmp, "ADA")
check("read back 3 observations", len(rows) == 3, str(rows))
check("rows carry rate+cap", all("rate" in r and "cap" in r for r in rows),
      str(rows))

print("\n=== case 2: sort-on-read, archives never rewritten ===")
tmp2 = tempfile.mkdtemp(prefix="funding-ooo-")
# newest observation first, then an older batch appended after it
fmod.append_funding(tmp2, "ETH", [_obs(2000), _obs(2300)])
fmod.append_funding(tmp2, "ETH", [_obs(1100), _obs(1400)])
path = os.path.join(tmp2, "ETH.csv.gz")
before = hashlib.sha256(open(path, "rb").read()).hexdigest()
rows2 = fmod.read_funding(tmp2, "ETH")
ts2 = [r["ts"] for r in rows2]
check("read_funding chronological despite append order",
      ts2 == sorted(ts2), str(ts2))
check("read_funding keeps every observation", len(rows2) == 4,
      str(len(rows2)))
after = hashlib.sha256(open(path, "rb").read()).hexdigest()
check("archive bytes unchanged (no rewrite)", before == after,
      "archive was mutated by read_funding")
with gzip.open(path, "rt", newline="") as f:
    disk = [int(r["ts"]) for r in
            __import__("csv").DictReader(f)]
check("on-disk order still append order",
      disk == [2000, 2300, 1100, 1400], str(disk))

print("\n=== case 3: collect_funding from one tickers() request ===")
tmp3 = tempfile.mkdtemp(prefix="funding-collect-")
from proto import mexc as mexmod
from proto import scan as scanmod

real_tk, real_det = mexmod.tickers, mexmod.details
real_canon, real_syn = scanmod.canon, scanmod.is_synthetic


def _fake_tickers():
    return {
        "ADA_USDT": {"fundingRate": "0.00012", "maxFundingRate": "0.002"},
        "ETH_USDT": {"fundingRate": "-0.00008", "maxFundingRate": "0.002"},
        "AAPL_USDT": {"fundingRate": "0.0005", "maxFundingRate": "0.002"},
        "BTC_USDT": {"fundingRate": "0.0", "maxFundingRate": "0.002"},
        "NOTUSDT": {"fundingRate": "0.0001"},  # not a USDT perp
    }


def _fake_details():
    # AAPL_USDT carries an equity plate -> synthetic, excluded.
    return {"AAPL_USDT": {"conceptPlate": ["mc-trade-zone-Stock"]}}


def _fake_canon(sym):
    return sym[:-5] if sym.endswith("_USDT") else sym


def _fake_is_synthetic(coin, detail_row):
    plates = (detail_row or {}).get("conceptPlate") or []
    return any("Stock" in p for p in plates)


mexmod.tickers = _fake_tickers
mexmod.details = _fake_details
scanmod.canon = _fake_canon
scanmod.is_synthetic = _fake_is_synthetic
try:
    counts = fmod.collect_funding(tmp3, now=1792000000)
finally:
    mexmod.tickers, mexmod.details = real_tk, real_det
    scanmod.canon, scanmod.is_synthetic = real_canon, real_syn

check("one archive per non-synthetic USDT perp",
      sorted(os.listdir(tmp3)) == ["ADA.csv.gz", "BTC.csv.gz",
                                   "ETH.csv.gz"],
      str(sorted(os.listdir(tmp3))))
check("counts returned per coin",
      counts == {"ADA": 1, "ETH": 1, "BTC": 1}, str(counts))
ada = fmod.read_funding(tmp3, "ADA")
check("observation ts is the snapshot time",
      ada and ada[0]["ts"] == 1792000000, str(ada))
check("rate+cap recorded from the ticker row",
      ada and ada[0]["rate"] == 0.00012 and ada[0]["cap"] == 0.002,
      str(ada))
eth = fmod.read_funding(tmp3, "ETH")
check("negative funding rate preserved",
      eth and eth[0]["rate"] == -0.00008, str(eth))
check("synthetic (AAPL_USDT) excluded",
      not os.path.exists(os.path.join(tmp3, "AAPL.csv.gz")))
check("non-USDT symbol excluded",
      not os.path.exists(os.path.join(tmp3, "NOTUSDT.csv.gz")))

print("\n=== case 4: audit reuse on a funding archive ===")
audit = audit_archive_file(tmp3, "ADA", tf_sec=300)
check("audit counts the funding observations",
      audit["count"] == 1, str(audit.get("count")))
check("audit coverage ratio 1.0 (single observation)",
      audit["coverage"]["ratio"] == 1.0, str(audit.get("coverage")))
check("audit attaches the funding path",
      audit["path"].endswith("ADA.csv.gz"), str(audit.get("path")))

print()
if FAILURES:
    print(f"FAIL  tests/test_funding.py  ({len(FAILURES)} failed: "
          f"{FAILURES})")
    sys.exit(1)
print("ALL PASS")
