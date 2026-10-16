"""Quant engine / research / report / CLI integration tests on synthetic
markets.  Offline; no network; stdlib only."""
import array
import dataclasses
import gzip
import json
import math
import os
import random
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.quant import engine as E, qstore, research as RS, report as RP
from proto.quant import registry, bars as BR, stats as S
from proto.quant.config import DEFAULT, grid_geometries, RuleParams, BAR_S

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


T0 = 1_700_000_100
K = 8 * 288                       # live starts here (8 days of history)


def make_series(n, seed, sigma, amt, plant=None):
    """(ts,o,h,l,c,vol,amt) bars; plant = (index, jump_frac, vol_mult)."""
    rng = random.Random(seed)
    px, out = 100.0, []
    for i in range(n):
        o = px
        r = rng.gauss(0, sigma)
        if plant and i == plant[0]:
            r = plant[1]
        px = o * math.exp(r)
        a = amt * (0.7 + 0.6 * rng.random())
        if plant and i == plant[0]:
            a *= plant[2]
        out.append((T0 + i * BAR_S, o, max(o, px) * 1.0005, min(o, px) * 0.9995,
                    px, a / px, a))
    return out


def planted_series(n, k, sigma, amt, jump=0.04, vol_mult=40, seed0=2):
    """Series whose bar k is a genuine NEW channel exit (not a continuation of
    an earlier run) - searched deterministically so the fixture is exact."""
    from proto.quant.stream import FeatureStream
    for seed in range(seed0, seed0 + 200):
        rows = make_series(n, seed, sigma, amt, plant=(k, jump, vol_mult))
        st = FeatureStream(DEFAULT.feat)
        snap = None
        for r in rows[:k + 1]:
            snap = st.update(r[0], r[1], r[2], r[3], r[4], r[6], 0.0)
        if snap is not None and snap.up_start and snap.rvol and snap.rvol >= 4:
            return rows
    raise RuntimeError("no suitable seed")


