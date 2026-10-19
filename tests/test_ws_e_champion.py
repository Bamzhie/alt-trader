#!/usr/bin/env python3
"""WS-E (workstream E): champion/candidate tracking + immutable decision chain.

Self-checking script in the house style: prints PASS/FAIL per check and
exits non-zero when anything fails.

Everything here is SYNTHETIC: hand-built events, registries and chain files
under temp dirs.  The suite never writes to ``data/``, never creates
``docs/decisions/chain.jsonl`` in the repository and never promotes
anything.

Covered:
  1. the registry is a VERSIONED history (both versions stay retrievable,
     the current champion is discoverable, a duplicate id is refused)
  2. importing the module creates no file; the registry file is created
     only at the explicit path the caller passes
  3. the matched comparison: both configs on the SAME events, the same
     window, matched costs; the paired difference equals the difference of
     the means; the interval is the day-cluster interval of the paired
     differences (recomputed here)
  4. refusals: different event sets, different cost models, no evaluable
     outcome -- each carries a reason and no numbers
  5. the decision chain detects tampering (broken hash + broken link) and
     refuses to extend a broken chain
  6. evidence is verified at write time (a changed file is refused)
  7. the human-owned log is untouched and the chain file is not created in
     the repository
"""
import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from proto.quant import barrier as B
from proto.quant import competing_outcomes as CO
from proto.quant import decision_log as DL
from proto.quant import stats as S
from proto.quant.champion import (CHAMPION_VERSION, ConfigVersion, Registry,
                                   compare)
from proto.quant.config import (BAR_S, DEFAULT, Geometry, RuleParams,
                                grid_geometries)

FAILURES = []
CHECKS = 0


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    cond = bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"   {detail}"))
    if not cond:
        FAILURES.append(name)


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
GRID = grid_geometries()
GEOM_CH = Geometry(48, 1.0, 1.0)        # champion geometry (in the grid)
GEOM_CA = Geometry(144, 3.0, 1.0)       # candidate geometry (in the grid)
GI_CH = GRID.index(GEOM_CH)
GI_CA = GRID.index(GEOM_CA)
RULE = RuleParams(family="breakout_start", policy="fade", rvol_min=4.0)
RULE_WIDE = RuleParams(family="breakout_start", policy="fade", rvol_min=2.0)
T0 = 1_700_000_100
WINDOW = (0.0, 1e9)                     # every synthetic event is inside
COINS = ("AAA", "BBB", "CCC", "DDD")
SIGMA = 0.002
QV24 = 5e6
CYCLE_S = 8 * 3600
SETTLE_RATE = 0.0005
BOOT = 2000                             # the bootstrap size compare() uses


def geom_dict(g):
    return {"horizon_bars": g.horizon_bars, "k_stop": g.k_stop,
            "r_tp": g.r_tp, "key": g.key()}


def mk_event(coin, ts, side, gross, kind=0, param=5.0, qv24=QV24):
    """A research-like event carrying per-(geometry, side) label slots."""
    gross_arr = [0.0] * (2 * len(GRID))
    kind_arr = [2] * (2 * len(GRID))
    off_arr = [0] * (2 * len(GRID))
    for k in range(2 * len(GRID)):
        gross_arr[k] = gross[k]
        kind_arr[k] = kind
        off_arr[k] = 0
    # The exit offsets of the two geometries under test: the champion's slot
    # exits inside its 48-bar horizon, the candidate's inside its 144-bar
    # horizon, so the holding windows (and their funding) differ.
    off_arr[GI_CH * 2 + (0 if side > 0 else 1)] = 40
    off_arr[GI_CA * 2 + (0 if side > 0 else 1)] = 100
    return SimpleNamespace(coin=coin, ts=ts, day=ts / 86400.0, fam="B",
                           param=param, side=side, qv24=qv24, sigma=SIGMA,
                           rvol=5.0, z1h=0.1, z4h=0.2, btc7d=0.01,
                           gross=gross_arr, kind=kind_arr, exit_offset=off_arr)


