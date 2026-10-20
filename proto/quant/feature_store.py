"""Point-in-time FEATURE STORE: versioned, append-only, as-of joined.

DESCRIPTIVE ONLY.  The store records measurements; it never selects, scores
or signals.  The point-in-time rule is the whole point of this module:

  * a value is stored with the timestamp at which it BECAME KNOWABLE
    (``available_ts``), which is never earlier than the bar it describes
    (``bar_ts``);
  * an as-of query returns the newest record with ``available_ts <= ts`` and
    NOTHING else: no future value, no forward fill, no interpolation;
  * a record that is older than the requested maximum age is reported as
    "stale" (value None, reason "stale"), and a feature that does not exist
    is reported as None (the caller then knows it is missing, not zero).

The store is append-only: ``put`` inserts and never overwrites, so the
history of a measurement cannot be rewritten.  A row that already exists
under the same key but with DIFFERENT content is refused as a provenance
conflict rather than silently ignored - otherwise a caller reusing a
feature identity for a different configuration would believe its record
was stored when it was not, and the stored row would carry the other
configuration's identity.

FEATURE IDENTITY.  A feature's identity is ``(coin, name, version,
bar_ts)``, and ``version`` is the full feature identity: the measure
version, plus the CONFIGURATION identity when the measure has one.  A
pivot's configuration (``left``/``right``/``frame_bars``) decides both
which bars are pivots and when each becomes knowable, so it is part of
the identity: ``pivots-v1+L2R2F1``.  Two pivot configurations therefore
never share a uniqueness key and never share a provenance hash.  Each row
also keeps the ``inputs_hash`` of the inputs that produced it (the series
and the parameters), so a parameter change, a measure-version bump or a
data revision is visible in the data.

Standard library only (hashlib, json, os, sqlite3, time).
"""
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass, replace

from .config import BAR_S
from .structure import PIVOT_VERSION

FEATURE_STORE_VERSION = "features-v1"

REASONS = ("missing", "stale", "insufficient")

__all__ = ["FEATURE_STORE_VERSION", "REASONS", "FeatureRecord", "FeatureStore",
           "from_estimate", "from_pivots", "pivot_config_id",
           "pivot_feature_version"]


@dataclass(frozen=True)
class FeatureRecord:
    """One stored feature value.

    coin          the symbol the feature belongs to
    name          the feature name ("hurst_rs", "breadth", ...)
    version       the FEATURE IDENTITY: the measure version, plus the
                  configuration identity when the measure has one (for
                  pivots: "pivots-v1+L2R2F1").  Two configurations of the
                  same measure are therefore two different features and
                  never share a uniqueness key or a provenance hash.
    bar_ts        the bar (or timestamp) the feature DESCRIBES
    available_ts  when the value became knowable (>= bar_ts)
    value         the measured value, or None
    reason        None when present, else one of REASONS ("missing" |
                  "stale" | "insufficient").  The vocabulary is a small,
                  stable contract: a more specific estimator reason (e.g.
                  "degenerate-zero-variance") is NOT stored in this field -
                  it lives in ``meta["estimator_reason"]``, so the stored
                  enum never grows and a consumer can rely on it.
    inputs_hash   the hash of the inputs that produced the value
    meta          the estimator parameters, window, uncertainty, ...
    """
    coin: str
    name: str
    version: str
    bar_ts: int
    available_ts: int
    value: float | None
    reason: str | None
    inputs_hash: str
    meta: dict


def from_estimate(coin, name, bar_ts, available_ts, estimate) -> FeatureRecord:
    """Build a :class:`FeatureRecord` from an ``estimators.Estimate``.

    The stored ``reason`` is always one of :data:`REASONS`
    ("missing" | "stale" | "insufficient"); the estimator's own reason
    (e.g. "degenerate-zero-variance") is kept in ``meta["estimator_reason"]``
    so the diagnosis survives without breaking the documented enum.  A
    degenerate estimate and a feature that was never computed therefore both
    store "missing" - the two are told apart by ``meta["estimator_reason"]``
    (and by ``meta["n"]``), not by the stored reason.  The
    estimator's parameters, sample size and uncertainty go into ``meta`` so
    the record is self-describing.
    """
    if estimate.value is None:
        reason = "insufficient" if "insufficient" in (estimate.reason or "") \
            else "missing"
    else:
        reason = None
    meta = dict(estimate.params or {})
    meta["n"] = estimate.n
    if estimate.uncertainty is not None:
        meta["uncertainty"] = estimate.uncertainty
    meta["estimator_version"] = estimate.version
    if estimate.reason:
        meta["estimator_reason"] = estimate.reason
    return FeatureRecord(coin=coin, name=name, version=estimate.version,
                         bar_ts=int(bar_ts), available_ts=int(available_ts),
                         value=estimate.value, reason=reason,
                         inputs_hash=estimate.inputs_hash, meta=meta)


