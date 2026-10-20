"""CLI:  python3 -m proto.quant <command>

  research   chronological research harness (dev -> validation -> holdout-once)
  watch      run the watcher on collected bars (SHADOW events only)
  report     measurement report for logged events
  status     watcher / pause status
  pause / resume   manual global pause flag
  gates      evaluate promotion for the config research.json selected
  prereg     preregistration records: `prereg validate` / `prereg show <ID>`
             (read-only; never evaluates a holdout, never writes to data/)
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
    ev.add_argument("--coins", default=None,
                    help="comma list of traded coins (default: all); BTC is "
                         "always loaded as the market factor")

    pr = sub.add_parser("prereg", help="preregistration records (read-only)")
    prsub = pr.add_subparsers(dest="prereg_cmd", required=True)
    prv = prsub.add_parser("validate", help="validate every record in --dir")
    prv.add_argument("--dir", default=os.path.join("docs", "hypotheses"))
    prs = prsub.add_parser("show", help="print one record as JSON")
    prs.add_argument("id")
    prs.add_argument("--dir", default=os.path.join("docs", "hypotheses"))

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
    if a.cmd == "prereg":
        from . import prereg
        if a.prereg_cmd == "validate":
            return _prereg_validate(a.dir)
        if a.prereg_cmd == "show":
            return _prereg_show(a.id, a.dir)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
