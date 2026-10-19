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

The store is append-only: ``put`` inserts and never overwrites, so the first
write for a key wins and the history of a measurement cannot be rewritten.
Each row keeps the ``inputs_hash`` of the estimator that produced it, so a
parameter change or a measure-version bump is visible in the data.

Standard library only (hashlib, json, os, sqlite3, time).
"""
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass, replace

from .structure import PIVOT_VERSION

FEATURE_STORE_VERSION = "features-v1"

REASONS = ("missing", "stale", "insufficient")

__all__ = ["FEATURE_STORE_VERSION", "REASONS", "FeatureRecord", "FeatureStore",
           "from_estimate", "from_pivots"]


@dataclass(frozen=True)
class FeatureRecord:
    """One stored feature value.

    coin          the symbol the feature belongs to
    name          the feature name ("hurst_rs", "breadth", ...)
    version       the measure version the value was produced with
    bar_ts        the bar (or timestamp) the feature DESCRIBES
    available_ts  when the value became knowable (>= bar_ts)
    value         the measured value, or None
    reason        None when present, else "missing" | "stale" | "insufficient"
                  (an estimator's own reason, e.g. "degenerate-zero-variance",
                  may be passed through for diagnosis)
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
    so the diagnosis survives without breaking the documented enum.  The
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


def from_pivots(coin, name, pivots, available_ts, meta=None) -> list[FeatureRecord]:
    """One :class:`FeatureRecord` per confirmed pivot.

    The records carry the PIVOT_VERSION, so a stored pivot is always
    attributable to the rule that produced it.  ``bar_ts`` is the pivot's own
    timestamp and ``available_ts`` the instant the pivot became knowable
    (``Pivot.confirmed_ts``); the caller passes the as-of instant it wants to
    record the pivot under.
    """
    out = []
    for p in pivots or ():
        m = dict(meta or {})
        m.update({"kind": p.kind, "leg_bars": p.leg_bars, "size": p.size,
                  "pivot_index": p.index, "pivot_version": PIVOT_VERSION,
                  "pivot_confirmed_ts": p.confirmed_ts})
        h = hashlib.sha256()
        h.update(f"{coin}|{name}|{PIVOT_VERSION}|{p.index}|{p.ts}|".encode())
        h.update(repr(float(p.price)).encode())
        out.append(FeatureRecord(coin=coin, name=name, version=PIVOT_VERSION,
                                  bar_ts=int(p.ts), available_ts=int(available_ts),
                                  value=float(p.price), reason=None,
                                  inputs_hash=h.hexdigest()[:16], meta=m))
    return out


class FeatureStore:
    """Append-only SQLite feature store (one file, no global state).

    The table is ``feature(coin, name, version, bar_ts, available_ts, value,
    reason, inputs_hash, meta, recorded_ts)`` with
    ``UNIQUE(coin, name, version, bar_ts)``: the key is the feature identity
    plus the bar it describes.

    The connection is opened the way the rest of the house does it (see
    ``proto/quant/qstore.py``): a 5 s busy timeout, ``check_same_thread``
    disabled and WAL journaling, so the store can be used from the service's
    worker threads.
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
        inserted, False when an identical key already existed (the first
        write wins).

        A record that claims to be knowable BEFORE the bar it describes
        (``available_ts < bar_ts``) is refused with a ValueError: it would
        let a value be joined to a moment it could not have existed.  A
        reason outside :data:`REASONS` is refused for the same reason (the
        stored vocabulary is a contract).
        """
        if int(record.available_ts) < int(record.bar_ts):
            raise ValueError(
                f"available_ts {record.available_ts} is before bar_ts "
                f"{record.bar_ts}: a value cannot be knowable before its bar")
        if record.reason is not None and record.reason not in REASONS:
            raise ValueError(
                f"reason {record.reason!r} is not one of {REASONS}")
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
