"""Workstream C: preregistration records + sliced research report.

Self-checking script: `python3 tests/test_ws_c_prereg.py`.

Covers:
  * `proto.quant.prereg` -- the record, the append-only variant registry,
    the hash helpers, the split/embargo discipline and the evidence checks;
  * `proto.quant.research_report` -- sliced results with denominators,
    the `insufficient` label for tiny cells and the sealed holdout;
  * the shipped 288-configuration record (evidence sha256 + quoted numbers
    re-read from the preserved artifact);
  * the existing markdown registry still validating clean.

Nothing here writes to `data/` and nothing touches the one-shot holdout.
"""

import array
import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import hypothesis_registry as hr
from proto.quant import prereg
from proto.quant import research_report as rr
from proto.quant import barrier as B
from proto.quant.config import Geometry, RuleParams, grid_geometries
from proto.quant.research import Event

FAILURES = []

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIP_DIR = os.path.join(ROOT, "docs", "hypotheses")
ARTIFACT = "docs/implementation-reports/quant-roadmap-2026-10-18/evaluation.json"
ARTIFACT_PATH = os.path.join(ROOT, ARTIFACT)

GEOMS = grid_geometries()
GEOM = Geometry(288, 3.0, 2.0)
RULE = RuleParams("breakout_start", "cont", rvol_min=4.0)


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def geom_index(geom):
    for i, g in enumerate(GEOMS):
        if (g.horizon_bars, g.k_stop, g.r_tp) == \
                (geom.horizon_bars, geom.k_stop, geom.r_tp):
            return i
    raise AssertionError("geometry not in the declared grid")


def make_event(coin, ts, day, side=1, sigma=0.001, qv24=50e6, rvol=5.0,
               fam="B", param=4.0, gross_r=0.5, geom=GEOM):
    """One synthetic research event with the stored triple-barrier slots."""
    ev = Event()
    ev.coin, ev.ts, ev.day = coin, ts, day
    ev.fam, ev.param, ev.side = fam, param, side
    ev.qv24, ev.sigma, ev.rvol = qv24, sigma, rvol
    ev.z1h, ev.z4h, ev.btc7d = 0.0, 0.0, 0.0
    n = len(GEOMS) * 2
    ev.gross = array.array("d", [0.0] * n)
    ev.kind = array.array("b", [2] * n)
    ev.exit_offset = array.array("H", [0] * n)
    k = geom_index(geom) * 2 + (0 if side > 0 else 1)
    ev.gross[k] = gross_r * B.stop_distance(sigma, geom.horizon_bars, geom.k_stop)
    return ev


def fixture_events():
    """~250 events on days 10..50 (inside the dev window), 6 coins."""
    events = []
    coins = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    for d in range(10, 51):
        for ci, coin in enumerate(coins):
            sigma = 0.001 + 0.0001 * ((d + ci) % 7)
            qv24 = 50e6
            if d in (10, 11, 12, 13, 14) and ci == 0:
                qv24 = 2e6                      # a thin-liquidity group
            side = 1 if (d + ci) % 2 == 0 else -1
            events.append(make_event(coin, int(d * 86400), float(d), side=side,
                                     sigma=sigma, qv24=qv24))
    return events


def make_record(**over):
    """A complete, valid 'registered' preregistration record."""
    rec = prereg.new_record(
        id="HYD-0002",
        title="Breakout continuation carries a positive net edge",
        status="registered",
        hypothesis="Pre-declared continuation rules have positive net R after costs.",
        feature_definitions=[{"name": "sigma_bar", "version": "feat-v1",
                              "definition": "EWMA Garman-Klass per-bar sigma",
                              "params": {"lam_sigma": 0.985}}],
        universe="all archived altcoin perps, BTC excluded",
        point_in_time_membership="a coin is a member while it has bars",
        horizons=["24h"],
        event_rules=[{"rule_id": "breakout_start[rvol>=4]/cont",
                      "family": "breakout_start",
                      "params": {"rvol_min": 4.0}, "side": "cont"}],
        costs={"fee_per_side": 0.0005,
               "slip_tiers": [[30000000, 0.00015], [10000000, 0.0003],
                              [3000000, 0.0006], [1000000, 0.0012]],
               "funding": "none", "source": "proto/quant/config.py CostModel"},
        splits={"dev": [6.0, 61.0], "val": [63.0, 77.0], "holdout": [79.0, 98.3]},
        embargo_days=2.0,
        statistics={"metric": "mean_r", "threshold": 0.0,
                    "denominator": "tradeable_events_in_window",
                    "test": "day-cluster bootstrap + max-t SPA"},
        promotion_criteria=[{"name": "spa_p_value", "test": "max-t SPA on dev",
                             "threshold": 0.10}],
        family=["HYD-0002"],
        config_hash="d33cff513c30",
        data_hash="a" * 64,
        code_hash="b" * 64,
        variants=[],
    )
    if over:
        from dataclasses import replace
        rec = replace(rec, **over)
    return rec


