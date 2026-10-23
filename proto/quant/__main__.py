"""CLI:  python3 -m proto.quant <command>

  research   chronological research harness (dev -> validation -> holdout-once)
  report     historical measurement report for logged quant events
  gates      evaluate promotion for the config research.json selected
  roe        ROE feasibility: the leverage a target ROE needs, the stop's
             cost on margin and the measured base rates (read-only, pre-
             holdout window only)
  prereg     preregistration records: `prereg validate` / `prereg show <ID>`
             (read-only; never evaluates a holdout, never writes to data/)
  champions  champion/candidate registry and the matched comparison
             (list | compare -- it cannot promote and never writes a
             decision)

This is an offline historical research toolkit. It is not wired into the
live service and has no live watch/emit command.
"""
import argparse
import json
import os
import sys
import time

from .config import DEFAULT


def _store(path):
    from .qstore import QuantStore
    return QuantStore(path)


def preholdout_window(bnd, horizon_days):
    """The (first, last) day of the pre-holdout window, or None if unsafe.

    `bnd` is `Splits.bounds()`.  The measured window is dev + validation; the
    label horizon is added to its last day, and if that reaches the first day
    of the sealed holdout the window would label holdout outcomes, so the
    caller must refuse it.  The window is half-open (entries are sampled with
    t < t_hi and the last label runs to t_hi + horizon), so a horizon that
    ends exactly ON the holdout's first day reads no holdout bar and is
    admitted; one that ends past it is refused.  With the frozen splits (val
    ends day 77, holdout starts day 79) a 1-day horizon clears it; the check
    exists so that a future change to the embargo cannot quietly label the
    holdout.
    """
    if bnd["val"][1] + horizon_days > bnd["holdout"][0]:
        return None
    return bnd["dev"][0], bnd["val"][1]


def _prereg_validate(directory):
    """`prereg validate`: print every record and its validation errors."""
    from . import prereg
    from .. import hypothesis_registry as hr
    try:
        records = prereg.load_all(directory)
    except ValueError as exc:
        # A sidecar that claims to be a preregistration record but cannot be
        # parsed is reported, not raised as a traceback.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    # A hypothesis is "known" when the markdown registry declares it or when
    # a preregistration record for it exists in the same directory.
    known = {e["id"] for e in hr.load_registry(directory)
             if isinstance(e.get("id"), str)}
    known |= {r.id for r in records}
    if not records:
        print(f"no preregistration records in {directory}")
        return 0
    failed = 0
    for rec in records:
        errors = prereg.validate(rec, known_hypothesis_ids=known)
        print(f"{rec.id}  status={rec.status}  variants={len(rec.variants)}  "
              f"{rec.title}")
        for err in errors:
            print(f"  ERROR: {err}")
        if errors:
            failed += 1
    print(f"{len(records)} record(s), {failed} with errors")
    return 1 if failed else 0


