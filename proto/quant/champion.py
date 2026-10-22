"""WS-E: champion / candidate tracking and the matched comparison.

Two things live here, and neither of them promotes anything:

  1. **A versioned registry.**  ``Registry`` stores CONFIG VERSIONS
     (rule id, config hash, resolved params, geometry, data/code hashes,
     role, status, timestamp, notes).  The registry is a HISTORY: an entry
     is never mutated.  A change of status is a NEW entry with a new id,
     so ``set_champion`` appends a "champion" version and retires the
     previous champion by appending a "retired" version of it.  The
     superseded rows stay exactly as they were written.

  2. **The matched comparison.**  ``compare(champion, candidate, ...)``
     evaluates BOTH configurations on the SAME events, in the SAME window,
     with MATCHED costs (the same cost model, the same slippage/fee
     multipliers, the same funding attribution) and returns the paired
     difference with a day-cluster interval and a coin-cluster interval.

The rules the comparison obeys (they are the point, not decoration):

  * **The same events are VERIFIED, not assumed.**  The two evaluations
    must cover the same event ids.  If they do not, the comparison is
    REFUSED with a reason: a comparison across two different event sets
    is not a comparison.
  * **The applied cost model is verified, and the provenance is reported
    separately.**  Both sides are evaluated with ONE applied cost model
    (the multipliers are folded into the cost model itself), so
    ``same_applied_cost_model`` is a real check that happens to be true by
    construction.  What can differ is the PROVENANCE: the two versions
    may have been registered under different ``config_hash`` values, i.e.
    their evidence was produced under different cost models.  That is
    reported as ``same_config_hash`` and it is a refusal -- with a reason
    that says exactly that, instead of pretending the applied cost models
    differed.
  * **The two sides may read different label slots.**  The same event
    carries one barrier label per (geometry, side); two configurations
    with different geometries read different slots of the same event.
    That is the intended comparison, so it is RECORDED: every side block
    carries its ``label_slots`` and the result carries
    ``same_label_slot``.
  * **No silent zeros.**  When a side has no evaluable outcome the result
    is refused with a reason, never a zero difference.
  * **Every number carries its denominator.**  The result says what the
    numbers are over (``denominator``) and how many events were evaluated
    (``n_events``), how many were selected, how many were excluded and
    why.
  * **Funding is never zero-filled.**  The outcome layer
    (``competing_outcomes``) attributes venue funding when the archives
    are configured.  When funding cannot be attributed the comparison
    falls back to the explicitly labelled FRICTION-ONLY view
    (``metric = "friction_net_r"``), which is never presented as a net
    number.  Both sides always use the SAME metric.

**Promotion is a conscious act.**  ``Registry.set_champion`` records a
PROMOTION: it makes a configuration the champion and retires the previous
one.  It therefore refuses unless the caller passes the explicit opt-in
``allow_promote=True``, and the refusal names the risk.  Nothing in the
production path calls it, the CLI has no ``promote`` sub-command, and no
promotion happened while this module was written.

Stdlib only.  This module never promotes a rule on its own, never enables
ACTIONABLE output, never writes a decision and never touches the holdout.
"""
from __future__ import annotations   # the class below defines a method named
# `list`, so the `list[...]` annotations in its body would resolve to that
# method instead of the builtin and fail under Python < 3.14's eager
# annotation evaluation (the README promises Python 3.10+).  With this
# import the annotations are never evaluated at runtime.

import hashlib
import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass, field, replace

from . import competing_outcomes as CO
from . import inference as INF
from . import stats as S
from .config import DEFAULT, Geometry, RuleParams, grid_geometries

CHAMPION_VERSION = "champion-v1"

#: The on-disk schema of the registry database.
REGISTRY_SCHEMA_VERSION = "champion-registry-v1"

#: Minimum number of clusters for an interval to be reported at all.
MIN_CLUSTERS = 3

#: Bootstrap size of the reported intervals (the house default of
#: ``stats.day_cluster_mean``), so a reader can recompute them exactly.
BOOTSTRAP_B = 2000