def pivot_config_id(left: int, right: int, frame_bars: int) -> str:
    """Canonical identity of a pivot configuration, e.g. ``L2R2F1``.

    ``left``/``right`` decide WHICH bars are pivots and ``right`` and
    ``frame_bars`` decide WHEN a pivot becomes knowable
    (``confirmed_ts = ts + (right + 1) * frame_bars * BAR_S``).  A pivot
    configuration is therefore part of the feature's identity, not an
    incidental parameter: two configurations can disagree about both the
    set of pivots and the instant each becomes knowable.

    The accepted settings are exactly the ones
    :func:`proto.quant.structure.confirmed_pivots` accepts: ``left`` and
    ``right`` are NON-NEGATIVE integers (the detector allows ``left=0`` and
    ``right=0``) and ``frame_bars`` is a positive integer.  Being stricter
    than the detector would make a legitimate pivot unstorable.
    """
    for label, value, minimum in (("left", left, 0), ("right", right, 0),
                                  ("frame_bars", frame_bars, 1)):
        if not isinstance(value, int) or isinstance(value, bool) \
                or value < minimum:
            raise ValueError(
                f"{label} must be an integer >= {minimum}, got {value!r}")
    return f"L{left}R{right}F{frame_bars}"


def pivot_feature_version(left: int, right: int, frame_bars: int) -> str:
    """The feature identity a pivot record is stored under.

    The identity is the measure version **plus** the configuration
    identity, so two pivot configurations can never share a key (and
    therefore can never silently collide under the store's uniqueness
    constraint) nor a provenance hash.
    """
    return f"{PIVOT_VERSION}+{pivot_config_id(left, right, frame_bars)}"


def from_pivots(coin, name, pivots, *, left: int, right: int,
                frame_bars: int, available_ts=None, meta=None
                ) -> list[FeatureRecord]:
    """One :class:`FeatureRecord` per confirmed pivot.

    The records carry the PIVOT_VERSION **and** the pivot configuration
    identity (``left``/``right``/``frame_bars``), so a stored pivot is
    always attributable to the exact configuration that produced it:
    different configurations are different feature identities, they never
    share a uniqueness key, and their provenance hashes differ.

    The configuration is REQUIRED, and it is CROSS-CHECKED against the
    pivots themselves: a pivot's ``confirmed_ts`` must equal
    ``ts + (right + 1) * frame_bars * BAR_S`` for the declared
    configuration, so a caller cannot claim a configuration that does not
    match the data it is storing.

    ``bar_ts`` is the pivot's own timestamp and ``available_ts`` the
    instant the pivot became KNOWABLE.  ``available_ts`` DEFAULTS to the
    pivot's ``confirmed_ts``; an ``available_ts`` EARLIER than the
    confirmation is REFUSED (ValueError): storing a pivot as knowable
    before it was knowable would let an as-of join return a look-ahead,
    which is the one thing this store exists to prevent.
    """
    cfg = pivot_config_id(left, right, frame_bars)
    version = pivot_feature_version(left, right, frame_bars)
    span = (right + 1) * frame_bars * BAR_S
    out = []
    for p in pivots or ():
        if int(p.confirmed_ts) != int(p.ts) + span:
            raise ValueError(
                f"pivot at ts {p.ts} has confirmed_ts {p.confirmed_ts}, which "
                f"does not match the declared configuration {cfg} "
                f"(expected ts + (right + 1) * frame_bars * BAR_S = "
                f"{int(p.ts) + span}); the pivots were produced with a "
                f"different configuration")
        avail = int(p.confirmed_ts) if available_ts is None else int(available_ts)
        if avail < int(p.confirmed_ts):
            raise ValueError(
                f"available_ts {avail} is before the pivot's confirmed_ts "
                f"{p.confirmed_ts}: a pivot cannot be knowable before it is "
                f"confirmed")
        m = dict(meta or {})
        m.update({"kind": p.kind, "leg_bars": p.leg_bars, "size": p.size,
                  "pivot_index": p.index, "pivot_version": PIVOT_VERSION,
                  "pivot_confirmed_ts": p.confirmed_ts,
                  "pivot_config_id": cfg, "left": left, "right": right,
                  "frame_bars": frame_bars})
        h = hashlib.sha256()
        h.update(f"{coin}|{name}|{version}|{cfg}|{p.index}|{p.ts}|".encode())
        h.update(repr(float(p.price)).encode())
        out.append(FeatureRecord(coin=coin, name=name, version=version,
                                  bar_ts=int(p.ts), available_ts=avail,
                                  value=float(p.price), reason=None,
                                  inputs_hash=h.hexdigest()[:16], meta=m))
    return out