def build_events(n_days=6, per_day=3, wide=False):
    """Synthetic events.  ``wide`` adds events with a lower rvol (param 3.0),
    which a rvol_min=2.0 rule would select and a rvol_min=4.0 rule would not."""
    events = []
    for d in range(n_days):
        for j in range(per_day):
            ts = T0 + d * 86400 + j * 3600
            coin = COINS[(d + j) % len(COINS)]
            side = 1 if (d + j) % 2 == 0 else -1
            gross = [0.0004 * ((k % 5) - 2) for k in range(2 * len(GRID))]
            # the champion and the candidate read DIFFERENT slots -> their
            # outcomes differ, so the paired difference is not trivially zero
            gross[GI_CH * 2 + (0 if side > 0 else 1)] = 0.005
            gross[GI_CA * 2 + (0 if side > 0 else 1)] = 0.001
            kind = (0, 1, 2)[(d + j) % 3]
            param = 3.0 if (wide and (d + j) % 4 == 0) else 5.0
            events.append(mk_event(coin, ts, side, gross, kind=kind, param=param))
    return events


def write_settlements(directory, coin, rows):
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, coin + ".csv.gz")
    with gzip.open(path, "wt", newline="") as f:
        f.write("settle_ts,settled_rate,cycle_hours\n")
        for ts, rate, cycle in rows:
            f.write(f"{ts},{rate},{cycle}\n")


