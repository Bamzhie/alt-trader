"""Watcher shadow selector + report/UI wiring (Task 7).

Self-checking script: `python3 tests/test_watcher_selector.py`.
Covers ev_r hand value, cost_total composer (floor 0, signed leg kept),
shadow_select (funding-unavailable never selected, K ceiling, <=2 per
|rho|>0.7 cluster, below-hurdle never selected, pause/disable silence),
non-suppression (unselected setups stay `active`; only shadow columns
written; scanner tables untouched), and the watcher_report CLI (states,
coverage input x coin x date, gross/net columns, correlated opens,
labeled assumptions, research banner).
"""

import os
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import watcher
from proto import watcher_report

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def close(a, b, tol=1e-12):
    return abs(a - b) <= tol


def sel_setup(sid, p=0.6, a=0.04, b=0.02, c=0.0017, funding="ok",
              cluster=None, rule="t1-break-v1"):
    s = {"id": sid, "rule_id": rule, "p_lb": p, "a_tgt": a, "b_stop": b,
         "c_total": c, "funding": funding}
    if cluster is not None:
        s["cluster"] = cluster
    return s


T0 = 1_700_000_000

print("=== ev_r ===")
# Hand value: (0.5*0.04 - 0.5*0.02 - 0.0017)/0.02 = 0.0083/0.02 = 0.415.
check("ev_r hand value",
      close(watcher.ev_r(0.5, 0.04, 0.02, 0.0017), 0.415),
      str(watcher.ev_r(0.5, 0.04, 0.02, 0.0017)))
check("ev_r zero costs",
      close(watcher.ev_r(0.5, 0.04, 0.02, 0.0), 0.5),
      str(watcher.ev_r(0.5, 0.04, 0.02, 0.0)))
check("ev_r certain win minus costs",
      close(watcher.ev_r(1.0, 0.04, 0.02, 0.0017),
            (0.04 - 0.0017) / 0.02))

print("\n=== cost_total composer (Task 3 deferred minor, defined once) ===")
base = 0.0017
rates = [0.0001, 0.0001, 0.0001]
check("long funding raises c_total",
      close(watcher.cost_total(base, "LONG", rates), base + 0.0003),
      str(watcher.cost_total(base, "LONG", rates)))
check("short rebate lowers but stays >= 0",
      close(watcher.cost_total(base, "SHORT", rates), base - 0.0003),
      str(watcher.cost_total(base, "SHORT", rates)))
check("short huge rebate floored at 0",
      watcher.cost_total(base, "SHORT", [0.01, 0.01, 0.01]) == 0.0)
check("signed leg kept for net column (fund_cost still negative)",
      watcher.fund_cost("SHORT", rates) < 0.0)
check("net_ret keeps signed leg (no floor)",
      close(watcher.net_ret(0.01, base, "SHORT", rates),
            0.01 - (base - 0.0003)),
      str(watcher.net_ret(0.01, base, "SHORT", rates)))

print("\n=== shadow_select: funding-unavailable never selected ===")
hot_unfunded = sel_setup("t1-break-v1:AAA:1", p=0.95, a=0.10, b=0.01,
                         c=0.0001, funding="unavailable")
cold_funded = sel_setup("t1-break-v1:BBB:1", p=0.6)
picked = watcher.shadow_select([hot_unfunded, cold_funded], K=15, ev_hurdle=0.1)
check("stale-funding setup silent despite sky-high EV",
      "t1-break-v1:AAA:1" not in picked, str(picked))
check("funded setup selected", "t1-break-v1:BBB:1" in picked, str(picked))

print("\n=== shadow_select: below-hurdle never selected ===")
weak = sel_setup("t1-break-v1:CCC:1", p=0.3)  # EV ~ -0.185
picked = watcher.shadow_select([weak], K=15, ev_hurdle=0.1)
check("weak EV excluded", picked == set(), str(picked))
ev_weak = watcher.ev_r(0.3, 0.04, 0.02, 0.0017)
check("sanity: weak EV below hurdle", ev_weak < 0.1, str(ev_weak))

print("\n=== shadow_select: K ceiling enforced ===")
many = [sel_setup(f"t1-break-v1:C{i:02d}:1", p=0.5 + 0.01 * i,
                  cluster=f"cl{i:02d}") for i in range(20)]
