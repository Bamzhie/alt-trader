"""ROE feasibility calculator: identities, fairness between sides, base rates
on synthetic markets, the event block in the watcher, and the CLI.

Read-only: this suite writes only into temporary directories.
"""
import ast
import gzip
import json
import math
import os
import random
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from proto.quant import bars as BR, engine as E, qstore, roe
from proto.quant.config import DEFAULT, BAR_S, BARS_PER_DAY

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


print("=== ROE identities ===")
for L in (5, 10, 20, 50):
    for c in (0.0, 0.0022, 0.004):
        m = roe.required_move(1.0, L, c)
        check(f"required move round-trips to +100% (L={L}, cost={c})",
              abs(roe.roe_from_move(m, L, c) - 1.0) < 1e-12)
p = roe.target_plan(0.5, 20, 0.0022)
check("net win/loss on margin are exactly +100% / -50% at the plan's levels",
      abs(20 * (p["target_move"] - 0.0022) - 1.0) < 1e-12
      and abs(20 * (p["stop_move"] + 0.0022) - 0.5) < 1e-12)
check("breakeven hit probability = loss / (win + loss) in ROE terms",
      abs(p["p_breakeven"] - 0.5 / 1.5) < 1e-12, str(p["p_breakeven"]))
check("driftless null is below the cost-adjusted breakeven",
      p["p_null"] < p["p_breakeven"])
check("max leverage for a stop falls as the stop widens",
      roe.max_leverage_for_stop(0.01) > roe.max_leverage_for_stop(0.03)
      > roe.max_leverage_for_stop(0.08))
lm = roe.max_leverage_for_stop(0.025)
check("at that leverage the stop is exactly inside the buffered liquidation distance",
      abs(0.025 - 0.8 * roe.liquidation_distance(lm)) < 1e-9)
try:
    roe.target_plan(0.001, 20, 0.0022)
    check("stop inside the round-trip cost is rejected", False)
except ValueError:
    check("stop inside the round-trip cost is rejected", True)

for bad, fn in (
        ("leverage 0", lambda: roe.required_move(1.0, 0, 0.0)),
        ("negative leverage", lambda: roe.required_move(1.0, -5, 0.0)),
        ("zero target", lambda: roe.target_plan(0.5, 20, 0.0022, target_roe=0.0)),
        ("zero stride", lambda: roe.empirical_rates(
            {"ts": [], "o": [], "h": [], "l": [], "c": [], "amt": []},
            {"target_move": 0.05, "stop_move": 0.025, "leverage": 20}, 0.0, stride=0)),
        ("zero target in the event block",
         lambda: roe.event_block(0.05, 0.02, 0.0022, target_roe=0.0))):
    try:
        fn()
        check(f"the library refuses {bad}", False)
    except ValueError:
        check(f"the library refuses {bad}", True)
    except ZeroDivisionError:
        check(f"the library refuses {bad}", False, "raised ZeroDivisionError")

print("\n=== event block: what +100% needs for an event's own levels ===")
blk = roe.event_block(0.079, 0.0735, 0.0022)
check("wide-stop geometry: ~13x needed, stop costs ~99% of margin, not inside liquidation",
      12 < blk["leverage_needed"] < 14 and blk["roe_at_stop"] < -0.95
      and not blk["liq_ok"] and blk["warnings"], str(blk))
tight = roe.event_block(0.052, 0.0227, 0.0022)
check("a 2.3% stop / 5.2% target geometry is livable at ~20x",
      tight["liq_ok"] and abs(tight["leverage_needed"] - 20) < 1.5
      and -0.6 < tight["roe_at_stop"] < -0.4, str(tight))
check("a target that does not clear costs is reported infeasible",
      roe.event_block(0.001, 0.01, 0.0022)["feasible"] is False)
check("a missing cost is treated as zero, not as an error",
      roe.event_block(0.05, 0.02, None)["feasible"] is True)
check("the block reports the liquidation headroom explicitly",
      "liq_distance" in blk and blk["liq_distance"] > 0, str(blk))

print("\n=== base rates on synthetic markets ===")
T0 = 1_700_000_100


