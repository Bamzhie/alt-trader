#!/usr/bin/env python3
"""Tests for the frozen HYD-0003 analysis protocol.

Self-checking script (repo style): prints PASS/FAIL and exits non-zero on
the first failure.  Covers the three things the protocol must enforce
mechanically -- the holdout wall, the denominators and the honest
labelling of a null or incomplete result.
"""
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from proto.quant import analysis as AN
from proto.quant import competing_outcomes as CO
from proto.quant.config import DEFAULT, Geometry, RuleParams
from proto.quant import research as R

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def make_event(coin, ts, day, side=1, gross=0.01, kind=0, qv24=5e7, sigma=0.002):
    ev = R.Event()
    ev.coin = coin
    ev.ts = ts
    ev.day = day
    ev.fam = "B"
    ev.param = 4.0
    ev.side = side
    ev.qv24 = qv24
    ev.sigma = sigma
    ev.rvol = 4.0
    ev.z1h = 0.0
    ev.z4h = 0.0
    ev.btc7d = 0.0
    ev.gross = [gross] * 72
    ev.kind = [kind] * 72
    ev.exit_offset = [287] * 72
    return ev


print("=== the holdout wall ===")
refused, reason = AN.refuse_holdout_window((79.0, 99.0), 79.0, {})
check("a window reaching into the holdout is refused", refused, str(reason))
check("the refusal says why", "holdout-sealed" in (reason or ""), str(reason))
refused2, reason2 = AN.refuse_holdout_window((63.0, 77.0), 79.0, {})
check("a validation window is allowed", not refused2, str(reason2))
refused3, reason3 = AN.refuse_holdout_window((6.0, 61.0), 79.0, {})
check("a dev window is allowed", not refused3, str(reason3))

print("\n=== denominators and honest labelling ===")
rule = RuleParams("breakout_start", "fade", rvol_min=4.0)
geom = Geometry(288, 1.5, 1.0)
# 3 events on 2 coins, 1 day -> below every minimum
few = [make_event("AAA" if i < 2 else "BBB", 1_700_000_000 + i * 300,
                  10.0 + i * 0.01) for i in range(3)]
res = AN.evaluate_member(few, rule, geom, (6.0, 61.0), cfg=DEFAULT)
d = res["denominators"]
check("the denominators are explicit", d["n_selected"] >= 3
      and d["n_coins"] == 2 and d["n_days"] == 1, str(d))
check("a cell below the minimums is 'insufficient'",
      res["status"] == "insufficient", str(res["status"]))
check("an insufficient cell reports NO mean", res["friction_only"] is None,
      str(res["friction_only"]))
check("an insufficient cell states why", "insufficient" in (res["reason"] or ""),
      str(res["reason"]))
check("a null result is never labelled evaluated",
      res["status"] != "evaluated" and res.get("funding_adjusted") is None)

print("\n=== cost separation (friction-only vs funding-adjusted) ===")
# 30 events, 20 coins, 20 days -> above the minimums, but no funding archive
# is configured, so the funding-adjusted cell must be EMPTY, not zero-filled.
evs = []
for i in range(240):
    evs.append(make_event(f"C{i % 20:02d}",
                          1_700_000_000 + (i % 40) * 86400,
                          10.0 + (i % 40) * 1.0))
res2 = AN.evaluate_member(evs, rule, geom, (6.0, 61.0), cfg=DEFAULT,
                           funding_dir=None, settlements_dir=None)
check("a well-populated cell is 'evaluated'", res2["status"] == "evaluated",
      str(res2["reason"]))
check("the friction-only mean is reported", res2["friction_only"] is not None
      and res2["friction_only"]["mean_r"] is not None, str(res2["friction_only"]))
check("with no funding archive the funding-adjusted cell is EMPTY "
      "(never zero-filled)", res2["funding_adjusted"] is None,
      str(res2["funding_adjusted"]))
check("the funding coverage is reported as a number, not a silent zero",
      res2["denominators"]["funding_coverage"] is not None
      and res2["denominators"]["n_funding_evaluable"] == 0,
      str(res2["denominators"]))
check("friction-only and funding-adjusted are separate fields",
      "friction_only" in res2 and "funding_adjusted" in res2)

print("\n=== the point-in-time structure feature ===")
bars = []
for i in range(400):
    hi = 10.0
    lo = 9.0
    if i == 200:
        hi = 12.0
        lo = 8.0
    bars.append({"ts": 1_700_000_000 + i * 300, "o": 10.0, "h": hi, "l": lo,
                 "c": 10.0, "vol": 1.0, "amt": 1.0})
states = AN.pivot_state_series(bars)
check("every bar has a state (possibly None)",
      len(states) == len(bars), f"{len(states)} vs {len(bars)}")
# a pivot at bar 200 (low) is confirmed at 200+2 bars
pivot_ts = bars[200]["ts"]
conf = pivot_ts + 3 * 300
check("the state before the confirmation is not the new pivot's",
      states.get(pivot_ts) != "low" or pivot_ts + 300 >= conf,
      f"{states.get(pivot_ts)}")
