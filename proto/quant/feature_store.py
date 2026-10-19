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

Standard library only (json, os, sqlite3, time).
"""
import json
import os
import sqlite3
import time
from dataclasses import dataclass, replace

FEATURE_STORE_VERSION = "features-v1"

REASONS = ("missing", "stale", "insufficient")


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

    The estimate's reason is carried over ("insufficient-sample" becomes
    "insufficient"); a missing reason with a missing value becomes
    "missing".  The estimator's parameters, sample size and uncertainty go
    into ``meta`` so the record is self-describing.
    """
    reason = estimate.reason
    if estimate.value is None:
        if reason is None:
            reason = "missing"
        elif "insufficient" in reason:
            reason = "insufficient"
    else:
        reason = None
    meta = dict(estimate.params or {})
    meta["n"] = estimate.n
    if estimate.uncertainty is not None:
        meta["uncertainty"] = estimate.uncertainty
    meta["estimator_version"] = estimate.version
    return FeatureRecord(coin=coin, name=name, version=estimate.version,
                         bar_ts=int(bar_ts), available_ts=int(available_ts),
                         value=estimate.value, reason=reason,
                         inputs_hash=estimate.inputs_hash, meta=meta)


class FeatureStore:
    """Append-only SQLite feature store (one file, no global state).

    The table is ``feature(coin, name, version, bar_ts, available_ts, value,
    reason, inputs_hash, meta, recorded_ts)`` with
    ``UNIQUE(coin, name, version, bar_ts)``: the key is the feature identity
    plus the bar it describes.
    """

    def __init__(self, path):
        self.path = path
        if path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path)
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
        self._conn.commit()

    # ---------------------------------------------------------------- write
    def put(self, record) -> bool:
        """Insert a record; never overwrites.  Returns True when the row was
        inserted, False when an identical key already existed (the first
        write wins)."""
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
        except (TypeError, ValueError):
            parsed = {}
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
