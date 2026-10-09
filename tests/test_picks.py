"""picks tests: top10 / watch / new / daily-top20. Offline only."""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import picks
from proto.report import format_top20
from proto.scorer import Scorecard, Veto
from proto.store import Store

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def mkcard(coin, score, direction="LONG", min_notional=0.01, vetoes=()):
    sc = Scorecard(coin=coin, direction=direction, score=score,
                   lean=0.5 if direction == "LONG" else -0.5,
                   min_notional=min_notional, price=1.0)
    sc.vetoes = [Veto(v, "r") for v in vetoes]
    return sc


cards = [mkcard("A", 90), mkcard("B", 80, min_notional=50.0),
         mkcard("C", 70, direction="SHORT"), mkcard("D", 60, vetoes=("late_move",)),
         mkcard("E", 50, direction="NEUTRAL"), mkcard("F", 40, min_notional=None),
         mkcard("Q", 85, min_notional=2.48)]

top = picks.top_picks(cards, stake=0.10, threshold=24.0)
check("picks only actionable+threshold",
      [c.coin for c in top] == ["A", "Q", "C"], str([c.coin for c in top]))
check("leveraged coin included in picks",
      "Q" in [c.coin for c in top])
check("vetoed/neutral/watch excluded from picks",
      all(c.coin not in ("B", "D", "E", "F") for c in top))

watch = picks.watch_list(cards, stake=0.10)
check("watch = stake-blocked only, by score",
      [c.coin for c in watch] == ["B", "F"], str([c.coin for c in watch]))

# --- store-backed: new listings + daily top20 ---
tmp = tempfile.mkdtemp()
store = Store(os.path.join(tmp, "p.db"))
now = int(time.time())


def log_at(coin, score, direction, ts, flagged=True):
    sc = mkcard(coin, score, direction)
    sc.magnitude_parts = {"VOL": .5, "BOOK": .4, "OI": .3}
    sc.lean_parts = {"VOL": .5, "BOOK": .4, "OI": .3}
    sid = store.log_signal(sc, flagged=flagged)
    store.conn.execute("UPDATE signal_log SET ts=? WHERE id=?", (ts, sid))
    store.conn.commit()
    return sid


log_at("OLD", 95, "LONG", now - 10 * 86400)          # first seen 10d ago
log_at("NEW1", 80, "LONG", now - 3600)
log_at("NEW2", 70, "SHORT", now - 7200)
log_at("NEW1", 60, "LONG", now - 60)                 # latest row wins score

new = picks.new_listings(store, days=7)
check("old coin excluded", all(d["coin"] != "OLD" for d in new), str(new))
check("newest by latest score",
      [d["coin"] for d in new] == ["NEW2", "NEW1"] or
      [d["coin"] for d in new] == ["NEW1", "NEW2"], str(new))
check("latest row supplies score",
      next(d for d in new if d["coin"] == "NEW1")["score"] == 60, str(new))

s1 = log_at("W1", 90, "LONG", now - 3600)
s2 = log_at("W2", 80, "SHORT", now - 3600)
log_at("SH", 99, "LONG", now - 3600, flagged=False)  # shadow: never a pick
store.log_outcome(s1, "1h", 2.0, 2.5, -0.5)
store.log_outcome(s2, "1h", -1.0, 0.2, -1.5)

days = picks.daily_top20(store, days=2)
check("two day buckets", len(days) == 2, str(len(days)))
today = days[-1]
check("shadow excluded from picks",
      all(p["coin"] != "SH" for p in today["picks"]), str(today["picks"]))
check("top20 score-ordered",
      [p["score"] for p in today["picks"]] ==
      sorted([p["score"] for p in today["picks"]], reverse=True))
h1 = today["hits"]["1h"]
check("1h 1W/1L of the day's picks", h1["resolved"] == 2 and h1["wins"] == 1,
      str(h1))
check("unresolved horizon zeroes", today["hits"]["24h"]["resolved"] == 0)
txt = format_top20(days)
check("top20 report renders", "DAILY TOP-20" in txt and "1h:res/%w" in txt)
store.close()

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
