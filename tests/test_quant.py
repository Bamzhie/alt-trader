"""Quant module unit tests: config, bars, stream, barrier math, stats, model,
selector, gates, risk, store.  Offline, stdlib only."""
import gzip
import math
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.quant import barrier as B, stats as S, model as M, risk as R
from proto.quant import bars as BR, gates as G, qstore, registry
from proto.quant.config import (DEFAULT, Geometry, RuleParams, QuantConfig,
                                grid_geometries, CostModel, Splits)
from proto.quant.selector import Selector
from proto.quant.stream import FeatureStream, Cusum

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def walk(n, seed=1, sigma=0.002, amt=20000.0, t0=1_700_000_100):
    rng = random.Random(seed)
    px, out = 100.0, []
    for i in range(n):
        o = px
        px *= math.exp(rng.gauss(0, sigma))
        c = px
        h = max(o, c) * (1 + abs(rng.gauss(0, sigma / 3)))
        l = min(o, c) * (1 - abs(rng.gauss(0, sigma / 3)))
        out.append((t0 + i * 300, o, h, l, c, amt * (0.7 + 0.6 * rng.random())))
    return out


print("=== config ===")
check("config hash is stable", DEFAULT.config_hash() == QuantConfig().config_hash())
check("changing a cost changes the hash",
      QuantConfig(cost=CostModel(fee_per_side=0.0006)).config_hash()
      != DEFAULT.config_hash())
check("grid is the pre-declared 36 geometries", len(grid_geometries()) == 36)
cm = DEFAULT.cost
check("tiered slippage: liquid cheaper than thin",
      cm.round_trip(50e6) < cm.round_trip(5e6) < cm.round_trip(1.5e6))
check("below the liquidity floor there is no cost model (untradeable)",
      cm.round_trip(5e5) is None)
check("fee/slip multipliers apply", cm.round_trip(5e6, slip_mult=0) == 2 * cm.fee_per_side)
b = Splits().bounds(91.4)
check("splits are ordered with embargo >= 1 day",
      b["dev"][1] + 1 <= b["val"][0] and b["val"][1] + 1 <= b["holdout"][0])

print("\n=== bars: order-independent loading ===")
tmp = tempfile.mkdtemp()
rows = walk(50)
with gzip.open(os.path.join(tmp, "AAA.csv.gz"), "wt", newline="") as f:
    f.write("ts,o,h,l,c,vol,amount\n")
    for r in rows[25:]:                                   # newer first ...
        f.write(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]},{r[5]}\n")
    for r in rows[:25] + rows[30:35]:                     # ... backfill + dups after
        f.write(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]},{r[5]}\n")
bb = BR.load_coin(tmp, "AAA")
check("unsorted file loads sorted", bb["ts"] == sorted(bb["ts"]))
check("duplicates removed", len(bb["ts"]) == 50)
check("contiguous after sort", BR.contiguous_runs(bb["ts"]) == [(0, 50)])
check("gap splits runs", BR.contiguous_runs([0, 300, 600, 1500, 1800]) == [(0, 3), (3, 5)])
check("missing file -> None", BR.load_coin(tmp, "ZZZ") is None)
check("valid_bar rejects h<l and non-positive", not BR.valid_bar(1, 0.9, 1.1, 1)
      and not BR.valid_bar(0, 1, 1, 1) and BR.valid_bar(1, 1.1, 0.9, 1.05))

print("\n=== stream: causality, warm-up, flags ===")
W = DEFAULT.feat.warmup_bars
N = W + 400
base = walk(N, seed=3)


def run(series):
    st, out = FeatureStream(DEFAULT.feat), []
    for ts, o, h, l, c, a in series:
        out.append(st.update(ts, o, h, l, c, a, 0.0))
    return out


snaps = run(base)
check("no features before warm-up", all(s is None for s in snaps[:W]) and snaps[W] is not None)
cut = W + 200
alt = base[:cut + 1] + walk(N - cut - 1, seed=99, sigma=0.01)  # different future
alt = [(base[i][0], *alt[i][1:]) for i in range(N)]
s2 = run(alt)
same = all(
    (snaps[i].sigma, snaps[i].z, snaps[i].rvol, snaps[i].qv24, snaps[i].up_start)
    == (s2[i].sigma, s2[i].z, s2[i].rvol, s2[i].qv24, s2[i].up_start)
    for i in range(W, cut + 1))
