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


print("=== the holdout wall (enforced on the evaluation path) ===")
refused, reason = AN.refuse_holdout_window((79.0, 99.0), 79.0, {})
check("a window reaching into the holdout is refused", refused, str(reason))
check("the refusal says why", "holdout-sealed" in (reason or ""), str(reason))
refused2, reason2 = AN.refuse_holdout_window((63.0, 77.0), 79.0, {})
check("a validation window is allowed", not refused2, str(reason2))
refused3, reason3 = AN.refuse_holdout_window((6.0, 61.0), 79.0, {})
check("a dev window is allowed", not refused3, str(reason3))
# The refusal must be ENFORCED by the evaluation itself, not only offered
# by a helper: an evaluation of a holdout window has to raise.
_rule = RuleParams("breakout_start", "fade", rvol_min=4.0)
_geom = Geometry(288, 1.5, 1.0)
_holdout_raised = False
try:
    AN.evaluate_member([], _rule, _geom, (79.0, 99.0), cfg=DEFAULT,
                       holdout_start_day=79.0)
except ValueError as exc:
    _holdout_raised = "holdout-sealed" in str(exc)
check("evaluating a holdout window RAISES (the wall is enforced)",
      _holdout_raised)
# the wall must be ON BY DEFAULT: the boundary comes from the configured
# splits, so a caller cannot forget to pass it.
_default_raised = False
try:
    AN.evaluate_member([], _rule, _geom, (79.0, 99.0), cfg=DEFAULT)
except ValueError as exc:
    _default_raised = "holdout-sealed" in str(exc)
check("the wall is ON by default (the configured holdout boundary)",
      _default_raised)
check("the configured holdout boundary is day 79",
      AN.holdout_start_day(DEFAULT) == 79.0, str(AN.holdout_start_day(DEFAULT)))
_ok = AN.evaluate_member([], _rule, _geom, (63.0, 77.0), cfg=DEFAULT,
                          holdout_start_day=79.0)
check("evaluating a validation window still works",
      _ok["status"] == "insufficient", str(_ok.get("reason")))

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

