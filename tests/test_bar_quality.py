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
  8. out-of-order file — coverage/age use the true sorted min/max
     (file-order first/last stay as ordering diagnostics)
  9. empty input (all None/zero, stale False)
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.bar_quality import (audit_bar_sequence, archive_max_ts,
                               audit_archive_file, check_incoming, main)
import csv
import gzip
import tempfile

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

print("\n=== case 9: out-of-order file — coverage/age use true min/max ===")
# An out-of-order archive (the WS1 finding shape: perfectly spaced bars
# appended in older batches) must NOT report a distorted coverage ratio
# or a wrong staleness age. coverage.span/ratio and age_s/stale are data
# properties: they must use the sorted unique min/max, not the raw
# file-order first/last rows. first_ts/last_ts keep documenting FILE
# ORDER (that is what makes them an ordering diagnostic).
ooo_grid = _grid(n=10)
# File order: newest batch first, then an older batch appended after it
# (exactly the collector append pattern seen in the real archives).
older = _grid(ooo_grid[0]["ts"] - 6 * 300, n=6)
ooo_file = ooo_grid + older
r9 = audit_bar_sequence(ooo_file, now=ooo_grid[-1]["ts"] + 300)
check("ooo file: file-order first_ts preserved (diagnostic)",
      r9["first_ts"] == ooo_file[0]["ts"], str(r9["first_ts"]))
check("ooo file: file-order last_ts preserved (diagnostic)",
      r9["last_ts"] == ooo_file[-1]["ts"], str(r9["last_ts"]))
cov9 = r9["coverage"]
check("ooo file: coverage.first_ts is the true min",
      cov9["first_ts"] == older[0]["ts"], str(cov9))
check("ooo file: coverage.last_ts is the true max",
      cov9["last_ts"] == ooo_grid[-1]["ts"], str(cov9))
# True span: older[0] .. ooo_grid[-1] = 15 slots * 300s
check("ooo file: coverage.span_s is the true span",
      cov9["span_s"] == 15 * 300, str(cov9))
check("ooo file: coverage.expected is the true slot count",
      cov9["expected"] == 16, str(cov9))
check("ooo file: coverage.ratio == 1.0 (perfect grid, no distortion)",
      cov9["ratio"] == 1.0, str(cov9))
# age_s/stale must key off the NEWEST bar (true max), not the file-order
# last row — here the file-order last row is the OLDEST batch.
check("ooo file: age_s keys off the newest bar",
      r9["age_s"] == 300, str(r9["age_s"]))
check("ooo file: not stale when newest bar is fresh",
      r9["stale"] is False, str(r9["stale"]))
# And with a genuinely stale newest bar, staleness must still fire.
r9s = audit_bar_sequence(ooo_file, now=ooo_grid[-1]["ts"] + 3600)
check("ooo file: stale fires on genuinely old newest bar",
      r9s["stale"] is True and r9s["age_s"] == 3600,
      str({"age_s": r9s["age_s"], "stale": r9s["stale"]}))

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

# ---------------------------------------------------------------------------
# Task 2: streaming archive audit + incoming check
# ---------------------------------------------------------------------------
def _write_csv_gz(path, bars, header=None):
    """Write bars (list of dicts) to a gzip CSV; returns the path."""
    hdr = header or ["ts", "o", "h", "l", "c", "vol", "amount"]
    with gzip.open(path, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=hdr)
        w.writeheader()
        for b in bars:
            w.writerow(b)
    return path