check("the state after the confirmation is the new pivot's",
      states.get(conf) == "low", f"{states.get(conf)}")
states2 = AN.pivot_states_for(bars, [pivot_ts, conf])
check("pivot_states_for agrees with the full series",
      states2.get(conf) == "low" and states2.get(pivot_ts) == states.get(pivot_ts),
      str(states2))
check("pivot_state_at returns None when no pivot is knowable",
      AN.pivot_state_at([], pivot_ts) is None)

kept, dropped = AN.filter_by_pivot_state(
    [make_event("AAA", conf, 10.0), make_event("BBB", pivot_ts, 10.0)],
    states, kind="low")
check("the filter keeps only the events whose bar carries the state",
      len(kept) == 1 and kept[0].coin == "AAA" and dropped == 1,
      f"kept={len(kept)} dropped={dropped}")

print("\n=== the family correction and the freeze criteria ===")
members = []
for i, (label, mean) in enumerate((("m1", 0.01), ("m2", -0.02),
                                    ("m3", 0.005))):
    members.append({
        "label": label, "status": "evaluated",
        "friction_only": {"mean_r": mean, "ci_lo_day": mean - 0.005,
                          "ci_hi_day": mean + 0.005,
                          "p_one_sided": 0.2 if mean > 0 else 0.9},
        "denominators": {"n_friction_evaluable": 300, "n_coins": 20,
                         "n_days": 30, "n_funding_evaluable": 0,
                         "funding_coverage": 0.0},
        "funding_adjusted": None,
        "_values": [mean] * 300, "_days": list(range(30)) * 10,
    })
fam = AN.family_report(members)
check("the family correction runs over the evaluable members",
      fam["status"] == "evaluated" and fam["n_evaluable"] == 3, str(fam)[:200])
check("the family p-value is reported", fam["family_p"] is not None,
      str(fam.get("family_p")))

good = {
    "label": "m1", "status": "evaluated",
    "friction_only": {"mean_r": 0.02, "ci_lo_day": 0.005, "ci_hi_day": 0.03,
                      "p_one_sided": 0.001},
    "denominators": {"n_friction_evaluable": 300, "n_coins": 20, "n_days": 30},
    "funding_adjusted": {"delta_vs_friction_only": -0.0005},
}
good_val = {
    "label": "m1", "status": "evaluated",
    "friction_only": {"mean_r": 0.015, "ci_lo_day": 0.002, "ci_hi_day": 0.028,
                      "p_one_sided": 0.004},
    "denominators": {"n_friction_evaluable": 260, "n_coins": 18, "n_days": 25},
}
fam_ok = {"family_p": 0.02}
frozen, checks = AN.freeze_criteria(good, fam_ok, good_val,
                                     placebo={"p_value": 0.01})
check("a member passing every criterion freezes", frozen,
      str([c for c in checks if not c["passed"]]))
# 8 checks: the preregistered 7 criteria, with criteria 1 and 2 each
# split into their mean and interval halves.  Criterion 7 (a fade member
# must beat its own cont control) is a FAMILY comparison, checked by the
# caller, not inside a single member's criteria.
check("every criterion is reported", len(checks) == 8, str(len(checks)))
bad = dict(good)
bad["friction_only"] = dict(good["friction_only"], ci_lo_day=-0.001)
frozen_bad, checks_bad = AN.freeze_criteria(bad, fam_ok, good_val,
                                            placebo={"p_value": 0.01})
check("a member failing a criterion does NOT freeze", not frozen_bad)
check("the failing criterion is named",
      any(c["criterion"].startswith("dev day-cluster") and not c["passed"]
          for c in checks_bad), str(checks_bad))
fam_bad = {"family_p": 0.4}
frozen_bad2, _ = AN.freeze_criteria(good, fam_bad, good_val,
                                     placebo={"p_value": 0.01})
check("a family p above the threshold blocks the freeze", not frozen_bad2)
frozen_bad3, _ = AN.freeze_criteria(good, fam_ok, good_val,
                                     placebo={"p_value": 0.2})
check("a placebo p above the threshold blocks the freeze", not frozen_bad3)

print("\n=== coverage rule ===")
ok, why = AN.coverage_rule({"days_covered": 90.0, "malformed": 0, "gaps": 0})
check("a well-covered coin is eligible", ok, why)
ok2, why2 = AN.coverage_rule({"days_covered": 20.0, "malformed": 0, "gaps": 0})
check("a thinly covered coin is not", not ok2, why2)
ok3, why3 = AN.coverage_rule({"days_covered": 90.0, "malformed": 3, "gaps": 0})
check("a coin with malformed rows is not", not ok3, why3)

print("\n=== one-sided p ===")
check("a positive t gives a small p", AN.one_sided_p(3.0) < 0.01)
check("a negative t gives a p near 1", AN.one_sided_p(-3.0) > 0.99)
check("a missing t gives None", AN.one_sided_p(None) is None)

print()
if FAILURES:
    print(f"FAILURES: {len(FAILURES)}")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
print("ALL PASS (analysis protocol)")
