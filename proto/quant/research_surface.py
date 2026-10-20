"""The READ-ONLY research surface behind `GET /quant-research`.

This module assembles the honest, denominator-first view of the shadow
research log that the Research view renders: sample counts with their
denominators, uncertainty as INTERVAL ENDPOINTS ONLY, calibration /
coverage / validation status, and feature provenance.

Hard rules this module obeys (they are the app's, not just the route's):

* READ-ONLY.  It opens the quant store read-only, never writes to it, never
  writes a promotion record and never changes a promotion decision.  The only
  promotion artefact it reads is `gates.promotion_for`, and it reports that
  as a *validation status*, never as an action.
* NO HOLDOUT SPEND.  The holdout is never evaluated here.  A rule that was
  promoted long ago keeps its recorded gate outcome; this route only reports
  it.
* UNCERTAINTY IS INTERVAL ENDPOINTS ONLY.  The mean net R computed by
  `report.rule_report` is deliberately NOT part of the payload: a point
  reading of the result is never published.
* EXPLICIT DENOMINATORS.  A value that cannot be computed is `null` with a
  reason next to it, never a zero and never a guess.
* SHADOW ONLY.  The Research view is the shadow research log.  Legacy
  `ACTIONABLE` rows are excluded from every count, share and interval here
  (the shadow-only contract), so the sample, the denominators and the
  interval always describe the same rows.
* Standard library only.  No pandas / numpy / scipy.

The statistics are NOT re-implemented here: the day-cluster bootstrap
interval comes from `report.rule_report` (the same function the CLI report
uses), so the Research view and the research report can never disagree.
"""
import hashlib
import importlib
import json
import os
import sqlite3
import subprocess
import threading
import time

from . import gates as gatesmod
from .config import DEFAULT as DEFAULT_CONFIG
from .registry import by_id
from .report import rule_report

#: The version tag of the payload shape. Additive changes bump it.
SURFACE_VERSION = "research-surface-v1"

#: Shown next to every rule's figures. Costs are ASSUMED (they are not a
#: measurement), and funding is not modelled - so the note discloses the
#: assumption instead of hiding it.
COST_NOTE = ("costs are assumed (fee + slippage); funding is not modeled; "
             "no fill model beyond next bar open")

#: Why the calibration table cannot be built from the shadow store.
CALIBRATION_REASON = ("the shadow store records outcomes, not predicted "
                      "outcomes - calibration is not computed")

#: What the coverage denominator is made of.
COVERAGE_REASON = ("attributed events over recorded opportunities (emitted + "
                   "recorded suppressions); opportunities the watcher never "
                   "recorded are not in the denominator")

#: The reason an empty store carries.
EMPTY_REASON = "no shadow events recorded yet"

#: How long the assembled surface may be reused. The store's content changes
#: only when new bars arrive, and the day-cluster bootstrap is the expensive
#: part; a short TTL keeps the per-frame read amplification down. The cache
#: key is the store's content fingerprint, so a change invalidates it at
#: once — the TTL is the upper bound on staleness, not a way to hide it.
CACHE_TTL_S = 15.0

#: How long the code revision may be reused before it is looked up again.
CODE_REV_TTL_S = 300.0

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The cached code revision: (value, looked_up_at).
_CODE_REV = None
_CODE_REV_LOCK = threading.Lock()

# The cached surface: (key, built_at, payload).
_CACHE = None
_CACHE_LOCK = threading.Lock()


class _EmptyResult:
    """An empty query result (a store without the suppression table)."""

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _Conn:
    """The quant connection, tolerant of a store without quant_suppressed.

    `/quant-status` treats a missing suppression table as "no suppressions";
    the research surface answers the same way instead of failing the whole
    route over a table it can live without.
    """

    def __init__(self, conn, has_suppressed):
        self._conn = conn
        self._has_suppressed = has_suppressed

    def execute(self, sql, args=()):
        if not self._has_suppressed and "quant_suppressed" in sql:
            return _EmptyResult()
        return self._conn.execute(sql, args)


class ReadOnlyStore:
    """The quant store as the research surface sees it: read-only, SHADOW-only.

    `report.rule_report` wants a store-like object; this one never writes and
    never hands back a legacy `ACTIONABLE` row, so the counts, the shares and
    the interval all describe exactly the same shadow rows.
    """

    def __init__(self, conn, has_suppressed=True):
        self.conn = _Conn(conn, has_suppressed) if not has_suppressed else conn

    def all_events(self, rule_id=None):
        q = "SELECT * FROM quant_event WHERE mode='SHADOW'"
        a = []
        if rule_id:
            q += " AND rule_id=?"
            a.append(rule_id)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY bar_ts", a)]

    def resolved_rows(self, rule_id=None):
        q = ("SELECT e.*, o.kind, o.net_r, o.exit_offset, o.gross_log "
             "FROM quant_event e JOIN quant_outcome o ON o.event_id=e.id "
             "WHERE e.mode='SHADOW'")
        a = []
        if rule_id:
            q += " AND e.rule_id=?"
            a.append(rule_id)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY e.bar_ts", a)]