def series(n, seed, sigma, drift=0.0, amt=50000.0):
    rng = random.Random(seed)
    px = 100.0
    o_, h_, l_, c_, a_, t_ = [], [], [], [], [], []
    for i in range(n):
        op = px
        px *= math.exp(drift + rng.gauss(0, sigma))
        o_.append(op); c_.append(px)
        h_.append(max(op, px) * 1.0004); l_.append(min(op, px) * 0.9996)
        a_.append(amt); t_.append(T0 + i * BAR_S)
    return {"ts": t_, "o": o_, "h": h_, "l": l_, "c": c_,
            "vol": a_, "amt": a_}


def gapped(n, seed, gap_at, gap_len, **kw):
    """The same price path with `gap_len` bars punched out (a real time gap)."""
    b = series(n, seed, **kw)
    keep = [i for i in range(n) if not (gap_at <= i < gap_at + gap_len)]
    return {k: [v[i] for i in keep] for k, v in b.items()}


def expected_entries(ts, horizon, stride):
    """The sampling rule stated independently of the implementation: an entry
    needs a full 24 h of IN-RUN history and a full horizon inside its own
    contiguous run, on the global stride grid."""
    n_ok = 0
    for (a, z) in BR.contiguous_runs(ts):
        for i in range(a + BARS_PER_DAY, z - horizon):
            if (i - BARS_PER_DAY) % stride == 0:
                n_ok += 1
    return n_ok


N = 40 * BARS_PER_DAY
plan = roe.target_plan(0.5, 20, 0.0)
rw = series(N, 1, 0.0035)
r = roe.empirical_rates(rw, plan, 0.0, stride=6)
s = roe.summarize_rates(r, plan)
check("counts add up (TP + SL + neither = samples)", r["tp"] + r["sl"] + r["time"] == r["n"])
tot = {1: 0, -1: 0}
roes = []
for sd in range(21, 29):                       # several markets: overlapping paths
    rr_ = roe.empirical_rates(series(N, sd, 0.0035), plan, 0.0, stride=6)
    tot[1] += rr_["by_side"][1][0]; tot[-1] += rr_["by_side"][-1][0]
    roes.append(sum(rr_["roe"]) / len(rr_["roe"]))
check("driftless markets: long and short TP counts agree within 10% in aggregate",
      abs(tot[1] - tot[-1]) <= 0.10 * max(tot[1], tot[-1]), str(tot))
mean_roe = sum(roes) / len(roes)
check("driftless markets, zero cost: mean ROE per trade is ~0 (|x| < 4%)",
      abs(mean_roe) < 0.04, str(mean_roe))
up = series(N, 2, 0.0035, drift=0.0004)
ru = roe.empirical_rates(up, plan, 0.0, stride=6)
check("an uptrend favors longs: long TP share > short TP share",
      ru["by_side"][1][0] > 2 * ru["by_side"][-1][0], str(ru["by_side"]))
check("an uptrend has positive average ROE for longs, negative for shorts",
      (ru["by_side"][1][0] - ru["by_side"][1][1]) > 0
      and (ru["by_side"][-1][0] - ru["by_side"][-1][1]) < 0)
costly = roe.empirical_rates(rw, roe.target_plan(0.5, 20, 0.0022), 0.0022, stride=6)
free = roe.empirical_rates(rw, roe.target_plan(0.5, 20, 0.0), 0.0, stride=6)
check("costs reduce average ROE by about leverage x cost (4.4%)",
      0.02 < (sum(free["roe"]) / len(free["roe"])) - (sum(costly["roe"]) / len(costly["roe"])) < 0.07)
check("short target is worth exactly +a in simple return (no log-asymmetry)",
      abs(roe.required_move(1.0, 20, 0) - 0.05) < 1e-12)
thin = series(N, 3, 0.0035, amt=100.0)
check("illiquid coin contributes no samples (liquidity gate)",
      roe.empirical_rates(thin, plan, 0.0, stride=6)["n"] == 0)

# deterministic fall: every long entry is stopped out, every short entry hits its target
fall = series(5 * BARS_PER_DAY, 4, 0.0, drift=-0.005)
rf = roe.empirical_rates(fall, roe.target_plan(0.5, 20, 0.0), 0.0, stride=24)
longs_stop = rf["by_side"][1][1]; shorts_tp = rf["by_side"][-1][0]
check("steady fall: every long is stopped, every short reaches its target",
      longs_stop > 0 and rf["by_side"][1][0] == 0 and shorts_tp == longs_stop
      and rf["by_side"][-1][1] == 0, str(rf["by_side"]))
