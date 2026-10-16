"""Research harness: candidate events -> triple-barrier labels -> chronological
evaluation with multiplicity control.

    python3 -m proto.quant.research --data data/bars --out data/quant_research

Stages (the discipline is the point):
  1. collect_events: every candidate in every coin, labeled for ALL grid
     geometries and BOTH sides (so policy/geometry choices never re-touch the
     bars and can be audited).
  2. dev: all pre-declared configs evaluated on the dev window; a max-t test
     says whether the BEST of them beats chance given how many were tried.
  3. val: only the single dev-selected config is evaluated (confirmation).
  4. holdout: evaluated at most ONCE, only with --holdout, for the config the
     val stage kept; a lock file refuses any second look.

Nothing here tunes on validation or holdout data.
"""
import argparse
import array
import json
import math
import os
import sys
import time

from . import barrier as B
from . import stats as S
from .bars import load_coin, btc_returns, list_coins, contiguous_runs
from .config import (DEFAULT, BARS_PER_DAY, BAR_S, grid_geometries,
                     GRID_RVOL, RuleParams)
from .stream import FeatureStream, Cusum

SHOCK_HS = (5.0, 8.0)
MAX_H = max(g.horizon_bars for g in grid_geometries())


class Event:
    __slots__ = ("coin", "ts", "day", "fam", "param", "side", "qv24", "sigma",
                 "rvol", "z1h", "z4h", "btc7d", "gross", "kind")


def _btc7d_map(btc):
    ts, c = btc["ts"], btc["c"]
    out = {}
    lag = 7 * BARS_PER_DAY
    for i in range(lag, len(ts)):
        if ts[i] - ts[i - lag] == lag * BAR_S:
            out[ts[i]] = math.log(c[i] / c[i - lag])
    return out


def collect_events(data_dir, cfg=DEFAULT, coins=None, progress=False):
    """Returns (events, meta).  meta carries archive start/end for splits."""
    btc = load_coin(data_dir, "BTC")
    if btc is None:
        raise SystemExit("BTC bars are required as the market factor")
    br = btc_returns(btc)
    b7 = _btc7d_map(btc)
    geoms = grid_geometries()
    t_min = min(btc["ts"])
    t_max = max(btc["ts"])
    events = []
    names = coins or [c for c in list_coins(data_dir) if c != "BTC"]
    rho_min = min(GRID_RVOL)
    for ci, coin in enumerate(names):
        bars = load_coin(data_dir, coin)
        if not bars:
            continue
        o, h, l, c, amt, ts = (bars[k] for k in ("o", "h", "l", "c", "amt", "ts"))
        t_min = min(t_min, ts[0])
        for (a, z) in contiguous_runs(ts):
            st = FeatureStream(cfg.feat)
            cus = {hh: Cusum(hh, cfg.feat.cusum_k, cfg.feat.cusum_cooldown)
                   for hh in SHOCK_HS}
            for i in range(a, z):
                try:
                    snap = st.update(ts[i], o[i], h[i], l[i], c[i], amt[i],
                                     br.get(ts[i], 0.0))
                except ValueError:
                    st = FeatureStream(cfg.feat)      # invalid bar: restart
                    continue
                if snap is None:
                    continue
                cands = []
                if (snap.up_start or snap.dn_start) and snap.rvol is not None \
                        and snap.rvol >= rho_min:
                    cands.append(("B", snap.rvol, 1 if snap.up_start else -1))
                for hh, cu in cus.items():
                    s = cu.update(snap.n, snap.z)
                    if s:
                        cands.append(("S", hh, s))
                if not cands or snap.qv24 < cfg.cost.min_qv24:
                    continue
                if snap.sigma <= 1e-6:
                    continue
                path = B.make_path(o, h, l, c, i, MAX_H)
                if path is None:
                    continue
                gross = array.array("d")
                kind = array.array("b")
                for g in geoms:
                    dd = B.stop_distance(snap.sigma, g.horizon_bars, g.k_stop)
                    for side in (1, -1):
                        gr, k, _ = B.label_path(path, side, dd, g.horizon_bars,
                                                g.r_tp)
                        gross.append(gr)
                        kind.append({"TP": 0, "SL": 1, "TIME": 2}[k])
                for fam, param, side in cands:
                    ev = Event()
                    ev.coin, ev.ts = coin, snap.ts
                    ev.day = (snap.ts - 0) / 86400.0
                    ev.fam, ev.param, ev.side = fam, param, side
                    ev.qv24, ev.sigma, ev.rvol = snap.qv24, snap.sigma, snap.rvol
                    ev.z1h, ev.z4h = snap.z1h, snap.z4h
                    ev.btc7d = b7.get(snap.ts)
                    ev.gross, ev.kind = gross, kind
                    events.append(ev)
        if progress and (ci + 1) % 10 == 0:
            print(f"  collected {ci + 1}/{len(names)} coins, "
                  f"{len(events)} events", file=sys.stderr, flush=True)
    meta = {"t_min": t_min, "t_max": t_max,
            "n_days": (t_max - t_min) / 86400.0}
    for ev in events:                        # day index relative to archive start
        ev.day = (ev.ts - t_min) / 86400.0
    return events, meta