#: The fields of a ``RuleParams`` and of a ``Geometry`` a config version
#: must carry.  A version that does not resolve to exactly ONE rule is
#: refused: silently dropping an unknown key or filling in a default
#: would evaluate a configuration nobody described.
RULE_PARAM_FIELDS = ("family", "policy", "rvol_min", "cusum_h")
GEOMETRY_FIELDS = ("horizon_bars", "k_stop", "r_tp")

_ID_RE = re.compile(r"^cfg-(\d+)$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS config_versions (
    id          TEXT PRIMARY KEY,
    rule_id     TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    params      TEXT NOT NULL,
    geometry    TEXT NOT NULL,
    role        TEXT NOT NULL,
    status      TEXT NOT NULL,
    data_hash   TEXT NOT NULL,
    code_hash   TEXT NOT NULL,
    created_ts  INTEGER NOT NULL,
    notes       TEXT NOT NULL,
    supersedes  TEXT,
    seq         INTEGER
);
CREATE TABLE IF NOT EXISTS registry_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ConfigVersion:
    """One version of one configuration.

    ``id``          stable, human readable ("cfg-0003").
    ``rule_id``     the rule this configuration runs (see
                    ``proto.quant.registry.by_id()``).
    ``config_hash`` the ``QuantConfig.config_hash()`` the evidence of this
                    version was produced under.  Results are only
                    comparable within one hash.
    ``params``      the resolved ``RuleParams`` as a dict -- all of
                    ``family``, ``policy``, ``rvol_min``, ``cusum_h``.
    ``geometry``    ``{"horizon_bars", "k_stop", "r_tp", "key"}``.
    ``role``        "candidate" | "champion" | "control" | "retired".
    ``status``      "candidate" | "champion" | "retired".
    ``data_hash``   the hash of the data the version was measured on.
    ``code_hash``   the hash of the code that produced the measurement.
    ``created_ts``  epoch seconds of the version.
    ``notes``       free text.
    """
    id: str
    rule_id: str
    config_hash: str
    params: dict = field(default_factory=dict)
    geometry: dict = field(default_factory=dict)
    role: str = "candidate"
    status: str = "candidate"
    data_hash: str = ""
    code_hash: str = ""
    created_ts: int = 0
    notes: str = ""


def _row_values(version, supersedes=None, seq=None):
    return (version.id, version.rule_id, version.config_hash,
            json.dumps(version.params or {}, sort_keys=True, default=str),
            json.dumps(version.geometry or {}, sort_keys=True, default=str),
            version.role, version.status, version.data_hash,
            version.code_hash, int(version.created_ts or 0), version.notes,
            supersedes, seq)


def _from_row(row):
    return ConfigVersion(
        id=row["id"], rule_id=row["rule_id"], config_hash=row["config_hash"],
        params=json.loads(row["params"]), geometry=json.loads(row["geometry"]),
        role=row["role"], status=row["status"], data_hash=row["data_hash"],
        code_hash=row["code_hash"], created_ts=row["created_ts"],
        notes=row["notes"])


