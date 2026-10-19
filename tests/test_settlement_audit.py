"""Tests: WS4 settlement-backfill per-coin manifest (pure + injectable probe).

Self-checking script: python3 tests/test_settlement_audit.py
Covers:
  1. read_coin_stats counts raw/valid/malformed/duplicate rows, span, cycles
  2. absent archive -> None; corrupt archive -> error dict (no abort)
  3. expected_live_tail_rows / venue+unexplained shortfall arithmetic
  4. page_boundary_cap signature
  5. classify() for all four outcomes (probe error, missing, complete,
     truncated) and the offline page-boundary fallback
  6. build_manifest reconciles against the bar universe; outcomes sum;
     totals; settlement-only coins listed
  7. build_manifest is read-only (archive untouched)
  8. write_json / write_csv round-trip
  9. CLI main() --no-venue and --json
"""
import contextlib
import csv
import gzip
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.quant import settlement_audit as A                     # noqa: E402

_fail = []


def check(name, cond, detail=""):
    if not cond:
        _fail.append(name)
        print(f"FAIL  {name}" + (f"  {detail}" if detail else ""))
    else:
        print(f"ok    {name}")


def write_settles(path, rows):
    """rows: list of (settle_ts, settled_rate, cycle_hours_or_'')"""
    with gzip.open(path, "wt", newline="") as f:
        f.write("settle_ts,settled_rate,cycle_hours\n")
        for ts, rate, cyc in rows:
            f.write(f"{ts},{rate},{cyc}\n")


def touch(path):
    open(path, "a").close()


# 1. archive stats
with tempfile.TemporaryDirectory() as d:
    # 5 raw rows: one NaN rate (dropped), one duplicate ts, one blank cycle
    # (allowed: the read path keeps a None cycle).
    write_settles(os.path.join(d, "AAA.csv.gz"),
                  [(1000, 0.0001, 4), (2000, 0.0002, 4), (2000, 0.0003, 4),
                   (3000, "nan", 4), (4000, 0.0004, "")])
    st = A.read_coin_stats(d, "AAA")
    check("stats raw_rows counts every data row", st["raw_rows"] == 5)
    check("stats valid_rows drops non-finite rates", st["valid_rows"] == 4)
    check("stats malformed_rows = raw - valid", st["malformed_rows"] == 1)
    check("stats duplicate_rows detects dup settle_ts", st["duplicate_rows"] == 1)
    check("stats unique_ts", st["unique_ts"] == 3)
    check("stats first/last settle ts", st["first_settle_ts"] == 1000
          and st["last_settle_ts"] == 4000)
    check("stats span_days", abs(st["span_days"] - (3000 / 86400.0)) < 1e-6)
    check("stats cycles (blank cycle kept out of the set)",
          st["cycles"] == [4])
    check("stats read_error is None on a clean file", st["error"] is None)

# 2. absent + corrupt
with tempfile.TemporaryDirectory() as d:
    check("absent archive -> None", A.read_coin_stats(d, "NOPE") is None)
    with open(os.path.join(d, "BAD.csv.gz"), "wb") as f:
        f.write(b"not a gzip stream")
    bad = A.read_coin_stats(d, "BAD")
    check("corrupt archive -> error dict, no abort",
          bad is not None and bad["error"] and bad["valid_rows"] == 0)

# 3. tail / shortfall arithmetic
stats = {"cycles": [4], "last_settle_ts": 1_700_000_000, "valid_rows": 100}
obs = 1_700_000_000 + 20 * 3600
check("expected_live_tail_rows uses the fastest cycle",
      A.expected_live_tail_rows(stats, obs) == 5)
check("expected_live_tail_rows None without cycles",
      A.expected_live_tail_rows({"cycles": [], "last_settle_ts": 1}, obs) is None)
check("expected_live_tail_rows None without a last ts",
      A.expected_live_tail_rows({"cycles": [4], "last_settle_ts": None},
                                obs) is None)
check("venue_shortfall_rows = venue - valid",
      A.venue_shortfall_rows(stats, {"total_count": 130}) == 30)
check("venue_shortfall_rows None without venue",
      A.venue_shortfall_rows(stats, {}) is None)
check("unexplained shortfall subtracts the tail",
      A.unexplained_shortfall_rows(stats, {"total_count": 130}, obs) == 25)
check("unexplained shortfall is 0 when within tail",
      A.unexplained_shortfall_rows(stats, {"total_count": 104}, obs) == 0)

# 4. page-boundary signature
check("page_boundary_cap true at exactly 1000",
      A.page_boundary_cap({"raw_rows": 1000}))
check("page_boundary_cap true at 5000",
      A.page_boundary_cap({"raw_rows": 5000}))
check("page_boundary_cap false off-boundary",
      not A.page_boundary_cap({"raw_rows": 999}))
check("page_boundary_cap false below one page",
      not A.page_boundary_cap({"raw_rows": 0}))

# 5. classify
check("probe error -> failed_or_error",
      A.classify(stats, {"error": "MexcError: boom"}, obs) == A.FAILED)
check("no archive -> missing_archive",
      A.classify(None, {"total_count": 0}, obs) == A.MISSING)
check("no archive beats venue history -> still missing_archive",
      A.classify(None, {"total_count": 999}, obs) == A.MISSING)
complete_stats = {"cycles": [4], "last_settle_ts": obs - 8 * 3600,
                  "valid_rows": 500, "raw_rows": 500}
check("venue deficit within tail -> complete",
      A.classify(complete_stats, {"total_count": 502}, obs) == A.COMPLETE)
cap_stats = {"cycles": [1], "last_settle_ts": obs - 19 * 3600,
             "valid_rows": 5000, "raw_rows": 5000}
