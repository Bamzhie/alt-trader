#!/usr/bin/env python3
"""Tests for the manual-review signal surface (read-only, no execution)."""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from proto.quant import signals as SG
from proto.quant import qstore as QS

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


RULE = "quant-v1:breakout_start:rvol4:fade:T288_ks1.5_R1"
CONTROL = "quant-v1:breakout_start:rvol4:cont:T288_ks1.5_R1"
NOW = 1_700_000_000


def make_store(events):
    path = os.path.join(tempfile.mkdtemp(prefix="sig-"), "quant.db")
    st = QS.QuantStore(path)
    for e in events:
        st.insert_event(e)
    return st


def ev_row(eid, coin, side, ts, rule=RULE, role="candidate"):
    return {
        "id": eid, "rule_id": rule, "config_hash": "cfg1", "coin": coin,
        "side": side, "bar_ts": ts, "mode": "SHADOW", "role": role,
        "state": "EMITTED", "ref_price": 100.0, "stop": 99.0, "tp": 102.0,
        "stop_frac": 0.01, "horizon": 288, "r_tp": 1.5, "cost_frac": 0.0009,
        "sigma": 0.002, "qv24": 5e7, "features": "{}", "created_ts": NOW,
    }


print("=== a candidate carries everything a reviewer needs ===")
store = make_store([ev_row(1, "AAA", 1, NOW - 600),
                    ev_row(2, "BBB", -1, NOW - 900, rule=CONTROL, role="control")])
rep = SG.signals_report(store, now=NOW, evidence=None)
check("the report counts the candidates", rep["count"] == 2, str(rep["count"]))
check("the report counts the directions",
      rep["counts"] == {"long": 1, "short": 1}, str(rep["counts"]))
s = rep["signals"][0]
for field in ("coin", "direction", "rule_id", "rule_label", "signal_ts",
              "signal_ts_utc", "data_freshness", "entry", "risk",
              "outcome", "costs", "evidence", "execution"):
    check(f"the candidate has {field}", field in s and s[field] is not None,
          str(s.get(field)))
check("the direction is long/short, not a side number",
      s["direction"] in ("long", "short"), str(s["direction"]))
check("the freshness is stated", s["data_freshness"]["signal_age_s"] == 600,
      str(s["data_freshness"]))
check("the stop, target and horizon are stated",
      s["risk"]["stop"] == 99.0 and s["risk"]["target"] == 102.0
      and s["risk"]["horizon_bars"] == 288, str(s["risk"]))
check("the assumptions behind the levels are stated",
      "stop" in s["risk"]["assumptions"]
      and "tie_break" in s["risk"]["assumptions"]
      and "leverage" in s["risk"]["assumptions"],
      str(s["risk"]["assumptions"].keys()))
check("the entry basis is explicit", "open of the bar after the signal"
      in s["entry"]["basis"], str(s["entry"]))
check("the cost basis is labelled as an assumption",
      "assumption" in s["costs"]["basis"], str(s["costs"]))

print("\n=== it is never actionable, and says so ===")
check("no candidate is actionable",
      all(x["execution"]["actionable"] is False for x in rep["signals"]))
check("the mode reported is the store's own SHADOW mode",
      all(x["execution"]["mode"] == "SHADOW" for x in rep["signals"]))
check("the execution note says the app will not execute it",
      "does not execute" in rep["signals"][0]["execution"]["note"],
      rep["signals"][0]["execution"]["note"])
check("the report carries the manual-review note",
      "does not execute" in rep["note"], rep["note"])

print("\n=== the evidence status is honest ===")
check("without an analysis artifact the evidence is 'insufficient'",
      rep["signals"][0]["evidence"]["status"] == "insufficient",
      str(rep["signals"][0]["evidence"]))
check("the insufficiency carries a reason",
      "reason" in rep["signals"][0]["evidence"]
      and rep["signals"][0]["evidence"]["reason"], str(rep["signals"][0]["evidence"]))
evidence = {
    "preregistration_id": "HYD-0003",
    "config_hash": "cfgA",
    "expected_config_hash": "cfgA",
    "members": {
        RULE: {"status": "exploratory", "reason": "previously examined",
               "n_events": 300, "n_coins": 20, "n_days": 30,
               "ci_lo_day": -0.01, "ci_hi_day": 0.02, "family_p": 0.4,
               "funding_coverage": 0.99},
    },
}
rep2 = SG.signals_report(store, now=NOW, evidence=evidence)
ev0 = [x for x in rep2["signals"] if x["rule_id"] == RULE][0]["evidence"]
ev1 = [x for x in rep2["signals"] if x["rule_id"] == CONTROL][0]["evidence"]
check("a rule in the analysis keeps its preregistered status",
      ev0["status"] == "exploratory", str(ev0))