def make_variant(variant_id="v1", value=-0.0154, evidence=None, status="failed"):
    return prereg.Variant(
        variant_id=variant_id,
        hypothesis_id="HYD-0002",
        description="the completed 288-configuration sweep",
        config_hash="d33cff513c30",
        result={"status": status, "metric": "mean_r", "value": value,
                "n": 24749, "ci_lo": -0.04171, "ci_hi": 0.01424,
                "detail": {"reason": "no positive edge"}},
        tested_ts=1792409830,
        evidence=evidence or [])


# --------------------------------------------------------------- hash helpers
print("=== hash helpers ===")
tmp = tempfile.mkdtemp()
f1 = os.path.join(tmp, "a.txt")
f2 = os.path.join(tmp, "b.txt")
with open(f1, "wb") as fh:
    fh.write(b"alpha")
with open(f2, "wb") as fh:
    fh.write(b"beta")

check("file_sha256 matches hashlib",
      prereg.file_sha256(f1) == hashlib.sha256(b"alpha").hexdigest(),
      prereg.file_sha256(f1))
check("file_sha256 differs per file",
      prereg.file_sha256(f1) != prereg.file_sha256(f2))

dh1 = prereg.data_hash({"b": f2, "a": f1})
dh2 = prereg.data_hash({"a": f1, "b": f2})
check("data_hash is order independent", dh1 == dh2, (dh1, dh2))
check("data_hash is a 64-hex digest",
      len(dh1) == 64 and all(c in "0123456789abcdef" for c in dh1), dh1)
check("data_hash changes with content",
      prereg.data_hash({"a": f1}) != prereg.data_hash({"a": f2}))

missing = os.path.join(tmp, "nope.txt")
raised = False
try:
    prereg.data_hash({"missing": missing})
except FileNotFoundError as exc:
    raised = "nope.txt" in str(exc)
except Exception:                                   # pragma: no cover
    raised = False
check("data_hash raises a clear error on a missing file", raised)

ch = prereg.code_hash(ROOT, ["proto/quant/prereg.py", "proto/quant/config.py"])
check("code_hash is a 64-hex digest",
      len(ch) == 64 and all(c in "0123456789abcdef" for c in ch), ch)
ch2 = prereg.code_hash(ROOT, ["proto/quant/config.py", "proto/quant/prereg.py"])
check("code_hash is order independent", ch == ch2)
check("code_hash changes when the file set changes",
      ch != prereg.code_hash(ROOT, ["proto/quant/config.py"]))

# code_hash_at pins the fingerprint to a revision instead of the worktree
head = prereg.git_rev(ROOT)
clean_files = ["proto/quant/config.py", "proto/quant/stats.py",
               "proto/quant/bars.py"]
check("code_hash_at agrees with code_hash on a clean tree",
      prereg.code_hash_at(ROOT, head, clean_files) ==
      prereg.code_hash(ROOT, clean_files),
      f"{head}")
raised = False
try:
    prereg.code_hash_at(ROOT, head, ["proto/quant/does_not_exist.py"])
except FileNotFoundError:
    raised = True
check("code_hash_at raises for a file missing at the revision", raised)

# ------------------------------------------------------------- record + rules
print("\n=== record validation ===")
rec = make_record()
errs = prereg.validate(rec)
check("a complete registered record validates clean", errs == [], str(errs))

REQUIRED_STR = ("title", "hypothesis", "universe", "point_in_time_membership",
                 "config_hash", "data_hash", "code_hash")
REQUIRED_LIST = ("feature_definitions", "horizons", "event_rules", "family")
REQUIRED_DICT = ("costs", "splits", "statistics")
missing_errors = []
for field in REQUIRED_STR:
    e = prereg.validate(make_record(**{field: ""}))
    if f"required-field: {field} is required" not in e:
        missing_errors.append((field, e))
for field in REQUIRED_LIST:
    e = prereg.validate(make_record(**{field: []}))
    if f"required-field: {field} is required" not in e:
        missing_errors.append((field, e))
