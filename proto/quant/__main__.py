"""CLI:  python3 -m proto.quant <command>

  research   chronological research harness (dev -> validation -> holdout-once)
  watch      run the watcher on collected bars (SHADOW events only)
  report     measurement report for logged events
  status     watcher / pause status
  pause / resume   manual global pause flag
  gates      evaluate promotion for the config research.json selected
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

    w = sub.add_parser("watch")
    w.add_argument("--data", default="data/bars")
    w.add_argument("--db", default="data/quant.db")
    w.add_argument("--every", type=int, default=300)
    w.add_argument("--once", action="store_true")
    w.add_argument("--equity", type=float, default=1000.0)

    for name in ("report", "status", "pause", "resume"):
        p = sub.add_parser(name)
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
    ev.add_argument("--coins", default=None)

    a = ap.parse_args(argv)

    if a.cmd == "research":
        from . import research
        return research.main([x for x in (
            "--data", a.data, "--out", a.out,
            *(("--coins", a.coins) if a.coins else ()),
            *(("--holdout",) if a.holdout else ())) if x])
    if a.cmd == "watch":
        from .engine import Watcher
        st = _store(a.db)
        wt = Watcher(a.data, st, equity=a.equity)
        try:
            while True:
                res = wt.step()
                print(time.strftime("%H:%M:%S"), json.dumps(res), flush=True)
                if a.once:
                    break
                time.sleep(a.every)
        except KeyboardInterrupt:
            pass
        finally:
            st.close()
        return 0
    if a.cmd in ("report", "status", "pause", "resume"):
        st = _store(a.db)
        try:
            if a.cmd == "report":
                from . import report
                promo_dir = os.path.dirname(a.db) or "."
                print(report.render(report.build(st, DEFAULT, promo_dir)))
            elif a.cmd == "status":
                print(json.dumps({"config_hash": DEFAULT.config_hash(),
                                  "pause": st.get_meta("pause"),
                                  "auto_pause": st.get_meta("auto_pause"),
                                  "auto_pause_reason": st.get_meta("auto_pause_reason"),
                                  "events": st.conn.execute(
                                      "SELECT COUNT(*) FROM quant_event").fetchone()[0]},
                                 indent=2))
            else:
                st.set_meta("pause", "1" if a.cmd == "pause" else "0")
                print("global pause", "ON" if a.cmd == "pause" else "OFF")
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
        # Filter bar archive to requested coins if --coins given
        fargs = [x for x in ("--coins", a.coins) if x is not None]
        report = eval_harness.evaluate(
            a.data,
            funding_dir=a.funding_dir if os.path.isdir(a.funding_dir) else None,
            settlements_dir=a.settlements_dir if os.path.isdir(a.settlements_dir) else None,
            cfg=_cfg,
            spend_holdout=a.holdout,
            out_dir=a.out)
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
    return 1


if __name__ == "__main__":
    sys.exit(main())
