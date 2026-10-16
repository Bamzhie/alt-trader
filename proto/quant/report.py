"""Measurement report for logged quant events.

Every figure states its denominator.  Shadow results are never described as
profit: the report prints net R after the ASSUMED costs, TP/SL/TIME shares
against the driftless null for the rule's own geometry, and cumulative
incidence that treats still-open events as censored (not as losses).
"""
import time

from . import barrier as B
from . import stats as S
from .config import BAR_S
from .registry import by_id
from . import gates as gatesmod


def rule_report(store, rule_id, cfg, promo_dir):
    spec = by_id().get(rule_id)
    events = store.all_events(rule_id)
    resolved = store.resolved_rows(rule_id)
    n_open = sum(1 for e in events if e["state"] == "EMITTED")
    out = {"rule_id": rule_id, "role": spec.role if spec else "?",
           "events": len(events), "open": n_open, "resolved": len(resolved)}
    promo = gatesmod.promotion_for(promo_dir, rule_id, cfg.config_hash())
    out["status"] = "ACTIONABLE" if promo else "RESEARCH (not actionable)"
    if resolved:
        vals = [r["net_r"] for r in resolved]
        days = [r["bar_ts"] // 86400 for r in resolved]
        m, lo, hi, nd = S.day_cluster_mean(vals, days, B=1500)
        k = {"TP": 0, "SL": 0, "TIME": 0}
        for r in resolved:
            k[r["kind"]] += 1
        n = len(resolved)
        out.update({"net_r": m, "ci_lo": lo, "ci_hi": hi, "days": nd,
                    "tp": k["TP"] / n, "sl": k["SL"] / n, "time": k["TIME"] / n,
                    "coins": len({r["coin"] for r in resolved})})
        if spec:
            a, b = spec.geometry.r_tp, 1.0
            term = k["TP"] + k["SL"]
            out["null_p0"] = B.null_hit_prob(a, b)
            out["tp_given_terminal"] = k["TP"] / term if term else None
            costs = [r["cost_frac"] / r["stop_frac"] for r in resolved]
            out["mean_cost_r"] = sum(costs) / len(costs)
            out["breakeven_p"] = B.breakeven_hit_prob(a, b, out["mean_cost_r"])
        # cumulative incidence: open events censored at their age
        now_bars = None
        times, causes = [], []
        code = {"SL": 1, "TP": 2, "TIME": 3}
        for r in resolved:
            times.append(r["exit_offset"])
            causes.append(code[r["kind"]])
        horizon = spec.geometry.horizon_bars if spec else 288
        aj = S.aalen_johansen(times, causes, 3, horizon)
        out["cuminc_final"] = {"SL": aj[1][-1][1] if aj[1] else 0.0,
                               "TP": aj[2][-1][1] if aj[2] else 0.0,
                               "TIME": aj[3][-1][1] if aj[3] else 0.0}
    sup = store.conn.execute(
        "SELECT reason, SUM(n) FROM quant_suppressed WHERE rule_id=? GROUP BY reason",
        (rule_id,)).fetchall()
    out["suppressed"] = {r[0]: r[1] for r in sup}
    return out


def build(store, cfg, promo_dir):
    ids = [r[0] for r in store.conn.execute(
        "SELECT DISTINCT rule_id FROM quant_event ORDER BY rule_id")]
    return {"config_hash": cfg.config_hash(),
            "paused": store.get_meta("auto_pause") == "1" or
                      store.get_meta("pause") == "1",
            "pause_reason": store.get_meta("auto_pause_reason", ""),
            "rules": [rule_report(store, rid, cfg, promo_dir) for rid in ids]}


def render(rep):
    L = [f"QUANT REPORT  config {rep['config_hash']}  "
         f"{'PAUSED: ' + rep['pause_reason'] if rep['paused'] else 'running'}",
         "Costs are ASSUMED (fee + tiered slippage); funding is not modeled; "
         "no fill model beyond 'next bar open'.", ""]
    if not rep["rules"]:
        L.append("No events logged yet. Silence is a valid output.")
    for r in rep["rules"]:
        L.append(f"{r['rule_id']}  [{r['role']}]  {r['status']}")
        L.append(f"  events {r['events']} (open {r['open']}, resolved {r['resolved']})")
        if "net_r" in r:
            L.append(f"  net R {r['net_r']:+.4f}  95% day-cluster CI "
                     f"[{r['ci_lo']:+.4f},{r['ci_hi']:+.4f}]  "
                     f"({r['days']} days, {r['coins']} coins, n={r['resolved']})")
            L.append(f"  TP/SL/TIME {r['tp']:.2f}/{r['sl']:.2f}/{r['time']:.2f}"
                     + (f"  | TP among terminal {r['tp_given_terminal']:.3f} vs "
                        f"driftless null {r['null_p0']:.3f}, cost-adjusted "
                        f"breakeven {r['breakeven_p']:.3f}"
                        if r.get("tp_given_terminal") is not None else ""))
        if r["suppressed"]:
            L.append("  suppressed: " + ", ".join(f"{k}={v}" for k, v in
                                                  sorted(r["suppressed"].items())))
        L.append("")
    return "\n".join(L)