def _code_rev_uncached():
    """The short git revision of the running tree, or None.

    Best effort and read-only: the environment variable wins, then git.  Any
    failure is an explicit None (never a guess).
    """
    env = (os.environ.get("ALT_RADAR_CODE_REV") or "").strip()
    if env:
        return env
    try:
        out = subprocess.run(
            ["git", "-C", _REPO_ROOT, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def code_rev():
    """The code revision, cached for a short TTL.

    It is provenance, not a per-request fact: the shell-out to git happens
    at most once per TTL, never once per request.  Still read-only.
    """
    global _CODE_REV
    now = time.time()
    with _CODE_REV_LOCK:
        if _CODE_REV is not None and (now - _CODE_REV[1]) < CODE_REV_TTL_S:
            return _CODE_REV[0]
    value = _code_rev_uncached()
    with _CODE_REV_LOCK:
        _CODE_REV = (value, time.time())
    return value


def feature_versions():
    """The measure/feature versions this tree ships.

    Best effort: a module that is not in the tree is skipped, never
    imported blindly.  The list is provenance - it says which estimator
    versions produced the numbers behind the surface.
    """
    out = []
    try:
        estimators = importlib.import_module(".estimators", __package__)
    except Exception:            # pragma: no cover - module not in the tree
        estimators = None
    if estimators is not None:
        for const in sorted(n for n in dir(estimators)
                            if n.endswith("_VERSION")):
            version = getattr(estimators, const)
            if isinstance(version, str):
                out.append({"name": const[: -len("_VERSION")].lower(),
                            "version": version})
    for module_name, const, label in (
            ("feature_store", "FEATURE_STORE_VERSION", "feature_store"),
            ("structure", "PIVOT_VERSION", "pivots")):
        try:
            module = importlib.import_module("." + module_name, __package__)
        except Exception:        # pragma: no cover - module not in the tree
            continue
        version = getattr(module, const, None)
        if isinstance(version, str):
            out.append({"name": label, "version": version})
    return out


def _validation(rule_id, config_hash, records):
    """The validation status of one rule.

    `validated` ONLY when a PASSED promotion record exists for this exact
    rule id + config hash.  Otherwise the status is `not_validated` and the
    gate's own reasons say why (a missing record is its own reason: absent
    evidence fails, it is never read as a pass).
    """
    rec = records.get(f"{rule_id}|{config_hash}")
    if rec and rec.get("passed"):
        return {"status": "validated", "reasons": list(rec.get("reasons") or [])}
    if rec:
        reasons = list(rec.get("reasons") or [])
    else:
        reasons = ["no promotion record for this rule id and config hash"]
    return {"status": "not_validated", "reasons": reasons}


def _rule_payload(rep, config_hash, records):
    """One rule's row of the surface, with explicit denominators."""
    resolved = rep["resolved"]
    interval = None
    shares = {"tp": None, "sl": None, "time": None}
    # The days and coins are the measured range's denominators (the rows it
    # was computed on).  A rule with no resolved row has no denominators:
    # they are null, never a zero standing in for "not measured".
    days = None
    coins = None
    if resolved:
        # Interval endpoints only - the mean net R stays out of the payload.
        interval = {"lo": rep["ci_lo"], "hi": rep["ci_hi"]}
        shares = {"tp": rep["tp"], "sl": rep["sl"], "time": rep["time"]}
        days = rep.get("days", 0)
        coins = rep.get("coins", 0)
    suppression = [{"reason": reason, "n": n} for reason, n in
                    sorted(rep.get("suppressed", {}).items(),
                           key=lambda kv: (-kv[1], kv[0]))]
    return {
        "rule_id": rep["rule_id"],
        "role": rep["role"],
        "events": rep["events"],
        "resolved": resolved,
        "open": rep["open"],
        "days": days,
        "coins": coins,
        "interval": interval,
        "outcome_shares": shares,
        "cost_note": COST_NOTE,
        "suppression": suppression,
        "validation": _validation(rep["rule_id"], config_hash, records),
    }


def _sample(conn, n_rules):
    """Sample counts with their denominators.

    `days` and `coins` are the distinct days and coins of the WHOLE sample
    (every shadow event).  A rule's own days and coins are the measured
    range's denominators - the two scopes are different on purpose, and the
    view says which is which.
    """
    row = conn.execute(
        "SELECT COUNT(*), "
        "COALESCE(SUM(CASE WHEN state='EMITTED' THEN 1 ELSE 0 END), 0), "
        "COALESCE(SUM(CASE WHEN state='RESOLVED' THEN 1 ELSE 0 END), 0), "
        "COUNT(DISTINCT bar_ts / 86400), COUNT(DISTINCT coin) "
        "FROM quant_event WHERE mode='SHADOW'").fetchone()
    return {"events": row[0], "resolved": row[2], "open": row[1],
            "days": row[3], "coins": row[4], "rules": n_rules}


def _coverage(conn, events, has_suppressed=True):
    """Coverage: attributed events over the recorded opportunities.

    An opportunity is either an emitted event or a recorded suppression.
    Opportunities the watcher never recorded are not in the denominator, and
    the reason says so.
    """
    suppressed = 0
    if has_suppressed:
        suppressed = conn.execute(
            "SELECT COALESCE(SUM(n), 0) FROM quant_suppressed").fetchone()[0]
    eligible = int(events) + int(suppressed or 0)
    if eligible <= 0:
        return {"attributed": int(events), "eligible": 0, "ratio": None,
                "reason": "no recorded opportunities yet - coverage is not "
                          "computable"}
    return {"attributed": int(events), "eligible": eligible,
            "ratio": int(events) / eligible, "reason": COVERAGE_REASON}


def build(conn, cfg=DEFAULT_CONFIG, promo_dir=".", now=None, records=None):
    """Assemble the research surface from an OPEN quant store connection.

    `conn` must be a read-only connection to the quant store.  `promo_dir` is
    the directory holding `promotions.json` (the gate records).  Nothing is
    written, the holdout is never evaluated, and no promotion decision is
    changed.

    `records` may carry the already-loaded gate records (the cached path
    loads them once and hands them over).
    """
    conn.row_factory = sqlite3.Row
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    store = ReadOnlyStore(conn, has_suppressed="quant_suppressed" in tables)
    config_hash = cfg.config_hash()
    if records is None:
        records = gatesmod.load(promo_dir)
    if not isinstance(records, dict):
        records = {}
    rule_ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT rule_id FROM quant_event "
        "WHERE mode='SHADOW' ORDER BY rule_id")]
    rules = []
    for rule_id in rule_ids:
        rep = rule_report(store, rule_id, cfg, promo_dir)
        rules.append(_rule_payload(rep, config_hash, records))
    sample = _sample(conn, len(rules))
    status = "ok" if sample["events"] > 0 else "insufficient"
    reasons = [] if status == "ok" else [EMPTY_REASON]
    validated = sum(1 for rule in rules
                    if rule["validation"]["status"] == "validated")
    return {
        "version": SURFACE_VERSION,
        "status": status,
        "reasons": reasons,
        "generated_ts": int(time.time()) if now is None else int(now),
        "provenance": {
            "config_hash": config_hash,
            "code_rev": code_rev(),
            "rule_ids": sorted(by_id().keys()),
            "feature_versions": feature_versions(),
        },
        "sample": sample,
        "rules": rules,
        "calibration": {"available": False, "buckets": [], "count": 0,
                        "reason": CALIBRATION_REASON},
        "coverage": _coverage(conn, sample["events"],
                              has_suppressed="quant_suppressed" in tables),
        "validation": {"validated": validated, "rules": len(rules)},
    }