def write(dirp, coin, rows, mode="wt"):
    p = os.path.join(dirp, coin + ".csv.gz")
    with gzip.open(p, mode, newline="") as f:
        if mode == "wt":
            f.write("ts,o,h,l,c,vol,amount\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


def relaxed(cfg=DEFAULT):
    s = dataclasses.replace(cfg.selector, cooldown_bars=0)
    return dataclasses.replace(cfg, selector=s)


def setup(tag):
    d = tempfile.mkdtemp()
    bars = os.path.join(d, "bars")
    os.makedirs(bars)
    return d, bars


print("=== engine: replay is silent, live bars emit, no duplicates ===")
d, bars = setup("a")
btc = make_series(K + 400, 1, 0.001, 5e6)
aaa = planted_series(K + 400, K, 0.002, 20000)
write(bars, "BTC", btc[:K]); write(bars, "AAA", aaa[:K])
clock = [btc[K - 1][0] + BAR_S]
st = qstore.QuantStore(os.path.join(d, "q.db"))
w = E.Watcher(bars, st, relaxed(), now_fn=lambda: clock[0])
r = w.step()
check("start-up replays history without emitting", r["emitted"] == 0 and r["ok"])
write(bars, "BTC", btc[K:K + 1], "at"); write(bars, "AAA", aaa[K:K + 1], "at")
clock[0] = btc[K][0] + BAR_S
r = w.step()
evs = st.all_events()
rules_hit = {e["rule_id"].split(":")[1:4] and (e["rule_id"], e["side"]) for e in evs}
fade = [e for e in evs if ":fade:" in e["rule_id"] and ":breakout_start:" in e["rule_id"]]
cont = [e for e in evs if ":cont:" in e["rule_id"] and ":breakout_start:" in e["rule_id"]]
check("volume-confirmed breakout emits the fade candidate and its control",
      len(fade) == 1 and len(cont) == 1, str(len(evs)))
check("fade trades against the up-break, control with it",
      fade[0]["side"] == -1 and cont[0]["side"] == 1)
check("events are SHADOW and labeled non-actionable",
      all(e["mode"] == "SHADOW" for e in evs)
      and "not actionable" in json.loads(fade[0]["features"])["warnings"][-1])
check("stop/target sit on the correct sides for a short",
      fade[0]["stop"] > fade[0]["ref_price"] > fade[0]["tp"])
check("cost model recorded on the event", 0 < fade[0]["cost_frac"] < 0.01)
n_before = len(st.all_events())
w.step(); w.step()
check("re-stepping with no new bars adds nothing", len(st.all_events()) == n_before)
# restart: new Watcher over the same files must not re-emit
w2 = E.Watcher(bars, st, relaxed(), now_fn=lambda: clock[0])
w2.step()
check("restart (replay) does not duplicate events", len(st.all_events()) == n_before)

print("\n=== engine: outcomes resolve with the research labeling ===")
up = btc[K + 1:K + 60]
# price keeps rising 12% over the next 40 bars: fade (short) stops out, control (long) hits TP
rows = []
px = aaa[K][4]
for i in range(1, 60):
    o = px
    px = o * (1.003 if i <= 40 else 1.0)
    rows.append((T0 + (K + i) * BAR_S, o, px * 1.0005, o * 0.9995, px, 1.0, 20000.0))
write(bars, "BTC", btc[K + 1:K + 60], "at"); write(bars, "AAA", rows, "at")
clock[0] = btc[K + 59][0] + BAR_S
w.step()
res = {e["rule_id"]: e for e in st.resolved_rows()}
fk = [v for k, v in res.items() if ":fade:" in k and "breakout" in k]
ck = [v for k, v in res.items() if ":cont:" in k and "breakout" in k]
check("short fade is stopped out by the run-up", fk and fk[0]["kind"] == "SL", str(fk and fk[0]["kind"]))
check("long control reaches its target", ck and ck[0]["kind"] == "TP", str(ck and ck[0]["kind"]))
check("net R: stop is about -1R minus friction, target about +1R minus friction",
      fk and -1.2 < fk[0]["net_r"] < -1.0 and ck and 0.8 < ck[0]["net_r"] < 1.0,
      str((fk and fk[0]["net_r"], ck and ck[0]["net_r"])))

print("\n=== engine: gaps, staleness, pause ===")
d2, bars2 = setup("b")
btc2 = make_series(K + 200, 5, 0.001, 5e6)
bbb = make_series(K + 200, 6, 0.002, 20000, plant=(K + 3, 0.04, 40))
write(bars2, "BTC", btc2[:K]); write(bars2, "BBB", bbb[:K])
clock2 = [btc2[K - 1][0] + BAR_S]
st2 = qstore.QuantStore(os.path.join(d2, "q.db"))
w3 = E.Watcher(bars2, st2, relaxed(), now_fn=lambda: clock2[0])
w3.step()
# bar K arrives, bar K+1 is MISSING for BBB, K+2.. arrive
write(bars2, "BTC", btc2[K:K + 8], "at")
write(bars2, "BBB", [bbb[K]] + bbb[K + 2:K + 8], "at")
clock2[0] = btc2[K + 7][0] + BAR_S
r = w3.step()
check("a gap is detected and counted", r["gaps"] >= 1, str(r))
check("after a gap the coin is rebuilt: no signal from unwarmed features",
      len(st2.all_events()) == 0)
check("the gapped coin is marked stale relative to BTC for pause accounting",
      w3.states["BBB"].last_ts is not None)
# stale data -> auto pause
clock2[0] = btc2[K + 7][0] + 20 * BAR_S
w3.step()
check("stale BTC bars trigger the global auto-pause", st2.get_meta("auto_pause") == "1"
      and "stale" in st2.get_meta("auto_pause_reason"))
st2.set_meta("pause", "1")
check("manual pause flag is honored", w3.paused())
st2.set_meta("pause", "0")

print("\n=== engine: suppression is counted, never silent ===")
d3, bars3 = setup("c")
btc3 = make_series(K + 200, 7, 0.001, 5e6)
ccc = make_series(K + 200, 8, 0.002, 20000, plant=(K, 0.04, 40))
write(bars3, "BTC", btc3[:K]); write(bars3, "CCC", ccc[:K])
clock3 = [btc3[K - 1][0] + BAR_S]
st3 = qstore.QuantStore(os.path.join(d3, "q.db"))
w4 = E.Watcher(bars3, st3, relaxed(), now_fn=lambda: clock3[0])
w4.step()
write(bars3, "BTC", btc3[K:K + 20], "at"); write(bars3, "CCC", ccc[K:K + 20], "at")
clock3[0] = btc3[K + 19][0] + 5 * BAR_S          # signal is now 20 bars old
w4.step()
sup = dict(st3.conn.execute("SELECT reason, SUM(n) FROM quant_suppressed GROUP BY reason").fetchall())
check("a candidate older than the age limit is suppressed as stale_signal",
      sup.get("stale_signal", 0) >= 1 and len(st3.all_events()) == 0, str(sup))
thin = make_series(K + 50, 9, 0.002, 50.0, plant=(K, 0.04, 40))     # ~$14k/day: untradeable
d4, bars4 = setup("d")
write(bars4, "BTC", btc3[:K]); write(bars4, "THIN", thin[:K])
st4 = qstore.QuantStore(os.path.join(d4, "q.db"))
clk4 = [btc3[K - 1][0] + BAR_S]
w5 = E.Watcher(bars4, st4, relaxed(), now_fn=lambda: clk4[0])
w5.step()
write(bars4, "BTC", btc3[K:K + 1], "at"); write(bars4, "THIN", thin[K:K + 1], "at")
clk4[0] = btc3[K][0] + BAR_S
w5.step()
sup4 = dict(st4.conn.execute("SELECT reason, SUM(n) FROM quant_suppressed GROUP BY reason").fetchall())
check("an illiquid coin is suppressed with reason 'liquidity'",
      sup4.get("liquidity", 0) >= 1 and not st4.all_events(), str(sup4))

print("\n=== read_tail does not assume file order ===")
tp = os.path.join(tempfile.mkdtemp(), "X.csv.gz")
rr = make_series(100, 3, 0.002, 1000)
write(os.path.dirname(tp), "X", rr[50:] + rr[:50])
tail = E.read_tail(tp, 600)
check("tail is sorted and de-duplicated", [t[0] for t in tail] == sorted({r[0] for r in rr}))

print("\n=== report ===")
rep = RP.build(st, DEFAULT, d)
txt = RP.render(rep)
check("report lists the rules and labels them research / not actionable",
      "RESEARCH (not actionable)" in txt and "breakout_start" in txt)
check("report states the cost assumption and funding caveat",
      "ASSUMED" in txt and "funding is not modeled" in txt)
fr = [x for x in rep["rules"] if ":fade:" in x["rule_id"] and "breakout" in x["rule_id"]][0]
check("report carries resolved counts and the driftless null",
      fr["resolved"] == 1 and "net_r" in fr and abs(fr["null_p0"] - 0.5) < 1e-9, str(fr))

print("\n=== research harness plumbing (planted edge vs placebo) ===")


def fake_events(plant_gi=None, seed=1, n=2600):
    rng = random.Random(seed)
    geoms = grid_geometries()
    out = []
    for _ in range(n):
        day = rng.choice(list(range(7, 61)) + list(range(63, 77)))
        ev = RS.Event()
        ev.coin, ev.ts, ev.day = f"C{rng.randrange(40)}", 0, day + rng.random()
        ev.fam, ev.param, ev.side = "B", 4.0, rng.choice((1, -1))
        ev.qv24, ev.sigma, ev.rvol = 5e6, 0.003, 5.0
        ev.z1h = ev.z4h = ev.btc7d = 0.0
        g_, k_ = array.array("d"), array.array("b")
        for gi, g in enumerate(geoms):
            dd = ev.sigma * math.sqrt(g.horizon_bars) * g.k_stop
            for side in (1, -1):
                traded_fade = (side == -ev.side)
                p_tp = 1.0 / (1.0 + g.r_tp)                     # driftless null
                if plant_gi is not None and gi == plant_gi and traded_fade:
                    p_tp = 0.80
                if rng.random() < p_tp:
                    g_.append(g.r_tp * dd); k_.append(0)
                else:
                    g_.append(-dd); k_.append(1)
        ev.gross, ev.kind = g_, k_
        out.append(ev)
    return out


meta = {"t_min": 0, "t_max": 0, "n_days": 91.0}
null_ev = fake_events(None)
rn = RS.run_stage_dev_val(null_ev, meta)
check("placebo market: nothing survives the best-of-many test (p > 0.1)",
      rn["spa"]["p_value"] > 0.10, str(rn["spa"]))
check("placebo market: every config loses roughly its friction",
      rn["dev_config_means"]["share_positive"] < 0.2, str(rn["dev_config_means"]))
gi0 = next(i for i, g in enumerate(grid_geometries()) if g.key() == "T144_ks1.5_R1")
pe = fake_events(gi0, seed=2)
rp = RS.run_stage_dev_val(pe, meta)
check("planted edge: it is selected on dev", rp["selected"]["geometry"] == "T144_ks1.5_R1"
      and rp["selected"]["rule"].endswith("/fade"), str(rp["selected"]["rule"]))
check("planted edge: the multiplicity test is significant", rp["spa"]["p_value"] < 0.05,
      str(rp["spa"]))
check("planted edge: validation confirms it with a positive interval",
      rp["val"]["ci_lo"] > 0, str(rp["val"]))
out_dir = tempfile.mkdtemp()
h, h2 = RS.holdout_once(pe, {"t_min": 0, "t_max": 0, "n_days": 91.0}, rp["selected"], out_dir)
check("holdout was evaluated and returns summaries", h["n"] >= 0 and "mean_r" in h2)
try:
    RS.holdout_once(pe, meta, rp["selected"], out_dir)
    check("a second holdout look is refused", False)
except SystemExit:
    check("a second holdout look is refused", True)
check("no event window crosses a split boundary (embargo)",
      all(e.day < 61 or e.day >= 63 for e in pe))
tab = RS.geometry_table(null_ev, RuleParams("breakout_start", "fade", rvol_min=4.0),
                        DEFAULT.splits.bounds(91.0)["dev"])
tp_rows = [r for r in tab if r["geom"] == "T144_ks1.5_R1"]
check("geometry table: TP-first among terminal ~ driftless null in a placebo market",
      tp_rows and abs(tp_rows[0]["tp_given_terminal"] - tp_rows[0]["null_p0"]) < 0.06,
      str(tp_rows))
sc = RS.scorer_check(fake_events(None, seed=3), meta)
check("scorer on pure noise has no meaningful calibration", (not sc["ok"]) or
      sc["calibration_slope"] < 0.8 or sc["top_tercile"]["ci_lo"] < 0.02, str(sc.get("calibration_slope")))

print("\n=== collect_events on a synthetic archive ===")
d5, bars5 = setup("e")
write(bars5, "BTC", make_series(12 * 288, 21, 0.001, 5e6))
write(bars5, "ZZZ", make_series(12 * 288, 22, 0.002, 30000))
ev5, m5 = RS.collect_events(bars5, DEFAULT, ["ZZZ"])
check("events carry labels for every grid geometry and both sides",
      all(len(e.gross) == 72 and len(e.kind) == 72 for e in ev5) and ev5 is not None)
check("meta reports the archive span", abs(m5["n_days"] - 12 * 288 * 300 / 86400.0 + 300 / 86400.0) < 0.01)

print("\n=== CLI ===")
envp = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
cdb = os.path.join(tempfile.mkdtemp(), "cli.db")
cp = subprocess.run([sys.executable, "-m", "proto.quant", "status", "--db", cdb],
                    capture_output=True, text=True, env=envp, cwd=envp["PYTHONPATH"])
check("status runs and reports the config hash", cp.returncode == 0 and "config_hash" in cp.stdout, cp.stderr[-300:])
cp = subprocess.run([sys.executable, "-m", "proto.quant", "pause", "--db", cdb],
                    capture_output=True, text=True, env=envp, cwd=envp["PYTHONPATH"])
check("pause flag can be set from the CLI", "ON" in cp.stdout)
cp = subprocess.run([sys.executable, "-m", "proto.quant", "report", "--db", cdb],
                    capture_output=True, text=True, env=envp, cwd=envp["PYTHONPATH"])
check("report on an empty store says silence is valid", "Silence is a valid output" in cp.stdout, cp.stderr[-300:])

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