check("stop-out is exactly -50% and target exactly +100% of margin (both sides exact)",
      sorted({round(x, 6) for x in rf["roe"]}) == [-0.5, 1.0], str(sorted({round(x, 6) for x in rf["roe"]})))

print("\n=== a timestamp gap is never bridged ===")
NG = 6 * BARS_PER_DAY
g = gapped(NG, 31, BARS_PER_DAY, 100, sigma=0.003)
rg = roe.empirical_rates(g, plan, 0.0, stride=6)
exp_entries = expected_entries(g["ts"], BARS_PER_DAY, 6)
bridged_entries = sum(1 for i in range(BARS_PER_DAY, len(g["ts"]) - BARS_PER_DAY)
                      if (i - BARS_PER_DAY) % 6 == 0)
check("the gap costs samples (a bridging implementation would count more)",
      exp_entries < bridged_entries, f"{exp_entries} vs {bridged_entries}")
check("the entries are exactly the ones the sampling rule allows",
      rg["entries"] == exp_entries, f"{rg['entries']} vs {exp_entries}")
runs = BR.contiguous_runs(g["ts"])


def run_of(i):
    for (a, z) in runs:
        if a <= i < z:
            return (a, z)
    return None


bad = [i for i in rg["entry_idx"] if run_of(i) is None
       or run_of(i)[1] - (i + 1) < BARS_PER_DAY]
check("every entry's label window lies inside its own contiguous run",
      not bad, f"{len(bad)} entries cross a gap")
bad_hist = [i for i in rg["entry_idx"] if run_of(i) and i - run_of(i)[0] < BARS_PER_DAY]
check("every entry's 24h liquidity window lies inside its own contiguous run",
      not bad_hist, f"{len(bad_hist)} entries reach back over a gap")

print("\n=== coin_report / pooled over a bar directory ===")
d = tempfile.mkdtemp()


def write(coin, bars):
    with gzip.open(os.path.join(d, coin + ".csv.gz"), "wt", newline="") as f:
        f.write("ts,o,h,l,c,vol,amount\n")
        for i in range(len(bars["ts"])):
            f.write(",".join(str(bars[k][i]) for k in ("ts", "o", "h", "l", "c", "vol", "amt")) + "\n")


write("BTC", series(N, 11, 0.001, amt=5e6))
write("AAA", series(N, 12, 0.003, amt=40000.0))
write("ZZZ", series(N, 13, 0.003, amt=50.0))
write("GAP", gapped(N, 14, N - 50, 40, sigma=0.003, amt=40000.0))
rep = roe.coin_report(d, "AAA", stop_roe=0.5, leverages=(10, 20), stride=24)
check("a liquid coin yields a row per leverage", rep and len(rep["rows"]) == 2)
check("the coin's round-trip cost comes from the cost model",
      rep and abs(rep["cost_frac"] - DEFAULT.cost.round_trip(rep["qv24"])) < 1e-12,
      str(rep and rep.get("cost_frac")))
check("an illiquid coin is reported untradeable, not analyzed",
      (roe.coin_report(d, "ZZZ", leverages=(20,)) or {}).get("untradeable"))
check("a coin whose 24h volume window spans a gap is reported untradeable",
      (roe.coin_report(d, "GAP", leverages=(20,)) or {}).get("untradeable"),
      str({k: v for k, v in (roe.coin_report(d, "GAP", leverages=(20,)) or {}).items()
           if k != "rows"}))
check("a coin with no archive is skipped", roe.coin_report(d, "NOPE") is None)
pool = roe.pooled_table(d, ["AAA", "ZZZ"], stop_roe=0.5, leverages=(20,), stride=24)
check("pooled table only counts the liquid coin", pool[20]["coins"] == 1)
check("pooled samples add up across the coins",
      pool[20]["n"] == rep["rows"][1]["rates"]["n"])
check("the pooled row carries a day-cluster interval for TP among terminal",
      "tp_ci" in pool[20] and len(pool[20]["tp_ci"]) == 2
      and pool[20]["tp_ci"][0] <= pool[20]["tp_given_terminal"] <= pool[20]["tp_ci"][1],
      str(pool[20].get("tp_ci")))
check("the pooled row carries a day-cluster interval for the mean ROE",
      "roe_ci" in pool[20] and len(pool[20]["roe_ci"]) == 2
      and pool[20]["roe_ci"][0] <= pool[20]["mean_roe"] <= pool[20]["roe_ci"][1],
      str(pool[20].get("roe_ci")))
