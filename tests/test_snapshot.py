"""snapshot tests: latest_rows + snapshot_cards roundtrip. Offline only."""

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


def mkcard(coin, score, vetoes=(), oi=4.2, notion=1000.0):
    sc = Scorecard(coin=coin, direction="LONG", score=score, lean=0.5,
                   earlyness=0.5, price=2.0, change_24h_pct=1.0,
                   quote_vol_24h=1e6, spread_pct=0.1, funding_rate=0.0001,
                   oi_change_pct=oi, oi_notional=notion, min_notional=0.01)
    sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.9}
    sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.8}
    sc.vetoes = [Veto(v, v) for v in vetoes]
    return sc


tmp = tempfile.mkdtemp()
store = Store(os.path.join(tmp, "s.db"))
check("empty DB -> no rows", store.latest_rows() == [])

store.log_signal(mkcard("A", 90), flagged=True)
store.log_signal(mkcard("B", 70, vetoes=("late_move",)), flagged=False)
old_id = store.log_signal(mkcard("OLD", 99), flagged=True)
store.conn.execute("UPDATE signal_log SET ts=? WHERE id=?",
                   (int(time.time()) - 10_000, old_id))
store.conn.commit()

rows = store.latest_rows()
check("current cycle only", [r["coin"] for r in rows] == ["A", "B"],
      str([r["coin"] for r in rows]))
check("score ordered", rows[0]["score"] == 90)
check("OI percent persisted", rows[0]["oi_change_pct"] == 4.2,
      str(rows[0].get("oi_change_pct")))
check("OI notional persisted", rows[0]["oi_notional"] == 1000.0)
check("veto codes persisted", rows[1]["veto_codes"] == "late_move")

cards = model.snapshot_cards(rows)
check("two cards rebuilt", len(cards) == 2)
check("repeat coin keeps first (highest-scored) row",
      len(model.snapshot_cards([{"coin": "A", "score": 90.0},
                                {"coin": "A", "score": 80.0}])) == 1)
a = cards[0]
check("fields restored",
      a.coin == "A" and a.score == 90 and a.direction == "LONG"
      and a.lean == 0.5 and a.price == 2.0 and a.oi_notional == 1000.0)
check("parts restored",
      a.magnitude_parts.get("OI") == 0.9 and a.lean_parts.get("BOOK") == 0.4)
check("vetoes rebuilt with codes",
      [v.code for v in cards[1].vetoes] == ["late_move"],
      str(cards[1].vetoes))
check("label names time+coins",
      model.snapshot_label(rows).startswith("saved ") and "(2 coins)" in
      model.snapshot_label(rows), model.snapshot_label(rows))
check("empty label", model.snapshot_label([]) == "")
store.close()

# legacy DB (pre-oi_change_pct column, but otherwise current) snapshots
lp = os.path.join(tmp, "legacy.db")
import sqlite3
c = sqlite3.connect(lp)
c.execute("CREATE TABLE signal_log (id INTEGER PRIMARY KEY, ts INTEGER, "
          "coin TEXT, venue TEXT DEFAULT 'MEXC', flagged INTEGER DEFAULT 1, "
          "score REAL, lean REAL, direction TEXT, earlyness REAL, "
          "mag_vol REAL, mag_book REAL, mag_oi REAL, lean_vol REAL, "
          "lean_book REAL, lean_oi REAL, price REAL, change_24h_pct REAL, "
          "quote_vol_24h REAL, spread_pct REAL, funding_rate REAL, "
          "min_notional REAL, veto_codes TEXT, tradeable INTEGER DEFAULT 1, "
          "tier INTEGER DEFAULT 2, flag_version INTEGER DEFAULT 1, "
          "oi_notional REAL)")
c.execute("INSERT INTO signal_log (ts, coin, score) VALUES (?,?,?)",
          (int(time.time()), "X", 10.0))
c.commit()
c.close()
s2 = Store(lp)
r2 = s2.latest_rows()
check("legacy row snapshots with NULL OI",
      len(r2) == 1 and r2[0]["oi_change_pct"] is None, str(r2))
s2.close()

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
