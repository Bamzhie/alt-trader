"""Signal success analysis: how flagged signals actually performed.

Win = resolved signed return_pct > 0 (moved in our favour past entry).
Loss = resolved and <= 0. Counts only FLAGGED rows (actionable + above
threshold at log time) inside the window — shadow rows are the denominator
for recall studies, not for hit-rate. Run: python3 -m proto.report
"""

import argparse
import time

HORIZONS = ("1h", "4h", "24h", "7d")


def signal_stats(store, hours=24):
    """Hit-rate stats for flagged signals logged in the last `hours`.

    {"window_hours", "signals" (flagged count),
     "by_horizon": {h: {"resolved", "wins", "losses", "pct_won", "avg_return"}},
     "overall": {... same ...}} — overall counts each resolved outcome row.
    """
    blank = {"resolved": 0, "wins": 0, "losses": 0,
             "pct_won": 0.0, "avg_return": 0.0}
    out = {"window_hours": hours, "signals": 0,
           "by_horizon": {h: dict(blank) for h in HORIZONS},
           "overall": dict(blank)}
    if store is None:
        return out
    since = time.time() - hours * 3600
    out["signals"] = store.conn.execute(
        "SELECT COUNT(*) FROM signal_log WHERE flagged=1 AND ts>?",
        (since,)).fetchone()[0]
    rows = store.conn.execute(
        "SELECT o.horizon, o.return_pct FROM outcome_log o"
        " JOIN signal_log s ON s.id = o.signal_id"
        " WHERE s.flagged=1 AND s.ts>?", (since,)).fetchall()
    acc = {h: [] for h in HORIZONS}
    for horizon, ret in rows:
        if horizon not in acc or ret is None:
            continue
        try:
            acc[horizon].append(float(ret))
        except (TypeError, ValueError):
            continue
    all_rets = []
    for h, rets in acc.items():
        all_rets.extend(rets)
        out["by_horizon"][h] = _summ(rets)
    out["overall"] = _summ(all_rets)
    return out


def _summ(rets):
    n = len(rets)
    if not n:
        return {"resolved": 0, "wins": 0, "losses": 0,
                "pct_won": 0.0, "avg_return": 0.0}
    wins = sum(1 for r in rets if r > 0)
    return {"resolved": n, "wins": wins, "losses": n - wins,
            "pct_won": 100.0 * wins / n,
            "avg_return": sum(rets) / n}


def format_report(stats):
    L = [f"FLAGGED SIGNALS · last {stats['window_hours']}h: "
         f"{stats['signals']} signals"]
    L.append(f"{'horizon':<8}{'resolved':>9}{'won':>6}{'lost':>6}"
             f"{'%won':>7}{'avg ret':>9}")
    for h in HORIZONS:
        s = stats["by_horizon"][h]
        L.append(f"{h:<8}{s['resolved']:>9}{s['wins']:>6}{s['losses']:>6}"
                 f"{s['pct_won']:>6.1f}%{s['avg_return']:>+8.2f}%")
    o = stats["overall"]
    L.append(f"{'overall':<8}{o['resolved']:>9}{o['wins']:>6}{o['losses']:>6}"
             f"{o['pct_won']:>6.1f}%{o['avg_return']:>+8.2f}%")
    return "\n".join(L)


def format_top20(days_stats):
    """Daily top-20 review table: the 3–7 day analysis view.

    One line per day: date, picks count, then per-horizon resolved + %won.
    Days whose horizons haven't matured show 0 resolved (pending).
    """
    L = ["DAILY TOP-20 · flagged top 20 by score per UTC day"]
    L.append(f"{'day':<12}{'picks':>6}"
             + "".join(f"{h+':res/%w':>13}" for h in HORIZONS))
    for d in days_stats:
        cells = "".join(
            f"{d['hits'][h]['resolved']:>7}"
            f"{d['hits'][h]['pct']:>5.0f}%" for h in HORIZONS)
        L.append(f"{d['day']:<12}{len(d['picks']):>6}{cells}")
        top3 = ", ".join(f"{p['coin']}({p['score']:.0f}{_darrow(p)})"
                         for p in d["picks"][:3])
        if top3:
            L.append(f"  top: {top3}")
    return "\n".join(L)


def _darrow(p):
    return {"LONG": "▲", "SHORT": "▼"}.get(p.get("direction"), "•")


def main():
    ap = argparse.ArgumentParser(description="ALT RADAR signal hit-rate")
    ap.add_argument("--db", default="data/signals.db")
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--top20", action="store_true",
                    help="daily top-20 review instead of windowed hit-rate")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    from .store import Store
    from . import picks as picks_mod
    store = Store(args.db)
    try:
        if args.top20:
            print(format_top20(picks_mod.daily_top20(store, args.days)))
        else:
            print(format_report(signal_stats(store, args.hours)))
    finally:
        store.close()


if __name__ == "__main__":
    main()