check("the intervals are moving-block intervals, and the block is declared",
      pool[20].get("block_days") == roe.BLOCK_DAYS
      and pool[20].get("bootstrap_b") == roe.BOOTSTRAP_B,
      str({k: pool[20].get(k) for k in ("block_days", "bootstrap_b")}))
check("the interval's dependence on the block length is reported",
      set(pool[20].get("ci_by_block", {})) == {str(b) for b in roe.BLOCK_SENSITIVITY},
      str(sorted(pool[20].get("ci_by_block", {}))))
check("the pooled plan keeps the requested leverage", pool[20]["plan"]["leverage"] == 20)

print("\n=== the watcher records the ROE block on every event ===")
K = 8 * BARS_PER_DAY
sys.path.insert(0, os.path.join(ROOT, "tests"))


def planted(n, k, seed, sigma=0.002, amt=20000.0, jump=0.04, mult=40):
    from proto.quant.stream import FeatureStream
    for sd in range(seed, seed + 300):
        rng = random.Random(sd)
        px, rows = 100.0, []
        for i in range(n):
            o = px
            r_ = jump if i == k else rng.gauss(0, sigma)
            px = o * math.exp(r_)
            a = amt * (0.7 + 0.6 * rng.random()) * (mult if i == k else 1)
            rows.append((T0 + i * BAR_S, o, max(o, px) * 1.0005, min(o, px) * 0.9995, px, a / px, a))
        st = FeatureStream(DEFAULT.feat)
        snap = None
        for r_ in rows[:k + 1]:
            snap = st.update(r_[0], r_[1], r_[2], r_[3], r_[4], r_[6], 0.0)
        if snap and snap.up_start and snap.rvol and snap.rvol >= 4:
            return rows
    raise RuntimeError("seed search failed")


def writerows(dirp, coin, rows, mode="wt"):
    with gzip.open(os.path.join(dirp, coin + ".csv.gz"), mode, newline="") as f:
        if mode == "wt":
            f.write("ts,o,h,l,c,vol,amount\n")
        for r_ in rows:
            f.write(",".join(str(x) for x in r_) + "\n")


bd = tempfile.mkdtemp()
rng = random.Random(5)
btc = []
px = 100.0
for i in range(K + 20):
    o = px; px *= math.exp(rng.gauss(0, 0.001))
    btc.append((T0 + i * BAR_S, o, max(o, px) * 1.0003, min(o, px) * 0.9997, px, 1.0, 5e6))
aaa = planted(K + 20, K, 2)
writerows(bd, "BTC", btc[:K]); writerows(bd, "AAA", aaa[:K])
import dataclasses
cfg = dataclasses.replace(DEFAULT, selector=dataclasses.replace(DEFAULT.selector, cooldown_bars=0))
st = qstore.QuantStore(os.path.join(bd, "q.db"))
clock = [btc[K - 1][0] + BAR_S]
w = E.Watcher(bd, st, cfg, now_fn=lambda: clock[0])
w.step()
writerows(bd, "BTC", btc[K:K + 1], "at"); writerows(bd, "AAA", aaa[K:K + 1], "at")
clock[0] = btc[K][0] + BAR_S
w.step()
evs = st.all_events()
check("events were emitted", len(evs) >= 1)
feats = [json.loads(e["features"]) for e in evs]
check("every event carries the roe_100 block", all("roe_100" in f for f in feats))
check("the block reports leverage needed and the stop's cost on margin",
      all(f["roe_100"].get("feasible") and f["roe_100"]["roe_at_stop"] < 0 for f in feats),
      str(feats[0].get("roe_100") if feats else None))
check("the block's leverage is the one the event's own target needs",
      all(abs(f["roe_100"]["leverage_needed"]
              * (abs(e["tp"] / e["ref_price"] - 1.0) - e["cost_frac"])
              - f["roe_100"]["target_roe"]) < 1e-9 for f, e in zip(feats, evs)))
check("events remain SHADOW / not actionable",
      all(e["mode"] == "SHADOW" for e in evs))

print("\n=== the champion module imports on Python 3.10-3.13 ===")
champ_path = os.path.join(ROOT, "proto", "quant", "champion.py")
champ_src = open(champ_path, encoding="utf-8").read()
champ_tree = ast.parse(champ_src)
list_classes = [c for c in ast.walk(champ_tree)
                if isinstance(c, ast.ClassDef)
                and any(isinstance(n, ast.FunctionDef) and n.name == "list"
                        for n in c.body)]