check("the evidence carries the sample size and the interval",
      ev0["n_events"] == 300 and ev0["n_coins"] == 20 and ev0["n_days"] == 30
      and ev0["ci_lo_day"] == -0.01, str(ev0))
check("a rule OUTSIDE the analysis is 'insufficient' with a reason",
      ev1["status"] == "insufficient" and ev1["reason"], str(ev1))
check("no candidate is labelled validated unless the analysis says so",
      all(x["evidence"]["status"] != "validated" for x in rep2["signals"]))

print("\n=== stale candidates are not offered for review ===")
old = make_store([ev_row(3, "CCC", 1, NOW - 10 * 3600)])
rep3 = SG.signals_report(old, now=NOW, evidence=None)
check("a signal older than the review window is not a candidate",
      rep3["count"] == 0, str(rep3["count"]))

print("\n=== the evidence file is optional and read-only ===")
with tempfile.TemporaryDirectory() as d:
    os.makedirs(os.path.join(d, os.path.dirname(SG.EVIDENCE_RELPATH)))
    p = os.path.join(d, SG.EVIDENCE_RELPATH)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write('{"preregistration_id": "HYD-0003", "rules": {}}')
    loaded = SG.load_evidence(root=d)
    check("the evidence file loads", loaded and loaded["preregistration_id"]
          == "HYD-0003", str(loaded))
    before = open(p, "rb").read()
    SG.load_evidence(root=d)
    check("loading the evidence file does not modify it",
          open(p, "rb").read() == before)
check("a missing evidence file yields None, not an exception",
      SG.load_evidence(root=tempfile.mkdtemp()) is None)

print("\n=== the route is read-only and auth-gated ===")
import http.client
import json as _json

from proto import service as svc

TOKEN = "test-token"
tmp = tempfile.mkdtemp(prefix="sig-route-")
qdb = os.path.join(tmp, "quant.db")
st = QS.QuantStore(qdb)
st.insert_event(ev_row(11, "ZZZ", 1, int(time.time()) - 300))
st.close()
server = svc.serve(0, TOKEN, lambda: {}, store_path=os.path.join(tmp, "s.db"),
                    data_dir=tmp, quant_db=qdb)
port = server.server_address[1]
time.sleep(0.2)


def get(path, token=TOKEN, on_port=None):
    conn = http.client.HTTPConnection("127.0.0.1", on_port or port, timeout=10)
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    try:
        return resp.status, _json.loads(body)
    except ValueError:
        return resp.status, None


try:
    st_code, body = get("/quant-signals")
    check("the route answers 200", st_code == 200, f"got {st_code}")
    data = (body or {}).get("data") or {}
    check("the payload is the signals-v1 object",
          data.get("version") == SG.SIGNALS_VERSION, str(data.get("version")))
    check("the candidate is present", data.get("count", 0) >= 1,
          str(data.get("count")))
    sig = (data.get("signals") or [{}])[0]
    check("the route's candidate carries the manual-review note",
          "does not execute" in (sig.get("execution") or {}).get("note", ""),
          str(sig.get("execution")))
    check("the route's candidate is not actionable",
          (sig.get("execution") or {}).get("actionable") is False)
    check("the route's candidate keeps the SHADOW mode",
          (sig.get("execution") or {}).get("mode") == "SHADOW",
          str((sig.get("execution") or {}).get("mode")))
    st_code2, _ = get("/quant-signals", token=None)
    check("the route without a token -> 401", st_code2 == 401, f"got {st_code2}")
    st_code3, _ = get("/quant-signals", token="wrong")
    check("the route with a bad token -> 401", st_code3 == 401, f"got {st_code3}")
finally:
    server.shutdown()
    server.server_close()

print("\n=== the route never writes to the store ===")
import hashlib

