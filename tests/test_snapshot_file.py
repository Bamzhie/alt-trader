"""close-time snapshot tests: save/load roundtrip + launch resolution."""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gui import model
from proto import snapshot as snap
from proto.planner import Plan
from proto.scorer import Scorecard, Veto
from proto.store import Store

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def mkcard(coin, score):
    sc = Scorecard(coin=coin, direction="LONG", score=score, lean=0.5,
                   oi_change_pct=4.2, oi_notional=1000.0)
    sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.9}
    sc.vetoes = [Veto("late_move", "late_move")]
    return sc


tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "s.db")

check("missing file -> empty", snap.load(db) == (None, None, None))
check("no file, no db -> empty source",
      model.resolve_launch_snapshot(db)[0] == "empty")

cards = [mkcard("A", 90), mkcard("B", 70)]
plans = {"A": {"plan": Plan(coin="A", direction="LONG"), "err": None}}
check("save ok", snap.save(db, cards, plans=plans,
                           meta={"stake": 0.10}) is True)
rcards, rplans, ts = snap.load(db)
check("cards roundtrip",
      [(c.coin, c.score) for c in rcards] == [("A", 90), ("B", 70)])
check("vetoes roundtrip", [v.code for v in rcards[0].vetoes] == ["late_move"])
check("OI fields roundtrip",
      rcards[0].oi_change_pct == 4.2 and rcards[0].oi_notional == 1000.0)
check("plans roundtrip",
      rplans["A"]["plan"].direction == "LONG", str(rplans.keys()))
check("timestamp fresh", time.time() - ts < 60)

source, got, got_plans, label = model.resolve_launch_snapshot(db)
check("file wins over db", source == "file", source)
check("file label names coins", "(2 coins)" in label, label)

# corrupt file -> falls back, never raises
with open(snap.path_for(db), "wb") as f:
    f.write(b"\x00not a pickle\xff")
check("corrupt -> missing", snap.load(db) == (None, None, None))

# stale file -> treated as missing
check("save again ok", snap.save(db, cards) is True)
import pickle
p = pickle.load(open(snap.path_for(db), "rb"))
p["saved_at"] = time.time() - 8 * 86400
pickle.dump(p, open(snap.path_for(db), "wb"), protocol=4)
check("stale -> missing", snap.load(db) == (None, None, None))

# file gone, DB has rows -> db source (no plans)
store = Store(db)
sc = mkcard("C", 60)
sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
store.log_signal(sc, flagged=True)
store.close()
os.remove(snap.path_for(db))
source, got, got_plans, label = model.resolve_launch_snapshot(db)
check("db fallback", source == "db" and len(got) == 1, (source, len(got)))
check("db fallback carries no plans", got_plans == {})

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
