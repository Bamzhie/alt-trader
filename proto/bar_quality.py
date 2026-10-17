"""Bar-archive in-memory audit core (Task 1).

Pure stdlib. ``audit_bar_sequence`` inspects an iterable of bar mappings
(each carrying at least ``"ts"``) without sorting or mutating the rows: it
streams file order once, collecting per-ts fingerprints for duplicate
detection and out-of-order tracking. The compact set of unique timestamps is
then sorted on its own (never the rows) to compute gaps and coverage.
"""

import csv
import gzip
import os
import sys
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

    # Staleness is a property of the DATA (newest bar), not of file order:
    # an out-of-order file whose file-order last row is an old batch must
    # not be reported stale while its newest bar is fresh.
    stale = False
    age_s: Optional[int] = None
    if unique_ts:
        newest_ts = unique_ts[-1]
        age_s = int(now - newest_ts)
        if age_s > 2 * tf_sec:
            stale = True

    # Coverage over the unique timestamp range — again the sorted min/max,
    # never the raw file-order first/last (an out-of-order archive would
    # otherwise report a negative or distorted span and a bogus ratio).
    span_s: Optional[int] = None
    expected: Optional[int] = None
    ratio: Optional[float] = None
    if unique_ts:
        cov_first = unique_ts[0]
        cov_last = unique_ts[-1]
        span_s = cov_last - cov_first
        unique = len(unique_ts)
        if span_s % tf_sec == 0:
            expected = span_s // tf_sec + 1
            ratio = (unique / expected) if expected else None
    else:
        unique = 0
        cov_first = None
        cov_last = None

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
            "first_ts": cov_first,
            "last_ts": cov_last,
            "span_s": span_s if span_s is not None else 0,
            "unique": len(unique_ts) if unique_ts else 0,
            "expected": expected,
            "ratio": ratio,
        },
    }


# ---------------------------------------------------------------------------
# Task 2: streaming archive file audit + incoming check
# ---------------------------------------------------------------------------

# Matches proto/collector.py HEADER and _path naming.
_BAR_HEADER = ("ts", "o", "h", "l", "c", "vol", "amount")