picked = watcher.shadow_select(many, K=15, ev_hurdle=0.1)
check("20 eligible, K=15 -> 15 picked", len(picked) == 15, str(len(picked)))
evs = sorted((watcher.ev_r(s["p_lb"], s["a_tgt"], s["b_stop"], s["c_total"])
              for s in many))
cutoff = evs[-15]
check("top-15 by EV win (lowest picked >= cutoff)",
      all(watcher.ev_r(s["p_lb"], s["a_tgt"], s["b_stop"], s["c_total"]) >= cutoff
          for s in many if s["id"] in picked))

print("\n=== shadow_select: 3 mutually-correlated -> <=2 selected ===")
trio = [sel_setup(f"cusum-raw-v1:QQ:{i}", p=0.9 - 0.01 * i, cluster="hot")
        for i in range(3)]
picked = watcher.shadow_select(trio, K=15, ev_hurdle=0.1)
check("one |rho|>0.7 cluster yields <=2", len(picked) <= 2, str(picked))
check("cluster winners are top-2 by EV",
      picked == {"cusum-raw-v1:QQ:0", "cusum-raw-v1:QQ:1"}, str(picked))

print("\n=== shadow_select: pause + per-trigger disable ===")
ok = sel_setup("t1-break-v1:DDD:1", p=0.6)
check("global pause -> empty", watcher.shadow_select([ok], paused=True) == set())
check("disabled rule excluded",
      watcher.shadow_select([ok], disabled=("t1-break-v1",)) == set(),
      str(watcher.shadow_select([ok], disabled=("t1-break-v1",))))
other = sel_setup("cusum-raw-v1:DDD:1", p=0.6, rule="cusum-raw-v1")
check("other rule still picked under disable",
      watcher.shadow_select([ok, other], disabled=("t1-break-v1",)) == {other["id"]})
check("missing p_lb stays silent (selector silent, setup kept)",
      watcher.shadow_select([{"id": "x", "a_tgt": 0.04, "b_stop": 0.02,
                              "c_total": 0.0017, "funding": "ok"}]) == set())

print("\n=== non-suppression: unselected setups stay active ===")
conn = sqlite3.connect(":memory:")
watcher.ensure_watcher_tables(conn)


def db_setup(coin, ts, direction="LONG"):
    b_stop, a_tgt = 0.02, 0.04
    stop, tgt = 100.0 * (1 - b_stop), 100.0 * (1 + a_tgt)
    return {"id": f"t1-break-v1:{coin}:{ts}", "rule_id": "t1-break-v1",
            "coin": coin, "symbol": coin + "USDT", "src": "t1_break",
            "direction": direction, "src_bar_ts": ts, "entry": 100.0,
            "b_stop": b_stop, "a_tgt": a_tgt, "stop_px": stop, "tgt_px": tgt,
            "N_bars": 288, "funding": "ok", "created_at": ts}


ids = [watcher.emit_setup(conn, db_setup(c, T0 + i * 200_000))
       for i, c in enumerate(("AAA", "BBB", "CCC"))]
before = {r[0]: r for r in conn.execute("SELECT * FROM watcher_setup").fetchall()}
cols = [d[0] for d in conn.execute("SELECT * FROM watcher_setup").description]
cands = [sel_setup(ids[0], p=0.8),           # winner
         sel_setup(ids[1], p=0.3),           # below hurdle
         dict(sel_setup(ids[2], p=0.9), funding="unavailable")]  # stale
picked = watcher.shadow_select(cands, K=15, ev_hurdle=0.1)
check("only the funded winner picked", picked == {ids[0]}, str(picked))
ev_map = {s["id"]: watcher.ev_r(s["p_lb"], s["a_tgt"], s["b_stop"], s["c_total"])
          for s in cands if s["funding"] == "ok" and s["p_lb"] is not None}
p_map = {s["id"]: s["p_lb"] for s in cands if s.get("p_lb") is not None}
n = watcher.apply_shadow_flags(conn, picked, ev_by_id=ev_map, p_by_id=p_map)
check("one row flagged", n == 1, str(n))
after = {r[0]: r for r in conn.execute("SELECT * FROM watcher_setup").fetchall()}
states = conn.execute("SELECT id, state, monitoring, shadow_selected "
                      "FROM watcher_setup").fetchall()
check("all three still active",
      all(s == "active" for _, s, _, _ in states), str(states))
check("monitoring untouched (all ok)",
      all(m == "ok" for _, _, m, _ in states), str(states))
flag = {i: f for i, _, _, f in states}
check("flag set only on winner",
      flag == {ids[0]: 1, ids[1]: 0, ids[2]: 0}, str(flag))
