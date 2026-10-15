"""Bar-archive in-memory audit core (Task 1).

Pure stdlib. ``audit_bar_sequence`` inspects an iterable of bar mappings
(each carrying at least ``"ts"``) without sorting or mutating the rows: it
streams file order once, collecting per-ts fingerprints for duplicate
detection and out-of-order tracking. The compact set of unique timestamps is
then sorted on its own (never the rows) to compute gaps and coverage.
"""

import time

from typing import Dict, Iterable, List, Mapping, Optional


# Canonical fingerprint fields: any difference between two same-ts rows marks
# the duplicate as "conflicting".
_FP_FIELDS = ("o", "h", "l", "c", "vol", "amount")


def _fingerprint(bar: Mapping) -> tuple:
    return tuple(bar.get(f) for f in _FP_FIELDS)


def audit_bar_sequence(bars: Iterable[Mapping],
                       *, tf_sec: int = 300,
                       now: Optional[float] = None) -> Dict:
    """Audit a sequence of 5m bars for archive-quality issues.

    Parameters
    ----------
    bars : iterable of mappings
        Each mapping must carry at least ``"ts"`` (UTC seconds, int). The
        iterable is consumed exactly once, in file order; rows are never
        sorted or rewritten (read-only over the caller's iterable).
    tf_sec : int
        Expected bar spacing in seconds (default 300 = 5m).
    now : float | None
        Reference wall-clock seconds for staleness. Defaults to
        :func:`time.time`.

    Returns
    -------
    dict
        Keys: ``count``, ``first_ts``, ``last_ts``, ``invalid_rows``,
        ``duplicates``, ``out_of_order``, ``out_of_order_count``, ``gaps``,
        ``missing_count``, ``age_s``, ``stale``, ``coverage``.
    """
    if now is None:
        now = time.time()

    seen: Dict[int, List] = {}        # ts -> [count, fingerprint]
    dup_entries: List[Dict] = []      # first-repeat order
    out_of_order: List[Dict] = []
    out_of_order_count = 0
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None
    count = 0
    invalid_rows = 0
    prev_ts: Optional[int] = None

    for idx, bar in enumerate(bars):
        if not isinstance(bar, Mapping):
            invalid_rows += 1
            continue
        ts = bar.get("ts")
        if ts is None:
            invalid_rows += 1
            continue
        # Treat non-int-ish ts as invalid but keep it out of sequence math.
        if not isinstance(ts, int):
            try:
                ts = int(ts)
            except (TypeError, ValueError):
                invalid_rows += 1
                continue

        count += 1
        if first_ts is None:
            first_ts = ts
        last_ts = ts

        # Out-of-order: current ts strictly less than the previous ts in file order.
        if prev_ts is not None and ts < prev_ts:
            out_of_order_count += 1
            if len(out_of_order) < 50:
                out_of_order.append(
                    {"index": idx, "ts": ts, "prev_ts": prev_ts}
                )
        prev_ts = ts

        # Duplicate detection: per-ts fingerprint, first repeat wins.
        fp = _fingerprint(bar)
        entry = seen.get(ts)
        if entry is None:
            seen[ts] = [1, fp]
        else:
            entry[0] += 1
            if len(dup_entries) < 50:
                # conflicting if the fingerprint differs from the first row.
                existing_fp = entry[1]
                conflicting = fp != existing_fp
                dup_entries.append(
                    {"ts": ts, "occurrences": entry[0],
                     "conflicting": conflicting}
                )

    # Gaps + coverage are computed over the sorted *unique* timestamp set.
    unique_ts = sorted(seen.keys())
    gaps: List[Dict] = []
    missing_count = 0

    for i in range(1, len(unique_ts)):
        from_ts = unique_ts[i - 1]
        to_ts = unique_ts[i]
        span_s = to_ts - from_ts
        if span_s > tf_sec:
            if span_s % tf_sec == 0:
                missing = span_s // tf_sec - 1
            else:
                missing = None
            if missing is not None:
                missing_count += missing
            if len(gaps) < 50:
                gaps.append(
                    {"from_ts": from_ts, "to_ts": to_ts,
                     "span_s": span_s, "missing": missing}
                )

    stale = False
    age_s: Optional[int] = None
    if last_ts is not None:
        age_s = int(now - last_ts)
        if age_s > 2 * tf_sec:
            stale = True

    # Coverage over the unique timestamp range.
    span_s: Optional[int] = None
    expected: Optional[int] = None
    ratio: Optional[float] = None
    if first_ts is not None and last_ts is not None:
        span_s = last_ts - first_ts
        unique = len(unique_ts)
        if span_s % tf_sec == 0:
            expected = span_s // tf_sec + 1
            ratio = (unique / expected) if expected else None
    else:
        unique = 0

    return {
        "count": count,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "invalid_rows": invalid_rows,
        "duplicates": dup_entries,
        "out_of_order": out_of_order,
        "out_of_order_count": out_of_order_count,
        "gaps": gaps,
        "missing_count": missing_count,
        "age_s": age_s,
        "stale": stale,
        "coverage": {
            "first_ts": first_ts,
            "last_ts": last_ts,
            "span_s": span_s if span_s is not None else 0,
            "unique": len(unique_ts) if first_ts is not None else 0,
            "expected": expected,
            "ratio": ratio,
        },
    }
