#!/usr/bin/env python3
"""WS-B (workstream B): competing outcomes, inference and family correction.

Self-checking script in the house style: prints PASS/FAIL per check and
exits non-zero when anything fails.  Everything here is SYNTHETIC (hand-built
events, hand-built funding fixtures under a temp dir) so the suite is fast,
deterministic and never touches data/.

Covered:
  1. cause taxonomy (TP | SL | TIME | INVALIDATED | CENSORED) is exhaustive
     and mutually exclusive, and the counts sum to the selected denominator
  2. censoring is real (end of sample, data loss) -- counted, never dropped
  3. invalidation takes precedence and never fabricates a return
  4. friction / funding stay separately attributable; net = gross - friction
     - funding exactly; unattributable funding yields net=None + a reason
  5. conservative barrier ordering (stop wins a same-bar tie, the fill bar's
     stop is terminal) survives the outcome layer
  6. gross/net distributions and expected return with explicit denominators
  7. chronological probability estimation with a calibration table
  8. day-cluster AND coin-cluster uncertainty
  9. three baselines on the same event set
 10. family-level multiple testing (Holm + bootstrap max-t)
"""
import gzip
import math
import os
import random
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from proto.quant import barrier as B
from proto.quant import competing_outcomes as CO
from proto.quant import family_test as FT
from proto.quant import inference as INF
from proto.quant.config import (BAR_S, DEFAULT, Geometry, RuleParams,
                                grid_geometries)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  " + name)
    else:
        print("  FAIL  " + name + (("   " + str(detail)) if detail else ""))
        FAILURES.append(name)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
T0 = 1_700_000_100
GRID = grid_geometries()
GEOM = Geometry(48, 1.0, 1.0)          # horizon 48 bars, k_stop 1, r_tp 1
GI = GRID.index(GEOM)
RULE = RuleParams(family="breakout_start", policy="fade", rvol_min=4.0)
RULE_CONT = RuleParams(family="breakout_start", policy="cont", rvol_min=4.0)
WINDOW = (0.0, 1e9)                    # every synthetic event is inside
CYCLE_S = 8 * 3600
SETTLE_RATE = 0.0005


def trade_slot(ev, rule=RULE):
    """Label slot of the TRADE side (same convention as the harness)."""
    side = ev.side if rule.policy == "cont" else -ev.side
    return GI * 2 + (0 if side > 0 else 1)


def mk_event(coin="AAA", ts=T0, side=1, kind=0, gross=0.01, offset=0,
             qv24=5e6, sigma=0.002, param=5.0, fam="B", **extra):
    """A research-like event: per-(geometry, side) label slots."""
    gross_arr = [0.0] * (2 * len(GRID))
    kind_arr = [2] * (2 * len(GRID))
    off_arr = [0] * (2 * len(GRID))
    for s in (1, -1):
        k = GI * 2 + (0 if s > 0 else 1)
        gross_arr[k] = gross
        kind_arr[k] = kind
        off_arr[k] = offset
    ev = SimpleNamespace(coin=coin, ts=ts, day=ts / 86400.0, fam=fam,
                         param=param, side=side, qv24=qv24, sigma=sigma,
                         rvol=5.0, z1h=0.1, z4h=0.2, btc7d=0.01,
                         gross=gross_arr, kind=kind_arr, exit_offset=off_arr)
    for kk, vv in extra.items():
        setattr(ev, kk, vv)
    return ev


def write_settlements(directory, coin, rows):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, coin + ".csv.gz")
    with gzip.open(path, "wt", newline="") as f:
        f.write("settle_ts,settled_rate,cycle_hours\n")
        for ts, rate, cycle in rows:
            f.write(f"{ts},{rate},{cycle}\n")


