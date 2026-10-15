"""Bar-archive in-memory audit (Task 1).

Self-checking script: python3 tests/test_bar_quality.py
Covers audit_bar_sequence over the 8 cases from the plan:
  1. clean 5m grid (zero findings, stale=False with now=last_ts+300)
  2. duplicate ts same values (occurrences 2, conflicting False)
  3. duplicate ts differing close (conflicting True)
  4. out-of-order pair (exact index/prev_ts)
  5. exact-multiple gap (missing 2 for a 3-slot jump)
  6. off-grid gap (missing None, still listed)
  7. stale file (now=last_ts+3600, stale True)
  8. empty input (all None/zero, stale False)
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.bar_quality import audit_bar_sequence

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def _bar(ts, c=100.0, px=None):
    """A minimal bar mapping with the canonical fields."""
    px = px if px is not None else c
    return {"ts": ts, "o": px, "h": px + 0.5, "l": px - 0.5,
            "c": c, "vol": 1000.0, "amount": 1000.0 * px}


def _grid(start_ts=1_700_000_000, n=10, step=300):
    """A clean ascending 5m grid."""
    return [_bar(start_ts + i * step, c=100.0 + i) for i in range(n)]


print("=== case 1: clean 5m grid ===")
clean = _grid(n=10)
now = clean[-1]["ts"] + 300  # src bar closed exactly 1 tf ago -> not stale
r1 = audit_bar_sequence(clean, now=now)
check("count == 10", r1["count"] == 10, str(r1["count"]))
check("first_ts", r1["first_ts"] == clean[0]["ts"], str(r1["first_ts"]))
check("last_ts", r1["last_ts"] == clean[-1]["ts"], str(r1["last_ts"]))
check("invalid_rows == 0", r1["invalid_rows"] == 0, str(r1["invalid_rows"]))
check("no duplicates", r1["duplicates"] == [], str(r1["duplicates"]))
check("no out_of_order", r1["out_of_order"] == [], str(r1["out_of_order"]))
check("out_of_order_count == 0", r1["out_of_order_count"] == 0,
      str(r1["out_of_order_count"]))
check("no gaps", r1["gaps"] == [], str(r1["gaps"]))
check("missing_count == 0", r1["missing_count"] == 0, str(r1["missing_count"]))
check("age_s == 300", r1["age_s"] == 300, str(r1["age_s"]))
check("stale == False", r1["stale"] is False, str(r1["stale"]))
cov = r1["coverage"]
check("coverage.unique == 10", cov["unique"] == 10, str(cov))
check("coverage.expected == 10", cov["expected"] == 10, str(cov))
check("coverage.ratio == 1.0", cov["ratio"] == 1.0, str(cov))

print("\n=== case 2: duplicate ts, same values ===")
b = _grid(n=8)
dup = [dict(b[3]), dict(b[3])]  # exact duplicate close/price
dup_seq = b[:3] + dup + b[4:]
r2 = audit_bar_sequence(dup_seq, now=dup_seq[-1]["ts"] + 300)
check("one duplicate entry", len(r2["duplicates"]) == 1, str(r2["duplicates"]))
d2 = r2["duplicates"][0]
check("dup ts value", d2["ts"] == b[3]["ts"], str(d2))
check("dup occurrences == 2", d2["occurrences"] == 2, str(d2))
check("dup conflicting == False", d2["conflicting"] is False, str(d2))
# gaps: the duplicate ts does not create a gap (same slot reused). expect none.
check("no gaps from dup", r2["gaps"] == [], str(r2["gaps"]))
# 8 base bars share 8 unique ts (one duplicated); dup ts reuses its slot.
check("unique == 8 in coverage", r2["coverage"]["unique"] == 8,
      str(r2["coverage"]))

print("\n=== case 3: duplicate ts, differing close ===")
b = _grid(n=8)
d = dict(b[3]); d["c"] = 999.0  # different close -> conflicting
dup_seq3 = b[:3] + [dict(b[3]), d] + b[4:]
r3 = audit_bar_sequence(dup_seq3, now=dup_seq3[-1]["ts"] + 300)
d3 = r3["duplicates"][0]
check("dup occurrences == 2 (conflicting)", d3["occurrences"] == 2, str(d3))
check("dup conflicting == True", d3["conflicting"] is True, str(d3))

print("\n=== case 4: out-of-order pair ===")
ordered = _grid(n=6)
# swap last two -> out of order at index 5
oo = list(ordered)
oo[4], oo[5] = oo[5], oo[4]
r4 = audit_bar_sequence(oo, now=oo[-1]["ts"] + 300)
check("one out_of_order entry", len(r4["out_of_order"]) == 1, str(r4["out_of_order"]))
e4 = r4["out_of_order"][0]
check("oo index == 5", e4["index"] == 5, str(e4))
check("oo ts == ordered[4].ts", e4["ts"] == ordered[4]["ts"], str(e4))
check("oo prev_ts == ordered[5].ts", e4["prev_ts"] == ordered[5]["ts"], str(e4))
check("out_of_order_count == 1", r4["out_of_order_count"] == 1,
      str(r4["out_of_order_count"]))
# note: out_of_order list is capped at 50; count is the full total
check("oo list capped at 50 (len<=50)", len(r4["out_of_order"]) <= 50,
      str(e4))

print("\n=== case 5: exact-multiple gap (missing 2) ===")
b = _grid(n=4)
# insert a 3-slot jump: from b[3] jump +900s -> missing slots at +300,+600
gap_seq = b[:4] + [_bar(b[3]["ts"] + 900, c=104.0)]
r5 = audit_bar_sequence(gap_seq, now=gap_seq[-1]["ts"] + 300)
check("one gap", len(r5["gaps"]) == 1, str(r5["gaps"]))
g5 = r5["gaps"][0]
check("gap from_ts == b[3].ts", g5["from_ts"] == b[3]["ts"], str(g5))
check("gap to_ts == jump target", g5["to_ts"] == b[3]["ts"] + 900, str(g5))
check("gap span_s == 900", g5["span_s"] == 900, str(g5))
check("gap missing == 2", g5["missing"] == 2, str(g5))
check("missing_count == 2", r5["missing_count"] == 2, str(r5["missing_count"]))
# coverage spans the whole sequence (first -> last across all 5 rows).
check("coverage.expected reflects full span", r5["coverage"]["expected"] ==
      1800 // 300 + 1, str(r5["coverage"]))
check("coverage.unique == 5", r5["coverage"]["unique"] == 5, str(r5["coverage"]))

print("\n=== case 6: off-grid gap (missing None) ===")
b = _grid(n=4)
# off-grid: jump of 700s (not a multiple of 300) -> still listed, missing None
seq6 = b[:4] + [_bar(b[3]["ts"] + 700, c=104.0)]
r6 = audit_bar_sequence(seq6, now=seq6[-1]["ts"] + 300)
check("off-grid gap listed", len(r6["gaps"]) == 1, str(r6["gaps"]))
g6 = r6["gaps"][0]
check("off-grid missing is None", g6["missing"] is None, str(g6))
check("off-grid from_ts", g6["from_ts"] == b[3]["ts"], str(g6))
check("off-grid to_ts", g6["to_ts"] == b[3]["ts"] + 700, str(g6))
check("off-grid span_s == 700", g6["span_s"] == 700, str(g6))
check("off-grid missing_count == 0", r6["missing_count"] == 0,
      str(r6["missing_count"]))
check("off-grid coverage.expected None", r6["coverage"]["expected"] is None,
      str(r6["coverage"]))

print("\n=== case 7: stale file ===")
b = _grid(n=6)
now_stale = b[-1]["ts"] + 3600  # well past 2*tf
r7 = audit_bar_sequence(b, now=now_stale)
check("stale == True", r7["stale"] is True, str(r7["stale"]))
check("age_s == 3600", r7["age_s"] == 3600, str(r7["age_s"]))

print("\n=== case 8: empty input ===")
r8 = audit_bar_sequence([], now=1_700_006_300)
check("empty count == 0", r8["count"] == 0, str(r8["count"]))
check("empty first_ts None", r8["first_ts"] is None, str(r8["first_ts"]))
check("empty last_ts None", r8["last_ts"] is None, str(r8["last_ts"]))
check("empty invalid_rows 0", r8["invalid_rows"] == 0, str(r8["invalid_rows"]))
check("empty duplicates []", r8["duplicates"] == [], str(r8["duplicates"]))
check("empty out_of_order []", r8["out_of_order"] == [], str(r8["out_of_order"]))
check("empty out_of_order_count 0", r8["out_of_order_count"] == 0,
      str(r8["out_of_order_count"]))
check("empty gaps []", r8["gaps"] == [], str(r8["gaps"]))
check("empty missing_count 0", r8["missing_count"] == 0, str(r8["missing_count"]))
check("empty age_s None", r8["age_s"] is None, str(r8["age_s"]))
check("empty stale False", r8["stale"] is False, str(r8["stale"]))
cov8 = r8["coverage"]
check("empty coverage None fields",
      cov8["first_ts"] is None and cov8["last_ts"] is None
      and cov8["span_s"] == 0 and cov8["unique"] == 0
      and cov8["expected"] is None and cov8["ratio"] is None,
      str(cov8))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
