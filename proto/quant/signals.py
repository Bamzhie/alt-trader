"""Rule-generated signal CANDIDATES for manual review (read-only).

THE THREE THINGS THIS MODULE KEEPS SEPARATE:

1. **A rule-generated candidate.**  A rule fired on a closed bar.  It is a
   *candidate* for the owner to consider by hand.
2. **The evidence status of that rule.**  ``exploratory`` (the rule has
   been looked at but was not preregistered, or its result did not pass the
   frozen criteria), ``insufficient`` (not enough evidence to say anything,
   with the reason), or ``validated`` (the rule passed the preregistered
   criteria on dev AND validation).  Only the preregistration artifacts can
   produce ``validated``; nothing here can promote a rule.
3. **Execution.**  Never.  This module reads the shadow store and returns
   candidates; it does not place, route or prepare an order, and the
   ``mode`` it reports is the store's own ``SHADOW`` mode.  A promotion or
   validation result never authorises execution.

Read-only: this module opens the quant store read-only and never writes.
"""
import json
import os
import time

from .config import BAR_S, DEFAULT
from .registry import by_id

SIGNALS_VERSION = "signals-v1"

# Where the evidence summary (produced by the preregistered analysis) is
# looked up, relative to the repository root.  The service runs with the
# repository as its working directory.
EVIDENCE_RELPATH = os.path.join(
    "docs", "implementation-reports", "quant-research-2026-10-20",
    "hyd0003-evidence.json")

# A signal older than this is history, not a candidate for review.
MAX_SIGNAL_AGE_S = 3 * 3600


def _label_for(rule_id):
    spec = by_id().get(rule_id)
    if spec is None:
        return rule_id
    p = spec.params
    arg = (f"rvol>={p.rvol_min:g}" if p.family == "breakout_start"
           else f"h={p.cusum_h:g}")
    g = spec.geometry
    return (f"{p.family} {arg} {p.policy} · {g.key()} · "
            f"{p.family}/{'fade' if p.policy == 'fade' else 'cont'}")


def load_evidence(root=None):
    """The preregistered evidence summary, or ``None`` when it is absent.

    The file is produced by the frozen analysis protocol; it records, per
    rule, whether the rule passed the preregistered criteria and the
    denominators behind that verdict.
    """
    root = root or os.getcwd()
    path = os.path.join(root, EVIDENCE_RELPATH)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _evidence_for(rule_id, evidence, now, feature_id=None):
    """The evidence status of a rule (or of one MEMBER of it), with its
    denominators and its reason.

    The lookup key is the rule id **plus** the feature identity: two
    members of the same rule (for example the same rule with and without a
    point-in-time filter) are different features and must not share a
    verdict.  The artifact must also match the preregistration it claims
    (by id and by the config hash it was run under), or it is refused: a
    stale or hand-edited artifact must never be able to render a rule as
    validated.
    """
    if not evidence:
        return {
            "status": "insufficient",
            "reason": "no analysis artifact: the preregistered analysis "
                      "has not been run (or its result is not available), so "
                      "this rule has no evidence either way",
            "n_events": None, "n_coins": None, "n_days": None,
            "ci_lo_day": None, "ci_hi_day": None, "family_p": None,
            "source": None,
        }
    if evidence.get("config_hash") and evidence.get("config_hash")             != evidence.get("expected_config_hash"):
        return {
            "status": "insufficient",
            "reason": f"the analysis artifact was produced under config "
                      f"{evidence.get('config_hash')}, not the configured "
                      f"{evidence.get('expected_config_hash')}: it does not "
                      f"describe this configuration",
            "n_events": None, "n_coins": None, "n_days": None,
            "ci_lo_day": None, "ci_hi_day": None, "family_p": None,
            "source": evidence.get("preregistration_id"),
        }
    members = evidence.get("members") or {}
    key = f"{rule_id}|{feature_id}" if feature_id else rule_id
    rec = members.get(key) or members.get(rule_id)
    if rec is None:
        return {
            "status": "insufficient",
            "reason": f"this rule/feature is not in the preregistered "
                      f"analysis ({len(members)} members are)",
            "n_events": None, "n_coins": None, "n_days": None,
            "ci_lo_day": None, "ci_hi_day": None, "family_p": None,
            "source": evidence.get("preregistration_id"),
        }
    return {
        "status": rec.get("status", "insufficient"),
        "reason": rec.get("reason"),
        "n_events": rec.get("n_events"),
        "n_coins": rec.get("n_coins"),
        "n_days": rec.get("n_days"),
        "ci_lo_day": rec.get("ci_lo_day"),
        "ci_hi_day": rec.get("ci_hi_day"),
        "family_p": rec.get("family_p"),
        "funding_coverage": rec.get("funding_coverage"),
        "source": evidence.get("preregistration_id"),
    }


def _assumptions(spec, ev):
    g = spec.geometry
    horizon_s = g.horizon_bars * BAR_S
    return {
        "stop": "k_stop * sigma * sqrt(T) bars from the entry",
        "target": "r_tp * the stop distance",
        "time_exit": f"the close of the last bar of the horizon "
                     f"({g.horizon_bars} bars = {horizon_s / 3600:.1f} h)",
        "tie_break": "a bar that touches both the stop and the target "
                     "resolves as the STOP (conservative)",
        "side_rule": "the rule's policy sets the trade side; a fade trade "
                     "is against the break",
        "k_stop": g.k_stop,
        "r_tp": g.r_tp,
        "horizon_bars": g.horizon_bars,
        "sigma": ev.get("sigma"),
        "leverage": "none applied: the levels are R (risk units) and prices, "
                    "not a leveraged equity return",
    }


