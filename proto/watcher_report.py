"""Watcher diagnostics: research-only shadow report.

Read-only: every number traces to a watcher_setup / watcher_eval row or is
labeled an assumption. Unit-test success is not edge; no profitability claim
without out-of-sample evidence. Run: python3 -m proto.watcher_report
"""

import argparse
import datetime
import sqlite3

from . import watcher as _w

RESEARCH_BANNER = ("WATCHER (research, shadow-only -- scanner ranking/flags "
                   "untouched; this app places no orders)")

ASSUMPTIONS = (
    "ASSUMPTION taker 0.0005/side: fee tier unverified, labeled estimate",
    "ASSUMPTION slip 0.0005: labeled estimate",
    "ASSUMPTION spread: contemporaneous top-of-book snapshot, labeled",
    "ASSUMPTION funding projection: mexc.funding_rate() now held flat over "
    "scheduled settlements in [entry, entry+N]; past settlements use known "
    "snapshot rates; no historical archive, never backfilled (limitation)",
    "ASSUMPTION settlement schedule: 8h default; venue-metadata override versioned",
    "ASSUMPTION price_source=perp_trades proxy: stop/target touches on perp "
    "H/L, no mark feed (documented limitation)",
    "ASSUMPTION correlation clusters: |rho|>0.7 on trailing-7d 1h residuals; "
    "same-coin grouping below is a display proxy, not measured rho",
)

_NO_EDGE_NOTE = ("Unit-test success is not edge. No profitability claim "
                 "without holdout after-cost expectancy + shadow agreement.")


def _has_tables(conn):
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    return "watcher_setup" in tables and "watcher_eval" in tables


def summarize_states(conn):
    """Counts by (state, substate) + monitoring + shadow picks. Zero shape
    when the watcher tables are absent (read-only: never created here)."""
    out = {"total": 0, "by_state": {}, "monitoring": {"ok": 0, "unavailable": 0},
           "selected": 0}
    if conn is None or not _has_tables(conn):
        return out
    for sid_state, sub, mon, sel in conn.execute(
            "SELECT state, substate, monitoring, shadow_selected "
            "FROM watcher_setup").fetchall():
        out["total"] += 1
        key = f"{sid_state}/{sub}" if sub else sid_state
        out["by_state"][key] = out["by_state"].get(key, 0) + 1
        out["monitoring"]["unavailable" if mon == "unavailable" else "ok"] += 1
        out["selected"] += 1 if sel else 0
    return out


def coverage_by_coin_day(conn):
    """Eval-bar coverage per coin x UTC day: ok vs unavailable bars.

    Missing/stale inputs surface as unavailable (never defaulted); setups
    with no eval rows yet are listed with evaluated=0 so silence is visible.
    """
    rows = []
    if conn is None or not _has_tables(conn):
        return rows
    acc = {}
    for coin, bar_ts, mon in conn.execute(
            "SELECT s.coin, e.bar_ts, e.monitoring FROM watcher_eval e "
            "JOIN watcher_setup s ON s.id = e.setup_id").fetchall():
        try:
            day = datetime.datetime.fromtimestamp(
                int(bar_ts), tz=datetime.timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OverflowError, OSError):
            day = "?"
        cell = acc.setdefault((coin, day), {"ok": 0, "unavailable": 0})
        cell["unavailable" if mon == "unavailable" else "ok"] += 1
    seen = set()
    for coin, _cid in conn.execute("SELECT coin, id FROM watcher_setup").fetchall():
        seen.add(coin)
        if not any(c == coin for (c, _day) in acc):
            # Setup emitted but never evaluated: silence stays visible.
            acc[(coin, "?")] = {"ok": 0, "unavailable": 0}
    for (coin, day), cell in sorted(acc.items()):
        rows.append({"coin": coin, "day": day, "ok_bars": cell["ok"],
                     "unavailable_bars": cell["unavailable"]})
    return rows


def _latest_eval(conn):
    latest = {}
    for sid, bar_ts, gross in conn.execute(
            "SELECT setup_id, bar_ts, ret_gross FROM watcher_eval "
            "ORDER BY bar_ts").fetchall():
        latest[sid] = gross
    return latest


def net_of(ret_gross, cost_total, funding_state):
    """Net column: gross minus the floored composer when funding is known;
    None (n/a) when funding is unavailable or inputs are missing."""
    if ret_gross is None or cost_total is None:
        return None
    if funding_state == "unavailable":
        return None
    return ret_gross - cost_total


def gross_net_rows(conn):
    """One row per setup: id, rule, coin, dir, state, ret_gross (latest eval),
    ret_net (signed composer; None when funding unavailable), EV_R/p-hat-LB,
    shadow flag, funding state."""
    rows = []
    if conn is None or not _has_tables(conn):
        return rows
    latest = _latest_eval(conn)
    for (sid, rule, coin, direction, state, sub, cost, fund, ev, p_lb,
         sel) in conn.execute(
            "SELECT id, rule_id, coin, direction, state, substate, cost_total,"
            " funding_state, ev_r, p_lb, shadow_selected FROM watcher_setup "
            "ORDER BY coin, src_bar_ts").fetchall():
        gross = latest.get(sid)
        rows.append({"id": sid, "rule": rule, "coin": coin,
                     "direction": direction,
                     "state": f"{state}/{sub}" if sub else state,
                     "ret_gross": gross,
                     "ret_net": net_of(gross, cost, fund),
                     "ev_r": ev, "p_lb": p_lb,
                     "shadow_selected": bool(sel), "funding": fund})
    return rows


