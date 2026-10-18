"""Tests: WS5 minimum-coverage policy (pure logic; no data access).

Self-checking script: python3 tests/test_quant_coverage.py
Covers:
  1. holdout_start_day matches the frozen Splits arithmetic (79.0 default)
  2. min_span_days = holdout_start + required holdout length (91.0 default)
  3. classify_coverage boundaries: full / partial / insufficient
  4. None / missing / negative spans are "insufficient"
  5. summarize() counts and per-bucket coin lists (sorted), total
  6. policy is derived from the splits object (custom splits honoured)
  7. audit_spans reads a temp bar dir and classifies by sorted min/max
     (unsorted archives must not be mis-measured)
  8. no production writes / read-only
  9. CLI main(): human summary and --json audit output
"""
import gzip
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.quant.config import DEFAULT, Splits            # noqa: E402
from proto.quant import coverage as C                     # noqa: E402

_fail = []


def check(name, cond):
    if not cond:
        _fail.append(name)
        print(f"FAIL  {name}")
    else:
        print(f"ok    {name}")


# 1. holdout start day
check("holdout_start_day default == 79.0",
      C.holdout_start_day(DEFAULT.splits) == 79.0)

# 2. min span days
check("min_span_days default == 91.0",
      C.min_span_days(DEFAULT.splits) == 91.0)
check("min_span_days custom holdout len",
      C.min_span_days(DEFAULT.splits, min_holdout_days=30.0) == 109.0)

# 3. classification boundaries
check("120d -> full", C.classify_coverage(120.0) == "full")
check("91.0d boundary -> full", C.classify_coverage(91.0) == "full")
check("90.99d -> partial", C.classify_coverage(90.99) == "partial")
check("79.0d boundary -> partial", C.classify_coverage(79.0) == "partial")
check("78.99d -> insufficient", C.classify_coverage(78.99) == "insufficient")
check("6d -> insufficient", C.classify_coverage(6.0) == "insufficient")

# 4. missing / invalid spans
check("None -> insufficient", C.classify_coverage(None) == "insufficient")
check("negative -> insufficient", C.classify_coverage(-5.0) == "insufficient")
check("non-numeric -> insufficient", C.classify_coverage("abc") == "insufficient")

# 5. summarize
spans = {"AAA": 120.0, "BBB": 85.0, "CCC": 10.0, "DDD": 91.0, "EEE": None}
s = C.summarize(spans)
check("summarize counts", s["counts"] == {"full": 2, "partial": 1,
                                          "insufficient": 2})
check("summarize full list sorted", s["coins"]["full"] == ["AAA", "DDD"])
check("summarize insufficient list sorted",
      s["coins"]["insufficient"] == ["CCC", "EEE"])
check("summarize total", s["total"] == 5)
check("summarize reports min_span_days", s["min_span_days"] == 91.0)
check("summarize reports holdout_start_day", s["holdout_start_day"] == 79.0)

# 6. custom splits honoured
cs = Splits(dev_end_day=10.0, embargo_days=1.0, val_len_days=4.0)
check("custom splits holdout_start == 16.0", C.holdout_start_day(cs) == 16.0)
check("custom splits min_span == 28.0", C.min_span_days(cs) == 28.0)
check("custom classify 20d -> partial", C.classify_coverage(20.0, cs) == "partial")
check("custom classify 30d -> full", C.classify_coverage(30.0, cs) == "full")

# 7. audit_spans on a temp (deliberately unsorted) archive
def _write(path, ts_list):
    with gzip.open(path, "wt", newline="") as f:
        f.write("ts,o,h,l,c,vol,amount\n")
        for t in ts_list:
            f.write(f"{t},1,1,1,1,1,1\n")

with tempfile.TemporaryDirectory() as d:
    day = 86400
    step = 300
    n = 100 * day // step          # 100 days of 5m bars
    full = [1_700_000_000 + i * step for i in range(n)]
    # write non-chronologically: newest chunk first, older chunk appended
    _write(os.path.join(d, "LONG.csv.gz"), full[5000:] + full[:5000])
    # 20-day coin
    m = 20 * day // step
    _write(os.path.join(d, "SHORT.csv.gz"), [1_700_000_000 + i * step for i in range(m)])
    spans = C.audit_spans(d)
    check("audit_spans reads both coins", set(spans) == {"LONG", "SHORT"})
    check("audit_spans LONG ~100d despite file order",
          abs(spans["LONG"] - 99.999) < 0.01)
    check("audit_spans SHORT ~20d", abs(spans["SHORT"] - 20.0) < 0.01)
    a = C.summarize(spans)
    check("audit classify LONG full", C.classify_coverage(spans["LONG"]) == "full")
    check("audit classify SHORT insufficient",
          C.classify_coverage(spans["SHORT"]) == "insufficient")

# 8. no production writes (the temp dir was removed; repo data untouched)
check("no data dir created by import", not os.path.exists("data/quant_research"))

# 9. CLI main(): human summary and --json
import contextlib                                            # noqa: E402
import io                                                    # noqa: E402
import json                                                  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    day = 86400
    step = 300
    _write(os.path.join(d, "LONG.csv.gz"),
           [1_700_000_000 + i * step for i in range(100 * day // step)])
    _write(os.path.join(d, "SHORT.csv.gz"),
           [1_700_000_000 + i * step for i in range(5 * day // step)])

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = C.main(["--data-dir", d])
    check("coverage CLI returns 0", rc == 0)
    text = buf.getvalue()
    check("coverage CLI human summary mentions full bucket",
          "full (>= 91.0d):" in text)
    check("coverage CLI lists the insufficient coin", "SHORT" in text)

    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        C.main(["--data-dir", d, "--json"])
    payload = json.loads(buf2.getvalue())
    check("coverage CLI --json counts",
          payload["counts"] == {"full": 1, "partial": 0, "insufficient": 1})
    check("coverage CLI --json includes per-coin spans",
          payload["spans"]["LONG"] > 99.0 and payload["spans"]["SHORT"] < 6.0)

if _fail:
    print(f"\n{len(_fail)} FAILED: {_fail}")
    sys.exit(1)
print("\nALL PASS")