check("features at bar t do not depend on later bars (causal)", same)
check("rerun is deterministic", all(
    a.sigma == b.sigma for a, b in zip(snaps[W:], run(base)[W:])))
# planted breakout with volume
pl = list(base[:W + 100])
last = pl[-1]
jump = last[4] * 1.04
pl.append((last[0] + 300, last[4], jump * 1.001, last[4], jump, 20000 * 30))
pl.append((last[0] + 600, jump, jump * 1.01, jump * 0.999, jump * 1.008, 20000 * 5))
ss = run(pl)
check("first bar of a channel exit is flagged up_start with age 0",
      ss[-2].up_start and ss[-2].up_age == 0, str((ss[-2].up_start, ss[-2].up_age)))
check("the next bar outside the channel is not a new start; age counts from the run start",
      (not ss[-1].up_start) and ss[-1].up_age == 1)
check("volume spike gives time-of-day RVOL well above 1", ss[-2].rvol and ss[-2].rvol > 5,
      str(ss[-2].rvol))
check("down flags stay clear", not ss[-2].dn_start)
try:
    FeatureStream(DEFAULT.feat).update(1, 1.0, 0.9, 1.1, 1.0, 1.0, 0.0)
    check("invalid bar raises", False)
except ValueError:
    check("invalid bar raises", True)
cu = Cusum(h=3.0, k=0.5, cooldown=2)
evs = [cu.update(i, 1.2) for i in range(10)]
check("cusum fires on persistent positive drift then resets", 1 in evs and evs.count(1) <= 3)
check("cusum silent on zero input", all(Cusum(3.0).update(i, 0.0) == 0 for i in range(50)))

print("\n=== barrier math (closed forms vs Monte Carlo) ===")
check("null hit prob", abs(B.null_hit_prob(2, 1) - 1 / 3) < 1e-12)
check("breakeven prob with cost", abs(B.breakeven_hit_prob(2, 1, 0.2) - 0.4) < 1e-12)


def mc(a, b, mu, sig, n=3000, dt=1 / 400, seed=2):
    rng = random.Random(seed)
    wins = tot = 0
    sd = sig * math.sqrt(dt)
    for _ in range(n):
        x = 0.0
        for _ in range(int(40 / dt)):
            x += mu * dt + sd * rng.gauss(0, 1)
            if x >= a:
                wins += 1; tot += 1; break
            if x <= -b:
                tot += 1; break
    return wins / tot


for a, b_, mu in ((2, 1, 0.0), (2, 1, 0.3), (1, 2, -0.2)):
    f = B.p_tp_first(a, b_, mu, 1.0)
    m = mc(a, b_, mu, 1.0)
    check(f"P(TP first) a={a} b={b_} mu={mu:+.1f}: formula {f:.3f} ~ MC {m:.3f}",
          abs(f - m) < 0.04)
check("noise-only touch probability 2*Phi(-x)",
      abs(B.noise_touch_prob(3, 1, 9) - 2 * B.Phi(-1.0)) < 1e-12)
check("friction in R", abs(B.friction_r(0.002, 0.01) - 0.2) < 1e-12)
rng = random.Random(5)
bad = tot = 0
for _ in range(60):
    px = 100.0
    o, h, l, c = [], [], [], []
    for _ in range(420):
        op = px
        px *= math.exp(rng.gauss(0, 0.004))
        o.append(op); c.append(px)
        h.append(max(op, px) * (1 + abs(rng.gauss(0, 0.002))))
        l.append(min(op, px) * (1 - abs(rng.gauss(0, 0.002))))
    i = rng.randrange(0, 80)
    path = B.make_path(o, h, l, c, i, 288)
    for T in (48, 144, 288):
        for side in (1, -1):
            for dd in (0.004, 0.012, 0.04):
                for r in (1.0, 3.0):
                    x = B.label_path(path, side, dd, T, r)
                    y = B.label_reference(o, h, l, c, i, side, dd, T, r)
                    bars_ = [(k, o[k], h[k], l[k], c[k]) for k in range(i + 1, i + 1 + T)]
                    z = B.label_stream(bars_, side, dd, T, r)
                    tot += 1
                    if x[1] != y[1] or x[2] != y[2] or abs(x[0] - y[0]) > 1e-12 \
                            or z is None or z[1] != y[1] or z[2] != y[2]:
                        bad += 1