check("the class that defines a `list` method exists", len(list_classes) == 1,
      str([c.name for c in list_classes]))
has_future = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                  and any(a.name == "annotations" for a in n.names)
                  for n in champ_tree.body)
check("the module opts out of eager annotation evaluation", has_future)


def _class_src(node):
    return ast.unparse(ast.Module(body=[node], type_ignores=[]))


def _eager_eval_annotations(cls, class_node, module_globals):
    """Emulate Python < 3.14's EAGER annotation evaluation inside a class
    body: every annotation expression is evaluated in the class namespace
    (class scope first, then module globals), exactly as the class body
    would have evaluated it."""
    ns = dict(module_globals)
    ns.update(vars(cls))
    for node in ast.walk(class_node):
        anns = []
        if isinstance(node, ast.FunctionDef):
            a = node.args
            for arg in (list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)):
                if arg.annotation is not None:
                    anns.append(arg.annotation)
            if node.returns is not None:
                anns.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            anns.append(node.annotation)
        for ann in anns:
            eval(compile(ast.Expression(ann), "<ann>", "eval"), ns)


if list_classes:
    import proto.quant.champion as champ
    cls_node = list_classes[0]
    src = _class_src(cls_node)
    ns = {"ConfigVersion": champ.ConfigVersion}
    exec(compile(src, "<champion-class>", "exec"), ns)
    built = ns[cls_node.name]
    eager_ok = True
    try:
        _eager_eval_annotations(built, cls_node, champ.__dict__)
    except TypeError:
        eager_ok = False
    check("without the future import the class body itself raises (Python < 3.14)",
          not eager_ok)
    lazy_ok = True
    try:
        exec(compile("from __future__ import annotations\n" + src,
                     "<champion-class>", "exec"),
             {"ConfigVersion": champ.ConfigVersion})
    except TypeError:
        lazy_ok = False
    check("with the future import the same class body compiles", lazy_ok)
    check("with the future import the annotations stay strings (never evaluated)",
          isinstance(champ.Registry.list.__annotations__["return"], str),
          str(champ.Registry.list.__annotations__))
    check("the champion module imports", champ.Registry is not None)

print("\n=== CLI ===")
env = dict(os.environ, PYTHONPATH=ROOT)
cp = subprocess.run([sys.executable, "-m", "proto.quant", "roe", "--data", d,
                     "--leverage", "20", "--stride", "24", "--coins", "AAA"],
                    capture_output=True, text=True, env=env, cwd=ROOT)
check("roe command runs and prints the not-a-recommendation banner",
      cp.returncode == 0 and "not a recommendation" in cp.stdout
      and "leverage 20x" in cp.stdout, cp.stderr[-300:] + cp.stdout[-300:])
check("the command names the window it measured",
      "pre-holdout" in cp.stdout, cp.stdout[-400:])
check("the command says the holdout is EXCLUDED from the measurement, "
      "not that the archive is never read",
      "EXCLUDED" in cp.stdout and "NOT read" not in cp.stdout
      and "no holdout outcome" in cp.stdout, cp.stdout[-600:])
check("the command declares the bootstrap it used",
      "moving-block bootstrap" in cp.stdout and "consecutive UTC days" in cp.stdout,
      cp.stdout[-600:])
from proto.quant.__main__ import preholdout_window as _phw
_frozen = {"dev": (6.0, 61.0), "val": (63.0, 77.0), "holdout": (79.0, 101.2)}
check("the window guard admits the frozen splits with a 1-day horizon",
      _phw(_frozen, 1.0) == (6.0, 77.0), str(_phw(_frozen, 1.0)))
check("the window guard refuses a horizon that would reach the holdout",
      _phw({"dev": (6.0, 61.0), "val": (63.0, 78.5), "holdout": (79.0, 101.2)},
           1.0) is None)
check("the window guard admits a horizon that ends exactly ON the holdout's "
      "first day: the measured window is half-open, so no holdout bar is read",
      _phw({"dev": (6.0, 61.0), "val": (63.0, 78.0), "holdout": (79.0, 101.2)},
           1.0) == (6.0, 78.0))