def rule_variants():
    out = []
    for pol in ("fade", "cont"):
        for rho in GRID_RVOL:
            out.append(RuleParams("breakout_start", pol, rvol_min=rho))
        for hh in SHOCK_HS:
            out.append(RuleParams("residual_shock", pol, cusum_h=hh))
    return out


def _select(events, rule, lo, hi):
    fam = "B" if rule.family == "breakout_start" else "S"
    out = []
    for ev in events:
        if not (lo <= ev.day < hi) or ev.fam != fam:
            continue
        if fam == "B" and ev.param < rule.rvol_min:
            continue
        if fam == "S" and ev.param != rule.cusum_h:
            continue
        out.append(ev)
    return out


def eval_config(events, rule, geom_idx, geom, window, cfg=DEFAULT,
                slip_mult=1.0, fee_mult=1.0):
    """Net R per event for one (rule, geometry) in a day window."""
    lo, hi = window
    sel = _select(events, rule, lo, hi)
    vals, days, kinds, coins = [], [], [0, 0, 0], set()
    for ev in sel:
        side = ev.side if rule.policy == "cont" else -ev.side
        k = geom_idx * 2 + (0 if side > 0 else 1)
        cost = cfg.cost.round_trip(ev.qv24, slip_mult, fee_mult)
        if cost is None:
            continue
        dd = B.stop_distance(ev.sigma, geom.horizon_bars, geom.k_stop)
        vals.append(B.net_r(ev.gross[k], dd, cost))
        days.append(int(ev.day))
        kinds[ev.kind[k]] += 1
        coins.add(ev.coin)
    n = len(vals)
    return {"n": n, "values": vals, "days": days, "coins": len(coins),
            "tp": kinds[0] / n if n else float("nan"),
            "sl": kinds[1] / n if n else float("nan"),
            "time": kinds[2] / n if n else float("nan")}


def summarize(res, B_=1500):
    m, lo, hi, nd = S.day_cluster_mean(res["values"], res["days"], B=B_)
    return {"n": res["n"], "days": nd, "coins": res["coins"], "mean_r": m,
            "ci_lo": lo, "ci_hi": hi, "tp": res["tp"], "sl": res["sl"],
            "time": res["time"],
            "t": S.day_cluster_t(res["values"], res["days"])}


def rule_name(r):
    p = f"rvol>={r.rvol_min:g}" if r.family == "breakout_start" else f"cusum_h={r.cusum_h:g}"
    return f"{r.family}[{p}]/{r.policy}"