check(f"fast, reference and streaming labelers agree ({tot} cases)", bad == 0, f"bad={bad}")
# stop-first on a bar that touches both
o_ = [100, 100, 100]; h_ = [100, 111, 100]; l_ = [100, 89, 100]; c_ = [100, 100, 100]
check("same-bar stop/target tie resolves to the STOP",
      B.label_reference(o_, h_, l_, c_, 0, 1, 0.10, 2, 1.0)[1] == "SL")
check("stream labeler stays pending without a touch and too few bars",
      B.label_stream([(0, 100, 100.1, 99.9, 100)], 1, 0.05, 10, 1.0) is None)
check("net R subtracts the round-trip cost in stop units",
      abs(B.net_r(0.02, 0.01, 0.002) - 1.8) < 1e-12)
check("plan levels: stop below / target above for a long",
      (lambda p: p["stop"] < 100 < p["tp"])(R.plan_levels(100, 1, 0.003, Geometry(144, 2, 2))))

print("\n=== stats ===")
rng = random.Random(11)
vals = [rng.gauss(0.2, 1.0) for _ in range(1500)]
days = [i // 15 for i in range(1500)]
m, lo, hi, nd = S.day_cluster_mean(vals, days, B=800)
check("day-cluster CI covers the true mean", lo < 0.2 < hi, f"{lo:.3f},{hi:.3f}")
# strongly day-correlated noise: CI must be wider than the naive one
shock = {d: rng.gauss(0, 1) for d in range(100)}
cv = [shock[i // 15] + rng.gauss(0, 0.3) for i in range(1500)]
m2, lo2, hi2, _ = S.day_cluster_mean(cv, days, B=800)
naive = 1.96 * (sum((x - m2) ** 2 for x in cv) / 1499) ** 0.5 / (1500 ** 0.5)
check("clustering widens the interval vs naive row-level", (hi2 - lo2) / 2 > 2 * naive,
      f"{(hi2 - lo2) / 2:.3f} vs {naive:.3f}")
# multi-day dependence: a random walk over days, so consecutive days share
# their shocks.  A one-day cluster treats them as independent.
walk = [0.0]
for d in range(1, 100):
    walk.append(walk[-1] + rng.gauss(0, 1))
mv = [walk[i // 15] + rng.gauss(0, 0.1) for i in range(1500)]
_mw, wlo1, whi1, _ = S.day_cluster_mean(mv, days, B=800)
_w3, wlo3, whi3, _ = S.block_cluster_mean(mv, days, block=3, B=800)
check("the block bootstrap is wider than the day bootstrap under multi-day dependence",
      (whi3 - wlo3) > (whi1 - wlo1), f"{whi3 - wlo3:.3f} vs {whi1 - wlo1:.3f}")
check("the block interval widens as the block grows",
      S.block_cluster_mean(mv, days, block=7, B=800)[2]
      - S.block_cluster_mean(mv, days, block=7, B=800)[1]
      > (whi3 - wlo3))
_b1 = S.block_cluster_mean(mv, days, block=1, B=800)
check("a one-day block reproduces the day-cluster interval exactly",
      abs(_b1[1] - wlo1) < 1e-12 and abs(_b1[2] - whi1) < 1e-12,
      f"{_b1[1]},{_b1[2]} vs {wlo1},{whi1}")
check("the block bootstrap is deterministic",
      S.block_cluster_mean(vals, days, block=3, B=200, seed=5)
      == S.block_cluster_mean(vals, days, block=3, B=200, seed=5))
_st, slo, shi, snd = S.block_cluster_mean(vals, days, block=3, B=800)
check("the block interval covers the true mean on stationary data",
      slo < 0.2 < shi, f"{slo:.3f},{shi:.3f}")
check("a block longer than the series is clamped, not an error",
      S.block_cluster_mean(vals[:10], days[:10], block=99, B=50)[3] == 1)
aj = S.aalen_johansen([1, 2, 3, 4, 5, 6], [1, 2, 0, 1, 0, 2], 2, 10)
check("Aalen-Johansen matches the hand calculation",
      abs(aj[1][-1][1] - 7 / 18) < 1e-9 and abs(aj[2][-1][1] - 11 / 18) < 1e-9)
ep = S.EProcess(0.3)
for _ in range(60):
    ep.update(1 if rng.random() < 0.6 else 0)
check("e-process grows under a true edge", ep.e_value() > 20, str(ep.e_value()))
ep0 = S.EProcess(0.3)
mx = 0
for _ in range(300):
    ep0.update(1 if rng.random() < 0.3 else 0)
    mx = max(mx, ep0.e_value())
check("e-process stays small under the null", mx < 20, str(mx))
nv, nd_ = [], []
for k in range(20):
    r_ = random.Random(100 + k)
    nv.append([r_.gauss(0, 1) for _ in range(300)])
    nd_.append([i // 6 for i in range(300)])
_, _, p_null = S.spa_max_t(nv, nd_, B=300)
check("max-t on 20 pure-noise configs: not significant", p_null > 0.1, f"p={p_null:.3f}")
nv2 = [v[:] for v in nv]
nv2[3] = [x + 0.6 for x in nv2[3]]
_, t_, p_edge = S.spa_max_t(nv2, nd_, B=300)
check("max-t detects one planted edge among 20", p_edge < 0.05, f"p={p_edge:.3f}")
check("calibration slope ~1 for calibrated predictions",
      abs(S.calibration_slope([0.1 * i for i in range(10)], [0.1 * i for i in range(10)]) - 1) < 1e-9)

print("\n=== model ===")
rr = random.Random(4)
X = [[rr.gauss(0, 1) for _ in range(3)] for _ in range(800)]
y = [0.5 * a - 0.2 * c + rr.gauss(0, 0.3) for a, _, c in X]
rg = M.Ridge(5.0).fit(X, y)
check("ridge recovers the signal weights", abs(rg.w[0] - 0.5) < 0.1 and abs(rg.w[2] + 0.2) < 0.1,
      str(rg.w))
pt = rg.predict(X[:3])
check("ridge predicts finite numbers", all(math.isfinite(v) for v in pt))
tb = M.bucket_table(rg.predict(X), y, 4)
check("realized mean rises with predicted bucket", tb[0][1] < tb[-1][1])

print("\n=== risk ===")
sz = R.size_position(1000.0, 0.02, DEFAULT.risk)
check("5% risk with a 2% stop -> 2.5x notional", abs(sz["notional"] - 2500) < 1e-6
      and abs(sz["risk_frac_eff"] - 0.05) < 1e-9)
cz = R.size_position(1000.0, 0.002, DEFAULT.risk)
check("a very tight stop is leverage-capped and says so",
      cz["capped"] and cz["risk_frac_eff"] < 0.05 and cz["warnings"])
rs = random.Random(8)
f1 = [rs.gauss(0, 1) for _ in range(100)]
cl = R.correlation_clusters({"A": f1, "B": [x + rs.gauss(0, 0.1) for x in f1],
                             "C": [rs.gauss(0, 1) for _ in range(100)], "D": [1.0] * 5})
check("correlated coins share a cluster, independent ones do not",
      cl["A"] == cl["B"] and cl["A"] != cl["C"])
check("thin history gets its own cluster (never merged by guess)", cl["D"] not in (cl["A"], cl["C"]))
ex = R.Exposure(DEFAULT.risk)
ex.add(1, 0, 0.05); ex.add(2, 0, 0.05); ex.add(3, 0, 0.05)
check("cluster cap blocks a 4th open signal", not ex.can_add(0, 0.05)[0])
ex2 = R.Exposure(DEFAULT.risk)
for i in range(4):
    ex2.add(i, i, 0.05)
check("aggregate open-risk cap blocks beyond 20%", not ex2.can_add(9, 0.05)[0])

print("\n=== store ===")
qs = qstore.QuantStore(os.path.join(tmp, "q.db"))
ev = dict(rule_id="r1", config_hash="h", coin="X", side=1, bar_ts=1000, mode="SHADOW",
          role="candidate", ref_price=1.0, stop=0.9, tp=1.1, stop_frac=0.1, horizon=10,
          r_tp=1.0, cost_frac=0.002, sigma=0.003, qv24=5e6, features={"a": 1})
i1 = qs.insert_event(ev)
check("event insert returns an id", i1 is not None)
check("duplicate (rule, coin, bar) is rejected", qs.insert_event(ev) is None)
qs.resolve(i1, "TP", 1.0, 1300, 1, 0.1, 0.9)
check("resolve closes the event", not qs.open_events() and qs.resolved_rows("r1")[0]["kind"] == "TP")
qs.bump_suppressed("2026-10-12", "r1", "cooldown", 2)
qs.bump_suppressed("2026-10-12", "r1", "cooldown", 3)
check("suppressed counters aggregate", qs.conn.execute(
    "SELECT n FROM quant_suppressed").fetchone()[0] == 5)

print("\n=== selector gates ===")
cfg = DEFAULT
qs2 = qstore.QuantStore(os.path.join(tmp, "q2.db"))
spec = registry.REGISTERED[0]
sel = Selector(cfg, qs2, R.Exposure(cfg.risk))
T = 1_700_000_000 - (1_700_000_000 % 86400) + 3600
ok, why, mode, cost = sel.decide(spec, "X", T, 5e6, 0.05)
check("a liquid, cost-feasible candidate is emitted as SHADOW", ok and mode == "SHADOW")
check("below the liquidity floor is suppressed", sel.decide(spec, "X", T, 5e5, 0.05)[1] == "liquidity")
check("friction above 0.10R is suppressed", sel.decide(spec, "X", T, 5e6, 0.01)[1] == "cost_r")
e1 = dict(ev, rule_id=spec.rule_id, coin="X", bar_ts=T)
qs2.insert_event(e1)
check("cooldown suppresses a repeat", sel.decide(spec, "X", T + 600, 5e6, 0.05)[1] == "cooldown")
e2 = dict(ev, rule_id=spec.rule_id, coin="Y", bar_ts=T)
qs2.insert_event(e2)
check("another coin is unaffected", sel.decide(spec, "Z", T + 600, 5e6, 0.05)[0])
qs2.set_meta(f"disable:{spec.rule_id}", "1")
check("per-rule disable switch works", sel.decide(spec, "Q", T, 5e6, 0.05)[1] == "rule_disabled")
qs2.set_meta(f"disable:{spec.rule_id}", "0")
promo = {"expected_net_r": 0.05}
check("promoted rule below the EV hurdle is suppressed",
      sel.decide(spec, "Q", T, 5e6, 0.05, promotion=promo)[1] == "ev_below_hurdle")
good = {"expected_net_r": 0.2}
ok, why, mode, _ = sel.decide(spec, "Q", T, 5e6, 0.05, promotion=good, cluster=1, risk_frac_eff=0.05)
check("promoted rule above the hurdle is ACTIONABLE", ok and mode == "ACTIONABLE")

print("\n=== promotion gates ===")
good_h = {"n": 400, "days": 40, "coins": 30, "ci_lo": 0.03, "ci_hi": 0.2, "mean_r": 0.1}
ok, why = G.evaluate(good_h, {"mean_r": 0.05}, 0.04, {"mean_r": 0.04})
check("all conditions met -> pass", ok, str(why))
check("no holdout -> fail", not G.evaluate(None, {"mean_r": 1}, 0.0, {"mean_r": 1})[0])
check("CI lower bound <= 0 -> fail", not G.evaluate(dict(good_h, ci_lo=-0.01), {"mean_r": 0.05}, 0.04, {"mean_r": 0.04})[0])
check("too few days -> fail", not G.evaluate(dict(good_h, days=12), {"mean_r": 0.05}, 0.04, {"mean_r": 0.04})[0])
check("validation not positive -> fail", not G.evaluate(good_h, {"mean_r": -0.01}, 0.04, {"mean_r": 0.04})[0])
check("multiplicity p too high -> fail", not G.evaluate(good_h, {"mean_r": 0.05}, 0.5, {"mean_r": 0.04})[0])
check("fails when slippage doubled -> fail", not G.evaluate(good_h, {"mean_r": 0.05}, 0.04, {"mean_r": -0.01})[0])
G.record(tmp, "rid", "hash", True, [], good_h)
G.record(tmp, "rid2", "hash", False, ["x"], good_h)
check("promotion record round-trips; only passes are returned",
      G.promotion_for(tmp, "rid", "hash")["expected_net_r"] == 0.03
      and G.promotion_for(tmp, "rid2", "hash") is None
      and G.promotion_for(tmp, "rid", "other") is None)
check("no rule is promoted by default", all(
    G.promotion_for(tmp, r.rule_id, DEFAULT.config_hash()) is None for r in registry.REGISTERED))
check("registered ids are unique and include controls",
      len({r.rule_id for r in registry.REGISTERED}) == len(registry.REGISTERED)
      and any(r.role == "control" for r in registry.REGISTERED))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