shadow_cols = {"shadow_selected", "ev_r", "p_lb"}
for sid in ids:
    b, a = before[sid], after[sid]
    same = all(b[i] == a[i] for i, c in enumerate(cols) if c not in shadow_cols)
    check(f"non-shadow columns byte-identical for {sid.split(':')[1]}", same)
check("no scanner tables created by selector",
      "signal_log" not in {r[0] for r in conn.execute(
          "SELECT name FROM sqlite_master WHERE type='table'").fetchall()})

print("\n=== watcher_report: sections + research labels ===")
rep_conn = sqlite3.connect(":memory:")
watcher.ensure_watcher_tables(rep_conn)
r1 = watcher.emit_setup(rep_conn, db_setup("AAA", T0))
r2 = watcher.emit_setup(rep_conn, db_setup("BBB", T0 + 200_000))
# Resolve one setup so STATES shows a terminal row; leave one active.
ts = T0 + 300
rep_conn.execute("UPDATE watcher_setup SET b_stop=?, a_tgt=?, stop_px=?, tgt_px=?"
                 " WHERE id=?", (0.02, 0.04, 98.0, 104.0, r1))
st, _ = watcher.eval_bar(
    rep_conn, r1, {"ts": ts, "o": 100.0, "h": 105.0, "l": 99.0, "c": 104.5},
    {"now": ts + 300, "bar_ts": ts})
check("fixture resolved_target", st == "resolved_target", st)
watcher.eval_bar(rep_conn, r2, {"ts": ts, "o": 100.0, "h": 101.0, "l": 99.0,
                                "c": 100.5}, {"now": ts + 300, "bar_ts": ts})
states = watcher_report.summarize_states(rep_conn)
check("states count both setups", states["total"] == 2, str(states))
cov = watcher_report.coverage_by_coin_day(rep_conn)
check("coverage rows carry coin/day/ok/unavailable",
      all({"coin", "day", "ok_bars", "unavailable_bars"} <= set(r) for r in cov),
      str(cov))
rows = watcher_report.gross_net_rows(rep_conn)
check("gross/net rows for both setups", len(rows) == 2, str(len(rows)))
check("gross column present", all("ret_gross" in r for r in rows))
check("net column present", all("ret_net" in r for r in rows))
text = watcher_report.format_watcher_report(rep_conn)
for needle in ("WATCHER", "research", "STATES", "COVERAGE",
               "GROSS/NET", "CORRELATED", "ASSUMPTION"):
    check(f"report shows {needle!r}", needle in text, text[:400])
check("report disclaims no edge claim",
      "Unit-test success" in text or "no profitability" in text.lower()
      or "not edge" in text.lower() or "NO profitability" in text
      or "profitability" in text.lower(), text[:400])

print("\n=== watcher_report CLI ===")
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
cli_conn = sqlite3.connect(tmp.name)
watcher.ensure_watcher_tables(cli_conn)
watcher.emit_setup(cli_conn, db_setup("AAA", T0))
cli_conn.commit()
cli_conn.close()
r = subprocess.run([sys.executable, "-m", "proto.watcher_report",
                    "--db", tmp.name],
                   capture_output=True, text=True, timeout=120,
                   cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
check("CLI exits 0", r.returncode == 0, f"rc={r.returncode} err={r.stderr[-500:]}")
check("CLI prints research banner", "WATCHER" in r.stdout and "research" in r.stdout,
      r.stdout[:400])
os.unlink(tmp.name)

print("\n=== gui/model.py display-only watcher helpers ===")
from gui import model as gui_model
check("model exposes watcher_row_text", hasattr(gui_model, "watcher_row_text"))
check("model exposes watcher_panel_lines", hasattr(gui_model, "watcher_panel_lines"))
line = gui_model.watcher_row_text({"id": "t1-break-v1:AAA:1", "coin": "AAA",
                                   "direction": "LONG", "state": "active",
                                   "ev_r": 0.41, "shadow_selected": 1})
check("row text names coin + shadow flag", "AAA" in line and "SHADOW" in line, line)
panel = gui_model.watcher_panel_lines([
    {"id": "t1-break-v1:AAA:1", "coin": "AAA", "direction": "LONG",
     "state": "active", "ev_r": 0.41, "shadow_selected": 1}])
check("panel is research-labeled",
      any("research" in ln.lower() for ln in panel), str(panel[:3]))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