check("page cap + deficit beyond tail -> truncated",
      A.classify(cap_stats, {"total_count": 7000}, obs) == A.TRUNCATED)
check("page cap + deficit within tail -> complete",
      A.classify(cap_stats, {"total_count": 5010}, obs) == A.COMPLETE)
check("non-boundary shortfall beyond tail -> truncated",
      A.classify({"cycles": [1], "last_settle_ts": obs,
                  "valid_rows": 500, "raw_rows": 500},
                 {"total_count": 1200}, obs) == A.TRUNCATED)
check("offline page boundary -> truncated (unconfirmed)",
      A.classify({"cycles": [4], "last_settle_ts": obs, "valid_rows": 5000,
                  "raw_rows": 5000}, None, obs) == A.TRUNCATED)
check("offline off-boundary -> complete (unconfirmed)",
      A.classify({"cycles": [4], "last_settle_ts": obs, "valid_rows": 500,
                  "raw_rows": 500}, None, obs) == A.COMPLETE)

# 6/7. build_manifest reconciliation + read-only
with tempfile.TemporaryDirectory() as root:
    sd = os.path.join(root, "settlements")
    fd = os.path.join(root, "funding")
    bd = os.path.join(root, "bars")
    for p in (sd, fd, bd):
        os.makedirs(p)
    obs = 1_700_000_000
    # AAA: complete. BBB: page-capped + venue far ahead -> truncated.
    write_settles(os.path.join(sd, "AAA.csv.gz"),
                  [(obs - 100, 0.0001, 4), (obs - 50, 0.0002, 4)])
    write_settles(os.path.join(sd, "BBB.csv.gz"),
                  [(obs - 5000 + i, 0.0, 1) for i in range(1000)])
    write_settles(os.path.join(sd, "ZZZ.csv.gz"), [(obs - 10, 0.0, 8)])
    touch(os.path.join(fd, "AAA.csv.gz"))
    for c in ("AAA", "BBB", "CCC", "DDD"):
        touch(os.path.join(bd, c + ".csv.gz"))

    venue = {
        "AAA": {"symbol": "AAA_USDT", "total_count": 2, "total_pages": 1,
                "error": None, "in_venue_tickers": True},
        "BBB": {"symbol": "BBB_USDT", "total_count": 7000, "total_pages": 7,
                "error": None, "in_venue_tickers": True},
        "CCC": {"symbol": "CCC_USDT", "total_count": 0, "total_pages": 0,
                "error": None, "in_venue_tickers": False},
        "DDD": {"symbol": "DDD_USDT", "total_count": None, "total_pages": None,
                "error": "MexcError: boom", "in_venue_tickers": None},
    }
    before = sorted(os.listdir(sd))
    man = A.build_manifest(sd, ["AAA", "BBB", "CCC", "DDD"], funding_dir=fd,
                           venue=venue, observed_at=obs)
    after = sorted(os.listdir(sd))
    check("build_manifest is read-only", before == after)
    check("bar universe size", man["bar_universe_n"] == 4)
    check("outcomes reconcile to the bar universe",
          sum(man["outcomes"].values()) == 4, str(man["outcomes"]))
    check("outcomes split", man["outcomes"] == {A.COMPLETE: 1, A.TRUNCATED: 1,
                                                A.MISSING: 1, A.FAILED: 1},
          str(man["outcomes"]))
    check("truncated coin named", man["truncated_coins"] == ["BBB"])
    check("unexplained shortfall totalled",
          man["totals"]["unexplained_shortfall_rows"]
          == 7000 - 1000 - ((5000 - 999) // 3600),
          str(man["totals"]))
    check("funding presence recorded", any(
        e["coin"] == "AAA" and e["funding_archive_present"] for e in man["coins"]))
    check("missing coin carries venue history evidence", any(
        e["coin"] == "CCC" and e["venue_total_count"] == 0
        and e["outcome"] == A.MISSING for e in man["coins"]))
    check("probe-error coin is failed_or_error", any(
        e["coin"] == "DDD" and e["outcome"] == A.FAILED for e in man["coins"]))
    check("settlement-only coin listed separately",
          man["settlement_only_coins"] == ["ZZZ"])
    check("note: backfill api errors not recorded",
          man["notes"]["backfill_api_errors_recorded"] is False)

    # 8. writers
    jp = os.path.join(root, "manifest.json")
    cp = os.path.join(root, "manifest.csv")
    A.write_json(man, jp)
    A.write_csv(man, cp)
    back = json.load(open(jp))
    check("write_json round-trips outcomes", back["outcomes"] == man["outcomes"])
    check("write_json round-trips every coin",
          len(back["coins"]) == 4)
    with open(cp) as f:
        rows = list(csv.DictReader(f))
    check("write_csv has one row per bar-universe coin", len(rows) == 4)
    check("write_csv carries the outcome column",
          {r["outcome"] for r in rows} ==
          {A.COMPLETE, A.TRUNCATED, A.MISSING, A.FAILED})

    # 9. CLI offline
    out = os.path.join(root, "cli.json")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = A.main(["--settlements-dir", sd, "--bar-dir", bd,
                     "--funding-dir", fd, "--no-venue", "--out", out,
                     "--json"])
    check("CLI returns 0", rc == 0)
    payload = json.loads(buf.getvalue())
    check("CLI manifest sums to the bar universe",
          sum(payload["outcomes"].values()) == payload["bar_universe_n"] == 4)
    check("CLI --no-venue leaves the venue probe empty",
          all(e["venue_total_count"] is None for e in payload["coins"]))
    check("CLI wrote the manifest file", os.path.exists(out))

if _fail:
    print(f"\n{len(_fail)} FAILED: {_fail}")
    sys.exit(1)
print("\nALL PASS")