def _content_key(conn, records):
    """A cheap fingerprint of the store and of the gate records.

    Two cheap aggregates over the event/outcome tables plus a digest of the
    promotion records: enough to notice any new event, outcome or gate
    record, and cheap enough to run on every request.
    """
    row = conn.execute(
        "SELECT (SELECT COUNT(*) FROM quant_event), "
        "(SELECT COALESCE(MAX(id), 0) FROM quant_event), "
        "(SELECT COUNT(*) FROM quant_outcome), "
        "(SELECT COALESCE(MAX(resolved_ts), 0) FROM quant_outcome)").fetchone()
    try:
        path = conn.execute("PRAGMA database_list").fetchone()[2] or ""
    except (sqlite3.Error, IndexError, TypeError):
        path = ""
    promo = hashlib.sha256(
        json.dumps(records, sort_keys=True, default=str).encode()).hexdigest()
    return (path, tuple(row), promo)


def build_cached(conn, cfg=DEFAULT_CONFIG, promo_dir=".", now=None):
    """The surface, reused for a short TTL (read-only, in memory).

    The store's content changes only when new bars arrive and the
    day-cluster bootstrap is the expensive part, so the assembled surface is
    reused for `CACHE_TTL_S`.  The cache key is the store's content
    fingerprint: a change invalidates it at once, and the TTL is the upper
    bound on staleness.  Nothing is written and no holdout is touched.
    """
    global _CACHE
    records = gatesmod.load(promo_dir)
    if not isinstance(records, dict):
        records = {}
    key = _content_key(conn, records)
    stamp = time.time()
    with _CACHE_LOCK:
        entry = _CACHE
        if (entry is not None and entry[0] == key
                and (stamp - entry[1]) < CACHE_TTL_S):
            return entry[2]
    payload = build(conn, cfg=cfg, promo_dir=promo_dir, now=now,
                    records=records)
    with _CACHE_LOCK:
        _CACHE = (key, time.time(), payload)
    return payload