for field in REQUIRED_DICT:
    e = prereg.validate(make_record(**{field: {}}))
    if f"required-field: {field} is required" not in e:
        missing_errors.append((field, e))
check("every missing required field is reported by name",
      not missing_errors, str(missing_errors))

check("an unknown status is rejected",
      any("unknown-status" in e for e in prereg.validate(make_record(status="bogus"))),
      str(prereg.validate(make_record(status="bogus"))))
check("a bad id is rejected",
      any("bad-id" in e for e in prereg.validate(make_record(id="HYD-99"))),
      str(prereg.validate(make_record(id="HYD-99"))))
check("a non-hex config_hash is rejected",
      any("bad-hash" in e for e in prereg.validate(make_record(config_hash="zzz"))),
      str(prereg.validate(make_record(config_hash="zzz"))))

print("\n=== splits, embargo and family ===")
bad_order = make_record(splits={"dev": [6.0, 61.0], "val": [63.0, 50.0],
                                 "holdout": [79.0, 98.3]})
check("a reversed window is rejected",
      any("within-window" in e for e in prereg.validate(bad_order)),
      str(prereg.validate(bad_order)))
bad_seq = make_record(splits={"dev": [6.0, 70.0], "val": [63.0, 77.0],
                               "holdout": [79.0, 98.3]})
check("dev_end after val_start is an ordering error",
      any("ordering" in e for e in prereg.validate(bad_seq)),
      str(prereg.validate(bad_seq)))
touching = make_record(splits={"dev": [6.0, 61.0], "val": [61.0, 77.0],
                               "holdout": [79.0, 98.3]})
check("a touching dev/val boundary fails the embargo",
      any("embargo" in e for e in prereg.validate(touching)),
      str(prereg.validate(touching)))
short_emb = make_record(embargo_days=0.5)
check("too small an embargo is rejected",
      any("embargo" in e for e in prereg.validate(short_emb)),
      str(prereg.validate(short_emb)))
week = make_record(horizons=["7d"], embargo_days=2.0)
check("a 7d horizon requires a 7d embargo",
      any("embargo" in e for e in prereg.validate(week)),
      str(prereg.validate(week)))
week_ok = make_record(horizons=["7d"], embargo_days=7.0,
                      splits={"dev": [6.0, 61.0], "val": [70.0, 84.0],
                              "holdout": [92.0, 98.3]})
check("a 7d horizon with a 7d embargo and 9d gaps validates",
      prereg.validate(week_ok) == [], str(prereg.validate(week_ok)))
check("an empty family is rejected",
      any("family" in e for e in prereg.validate(make_record(family=[]))),
      str(prereg.validate(make_record(family=[]))))
check("an unknown hypothesis id in the family is rejected",
      any("unknown-hypothesis" in e
          for e in prereg.validate(make_record(family=["HYD-9999"]),
                                   known_hypothesis_ids=["HYD-0001"])),
      str(prereg.validate(make_record(family=["HYD-9999"]),
                          known_hypothesis_ids=["HYD-0001"])))
check("a known hypothesis id in the family validates",
      prereg.validate(make_record(family=["HYD-0001"]),
                      known_hypothesis_ids=["HYD-0001"]) == [],
      str(prereg.validate(make_record(family=["HYD-0001"]),
                          known_hypothesis_ids=["HYD-0001"])))

# --------------------------------------------------------- variant + evidence
print("\n=== variant registry ===")
tmp2 = tempfile.mkdtemp()
evidence_file = os.path.join(tmp2, "evidence.json")
with open(evidence_file, "w") as fh:
    fh.write('{"hello": "world"}')
real_sha = hashlib.sha256(open(evidence_file, "rb").read()).hexdigest()

good = make_variant(evidence=[{"path": evidence_file, "sha256": real_sha}])
rec_good = make_record(status="testing", variants=[good])
check("a record with a tested variant validates clean",
      prereg.validate(rec_good) == [], str(prereg.validate(rec_good)))

wrong = make_variant(evidence=[{"path": evidence_file, "sha256": "0" * 64}])
e = prereg.validate(make_record(status="testing", variants=[wrong]))
check("a wrong evidence sha256 is flagged",
      any("evidence-hash-mismatch" in x for x in e), str(e))

short_sha = make_variant(evidence=[{"path": evidence_file, "sha256": "abc"}])
e = prereg.validate(make_record(status="testing", variants=[short_sha]))
check("a non-64-hex evidence hash is flagged",
      any("bad-evidence-hash" in x for x in e), str(e))

