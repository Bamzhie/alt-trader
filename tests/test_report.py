"""report tests: windowed flagged hit-rate. Offline: temp DB only."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.report import format_report, signal_stats
from proto.scorer import Scorecard
from proto.store import Store
import tempfile

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def mkcard(coin, direction="LONG"):
    sc = Scorecard(coin=coin, direction=direction, lean=0.5, earlyness=0.5,
                   score=50.0, price=1.0, quote_vol_24h=1e6)
    sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    return sc


tmp = tempfile.mkdtemp()
store = Store(os.path.join(tmp, "r.db"))

old = int(time.time()) - 100_000  # outside the 24h window
now = int(time.time())

# flagged LONG: 2 wins + 1 loss at 1h; flagged SHORT: 1 win at 4h
a = store.log_signal(mkcard("A"), flagged=True)
b = store.log_signal(mkcard("B"), flagged=True)
c = store.log_signal(mkcard("C", "SHORT"), flagged=True)
d = store.log_signal(mkcard("D"), flagged=False)   # shadow: excluded
e = store.log_signal(mkcard("E"), flagged=True)    # old: outside window
store.conn.execute("UPDATE signal_log SET ts=? WHERE id=?", (old, e))
store.conn.commit()
store.log_outcome(a, "1h", 2.0, 2.5, -0.5)
store.log_outcome(b, "1h", -1.0, 0.2, -1.5)
store.log_outcome(c, "4h", 3.0, 3.0, -0.1)
store.log_outcome(d, "1h", 9.0, 9.0, 0.0)    # shadow win: must not count
store.log_outcome(e, "1h", 9.0, 9.0, 0.0)    # old win: must not count
store.log_outcome(a, "7d", 0.0, 0.5, -0.5)   # zero return = loss, not win

st = signal_stats(store, 24)
check("flagged count (windowed)", st["signals"] == 3, str(st["signals"]))
h1 = st["by_horizon"]["1h"]
check("1h resolved 2", h1["resolved"] == 2, str(h1))
check("1h 1W/1L", h1["wins"] == 1 and h1["losses"] == 1, str(h1))
check("1h pct 50", abs(h1["pct_won"] - 50.0) < 1e-9, str(h1))
check("1h avg +0.5", abs(h1["avg_return"] - 0.5) < 1e-9, str(h1))
h4 = st["by_horizon"]["4h"]
check("4h 1W", h4["wins"] == 1 and h4["pct_won"] == 100.0, str(h4))
check("24h empty zeroes", st["by_horizon"]["24h"]["resolved"] == 0)
o = st["overall"]
check("overall 4 rows (1h x2 + 4h + 7d)", o["resolved"] == 4, str(o))
check("overall 2W/2L", o["wins"] == 2 and o["losses"] == 2, str(o))
check("zero-return counts as loss", o["losses"] == 2)
txt = format_report(st)
check("report labels observation scope",
      "FLAGGED SCAN OBSERVATIONS" in txt and "%won" in txt, txt[:100])
check("None store zero shape", signal_stats(None, 24)["signals"] == 0)
store.close()

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