class Registry:
    """A versioned history of configurations, in a SQLite file.

    The database file is created **only** at the explicit path the caller
    passes: there is no default location, and importing this module
    creates nothing at all.

    The registry is APPEND-ONLY.  ``add`` inserts a version; a duplicate
    id is an error.  ``set_champion`` does not edit a row -- it appends a
    new "champion" version of the promoted configuration and a new
    "retired" version of the champion it replaces, so every earlier
    version stays exactly as it was written.

    ``set_champion`` is a PROMOTION and needs the explicit opt-in
    ``allow_promote=True`` (see the module docstring).
    """

    def __init__(self, path: str):
        if not path:
            raise ValueError("the registry needs an explicit path")
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO registry_meta (key, value) VALUES (?, ?)",
            ("version", REGISTRY_SCHEMA_VERSION))
        self._conn.commit()

    # -- lifecycle ---------------------------------------------------------
    def close(self):
        """Close the database connection.  Safe to call twice."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- writes ------------------------------------------------------------
    def add(self, version: ConfigVersion) -> None:
        """Append ONE version.  A duplicate id is an error."""
        if not isinstance(version, ConfigVersion):
            raise TypeError("a ConfigVersion is required")
        if not version.id:
            raise ValueError("a config version needs an id")
        if self.get(version.id) is not None:
            raise ValueError(f"duplicate config id: {version.id}")
        stamp = int(version.created_ts or 0) or int(time.time())
        version = replace(version, created_ts=stamp)
        seq = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM config_versions").fetchone()[0]
        try:
            self._conn.execute(
                "INSERT INTO config_versions (id, rule_id, config_hash, params,"
                " geometry, role, status, data_hash, code_hash, created_ts,"
                " notes, supersedes, seq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                _row_values(version, None, seq))
        except sqlite3.IntegrityError:
            raise ValueError(f"duplicate config id: {version.id}") from None
        self._conn.commit()

    def set_champion(self, id: str, *, allow_promote: bool = False) -> None:
        """Make ``id`` the champion -- by APPENDING, never by editing.

        **This is a promotion and it needs the explicit opt-in**
        ``allow_promote=True``.  Without it the call is refused with a
        reason: a champion is the operator's live configuration, and
        recording one changes what the platform believes is current, so
        it must be a conscious act with the promotion gate's evidence
        (``proto.quant.gates``) in hand -- never a side effect of a
        measurement.

        The previous champion is retired by appending a "retired" version
        of it; the new champion is appended as a new "champion" version of
        the promoted configuration.  The superseded rows keep their
        history.  Promoting the current champion again is a NO-OP: the
        call compares the champion's LINEAGE with the target, so asking
        twice does not append four rows.
        """
        if not allow_promote:
            raise ValueError(
                "set_champion records a PROMOTION (it makes a configuration "
                "the champion and retires the previous one), so it needs the "
                "explicit opt-in allow_promote=True. Promote only with the "
                "promotion gate's evidence recorded (proto.quant.gates); a "
                "measurement must never promote anything by accident.")
        target = self.get(id)
        if target is None:
            raise ValueError(f"unknown config id: {id}")
        current = self.champion()
        if current is not None and (target.id == current.id
                                    or target.id in self._lineage(current.id)):
            return                      # already the champion (same lineage)
        now = int(time.time())
        if current is not None:
            retired = replace(current, id=self._next_id(), role="retired",
                              status="retired", created_ts=now,
                              notes=(current.notes
                                     + " | retired: superseded as champion"
                                     ).strip(" |"))
            self._insert(retired, supersedes=current.id)
        champion = replace(target, id=self._next_id(), role="champion",
                           status="champion", created_ts=now,
                           notes=(target.notes + " | champion").strip(" |"))
        self._insert(champion, supersedes=target.id)

    def _insert(self, version, supersedes=None):
        seq = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM config_versions").fetchone()[0]
        self._conn.execute(
            "INSERT INTO config_versions (id, rule_id, config_hash, params,"
            " geometry, role, status, data_hash, code_hash, created_ts,"
            " notes, supersedes, seq) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _row_values(version, supersedes, seq))
        self._conn.commit()

    def _next_id(self):
        highest = 0
        for row in self._conn.execute("SELECT id FROM config_versions"):
            m = _ID_RE.match(row["id"])
            if m:
                highest = max(highest, int(m.group(1)))
        return f"cfg-{highest + 1:04d}"

    # -- reads -------------------------------------------------------------
    def get(self, id: str) -> ConfigVersion | None:
        """One version by id (any version, superseded or not)."""
        row = self._conn.execute(
            "SELECT * FROM config_versions WHERE id = ?", (id,)).fetchone()
        return _from_row(row) if row else None

    def _lineage(self, id: str) -> set:
        """The ids the given version descends from (its supersedes chain).

        A promoted configuration is a NEW version of an earlier one, so
        "is this already the champion?" is a question about the LINEAGE,
        not about the raw ids.
        """
        seen = set()
        current = id
        while current and current not in seen:
            seen.add(current)
            row = self._conn.execute(
                "SELECT supersedes FROM config_versions WHERE id = ?",
                (current,)).fetchone()
            if row is None:
                break
            current = row["supersedes"]
        return seen

    def list(self, status: str | None = None) -> list[ConfigVersion]:
        """Every version in insertion order, optionally filtered by status.

        This is the HISTORY: a superseded version is still in the list.
        Use ``list_current`` for the live view.
        """
        sql = "SELECT * FROM config_versions"
        args = ()
        if status is not None:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY seq"
        return [_from_row(row) for row in self._conn.execute(sql, args)]

    def list_current(self, status: str | None = None) -> list[ConfigVersion]:
        """The versions that no later version supersedes (the live view)."""
        superseded = {row["supersedes"] for row in self._conn.execute(
            "SELECT supersedes FROM config_versions WHERE supersedes IS NOT NULL")}
        return [v for v in self.list(status) if v.id not in superseded]

    def champion(self) -> ConfigVersion | None:
        """The CURRENT champion (the most recent "champion" version)."""
        row = self._conn.execute(
            "SELECT * FROM config_versions WHERE status = 'champion' "
            "ORDER BY seq DESC LIMIT 1").fetchone()
        return _from_row(row) if row else None


# ---------------------------------------------------------------------------
# the matched comparison
# ---------------------------------------------------------------------------
def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _effective_cfg(cfg, slip_mult, fee_mult):
    """The cost model with the multipliers folded in.

    The multipliers are applied to the COST MODEL itself (so the outcome
    layer, which asks the cost model for the round-trip friction, prices
    both sides identically) rather than to the numbers afterwards.
    """
    base = cfg.cost
    cost = replace(
        base,
        fee_per_side=base.fee_per_side * fee_mult,
        slip_tiers=tuple((floor, slip * slip_mult)
                         for floor, slip in base.slip_tiers))
    return replace(cfg, cost=cost)


def _applied_cost_model(cfg, slip_mult, fee_mult, funding_dir,
                        settlements_dir):
    """The cost model ONE side is evaluated with, described and hashed.

    Both sides of a comparison are evaluated with the SAME applied cost
    model (the multipliers are folded into the cost model), so the
    descriptor is still computed per side and compared: the check is a
    real check, and it is reported as ``same_applied_cost_model``.

    The PROVENANCE of a side (the ``config_hash`` its evidence was
    produced under) is a different fact and is reported separately as
    ``same_config_hash``.
    """
    eff = _effective_cfg(cfg, slip_mult, fee_mult)
    payload = {
        "fee_per_side": eff.cost.fee_per_side,
        "slip_tiers": [list(t) for t in eff.cost.slip_tiers],
        "min_qv24": eff.cost.min_qv24,
        "slip_mult": slip_mult,
        "fee_mult": fee_mult,
        "funding_dir": funding_dir,
        "settlements_dir": settlements_dir,
    }
    out = dict(payload)
    out["hash"] = hashlib.sha256(
        _canonical(payload).encode("utf-8")).hexdigest()
    return out


def _resolve(version):
    """(rule, geometry, error): the rule and geometry of a config version.

    A version that does not resolve to exactly ONE rule is REFUSED with a
    reason: dropping an unknown param key or filling in a dataclass
    default would silently evaluate a configuration the caller never
    described (a missing ``params`` must not become "breakout_start /
    fade / rvol_min=4.0" by accident).
    """
    params = version.params
    if not isinstance(params, dict) or not params:
        return None, None, ("the params are missing: a rule needs "
                            + ", ".join(RULE_PARAM_FIELDS))
    unknown = sorted(set(params) - set(RULE_PARAM_FIELDS))
    if unknown:
        return None, None, f"the params have unknown keys: {unknown}"
    missing = [f for f in RULE_PARAM_FIELDS if f not in params]
    if missing:
        return None, None, f"the params are missing: {missing}"
    for field in ("family", "policy"):
        if not isinstance(params[field], str) or not params[field]:
            return None, None, (f"the params' {field} must be a non-empty "
                                f"string, got {params[field]!r}")
    for field in ("rvol_min", "cusum_h"):
        value = params[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, None, (f"the params' {field} must be a number, got "
                                f"{value!r}")
    try:
        rule = RuleParams(**params)
    except (TypeError, ValueError) as exc:
        return None, None, f"the params are not a RuleParams: {exc}"

    geometry = version.geometry
    if not isinstance(geometry, dict) or not geometry:
        return None, None, ("the geometry is missing: a geometry needs "
                            + ", ".join(GEOMETRY_FIELDS))
    unknown = sorted(set(geometry) - set(GEOMETRY_FIELDS) - {"key"})
    if unknown:
        return None, None, f"the geometry has unknown keys: {unknown}"
    missing = [f for f in GEOMETRY_FIELDS if f not in geometry]
    if missing:
        return None, None, f"the geometry is missing: {missing}"
    for field in GEOMETRY_FIELDS:
        value = geometry[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, None, (f"the geometry's {field} must be a number, "
                                f"got {value!r}")
    try:
        geom = Geometry(int(geometry["horizon_bars"]),
                        float(geometry["k_stop"]),
                        float(geometry["r_tp"]))
    except (TypeError, ValueError) as exc:
        return None, None, f"the geometry is not a Geometry: {exc}"
    if not any(g.horizon_bars == geom.horizon_bars
               and g.k_stop == geom.k_stop and g.r_tp == geom.r_tp
               for g in grid_geometries()):
        return None, None, (f"the geometry {geom.key()} is not in the "
                            "pre-declared grid, so the events' label slots "
                            "cannot be addressed")
    return rule, geom, None


def _label_slots(geom):
    """The two label slots (long and short) a geometry reads per event."""
    try:
        return sorted((CO.slot_index(geom, 1), CO.slot_index(geom, -1)))
    except ValueError:
        return None


def _value(outcome, metric):
    if metric == "net_r":
        return outcome.net_r
    return CO.friction_net_r(outcome)


def _side_block(version, values, days, coins, causes, n_selected, n_evaluable,
                metric, seed=1, geometry=None, label_slots=None):
    """The per-side report: the mean over the PAIRED events, its interval
    and its denominators.

    The interval follows the SAME rule as the cluster intervals in
    ``inference.uncertainty``: a degenerate (zero-width) day-cluster
    interval is reported as NO interval and NO statistic, with a reason --
    it is never presented as if it were a cluster interval.
    """
    n = len(values)
    block = {
        "id": version.id,
        "rule_id": version.rule_id,
        "status": version.status,
        "role": version.role,
        "config_hash": version.config_hash,
        "n": n,
        "n_selected": n_selected,
        "n_evaluable": n_evaluable,
        "mean_r": (sum(values) / n) if n else None,
        "ci_lo": None,
        "ci_hi": None,
        "t": None,
        "days": len(set(days)),
        "coins": len(set(coins)),
        "causes": causes,
        "metric": metric,
        "geometry": (geometry.key() if geometry is not None else None),
        "label_slots": list(label_slots) if label_slots else None,
        "denominator": ("the paired events: the events BOTH configurations "
                        "were evaluated on, in the window, with the "
                        f"'{metric}' metric"),
        "reason": None,
    }
    n_days = len(set(days))
    if n and n_days >= MIN_CLUSTERS:
        _m, lo, hi, _D = S.day_cluster_mean(values, days, B=BOOTSTRAP_B,
                                            seed=seed)
        if (lo is not None and hi is not None and math.isfinite(lo)
                and math.isfinite(hi) and lo < hi):
            block["ci_lo"], block["ci_hi"] = lo, hi
            block["t"] = S.day_cluster_t(values, days)
        else:
            # A degenerate (zero-width) day-cluster interval is NOT evidence
            # of certainty: no interval, no statistic, and a reason.
            block["reason"] = ("the day-cluster interval is suppressed: the "
                               "bootstrap produced a degenerate (zero-width) "
                               "interval")
    elif n:
        block["reason"] = (f"the day-cluster interval is suppressed: only "
                           f"{n_days} distinct days (< {MIN_CLUSTERS})")
    return block


def _refusal(reason, verified=None, **extra):
    """The result shape of a refused comparison: no numbers, one reason.

    The verification flags report what was ACTUALLY verified before the
    refusal -- ``None`` means "not established" (not "false").
    """
    out = {
        "n_events": 0, "n_champion": 0, "n_candidate": 0,
        "same_events": None,
        "same_applied_cost_model": None,
        "same_config_hash": None,
        "same_label_slot": None,
        "cost_model": {},
        "champion": {}, "candidate": {},
        "difference": None, "ci_lo": None, "ci_hi": None, "t": None,
        "clusters": {"day": 0, "coin": 0},
        "disagreements": 0,
        "denominator": "no comparison: the comparison was refused",
        "refused": True,
        "reason": reason,
        "reasons": [reason] if reason else [],
        "metric": None,
        "ci_lo_coin": None, "ci_hi_coin": None, "t_coin": None,
    }
    if verified:
        out.update(verified)
    out.update(extra)
    # The brief's key: the two sides were priced with the same APPLIED
    # cost model.  The provenance equality is reported separately.
    out["same_cost_model"] = out["same_applied_cost_model"]
    return out


def compare(champion: ConfigVersion, candidate: ConfigVersion, events, window,
            *, cfg=DEFAULT, funding_dir=None, settlements_dir=None,
            slip_mult=1.0, fee_mult=1.0, seed=1) -> dict:
    """Compare a champion and a candidate on the SAME events with MATCHED costs.

    Both configurations are evaluated on the same event set, in the same
    window, with the same applied cost model, the same slippage/fee
    multipliers and the same funding attribution.  The result is the
    PAIRED difference (per event: candidate minus champion) with a
    day-cluster interval and a coin-cluster interval.

    Returns a dict with::

        n_events        the paired, evaluable events (the denominator of
                        every number below)
        n_champion      the champion's evaluable count on those events
        n_candidate     the candidate's evaluable count on those events
        same_events     True when both evaluations covered the same event
                        ids (VERIFIED, not assumed)
        same_cost_model True when both sides were priced with the same
                        APPLIED cost model (the brief's key; the applied
                        model is shared, so this is True by construction
                        and is verified per side)
        same_applied_cost_model  the same fact under its explicit name
        same_config_hash True when the two versions were registered under
                        the same config hash (their PROVENANCE)
        same_label_slot True when the two sides read the SAME label slot
                        of each event; False when they read different
                        ones (different geometries -- the intended
                        comparison), None when not established
        cost_model      the applied cost model the comparison ran under
        champion        the champion's per-side report (with its
                        ``label_slots`` and ``geometry``)
        candidate       the candidate's per-side report
        difference      the mean paired difference (candidate - champion)
        ci_lo / ci_hi   the day-cluster interval of the paired differences
        t               the day-cluster t of the paired differences
        clusters        {"day": n_days, "coin": n_coins}
        disagreements   the paired events where the two sides disagree on
                        the outcome cause
        denominator     what the numbers are over

    plus ``refused`` / ``reason`` / ``reasons``, ``metric`` (which metric
    the numbers use) and ``ci_lo_coin`` / ``ci_hi_coin`` / ``t_coin``.

    Refusals (the result carries a reason and NO numbers; the verification
    flags report what WAS verified before the refusal):

      * the two evaluations covered different event sets;
      * the two versions were registered under different config hashes
        (their evidence was produced under different cost models);
      * a side has no evaluable outcome;
      * a version's params or geometry do not resolve to exactly one rule,
        or its geometry is not in the pre-declared grid (the label slots
        of the events are addressed by that grid, so there is nothing to
        read).

    The metric is "net_r" when the venue funding cost can be attributed,
    and "friction_net_r" (explicitly friction-only, never a net number)
    when it cannot.  Both sides always use the same metric.
    """
    # -- what has been VERIFIED so far (a refusal reports exactly this) --
    verified = {
        "same_applied_cost_model": None,
        "same_config_hash": None,
        "same_events": None,
        "same_label_slot": None,
    }

    cm_ch = _applied_cost_model(cfg, slip_mult, fee_mult, funding_dir,
                                settlements_dir)
    cm_ca = _applied_cost_model(cfg, slip_mult, fee_mult, funding_dir,
                                settlements_dir)
    verified["same_applied_cost_model"] = cm_ch["hash"] == cm_ca["hash"]
    verified["same_config_hash"] = (champion.config_hash
                                     == candidate.config_hash)

    ch_rule, ch_geom, ch_err = _resolve(champion)
    if ch_err:
        return _refusal(f"the champion cannot be resolved: {ch_err}",
                        verified, cost_model=cm_ch,
                        champion_id=champion.id, candidate_id=candidate.id,
                        window=[window[0], window[1]])
    ca_rule, ca_geom, ca_err = _resolve(candidate)
    if ca_err:
        return _refusal(f"the candidate cannot be resolved: {ca_err}",
                        verified, cost_model=cm_ch,
                        champion_id=champion.id, candidate_id=candidate.id,
                        window=[window[0], window[1]])

    slots_ch = _label_slots(ch_geom)
    slots_ca = _label_slots(ca_geom)
    verified["same_label_slot"] = (slots_ch is not None and slots_ca is not None
                                   and slots_ch == slots_ca)

    if not verified["same_config_hash"]:
        return _refusal(
            "the two configurations were registered under DIFFERENT config "
            f"hashes (champion {champion.config_hash}, candidate "
            f"{candidate.config_hash}), so their evidence was produced under "
            "different cost models; the comparison is refused. Register both "
            "under one config hash (or supersede) before comparing.",
            verified, cost_model=cm_ch,
            champion_id=champion.id, candidate_id=candidate.id,
            window=[window[0], window[1]])

    eff = _effective_cfg(cfg, slip_mult, fee_mult)
    try:
        res_ch = CO.resolve_competing(events, ch_rule, ch_geom, window, eff,
                                      funding_dir=funding_dir,
                                      settlements_dir=settlements_dir)
        res_ca = CO.resolve_competing(events, ca_rule, ca_geom, window, eff,
                                      funding_dir=funding_dir,
                                      settlements_dir=settlements_dir)
    except ValueError as exc:
        return _refusal(f"the configurations cannot be evaluated: {exc}",
                        verified, cost_model=cm_ch,
                        champion_id=champion.id, candidate_id=candidate.id,
                        window=[window[0], window[1]])

    pairs_ch = list(zip(res_ch["selected_events"], res_ch["outcomes"]))
    pairs_ca = list(zip(res_ca["selected_events"], res_ca["outcomes"]))
    map_ch = {o.event_id: (ev, o) for ev, o in pairs_ch}
    map_ca = {o.event_id: (ev, o) for ev, o in pairs_ca}
    if len(map_ch) != len(pairs_ch) or len(map_ca) != len(pairs_ca):
        return _refusal("the event ids are not unique; the comparison would "
                        "pair an event with itself",
                        verified, cost_model=cm_ch,
                        champion_id=champion.id, candidate_id=candidate.id,
                        window=[window[0], window[1]])

    ids_ch = [o.event_id for _ev, o in pairs_ch]
    ids_ca = [o.event_id for _ev, o in pairs_ca]
    verified["same_events"] = (set(ids_ch) == set(ids_ca)
                               and len(ids_ch) == len(ids_ca))
    if not verified["same_events"]:
        only_ch = sorted(set(ids_ch) - set(ids_ca))[:3]
        only_ca = sorted(set(ids_ca) - set(ids_ch))[:3]
        return _refusal(
            f"the two configurations were evaluated on different event sets "
            f"(champion {len(ids_ch)} events, candidate {len(ids_ca)} events); "
            f"champion-only: {only_ch}, candidate-only: {only_ca}",
            verified, cost_model=cm_ch,
            champion_id=champion.id, candidate_id=candidate.id,
            window=[window[0], window[1]])

    # --- the metric: net R when funding is attributable, friction-only else
    metric = "net_r"
    paired = [(eid, map_ch[eid], map_ca[eid]) for eid in ids_ch
              if _value(map_ch[eid][1], metric) is not None
              and _value(map_ca[eid][1], metric) is not None]
    if not paired:
        metric = "friction_net_r"
        paired = [(eid, map_ch[eid], map_ca[eid]) for eid in ids_ch
                  if _value(map_ch[eid][1], metric) is not None
                  and _value(map_ca[eid][1], metric) is not None]
        reasons = [
            "the venue funding cost could not be attributed to these events: "
            "the comparison is on the FRICTION-ONLY net R "
            "(metric=friction_net_r), which is not a net number"]
    else:
        reasons = []
    if not paired:
        return _refusal(
            "neither side has an evaluable outcome in this window; there is "
            "nothing to compare (no number is reported for an empty sample)",
            verified, cost_model=cm_ch,
            champion_id=champion.id, candidate_id=candidate.id,
            window=[window[0], window[1]])

    ch_vals = [_value(map_ch[eid][1], metric) for eid, _a, _b in paired]
    ca_vals = [_value(map_ca[eid][1], metric) for eid, _a, _b in paired]
    days = [int(a[0].day) for _eid, a, _b in paired]
    coins = [a[0].coin for _eid, a, _b in paired]
    diffs = [c - a for a, c in zip(ch_vals, ca_vals)]
    n = len(paired)

    n_days = len(set(days))
    n_coins = len(set(coins))

    if n_days >= MIN_CLUSTERS:
        mean, ci_lo, ci_hi, _D = S.day_cluster_mean(diffs, days,
                                                     B=BOOTSTRAP_B, seed=seed)
        if (ci_lo is None or ci_hi is None or not math.isfinite(ci_lo)
                or not math.isfinite(ci_hi) or ci_lo >= ci_hi):
            # A degenerate (zero-width) day-cluster interval is NOT evidence
            # of certainty: no interval and no statistic, with a reason.
            ci_lo = ci_hi = None
            t = None
            reasons.append("the day-cluster interval is suppressed: the "
                           "bootstrap produced a degenerate (zero-width) "
                           "interval")
        else:
            t = S.day_cluster_t(diffs, days)
    else:
        mean = sum(diffs) / n
        ci_lo = ci_hi = None
        t = None
        reasons.append(f"the day-cluster interval is suppressed: only "
                       f"{n_days} distinct days (< {MIN_CLUSTERS})")

    unc = INF.uncertainty(diffs, days, coins, B=BOOTSTRAP_B, seed=seed)
    ci_lo_coin = unc["ci_lo_coin"]
    ci_hi_coin = unc["ci_hi_coin"]
    t_coin = unc["t_coin"]
    if n_coins < MIN_CLUSTERS:
        reasons.append(f"the coin-cluster interval is suppressed: only "
                       f"{n_coins} distinct coins (< {MIN_CLUSTERS})")
    else:
        reasons.extend(r for r in unc["reasons"] if "coin" in r)

    causes_ch = {}
    causes_ca = {}
    disagreements = 0
    for _eid, a, b in paired:
        causes_ch[a[1].cause] = causes_ch.get(a[1].cause, 0) + 1
        causes_ca[b[1].cause] = causes_ca.get(b[1].cause, 0) + 1
        if a[1].cause != b[1].cause:
            disagreements += 1

    n_eval_ch = sum(1 for _ev, o in pairs_ch
                    if _value(o, metric) is not None)
    n_eval_ca = sum(1 for _ev, o in pairs_ca
                    if _value(o, metric) is not None)

    denominator = (f"the {n} events both configurations were evaluated on in "
                   f"the window {list(window)} (rule-selected, tradeable, "
                   f"and evaluable under the '{metric}' metric)")

    out = {
        "n_events": n,
        "n_champion": n,
        "n_candidate": n,
        "same_events": True,
        "same_applied_cost_model": True,
        "same_config_hash": True,
        "same_label_slot": verified["same_label_slot"],
        "cost_model": cm_ch,
        "champion": _side_block(champion, ch_vals, days, coins, causes_ch,
                                len(pairs_ch), n_eval_ch, metric, seed,
                                ch_geom, slots_ch),
        "candidate": _side_block(candidate, ca_vals, days, coins, causes_ca,
                                 len(pairs_ca), n_eval_ca, metric, seed,
                                 ca_geom, slots_ca),
        "difference": mean,
        "ci_lo": ci_lo,
        "ci_hi": ci_hi,
        "t": t,
        "clusters": {"day": n_days, "coin": n_coins},
        "disagreements": disagreements,
        "denominator": denominator,
        "refused": False,
        "reason": None,
        "reasons": reasons,
        "metric": metric,
        "ci_lo_coin": ci_lo_coin,
        "ci_hi_coin": ci_hi_coin,
        "t_coin": t_coin,
        "window": [window[0], window[1]],
        "champion_id": champion.id,
        "candidate_id": candidate.id,
        "version": CHAMPION_VERSION,
    }
    out["same_cost_model"] = out["same_applied_cost_model"]
    return out