def settlements_dir():
    """A settlement archive that covers every holding window of the fixture."""
    d = os.path.join(tempfile.mkdtemp(prefix="wse-settle-"), "settlements")
    lo = ((T0 - 3 * 86400) // CYCLE_S) * CYCLE_S
    hi = T0 + 12 * 86400
    rows = [(ts, SETTLE_RATE, 8) for ts in range(lo, hi, CYCLE_S)]
    for coin in COINS:
        write_settlements(d, coin, rows)
    return d


def mk_version(vid, geom, *, config_hash=None, role="candidate",
               status="candidate", params=None):
    return ConfigVersion(
        id=vid,
        rule_id="quant-v1:breakout_start:rvol4:fade:" + geom.key(),
        config_hash=(config_hash if config_hash is not None
                     else DEFAULT.config_hash()),
        params=params if params is not None else {
            "family": "breakout_start", "policy": "fade", "rvol_min": 4.0,
            "cusum_h": 5.0},
        geometry=geom_dict(geom),
        role=role,
        status=status,
        data_hash="d" * 16,
        code_hash="c" * 16,
        created_ts=int(time.time()),
        notes="synthetic fixture")


def temp_dir(prefix):
    return tempfile.mkdtemp(prefix=prefix)


# ---------------------------------------------------------------------------
# 1. the registry is a versioned history
# ---------------------------------------------------------------------------
section("1. registry: versioned history")

reg_dir = temp_dir("wse-reg-")
reg_path = os.path.join(reg_dir, "registry.db")
reg = Registry(reg_path)
check("the registry file is created at the explicit path",
      os.path.isfile(reg_path), reg_path)
check("nothing else is created in the registry directory",
      sorted(os.listdir(reg_dir)) == ["registry.db"],
      str(sorted(os.listdir(reg_dir))))

v1 = mk_version("cfg-0001", GEOM_CH)
reg.add(v1)
check("the added version is retrievable", reg.get("cfg-0001") is not None)
check("the added version round-trips its fields",
      reg.get("cfg-0001").geometry == geom_dict(GEOM_CH)
      and reg.get("cfg-0001").rule_id == v1.rule_id
      and reg.get("cfg-0001").params == v1.params,
      str(reg.get("cfg-0001")))
check("an unknown id is not found", reg.get("cfg-9999") is None)

try:
    reg.add(mk_version("cfg-0001", GEOM_CH))
    check("a duplicate id is refused", False, "no error raised")
except Exception as exc:
    check("a duplicate id is refused", "duplicate" in str(exc).lower(),
          str(exc))

check("there is no champion before one is set", reg.champion() is None)

reg.set_champion("cfg-0001")
champ = reg.champion()
check("the champion is discoverable", champ is not None)
check("the champion is a NEW version (the old row is never mutated)",
      champ is not None and champ.id != "cfg-0001"
      and champ.status == "champion" and champ.role == "champion",
      str(champ))
check("the superseded version stays retrievable",
      reg.get("cfg-0001") is not None
      and reg.get("cfg-0001").status == "candidate")
check("the history keeps both versions",
      len(reg.list()) == 2, str(len(reg.list())))

v2 = mk_version("cfg-0002", GEOM_CA)
reg.add(v2)
reg.set_champion("cfg-0002")
champ2 = reg.champion()
check("the new champion is the candidate's new version",
      champ2 is not None and champ2.status == "champion"
      and champ2.geometry == geom_dict(GEOM_CA), str(champ2))
retired = [v for v in reg.list() if v.status == "retired"]
check("the previous champion was demoted to retired (as a NEW version)",
      len(retired) == 1 and retired[0].id != champ.id
      and retired[0].role == "retired", str(retired))
check("every earlier version is still retrievable",
      all(reg.get(v.id) is not None for v in reg.list()),
      str([v.id for v in reg.list()]))
check("the retired version is listed under its status",
      [v.id for v in reg.list(status="retired")] == [retired[0].id])
check("the champion is not listed as retired",
      champ2.id not in [v.id for v in reg.list(status="retired")])
reg.close()

reg2 = Registry(reg_path)
check("the registry survives a reopen",
      reg2.champion() is not None and len(reg2.list()) == 4,
      str(len(reg2.list())))
reg2.close()

section("2. importing the module creates no file")
imp_dir = temp_dir("wse-import-")
code = ("import sys; sys.path.insert(0, %r);"
        "import proto.quant.champion as c; print(c.CHAMPION_VERSION)" % ROOT)
proc = subprocess.run([sys.executable, "-c", code], cwd=imp_dir,
                      capture_output=True, text=True)
check("the module imports cleanly", proc.returncode == 0, proc.stderr[-500:])
check("importing the module creates no file",
      os.listdir(imp_dir) == [], str(os.listdir(imp_dir)))
check("the module exports its version",
      CHAMPION_VERSION == "champion-v1", CHAMPION_VERSION)

# ---------------------------------------------------------------------------
# 3. the matched comparison
# ---------------------------------------------------------------------------
section("3. compare: same events, same window, matched costs")

events = build_events()
champ_v = mk_version("cfg-0001", GEOM_CH)
cand_v = mk_version("cfg-0002", GEOM_CA)
res = compare(champ_v, cand_v, events, WINDOW)

check("the comparison is not refused", res.get("refused") is False,
      str(res.get("reason")))
check("the same events were used", res["same_events"] is True)
check("the same cost model was used", res["same_cost_model"] is True)
check("both sides report the same n_events",
      res["n_events"] == res["n_champion"] == res["n_candidate"],
      f"{res['n_events']} {res['n_champion']} {res['n_candidate']}")
check("n_events counts the fixture's events", res["n_events"] == len(events),
      f"{res['n_events']} vs {len(events)}")
check("a difference is reported", res["difference"] is not None)
check("an interval is reported",
      res["ci_lo"] is not None and res["ci_hi"] is not None)
check("a cluster count is reported",
      res["clusters"]["day"] >= 3 and res["clusters"]["coin"] >= 3,
      str(res["clusters"]))
check("a coin-cluster interval is reported as well",
      res.get("ci_lo_coin") is not None and res.get("ci_hi_coin") is not None)
check("the denominator is stated", bool(res.get("denominator")))

# --- independent recomputation of the paired difference and its interval ---
def recompute(events, champ_geom, cand_geom, rule=RULE):
    """The paired differences, computed here from the events alone."""
    diffs, days, coins, ch_vals, ca_vals = [], [], [], [], []
    for ev in events:
        side = ev.side if rule.policy == "cont" else -ev.side
        k_ch = GI_CH * 2 + (0 if side > 0 else 1)
        k_ca = GI_CA * 2 + (0 if side > 0 else 1)
        friction = DEFAULT.cost.round_trip(ev.qv24)
        dd_ch = B.stop_distance(ev.sigma, champ_geom.horizon_bars,
                                champ_geom.k_stop)
        dd_ca = B.stop_distance(ev.sigma, cand_geom.horizon_bars,
                                cand_geom.k_stop)
        ch = (ev.gross[k_ch] - friction) / dd_ch
        ca = (ev.gross[k_ca] - friction) / dd_ca
        ch_vals.append(ch)
        ca_vals.append(ca)
        diffs.append(ca - ch)
        days.append(int(ev.day))
        coins.append(ev.coin)
    return diffs, days, coins, ch_vals, ca_vals


diffs, days, coins, ch_vals, ca_vals = recompute(events, GEOM_CH, GEOM_CA)
m, lo, hi, ndays = S.day_cluster_mean(diffs, days, B=BOOT, seed=1)
check("the difference equals the difference of the means",
      abs(res["difference"] - (sum(ca_vals) / len(ca_vals)
                               - sum(ch_vals) / len(ch_vals))) < 1e-12,
      f"{res['difference']}")
check("the difference equals the mean of the paired differences",
      abs(res["difference"] - m) < 1e-12, f"{res['difference']} vs {m}")
check("the interval is the day-cluster interval of the paired differences",
      abs(res["ci_lo"] - lo) < 1e-12 and abs(res["ci_hi"] - hi) < 1e-12,
      f"{res['ci_lo']},{res['ci_hi']} vs {lo},{hi}")
check("the day cluster count matches", res["clusters"]["day"] == ndays,
      f"{res['clusters']['day']} vs {ndays}")
check("the champion mean matches the recomputation",
      abs(res["champion"]["mean_r"] - sum(ch_vals) / len(ch_vals)) < 1e-12)
check("the candidate mean matches the recomputation",
      abs(res["candidate"]["mean_r"] - sum(ca_vals) / len(ca_vals)) < 1e-12)
check("the metric is the friction-only one without a funding archive",
      res.get("metric") == "friction_net_r", str(res.get("metric")))

# --- the same comparison with a funding archive: the metric becomes net R ---
fund_dir = settlements_dir()
res_f = compare(champ_v, cand_v, events, WINDOW, funding_dir=fund_dir)
check("with a funding archive the metric is net R",
      res_f.get("metric") == "net_r", str(res_f.get("metric")))
check("the funded comparison is not refused", res_f.get("refused") is False,
      str(res_f.get("reason")))
check("the funded comparison still uses the same events",
      res_f["same_events"] is True and res_f["n_events"] == res["n_events"],
      f"{res_f['n_events']} vs {res['n_events']}")
check("funding moves the numbers (it is not ignored)",
      abs(res_f["champion"]["mean_r"] - res["champion"]["mean_r"]) > 1e-9,
      f"{res_f['champion']['mean_r']} vs {res['champion']['mean_r']}")
check("the funded comparison has a cost model that names the funding archive",
      res_f["cost_model"].get("funding_dir") == fund_dir,
      str(res_f["cost_model"].get("funding_dir")))

# --- the cost multipliers are applied to BOTH sides ---
res_slip = compare(champ_v, cand_v, events, WINDOW, slip_mult=2.0)
check("a doubled slippage changes both sides",
      res_slip["champion"]["mean_r"] < res["champion"]["mean_r"]
      and res_slip["candidate"]["mean_r"] < res["candidate"]["mean_r"],
      f"{res_slip['champion']['mean_r']} / {res_slip['candidate']['mean_r']}")
check("the cost model records the multiplier",
      res_slip["cost_model"].get("slip_mult") == 2.0,
      str(res_slip["cost_model"].get("slip_mult")))

# ---------------------------------------------------------------------------
# 4. refusals
# ---------------------------------------------------------------------------
section("4. compare: refusals (no silent numbers)")

wide_events = build_events(wide=True)
wide_ch = mk_version("cfg-0001", GEOM_CH)
wide_ca = mk_version("cfg-0003", GEOM_CA, params={
    "family": "breakout_start", "policy": "fade", "rvol_min": 2.0,
    "cusum_h": 5.0})
res_diff = compare(wide_ch, wide_ca, wide_events, WINDOW)
check("different event sets are refused", res_diff.get("refused") is True,
      str(res_diff.get("reason")))
check("the refusal states a reason", bool(res_diff.get("reason")))
check("the refusal reports no difference", res_diff["difference"] is None)
check("the refusal reports no interval",
      res_diff["ci_lo"] is None and res_diff["ci_hi"] is None)
check("the refusal reports same_events = False",
      res_diff["same_events"] is False)

res_cost = compare(mk_version("cfg-0001", GEOM_CH, config_hash="hash-a"),
                    mk_version("cfg-0002", GEOM_CA, config_hash="hash-b"),
                    events, WINDOW)
check("different cost models are refused", res_cost.get("refused") is True,
      str(res_cost.get("reason")))
check("the cost-model refusal states a reason", bool(res_cost.get("reason")))
check("the cost-model refusal reports no numbers",
      res_cost["difference"] is None and res_cost["same_cost_model"] is False)

untradeable = [mk_event(ev.coin, ev.ts, ev.side, ev.gross, kind=0, qv24=1e5)
                for ev in events]
res_none = compare(champ_v, cand_v, untradeable, WINDOW)
check("no evaluable outcome is refused", res_none.get("refused") is True,
      str(res_none.get("reason")))
check("the no-outcome refusal states a reason", bool(res_none.get("reason")))
check("the no-outcome refusal is not a zero",
      res_none["difference"] is None and res_none["n_events"] == 0,
      str(res_none["difference"]))

# ---------------------------------------------------------------------------
# 5. the decision chain
# ---------------------------------------------------------------------------
section("5. decision chain: append, hash chain, tamper detection")

chain_dir = temp_dir("wse-chain-")
chain = os.path.join(chain_dir, "chain.jsonl")
ev_dir = temp_dir("wse-evidence-")
ev_file = os.path.join(ev_dir, "evidence.txt")
with open(ev_file, "w") as fh:
    fh.write("evidence for the decision\n")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


ev_hash = sha256_file(ev_file)
e1 = DL.append(chain, kind="continue", subject="cfg-0001",
                reasons=["collecting evidence"], evidence=[],
                decided_by="tester", ts=1_700_000_000)
check("the first entry is DEC-0001", e1["id"] == "DEC-0001", e1["id"])
check("the first entry has no predecessor", e1["prev_hash"] is None)
check("the hash is 64 hex characters",
      isinstance(e1["hash"], str) and len(e1["hash"]) == 64
      and all(c in "0123456789abcdef" for c in e1["hash"]), e1["hash"])
check("the recorded hash is the entry's own hash",
      e1["hash"] == DL.entry_hash(e1))
check("the hash ignores the hash field itself",
      DL.entry_hash(dict(e1, hash="x" * 64)) == e1["hash"])
check("next_id continues the sequence", DL.next_id(chain) == "DEC-0002",
      DL.next_id(chain))

e2 = DL.append(chain, kind="hold", subject="cfg-0002",
                reasons=["sample too small"], evidence=[],
                hypothesis_id="HYD-0001", decided_by="tester",
                ts=1_700_000_100)
check("the second entry links to the first",
      e2["prev_hash"] == e1["hash"])
check("next_id sees both entries", DL.next_id(chain) == "DEC-0003")

e3 = DL.append(chain, kind="reject", subject="cfg-0002",
                reasons=["the holdout says no", "the interval covers zero"],
                evidence=[{"path": ev_file, "sha256": ev_hash}],
                decided_by="tester", ts=1_700_000_200)
check("the third entry links to the second", e3["prev_hash"] == e2["hash"])
check("the chain validates clean", DL.validate_chain(chain) == [],
      str(DL.validate_chain(chain)))
check("load returns the three entries", len(DL.load(chain)) == 3)
check("the file has one newline-terminated line per entry",
      open(chain).read().count("\n") == 3)
check("the kinds are the four-vocabulary set",
      set(DL.DECISION_KINDS) == {"promote", "reject", "continue", "hold"},
      str(DL.DECISION_KINDS))
try:
    DL.append(chain, kind="maybe", subject="x", reasons=["r"], evidence=[],
              decided_by="tester")
    check("an unknown kind is refused", False, "no error raised")
except Exception as exc:
    check("an unknown kind is refused", True)
try:
    DL.append(chain, kind="hold", subject="x", reasons=[], evidence=[],
              decided_by="tester")
    check("empty reasons are refused", False, "no error raised")
except Exception:
    check("empty reasons are refused", True)
try:
    DL.append(chain, kind="hold", subject="x", reasons=["r"], evidence=[],
              decided_by="")
    check("an empty decided_by is refused", False, "no error raised")
except Exception:
    check("an empty decided_by is refused", True)

section("6. decision chain: tampering is detected")

raw = open(chain).read().splitlines()
tampered = json.loads(raw[1])
tampered["value"] = 99                     # an out-of-band edit
lines = [raw[0], json.dumps(tampered, sort_keys=True), raw[2]]
with open(chain, "w") as fh:
    fh.write("\n".join(lines) + "\n")
errs = DL.validate_chain(chain)
check("the edited entry is reported (broken hash)",
      any("hash mismatch" in e for e in errs), str(errs))
check("the broken link is reported",
      any("broken link" in e for e in errs), str(errs))
check("the chain is not valid after the edit", len(errs) >= 2, str(errs))
try:
    DL.append(chain, kind="continue", subject="cfg-0002",
              reasons=["after a tamper"], evidence=[], decided_by="tester")
    check("appending to a broken chain is refused", False, "no error raised")
except Exception:
    check("appending to a broken chain is refused", True)
check("the broken chain was not extended",
      len(open(chain).read().splitlines()) == 3)

section("7. decision chain: evidence is verified at write time")

# a FRESH chain: the one above was tampered with on purpose
chain2 = os.path.join(temp_dir("wse-chain2-"), "chain.jsonl")
ev2 = os.path.join(ev_dir, "evidence2.txt")
with open(ev2, "w") as fh:
    fh.write("stable evidence\n")
good = DL.append(chain2, kind="continue", subject="cfg-0003",
                 reasons=["evidence checked"],
                 evidence=[{"path": ev2, "sha256": sha256_file(ev2)}],
                 decided_by="tester", ts=1_700_000_300)
check("a valid evidence file is accepted", good["id"] == "DEC-0001",
      good["id"])
check("the chain with the valid evidence validates clean",
      DL.validate_chain(chain2) == [], str(DL.validate_chain(chain2)))
with open(ev2, "w") as fh:
    fh.write("CHANGED after the decision\n")
try:
    DL.append(chain2, kind="continue", subject="cfg-0004",
              reasons=["stale evidence"], evidence=[
                  {"path": ev2, "sha256": sha256_file(ev2)}],
              decided_by="tester", ts=1_700_000_400)
    check("a changed evidence file is refused", False, "no error raised")
except Exception:
    check("a changed evidence file is refused", True)
stale_hash = sha256_file(ev2)
try:
    DL.append(chain2, kind="continue", subject="cfg-0004",
              reasons=["stale evidence"], evidence=[
                  {"path": ev2, "sha256": stale_hash}],
              decided_by="tester", ts=1_700_000_400)
    check("a stale evidence hash is refused", False, "no error raised")
except Exception:
    check("a stale evidence hash is refused", True)
try:
    DL.append(chain2, kind="continue", subject="cfg-0004",
              reasons=["missing evidence"], evidence=[
                  {"path": os.path.join(ev_dir, "nope.txt"),
                   "sha256": "0" * 64}],
              decided_by="tester", ts=1_700_000_400)
    check("a missing evidence file is refused", False, "no error raised")
except Exception:
    check("a missing evidence file is refused", True)
check("the refusals did not write anything",
      len(open(chain2).read().splitlines()) == 1,
      str(len(open(chain2).read().splitlines())))

section("8. decision chain: validate_chain finds every defect")


def raw_chain(entries):
    d = temp_dir("wse-defect-")
    p = os.path.join(d, "chain.jsonl")
    with open(p, "w") as fh:
        for e in entries:
            fh.write(json.dumps(e, sort_keys=True) + "\n")
    return p


def good_entry(**kw):
    e = {"id": "DEC-0001", "ts": 1_700_000_000, "kind": "hold",
         "subject": "cfg-0001", "hypothesis_id": None,
         "reasons": ["because"], "decided_by": "tester",
         "evidence": [], "prev_hash": None}
    e.update(kw)
    e["hash"] = DL.entry_hash(e)
    return e


dup = [good_entry(), good_entry(id="DEC-0001", ts=1_700_000_001)]
check("a duplicate id is reported",
      any("duplicate-id" in e for e in DL.validate_chain(raw_chain(dup))),
      str(DL.validate_chain(raw_chain(dup))))
bad_id = [good_entry(id="DEC-1")]
check("a bad id format is reported",
      any("bad-id" in e for e in DL.validate_chain(raw_chain(bad_id))),
      str(DL.validate_chain(raw_chain(bad_id))))
back = [good_entry(ts=1_700_000_000), good_entry(id="DEC-0002",
                                                 ts=1_699_999_999,
                                                 prev_hash=None)]
check("a non-monotonic timestamp is reported",
      any("non-monotonic" in e
          for e in DL.validate_chain(raw_chain(back))),
      str(DL.validate_chain(raw_chain(back))))
kind = [good_entry(kind="maybe")]
check("an unknown kind is reported",
      any("unknown kind" in e for e in DL.validate_chain(raw_chain(kind))),
      str(DL.validate_chain(raw_chain(kind))))
reasons = [good_entry(reasons=[])]
check("empty reasons are reported",
      any("reasons" in e for e in DL.validate_chain(raw_chain(reasons))),
      str(DL.validate_chain(raw_chain(reasons))))
decider = [good_entry(decided_by="")]
check("an empty decided_by is reported",
      any("decided_by" in e for e in DL.validate_chain(raw_chain(decider))),
      str(DL.validate_chain(raw_chain(decider))))
ev_bad = [good_entry(evidence=[{"path": "x", "sha256": "nothex"}])]
check("an evidence entry without a 64-hex sha256 is reported",
      any("sha256" in e for e in DL.validate_chain(raw_chain(ev_bad))),
      str(DL.validate_chain(raw_chain(ev_bad))))
edited = good_entry()
edited["reasons"] = ["changed"]
check("an edited entry is reported (the hash no longer matches)",
      any("hash mismatch" in e
          for e in DL.validate_chain(raw_chain([edited]))),
      str(DL.validate_chain(raw_chain([edited]))))
check("an empty chain validates clean",
      DL.validate_chain(os.path.join(temp_dir("wse-empty-"), "chain.jsonl")) == [])

# ---------------------------------------------------------------------------
# 9. the repository is untouched
# ---------------------------------------------------------------------------
section("9. the repository is untouched")

decisions_dir = os.path.join(ROOT, "docs", "decisions")
check("the human-owned log still exists",
      os.path.isfile(os.path.join(decisions_dir, "log.jsonl")))
check("the human-owned log is still 0 bytes",
      os.path.getsize(os.path.join(decisions_dir, "log.jsonl")) == 0,
      str(os.path.getsize(os.path.join(decisions_dir, "log.jsonl"))))
check("the chain file was NOT created in the repository",
      not os.path.exists(os.path.join(decisions_dir, "chain.jsonl")))
check("docs/decisions holds only the human-owned log",
      sorted(os.listdir(decisions_dir)) == ["log.jsonl"],
      str(sorted(os.listdir(decisions_dir))))
check("data/promotions.json was not created or modified",
      not os.path.exists(os.path.join(ROOT, "data", "promotions.json")))
stray = []
for base, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".venv-qt")]
    for f in files:
        if f.endswith(".db") or f == "chain.jsonl" or f == "promotions.json":
            stray.append(os.path.join(base, f))
check("no registry database or chain file was created in the repository",
      not stray, str(stray))

print()
if FAILURES:
    print(f"FAIL  tests/test_ws_e_champion.py  "
          f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
    sys.exit(1)
print(f"ALL PASS  tests/test_ws_e_champion.py  ({CHECKS} checks)")