out_json = os.path.join(tempfile.mkdtemp(), "roe.json")
cp2 = subprocess.run([sys.executable, "-m", "proto.quant", "roe", "--data", d,
                      "--leverage", "20", "--stride", "24", "--coins", "AAA",
                      "--out", out_json],
                     capture_output=True, text=True, env=env, cwd=ROOT)
check("the command writes its JSON output", cp2.returncode == 0
      and os.path.exists(out_json), cp2.stderr[-300:])
if os.path.exists(out_json):
    blob = json.load(open(out_json))
    check("the JSON has one pooled entry per requested leverage",
          list(blob.get("pooled", {})) == ["20"], str(list(blob)))
    check("the JSON carries the denominators",
          blob["pooled"]["20"]["n"] > 0 and blob["pooled"]["20"]["coins"] >= 1,
          str(blob["pooled"]["20"])[:200])
    check("the JSON carries the per-coin rows",
          len(blob.get("coins", [])) >= 1
          and blob["coins"][0]["coin"] == "AAA", str(blob.get("coins"))[:200])
    w = blob.get("window", {})
    check("the measured window stops before the sealed holdout starts",
          w.get("holdout_excluded") is True
          and w["t_hi"] + w["horizon_s"] <= w["holdout_start_ts"],
          str(w))
cp3 = subprocess.run([sys.executable, "-m", "proto.quant", "roe", "--data",
                      os.path.join(tempfile.mkdtemp(), "empty"), "--leverage", "20"],
                     capture_output=True, text=True, env=env, cwd=ROOT)
check("a missing archive is refused with a message, not a traceback",
      cp3.returncode == 1 and "BTC" in cp3.stdout + cp3.stderr
      and "Traceback" not in cp3.stderr, cp3.stdout[-200:] + cp3.stderr[-200:])
for bad in (["--leverage", "abc"], ["--stride", "0"], ["--target-roe", "0"],
            ["--stop-roe", "0"]):
    cpb = subprocess.run([sys.executable, "-m", "proto.quant", "roe", "--data", d,
                          "--coins", "AAA", *bad],
                         capture_output=True, text=True, env=env, cwd=ROOT)
    check(f"an invalid argument is refused with a message: {' '.join(bad)}",
          cpb.returncode == 1 and "Traceback" not in cpb.stderr
          and (cpb.stdout + cpb.stderr).strip() != "",
          cpb.stdout[-200:] + cpb.stderr[-200:])
cpo = subprocess.run([sys.executable, "-m", "proto.quant", "roe", "--data", d,
                      "--coins", "AAA", "--leverage", "20",
                      "--out", os.path.join(tempfile.mkdtemp(), "no", "dir", "r.json")],
                     capture_output=True, text=True, env=env, cwd=ROOT)
check("an unwritable --out path is refused with a message, not a traceback",
      cpo.returncode == 1 and "Traceback" not in cpo.stderr
      and (cpo.stdout + cpo.stderr).strip() != "",
      cpo.stdout[-200:] + cpo.stderr[-200:])
cpn = subprocess.run([sys.executable, "-m", "proto.quant", "roe", "--data", d,
                      "--coins", "NOPE", "--leverage", "20"],
                     capture_output=True, text=True, env=env, cwd=ROOT)
check("a coin with no archive is refused with a message, not a traceback",
      cpn.returncode == 1 and "Traceback" not in cpn.stderr
      and "NOPE" in cpn.stdout + cpn.stderr,
      cpn.stdout[-200:] + cpn.stderr[-200:])
cpi = subprocess.run([sys.executable, "-m", "proto.quant", "roe", "--data", d,
                      "--coins", "AAA", "--leverage", "1", "--target-roe", "1"],
                     capture_output=True, text=True, env=env, cwd=ROOT)
check("a geometry of 100% of price is refused with a message, not a traceback",
      cpi.returncode == 1 and "Traceback" not in cpi.stderr
      and (cpi.stdout + cpi.stderr).strip() != "",
      cpi.stdout[-200:] + cpi.stderr[-200:])
cpl = subprocess.run([sys.executable, "-m", "proto.quant", "roe", "--data", d,
                      "--coins", "AAA", "--leverage", "200", "--stride", "288"],
                     capture_output=True, text=True, env=env, cwd=ROOT)
check("a stop outside the liquidation distance is reported as NOT inside",
      cpl.returncode == 0 and "NOT inside" in cpl.stdout, cpl.stdout[-400:])

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