missing_ev = make_variant(evidence=[{"path": os.path.join(tmp2, "gone.json"),
                                     "sha256": real_sha}])
e = prereg.validate(make_record(status="testing", variants=[missing_ev]))
check("a missing evidence file is flagged",
      any("evidence-missing" in x for x in e), str(e))

with open(evidence_file, "a") as fh:
    fh.write("tampered")
e = prereg.validate(make_record(status="testing", variants=[good]))
check("a modified evidence file is detected by re-hashing",
      any("evidence-hash-mismatch" in x for x in e), str(e))

no_denom = make_variant()
no_denom = prereg.Variant(**{**no_denom.__dict__,
                              "result": {"status": "passed", "metric": "mean_r",
                                         "value": 1.0, "n": 0, "ci_lo": None,
                                         "ci_hi": None, "detail": {}}})
e = prereg.validate(make_record(status="testing", variants=[no_denom]))
check("a non-pending variant without a denominator is flagged",
      any("denominator" in x for x in e), str(e))

pending = prereg.Variant(**{**make_variant().__dict__,
                            "result": {"status": "pending", "metric": "mean_r",
                                       "value": None, "n": 0, "ci_lo": None,
                                       "ci_hi": None, "detail": {}}})
check("a pending variant needs no denominator",
      prereg.validate(make_record(status="testing", variants=[pending])) == [],
      str(prereg.validate(make_record(status="testing", variants=[pending]))))

dup = make_record(status="testing",
                  variants=[make_variant(value=1.0), make_variant(value=2.0)])
e = prereg.validate(dup)
check("a duplicated variant id with a changed result is flagged",
      any("variant-result-changed" in x for x in e), str(e))

print("\n=== register_variant is append-only ===")
tmp3 = tempfile.mkdtemp()
base = make_record(status="testing", variants=[])
path = prereg.save(base, tmp3)
check("save writes <dir>/<ID>.json", os.path.isfile(path) and
      os.path.basename(path) == "HYD-0002.json", path)

r1 = prereg.register_variant(base, make_variant(evidence=[]), tmp3)
check("a new variant is appended", len(r1.variants) == 1, str(r1.variants))
stored = prereg.load(tmp3, "HYD-0002")
check("the appended variant is persisted",
      len(stored.variants) == 1 and stored.variants[0].variant_id == "v1",
      str(stored.variants))
check("the record round-trips through save/load",
      stored.id == r1.id and stored.splits == r1.splits and
      stored.costs == r1.costs and stored.family == r1.family and
      stored.variants[0].result == r1.variants[0].result,
      str(stored))

before = open(path, "rb").read()
r2 = prereg.register_variant(r1, make_variant(evidence=[]), tmp3)
check("re-registering an identical variant is idempotent",
      len(r2.variants) == 1 and r2.variants == r1.variants, str(r2.variants))
check("the idempotent re-registration does not rewrite the file",
      open(path, "rb").read() == before)

raised = False
try:
    prereg.register_variant(r1, make_variant(value=0.5, evidence=[]), tmp3)
except prereg.PreregError as exc:
    raised = "variant-result-changed" in str(exc)
check("re-registering a changed result is refused", raised)
after = prereg.load(tmp3, "HYD-0002")
check("the refused registration did not overwrite the stored result",
      len(after.variants) == 1 and after.variants[0].result["value"] == -0.0154,
      str(after.variants))

check("load_all finds the record",
      [r.id for r in prereg.load_all(tmp3)] == ["HYD-0002"],
      str([r.id for r in prereg.load_all(tmp3)]))

# --------------------------------------------------------------- sliced report
print("\n=== sliced research report ===")
events = fixture_events()
report = rr.slice_results(events, RULE, GEOM, (6.0, 61.0))
check("the report is produced", isinstance(report, dict), str(type(report)))
check("the report carries its version",
      report.get("report_version") == rr.RESEARCH_REPORT_VERSION,
      str(report.get("report_version")))
check("the report carries the config hash",
      report.get("config_hash") == rr.DEFAULT.config_hash(),
      str(report.get("config_hash")))
check("the report carries a code hash",
      isinstance(report.get("code_hash"), str) and
      len(report.get("code_hash")) == 64, str(report.get("code_hash")))
check("the report has a data_hash slot", "data_hash" in report,
      str(sorted(report.keys())))