print("\n=== task 2: archive_max_ts + audit_archive_file parity ===")
tmpdir = tempfile.mkdtemp()
# Build a defective sequence: a duplicate ts with a conflicting close.
defects = _grid(n=10)
defects.insert(5, dict(defects[4]))  # duplicate ts, same values as index 4
defects[6]["c"] = 999.0              # same ts as index 5, different close -> conflicting
_write_csv_gz(os.path.join(tmpdir, "DEF.csv.gz"), defects)
max_ts, max_cnt = archive_max_ts(tmpdir, "DEF")
check("archive_max_ts returns max ts", max_ts == defects[-1]["ts"], str((max_ts, defects[-1]["ts"])))
check("archive_max_ts returns count", max_cnt == len(defects), str(max_cnt))
audit = audit_archive_file(tmpdir, "DEF", now=defects[-1]["ts"] + 300)
# Same rows handed to audit_bar_sequence directly must match (minus coin/path).
direct = audit_bar_sequence(defects, now=defects[-1]["ts"] + 300)
check("archive count matches direct", audit["count"] == direct["count"], str(audit["count"]))
check("archive duplicates match direct", audit["duplicates"] == direct["duplicates"], str(audit["duplicates"]))
check("archive out_of_order match direct", audit["out_of_order"] == direct["out_of_order"])
check("archive gaps match direct", audit["gaps"] == direct["gaps"])
check("archive has coin key", audit["coin"] == "DEF")
check("archive has path key", audit["path"].endswith("DEF.csv.gz"))
check("archive lists capped at max_examples (50)", len(audit["duplicates"]) <= 50 and len(audit["out_of_order"]) <= 50 and len(audit["gaps"]) <= 50)
# missing file -> empty shape, never raises
missing = audit_archive_file(tmpdir, "NOPE")
check("missing file count 0", missing["count"] == 0, str(missing))
check("missing file no raise: coin/path present", missing["coin"] == "NOPE" and missing["path"].endswith("NOPE.csv.gz"))
check("missing file: not found -> (None, 0)", archive_max_ts(tmpdir, "NOPE") == (None, 0))
# 20k-row synthetic clean file parity
big = [_bar(1_700_000_000 + i * 300, c=100.0 + i) for i in range(20000)]
_write_csv_gz(os.path.join(tmpdir, "BIG.csv.gz"), big)
big_audit = audit_archive_file(tmpdir, "BIG", now=big[-1]["ts"] + 300)
check("20k parity count", big_audit["count"] == 20000, str(big_audit["count"]))
check("20k parity coverage unique", big_audit["coverage"]["unique"] == 20000, str(big_audit["coverage"]))
check("20k parity coverage expected", big_audit["coverage"]["expected"] == 20000, str(big_audit["coverage"]))
check("20k parity coverage ratio", big_audit["coverage"]["ratio"] == 1.0, str(big_audit["coverage"]["ratio"]))
check("20k parity missing_count 0", big_audit["missing_count"] == 0, str(big_audit["missing_count"]))
# malformed ts row: counted in invalid_rows, skipped, file completes
mal = _grid(n=5)
mal.append({"ts": "not-a-number", "o": 1, "h": 1, "l": 1, "c": 1, "vol": 1, "amount": 1})
_write_csv_gz(os.path.join(tmpdir, "MAL.csv.gz"), mal)
mal_audit = audit_archive_file(tmpdir, "MAL")
check("malformed ts -> invalid_rows 1", mal_audit["invalid_rows"] == 1, str(mal_audit["invalid_rows"]))
check("malformed ts -> count 5 (skipped)", mal_audit["count"] == 5, str(mal_audit["count"]))

print("\n=== task 2: check_incoming ===")
# archive_max_ts=1000: incoming ts > 1000 are new; ts <= 1000 are at_or_below_max
# (a bar at/below the archive ceiling cannot extend history without a full set,
#  so it is honestly "duplicate_or_reorder").
inc_bars = [{"ts": 900}, {"ts": 1100}, {"ts": 1100}, {"ts": 1050}]
inc = check_incoming(1000, inc_bars)
check("incoming: 1100 and 1050 are new (above archive max)", inc["new"] == [1100, 1100, 1050], str(inc["new"]))
check("incoming: 900 at_or_below_max (archive ceiling)", len(inc["at_or_below_max"]) == 1 and inc["at_or_below_max"][0]["ts"] == 900 and inc["at_or_below_max"][0]["reason"] == "duplicate_or_reorder", str(inc["at_or_below_max"]))
check("incoming: internal_duplicates [1100]", inc["internal_duplicates"] == [1100], str(inc["internal_duplicates"]))
check("incoming: internal_out_of_order flagged", len(inc["internal_out_of_order"]) == 1, str(inc["internal_out_of_order"]))
check("incoming: ooo index/ts/prev_ts", inc["internal_out_of_order"][0] == {"index": 3, "ts": 1050, "prev_ts": 1100}, str(inc["internal_out_of_order"][0]))
# archive_max_ts=None: all new
inc2 = check_incoming(None, [{"ts": 900}, {"ts": 1000}, {"ts": 1100}])
check("incoming None max: all new", inc2["new"] == [900, 1000, 1100], str(inc2["new"]))
check("incoming None max: no at_or_below", inc2["at_or_below_max"] == [], str(inc2["at_or_below_max"]))
check("incoming None max: no internal dupes", inc2["internal_duplicates"] == [], str(inc2["internal_duplicates"]))
check("incoming None max: no ooo", inc2["internal_out_of_order"] == [], str(inc2["internal_out_of_order"]))

# ---------------------------------------------------------------------------
# Task 3: CLI report (main)
# ---------------------------------------------------------------------------
import io
import json
from contextlib import redirect_stdout