qdb2 = os.path.join(tempfile.mkdtemp(prefix="sig-ro-"), "quant.db")
st2 = QS.QuantStore(qdb2)
st2.insert_event(ev_row(21, "RO", 1, int(time.time()) - 300))
st2.close()
before_hash = hashlib.sha256(open(qdb2, "rb").read()).hexdigest()
before_mtime = os.stat(qdb2).st_mtime
server2 = svc.serve(0, TOKEN, lambda: {}, store_path=os.path.join(tmp, "s2.db"),
                     data_dir=tmp, quant_db=qdb2)
port2 = server2.server_address[1]
time.sleep(0.2)
try:
    st_code4, body4 = get("/quant-signals", on_port=port2)
    check("the read-only route answers 200", st_code4 == 200, f"got {st_code4}")
    after_hash = hashlib.sha256(open(qdb2, "rb").read()).hexdigest()
    check("the route did not modify the quant store", after_hash == before_hash)
    check("the route did not touch the store's mtime",
          os.stat(qdb2).st_mtime == before_mtime)
finally:
    server2.shutdown()
    server2.server_close()

missing_db = os.path.join(tempfile.mkdtemp(prefix="sig-missing-"), "quant.db")
server3 = svc.serve(0, TOKEN, lambda: {}, store_path=os.path.join(tmp, "s3.db"),
                     data_dir=tmp, quant_db=missing_db)
port3 = server3.server_address[1]
time.sleep(0.2)
try:
    st_code5, _ = get("/quant-signals", on_port=port3)
    check("a missing quant store -> 503 (it is not created)", st_code5 == 503,
          f"got {st_code5}")
    check("the missing quant store was NOT created", not os.path.exists(missing_db))
finally:
    server3.shutdown()
    server3.server_close()

print("\n=== only SHADOW rows are surfaced ===")
st3 = QS.QuantStore(os.path.join(tempfile.mkdtemp(prefix="sig-mode-"), "q.db"))
row = ev_row(31, "MD", 1, int(time.time()) - 200)
st3.insert_event(row)
st3.insert_event({**ev_row(32, "AC", 1, int(time.time()) - 200), "mode": "ACTIONABLE"})
st3.close()
rows = SG.build_signals([dict(row)], {}, now=int(time.time()), evidence=None)
check("an ACTIONABLE row is not surfaced as a manual-review candidate",
      all(r["coin"] != "AC" for r in rows), str([r["coin"] for r in rows]))
check("the surfaced candidate keeps the SHADOW mode",
      rows and rows[0]["execution"]["mode"] == "SHADOW",
      str(rows[0]["execution"] if rows else None))

print("\n=== the evidence is keyed by member, not by rule alone ===")
ev_by_member = {
    "preregistration_id": "HYD-0003",
    "config_hash": "cfgA",
    "expected_config_hash": "cfgA",
    "members": {
        f"{RULE}|pivot_low": {
            "status": "exploratory", "reason": "the filtered member",
            "n_events": 42, "n_coins": 7, "n_days": 9,
            "ci_lo_day": -0.02, "ci_hi_day": 0.03, "family_p": 0.3,
        },
    },
}
r_with = SG.build_signals([{**dict(ev_row(41, "MB", 1, int(time.time()) - 100)),
                            "feature_id": "pivot_low"}],
                          {}, now=int(time.time()), evidence=ev_by_member)
check("the member's own verdict is used",
      r_with and r_with[0]["evidence"]["status"] == "exploratory"
      and r_with[0]["evidence"]["n_events"] == 42,
      str(r_with[0]["evidence"] if r_with else None))
r_without = SG.build_signals([{**dict(ev_row(42, "MB", 1, int(time.time()) - 100)),
                               "feature_id": "other"}],
                             {}, now=int(time.time()), evidence=ev_by_member)
check("another member of the same rule does NOT inherit the verdict",
      r_without and r_without[0]["evidence"]["status"] == "insufficient",
      str(r_without[0]["evidence"] if r_without else None))
ev_wrong = dict(ev_by_member, config_hash="cfgB")
r_wrong = SG.build_signals([{**dict(ev_row(43, "MB", 1, int(time.time()) - 100)),
                              "feature_id": "pivot_low"}],
                            {}, now=int(time.time()), evidence=ev_wrong)
check("an artifact produced under another config is refused",
      r_wrong and r_wrong[0]["evidence"]["status"] == "insufficient"
      and "config" in (r_wrong[0]["evidence"]["reason"] or ""),
      str(r_wrong[0]["evidence"] if r_wrong else None))

print()
if FAILURES:
    print(f"FAILURES: {len(FAILURES)}")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
print("ALL PASS (manual-review signals)")