overall = report.get("overall") or {}
check("the overall block has n, mean and an interval",
      overall.get("n") and overall.get("mean") is not None and
      overall.get("ci_lo") is not None and overall.get("ci_hi") is not None,
      str(overall))
check("the overall block reports day and coin denominators",
      overall.get("n_days", 0) > 0 and overall.get("n_coins", 0) > 0,
      str(overall))

slices = report.get("slices") or {}
check("the report has a coin slice", "coin" in slices, str(sorted(slices)))
check("the report has a period slice", "period" in slices, str(sorted(slices)))
check("the report has a regime slice", "regime" in slices, str(sorted(slices)))
check("the report has a liquidity slice", "liquidity" in slices,
      str(sorted(slices)))

coin_slice = slices.get("coin") or {}
check("the coin slice reports the top and the bottom",
      coin_slice.get("top") and coin_slice.get("bottom"), str(coin_slice))
check("every coin entry carries its n",
      all("n" in c for c in coin_slice.get("top", []) + coin_slice.get("bottom", [])),
      str(coin_slice))

periods = slices.get("period") or []
check("the window is split into 5 chronological sub-periods",
      len(periods) == 5, str(len(periods)))
check("the sub-periods are ordered and disjoint",
      all(periods[i]["lo"] < periods[i]["hi"] for i in range(len(periods))) and
      all(abs(periods[i]["hi"] - periods[i + 1]["lo"]) < 1e-9
          for i in range(len(periods) - 1)), str(periods))

regimes = slices.get("regime") or []
check("the regime slice classifies events into volatility groups",
      len(regimes) >= 3 and sum(c["n"] for c in regimes) == overall.get("n"),
      str(regimes))
liquidity = slices.get("liquidity") or []
check("the liquidity slice uses the slip-tier boundaries",
      len(liquidity) >= 4, str(liquidity))
check("the liquidity groups cover every selected event",
      sum(c.get("n_events", 0) for c in liquidity) >= overall.get("n", 0),
      str(liquidity))

thin = [c for c in liquidity if c.get("status") == "insufficient"]
check("a cell below min_cell is labelled insufficient", bool(thin), str(liquidity))
check("an insufficient cell reports no mean",
      all("mean" not in c for c in thin), str(thin))
check("an insufficient cell reports its n and min_cell",
      all(c.get("min_cell") == 30 and "n" in c for c in thin), str(thin))

small = rr.slice_results(events, RULE, GEOM, (6.0, 61.0), min_cell=100000)
check("a huge min_cell makes every cell insufficient",
      (small.get("overall") or {}).get("status") == "insufficient",
      str(small.get("overall")))
check("an insufficient overall block reports no mean",
      "mean" not in (small.get("overall") or {}), str(small.get("overall")))

print("\n=== the holdout stays sealed ===")
sealed = rr.slice_results(events, RULE, GEOM, (79.0, 98.3))
check("a window inside the holdout is refused",
      sealed.get("status") == "refused", str(sealed.get("status")))
check("the refusal states a reason",
      "holdout" in str(sealed.get("reason", "")).lower(),
      str(sealed.get("reason")))
check("the refusal reports no metrics",
      sealed.get("overall") is None and not sealed.get("slices"),
      str(sealed)[:200])
overlap = rr.slice_results(events, RULE, GEOM, (70.0, 90.0))
check("a window that reaches into the holdout is refused",
      overlap.get("status") == "refused", str(overlap.get("status")))

src = open(os.path.join(ROOT, "proto", "quant", "research_report.py"),
           "r", encoding="utf-8").read()
tree = ast.parse(src)
docstrings = set()
for node in ast.walk(tree):
    if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                         ast.ClassDef)):
        doc = ast.get_docstring(node, clean=False)
        if doc:
            docstrings.add(doc)
literals = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value not in docstrings]
check("no code literal mentions the holdout lock",
      not any("holdout_evaluated" in s for s in literals),
      str([s for s in literals if "holdout_evaluated" in s]))
opens = [n for n in ast.walk(tree)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
         and n.func.id == "open"]
check("the report module opens no files at all", not opens,
      str([ast.dump(o) for o in opens]))

# ---------------------------------------------------- the shipped 288-config record
print("\n=== the shipped 288-configuration record ===")
known_ids = {e["id"] for e in hr.load_registry(SHIP_DIR)
             if isinstance(e.get("id"), str)}
entries, md_errors = hr.load_and_validate(SHIP_DIR)
check("the markdown registry still validates clean", md_errors == [], str(md_errors))
check("HYD-0001 is still present with status testing",
      any(e.get("id") == "HYD-0001" and e.get("status") == "testing"
          for e in entries),
      str([(e.get("id"), e.get("status")) for e in entries]))