def neff_diagnostic(n, rho_bar):
    """Effective independent count N/(1+(N-1)*rho_bar); None on bad input."""
    try:
        n, rho_bar = int(n), float(rho_bar)
    except (TypeError, ValueError):
        return None
    if n < 1 or not 0.0 <= rho_bar < 1.0:
        return None
    return n / (1.0 + (n - 1) * rho_bar)


def correlated_opens(conn, clusters=None, rho=None):
    """Groups of simultaneously-active setups that must not be read as
    independent bets. Default grouping is same-coin (labeled proxy); pass
    clusters={setup_id: label} for measured |rho|>0.7 sets and rho={label:
    mean_rho} for the N_eff diagnostic. Only groups with >1 open are listed.
    """
    groups = []
    if conn is None or not _has_tables(conn):
        return groups
    clusters = clusters or {}
    rho = rho or {}
    buckets = {}
    for sid, coin, sel in conn.execute(
            "SELECT id, coin, shadow_selected FROM watcher_setup "
            "WHERE state='active' ORDER BY id").fetchall():
        key = clusters.get(sid, f"coin:{coin}")
        buckets.setdefault(key, {"ids": [], "selected": []})
        buckets[key]["ids"].append(sid)
        if sel:
            buckets[key]["selected"].append(sid)
    for key in sorted(buckets):
        b = buckets[key]
        if len(b["ids"]) < 2:
            continue
        measured = key in rho or not str(key).startswith("coin:")
        groups.append({"group": key, "n": len(b["ids"]), "ids": b["ids"],
                       "shadow_selected": b["selected"],
                       "measured_rho": rho.get(key),
                       "n_eff": (neff_diagnostic(len(b["ids"]), rho[key])
                                 if key in rho else None),
                       "proxy": None if measured else
                       "same-coin display proxy, not measured rho"})
    return groups


def _fmt_pct(x):
    return "n/a" if x is None else f"{100.0 * x:+.2f}%"


def format_watcher_report(conn, clusters=None, rho=None, paused=None,
                          disabled=()):
    """Full text report: banner, flags, states, coverage, gross/net,
    correlated opens, assumptions."""
    if paused is None:
        paused = _w.WATCHER_PAUSED
    L = [RESEARCH_BANNER,
         f"flags: paused={bool(paused)} "
         f"disabled={','.join(disabled) if disabled else 'none'} "
         f"(K={_w.SELECTOR_K}/day, EV_hurdle={_w.EV_HURDLE}, "
         f"cluster_cap={_w.CLUSTER_CAP} at |rho|>{_w.CLUSTER_RHO})",
         ""]
    L.append("STATES")
    st = summarize_states(conn)
    if not st["total"]:
        L.append("  no watcher setups recorded")
    else:
        for key in sorted(st["by_state"]):
            L.append(f"  {key:<18}{st['by_state'][key]:>6}")
        mon = st["monitoring"]
        L.append(f"  monitoring ok={mon.get('ok', 0)} "
                 f"unavailable={mon.get('unavailable', 0)} "
                 f"shadow_selected={st['selected']}/{st['total']}")
    L.append("")
    L.append("COVERAGE input x coin x date (eval bars; unavailable = "
             "missing/stale, never defaulted)")
    cov = coverage_by_coin_day(conn)
    if not cov:
        L.append("  no eval bars recorded")
    else:
        L.append(f"  {'coin':<10}{'day':<12}{'ok':>8}{'unavail':>8}")
        for r in cov:
            L.append(f"  {r['coin']:<10}{r['day']:<12}{r['ok_bars']:>8}"
                     f"{r['unavailable_bars']:>8}")
    L.append("")
    L.append("GROSS/NET per setup (net = gross - cost composer; "
             "n/a when funding unavailable)")
    L.append(f"  {'id':<34}{'state':<18}{'gross':>9}{'net':>9}"
             f"{'ev_r':>8}  flags")
    for r in gross_net_rows(conn):
        flags = ("SHADOW" if r["shadow_selected"] else "-") + (
            "/FUND?" if r["funding"] == "unavailable" else "")
        ev = "n/a" if r["ev_r"] is None else f"{r['ev_r']:+.2f}"
        L.append(f"  {r['id']:<34}{r['state']:<18}"
                 f"{_fmt_pct(r['ret_gross']):>9}{_fmt_pct(r['ret_net']):>9}"
                 f"{ev:>8}  {flags}")
    if not gross_net_rows(conn):
        L.append("  (none)")
    L.append("")
    L.append("CORRELATED OPENS (>1 active in group; not independent bets)")
    groups = correlated_opens(conn, clusters, rho)
    if not groups:
        L.append("  none")
    else:
        for g in groups:
            extra = (f" n_eff={g['n_eff']:.2f} (rho-bar={g['measured_rho']})"
                     if g["n_eff"] is not None else "")
            if g["proxy"]:
                extra += f" [{g['proxy']}]"
            L.append(f"  {g['group']}: n={g['n']} "
                     f"shadow={len(g['shadow_selected'])}{extra}")
            for sid in g["ids"]:
                L.append(f"    {sid}")
    L.append("")
    L.append("ASSUMPTIONS (every estimate labeled; venue values trace to "
             "logged rows)")
    for a in ASSUMPTIONS:
        L.append(f"  - {a}")
    L.append(_NO_EDGE_NOTE)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Watcher shadow report "
                                 "(research-only, read-only)")
    ap.add_argument("--db", default="data/signals.db")
    ap.add_argument("--paused", action="store_true")
    ap.add_argument("--disable", action="append", default=[],
                    help="per-trigger rule_id to silence (repeatable)")
    args = ap.parse_args()
    try:
        conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    except sqlite3.OperationalError as e:
        print(f"cannot open {args.db} read-only: {e}")
        raise SystemExit(1)
    try:
        print(format_watcher_report(conn, paused=args.paused,
                                    disabled=tuple(args.disable)))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
