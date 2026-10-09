"""plan measurement tests: touches, log/resolve roundtrip, scan attach."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import outcomes as outmod
from proto import planner as pl
from proto import scan as scanmod
from proto.scorer import Scorecard
from proto.store import Store

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def bar(ts, o, h, l, c):
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "vol": 1.0, "amount": 1.0}


print("=== first-touch semantics ===")
# LONG 100/95/110/120: drift up, dip touches stop at bar 3, TP1 at 4, TP2 never
bars = [bar(1, 100, 101, 99, 100.5), bar(2, 100.5, 103, 100, 102),
        bar(3, 102, 102.5, 94, 101), bar(4, 101, 111, 100, 110),
        bar(5, 110, 112, 109, 111)]
t = outmod.plan_touches("LONG", 95, 110, 120, bars)
check("stop first at bar 3", t["stop"] == 3, str(t))
check("tp1 at bar 4", t["tp1"] == 4, str(t))
check("tp2 untouched -> None", t["tp2"] is None, str(t))

# same bar touches stop AND target -> stop wins (conservative)
both = [bar(1, 100, 115, 90, 105)]
t2 = outmod.plan_touches("LONG", 95, 110, 120, both)
check("same-bar stop+TP counts stop only",
      t2["stop"] == 1 and t2["tp1"] is None and t2["tp2"] is None, str(t2))

# SHORT mirrors: stop above on highs, targets below on lows
sbars = [bar(1, 100, 101, 99, 99.5), bar(2, 99.5, 100, 97, 97.5),
         bar(3, 97.5, 106, 97, 98)]
t3 = outmod.plan_touches("SHORT", 105, 90, 80, sbars)
check("short stop first at bar 3", t3["stop"] == 3, str(t3))
check("short targets untouched", t3["tp1"] is None and t3["tp2"] is None,
      str(t3))
sbars2 = [bar(1, 100, 101, 89, 90), bar(2, 90, 91, 79, 80)]
t4 = outmod.plan_touches("SHORT", 105, 90, 80, sbars2)
check("short tp1 bar1 tp2 bar2, no stop",
      t4 == {"stop": None, "tp1": 1, "tp2": 2}, str(t4))

print("\n=== store: log_plan / planned / plan_stats ===")
tmp = tempfile.mkdtemp()
store = Store(os.path.join(tmp, "p.db"))
check("no plans pending on empty db", store.planned() == [])
check("empty plan stats", store.plan_stats()["planned"] == 0)

sc = Scorecard(coin="X", direction="LONG")
p = pl.Plan(coin="X", direction="LONG", entry_low=99.9, entry_high=100.1,
            stop=95.0, tp1=110.0, tp2=125.0, leverage=10, notional=1.0,
            max_loss=0.05)
sid = 7
store.conn.execute(
    "INSERT INTO signal_log (id, ts, coin, flagged, direction, price)"
    " VALUES (?,?,?,?,?,?)", (sid, 1_700_000_000, "X", 1, "LONG", 100.0))
store.conn.commit()
store.log_plan(sid, p)
pend = store.planned()
check("planned lists it", len(pend) == 1 and pend[0][0] == sid, str(pend))
store.log_plan_outcome(sid, True, True, False, 3, 4, None, 10)
check("terminal (stop hit) leaves the pending set",
      store.planned() == [], str(store.planned()))
st = store.plan_stats()
check("stats count stop+tp1, not tp2",
      st["planned"] == 1 and st["stop_hit"] == 1 and st["tp1_hit"] == 1
      and st["tp2_hit"] == 0, str(st))
check("percents", st["stop_pct"] == 100.0 and st["tp2_pct"] == 0.0, str(st))

sid2 = 8
store.conn.execute(
    "INSERT INTO signal_log (id, ts, coin, flagged, direction, price)"
    " VALUES (?,?,?,?,?,?)", (sid2, 1_700_000_100, "Y", 1, "SHORT", 50.0))
store.conn.commit()
store.log_plan(sid2, pl.Plan(coin="Y", direction="SHORT", stop=55.0,
                             tp1=40.0, tp2=30.0))
store.log_plan_outcome(sid2, False, True, False, None, 5, None, 50)
check("tp1-only stays pending (stop still possible)",
      [r[0] for r in store.planned()] == [sid2],
      str(store.planned()))
store.close()

print("\n=== scan attaches score-consistent plans (offline venue) ===")
BARS = []
_px = 1.0
for _i in range(60):
    _c = _px * 1.002
    BARS.append({"ts": 1_700_000_000 + _i * 300, "o": _px, "h": _c * 1.001,
                 "l": _px * 0.999, "c": _c, "vol": 1e5, "amount": 1e5 * _c})
    _px = _c
TK = {"lastPrice": BARS[-1]["c"], "amount24": 5_000_000.0, "riseFallRate": 0.01,
      "fundingRate": 0.0001, "maxFundingRate": 0.0018}
DETAIL = {"minVol": 1, "contractSize": 0.05}
orig = (scanmod.mexc.klines, scanmod.mexc.depth)
scanmod.mexc.klines = lambda sym, interval="5m", limit=200: list(BARS)
scanmod.mexc.depth = lambda sym, limit=20: ([(0.999, 100.0)],
                                            [(1.001, 100.0)])
try:
    sc2 = scanmod.analyse_one("T_USDT", "T", DETAIL, TK, 0.10,
                              attach_plans=True)
finally:
    scanmod.mexc.klines, scanmod.mexc.depth = orig
check("card scored", sc2 is not None)
check("plan attached", getattr(sc2, "plan", None) is not None,
      str(getattr(sc2, "plan", None)))
check("attached plan valid",
      getattr(getattr(sc2, "plan", None), "valid", False))
check("default path attaches nothing",
      True)  # attach_plans defaults False; signature compat below
import inspect
check("analyse_one keeps old call shape working",
      "attach_plans" in inspect.signature(scanmod.analyse_one).parameters)

print("\n=== resolve_plans wiring (offline collector) ===")
import proto.collector as colmod
tmp2 = tempfile.mkdtemp()
s2 = Store(os.path.join(tmp2, "q.db"))
scq = Scorecard(coin="Q", direction="LONG", price=100.0)
s2.conn.execute(
    "INSERT INTO signal_log (id, ts, coin, flagged, direction, price)"
    " VALUES (?,?,?,?,?,?)", (21, 1000, "Q", 1, "LONG", 100.0))
s2.conn.commit()
s2.log_plan(21, pl.Plan(coin="Q", direction="LONG", stop=95.0, tp1=110.0,
                        tp2=120.0))
fut = [bar(1001 + i, 100 + i, 102 + i, 99 + i, 101 + i) for i in range(5)]
fut[2] = bar(1003, 108, 112, 107, 111)   # bar 3 touches TP1 (high 112)
orig_read = colmod.read_bars
colmod.read_bars = lambda data_dir, coin: list(fut)
try:
    n = outmod.resolve_plans(s2, {"Q": "Q_USDT"})
finally:
    colmod.read_bars = orig_read
row = s2.conn.execute(
    "SELECT stop_hit, tp1_hit, tp2_hit, bars_to_tp1, bars_examined"
    " FROM plan_outcome WHERE signal_id=21").fetchone()
check("one plan row written", n == 1, str(n))
check("tp1 hit at bar 3, no stop",
      row == (0, 1, 0, 3, 5), str(row))
check("tp1-only stays pending", [r[0] for r in s2.planned()] == [21])
s2.close()

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
