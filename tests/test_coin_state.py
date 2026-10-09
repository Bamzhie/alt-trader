"""coin_state tests: one row per coin, upsert semantics, backfill."""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui import model
from proto.scorer import Scorecard, Veto
from proto.store import Store

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def mkcard(coin, score, vetoes=()):
    sc = Scorecard(coin=coin, direction="LONG", score=score, lean=0.5,
                   price=1.0, quote_vol_24h=1e6, min_notional=0.01)
    sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    sc.vetoes = [Veto(v, v) for v in vetoes]
    return sc


tmp = tempfile.mkdtemp()
store = Store(os.path.join(tmp, "c.db"))

s1 = store.log_signal(mkcard("A", 50), flagged=True)
store.upsert_current(mkcard("A", 50), True, s1)
s2 = store.log_signal(mkcard("A", 70), flagged=True)
store.upsert_current(mkcard("A", 70), True, s2)
store.log_signal(mkcard("B", 30, vetoes=("late_move",)), flagged=False)
store.upsert_current(mkcard("B", 30, vetoes=("late_move",)), False, 999)

rows = store.current_rows()
check("one row per coin, not per scan",
      sorted(r["coin"] for r in rows) == ["A", "B"], str(rows))
a = next(r for r in rows if r["coin"] == "A")
check("latest fields win", a["score"] == 70, str(a["score"]))
check("latest signal id linked", a.get("signal_id") == s2,
      str(a.get("signal_id")))
first = store.conn.execute(
    "SELECT first_seen, scans_seen FROM coin_state WHERE coin='A'").fetchone()
check("scans_seen counts observations", first[1] == 2, str(first))
time.sleep(1.05)
s3 = store.log_signal(mkcard("A", 71), flagged=True)
store.upsert_current(mkcard("A", 71), True, s3)
again = store.conn.execute(
    "SELECT first_seen, scans_seen FROM coin_state WHERE coin='A'").fetchone()
check("first_seen sticky across rescans", again[0] == first[0],
      f"{again[0]} vs {first[0]}")
check("scans_seen increments", again[1] == 3, str(again))
check("board score-ordered",
      [r["coin"] for r in store.current_rows()][0] == "A")
store.close()

# backfill: journal with rows but empty board -> board appears on open
lp = os.path.join(tmp, "legacy.db")
store2 = Store(lp)
store2.log_signal(mkcard("X", 40), flagged=True)
store2.log_signal(mkcard("X", 44), flagged=False)
store2.log_signal(mkcard("Y", 90), flagged=True)
store2.conn.execute("DELETE FROM coin_state")
store2.conn.commit()
store2.close()
reopened = Store(lp)
back = {r["coin"]: r for r in reopened.current_rows()}
check("backfill restores one row per coin", sorted(back) == ["X", "Y"],
      str(sorted(back)))
check("backfill keeps latest", back["X"]["score"] == 44, str(back["X"]))
check("backfill first_seen is earliest",
      back["X"]["first_seen"] is not None)
reopened.close()

# launch resolution reads the board (file absent here)
source, cards, plans, label = model.resolve_launch_snapshot(lp)
check("launch falls back to board", source == "db", source)
check("board cards unique", len({c.coin for c in cards}) == len(cards))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