shipped = prereg.load_all(SHIP_DIR)
check("the shipped preregistration record loads", len(shipped) >= 1,
      str(len(shipped)))
for r in shipped:
    e = prereg.validate(r, known_hypothesis_ids=known_ids)
    check(f"{r.id} validates clean", e == [], str(e))

artifact = json.load(open(ARTIFACT_PATH))
artifact_sha = hashlib.sha256(open(ARTIFACT_PATH, "rb").read()).hexdigest()
reg = [r for r in shipped if r.id == "HYD-0002"]
check("the 288-config record is registered", bool(reg), str([r.id for r in shipped]))
if reg:
    r = reg[0]
    v = r.variants[0]
    check("the evidence path is the preserved artifact",
          v.evidence[0]["path"] == ARTIFACT, str(v.evidence))
    check("the evidence sha256 is the real digest of the artifact",
          v.evidence[0]["sha256"] == artifact_sha,
          f"{v.evidence[0]['sha256']} != {artifact_sha}")
    check("the quoted value is the artifact's dev mean_r",
          v.result["value"] == artifact["dev"]["mean_r"],
          f"{v.result['value']} != {artifact['dev']['mean_r']}")
    check("the quoted n is the artifact's dev n",
          v.result["n"] == artifact["dev"]["n"],
          f"{v.result['n']} != {artifact['dev']['n']}")
    check("the quoted interval is the artifact's dev interval",
          v.result["ci_lo"] == artifact["dev"]["ci_lo"] and
          v.result["ci_hi"] == artifact["dev"]["ci_hi"],
          f"{v.result['ci_lo']}/{v.result['ci_hi']}")
    check("the variant status is failed", v.result["status"] == "failed",
          str(v.result["status"]))
    check("the failure reason is stated",
          v.result["detail"].get("reason") == "no positive edge",
          str(v.result["detail"].get("reason")))
    check("the record states that the holdout was not evaluated",
          v.result["detail"].get("holdout_evaluated") is False and
          artifact["holdout"] is None,
          str(v.result["detail"].get("holdout_evaluated")))
    check("the record quotes the 288 configurations",
          v.result["detail"].get("n_configs") == artifact["n_configs"] == 288,
          str(v.result["detail"].get("n_configs")))
    check("the record quotes the SPA p-value",
          v.result["detail"]["spa"]["p_value"] == artifact["spa"]["p_value"],
          str(v.result["detail"].get("spa")))
    check("the record carries the run's config hash",
          r.config_hash == artifact["config_hash"],
          f"{r.config_hash} != {artifact['config_hash']}")
    check("the record carries real hashes",
          len(r.data_hash) == 64 and len(r.code_hash) == 64,
          f"{r.data_hash} / {r.code_hash}")
    check("the record's code hash is pinned to the sweep's revision",
          prereg.code_hash_at(ROOT, r.statistics["code_git_rev"],
                              r.statistics["code_files"]) == r.code_hash,
          f"{r.statistics['code_git_rev']}")

# ------------------------------------------------------------------------ CLI
print("\n=== prereg CLI ===")
env = dict(os.environ)
env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
proc = subprocess.run([sys.executable, "-m", "proto.quant", "prereg", "validate",
                       "--dir", "docs/hypotheses"], cwd=ROOT, env=env,
                      capture_output=True, text=True)
check("prereg validate exits 0 on a clean directory", proc.returncode == 0,
      proc.stdout + proc.stderr)
check("prereg validate prints the record", "HYD-0002" in proc.stdout,
      proc.stdout)
proc = subprocess.run([sys.executable, "-m", "proto.quant", "prereg", "show",
                       "HYD-0002", "--dir", "docs/hypotheses"], cwd=ROOT, env=env,
                      capture_output=True, text=True)
check("prereg show exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
try:
    shown = json.loads(proc.stdout)
    ok = shown.get("id") == "HYD-0002"
except ValueError:
    shown, ok = None, False
check("prereg show prints the record as JSON", ok, proc.stdout[:400])
proc = subprocess.run([sys.executable, "-m", "proto.quant", "prereg", "show",
                       "HYD-9999", "--dir", "docs/hypotheses"], cwd=ROOT, env=env,
                      capture_output=True, text=True)
check("prereg show exits 1 for an unknown record", proc.returncode == 1,
      proc.stdout + proc.stderr)

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
