"""backfill tests: paging, dedupe, resume-safety. Offline: fake venue."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import backfill as bfmod
from proto import mexc

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


BASE_TS = 1_700_000_000
STEP = 300


def fake_get(path, **kw):
    """Deterministic columnar 5m bars for the requested [start, end] window."""
    import re
    m = re.search(r"start=(\d+)&end=(\d+)", path)
    start, end = int(m.group(1)), int(m.group(2))
    first = (start // STEP + 1) * STEP
    ts = []
    t = first
    while t < end and len(ts) < 2000:
        ts.append(t)
        t += STEP
    n = len(ts)
    return {"time": ts, "open": [1.0] * n, "high": [1.1] * n,
            "low": [0.9] * n, "close": [1.05] * n, "vol": [10.0] * n,
            "amount": [10.5] * n}


orig_get = mexc._get
mexc._get = fake_get
try:
    # end param pins the window (backward compatible: default is now)
    bars = mexc.klines("X_USDT", "5m", limit=10, end=BASE_TS)
    check("end pins window", all(b["ts"] < BASE_TS for b in bars)
          and len(bars) == 10, str([b["ts"] for b in bars][-3:]))
    bars_now = mexc.klines("X_USDT", "5m", limit=5)
    check("default end is now", len(bars_now) == 5, str(len(bars_now)))
finally:
    mexc._get = orig_get

tmp = tempfile.mkdtemp()
import time as _t
recent_floor = int(_t.time()) - 30 * STEP
mexc._get = fake_get
try:
    new1, oldest1, err1 = bfmod.backfill_coin("X_USDT", "X", recent_floor,
                                              tmp)
    check("one chunk covers the window (extra history kept, harmless)",
          new1 == 2000, str(new1))
    check("no error", err1 is None, str(err1))
    import gzip, csv
    with gzip.open(os.path.join(tmp, "X.csv.gz"), "rt") as f:
        stored = list(csv.DictReader(f))
    check("stored oldest-first deduped",
          [int(r["ts"]) for r in stored] ==
          sorted(int(r["ts"]) for r in stored)
          and len({r["ts"] for r in stored}) == 2000, str(len(stored)))
    new2, _, err2 = bfmod.backfill_coin("X_USDT", "X", recent_floor,
                                        tmp)
    check("rerun adds nothing (resume-safe)", new2 == 0 and err2 is None,
          f"{new2},{err2}")
    deep_floor = int(_t.time()) - 2500 * STEP
    new3, oldest3, err3 = bfmod.backfill_coin("X_USDT", "X", deep_floor, tmp)
    check("multi-chunk paging reaches 2500 bars back",
          err3 is None and oldest3 is not None and oldest3 <= deep_floor,
          f"{err3},{oldest3}")
    import gzip as _gz, csv as _csv
    with _gz.open(os.path.join(tmp, "X.csv.gz"), "rt") as f:
        allrows = list(_csv.DictReader(f))
    check("merged file deduped across chunks",
          len({r["ts"] for r in allrows}) == len(allrows), str(len(allrows)))
finally:
    mexc._get = orig_get

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
