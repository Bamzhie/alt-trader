"""Hypothesis registry reader + validator (WS5-1a/1b).

Self-checking script: `python3 tests/test_hypotheses.py`.
Covers flat-mapping parse incl. quoted ':'/'#', the eight parse
rejections, validator fixtures (ordering-touching-but-embargo-fail,
reversed window, missing denominator, bad status, duplicate id,
non-UTF8/CRLF, missing/unclosed front-matter), empty dir, the shipped
`docs/hypotheses/HYD-0001` example validating clean, and the
Task 4 <-> Task 5 wiring (`load_registry` ids fed into `validate_log`).
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import decisions as dec
from proto import hypothesis_registry as hr

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def write(tmp, filename, text, mode="w"):
    path = os.path.join(tmp, filename)
    if mode == "w":
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    else:
        with open(path, "wb") as fh:
            fh.write(text)
    return path


def front(body_fields):
    return "---\n" + body_fields + "---\n\nBody prose.\n"


BASE_FIELDS = """id: HYD-0001
title: Funding quintile drift
status: testing
prediction: Top funding quintile drifts up over 24h
universe: all MEXC USDT perps in build_universe, BTC excluded
horizon: 24h
dev_start: 2024-01-01
dev_end: 2024-02-01
val_start: 2024-02-05
val_end: 2024-03-01
holdout_start: 2024-03-10
metric: median_forward_return_4h
threshold: 0.52
denominator: resolved_episodes
costs: taker 0.05pct per side, funding estimate stated
comparisons: 3
rule_version: v1
config_hash: abc123
"""


def variant(tmp, filename, fields):
    return write(tmp, filename, front(fields))


print("=== flat-mapping parse ===")
tmp = tempfile.mkdtemp()
variant(tmp, "HYD-0001-ok.md", BASE_FIELDS)
entries, errors = hr.load_and_validate(tmp)
check("valid testing entry parses", len(entries) == 1, str((entries, errors)))
check("valid testing entry clean", errors == [], str(errors))
check("entry gains _source_file", entries and entries[0].get("_source_file") == "HYD-0001-ok.md",
      str(entries))
check("validator ignores _-prefixed keys",
      entries and hr.validate(dict(entries[0], _junk="x")) == [], str(entries and hr.validate(dict(entries[0], _junk="x"))))

tmp = tempfile.mkdtemp()
quoted = BASE_FIELDS.replace(
    "title: Funding quintile drift",
    'title: "Drift: funding #1 quintile"')
variant(tmp, "HYD-0001-q.md", quoted)
entries, errors = hr.load_and_validate(tmp)
check("quoted value with : and # parses", errors == [] and len(entries) == 1, str((entries, errors)))
check("quoted value kept whole",
      entries and entries[0].get("title") == "Drift: funding #1 quintile",
      str(entries and entries[0].get("title")))

tmp = tempfile.mkdtemp()
commented = BASE_FIELDS + "# trailing comment line\n\n"
variant(tmp, "HYD-0001-c.md", commented)
entries, errors = hr.load_and_validate(tmp)
check("comments and blank lines ignored", errors == [] and len(entries) == 1, str(errors))

print("\n=== parse rejections ===")
REJECTIONS = [
    ("indent.md", "  title: indented\n", "indentation"),
    ("dash.md", "tags:\n- alpha\n", "dash-list"),
    ("bracket.md", "tags: [alpha, beta]\n", "[ ]-list"),
    ("pipe.md", "prediction: |\n", "multiline marker"),
    ("gt.md", "prediction: >\n", "multiline marker"),
    ("anchor.md", "title: &anchor value\n", "anchor"),
    ("tab.md", "\ttitle: tabbed\n", "tab indentation"),
    ("dupkey.md", "title: one\ntitle: two\n", "duplicate key"),
    ("nested.md", "extra:\n  subkey: 1\n", "nested mapping"),
]
for filename, snippet, label in REJECTIONS:
    tmp = tempfile.mkdtemp()
    variant(tmp, filename, snippet)
    entries, errors = hr.load_and_validate(tmp)
    ok = (len(entries) == 0 and len(errors) >= 1
          and all(e.startswith("parse: " + filename + ":") for e in errors))
    if label == "nested mapping":
        ok = ok and any("nested" in e for e in errors)
    check(f"reject {label} ({filename})", ok, str((entries, errors)))

print("\n=== validator fixtures ===")
tmp = tempfile.mkdtemp()
touching = BASE_FIELDS.replace("val_start: 2024-02-05", "val_start: 2024-02-01")
variant(tmp, "HYD-0010-touch.md", touching)
entries, errors = hr.load_and_validate(tmp)
rules = [e.split(": ")[1] if ": " in e else "" for e in errors]
check("touching boundary passes ordering but fails embargo",
      "embargo" in rules and "ordering" not in rules, str(errors))

tmp = tempfile.mkdtemp()
reversed_win = BASE_FIELDS.replace("dev_start: 2024-01-01", "dev_start: 2024-03-01")
variant(tmp, "HYD-0011-rev.md", reversed_win)
entries, errors = hr.load_and_validate(tmp)
rules = [e.split(": ")[1] if ": " in e else "" for e in errors]
check("reversed window reports within-window among errors",
      "within-window" in rules, str(errors))

tmp = tempfile.mkdtemp()
no_denom = "\n".join(l for l in BASE_FIELDS.splitlines()
                      if not l.startswith("denominator:")) + "\n"
variant(tmp, "HYD-0012-noden.md", no_denom)
entries, errors = hr.load_and_validate(tmp)
check("missing denominator fails",
      any("required-field" in e and "denominator" in e for e in errors), str(errors))

tmp = tempfile.mkdtemp()
bad_status = BASE_FIELDS.replace("status: testing", "status: bogus")
variant(tmp, "HYD-0013-badstat.md", bad_status)
entries, errors = hr.load_and_validate(tmp)
check("bad status reports unknown-status",
      any("unknown-status" in e for e in errors), str(errors))

tmp = tempfile.mkdtemp()
variant(tmp, "HYD-0020-a.md", BASE_FIELDS)
variant(tmp, "HYD-0020-b.md", BASE_FIELDS)
entries, errors = hr.load_and_validate(tmp)
check("duplicate id names both files",
      any("duplicate-id" in e and "HYD-0020-a.md" in e and "HYD-0020-b.md" in e
          for e in errors), str(errors))

tmp = tempfile.mkdtemp()
write(tmp, "HYD-0030-crlf.md", b"---\r\nid: HYD-0030\r\n---\r\nbody\r\n", mode="b")
variant(tmp, "HYD-0031-sib.md", BASE_FIELDS.replace("id: HYD-0001", "id: HYD-0031"))
entries, errors = hr.load_and_validate(tmp)
check("CRLF file yields parse error",
      any(e.startswith("parse: HYD-0030-crlf.md:") for e in errors), str(errors))
check("sibling still validates",
      any(e.get("id") == "HYD-0031" for e in entries)
      and not any("HYD-0031-sib.md" in e for e in errors), str((entries, errors)))

tmp = tempfile.mkdtemp()
write(tmp, "HYD-0032-bin.md",
      b"---\nid: HYD-0032\ntitle: caf\xc3\xa9 \xff broken\n---\n", mode="b")
entries, errors = hr.load_and_validate(tmp)
check("non-UTF8 file yields parse error",
      any(e.startswith("parse: HYD-0032-bin.md:") for e in errors), str(errors))

tmp = tempfile.mkdtemp()
write(tmp, "HYD-0040-nofm.md", "id: HYD-0040\ntitle: no block\n")
variant(tmp, "HYD-0041-sib.md", BASE_FIELDS.replace("id: HYD-0001", "id: HYD-0041"))
entries, errors = hr.load_and_validate(tmp)
check("missing front-matter is a parse error",
      any(e.startswith("parse: HYD-0040-nofm.md:") for e in errors), str(errors))
check("sibling of missing-block file still validates",
      any(e.get("id") == "HYD-0041" for e in entries), str((entries, errors)))

tmp = tempfile.mkdtemp()
write(tmp, "HYD-0050-unclosed.md", front(BASE_FIELDS).replace("---\n\nBody", "\nBody"))
variant(tmp, "HYD-0051-sib.md", BASE_FIELDS.replace("id: HYD-0001", "id: HYD-0051"))
entries, errors = hr.load_and_validate(tmp)
check("unclosed --- block is a parse error",
      any(e.startswith("parse: HYD-0050-unclosed.md:") for e in errors), str(errors))
check("sibling of unclosed file still validates",
      any(e.get("id") == "HYD-0051" for e in entries), str((entries, errors)))

tmp = tempfile.mkdtemp()
entries, errors = hr.load_and_validate(tmp)
check("empty dir -> ([], [])", entries == [] and errors == [], str((entries, errors)))

print("\n=== shipped example + cross-module wiring ===")
HERE = os.path.dirname(os.path.abspath(__file__))
SHIP_DIR = os.path.join(os.path.dirname(HERE), "docs", "hypotheses")
entries, errors = hr.load_and_validate(SHIP_DIR)
check("shipped registry validates clean", errors == [], str(errors))
check("example HYD-0001 present with status testing",
      any(e.get("id") == "HYD-0001" and e.get("status") == "testing"
          for e in entries),
      str([(e.get("id"), e.get("status")) for e in entries]))

known_ids = [e["id"] for e in hr.load_registry(SHIP_DIR)]


def _wiring_decision(hyp_id):
    return {"id": "DEC-0001", "ts": 1700000000, "hypothesis_id": hyp_id,
            "decision": "hold", "reasons": ["registry wiring check"],
            "checks": {}, "decided_by": "registry-wiring-check"}


def _wiring_log(obj):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(obj) + "\n")
    return path


ok_path = _wiring_log(_wiring_decision("HYD-0001"))
try:
    _, ok_errs = dec.validate_log(ok_path, known_ids)
    check("decision citing HYD-0001 accepted by validate_log",
          ok_errs == [], str(ok_errs))
finally:
    os.unlink(ok_path)

bad_path = _wiring_log(_wiring_decision("HYD-9999"))
try:
    _, bad_errs = dec.validate_log(bad_path, known_ids)
    check("decision citing HYD-9999 rejected with unknown-hypothesis",
          any("unknown-hypothesis" in e for e in bad_errs), str(bad_errs))
finally:
    os.unlink(bad_path)

print("\n=== numeric robustness ===")


def _registered_base(**over):
    entry = {"id": "HYD-0001", "title": "t", "status": "registered",
             "prediction": "p", "universe": "u", "horizon": "24h",
             "dev_start": "2024-01-01", "dev_end": "2024-02-01",
             "val_start": "2024-02-05", "val_end": "2024-03-01",
             "holdout_start": "2024-03-10", "metric": "m",
             "threshold": 0.52, "denominator": "d", "costs": "c",
             "comparisons": 3}
    entry.update(over)
    return entry


check("registered base fixture is clean",
      hr.validate(_registered_base()) == [],
      str(hr.validate(_registered_base())))

nan_errs = hr.validate(_registered_base(threshold=float("nan")))
check("NaN threshold rejected as non-numeric",
      any("threshold" in e for e in nan_errs), str(nan_errs))

inf_errs = hr.validate(_registered_base(threshold=float("inf")))
check("inf threshold rejected as non-numeric",
      any("threshold" in e for e in inf_errs), str(inf_errs))

inf_str_errs = hr.validate(_registered_base(threshold="1e999"))
check("overflowing numeric string threshold rejected",
      any("threshold" in e for e in inf_str_errs), str(inf_str_errs))

BIG = "9" * 5000
check("oversized integer string coerces without raising",
      hr._coerce_scalar(BIG) == BIG,
      str(hr._coerce_scalar(BIG))[:40])
check("oversized integer string is not an int",
      hr._as_int(BIG) is None, str(hr._as_int(BIG)))
check("oversized integer string is not a number",
      hr._as_number(BIG) is None, str(hr._as_number(BIG)))
big_errs = hr.validate(_registered_base(comparisons=BIG))
check("oversized comparisons value fails validation without raising",
      any("comparisons" in e for e in big_errs), str(big_errs))

no_horizon = {"id": "HYD-0001", "title": "t", "status": "proposed",
              "prediction": "p", "dev_start": "2024-01-01",
              "dev_end": "2024-01-01", "val_start": "2024-01-01",
              "val_end": "2024-02-01", "holdout_start": "2024-02-02"}
no_horizon_errs = hr.validate(no_horizon)
check("missing horizon skips embargo (no silent default)",
      not any(e.startswith("embargo:") for e in no_horizon_errs),
      str(no_horizon_errs))
check("missing horizon keeps within-window teeth",
      any(e.startswith("within-window:") for e in no_horizon_errs),
      str(no_horizon_errs))

tmp2 = tempfile.mkdtemp()
variant(tmp2, "HYD-0001-a.md", BASE_FIELDS)
variant(tmp2, "HYD-0001-b.md",
        BASE_FIELDS.replace("id: HYD-0001", 'id: " HYD-0001 "'))
_, dup_norm_errs = hr.load_and_validate(tmp2)
check("whitespace-padded quoted id still groups duplicates",
      any("duplicate-id" in e and "HYD-0001-a.md" in e
          and "HYD-0001-b.md" in e for e in dup_norm_errs),
      str(dup_norm_errs))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