print("\n=== the funding-adjusted cell uses ITS OWN cluster labels ===")
# The funding-adjusted numbers must carry the day/coin labels of the
# events they were computed from.  Here 30 of 40 events are
# funding-attributable and all of them sit on ONE day, while the
# friction-only events span 20 days: a slice of the friction labels would
# report 20 day-clusters for a 1-day sample and publish an interval for it.
_outcomes = []
for i in range(400):
    # The funding-attributable events are INTERLEAVED (every other one), so
    # a slice of the friction-only labels cannot coincide with them.
    attributed = (i % 2 == 0)
    day = 10 if attributed else (11 + (i // 2) % 20)
    _outcomes.append(CO.Outcome(
        event_id=f"e{i}", coin=f"C{i % 20:02d}", ts=1_700_000_000 + day * 86400,
        side=1, cause="TP", exit_ts=1_700_000_000 + day * 86400 + 300,
        gross_log=0.01, friction=0.0009,
        funding=(0.0001 if attributed else None),
        net_r=(0.01 if attributed else None), dd=0.01,
        reason=(None if attributed else "funding:incomplete:x")))


def _fake_resolve(*a, **kw):
    return {"n_selected": 400, "n_resolved": 400, "n_censored": 0,
            "n_invalidated": 0, "causes": {"TP": 400}, "outcomes": _outcomes,
            "coverage": 1.0}


_real_resolve = AN.CO.resolve_competing
AN.CO.resolve_competing = _fake_resolve
try:
    res3 = AN.evaluate_member([], _rule, _geom, (6.0, 61.0), cfg=DEFAULT)
finally:
    AN.CO.resolve_competing = _real_resolve
fa = res3.get("funding_adjusted")
check("the funding-adjusted cell reports its OWN day count",
      fa is not None and fa.get("clusters", {}).get("day") == 1,
      str(fa.get("clusters") if fa else None))
check("a one-day funding sample publishes no interval (it is degenerate)",
      fa is not None and fa.get("ci_lo_day") is None
      and fa.get("ci_hi_day") is None and fa.get("t_day") is None,
      str(fa))
check("the unattributable events are counted, not dropped",
      res3["denominators"]["n_funding_unattributed"] == 200
      and res3["denominators"]["n_funding_evaluable"] == 200,
      str(res3["denominators"]))
check("the funding coverage is reported",
      abs(res3["denominators"]["funding_coverage"] - 0.5) < 1e-9,
      str(res3["denominators"]["funding_coverage"]))
check("the friction-only cell still spans every day",
      res3["friction_only"]["clusters"]["day"] == 21,
      str(res3["friction_only"]["clusters"]))

print("\n=== the matched placebo compares the MEAN ===")
# The placebo is a DATE PERMUTATION: the donor for an event is another
# event of the same coin, time-of-day bucket and liquidity tier on a
# DIFFERENT date.  It therefore tests whether the observed mean is
# concentrated in a way the date mix would not produce.
def _mk_events(spec):
    """spec: list of (day, r) per (coin, tod) key."""
    out = []
    for k, (coin, tod) in enumerate([(f"C{i // 6:02d}", i % 6)
                                      for i in range(36)]):
        for day, r in spec:
            ev = R.Event()
            ev.coin = coin
            ev.ts = 1_700_000_000 + day * 86400 + tod * 3600
            ev.day = float(day)
            ev.fam = "B"
            ev.param = 4.0
            ev.side = 1
            ev.qv24 = 5e7
            ev.sigma = 0.002
            ev.rvol = 4.0
            ev.z1h = 0.0
            ev.z4h = 0.0
            ev.btc7d = 0.0
            ev.gross = [0.01] * 72
            ev.kind = [0] * 72
            ev.exit_offset = [287] * 72
            out.append((ev, r))
    return out


def _placebo_for(pairs):
    evs = [e for e, _ in pairs]
    outs = [CO.Outcome(event_id=f"e{i}", coin=e.coin, ts=e.ts, side=1,
                        cause="TP", exit_ts=e.ts + 300, gross_log=0.01,
                        friction=0.0009, funding=0.0001, net_r=0.01, dd=0.01,
                        reason=None) for i, e in enumerate(evs)]
    vals = [r for _, r in pairs]
    return AN.mean_r_placebo(evs, outs, vals, placebos=100, seed=7)


# a date-SPECIFIC edge: nine of every ten events sit on one date
_conc = _mk_events([(10, 0.10)] * 9 + [(20, 0.001)])
_placebo = _placebo_for(_conc)
check("the placebo reports the observed mean",
      _placebo["observed_mean"] is not None
      and abs(_placebo["observed_mean"]
              - (9 * 0.10 + 0.001) / 10) < 1e-9,
      str(_placebo["observed_mean"]))
check("a date-specific edge gets a small placebo p-value",
      _placebo["p_value"] is not None and _placebo["p_value"] <= 0.05,
      str(_placebo))
check("the placebo used donors on a different date",
      _placebo["n_donors"] == len(_conc), str(_placebo))
# a LEVEL edge (every date carries the same value) is NOT date-specific: the
# date-permutation null reproduces the mean, so the p-value stays high.  That
# is the documented semantics of this placebo, and the criterion is
# deliberately strict about it.
_level = _mk_events([(10, 0.10), (20, 0.10)])
_p_flat = _placebo_for(_level)
check("a level edge is not date-specific (the placebo p-value stays high)",
      _p_flat["p_value"] is not None and _p_flat["p_value"] > 0.5,
      str(_p_flat))

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
      states.get(pivot_ts) != "low", f"{states.get(pivot_ts)}")
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
CTRL = {"friction_only": {"mean_r": 0.001, "ci_lo_day": -0.01,
                          "ci_hi_day": 0.01, "p_one_sided": 0.8}}
CTRL_VAL = {"friction_only": {"mean_r": 0.0005, "ci_lo_day": -0.02,
                              "ci_hi_day": 0.02, "p_one_sided": 0.9}}
frozen, checks = AN.freeze_criteria(good, fam_ok, good_val,
                                     placebo={"p_value": 0.01},
                                     control=CTRL, control_val=CTRL_VAL)
check("a member passing every criterion freezes", frozen,
      str([c for c in checks if not c["passed"]]))
frozen_nc, checks_nc = AN.freeze_criteria(good, fam_ok, good_val,
                                          placebo={"p_value": 0.01})
check("without the mirror-image control the freeze is REFUSED", not frozen_nc)
check("the missing control is named",
      any("mirror-image" in c["criterion"] and not c["passed"]
          for c in checks_nc), str(checks_nc))
frozen_np, checks_np = AN.freeze_criteria(good, fam_ok, good_val, placebo=None,
                                          control=CTRL, control_val=CTRL_VAL)
check("without a placebo comparison the freeze is REFUSED", not frozen_np)
check("the missing placebo is named",
      any("placebo" in c["criterion"] and not c["passed"]
          for c in checks_np), str(checks_np))
# 9 checks: the preregistered 7 criteria, with criteria 1 and 2 each
# split into their mean and interval halves.
check("every criterion is reported", len(checks) == 9, str(len(checks)))
bad = dict(good)
bad["friction_only"] = dict(good["friction_only"], ci_lo_day=-0.001)
frozen_bad, checks_bad = AN.freeze_criteria(bad, fam_ok, good_val,
                                            placebo={"p_value": 0.01},
                                            control=CTRL, control_val=CTRL_VAL)
check("a member failing a criterion does NOT freeze", not frozen_bad)
check("the failing criterion is named",
      any(c["criterion"].startswith("dev day-cluster") and not c["passed"]
          for c in checks_bad), str(checks_bad))
fam_bad = {"family_p": 0.4}
frozen_bad2, _ = AN.freeze_criteria(good, fam_bad, good_val,
                                     placebo={"p_value": 0.01},
                                     control=CTRL, control_val=CTRL_VAL)
check("a family p above the threshold blocks the freeze", not frozen_bad2)
frozen_bad3, _ = AN.freeze_criteria(good, fam_ok, good_val,
                                     placebo={"p_value": 0.2},
                                     control=CTRL, control_val=CTRL_VAL)
check("a placebo p above the threshold blocks the freeze", not frozen_bad3)
BEAT = {"friction_only": {"mean_r": 0.5, "ci_lo_day": 0.4, "ci_hi_day": 0.6,
                          "p_one_sided": 0.001}}
frozen_bad4, _ = AN.freeze_criteria(good, fam_ok, good_val,
                                     placebo={"p_value": 0.01},
                                     control=BEAT, control_val=BEAT)
check("a member that does not beat its mirror image does not freeze",
      not frozen_bad4)

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
