"""Watcher lifecycle state machine + persistence + recovery (Task 6).

Self-checking script: `python3 tests/test_watcher_lifecycle.py`.
Covers dedupe/cooldown, CUSUM-fire-fed emission (no double-count),
eval_bar order gap->barriers(stop-first)->close-invalidation, same-bar
stop+tgt -> resolved_stop, invalidation never masks a touch,
48 unavailable bars -> expired_gap, restart replay idempotency, terminal
immutability, recompute tolerance 1e-9 -> unavailable, and
apply_breakeven called exactly once per ordered closed bar.
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import watcher

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


T0 = 1_700_000_000


def mksetup(rule="t1-break-v1", coin="DOGE", ts=T0, entry=100.0,
            direction="LONG", N=288, trail="none"):
    b_stop, a_tgt = 0.02, 0.04
    if direction == "LONG":
        stop, tgt = entry * (1 - b_stop), entry * (1 + a_tgt)
    else:
        stop, tgt = entry * (1 + b_stop), entry * (1 - a_tgt)
    return {"id": f"{rule}:{coin}:{ts}", "rule_id": rule, "coin": coin,
            "symbol": coin + "USDT",
            "src": "t1_break" if rule.startswith("t1") else "cusum_long",
            "direction": direction, "src_bar_ts": ts, "entry": entry,
            "b_stop": b_stop, "a_tgt": a_tgt, "stop_px": stop, "tgt_px": tgt,
            "N_bars": N, "trail": trail, "created_at": ts}


def bar(ts, o=100.0, h=101.0, l=99.0, c=100.0):
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c}


def ctx(ts, **kw):
    d = {"now": ts + 300, "bar_ts": ts}
    d.update(kw)
    return d


def memdb():
    conn = sqlite3.connect(":memory:")
    watcher.ensure_watcher_tables(conn)
    return conn


print("=== ensure_watcher_tables (exact spec DDL) ===")
conn = memdb()
tables = {r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
check("watcher_setup created", "watcher_setup" in tables)
check("watcher_eval created", "watcher_eval" in tables)
cols = {r[1] for r in conn.execute("PRAGMA table_info(watcher_setup)")}
for c in ("id", "rule_id", "coin", "symbol", "src", "direction",
          "src_bar_ts", "entry", "b_stop", "a_tgt", "stop_px", "tgt_px",
          "stop_dist_pct", "tgt_dist_pct", "N_bars", "trail", "state",
          "substate", "monitoring", "price_source", "cost_total",
          "cost_floor", "funding_state", "funding_projected", "fund_stress",
          "shadow_selected", "ev_r", "p_lb", "invalid_price",
          "last_eval_bar", "unavailable_bars", "created_at", "closed_at"):
    check(f"setup col {c}", c in cols, f"missing {c}")
ecols = {r[1] for r in conn.execute("PRAGMA table_info(watcher_eval)")}
for c in ("setup_id", "bar_ts", "state", "monitoring", "ret_gross",
          "ret_net", "note"):
    check(f"eval col {c}", c in ecols, f"missing {c}")

print("\n=== emit_setup dedupe + cooldown ===")
s = mksetup()
i1 = watcher.emit_setup(conn, s)
i2 = watcher.emit_setup(conn, dict(s))  # repeated qualifying bar
check("repeated bars -> same id", i1 == i2 == s["id"], f"{i1} vs {i2}")
n = conn.execute("SELECT COUNT(*) FROM watcher_setup").fetchone()[0]
check("no duplicate row", n == 1, f"count={n}")
s2 = mksetup(ts=T0 + 3600)  # 1h later, inside T1 24h cooldown
i3 = watcher.emit_setup(conn, s2)
n = conn.execute("SELECT COUNT(*) FROM watcher_setup").fetchone()[0]
check("cooldown suppresses second emit", n == 1 and i3 == i1,
      f"count={n} id={i3}")
s3 = mksetup(ts=T0 + 90_000)  # 25h later, past cooldown
i4 = watcher.emit_setup(conn, s3)
n = conn.execute("SELECT COUNT(*) FROM watcher_setup").fetchone()[0]
check("post-cooldown bar emits new row", n == 2 and i4 == s3["id"],
      f"count={n} id={i4}")

print("\n=== CUSUM-fire-fed emission (no double-count) ===")
conn2 = memdb()
zs = [1.0] * 15  # S+ grows 0.5/bar -> fires once at idx 8 (H=4)
fires = watcher.cusum_fire_ids(zs, k=0.5, H=4.0)
check("cusum fires once in 15 bars", len(fires) == 1, str(fires))
base = T0 + 1_000_000
bars = [bar(base + i * 300, c=100.0 + i) for i in range(15)]
ids = []
for side, i in fires:
    setup = mksetup(rule="cusum-raw-v1", coin="SOL",
                    ts=bars[i]["ts"], entry=bars[i]["c"],
                    direction=side, N=288)
    setup["src"] = "cusum_long" if side == "LONG" else "cusum_short"
    ids.append(watcher.emit_setup(conn2, setup))
n = conn2.execute("SELECT COUNT(*) FROM watcher_setup").fetchone()[0]
check("15 raw bars -> 1 emitted setup", n == 1, f"count={n}")
# Replaying the same fires (restart) adds nothing.
ids2 = [watcher.emit_setup(conn2, mksetup(
    rule="cusum-raw-v1", coin="SOL", ts=bars[i]["ts"],
    entry=bars[i]["c"], direction=side, N=288)) for side, i in fires]
check("replayed fires dedupe", ids2 == ids)
n = conn2.execute("SELECT COUNT(*) FROM watcher_setup").fetchone()[0]
check("still one row after replay", n == 1, f"count={n}")

print("\n=== eval_bar order: barriers stop-first ===")
conn3 = memdb()
s = mksetup(N=288)
sid = watcher.emit_setup(conn3, s)
# Same-bar stop+target touch -> stop wins (conservative).
st, mon = watcher.eval_bar(conn3, sid, bar(T0 + 300, o=100, h=105, l=97, c=101),
                           ctx(T0 + 300))
check("same-bar stop+tgt -> resolved_stop", st == "resolved_stop",
      f"{st}/{mon}")
row = conn3.execute(
    "SELECT state, substate FROM watcher_setup WHERE id=?", (sid,)).fetchone()
check("stored state/substate resolved/stop", tuple(row) == ("resolved", "stop"),
      str(row))
# Terminal row never rewritten by later bars.
st2, _ = watcher.eval_bar(conn3, sid, bar(T0 + 600, o=100, h=110, l=99, c=109),
                          ctx(T0 + 600))
check("terminal immutable", st2 == "resolved_stop", st2)
row2 = conn3.execute(
    "SELECT state, substate FROM watcher_setup WHERE id=?", (sid,)).fetchone()
check("terminal row unchanged", tuple(row2) == tuple(row), str(row2))

print("\n=== invalidation never masks a touch ===")
conn4 = memdb()
sid = watcher.emit_setup(conn4, mksetup(ts=T0, N=288))
st, _ = watcher.eval_bar(conn4, sid, bar(T0 + 300, o=100, h=105, l=97, c=101),
                         ctx(T0 + 300, invalidate=True, invalid_price=103.0))
check("barrier-day invalidation attempt after touch ignored",
      st == "resolved_stop", st)
sid2 = watcher.emit_setup(conn4, mksetup(ts=T0 + 200_000, N=288))
st, _ = watcher.eval_bar(conn4, sid2,
                         bar(T0 + 200_300, o=100, h=101, l=99, c=100),
                         ctx(T0 + 200_300, invalidate=True, invalid_price=103.0))
check("clean-bar invalidation -> invalidated", st == "invalidated", st)
px = conn4.execute(
    "SELECT invalid_price FROM watcher_setup WHERE id=?", (sid2,)).fetchone()[0]
check("invalid_price recorded", px == 103.0, str(px))

print("\n=== gap order + 48-gap expiry ===")
conn5 = memdb()
sid = watcher.emit_setup(conn5, mksetup(ts=T0, N=10_000))
# Forming bar (ts+300 > now) is unavailable even if H/L cross barriers.
st, mon = watcher.eval_bar(conn5, sid, bar(T0 + 300, o=100, h=200, l=1, c=150),
                           {"now": T0 + 300 + 299, "bar_ts": T0 + 300})
check("forming bar -> active/unavailable, no resolution",
      (st, mon) == ("active", "unavailable"), f"{st}/{mon}")
ub = conn5.execute(
    "SELECT unavailable_bars FROM watcher_setup WHERE id=?", (sid,)).fetchone()[0]
check("unavailable counter = 1", ub == 1, str(ub))
# 47 more unavailable bars (missing bar) -> still active.
for k in range(2, 48):
    ts = T0 + k * 300
    st, mon = watcher.eval_bar(conn5, sid, None, ctx(ts, missing=True))
check("47 unavailable -> still active", st == "active" and mon == "unavailable",
      f"{st}/{mon}")
ts = T0 + 48 * 300
st, mon = watcher.eval_bar(conn5, sid, None, ctx(ts, missing=True))
check("48th consecutive unavailable -> expired_gap", st == "expired_gap",
      f"{st}/{mon}")
row = conn5.execute(
    "SELECT state, substate FROM watcher_setup WHERE id=?", (sid,)).fetchone()
check("stored expired/gap", tuple(row) == ("expired", "gap"), str(row))
st2, _ = watcher.eval_bar(conn5, sid, bar(ts + 300), ctx(ts + 300))
check("expired_gap terminal immutable", st2 == "expired_gap", st2)

print("\n=== time expiry ===")
conn6 = memdb()
sid = watcher.emit_setup(conn6, mksetup(ts=T0, N=2))
watcher.eval_bar(conn6, sid, bar(T0 + 300, c=100.5), ctx(T0 + 300))
st, _ = watcher.eval_bar(conn6, sid, bar(T0 + 600, c=100.6), ctx(T0 + 600))
check("age >= N -> expired_time", st == "expired_time", st)

print("\n=== apply_breakeven exactly once per ordered closed bar ===")
conn7 = memdb()
sid = watcher.emit_setup(conn7, mksetup(ts=T0, N=288, trail="breakeven"))
calls = []
orig = watcher.apply_breakeven


def counting(state, close):
    calls.append(close)
    return orig(state, close)


watcher.apply_breakeven = counting
try:
    for k in range(1, 4):
        ts = T0 + k * 300
        watcher.eval_bar(conn7, sid, bar(ts, o=100, h=101, l=99, c=100),
                         ctx(ts))
finally:
    watcher.apply_breakeven = orig
check("3 closed bars -> 3 breakeven calls", len(calls) == 3, str(calls))
# Trail: trigger close >= entry+1*b_stop arms; next bar uses moved stop.
conn8 = memdb()
sid = watcher.emit_setup(conn8, mksetup(ts=T0, N=288, trail="breakeven"))
ts1 = T0 + 300
watcher.eval_bar(conn8, sid, bar(ts1, o=100, h=103, l=99.5, c=102.5),
                 ctx(ts1))  # close 102.5 >= 102 arms pending
ts2 = T0 + 600
st, _ = watcher.eval_bar(conn8, sid, bar(ts2, o=102.5, h=103, l=99.0, c=101),
                         ctx(ts2))  # l=99 < moved stop 100 -> stop
check("post-move touch uses moved stop (stop-first)",
      st == "resolved_stop", st)

print("\n=== recompute tolerance 1e-9 ===")
conn9 = memdb()
sid = watcher.emit_setup(conn9, mksetup(ts=T0, N=288))
st, mon = watcher.eval_bar(conn9, sid, bar(T0 + 300, c=100.5), ctx(
    T0 + 300, recompute_expected=0.01, recompute_actual=0.01 + 1e-8))
check("mismatch >1e-9 -> monitoring unavailable, active preserved",
      (st, mon) == ("active", "unavailable"), f"{st}/{mon}")
st, mon = watcher.eval_bar(conn9, sid, bar(T0 + 600, c=100.5), ctx(
    T0 + 600, recompute_expected=0.01, recompute_actual=0.01 + 1e-10))
check("mismatch <=1e-9 -> ok", (st, mon) == ("active", "ok"), f"{st}/{mon}")

print("\n=== checkpoint / restart replay ===")
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
c1 = sqlite3.connect(tmp.name)
watcher.ensure_watcher_tables(c1)
sid = watcher.emit_setup(c1, mksetup(ts=T0, N=288))
watcher.checkpoint(c1, "DOGE", "t1-break-v1", T0 + 300)
c1.commit()
c1.close()  # kill process
c2 = sqlite3.connect(tmp.name)  # reopen
watcher.ensure_watcher_tables(c2)
rep = watcher.replay_since(c2, "DOGE", "t1-break-v1")
check("watermark survives restart", rep["watermark"] == T0 + 300, str(rep))
check("one active setup after restart, no dup",
      rep["active_ids"] == [sid], str(rep))
sid_r = watcher.emit_setup(c2, mksetup(ts=T0, N=288))
check("re-emit after restart -> same id", sid_r == sid, sid_r)
n = c2.execute("SELECT COUNT(*) FROM watcher_setup").fetchone()[0]
check("still one row", n == 1, f"count={n}")
os.unlink(tmp.name)
c2.close()

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