class FeatureStore:
    """Append-only SQLite feature store (one file, no global state).

    The table is ``feature(coin, name, version, bar_ts, available_ts, value,
    reason, inputs_hash, meta, recorded_ts)`` with
    ``UNIQUE(coin, name, version, bar_ts)``: the key is the feature identity
    plus the bar it describes.  ``version`` is the FULL feature identity -
    the measure version plus the configuration identity when the measure has
    one (for pivots: ``pivots-v1+L2R2F1``), so two configurations of the
    same measure are two different features and cannot alias under the key.

    The connection follows the house conventions: the 5 s busy timeout comes
    from ``proto/service.py`` (e.g. :312) and ``proto/daemon.py:179`` (the
    other store classes use ``sqlite3.connect(path)`` or a 30 s timeout), and
    the WAL pragma from ``proto/quant/qstore.py:65`` and
    ``proto/store.py:384``.  ``check_same_thread=False`` is used HERE ONLY -
    it appears nowhere else in the repo - because the service and the daemon
    are threaded and this store is written from worker threads.
    """

    version = FEATURE_STORE_VERSION

    def __init__(self, path):
        self.path = path
        if path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS feature ("
            "coin TEXT NOT NULL, name TEXT NOT NULL, version TEXT NOT NULL, "
            "bar_ts INTEGER NOT NULL, available_ts INTEGER NOT NULL, "
            "value REAL, reason TEXT, inputs_hash TEXT NOT NULL, "
            "meta TEXT NOT NULL DEFAULT '{}', recorded_ts INTEGER NOT NULL, "
            "UNIQUE(coin, name, version, bar_ts))")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS feature_asof ON feature "
            "(coin, name, version, available_ts)")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, "
            "value TEXT NOT NULL)")
        self._conn.execute(
            "INSERT OR REPLACE INTO store_meta (key, value) VALUES (?,?)",
            ("schema_version", FEATURE_STORE_VERSION))
        self._conn.commit()

    # ---------------------------------------------------------------- write
    def put(self, record) -> bool:
        """Insert a record; never overwrites.  Returns True when the row was
        inserted, False when an IDENTICAL row already existed (the first
        write wins, and re-storing the same content is idempotent).

        A record that claims to be knowable BEFORE the bar it describes
        (``available_ts < bar_ts``) is refused with a ValueError: it would
        let a value be joined to a moment it could not have existed.  A
        reason outside :data:`REASONS` is refused for the same reason (the
        stored vocabulary is a contract).

        A row that already exists under the same key but with DIFFERENT
        content (a different value, availability or provenance hash) is a
        PROVENANCE CONFLICT and is refused with a ValueError.  Silently
        ignoring it would leave the earlier record - and its
        ``available_ts`` - in place, so a caller reusing a feature identity
        for a different configuration would believe its record was stored
        when it was not, and the stored record would carry the other
        configuration's identity.  History is never rewritten either way.
        """
        if int(record.available_ts) < int(record.bar_ts):
            raise ValueError(
                f"available_ts {record.available_ts} is before bar_ts "
                f"{record.bar_ts}: a value cannot be knowable before its bar")
        if record.reason is not None and record.reason not in REASONS:
            raise ValueError(
                f"reason {record.reason!r} is not one of {REASONS}")
        existing = self._conn.execute(
            "SELECT value, available_ts, inputs_hash FROM feature "
            "WHERE coin=? AND name=? AND version=? AND bar_ts=?",
            (record.coin, record.name, record.version,
             int(record.bar_ts))).fetchone()
        if existing is not None:
            same = (existing[0] == record.value
                    and int(existing[1]) == int(record.available_ts)
                    and existing[2] == record.inputs_hash)
            if same:
                return False
            raise ValueError(
                f"feature {record.coin}/{record.name}/{record.version} at "
                f"bar_ts {record.bar_ts} already exists with different "
                f"content (stored value={existing[0]!r}, "
                f"available_ts={existing[1]}, inputs_hash={existing[2]}; "
                f"new value={record.value!r}, "
                f"available_ts={record.available_ts}, "
                f"inputs_hash={record.inputs_hash}). The store is "
                f"append-only and never overwrites: use a distinct feature "
                f"identity (name/version) for a different configuration.")
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO feature "
            "(coin, name, version, bar_ts, available_ts, value, reason, "
            " inputs_hash, meta, recorded_ts) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (record.coin, record.name, record.version, int(record.bar_ts),
             int(record.available_ts), record.value, record.reason,
             record.inputs_hash, json.dumps(record.meta or {}, sort_keys=True,
                                            default=str),
             int(time.time())))
        self._conn.commit()
        return cur.rowcount > 0

    # ----------------------------------------------------------------- read
    def _row_to_record(self, row):
        coin, name, version, bar_ts, available_ts, value, reason, ih, meta = row
        try:
            parsed = json.loads(meta) if meta else {}
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"corrupt meta blob for {coin}/{name}/{version} at bar "
                f"{bar_ts}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                f"corrupt meta blob for {coin}/{name}/{version} at bar "
                f"{bar_ts}: not a JSON object")
        return FeatureRecord(coin=coin, name=name, version=version,
                             bar_ts=int(bar_ts), available_ts=int(available_ts),
                             value=value, reason=reason, inputs_hash=ih,
                             meta=parsed)

    def as_of(self, coin, name, version, ts, max_age_s=None):
        """The point-in-time join.

        Returns the newest record for (coin, name, version) whose
        ``available_ts`` is at or before ``ts``, or None when there is none.
        A record older than ``max_age_s`` seconds is returned with the reason
        "stale" and NO value (the caller must not treat it as fresh).
        """
        row = self._conn.execute(
            "SELECT coin, name, version, bar_ts, available_ts, value, reason, "
            "inputs_hash, meta FROM feature WHERE coin=? AND name=? AND "
            "version=? AND available_ts<=? ORDER BY available_ts DESC, "
            "bar_ts DESC, recorded_ts DESC LIMIT 1",
            (coin, name, version, int(ts))).fetchone()
        if row is None:
            return None
        rec = self._row_to_record(row)
        if max_age_s is not None:
            age = int(ts) - rec.available_ts
            if age > int(max_age_s):
                meta = dict(rec.meta)
                meta["age_s"] = age
                meta["stale_reason"] = rec.reason
                return replace(rec, value=None, reason="stale", meta=meta)
        return rec

    def latest(self, coin, name, version):
        """The newest record regardless of any as-of timestamp.

        DIAGNOSTIC ONLY: a latest value may have been computed after the
        moment a caller cares about.  Use :meth:`as_of` for joins.
        """
        row = self._conn.execute(
            "SELECT coin, name, version, bar_ts, available_ts, value, reason, "
            "inputs_hash, meta FROM feature WHERE coin=? AND name=? AND "
            "version=? ORDER BY available_ts DESC, bar_ts DESC, "
            "recorded_ts DESC LIMIT 1", (coin, name, version)).fetchone()
        return None if row is None else self._row_to_record(row)

    def history(self, coin, name, version):
        """Every stored record for the key, oldest first (diagnostics)."""
        rows = self._conn.execute(
            "SELECT coin, name, version, bar_ts, available_ts, value, reason, "
            "inputs_hash, meta FROM feature WHERE coin=? AND name=? AND "
            "version=? ORDER BY bar_ts ASC, recorded_ts ASC",
            (coin, name, version)).fetchall()
        return [self._row_to_record(r) for r in rows]

    def count(self, coin=None, name=None, version=None):
        """The number of stored rows (optionally filtered)."""
        q = "SELECT COUNT(*) FROM feature WHERE 1=1"
        args = []
        if coin is not None:
            q += " AND coin=?"
            args.append(coin)
        if name is not None:
            q += " AND name=?"
            args.append(name)
        if version is not None:
            q += " AND version=?"
            args.append(version)
        return int(self._conn.execute(q, args).fetchone()[0])

    def close(self):
        self._conn.commit()
        self._conn.close()