SETTLE_DIR = os.path.join(tempfile.mkdtemp(prefix="wsb-outcomes-"), "settlements")
write_settlements(SETTLE_DIR, "AAA", [
    (k * CYCLE_S, SETTLE_RATE, 8)
    for k in range((T0 - 20 * 86400) // CYCLE_S, (T0 + 20 * 86400) // CYCLE_S + 1)
])


def expected_funding(open_ts, close_ts, side, rate=SETTLE_RATE, cycle=CYCLE_S):
    """Hand calculation of the epoch-aligned funding boundaries in a window."""
    n = 0
    b = (open_ts // cycle + 1) * cycle
    while b < close_ts:
        n += 1
        b += cycle
    return rate * side * n


def mk_outcome(cause="TP", gross=0.01, friction=0.0016, funding=0.0005,
                dd=0.01, coin="AAA", ts=T0, side=1, reason=None):
    net = None
    if gross is not None and friction is not None and funding is not None \
            and dd:
        net = (gross - friction - funding) / dd
    return CO.Outcome(event_id=f"{coin}:{ts}", coin=coin, ts=ts, side=side,
                      cause=cause, exit_ts=ts + 300, gross_log=gross,
                      friction=friction, funding=funding, net_r=net, dd=dd,
                      reason=reason)


# --------------------------------------------------------------------------
print("=== 1. cause taxonomy: exhaustive and mutually exclusive ===")
evs = [
    mk_event(ts=T0, kind=0, gross=0.01, offset=3),        # TP
    mk_event(ts=T0 + 300, kind=1, gross=-0.01, offset=2),  # SL
    mk_event(ts=T0 + 600, kind=2, gross=0.002, offset=47),  # TIME
    mk_event(ts=T0 + 900, kind=0, gross=0.01, offset=1),   # TP
    mk_event(ts=T0 + 1200, kind=1, gross=-0.01, offset=4),  # SL
]
res = CO.resolve_competing(evs, RULE, GEOM, WINDOW, DEFAULT)
check("every selected event produces an outcome",
      len(res["outcomes"]) == res["n_selected"] == 5, str(res["n_selected"]))
check("cause counts sum exactly to the selected denominator",
      sum(res["causes"].values()) == res["n_selected"],
      f"{res['causes']} vs {res['n_selected']}")
check("causes are counted in their own buckets",
      res["causes"]["TP"] == 2 and res["causes"]["SL"] == 2
      and res["causes"]["TIME"] == 1 and res["causes"]["INVALIDATED"] == 0
      and res["causes"]["CENSORED"] == 0, str(res["causes"]))
check("coverage = n_resolved / n_selected",
      abs(res["coverage"] - res["n_resolved"] / res["n_selected"]) < 1e-12)
check("n_censored is zero here and n_invalidated is zero",
      res["n_censored"] == 0 and res["n_invalidated"] == 0)
check("every cause is one of the declared CAUSES",
      all(o.cause in CO.CAUSES for o in res["outcomes"]))
check("a TP outcome carries its gross and a non-None exit",
      res["outcomes"][0].gross_log == 0.01 and res["outcomes"][0].exit_ts is not None)

# --------------------------------------------------------------------------
print("\n=== 2. censoring is real: end of sample and data loss ===")
res_c = CO.resolve_competing(evs, RULE, GEOM, WINDOW, DEFAULT,
                             sample_end_ts=T0 + 10 * BAR_S)
check("a horizon past the sample end is CENSORED, not TIME",
      res_c["causes"]["CENSORED"] == 5 and res_c["causes"]["TIME"] == 0,
      str(res_c["causes"]))
check("censored events are counted, not dropped",
      res_c["n_censored"] == 5 and res_c["n_selected"] == 5)
check("censored events carry no gross and no net",
      all(o.gross_log is None and o.net_r is None for o in res_c["outcomes"]))
check("the censoring reason names the end of sample",
      all(o.reason == "censored:end-of-sample" for o in res_c["outcomes"]),
      str([o.reason for o in res_c["outcomes"]]))
check("censored events are excluded from the resolved denominator",
      res_c["n_resolved"] == 0 and res_c["coverage"] == 0.0)

lossy = mk_event(ts=T0, kind=0, gross=0.01, offset=1)
lossy.kind[trade_slot(lossy)] = None          # the label never resolved
res_l = CO.resolve_competing([lossy], RULE, GEOM, WINDOW, DEFAULT)
check("an unresolvable label is CENSORED with a data-loss reason",
      res_l["causes"]["CENSORED"] == 1
      and "data-loss" in (res_l["outcomes"][0].reason or ""),
      str(res_l["outcomes"][0].reason))
gap = mk_event(ts=T0, kind=0, gross=0.01, offset=1, data_loss="bar-gap")
res_g = CO.resolve_competing([gap], RULE, GEOM, WINDOW, DEFAULT)
check("a marked data loss names its cause",
      res_g["outcomes"][0].reason == "censored:data-loss:bar-gap",
      str(res_g["outcomes"][0].reason))
check("a censored event is still counted in n_selected",
      res_g["n_selected"] == 1 and res_g["n_censored"] == 1)

# --------------------------------------------------------------------------
print("\n=== 3. invalidation: precedence and no fabricated return ===")
res_i = CO.resolve_competing(evs, RULE, GEOM, WINDOW, DEFAULT,
                             invalidation=lambda ev: ev.ts == T0 + 600)
check("invalidation takes precedence over TP/SL/TIME",
      res_i["causes"]["INVALIDATED"] == 1 and res_i["causes"]["TIME"] == 0,
      str(res_i["causes"]))
inv = res_i["outcomes"][2]
check("an invalidation without an exit offset has no gross",
      inv.gross_log is None and inv.reason == "invalidation:no-exit",
      str(inv))
check("an invalidation is still counted in n_selected",
      res_i["n_selected"] == 5 and res_i["n_invalidated"] == 1)

inv2 = mk_event(ts=T0, kind=0, gross=0.01, offset=3)
inv2.invalid_exit_offset = [0] * (2 * len(GRID))
inv2.invalid_exit_offset[trade_slot(inv2)] = 2
inv2.invalid_gross = [0.0] * (2 * len(GRID))
inv2.invalid_gross[trade_slot(inv2)] = 0.004
res_i2 = CO.resolve_competing([inv2], RULE, GEOM, WINDOW, DEFAULT,
                              invalidation=lambda ev: True)
o2 = res_i2["outcomes"][0]
check("an invalidation with an exit offset measures the gross to that point",
      res_i2["causes"]["INVALIDATED"] == 1 and o2.gross_log == 0.004
      and o2.exit_ts == T0 + (1 + 2) * BAR_S, str(o2))

# --------------------------------------------------------------------------
print("\n=== 4. cost separation: friction, funding, net ===")
cont_ev = mk_event(coin="AAA", ts=T0, side=1, kind=2, gross=0.01, offset=47)
res_f = CO.resolve_competing([cont_ev], RULE_CONT, GEOM, WINDOW, DEFAULT,
                             settlements_dir=SETTLE_DIR)
o = res_f["outcomes"][0]
exp_fund = expected_funding(T0 + BAR_S, T0 + (2 + 47) * BAR_S, 1)
check("the funding cost equals the hand-calculated boundaries",
      abs(o.funding - exp_fund) < 1e-12, f"{o.funding} vs {exp_fund}")
check("the friction is the configured round trip",
      abs(o.friction - DEFAULT.cost.round_trip(5e6)) < 1e-15)
check("net_r = (gross_log - friction - funding) / dd EXACTLY",
      abs(o.net_r - (o.gross_log - o.friction - o.funding) / o.dd) < 1e-15)
check("the funding actually changes the net number",
      abs(o.net_r - (o.gross_log - o.friction) / o.dd) > 1e-9)
check("n_net_evaluable counts the outcomes with a net number",
      res_f["n_net_evaluable"] == 1)

nod = mk_event(coin="NODATA", ts=T0, side=1, kind=2, gross=0.01, offset=47)
res_n = CO.resolve_competing([nod], RULE_CONT, GEOM, WINDOW, DEFAULT,
                             settlements_dir=SETTLE_DIR)
on = res_n["outcomes"][0]
check("unattributable funding yields funding=None and net_r=None",
      on.funding is None and on.net_r is None)
check("the reason names the funding cause",
      (on.reason or "").startswith("funding:"), str(on.reason))
check("a friction-only number is never presented as net",
      on.net_r is None and res_n["n_net_evaluable"] == 0)
check("the event still counts in n_selected",
      res_n["n_selected"] == 1 and res_n["n_censored"] == 0)

# --------------------------------------------------------------------------
print("\n=== 5. conservative barrier ordering ===")
# Entry bar touches BOTH the stop and the target -> the stop wins.
o_arr = [100.0, 100.0, 100.0]
h_arr = [100.0, 111.0, 100.0]
l_arr = [100.0, 89.0, 100.0]
c_arr = [100.0, 100.0, 100.0]
path = B.make_path(o_arr, h_arr, l_arr, c_arr, 0, 2)
gross, kind, off = B.label_path(path, 1, 0.10, 2, 1.0)
check("the barrier layer resolves a same-bar tie as SL", kind == "SL", kind)
tie = mk_event(ts=T0, kind={"TP": 0, "SL": 1, "TIME": 2}[kind], gross=gross,
               offset=off)
res_t = CO.resolve_competing([tie], RULE_CONT, GEOM, WINDOW, DEFAULT)
check("the outcome layer keeps the conservative SL",
      res_t["causes"]["SL"] == 1 and res_t["causes"]["TP"] == 0,
      str(res_t["causes"]))
# The stop on the fill bar is terminal: a later target touch is ignored.
o2_ = [100.0, 100.0, 100.0]
h2_ = [100.0, 111.0, 111.0]
l2_ = [100.0, 89.0, 100.0]
c2_ = [100.0, 100.0, 100.0]
p2 = B.make_path(o2_, h2_, l2_, c2_, 0, 2)
g2, k2, o2 = B.label_path(p2, 1, 0.10, 2, 1.0)
check("the fill bar's stop is terminal (a later target is ignored)",
      k2 == "SL" and o2 == 0, f"{k2} {o2}")

# --------------------------------------------------------------------------
print("\n=== 6. distributions and expected return ===")
outs = [mk_outcome(cause="TP", gross=0.01, friction=0.0, funding=0.0, dd=1.0),
        mk_outcome(cause="TP", gross=0.02, friction=0.0, funding=0.0, dd=1.0),
        mk_outcome(cause="SL", gross=-0.01, friction=0.0, funding=0.0, dd=1.0),
        mk_outcome(cause="TIME", gross=0.00, friction=0.0, funding=0.0, dd=1.0),
        mk_outcome(cause="CENSORED", gross=None, friction=None, funding=None,
                   dd=None)]
dist = CO.outcome_distribution(outs, key="gross_log")
check("the distribution names its denominator",
      dist["n"] == 4 and dist["n_missing"] == 1, str(dist))
check("the mean is over the non-missing values only",
      abs(dist["mean"] - (0.01 + 0.02 - 0.01 + 0.0) / 4) < 1e-12)
check("the hit rate is over TERMINAL outcomes only",
      abs(dist["hit_rate"] - 2 / 3) < 1e-12, str(dist["hit_rate"]))
check("gross/friction/funding means are reported separately",
      abs(dist["mean_gross"] - 0.005) < 1e-12
      and abs(dist["mean_friction"] - 0.0) < 1e-12
      and abs(dist["mean_funding"] - 0.0) < 1e-12)
p5 = CO.outcome_distribution([mk_outcome(gross=v, friction=0.0, funding=0.0,
                                         dd=1.0) for v in (0, 1, 2, 3, 4)],
                             key="gross_log")
check("percentiles use linear interpolation between order statistics",
      abs(p5["p05"] - 0.2) < 1e-12 and abs(p5["p25"] - 1.0) < 1e-12
      and abs(p5["median"] - 2.0) < 1e-12 and abs(p5["p75"] - 3.0) < 1e-12
      and abs(p5["p95"] - 3.8) < 1e-12, str(p5))
fonly = CO.outcome_distribution(outs, key="friction_net_r")
check("a friction-only view exists and is never called net",
      fonly["n"] == 4 and abs(fonly["mean"] - 0.005) < 1e-12)

er = CO.expected_return(outs, key="gross_log")
check("expected return without days falls back to the plain mean",
      er["n"] == 4 and abs(er["mean"] - 0.005) < 1e-12
      and er["cluster"] == "row" and er["ci_lo"] < er["mean"] < er["ci_hi"]
      and "no day labels" in (er["reason"] or ""), str(er["reason"]))
er_day = CO.expected_return(outs, key="gross_log", days=[0, 0, 1, 1])
check("fewer than 3 day clusters gives NO interval and a reason",
      er_day["ci_lo"] is None and er_day["ci_hi"] is None
      and "suppressed" in (er_day["reason"] or "")
      and "2 distinct days" in (er_day["reason"] or ""), str(er_day["reason"]))
check("the cluster field is honest about the fallback that was used",
      er_day["cluster"] == "row", str(er_day["cluster"]))
check("the row-level interval is still available next to the suppressed one",
      er_day["row_ci_lo"] < er_day["mean"] < er_day["row_ci_hi"])
er_day3 = CO.expected_return(outs, key="gross_log", days=[0, 0, 1, 2])
check("with 3 distinct days the day-cluster interval is reported",
      er_day3["cluster"] == "day" and er_day3["ci_lo"] is not None
      and er_day3["ci_hi"] is not None and er_day3["n_days"] == 3)
er_none = CO.expected_return(outs, key="gross_log", days=[0, 0, None, 1])
check("day labels that do not align say so (not 'no day labels supplied')",
      "no day labels supplied" not in (er_none["reason"] or "")
      and "do not align" in (er_none["reason"] or ""), str(er_none["reason"]))

# A degenerate (zero-width) day-cluster interval is NOT a cluster interval:
# six IDENTICAL values over three days used to report ci_lo == ci_hi == the
# mean with cluster="day" -- a zero-width interval presented as certainty.
flat = [mk_outcome(cause="TP", gross=0.01, friction=0.0, funding=0.0, dd=1.0,
                   coin=f"C{i % 2}", ts=T0 + i * 86400)
        for i in range(6)]
er_flat = CO.expected_return(flat, key="gross_log",
                             days=[0, 0, 1, 1, 2, 2])
check("a degenerate day-cluster interval is suppressed, not reported",
      er_flat["ci_lo"] is None and er_flat["ci_hi"] is None,
      f"ci_lo={er_flat['ci_lo']} ci_hi={er_flat['ci_hi']}")
check("the suppressed interval says why",
      "degenerate" in (er_flat["reason"] or "")
      and "suppressed" in (er_flat["reason"] or ""), str(er_flat["reason"]))
check("the cluster field does not claim a day cluster it did not report",
      er_flat["cluster"] == "row", str(er_flat["cluster"]))
check("a degenerate interval takes its statistic with it",
      er_flat["t"] is None, str(er_flat["t"]))
check("the row-level interval is still available next to the suppressed one",
      er_flat["row_ci_lo"] is not None and er_flat["row_ci_hi"] is not None)
check("the mean is still reported (only the interval is suppressed)",
      er_flat["mean"] is not None and er_flat["n"] == 6, str(er_flat["n"]))

# --------------------------------------------------------------------------
print("\n=== 7. chronological probability and calibration ===")
N = 160
cp_events = []
for i in range(N):
    ts = T0 + i * 6 * 3600                      # 6h spacing -> 4 tod buckets
    bucket = (ts % 86400) // 21600
    if bucket == 0:
        kind = 1 if (i % 5 == 0) else 0        # 80% hits in bucket 0
    else:
        kind = 0 if (i % 5 == 0) else 1        # 20% hits elsewhere
    cp_events.append(mk_event(ts=ts, kind=kind, gross=0.01 if kind == 0 else -0.01,
                               offset=2))
cp = INF.chronological_probability(cp_events, RULE, GEOM, WINDOW, min_train=30,
                                    step=1)
check("the estimator predicts after the training prefix",
      cp["n_predictions"] == N - 30, str(cp["n_predictions"]))
check("every prediction that had a label is scored",
      cp["n_outcomes"] == N - 30, str(cp["n_outcomes"]))
check("every calibration bin carries its denominators",
      all(b["n_pred"] > 0 and b["n_hit"] >= 0 and b["observed"] is not None
          for b in cp["bins"]), str(cp["bins"]))
check("the bins' denominators sum to the scored outcomes",
      sum(b["n_pred"] for b in cp["bins"]) == cp["n_outcomes"])
check("the brier score is reported", isinstance(cp["brier"], float))
check("the calibration slope is reported or explained",
      cp["slope"] is not None or cp.get("reason"), str(cp.get("reason")))
check("the estimator learned the bucket structure",
      cp["records"] and
      sum(r["p"] for r in cp["records"] if r["bucket"][0] == 0) /
      max(sum(1 for r in cp["records"] if r["bucket"][0] == 0), 1) >
      sum(r["p"] for r in cp["records"] if r["bucket"][0] == 1) /
      max(sum(1 for r in cp["records"] if r["bucket"][0] == 1), 1))
cp_few = INF.chronological_probability(cp_events[:10], RULE, GEOM, WINDOW,
                                        min_train=30)
check("too few training events return empties with a reason",
      cp_few["n_predictions"] == 0 and cp_few["brier"] is None
      and cp_few["slope"] is None and cp_few.get("reason"),
      str(cp_few.get("reason")))

# The first `min_train` events are the TRAINING PREFIX: they are never
# predicted, but the first prediction is fitted on them.  Probe: 40 events
# whose first 30 are all hits -> the first prediction must be near 1, not
# the 0.5 of an empty training set.
probe = [mk_event(ts=T0 + i * 6 * 3600, kind=0, gross=0.01, offset=2)
         for i in range(40)]
pr = INF.chronological_probability(probe, RULE, GEOM, WINDOW, min_train=30,
                                    step=1)
check("the first prediction is fitted on the training prefix",
      pr["records"] and pr["records"][0]["p"] > 0.9,
      f"first p = {pr['records'][0]['p'] if pr['records'] else None}")
check("the training prefix is not discarded: n_train covers every event",
      pr["n_train"] == 40, str(pr["n_train"]))
check("the prefix itself is never predicted",
      pr["n_predictions"] == 10 and pr["n_outcomes"] == 10,
      str((pr["n_predictions"], pr["n_outcomes"])))

# --------------------------------------------------------------------------
print("\n=== 8. uncertainty: day cluster AND coin cluster ===")
rng = random.Random(4)
COEF = {"A": 1.0, "B": -1.0, "C": 1.2, "D": -0.8, "E": 0.6}
vals, dys, cns = [], [], []
for d in range(40):
    shock = rng.gauss(0, 2.0)                 # common daily shock
    for c in COEF:
        for _ in range(4):
            vals.append(COEF[c] + shock + rng.gauss(0, 0.5))
            dys.append(d)
            cns.append(c)
unc = INF.uncertainty(vals, days=dys, coins=cns, B=800, seed=3)
check("both intervals are present",
      unc["ci_lo_day"] is not None and unc["ci_hi_day"] is not None
      and unc["ci_lo_coin"] is not None and unc["ci_hi_coin"] is not None,
      str(unc))
check("the cluster counts are reported",
      unc["clusters"]["day"] == 40 and unc["clusters"]["coin"] == 5)
check("the coin-cluster interval is wider than the day-cluster interval",
      (unc["ci_hi_coin"] - unc["ci_lo_coin"]) > (unc["ci_hi_day"] - unc["ci_lo_day"]),
      f"coin={unc['ci_hi_coin'] - unc['ci_lo_coin']:.4f} "
      f"day={unc['ci_hi_day'] - unc['ci_lo_day']:.4f}")
unc2 = INF.uncertainty([1.0, 2.0, 3.0], days=[0, 0, 0], coins=["A", "A", "A"],
                       B=200, seed=1)
check("fewer than 3 clusters gives None intervals with a reason",
      unc2["ci_lo_day"] is None and unc2["ci_hi_day"] is None
      and unc2["ci_lo_coin"] is None and unc2["ci_hi_coin"] is None
      and unc2.get("reasons"), str(unc2.get("reasons")))
unc3 = INF.uncertainty([1.0, 2.0, 3.0, 4.0], days=[0, 1, 2, 3],
                        coins=["A", "B", "C", "A"], B=200, seed=1)
check("with only 3 coins the coin interval is still reported",
      unc3["ci_lo_coin"] is not None and unc3["clusters"]["coin"] == 3)
unc_flat = INF.uncertainty([1.0] * 8, days=[0, 1, 2, 3, 4, 5, 6, 7],
                            coins=["A", "B", "C", "A", "A", "B", "C", "A"],
                            B=200, seed=1)
check("a degenerate (zero-width) interval is suppressed with a reason",
      unc_flat["ci_lo_day"] is None and unc_flat["ci_hi_day"] is None
      and "day-interval-degenerate" in unc_flat["reasons"]
      and "coin-interval-degenerate" in unc_flat["reasons"],
      str(unc_flat["reasons"]))
check("a suppressed interval takes its statistic with it",
      unc_flat["t_day"] is None and unc_flat["t_coin"] is None, str(unc_flat))
unc_few = INF.uncertainty([1.0, 2.0, 3.0], days=[0, 0, 1],
                           coins=["A", "A", "A"], B=200, seed=1)
check("too few clusters takes the statistic with it as well",
      unc_few["t_day"] is None and unc_few["t_coin"] is None, str(unc_few))

# A None day label is a MISSING label, not a day: the labels do not align
# with the values, so they are treated like "no day labels".  The day
# bootstrap SORTS the day labels, so a mixed None/str list used to raise a
# TypeError out of the function.
unc_none_day = INF.uncertainty([1, 2, 3, 4], days=["a", None, "b", "c"])
check("a None day label never raises (it is a missing label, not a day)",
      isinstance(unc_none_day, dict), str(unc_none_day))
check("a None day label means no day interval",
      unc_none_day["ci_lo_day"] is None and unc_none_day["ci_hi_day"] is None
      and unc_none_day["t_day"] is None, str(unc_none_day))
check("the missing day label is reported as no-day-labels",
      "no-day-labels" in unc_none_day["reasons"],
      str(unc_none_day["reasons"]))
check("no day cluster is counted for a missing label",
      unc_none_day["clusters"]["day"] == 0, str(unc_none_day["clusters"]))
unc_none_coin = INF.uncertainty([1, 2, 3, 4], days=["a", "b", "c", "d"],
                                 coins=["A", None, "B", "C"])
check("a None coin label never raises either",
      isinstance(unc_none_coin, dict), str(unc_none_coin))
check("a None coin label means no coin interval",
      unc_none_coin["ci_lo_coin"] is None and unc_none_coin["ci_hi_coin"] is None
      and unc_none_coin["t_coin"] is None, str(unc_none_coin))
check("the missing coin label is reported as no-coin-labels",
      "no-coin-labels" in unc_none_coin["reasons"],
      str(unc_none_coin["reasons"]))

# --------------------------------------------------------------------------
print("\n=== 9. baselines on the same event set ===")
b_rng = random.Random(11)
b_events = []
for i in range(60):
    ts = T0 + i * 4 * 3600
    hit = b_rng.random() < 0.55
    b_events.append(mk_event(coin="AAA" if i % 2 == 0 else "BBB", ts=ts,
                              kind=0 if hit else 1,
                              gross=0.01 if hit else -0.01, offset=2))
b1 = INF.baselines(b_events, RULE, GEOM, WINDOW, placebos=50, seed=7)
b2 = INF.baselines(b_events, RULE, GEOM, WINDOW, placebos=50, seed=7)
check("the placebo distribution is reproducible under a fixed seed",
      b1["matched_placebo"]["mean"] == b2["matched_placebo"]["mean"]
      and b1["matched_placebo"]["p05"] == b2["matched_placebo"]["p05"])
check("the placebo percentiles are ordered",
      b1["matched_placebo"]["p05"] <= b1["matched_placebo"]["p95"])
check("the placebo distribution is not degenerate",
      b1["matched_placebo"]["sd"] > 0, str(b1["matched_placebo"]))
check("every baseline carries n and a denominator label",
      all("n" in b1[k] and b1[k].get("denominator")
          for k in ("unconditional", "rule_without_features")))
check("the unconditional baseline is computed on the same event set",
      b1["unconditional"]["n"] == len(b_events)
      and b1["rule_without_features"]["n"] == len(b_events))
check("the placebo reports the observed value and the extreme fraction",
      b1["matched_placebo"]["observed"] is not None
      and 0.0 <= b1["matched_placebo"]["p_value"] <= 1.0,
      str(b1["matched_placebo"]))
check("the placebo p-value carries the (cnt+1)/(B+1) correction",
      abs(b1["matched_placebo"]["p_value"]
          - (b1["matched_placebo"]["n_extreme"] + 1)
          / (b1["matched_placebo"]["n_placebos"] + 1)) < 1e-12
      and b1["matched_placebo"]["p_value"] > 0.0,
      str(b1["matched_placebo"]))
check("the placebo denominator is the donors actually used",
      0 < b1["matched_placebo"]["n_per_placebo"] <= len(b_events)
      and b1["matched_placebo"]["n_per_placebo_min"] >= 1,
      str(b1["matched_placebo"]["n_per_placebo"]))

# The baselines and the outcome layer must use the SAME event set: an
# event with no cost model is not a trade in either of them.
mixed = list(b_events) + [mk_event(coin="AAA", ts=T0 + 90 * 4 * 3600,
                                   kind=0, gross=0.01, offset=2, qv24=5e5)]
b_mixed = INF.baselines(mixed, RULE, GEOM, WINDOW, placebos=20, seed=7)
r_mixed = CO.resolve_competing(mixed, RULE, GEOM, WINDOW, DEFAULT)
check("the baselines and the outcome layer use the same event set",
      b_mixed["n_selected"] == r_mixed["n_selected"]
      and b_mixed["n_untradeable"] == r_mixed["n_untradeable"]
      == 1, str((b_mixed["n_selected"], r_mixed["n_selected"],
                  b_mixed["n_untradeable"], r_mixed["n_untradeable"])))

# --------------------------------------------------------------------------
print("\n=== 10. family-level multiple testing ===")
fam = [{"id": "a", "p": 0.001, "n": 500, "metric": "mean_r"},
       {"id": "b", "p": 0.02, "n": 500, "metric": "mean_r"},
       {"id": "c", "p": 0.30, "n": 500, "metric": "mean_r"}]
hol = FT.correct(fam, method="holm", alpha=0.05)
check("the family size is reported", hol["family_size"] == 3)
check("every adjusted p is at least its raw p",
      all(m["p_adj"] >= m["p"] - 1e-15 for m in hol["members"]), str(hol))
check("Holm adjustment is monotone in the raw p",
      [m["p_adj"] for m in hol["members"]] ==
      sorted([m["p_adj"] for m in hol["members"]]))
check("Holm rejects the two strongest members at alpha=0.05",
      hol["n_rejected"] == 2 and hol["members"][0]["rejected"]
      and hol["members"][1]["rejected"] and not hol["members"][2]["rejected"],
      str(hol["members"]))
one = FT.correct([{"id": "solo", "p": 0.031, "n": 10, "metric": "x"}],
                 method="holm", alpha=0.05)
check("a family of one returns that member's own p-value",
      abs(one["members"][0]["p_adj"] - 0.031) < 1e-15
      and abs(one["family_p"] - 0.031) < 1e-15, str(one))
empty = FT.correct([], method="holm")
check("an empty family is refused with a reason",
      (not empty.get("ok")) and empty.get("reason"), str(empty))
big = FT.correct(fam + [{"id": "d", "p": 0.9, "n": 500, "metric": "mean_r"}],
                 method="holm", alpha=0.05)
check("enlarging the family never lowers an existing member's adjusted p",
      all(bm["p_adj"] >= hm["p_adj"] - 1e-15
          for bm, hm in zip(big["members"], hol["members"])), str(big))

rng2 = random.Random(9)
mvals, mdays = [], []
for d in range(30):
    for _ in range(6):
        mvals.append(rng2.gauss(0.1, 1.0))
        mdays.append(d)
boot_fam = [{"id": "x", "p": 0.01, "n": len(mvals), "metric": "mean_r",
             "values": mvals, "days": mdays},
            {"id": "y", "p": 0.4, "n": len(mvals), "metric": "mean_r",
             "values": [v - 0.3 for v in mvals], "days": mdays}]
bt1 = FT.correct(boot_fam, method="bootstrap", B=400, seed=5)
bt2 = FT.correct(boot_fam, method="bootstrap", B=400, seed=5)
check("the bootstrap family correction is reproducible",
      bt1["family_p"] == bt2["family_p"], str(bt1))
check("the bootstrap reports n_bootstrap and a family p in (0, 1]",
      bt1["n_bootstrap"] == 400 and 0.0 < bt1["family_p"] <= 1.0, str(bt1))
bt3 = FT.correct(boot_fam + [{"id": "z", "p": 0.9, "n": len(mvals),
                               "metric": "mean_r", "values": mvals,
                               "days": mdays}],
                 method="bootstrap", B=400, seed=5)
check("enlarging the family does not lower the bootstrap family p",
      bt3["family_p"] >= bt1["family_p"] - 1e-15,
      f"{bt3['family_p']} vs {bt1['family_p']}")
check("every adjusted p is at least its raw p in the bootstrap method too",
      all(m["p_adj"] >= m["p"] - 1e-15 for m in bt1["members"]), str(bt1))
b1s = FT.correct([{"id": "solo", "p": 0.031, "n": 10, "metric": "x"}],
                 method="bootstrap")
check("a family of one returns that member's own p-value (bootstrap)",
      abs(b1s["family_p"] - 0.031) < 1e-15, str(b1s))

# --------------------------------------------------------------------------
print("\n=== 11. all five causes at once, and the net denominator ===")
mix = [
    mk_event(ts=T0, kind=0, gross=0.01, offset=2),            # TP
    mk_event(ts=T0 + 20000, kind=1, gross=-0.01, offset=2),   # SL
    mk_event(ts=T0 + 40000, kind=2, gross=0.002, offset=47),  # TIME
    mk_event(ts=T0 + 50000, kind=0, gross=0.01, offset=2),    # INVALIDATED
    mk_event(ts=T0 + 80000, kind=0, gross=0.01, offset=2),    # CENSORED
]
res_mix = CO.resolve_competing(mix, RULE, GEOM, WINDOW, DEFAULT,
                               invalidation=lambda ev: ev.ts == T0 + 50000,
                               sample_end_ts=T0 + 70000)
check("all five causes are present and exclusive",
      res_mix["causes"] == {"TP": 1, "SL": 1, "TIME": 1, "INVALIDATED": 1,
                            "CENSORED": 1}, str(res_mix["causes"]))
check("the five causes still sum to the denominator",
      sum(res_mix["causes"].values()) == res_mix["n_selected"] == 5)
check("the resolved denominator excludes the censored event",
      res_mix["n_resolved"] == 4 and res_mix["n_censored"] == 1
      and abs(res_mix["coverage"] - 4 / 5) < 1e-12)
check("the invalidated event is reported separately",
      res_mix["n_invalidated"] == 1)

# One event with attributable funding, one without: the net denominator
# must be explicit.
pair = [mk_event(coin="AAA", ts=T0, side=1, kind=2, gross=0.01, offset=47),
        mk_event(coin="NODATA", ts=T0 + 300, side=1, kind=2, gross=0.01,
                 offset=47)]
res_pair = CO.resolve_competing(pair, RULE_CONT, GEOM, WINDOW, DEFAULT,
                                settlements_dir=SETTLE_DIR)
check("n_net_evaluable is the net number's own denominator",
      res_pair["n_selected"] == 2 and res_pair["n_net_evaluable"] == 1,
      str(res_pair["n_net_evaluable"]))
net_dist = CO.outcome_distribution(res_pair["outcomes"], key="net_r")
check("the net distribution names its missing count",
      net_dist["n"] == 1 and net_dist["n_missing"] == 1, str(net_dist))
fric_dist = CO.outcome_distribution(res_pair["outcomes"], key="friction_net_r")
check("the friction-only view covers every event that has a gross",
      fric_dist["n"] == 2, str(fric_dist))

# A minimal hand-built event (scalar label, no exit offset) still resolves.
minimal = SimpleNamespace(coin="AAA", ts=T0, day=T0 / 86400.0, fam="B",
                          param=5.0, side=1, qv24=5e6, sigma=0.002,
                          gross=0.01, kind="TP")
res_min = CO.resolve_competing([minimal], RULE_CONT, GEOM, WINDOW, DEFAULT,
                               settlements_dir=SETTLE_DIR)
check("a minimal event with a scalar label resolves as TP",
      res_min["causes"]["TP"] == 1
      and res_min["outcomes"][0].gross_log == 0.01, str(res_min["causes"]))
check("an event without a stored exit offset falls back to the horizon",
      res_min["outcomes"][0].exit_ts == T0 + (48 + 1) * BAR_S,
      str(res_min["outcomes"][0].exit_ts))

# --------------------------------------------------------------------------
print("\n=== 12. cumulative incidence by cause (stats.aalen_johansen) ===")
inc_outs = [
    mk_outcome(cause="TP", ts=T0, gross=0.01),                    # exits at bar 1
    mk_outcome(cause="SL", ts=T0, gross=-0.01),                   # exits at bar 1
    mk_outcome(cause="TIME", ts=T0, gross=0.0),                   # exits at the horizon
    CO.Outcome(event_id="c", coin="AAA", ts=T0, side=1, cause="CENSORED",
               exit_ts=None, gross_log=None, friction=None, funding=None,
               net_r=None, dd=None, reason="censored:end-of-sample"),
]
inc = CO.cause_incidence(inc_outs, horizon_bars=10)
check("the incidence reports every competing cause",
      set(inc) == set(CO.INCIDENCE_CAUSES), str(sorted(inc)))
check("the incidence at the horizon is the hand calculation",
      abs(inc["TP"][-1][1] - 0.25) < 1e-9
      and abs(inc["SL"][-1][1] - 0.25) < 1e-9
      and abs(inc["TIME"][-1][1] - 0.25) < 1e-9, str(inc))
check("the censored unit stays at risk (it is censored, not failed)",
      abs(sum(v[-1][1] for v in inc.values()) - 0.75) < 1e-9,
      str({k: v[-1][1] for k, v in inc.items()}))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)