def _prereg_show(record_id, directory):
    """`prereg show <ID>`: print one record as JSON."""
    from . import prereg
    try:
        rec = prereg.load(directory, record_id)
    except (FileNotFoundError, ValueError) as exc:
        print(f"no readable preregistration record {record_id!r} in "
              f"{directory}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(prereg.record_to_dict(rec), indent=2, default=str))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m proto.quant",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("research")
    r.add_argument("--data", default="data/bars")
    r.add_argument("--out", default="data/quant_research")
    r.add_argument("--coins", default=None)
    r.add_argument("--holdout", action="store_true")

    p = sub.add_parser("report")
    p.add_argument("--db", default="data/quant.db")

    g = sub.add_parser("gates")
    g.add_argument("--research", default="data/quant_research/research.json")
    g.add_argument("--db", default="data/quant.db")

    ev = sub.add_parser("evaluate")
    ev.add_argument("--data", default="data/bars")
    ev.add_argument("--funding-dir", default="data/funding")
    ev.add_argument("--settlements-dir", default="data/funding_settlements")
    ev.add_argument("--out", default="data/quant_research")
    ev.add_argument("--holdout", action="store_true",
                    help="spend the one-shot holdout (only if val kept the config)")
    ev.add_argument("--coins", default=None,
                    help="comma list of traded coins (default: all); BTC is "
                         "always loaded as the market factor")

    ro = sub.add_parser("roe", help="ROE feasibility: the leverage a target "
                        "ROE needs, the stop's cost on margin and the "
                        "measured base rates (read-only calculator, not a "
                        "recommendation)")
    ro.add_argument("--data", default="data/bars")
    ro.add_argument("--target-roe", type=float, default=1.0,
                    help="gain at the target as a fraction of margin "
                         "(1.0 = +100%%)")
    ro.add_argument("--stop-roe", type=float, default=0.5,
                    help="loss at the stop as a fraction of margin (0.5 = -50%%)")
    ro.add_argument("--leverage", default="10,20", help="comma list")
    ro.add_argument("--coins", default=None,
                    help="comma list of coins (default: all except BTC)")
    ro.add_argument("--stride", type=int, default=12,
                    help="sample every Nth bar of every contiguous run")
    ro.add_argument("--top", type=int, default=15,
                    help="how many per-coin rows to print (0 = none)")
    ro.add_argument("--out", default=None, help="optional JSON output path")

    pr = sub.add_parser("prereg", help="preregistration records (read-only)")
    prsub = pr.add_subparsers(dest="prereg_cmd", required=True)
    prv = prsub.add_parser("validate", help="validate every record in --dir")
    prv.add_argument("--dir", default=os.path.join("docs", "hypotheses"))
    prs = prsub.add_parser("show", help="print one record as JSON")
    prs.add_argument("id")
    prs.add_argument("--dir", default=os.path.join("docs", "hypotheses"))
    ch = sub.add_parser("champions",
                         help="champion/candidate registry and comparison "
                              "(read-only: it cannot promote)")
    chsub = ch.add_subparsers(dest="chcmd", required=True)
    chl = chsub.add_parser("list", help="print the registry versions")
    chl.add_argument("--db", required=True, help="the registry database")
    cc = chsub.add_parser("compare",
                           help="compare a champion and a candidate on the "
                                "same events with matched costs")
    cc.add_argument("--db", required=True, help="the registry database")
    cc.add_argument("--champion", required=True, help="the champion config id")
    cc.add_argument("--candidate", required=True, help="the candidate config id")
    cc.add_argument("--window", required=True, choices=("dev", "val"),
                    help="the window both configurations are evaluated in")
    cc.add_argument("--out", default=None, help="write the comparison JSON here")
    cc.add_argument("--data", default="data/bars", help="the bars archive")
    cc.add_argument("--coins", default=None, help="comma list of coins")
    cc.add_argument("--funding-dir", default=None)
    cc.add_argument("--settlements-dir", default=None)

    a = ap.parse_args(argv)

    if a.cmd == "research":
        from . import research
        return research.main([x for x in (
            "--data", a.data, "--out", a.out,
            *(("--coins", a.coins) if a.coins else ()),
            *(("--holdout",) if a.holdout else ())) if x])
    if a.cmd == "report":
        st = _store(a.db)
        try:
            from . import report
            promo_dir = os.path.dirname(a.db) or "."
            print(report.render(report.build(st, DEFAULT, promo_dir)))
        finally:
            st.close()
        return 0
    if a.cmd == "gates":
        from . import gates
        from .registry import REGISTERED, RuleSpec
        from .config import RuleParams, Geometry
        rj = json.load(open(a.research))
        sel = rj["selected"]
        spec = RuleSpec(RuleParams(**sel["params"]), Geometry(**sel["geom"]))
        reg = {x.rule_id for x in REGISTERED}
        print("selected rule id:", spec.rule_id)
        if spec.rule_id not in reg:
            print("NOT REGISTERED: add it to proto/quant/registry.py (a new rule "
                  "id) and collect fresh shadow evidence before promotion.")
        ok, why = gates.evaluate(rj.get("holdout"), rj.get("val"),
                                 rj["spa"]["p_value"], rj.get("holdout_slip2x"))
        promo_dir = os.path.dirname(a.db) or "."
        gates.record(promo_dir, spec.rule_id, rj["config_hash"], ok and
                     spec.rule_id in reg, why, rj.get("holdout"))
        print("PASS" if ok else "NOT PROMOTED")
        for x in why:
            print("  -", x)
        return 0
    if a.cmd == "evaluate":
        from . import eval_harness
        from .config import DEFAULT as _cfg
        # Restrict the traded universe when --coins is given (comma list);
        # BTC is still loaded internally as the market factor. Whitespace is
        # tolerated; a list that is entirely blank means the whole universe.
        if a.coins:
            coins = [c.strip() for c in a.coins.split(",") if c.strip()] or None
        else:
            coins = None
        report = eval_harness.evaluate(
            a.data,
            funding_dir=a.funding_dir if os.path.isdir(a.funding_dir) else None,
            settlements_dir=a.settlements_dir if os.path.isdir(a.settlements_dir) else None,
            cfg=_cfg,
            spend_holdout=a.holdout,
            out_dir=a.out,
            coins=coins)
        print(report.to_json())
        if report.leakage_violations:
            print("LEAKAGE VIOLATIONS:", file=sys.stderr)
            for v in report.leakage_violations:
                print(f"  - {v}", file=sys.stderr)
        if report.missing_cost_coins:
            print(f"\nFunding cost data MISSING for {len(report.missing_cost_coins)} "
                  f"coins (surfaced, not estimated):", file=sys.stderr)
            for c in report.missing_cost_coins[:20]:
                print(f"  - {c}", file=sys.stderr)
            if len(report.missing_cost_coins) > 20:
                print(f"  ... and {len(report.missing_cost_coins) - 20} more",
                      file=sys.stderr)
        return 0
    if a.cmd == "roe":
        from . import roe as roemod
        from .bars import list_coins, load_coin
        from .config import BAR_S as DEFAULT_BAR_S
        from .config import BARS_PER_DAY as DEFAULT_BARS_PER_DAY
        btc = load_coin(a.data, "BTC")
        if not btc:
            print("BTC bars required for the archive span")
            return 1
        t_min = btc["ts"][0]
        n_days = (btc["ts"][-1] - t_min) / 86400.0
        bnd = DEFAULT.splits.bounds(n_days)
        # The PRE-HOLDOUT window only: dev + validation.  The archive's
        # TIMESTAMPS are read - they are what places the split boundaries -
        # but no entry is sampled and no outcome is labelled inside the
        # holdout window, so no holdout outcome enters this measurement.
        # The holdout is spent once, through the research harness, and only
        # by a config that validation kept.
        t_lo = t_min + bnd["dev"][0] * 86400
        t_hi = t_min + bnd["val"][1] * 86400
        # The label horizon is 24 h (288 bars) and is fixed here.  The guard
        # below makes the holdout guarantee explicit rather than implicit in
        # the split arithmetic: no label window may reach into the holdout.
        horizon_s = DEFAULT_BARS_PER_DAY * DEFAULT_BAR_S
        holdout_start = t_min + bnd["holdout"][0] * 86400
        if not preholdout_window(bnd, horizon_s / 86400.0):
            print("REFUSED: the label horizon would reach into the sealed "
                  "holdout window", file=sys.stderr)
            return 1
        try:
            levs = tuple(int(x) for x in a.leverage.split(","))
        except ValueError:
            print("--leverage must be a comma list of integers", file=sys.stderr)
            return 1
        if not levs or any(L <= 0 for L in levs):
            print("--leverage must be a comma list of positive integers",
                  file=sys.stderr)
            return 1
        if a.stride < 1:
            print("--stride must be at least 1", file=sys.stderr)
            return 1
        if a.target_roe <= 0:
            print("--target-roe must be positive", file=sys.stderr)
            return 1
        if a.stop_roe <= 0:
            print("--stop-roe must be positive", file=sys.stderr)
            return 1
        if a.coins:
            coins = [c.strip() for c in a.coins.split(",") if c.strip()]
        else:
            coins = [c for c in list_coins(a.data) if c != "BTC"]
        if "BTC" in coins:
            coins = [c for c in coins if c != "BTC"]
            print("BTC is the market factor, not a traded coin: it is excluded")
        if not coins:
            print("no coins to measure")
            return 1
        kw = dict(stop_roe=a.stop_roe, leverages=levs, target_roe=a.target_roe,
                  stride=a.stride, t_lo=t_lo, t_hi=t_hi)
        reports = []
        try:
            for i, coin in enumerate(coins):
                reports.append(roemod.coin_report(a.data, coin, **kw))
                if (i + 1) % 50 == 0:
                    print(f"  ... {i + 1}/{len(coins)} coins read", file=sys.stderr,
                          flush=True)
        except ValueError as exc:
            print(f"this geometry is not measurable: {exc}", file=sys.stderr)
            return 1
        pooled = roemod.pooled_from_reports(reports)
        # A coin is "usable" when it cleared the liquidity floor AND
        # contributed at least one sample: the printed universe and the
        # pooled denominators then refer to the same coins.  The coins that
        # did not contribute are counted by REASON, so "nothing was measured"
        # is never dressed up as "everything was illiquid".
        usable = [r for r in reports if r and "rows" in r
                  and any(row["rates"].get("n") for row in r["rows"])]
        missing = [c for c, r in zip(coins, reports) if r is None]
        untradeable = [r["coin"] for r in reports
                       if r and "untradeable" in r]
        costs = [r["cost_frac"] for r in usable]
        if not usable:
            print("nothing was measured:", file=sys.stderr)
            if missing:
                print(f"  no archive (or no bars in the window): "
                      f"{', '.join(missing[:10])}"
                      f"{' ...' if len(missing) > 10 else ''}", file=sys.stderr)
            if untradeable:
                print(f"  below the liquidity floor: "
                      f"{', '.join(untradeable[:10])}"
                      f"{' ...' if len(untradeable) > 10 else ''}", file=sys.stderr)
            return 1
        print(f"ROE feasibility  target +{a.target_roe:.0%} / stop "
              f"-{a.stop_roe:.0%} on margin")
        print(f"window: pre-holdout only - dev {bnd['dev'][0]:.0f}-"
              f"{bnd['dev'][1]:.0f} d + validation {bnd['val'][0]:.0f}-"
              f"{bnd['val'][1]:.0f} d of a {n_days:.1f} d archive")
        print(f"         the holdout window (from day {bnd['holdout'][0]:.0f}) is "
              "EXCLUDED from the measurement: no entry is sampled and no "
              "outcome is labelled there.  The archive's timestamps are still "
              "read - they are what places the split boundaries - but no "
              "holdout outcome is computed.")
        print("A calculator, not a recommendation: leverage creates no edge; no "
              "registered rule has passed the promotion gate.\n")
        print("assumptions")
        print("  bars        5-minute, 24 h horizon (288 bars), entry at the "
              "next bar open, stop-first on ties")
        print(f"  costs       fee {DEFAULT.cost.fee_per_side:.2%}/side + tiered "
              "slippage by trailing-24h quote volume (an assumption, not a "
              "measurement)")
        if costs:
            lo_c, hi_c = min(costs), max(costs)
            if hi_c - lo_c < 5e-5:
                print(f"              this archive: {lo_c:.2%} round trip")
            else:
                print(f"              this archive: {lo_c:.2%}-{hi_c:.2%} "
                      "round trip")
        print("  funding     NOT modeled")
        print(f"  sampling    every {a.stride}th bar of every contiguous run, "
              "both sides; a label window never crosses a timestamp gap")
        print(f"  intervals   moving-block bootstrap over "
              f"{roemod.BLOCK_DAYS} consecutive UTC days, "
              f"{roemod.BOOTSTRAP_B} resamples (the label window is 24 h, so "
              "the rows of adjacent days share their price paths)")
        print(f"  liquidity   a coin contributes only where its trailing-24h "
              f"quote volume is at least ${DEFAULT.cost.min_qv24:,.0f}")
        print(f"  universe    {len(usable)} of {len(coins)} coins contributed; "
              f"{len(untradeable)} below the liquidity floor, {len(missing)} "
              "with no archive or no bars in the window\n")
        for L in sorted(pooled):
            p = pooled[L]
            pl = p["plan"]
            print(f"leverage {L}x: the target needs a {pl['target_move']:.2%} "
                  f"price move, the stop {pl['stop_move']:.2%} "
                  f"(liquidation distance {pl['liq_distance']:.2%}; the stop is "
                  f"{'inside' if pl['liq_ok'] else 'NOT inside'} the buffer)")
            print(f"   samples {p['n']} ({p['entries']} entries, both sides) on "
                  f"{p['coins']} coins")
            print(f"   target touched at any time {p['touch']:.1%} | TP first "
                  f"{p['tp']:.1%} | stop first {p['sl']:.1%} | neither "
                  f"{p['time']:.1%}")
            # p_null and p_breakeven are probabilities of reaching the TARGET
            # FIRST, so they are compared with the TP-first rate - not with
            # the TP-among-terminal rate, which has a different denominator.
            print(f"   TP first {p['tp']:.1%} vs the cost-adjusted breakeven "
                  f"{pl['p_breakeven']:.1%} (the rate that breaks even) and the "
                  f"driftless null {pl['p_null']:.1%}")
            print(f"   TP among terminal {p['tp_given_terminal']:.1%} "
                  f"[{p['tp_ci'][0]:.1%}, {p['tp_ci'][1]:.1%}] (of the entries "
                  "that hit a barrier)")
            print(f"   mean ROE per trade (unconditional, after costs): "
                  f"{p['mean_roe']:+.1%} "
                  f"[{p['roe_ci'][0]:+.1%}, {p['roe_ci'][1]:+.1%}]\n")
            print(f"   intervals: moving-block bootstrap over "
                  f"{p['block_days']} consecutive UTC days, {p['bootstrap_b']} "
                  "resamples (the label window is 24 h, so the rows of adjacent "
                  "days share their price paths).  The width depends on the "
                  "block length:")
            for blk in sorted(p["ci_by_block"], key=int):
                sb = p["ci_by_block"][blk]
                print(f"      block {int(blk):>2} d   mean ROE "
                      f"[{sb['roe_ci'][0]:+.1%}, {sb['roe_ci'][1]:+.1%}]"
                      f"   TP among terminal "
                      f"[{sb['tp_ci'][0]:.1%}, {sb['tp_ci'][1]:.1%}]")
            print()
        if a.top and usable:
            usable.sort(key=lambda r: -r["qv24"])
            print(f"per-coin rows (the {min(a.top, len(usable))} most liquid "
                  "contributing coins, by trailing-24h quote volume)")
            print(f"  {'coin':<10}{'qv24 $M':>9}{'cost':>8}{'lev':>5}"
                  f"{'samples':>10}{'touch':>8}{'TP 1st':>8}{'SL 1st':>8}"
                  f"{'TP|term':>9}{'mean ROE':>11}")
            for r in usable[:a.top]:
                for row in r["rows"]:
                    s = row["rates"]
                    if not s.get("n"):
                        continue
                    print(f"  {r['coin']:<10}{r['qv24'] / 1e6:>9.1f}"
                          f"{r['cost_frac']:>8.2%}{row['leverage']:>5}"
                          f"{s['n']:>10}{s['touch']:>8.1%}{s['tp']:>8.1%}"
                          f"{s['sl']:>8.1%}{s['tp_given_terminal']:>9.1%}"
                          f"{s['mean_roe']:>+11.1%}")
            print()
        if a.out:
            blob = {
                "window": {"name": "pre-holdout", "t_lo": t_lo, "t_hi": t_hi,
                           "archive_days": n_days,
                           "dev_days": list(bnd["dev"]),
                           "val_days": list(bnd["val"]),
                           "holdout_start_day": bnd["holdout"][0],
                           "holdout_start_ts": holdout_start,
                           "horizon_s": horizon_s,
                           "holdout_excluded": t_hi + horizon_s <= holdout_start},
                "target_roe": a.target_roe, "stop_roe": a.stop_roe,
                "stride": a.stride,
                "pooled": {str(L): {k: v for k, v in p.items() if k != "plan"}
                           for L, p in pooled.items()},
                "coins": [{"coin": r["coin"], "qv24": r["qv24"],
                           "cost_frac": r["cost_frac"],
                           "rows": [{"leverage": row["leverage"],
                                     "plan": row["plan"],
                                     "rates": row["rates"]}
                                    for row in r["rows"]]}
                          for r in usable],
            }
            try:
                with open(a.out, "w") as fh:
                    json.dump(blob, fh, indent=2, default=float)
            except OSError as exc:
                print(f"cannot write {a.out}: {exc}", file=sys.stderr)
                return 1
            print(f"JSON written to {a.out}")
        return 0
    if a.cmd == "prereg":
        from . import prereg
        if a.prereg_cmd == "validate":
            return _prereg_validate(a.dir)
        if a.prereg_cmd == "show":
            return _prereg_show(a.id, a.dir)
        return 1
    if a.cmd == "champions":
        # Read-only by construction: there is no "promote" subcommand here
        # and this branch never writes a decision or a promotion record.
        from dataclasses import asdict
        from . import champion as CH
        if not os.path.exists(a.db):
            print(f"no registry at {a.db}", file=sys.stderr)
            return 1
        reg = CH.Registry(a.db)
        try:
            if a.chcmd == "list":
                for v in reg.list():
                    print(json.dumps(asdict(v), sort_keys=True, default=str))
                champ = reg.champion()
                if champ is not None:
                    print(f"champion: {champ.id} ({champ.rule_id})")
                else:
                    print("champion: (none)")
                return 0
            champion = reg.get(a.champion)
            candidate = reg.get(a.candidate)
            if champion is None:
                print(f"unknown champion id: {a.champion}", file=sys.stderr)
                return 1
            if candidate is None:
                print(f"unknown candidate id: {a.candidate}", file=sys.stderr)
                return 1
        finally:
            reg.close()
        from . import research as R
        coins = a.coins.split(",") if a.coins else None
        events, meta = R.collect_events(a.data, DEFAULT, coins)
        bounds = DEFAULT.splits.bounds(meta["n_days"])
        window = bounds[a.window]
        res = CH.compare(
            champion, candidate, events, window,
            funding_dir=(a.funding_dir if a.funding_dir
                         and os.path.isdir(a.funding_dir) else None),
            settlements_dir=(a.settlements_dir if a.settlements_dir
                             and os.path.isdir(a.settlements_dir) else None))
        print(json.dumps(res, indent=2, default=str))
        if a.out:
            with open(a.out, "w") as fh:
                json.dump(res, fh, indent=2, default=str)
        if res.get("refused"):
            print(f"REFUSED: {res.get('reason')}", file=sys.stderr)
            return 1
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