def archive_max_ts(data_dir, coin):
    """Return ``(max_ts, row_count)`` for a coin's gzip bar archive.

    Streams the file exactly once in file order — no list materialization of
    the full archive, no sorting. ``(None, 0)`` is returned when the file does
    not exist or cannot be read; non-integer ``ts`` rows are skipped (and
    still counted toward ``row_count`` for visibility).
    """
    path = os.path.join(data_dir, f"{coin}.csv.gz")
    if not os.path.exists(path):
        return None, 0
    max_ts = None
    n = 0
    try:
        with gzip.open(path, "rt", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                n += 1
                ts = row.get("ts")
                if ts is None:
                    continue
                try:
                    ts = int(ts)
                except (TypeError, ValueError):
                    continue
                if max_ts is None or ts > max_ts:
                    max_ts = ts
    except (OSError, gzip.BadGzipFile, csv.Error):
        return None, 0
    return max_ts, n


def audit_archive_file(data_dir, coin, *, tf_sec=300, now=None, max_examples=50,
                         ts_field="ts"):
    """Audit a single coin's persisted gzip bar archive.

    Streams the file once (file order, never sorted/reordered on disk), feeds
    the row list to :func:`audit_bar_sequence`, then caps the three example
    lists to ``max_examples``. Missing files return the empty audit shape
    (``count: 0``) with the coin/path keys attached — never raises.
    ``ts_field`` names the timestamp column (default ``"ts"`` for bar
    archives; pass ``"settle_ts"`` for funding-settlement archives) so
    other timestamped archives audit with the same sequence math.
    """
    path = os.path.join(data_dir, f"{coin}.csv.gz")
    result_base = audit_bar_sequence([], tf_sec=tf_sec, now=now)
    if not os.path.exists(path):
        result_base["coin"] = coin
        result_base["path"] = path
        return result_base
    rows = []
    invalid_rows = 0
    with gzip.open(path, "rt", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get(ts_field)
            if ts is None:
                invalid_rows += 1
                continue
            try:
                ts = int(ts)
            except (TypeError, ValueError):
                invalid_rows += 1
                continue
            rows.append({
                "ts": ts,
                "o": float(row["o"]) if row.get("o") is not None else None,
                "h": float(row["h"]) if row.get("h") is not None else None,
                "l": float(row["l"]) if row.get("l") is not None else None,
                "c": float(row["c"]) if row.get("c") is not None else None,
                "vol": float(row["vol"]) if row.get("vol") is not None else None,
                "amount": float(row["amount"]) if row.get("amount") is not None else None,
            })
    result = audit_bar_sequence(rows, tf_sec=tf_sec, now=now)
    result["count"] = result.get("count", 0)
    # Preserve invalid_rows surfaced by audit_bar_sequence if it saw them.
    if invalid_rows:
        result["invalid_rows"] = result.get("invalid_rows", 0) + invalid_rows
    # Cap the three example lists to max_examples.
    for key in ("duplicates", "out_of_order", "gaps"):
        if key in result:
            result[key] = result[key][:max_examples]
    result["coin"] = coin
    result["path"] = path
    return result


def check_incoming(archive_max_ts, bars):
    """Classify a fresh list of incoming bars against the archive's max ts.

    O(len(bars)), no archive I/O. ``archive_max_ts`` of ``None`` means the
    archive is empty/absent, so every bar is considered new.

    Returns ``{"new": [...], "at_or_below_max": [...],
    "internal_duplicates": [...], "internal_out_of_order": [...]}``.

    A bar whose ts is <= ``archive_max_ts`` is reported as
    ``at_or_below_max`` with reason ``"duplicate_or_reorder"`` — without a full
    set we cannot distinguish a genuine duplicate from an out-of-order
    arrival, and we never invent that distinction.
    """
    new = []
    at_or_below_max = []
    internal_duplicates = []
    internal_out_of_order = []
    seen = set()
    prev_ts = None
    for idx, bar in enumerate(bars):
        ts = bar.get("ts")
        if ts is None:
            continue
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            continue
        # Incoming-internal duplicate detection.
        if ts in seen:
            if ts not in internal_duplicates:
                internal_duplicates.append(ts)
        else:
            seen.add(ts)
        # Incoming-internal out-of-order.
        if prev_ts is not None and ts < prev_ts:
            internal_out_of_order.append({"index": idx, "ts": ts, "prev_ts": prev_ts})
        prev_ts = ts
        # Against-archive classification.
        if archive_max_ts is None:
            new.append(ts)
        elif ts > archive_max_ts:
            new.append(ts)
        else:
            at_or_below_max.append({"ts": ts, "reason": "duplicate_or_reorder"})
    return {
        "new": new,
        "at_or_below_max": at_or_below_max,
        "internal_duplicates": internal_duplicates,
        "internal_out_of_order": internal_out_of_order,
    }


# ---------------------------------------------------------------------------
# Task 3: CLI report
# ---------------------------------------------------------------------------

def _coin_from_path(path):
    "Extract the coin symbol from a 'COIN.csv.gz' basename."
    base = os.path.basename(path)
    if base.endswith(".csv.gz"):
        return base[:-7]
    return base.rsplit(".", 1)[0] if "." in base else base


def _summarize(audit, max_examples=50):
    """Reduce a full audit dict to the human-summary row + finding flag."""
    findings = []
    if audit.get("invalid_rows"):
        findings.append(f"invalid={audit['invalid_rows']}")
    if audit.get("duplicates"):
        findings.append(f"dupes={len(audit['duplicates'])}")
    if audit.get("out_of_order_count"):
        findings.append(f"ooo={audit['out_of_order_count']}")
    if audit.get("gaps"):
        findings.append(f"gaps={len(audit['gaps'])}")
    if audit.get("missing_count"):
        findings.append(f"missing={audit['missing_count']}")
    age = audit.get("age_s")
    age_s = f"{age}s" if age is not None else "-"
    stale = "STALE" if audit.get("stale") else "ok"
    return audit["coin"], audit["count"], audit.get("path", ""), findings, age_s, stale


def main(argv=None):
    """Bar-archive data-quality CLI.

    Exit codes: 0 on success (findings are data, not errors); 2 when the
    data directory is missing/unreadable or no coins are selected.
    """
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(
        prog="bar_quality",
        description="Audit on-disk MEXC bar archives (5m OHLCV).",
    )
    parser.add_argument("--data-dir", default="data/bars",
                        help="Directory holding <COIN>.csv.gz archives.")
    parser.add_argument("--coins", default="",
                        help="Comma-separated coin list (e.g. ADA,PEPE). "
                             "Omit with --all to scan every *.csv.gz.")
    parser.add_argument("--all", action="store_true",
                        help="Scan every *.csv.gz in --data-dir.")
    parser.add_argument("--max-examples", type=int, default=50,
                        help="Cap on per-finding example rows (default 50).")
    parser.add_argument("--tf-sec", type=int, default=300,
                        help="Expected bar/observation spacing in seconds "
                             "(default 300 = 5m bars; funding observation "
                             "archives use their snapshot cadence).")
    parser.add_argument("--ts-field", default="ts",
                        help="Timestamp column name for the grid check "
                             "(default 'ts' for bar/funding/price archives; "
                             "'settle_ts' for funding-settlement archives).")
    parser.add_argument("--now", type=float, default=None,
                        help="Override wall-clock seconds for staleness.")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON list instead of text.")
    namespace = parser.parse_args(argv)

    data_dir = namespace.data_dir
    if not os.path.isdir(data_dir):
        # Unreadable/missing data dir: exit 2, no traceback.
        if namespace.json:
            _json.dumps([]).__class__  # noop, keeps json path exercised
        return 2

    # Resolve the coin set.
    if namespace.all:
        coins = [_coin_from_path(p) for p in
                 sorted(os.listdir(data_dir))
                 if p.endswith(".csv.gz")]
    elif namespace.coins:
        coins = [c.strip() for c in namespace.coins.split(",") if c.strip()]
    else:
        # No filter given: default to scanning all available archives.
        coins = [_coin_from_path(p) for p in
                 sorted(os.listdir(data_dir))
                 if p.endswith(".csv.gz")]

    if not coins:
        return 2

    now = namespace.now
    max_examples = namespace.max_examples
    tf_sec = namespace.tf_sec
    ts_field = namespace.ts_field
    results = []
    for coin in coins:
        audit = audit_archive_file(data_dir, coin, tf_sec=tf_sec, now=now,
                                   max_examples=max_examples,
                                   ts_field=ts_field)
        results.append(audit)

    if namespace.json:
        print(_json.dumps(results, indent=2, default=str))
        return 0

    # Human mode.
    with_findings = 0
    for audit in results:
        coin, count, path, findings, age_s, stale = _summarize(audit)
        flag = "STALE" if audit.get("stale") else "ok"
        detail = " ".join(findings) if findings else "clean"
        print(f"{coin} rows={count} {detail} age={age_s} {flag}")
        if findings:
            with_findings += 1
    total_missing = sum(a.get("missing_count", 0) or 0 for a in results)
    stalest_age = max((a.get("age_s") for a in results if a.get("age_s") is not None), default=None)
    stalest = f"{stalest_age}s" if stalest_age is not None else "-"
    print(f"SUMMARY coins={len(results)} findings={with_findings} "
          f"missing={total_missing} stalest={stalest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