def build_signals(events, outcomes=None, *, now=None, evidence=None,
                  max_age_s=MAX_SIGNAL_AGE_S):
    """Assemble the manual-review candidates from the shadow store rows.

    ``events`` are the rows of ``quant_event`` (dict-like), ``outcomes``
    the matching ``quant_outcome`` rows keyed by event id.
    """
    now = int(time.time()) if now is None else int(now)
    outcomes = outcomes or {}
    reg = by_id()
    out = []
    for ev in events:
        rule_id = ev.get("rule_id")
        spec = reg.get(rule_id)
        if spec is None:
            continue
        # Only SHADOW rows are surfaced. A legacy ACTIONABLE row in the
        # store is skipped: this surface shows candidates for manual
        # review, and an ACTIONABLE row is a different thing entirely.
        mode = (ev.get("mode") or "SHADOW").upper()
        if mode != "SHADOW":
            continue
        ts = int(ev.get("bar_ts") or 0)
        age = now - ts
        if age > max_age_s:
            continue                      # history, not a candidate
        res = outcomes.get(ev.get("id"))
        side = int(ev.get("side") or 0)
        out.append({
            "id": ev.get("id"),
            "coin": ev.get("coin"),
            "direction": "long" if side > 0 else ("short" if side < 0
                                                  else "flat"),
            "side": side,
            "rule_id": rule_id,
            "rule_label": _label_for(rule_id),
            "rule_version": spec.params.__class__.__module__ and
                            rule_id.split(":")[0],
            "role": ev.get("role"),
            "signal_ts": ts,
            "signal_ts_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC",
                                           time.gmtime(ts)),
            "data_freshness": {
                "signal_age_s": age,
                "signal_age_min": round(age / 60.0, 1),
                "fresh": age <= max_age_s,
                "basis": "the age of the signal bar (measured from its "
                         "open timestamp)",
            },
            "entry": {
                "price": (res or {}).get("entry_price"),
                "basis": "the open of the bar after the signal "
                         "(already observed)" if (res or {}).get("entry_price")
                         else "the open of the bar after the signal "
                              "(not yet observed - the reference price is "
                              "the signal bar's close)",
                "reference_price": ev.get("ref_price"),
            },
            "risk": {
                "stop": ev.get("stop"),
                "target": ev.get("tp"),
                "stop_frac": ev.get("stop_frac"),
                "horizon_bars": ev.get("horizon"),
                "assumptions": _assumptions(spec, ev),
            },
            "outcome": {
                "state": "resolved" if res else "open",
                "kind": (res or {}).get("kind"),
                "exit_ts": (res or {}).get("exit_ts"),
                "gross_log": (res or {}).get("gross_log"),
                "net_r": (res or {}).get("net_r"),
            },
            "costs": {
                "friction_frac": ev.get("cost_frac"),
                "fee_per_side": DEFAULT.cost.fee_per_side,
                "basis": "fee is the planner's UNCONFIRMED constant and the "
                         "slippage is tiered by trailing-24h quote volume - "
                         "both assumptions, not measurements",
                "funding_coverage": None,
            },
            "feature_id": ev.get("features_hash") or ev.get("feature_id"),
            "evidence": _evidence_for(rule_id, evidence, now,
                                      feature_id=ev.get("feature_id")),
            "execution": {
                "mode": mode,
                "actionable": False,
                "note": "MANUAL REVIEW ONLY - the app does not execute "
                        "this signal, and no promotion or validation result "
                        "authorises an order",
            },
        })
    out.sort(key=lambda s: (-int(s.get("signal_ts") or 0),
                             s.get("coin") or ""))
    return out


def read_store(quant_db):
    """Open the quant store READ-ONLY and return (events, outcomes).

    The store is opened with ``mode=ro``: the signal surface must never
    create the database, run DDL, change the journal mode, or commit
    anything.  A missing or unreadable store raises.
    """
    import sqlite3
    conn = sqlite3.connect(
        f"file:{os.path.abspath(quant_db)}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "quant_event" not in tables:
            raise sqlite3.OperationalError("quant_event table missing")
        events = [dict(r) for r in conn.execute(
            "SELECT * FROM quant_event ORDER BY bar_ts DESC")]
        outcomes = {}
        if "quant_outcome" in tables:
            outcomes = {r["event_id"]: dict(r) for r in conn.execute(
                "SELECT * FROM quant_outcome")}
    finally:
        conn.close()
    return events, outcomes


def signals_report(store, *, now=None, evidence=None,
                   max_age_s=MAX_SIGNAL_AGE_S):
    """The full payload for the manual-review route.

    ``store`` is either an object with ``all_events()``/``resolved_rows()``
    or a ``(events, outcomes)`` pair as returned by :func:`read_store`.
    """
    if isinstance(store, tuple):
        events, outcomes = store
    else:
        events = store.all_events()
        outcomes = {r["event_id"]: r for r in store.resolved_rows()}
    sigs = build_signals(events, outcomes, now=now, evidence=evidence,
                          max_age_s=max_age_s)
    counts = {"long": 0, "short": 0}
    for s in sigs:
        if s["direction"] in counts:
            counts[s["direction"]] += 1
    return {
        "version": SIGNALS_VERSION,
        "generated_ts": int(time.time()) if now is None else int(now),
        "count": len(sigs),
        "counts": counts,
        "signals": sigs,
        "note": "Rule-generated candidates for MANUAL REVIEW. The app does "
                "not execute them; a promotion or validation result never "
                "authorises an order.",
    }
