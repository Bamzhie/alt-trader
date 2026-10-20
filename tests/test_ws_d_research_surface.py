"""Workstream D — the read-only research surface (`GET /quant-research`).

Self-checking script (stdlib only).  Talks to 127.0.0.1 ONLY: an egress
tripwire wraps socket connects and fails any connect outside loopback.

What is pinned here:
  * the payload shape of `research-surface-v1` (the exact keys);
  * explicit denominators: counts, days, coins, and `null` + a reason for
    everything that cannot be computed;
  * uncertainty as INTERVAL ENDPOINTS ONLY - the mean net R is not in the
    payload, and the endpoints are the ones `report.rule_report` computes;
  * validation is `validated` ONLY for a passed gate record;
  * the route is read-only and auth-gated (no token -> 401, bad token -> 401,
    good token -> 200), never spends the holdout and never writes;
  * an empty store answers `insufficient` with a reason;
  * the payload never carries the banned research vocabulary.

Run:  python3 tests/test_ws_d_research_surface.py   -> ALL PASS, exit 0
"""

import hashlib
import http.client
import json
import logging
import os
import socket
import sqlite3
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# ---- egress tripwire: loopback-only (allows 127.0.0.1 / localhost / ::1) --
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class NetworkTripwire(AssertionError):
    """A test tried to connect outside loopback — instant failure."""


_RealSocket = socket.socket
_RealCreateConnection = socket.create_connection


class _GuardedSocket(_RealSocket):
    def connect(self, address, *args, **kw):
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOOPBACK_HOSTS:
            raise NetworkTripwire(
                f"NETWORK TRIPWIRE: connect to non-loopback {host!r} blocked")
        return super().connect(address, *args, **kw)


def _guarded_create_connection(address, *args, **kw):
    host = address[0] if isinstance(address, tuple) else address
    if host not in LOOPBACK_HOSTS:
        raise NetworkTripwire(
            f"NETWORK TRIPWIRE: create_connection to non-loopback {host!r}")
    return _RealCreateConnection(address, *args, **kw)


socket.socket = _GuardedSocket
socket.create_connection = _guarded_create_connection

