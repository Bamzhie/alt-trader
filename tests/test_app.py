"""
Headless smoke test for the app's non-curses logic: store, sorting, filters,
and one real scan. Runs without a TTY so it can be verified in CI/automation.
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.store import Store
from proto.scorer import Scorecard
from proto import app as appmod

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


print("=== store: schema, logging, shadow rows ===")
tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "t.db")
st = Store(db)


def mkcard(coin, score, direction, vetoes=(), min_not=0.01):
    sc = Scorecard(coin=coin, score=score, direction=direction, lean=0.5,
                   earlyness=0.5, price=1.0, quote_vol_24h=1e6,
                   spread_pct=0.1, funding_rate=0.0001, min_notional=min_not)
    from proto.scorer import Veto
    sc.vetoes = [Veto(v, "reason") for v in vetoes]
    sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    return sc


st.log_signal(mkcard("AAA", 80.0, "LONG"), flagged=True)
st.log_signal(mkcard("BBB", 40.0, "SHORT"), flagged=False)
st.log_signal(mkcard("CCC", 20.0, "LONG", vetoes=("late_move",), min_not=5.0), flagged=False)

check("rows written", st.count() == 3, str(st.count()))
check("flagged count", st.count(flagged=True) == 1, str(st.count(flagged=True)))
check("shadow count (denominator)", st.count(flagged=False) == 2, str(st.count(flagged=False)))
check("distinct coins", st.distinct_coins() == 3)
s = st.stats()
check("stats longs", s["longs"] == 2, str(s))
check("stats shorts", s["shorts"] == 1, str(s))

row = st.conn.execute(
    "SELECT veto_codes, min_notional, flagged FROM signal_log WHERE coin='CCC'").fetchone()
check("veto reason persisted", row[0] == "late_move", str(row))
check("min notional persisted", row[1] == 5.0, str(row))
check("shadow row marked unflagged", row[2] == 0, str(row))

st.set_meta("last_start", "2026-10-07")
check("meta roundtrip", st.get_meta("last_start") == "2026-10-07")
st.close()

print("\n=== reopen persists across sessions ===")
st2 = Store(db)
check("rows survive reopen", st2.count() == 3, str(st2.count()))
st2.close()

print("\n=== sorting and direction filters ===")


class FakeArgs:
    stake = 0.10
    coins = 10
    interval = 60
    db = db
    log_threshold = 18.0
    write_logs = False


a = appmod.App(FakeArgs())
a.cards = [mkcard("L1", 30.0, "LONG"), mkcard("S1", 70.0, "SHORT"),
           mkcard("L2", 50.0, "LONG"), mkcard("N1", 60.0, "NEUTRAL")]

check("sort by score desc",
      [c.coin for c in sorted(a.cards, key=appmod.SORT_KEYS["score"])] ==
      ["S1", "N1", "L2", "L1"])

a.dir_filter = "both"
check("both shows all", len(a.visible()) == 4)
a.dir_filter = "long"
check("long filter", {c.coin for c in a.visible()} == {"L1", "L2"},
      str([c.coin for c in a.visible()]))
a.dir_filter = "short"
check("short filter", {c.coin for c in a.visible()} == {"S1"},
      str([c.coin for c in a.visible()]))
a.dir_filter = "both"

for k in appmod.SORT_KEYS:
    try:
        a.sort_key = k
        a.visible()
        check(f"sort key '{k}' works", True)
    except Exception as e:
        check(f"sort key '{k}' works", False, str(e))

print("\n=== live scan + store write (real network) ===")
a.refresh_universe()
check("universe is non-empty", len(a.uni) > 300, str(len(a.uni)))
check("no TradFi leaked", not any(c in ("OPENAI", "NVIDIA", "NAS100", "XAU",
                                       "AAPLSTOCK", "SILVER") for _, c in a.uni))
t0 = time.time()
a.scan_once()
check("scan produced cards", len(a.cards) > 0, str(len(a.cards)))
check("scan under 90s", time.time() - t0 < 90, f"{time.time()-t0:.1f}s")
check("logs written", a.store.count() > 0, str(a.store.count()))
both = [c for c in a.cards if c.direction in ("LONG", "SHORT")]
check("has directional signals", len(both) > 0, f"{len(both)}")
check("scores in range 0..100",
      all(0 <= c.score <= 100 for c in a.cards),
      f"min={min(c.score for c in a.cards)} max={max(c.score for c in a.cards)}")

print("\n=== plan generation on a real directional card ===")
plan_ok = 0
skipped = 0
for c in a.cards:
    if c.direction == "NEUTRAL":
        continue
    p, err = a.plan_for(c)
    if p is not None and p.valid:
        plan_ok += 1
    else:
        skipped += 1
check("at least one valid plan produced", plan_ok > 0, f"ok={plan_ok} skip={skipped}")
print(f"  ({plan_ok} valid plans, {skipped} unplannable — expected, a move can "
      f"extend past structure)")

a.store.close()
print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)