def run_stage_dev_val(events, meta, cfg=DEFAULT):
    bounds = cfg.splits.bounds(meta["n_days"])
    geoms = grid_geometries()
    rules = rule_variants()
    configs, vals, days = [], [], []
    for r in rules:
        for gi, g in enumerate(geoms):
            res = eval_config(events, r, gi, g, bounds["dev"], cfg)
            if res["n"] < 30:
                continue
            configs.append((r, gi, g, res))
            vals.append(res["values"])
            days.append(res["days"])
    best, best_t, p_spa = S.spa_max_t(vals, days, B=600)
    ranked = sorted(range(len(configs)), key=lambda k: -S.day_cluster_t(vals[k], days[k]))
    sel_r, sel_gi, sel_g, _ = configs[best]
    dev_sum = summarize(configs[best][3])
    val_res = eval_config(events, sel_r, sel_gi, sel_g, bounds["val"], cfg)
    val_sum = summarize(val_res)
    allmeans = sorted(sum(c[3]["values"]) / c[3]["n"] for c in configs)
    return {
        "bounds": bounds, "n_configs": len(configs),
        "spa": {"best_t": best_t, "p_value": p_spa},
        "selected": {"rule": rule_name(sel_r), "geometry": sel_g.key(),
                     "rule_obj": sel_r, "geom_idx": sel_gi, "geom": sel_g},
        "dev": dev_sum, "val": val_sum,
        "dev_config_means": {"min": allmeans[0], "median": allmeans[len(allmeans) // 2],
                             "max": allmeans[-1],
                             "share_positive": sum(1 for m in allmeans if m > 0) / len(allmeans)},
        "top5_dev": [(rule_name(configs[k][0]), configs[k][2].key(),
                      summarize(configs[k][3], 300)) for k in ranked[:5]],
        "configs": configs,
    }


def holdout_once(events, meta, selected, out_dir, cfg=DEFAULT):
    lock = os.path.join(out_dir, "holdout_evaluated.json")
    key = f"{selected['rule']}|{selected['geometry']}|{cfg.config_hash()}"
    if os.path.exists(lock):
        prev = json.load(open(lock))
        raise SystemExit("holdout already evaluated once (" + prev["key"] + "); "
                         "refusing a second look. Delete the lock only for a "
                         "genuinely new, newly collected holdout window.")
    bounds = cfg.splits.bounds(meta["n_days"])
    res = eval_config(events, selected["rule_obj"], selected["geom_idx"],
                      selected["geom"], bounds["holdout"], cfg)
    summ = summarize(res, 3000)
    r2 = eval_config(events, selected["rule_obj"], selected["geom_idx"],
                     selected["geom"], bounds["holdout"], cfg, slip_mult=2.0)
    summ2 = summarize(r2, 1500)
    os.makedirs(out_dir, exist_ok=True)
    json.dump({"key": key, "when": int(time.time()), "summary": summ,
               "slip2x": summ2}, open(lock, "w"))
    return summ, summ2


def sensitivity(events, selected, window, cfg=DEFAULT):
    out = {}
    for lab, sm, fm in (("base", 1.0, 1.0), ("slip x2", 2.0, 1.0),
                        ("fees only (slip 0)", 0.0, 1.0), ("slip x0.5", 0.5, 1.0)):
        r = eval_config(events, selected["rule_obj"], selected["geom_idx"],
                        selected["geom"], window, cfg, slip_mult=sm, fee_mult=fm)
        out[lab] = summarize(r, 500)
    return out


def geometry_table(events, rule, window, cfg=DEFAULT):
    """TP/SL/TIME shares vs the driftless null, per geometry - the TP-hit view."""
    rows = []
    for gi, g in enumerate(grid_geometries()):
        res = eval_config(events, rule, gi, g, window, cfg)
        if res["n"] < 30:
            continue
        s = summarize(res, 300)
        p0 = B.null_hit_prob(g.r_tp, 1.0)
        term = s["tp"] + s["sl"]
        rows.append({"geom": g.key(), "n": s["n"], "tp": s["tp"], "sl": s["sl"],
                     "time": s["time"],
                     "tp_given_terminal": s["tp"] / term if term > 0 else float("nan"),
                     "null_p0": p0, "net_r": s["mean_r"]})
    return rows


SCORER_GEOM = (288, 3.0, 3.0)      # pre-declared scorer target geometry (T, k_stop, r_tp)


def _scorer_rows(events, window, cfg, geom_key):
    gi, g = next((i, g) for i, g in enumerate(grid_geometries())
                 if (g.horizon_bars, g.k_stop, g.r_tp) == geom_key)
    X, y, days = [], [], []
    lo, hi = window
    for ev in events:
        if ev.fam != "B" or not (lo <= ev.day < hi):
            continue
        if None in (ev.rvol, ev.z1h, ev.z4h, ev.btc7d):
            continue
        cost = cfg.cost.round_trip(ev.qv24)
        if cost is None:
            continue
        s = ev.side
        side = -s                                    # fade the break
        k = gi * 2 + (0 if side > 0 else 1)
        dd = B.stop_distance(ev.sigma, g.horizon_bars, g.k_stop)
        y.append(B.net_r(ev.gross[k], dd, cost))
        X.append([math.log(max(ev.rvol, 1e-3)), s * ev.z1h, s * ev.z4h,
                  s * ev.btc7d / max(ev.sigma * math.sqrt(BARS_PER_DAY), 1e-6),
                  math.log(ev.qv24), math.log(ev.sigma)])
        days.append(int(ev.day))
    return X, y, days


def scorer_check(events, meta, cfg=DEFAULT, lam=200.0, geom_key=SCORER_GEOM):
    """Does a 6-feature ridge predict net R out of sample?  Train on dev only;
    report calibration on validation.  No threshold or feature is tuned on
    validation."""
    from .model import Ridge, bucket_table
    bounds = cfg.splits.bounds(meta["n_days"])
    Xd, yd, _ = _scorer_rows(events, bounds["dev"], cfg, geom_key)
    Xv, yv, dv = _scorer_rows(events, bounds["val"], cfg, geom_key)
    if len(Xd) < 200 or len(Xv) < 50:
        return {"ok": False, "reason": "too few events", "n_dev": len(Xd), "n_val": len(Xv)}
    m = Ridge(lam).fit(Xd, yd)
    pv = m.predict(Xv)
    slope = S.calibration_slope(pv, yv)
    tab = bucket_table(pv, yv, 3)
    idx = sorted(range(len(pv)), key=lambda i: pv[i])
    top = idx[2 * len(idx) // 3:]
    tv = [yv[i] for i in top]
    td = [dv[i] for i in top]
    mean, lo, hi, nd = S.day_cluster_mean(tv, td, B=1500)
    return {"ok": True, "n_dev": len(Xd), "n_val": len(Xv), "calibration_slope": slope,
            "buckets": tab, "top_tercile": {"n": len(tv), "mean_r": mean,
                                            "ci_lo": lo, "ci_hi": hi, "days": nd},
            "weights": dict(zip(["log_rvol", "z1h", "z4h", "btc7d", "log_qv24",
                                 "log_sigma"], m.w))}


def fmt(s):
    return (f"n={s['n']} days={s['days']} coins={s['coins']} "
            f"netR={s['mean_r']:+.4f} [{s['ci_lo']:+.4f},{s['ci_hi']:+.4f}] "
            f"TP/SL/TIME={s['tp']:.2f}/{s['sl']:.2f}/{s['time']:.2f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default="data/bars")
    ap.add_argument("--out", default="data/quant_research")
    ap.add_argument("--coins", default=None, help="comma list (default: all)")
    ap.add_argument("--holdout", action="store_true",
                    help="evaluate the untouched holdout ONCE (irreversible)")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    coins = a.coins.split(",") if a.coins else None
    t0 = time.time()
    events, meta = collect_events(a.data, DEFAULT, coins, progress=True)
    print(f"{len(events)} events from the archive in {time.time() - t0:.0f}s; "
          f"{meta['n_days']:.1f} days", file=sys.stderr)
    r = run_stage_dev_val(events, meta)
    print(f"configs tested on dev: {r['n_configs']}")
    print(f"selected: {r['selected']['rule']} {r['selected']['geometry']}")
    print(f"max-t over all configs: t={r['spa']['best_t']:.2f} "
          f"p={r['spa']['p_value']:.3f}")
    print("dev :", fmt(r["dev"]))
    print("val :", fmt(r["val"]))
    from dataclasses import asdict
    sel = r["selected"]
    out = {"config_hash": DEFAULT.config_hash(), "meta": meta,
           "n_configs": r["n_configs"], "spa": r["spa"],
           "selected": {"rule": sel["rule"], "geometry": sel["geometry"],
                        "params": asdict(sel["rule_obj"]),
                        "geom": asdict(sel["geom"])},
           "dev": r["dev"], "val": r["val"],
           "dev_config_means": r["dev_config_means"], "top5_dev": r["top5_dev"]}
    if a.holdout:
        if not (r["val"]["mean_r"] > 0):
            print("holdout NOT spent: validation did not keep the dev-selected "
                  "config (mean net R <= 0). The untouched holdout stays "
                  "reserved for a config that survives validation.")
        else:
            h, h2 = holdout_once(events, meta, r["selected"], a.out)
            out["holdout"], out["holdout_slip2x"] = h, h2
            print("holdout:", fmt(h))
    json.dump(out, open(os.path.join(a.out, "research.json"), "w"), indent=2,
              default=float)


if __name__ == "__main__":
    main()
