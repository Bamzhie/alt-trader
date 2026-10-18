"""Minimum-coverage policy for WS5 evaluation eligibility.

Background.  The frozen ``Splits`` carve one chronological archive into
dev -> embargo -> validation -> embargo -> holdout.  For the default config
(``first_event_day`` 6, ``dev_end_day`` 61, ``embargo_days`` 2,
``val_len_days`` 14) the holdout begins at day **79.0** and runs to the end
of the archive.  The original study reserved a ~12.4-day holdout
(``docs/quant-research-2026-10-12.md``: archive 2026-07-13 -> 2026-10-12,
91.4 days, holdout 79-91.4).  We therefore require a ~12-day holdout for a
"full" evaluation, i.e. an archive span of about **91 days**.

This module is pure and offline: it never touches the archives except in
``audit_spans``, which only reads.  It answers three operator questions:

* ``holdout_start_day`` -- where the holdout begins for a given splits object.
* ``min_span_days``     -- the archive span a coin needs for a full split.
* ``classify_coverage`` -- full / partial / insufficient split support.

IMPORTANT subtlety.  The evaluation's ``n_days`` is the UNION span across
coins (``research.collect_events`` takes min/max over the whole archive), so
a single long coin fixes the day coordinates for every coin.  Per-coin span
does not change the split boundaries; it decides which windows a coin can
actually populate.  That is exactly what ``classify_coverage`` reports.
"""

from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from .config import DEFAULT, Splits

# Original study reserved a 12.4-day holdout (79 -> 91.4).  Rounded to 12.
DEFAULT_MIN_HOLDOUT_DAYS = 12.0

FULL = "full"
PARTIAL = "partial"
INSUFFICIENT = "insufficient"


def holdout_start_day(splits: Splits = DEFAULT.splits) -> float:
    """First day of the holdout window (default 79.0).

    Derived from the splits' own arithmetic so a config change moves all
    three boundaries together.
    """
    return (splits.dev_end_day + splits.embargo_days
            + splits.val_len_days + splits.embargo_days)


def min_span_days(splits: Splits = DEFAULT.splits,
                  min_holdout_days: float = DEFAULT_MIN_HOLDOUT_DAYS) -> float:
    """Archive span (days) required for a full dev/val/holdout run."""
    return holdout_start_day(splits) + float(min_holdout_days)


def classify_coverage(span_days,
                      splits: Splits = DEFAULT.splits,
                      min_holdout_days: float = DEFAULT_MIN_HOLDOUT_DAYS) -> str:
    """Classify a coin's archive span into split-support buckets.

    * ``full``         -- span >= ``min_span_days``: dev + val + holdout
                          with at least ``min_holdout_days`` of holdout.
    * ``partial``      -- span >= ``holdout_start_day`` but shorter than
                          ``min_span_days``: dev + val, holdout present but
                          too short to be evidence.
    * ``insufficient`` -- span < ``holdout_start_day`` (or missing/invalid):
                          cannot produce a holdout at all; dev/val-only at
                          best.

    Missing, non-numeric, or negative spans are ``insufficient`` -- never
    silently treated as zero-coverage-but-eligible.
    """
    if span_days is None:
        return INSUFFICIENT
    try:
        s = float(span_days)
    except (TypeError, ValueError):
        return INSUFFICIENT
    if s != s or s < 0.0:          # NaN or negative
        return INSUFFICIENT
    if s >= min_span_days(splits, min_holdout_days):
        return FULL
    if s >= holdout_start_day(splits):
        return PARTIAL
    return INSUFFICIENT


def summarize(spans: Mapping[str, Optional[float]],
              splits: Splits = DEFAULT.splits,
              min_holdout_days: float = DEFAULT_MIN_HOLDOUT_DAYS) -> Dict:
    """Bucket a ``{coin: span_days}`` mapping; return counts and coin lists."""
    buckets: Dict[str, List[str]] = {FULL: [], PARTIAL: [], INSUFFICIENT: []}
    for coin, sp in spans.items():
        buckets[classify_coverage(sp, splits, min_holdout_days)].append(coin)
    for v in buckets.values():
        v.sort()
    return {
        "min_span_days": min_span_days(splits, min_holdout_days),
        "holdout_start_day": holdout_start_day(splits),
        "min_holdout_days": float(min_holdout_days),
        "counts": {k: len(v) for k, v in buckets.items()},
        "coins": buckets,
        "total": len(spans),
    }


def audit_spans(data_dir: str,
                coins: Optional[Iterable[str]] = None) -> Dict[str, Optional[float]]:
    """Read a bar archive directory and return ``{coin: span_days}``.

    Uses sorted unique min/max (via ``proto.quant.bars.load_coin``), so the
    collector's batch-appended, out-of-order layout is never mis-measured as
    a short span.  A coin with no readable bars maps to ``None``.
    """
    from .bars import list_coins, load_coin

    names = list(coins) if coins is not None else list_coins(data_dir)
    out: Dict[str, Optional[float]] = {}
    for coin in names:
        bars = load_coin(data_dir, coin)
        if not bars or len(bars["ts"]) < 2:
            out[coin] = None
            continue
        ts = bars["ts"]
        out[coin] = (ts[-1] - ts[0]) / 86400.0
    return out


def main(argv=None) -> int:
    """CLI: python3 -m proto.quant.coverage --data-dir data/bars [--json].

    Prints the split-support distribution (full / partial / insufficient)
    for the bar universe under the frozen Splits. Read-only.
    """
    import argparse
    import json

    ap = argparse.ArgumentParser(prog="python3 -m proto.quant.coverage",
                                 description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default="data/bars")
    ap.add_argument("--min-holdout-days", type=float,
                    default=DEFAULT_MIN_HOLDOUT_DAYS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    spans = audit_spans(a.data_dir)
    summary = summarize(spans, min_holdout_days=a.min_holdout_days)
    summary["spans"] = spans
    if a.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        c = summary["counts"]
        print(f"bar universe: {summary['total']} coins")
        print(f"full (>= {summary['min_span_days']:.1f}d):        {c[FULL]}")
        print(f"partial (>= {summary['holdout_start_day']:.1f}d):      "
              f"{c[PARTIAL]}")
        print(f"insufficient (< {summary['holdout_start_day']:.1f}d):  "
              f"{c[INSUFFICIENT]}")
        print("insufficient coins:",
              ", ".join(summary["coins"][INSUFFICIENT]) or "(none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