def _run_main(argv, capture=True):
    """Call main(argv) capturing stdout; return (exit_code, stdout_text)."""
    buf = io.StringIO()
    if capture:
        with redirect_stdout(buf):
            code = main(argv)
    else:
        code = main(argv)
    return code, buf.getvalue()


print("\n=== task 3: CLI report ===")
# Build a tmp data dir: one clean coin + one defective coin (a gap).
cli_dir = tempfile.mkdtemp()
clean_ts = _grid(n=5)
_write_csv_gz(os.path.join(cli_dir, "CLEAN.csv.gz"), clean_ts)
gappy = _grid(n=4)
gappy.append(_bar(gappy[-1]["ts"] + 900, c=104.0))  # 3-slot gap
_write_csv_gz(os.path.join(cli_dir, "GAPPY.csv.gz"), gappy)

# Human mode: exit 0 even with findings; SUMMARY line present.
# --now is pinned near the 2023 epoch test timestamps so bars are not stale.
_now_val = str(gappy[-1]["ts"] + 300)
_h_code, _h_out = _run_main(["--data-dir", cli_dir, "--all", "--now", _now_val])
check("cli human exit 0 with findings", _h_code == 0, str(_h_code))
check("cli human prints SUMMARY line", "SUMMARY" in _h_out, _h_out[-300:])
check("cli human prints CLEAN line", "CLEAN" in _h_out, _h_out[-200:])
check("cli human prints GAPPY line", "GAPPY" in _h_out, _h_out[-200:])

# JSON mode: parses; GAPPY shows missing_count 2; CLEAN shows 0.
_j_code, _j_out = _run_main(["--data-dir", cli_dir, "--all", "--json", "--now", _now_val])
check("cli json exit 0", _j_code == 0, str(_j_code))
_j = json.loads(_j_out)
check("cli json returns list", isinstance(_j, list), str(type(_j)))
_j_by = {_jc["coin"]: _jc for _jc in _j}
check("cli json has CLEAN", "CLEAN" in _j_by, str(_j_by.keys()))
check("cli json has GAPPY", "GAPPY" in _j_by, str(_j_by.keys()))
check("cli json GAPPY missing_count 2",
      _j_by["GAPPY"]["missing_count"] == 2,
      str(_j_by["GAPPY"]["missing_count"]))
check("cli json GAPPY stale False (pinned now)",
      _j_by["GAPPY"]["stale"] is False, str(_j_by["GAPPY"]["stale"]))
check("cli json GAPPY has coin+path",
      _j_by["GAPPY"]["coin"] == "GAPPY"
      and _j_by["GAPPY"]["path"].endswith("GAPPY.csv.gz"))
check("cli json CLEAN missing_count 0", _j_by["CLEAN"]["missing_count"] == 0)

# Exit code 2 on unreadable/missing data dir.
_e_code, _e_out = _run_main(["--data-dir", "/nonexistent/xyz"])
check("cli missing dir exit 2", _e_code == 2, str(_e_code))
check("cli missing dir no crash",
      _e_out == "" or "Traceback" not in _e_out, _e_out[-200:])

# --coins filter: only the named coin.
_c_code, _c_out = _run_main(["--data-dir", cli_dir, "--coins", "CLEAN"])
check("cli coins filter exit 0", _c_code == 0, str(_c_code))
check("cli coins filter omits GAPPY", "GAPPY" not in _c_out, _c_out[-200:])
check("cli coins filter includes CLEAN", "CLEAN" in _c_out, _c_out[-200:])

# --tf-sec: the same GAPPY file audited at its true 900s spacing is clean
# (a 900s-spaced archive is not a 300s grid with a gap). This is the
# funding-archive case: observations at the snapshot cadence.
_tf_code, _tf_out = _run_main(["--data-dir", cli_dir, "--coins", "GAPPY",
                               "--tf-sec", "900", "--now", _now_val])
check("cli tf-sec exit 0", _tf_code == 0, str(_tf_code))
check("cli tf-sec 900 makes GAPPY clean (no false gap)",
      "GAPPY" in _tf_out and "missing=0" in _tf_out.split("GAPPY", 1)[1],
      _tf_out[-200:])
_tfj_code, _tfj_out = _run_main(["--data-dir", cli_dir, "--coins", "GAPPY",
                                 "--tf-sec", "900", "--json",
                                 "--now", _now_val])
_tfj = {x["coin"]: x for x in json.loads(_tfj_out)}
check("cli tf-sec json GAPPY missing_count 0",
      _tfj["GAPPY"]["missing_count"] == 0,
      str(_tfj["GAPPY"]["missing_count"]))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