FAILURES = []
CHECKS = 0


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    cond = bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)
    return cond


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def req(port, path, token=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

from proto.quant import gates as gatesmod            # noqa: E402
from proto.quant import qstore                        # noqa: E402
from proto.quant import report as reportmod           # noqa: E402
from proto.quant import research_surface as surface   # noqa: E402
from proto.quant.config import DEFAULT as QCFG        # noqa: E402
from proto.quant.registry import REGISTERED           # noqa: E402

CANDIDATE = REGISTERED[0].rule_id      # a registered candidate rule
CONTROL = REGISTERED[1].rule_id        # its control twin
BANNED = ("confidence", "probability", "tier", "leverage", "liquidation",
          "scanned", "actionable")


def event(rule_id, coin, bar_ts, role, mode="SHADOW", config_hash=None):
    return {
        "rule_id": rule_id, "config_hash": config_hash or QCFG.config_hash(),
        "coin": coin, "side": 1, "bar_ts": bar_ts, "mode": mode,
        "role": role, "ref_price": 100.0, "stop": 98.0, "tp": 104.0,
        "stop_frac": 0.02, "horizon": 288, "r_tp": 2.0, "cost_frac": 0.001,
        "sigma": 0.01, "qv24": 5e6, "features": {"x": 1},
    }


def populate(store):
    """Two resolved events on two days + one open event + one legacy
    ACTIONABLE row (which must never be counted) + a suppression row."""
    d0 = 1_700_000_000 // 86400 * 86400
    ids = []
    for i, (coin, day, net_r, kind) in enumerate((
            ("AAA", d0, 0.02, "TP"),
            ("BBB", d0 + 86400, -0.01, "SL"),
            ("CCC", d0 + 2 * 86400, 0.03, "TP"))):
        eid = store.insert_event(event(CANDIDATE, coin, day, "candidate"))
        store.resolve(eid, kind, 100.0, day + 300, 1, 0.01, net_r)
        ids.append(eid)
    open_id = store.insert_event(event(CANDIDATE, "DDD", d0 + 3 * 86400,
                                       "candidate"))
    store.insert_event(event(CONTROL, "EEE", d0, "control"))
    store.insert_event(event(CANDIDATE, "ZZZ", d0, "candidate",
                             mode="ACTIONABLE"))
    store.bump_suppressed("2026-01-01", CANDIDATE, "holdout not evaluated", 4)
    store.commit()
    return ids, open_id


def main():
    d = tempfile.mkdtemp(prefix="ws-d-surface-")
    db_path = os.path.join(d, "quant.db")
    store = qstore.QuantStore(db_path)
    populate(store)

    # ---- the module -------------------------------------------------------
    conn = sqlite_ro(db_path)
    payload = surface.build(conn, cfg=QCFG, promo_dir=d, now=1700000000)
    conn.close()

    check("payload version", payload["version"] == "research-surface-v1",
          payload.get("version"))
    check("payload top-level keys", set(payload) == {
        "version", "status", "reasons", "generated_ts", "provenance",
        "sample", "rules", "calibration", "coverage", "validation"},
        str(sorted(payload)))
    check("status ok with events", payload["status"] == "ok", payload["status"])
    check("no reasons when ok", payload["reasons"] == [], str(payload["reasons"]))
    check("generated_ts is the injected stamp",
          payload["generated_ts"] == 1700000000, str(payload["generated_ts"]))

    prov = payload["provenance"]
    check("provenance keys", set(prov) == {
        "config_hash", "code_rev", "rule_ids", "feature_versions"},
        str(sorted(prov)))
    check("provenance config hash", prov["config_hash"] == QCFG.config_hash())
    check("provenance rule ids are the registered ones",
          prov["rule_ids"] == sorted(r.rule_id for r in REGISTERED),
          str(prov["rule_ids"]))
    check("provenance feature versions carry a name and a version",
          all(set(f) == {"name", "version"} for f in prov["feature_versions"]),
          str(prov["feature_versions"]))
    check("code rev is a string or null",
          prov["code_rev"] is None or isinstance(prov["code_rev"], str))

    sample = payload["sample"]
    check("sample keys", set(sample) == {
        "events", "resolved", "open", "days", "coins", "rules"},
        str(sorted(sample)))
    check("sample events count shadow rows only (legacy row excluded)",
          sample["events"] == 5, str(sample))
    check("sample resolved", sample["resolved"] == 3, str(sample))
    check("sample open", sample["open"] == 2, str(sample))
    check("sample days count the distinct days of the whole sample",
          sample["days"] == 4, str(sample))
    check("sample coins count the distinct coins of the whole sample",
          sample["coins"] == 5, str(sample))
    check("sample rules", sample["rules"] == 2, str(sample))

    rules = {r["rule_id"]: r for r in payload["rules"]}
    check("both rules are reported", set(rules) == {CANDIDATE, CONTROL},
          str(sorted(rules)))
    cand = rules[CANDIDATE]
    check("rule keys", set(cand) == {
        "rule_id", "role", "events", "resolved", "open", "days", "coins",
        "interval", "outcome_shares", "cost_note", "suppression",
        "validation"}, str(sorted(cand)))
    check("candidate role", cand["role"] == "candidate", cand["role"])
    check("control role", rules[CONTROL]["role"] == "control",
          rules[CONTROL]["role"])
    check("rule events count shadow rows only", cand["events"] == 4,
          str(cand["events"]))
    check("rule resolved", cand["resolved"] == 3, str(cand["resolved"]))
    check("rule open", cand["open"] == 1, str(cand["open"]))
    check("rule days are the measured range's denominators",
          cand["days"] == 3, str(cand["days"]))
    check("rule coins are the measured range's denominators",
          cand["coins"] == 3, str(cand["coins"]))
    # A rule with no resolved row has no denominators: null, never a zero.
    ctrl = rules[CONTROL]
    check("a rule with no resolved row reports a null interval",
          ctrl["interval"] is None, str(ctrl["interval"]))
    check("a rule with no resolved row reports null days and coins",
          ctrl["days"] is None and ctrl["coins"] is None, str(ctrl))
    check("a rule with no resolved row reports null outcome shares",
          ctrl["outcome_shares"] == {"tp": None, "sl": None, "time": None},
          str(ctrl["outcome_shares"]))

    check("interval carries endpoints only",
          set(cand["interval"]) == {"lo", "hi"}, str(cand["interval"]))
    check("interval endpoints are numbers",
          isinstance(cand["interval"]["lo"], float)
          and isinstance(cand["interval"]["hi"], float),
          str(cand["interval"]))
    check("interval is ordered", cand["interval"]["lo"] <= cand["interval"]["hi"],
          str(cand["interval"]))
    # The endpoints must be the ones the research report computes - the
    # statistics are reused, never re-implemented.
    conn = sqlite_ro(db_path)
    rep = reportmod.rule_report(
        surface.ReadOnlyStore(conn), CANDIDATE, QCFG, d)
    conn.close()
    check("interval equals report.rule_report's day-cluster interval",
          abs(cand["interval"]["lo"] - rep["ci_lo"]) < 1e-12
          and abs(cand["interval"]["hi"] - rep["ci_hi"]) < 1e-12,
          f"{cand['interval']} vs {rep['ci_lo'], rep['ci_hi']}")
    check("no point reading of the result in the payload",
          "net_r" not in json.dumps(payload) and "mean_r" not in
          json.dumps(payload), "mean leaked")

    shares = cand["outcome_shares"]
    check("outcome share keys", set(shares) == {"tp", "sl", "time"},
          str(sorted(shares)))
    check("outcome shares sum to one",
          abs(shares["tp"] + shares["sl"] + shares["time"] - 1.0) < 1e-9,
          str(shares))
    check("outcome shares are shares of the resolved rows",
          abs(shares["tp"] - 2 / 3) < 1e-9, str(shares))
    check("cost note discloses the assumption",
          "assumed" in cand["cost_note"] and "funding is not modeled"
          in cand["cost_note"], cand["cost_note"])

    check("suppression rows carry reason and n",
          cand["suppression"] == [{"reason": "holdout not evaluated", "n": 4}],
          str(cand["suppression"]))

    val = cand["validation"]
    check("validation keys", set(val) == {"status", "reasons"}, str(sorted(val)))
    check("validation is not_validated without a passed record",
          val["status"] == "not_validated", val["status"])
    check("validation names the missing record",
          any("no promotion record" in r for r in val["reasons"]),
          str(val["reasons"]))

    cal = payload["calibration"]
    check("calibration keys", set(cal) == {"available", "buckets", "count",
                                           "reason"}, str(sorted(cal)))
    check("calibration is unavailable with a reason",
          cal["available"] is False and cal["buckets"] == [] and cal["reason"],
          str(cal))
    check("calibration carries the bucket count (the view never counts)",
          cal["count"] == 0, str(cal))

    cov = payload["coverage"]
    check("coverage keys", set(cov) == {"attributed", "eligible", "ratio",
                                        "reason"}, str(sorted(cov)))
    check("coverage denominator counts the recorded suppressions",
          cov["eligible"] == cov["attributed"] + 4, str(cov))
    check("coverage ratio", abs(cov["ratio"] - cov["attributed"] / cov["eligible"])
          < 1e-9, str(cov))
    check("coverage carries a reason", bool(cov["reason"]), str(cov))

    vsum = payload["validation"]
    check("validation summary keys", set(vsum) == {"validated", "rules"},
          str(sorted(vsum)))
    check("validation summary counts the rules with a passed gate record",
          vsum == {"validated": 0, "rules": 2}, str(vsum))

    blob = json.dumps(payload).lower()
    for word in BANNED:
        check(f"payload carries no {word!r}", word not in blob,
              blob[:200])

    # ---- a passed gate record makes the rule validated -------------------
    gatesmod.record(d, CANDIDATE, QCFG.config_hash(), True,
                    ["holdout CI lower bound above zero"],
                    {"n": 250, "days": 30, "coins": 20, "mean_r": 0.15,
                     "ci_lo": 0.12, "ci_hi": 0.18})
    conn = sqlite_ro(db_path)
    payload2 = surface.build(conn, cfg=QCFG, promo_dir=d, now=1700000000)
    conn.close()
    val2 = {r["rule_id"]: r["validation"] for r in payload2["rules"]}
    check("a passed record validates the rule",
          val2[CANDIDATE]["status"] == "validated", str(val2[CANDIDATE]))
    check("the untested rule stays not_validated",
          val2[CONTROL]["status"] == "not_validated", str(val2[CONTROL]))
    check("a validated rule is still research (no action wording)",
          "actionable" not in json.dumps(payload2).lower())
    check("the validation summary counts the passed rule",
          payload2["validation"] == {"validated": 1, "rules": 2},
          str(payload2["validation"]))

    # ---- an empty store ---------------------------------------------------
    d2 = tempfile.mkdtemp(prefix="ws-d-empty-")
    store2 = qstore.QuantStore(os.path.join(d2, "quant.db"))
    store2.close()
    conn = sqlite_ro(os.path.join(d2, "quant.db"))
    empty = surface.build(conn, cfg=QCFG, promo_dir=d2, now=1700000000)
    conn.close()
    check("empty store is insufficient", empty["status"] == "insufficient",
          empty["status"])
    check("empty store carries a reason", bool(empty["reasons"]),
          str(empty["reasons"]))
    check("empty store sample is explicit zeros",
          empty["sample"] == {"events": 0, "resolved": 0, "open": 0,
                              "days": 0, "coins": 0, "rules": 0},
          str(empty["sample"]))
    check("empty store has no rules", empty["rules"] == [])
    check("empty store coverage ratio is null, not zero",
          empty["coverage"]["ratio"] is None, str(empty["coverage"]))
    check("empty store coverage names the reason",
          bool(empty["coverage"]["reason"]), str(empty["coverage"]))
    check("empty store validation summary is explicit zeros",
          empty["validation"] == {"validated": 0, "rules": 0},
          str(empty["validation"]))

    # ---- the short-TTL cache (read-only, in memory) ----------------------
    conn = sqlite_ro(db_path)
    first = surface.build_cached(conn, cfg=QCFG, promo_dir=d, now=1700000000)
    second = surface.build_cached(conn, cfg=QCFG, promo_dir=d, now=1700000000)
    conn.close()
    check("an unchanged store reuses the assembled surface",
          first is second, "the surface was rebuilt")
    # A new event changes the content fingerprint: the cache is invalidated.
    store3 = qstore.QuantStore(db_path)
    store3.insert_event(event(CONTROL, "FFF", 1_700_000_000 + 5 * 86400,
                               "control"))
    store3.commit()
    store3.close()
    conn = sqlite_ro(db_path)
    third = surface.build_cached(conn, cfg=QCFG, promo_dir=d, now=1700000000)
    conn.close()
    check("a changed store invalidates the cache", third is not first,
          "a stale surface was served")
    check("the refreshed surface sees the new event",
          third["sample"]["events"] == 6, str(third["sample"]))

    # ---- the route --------------------------------------------------------
    from proto import service as svc
    token = "ws-d-surface-token"
    server = svc.serve(0, token, quant_db=db_path)
    port = server.server_address[1]
    try:
        st, body = req(port, "/quant-research")
        check("no token -> 401", st == 401, f"got {st}")
        st, body = req(port, "/quant-research", token="wrong")
        check("bad token -> 401", st == 401, f"got {st}")
        st, body = req(port, "/quant-research", token=token)
        check("good token -> 200", st == 200, f"got {st} {body[:200]}")
        env = json.loads(body)
        check("envelope is ok", env.get("ok") is True, str(env)[:200])
        data = env.get("data") or {}
        check("route serves the documented shape",
              data.get("version") == "research-surface-v1"
              and set(data) == {"version", "status", "reasons", "generated_ts",
                                "provenance", "sample", "rules", "calibration",
                                "coverage", "validation"}, str(sorted(data)))
        check("route reports the validation status of the recorded gate",
              {r["rule_id"]: r["validation"]["status"]
               for r in data["rules"]}[CANDIDATE] == "validated",
              str(data.get("rules")))
    finally:
        server.shutdown()
        server.server_close()

    # ---- the route is read-only -------------------------------------------
    before = sha(db_path)
    promo_before = sha(os.path.join(d, "promotions.json"))
    server = svc.serve(0, token, quant_db=db_path)
    port = server.server_address[1]
    try:
        req(port, "/quant-research", token=token)
        req(port, "/quant-research", token=token)
    finally:
        server.shutdown()
        server.server_close()
    check("the quant store is untouched by the route", sha(db_path) == before)
    check("the promotion records are untouched by the route",
          sha(os.path.join(d, "promotions.json")) == promo_before)
    check("the holdout was never evaluated",
          not os.path.exists(os.path.join(d, "holdout_evaluated.json")))

    # ---- an unreadable store ---------------------------------------------
    server = svc.serve(0, token, quant_db=os.path.join(d, "missing.db"))
    port = server.server_address[1]
    try:
        st, body = req(port, "/quant-research", token=token)
        check("an unreadable store -> 503 UNAVAILABLE", st == 503, f"got {st}")
        check("the 503 carries the error envelope",
              json.loads(body).get("error", {}).get("code") == "UNAVAILABLE",
              body[:200])
    finally:
        server.shutdown()
        server.server_close()

    # ---- an internal failure is logged, not a silent 503 ------------------
    d3 = tempfile.mkdtemp(prefix="ws-d-broken-")
    broken = os.path.join(d3, "quant.db")
    conn = sqlite3.connect(broken)
    # A quant_event table the surface cannot read: the route must not
    # answer 503 without saying why in the log.
    conn.execute("CREATE TABLE quant_event (id INTEGER PRIMARY KEY, "
                 "rule_id TEXT)")
    conn.commit()
    conn.close()
    captured = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record)

    log = logging.getLogger("proto.service")
    handler = _Capture()
    log.addHandler(handler)
    try:
        server = svc.serve(0, token, quant_db=broken)
        port = server.server_address[1]
        try:
            st, body = req(port, "/quant-research", token=token)
        finally:
            server.shutdown()
            server.server_close()
    finally:
        log.removeHandler(handler)
    check("an internal failure answers 503", st == 503, f"got {st}")
    check("the internal failure is logged",
          any(r.levelno >= logging.WARNING for r in captured),
          str([r.getMessage() for r in captured]))
    check("the log line names the failure and leaks no token",
          any("research surface unavailable" in r.getMessage() for r in captured)
          and all(token not in r.getMessage() for r in captured),
          str([r.getMessage() for r in captured]))

    store.close()

    print()
    if FAILURES:
        print(f"FAIL  tests/test_ws_d_research_surface.py  "
              f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
        sys.exit(1)
    print(f"ALL PASS  tests/test_ws_d_research_surface.py  ({CHECKS} checks)")


def sqlite_ro(path):
    import sqlite3
    conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True,
                           timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


if __name__ == "__main__":
    main()
