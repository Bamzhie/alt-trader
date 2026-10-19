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
import functools
import importlib
import os
import sqlite3
import subprocess
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

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


@functools.lru_cache(maxsize=1)
def code_rev():
    """The short git revision of the running tree, or None.

    Best effort and read-only: the environment variable wins, then git.  Any
    failure is an explicit None (never a guess).  Cached - it is provenance,
    not a per-request fact.
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
    days = 0
    coins = 0
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

    `days` and `coins` count the RESOLVED rows - they are the denominators
    the day-cluster interval is computed on, so the two always agree.
    """
    events = conn.execute(
        "SELECT COUNT(*) FROM quant_event WHERE mode='SHADOW'").fetchone()[0]
    opened = conn.execute(
        "SELECT COUNT(*) FROM quant_event "
        "WHERE mode='SHADOW' AND state='EMITTED'").fetchone()[0]
    row = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT e.bar_ts / 86400), "
        "COUNT(DISTINCT e.coin) FROM quant_event e "
        "JOIN quant_outcome o ON o.event_id = e.id "
        "WHERE e.mode='SHADOW'").fetchone()
    resolved = row[0] or 0
    days = row[1] or 0
    coins = row[2] or 0
    return {"events": events, "resolved": resolved, "open": opened,
            "days": days, "coins": coins, "rules": n_rules}


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


def build(conn, cfg=DEFAULT_CONFIG, promo_dir=".", now=None):
    """Assemble the research surface from an OPEN quant store connection.

    `conn` must be a read-only connection to the quant store.  `promo_dir` is
    the directory holding `promotions.json` (the gate records).  Nothing is
    written, the holdout is never evaluated, and no promotion decision is
    changed.
    """
    conn.row_factory = sqlite3.Row
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    store = ReadOnlyStore(conn, has_suppressed="quant_suppressed" in tables)
    config_hash = cfg.config_hash()
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
        "calibration": {"available": False, "buckets": [],
                        "reason": CALIBRATION_REASON},
        "coverage": _coverage(conn, sample["events"],
                              has_suppressed="quant_suppressed" in tables),
    }
