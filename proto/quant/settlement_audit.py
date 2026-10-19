"""WS4 settlement-backfill audit: a per-coin outcome manifest.

Background.  ``data/funding_settlements`` was populated by
``proto.settlements.backfill`` in one run, but the report could only state
aggregate counts: ``backfill`` returns ``{coin: {new, skipped, pages,
truncated}}`` (or ``{coin, new, error}``) and that return value was never
persisted.  So the per-coin outcome -- and any per-coin API error -- was
lost.  This module rebuilds a machine-readable manifest from the archive
on disk and reconciles it, **read-only**, against the venue's own
``totalCount`` for every coin in the bar universe.

Outcome vocabulary (exactly one per bar-universe coin):

  ``complete``        archive present; the venue holds no more rows than
                      the elapsed live tail can explain, so there is no
                      evidence the backfill's page cap stopped the walk.
  ``truncated``       archive present but the venue now reports more
                      rows than the shortfall the live tail explains
                      (in practice: the walk stopped on a ``PAGE_SIZE``
                      boundary and the venue holds hundreds of rows more).
  ``missing_archive`` no settlement file for a coin in the bar universe.
  ``failed_or_error`` the reconciliation probe failed for this coin.  THE
                      ORIGINAL BACKFILL'S PER-COIN ERRORS WERE NEVER
                      RECORDED, so this column describes the probe, not
                      that run; it is ``0`` when the venue is healthy.

Live tail.  The archive is a snapshot; the venue keeps settling funding,
so a coin's ``totalCount`` at probe time can exceed what was archived by
the number of settles that fell due since.  ``expected_live_tail_rows``
estimates that from the coin's own fastest observed cycle:

    floor((observed_at - last_settle_ts) / (min(cycles) * 3600))

A shortfall within the tail is not evidence of truncation.  A shortfall
beyond it is.  Two independent signals therefore agree on the flagged
coins: an exact ``PAGE_SIZE`` row boundary **and** a shortfall the tail
cannot explain.

Pure and read-only.  ``read_coin_stats`` and ``build_manifest`` never
write.  ``probe_venue`` is the only networked step and takes an injectable
``probe`` so tests run offline; the live probe paces through the
settlement rate limiter and isolates per-coin transport errors.
"""

import csv
import gzip
import json
import math
import os
import time
from typing import Callable, Dict, Iterable, List, Optional

# ``collect_coin`` pages at 1000 rows; a truncated walk therefore always
# stops on an exact multiple of this.
PAGE_SIZE = 1000

COMPLETE = "complete"
TRUNCATED = "truncated"
MISSING = "missing_archive"
FAILED = "failed_or_error"
OUTCOMES = (COMPLETE, TRUNCATED, MISSING, FAILED)


def _settlement_path(settlements_dir: str, coin: str) -> str:
    return os.path.join(settlements_dir, f"{coin}.csv.gz")


def read_coin_stats(settlements_dir: str, coin: str) -> Optional[Dict]:
    """Per-coin archive stats, or ``None`` when the file is absent.

    ``valid_rows`` / ``unique_ts`` / ``cycles`` come from
    ``proto.settlements.read_settlements`` so the manifest counts exactly
    what cost attribution sees.  ``raw_rows`` counts every data row, so
    ``malformed_rows = raw_rows - valid_rows`` surfaces rows the read path
    silently drops (it never zero-fills).  A corrupt/unreadable archive is
    reported as a stats dict with ``error`` set rather than aborting the
    audit.
    """
    from .. import settlements as S

    path = _settlement_path(settlements_dir, coin)
    if not os.path.exists(path):
        return None

    raw_rows = 0
    read_error = None
    try:
        with gzip.open(path, "rt", newline="") as f:
            f.readline()                     # header
            for _ in f:
                raw_rows += 1
        rows = S.read_settlements(settlements_dir, coin)
    except (OSError, EOFError, UnicodeDecodeError) as e:
        return {"raw_rows": raw_rows, "valid_rows": 0, "malformed_rows": 0,
                "duplicate_rows": 0, "unique_ts": 0, "first_settle_ts": None,
                "last_settle_ts": None, "span_days": None, "cycles": [],
                "error": f"{type(e).__name__}: {e}"}

    ts = sorted(r["settle_ts"] for r in rows)
    unique = sorted(set(ts))
    cycles = sorted({r["cycle_hours"] for r in rows if r["cycle_hours"]})
    span = ((unique[-1] - unique[0]) / 86400.0) if len(unique) > 1 else None
    return {
        "raw_rows": raw_rows,
        "valid_rows": len(rows),
        "malformed_rows": raw_rows - len(rows),
        "duplicate_rows": len(ts) - len(unique),
        "unique_ts": len(unique),
        "first_settle_ts": unique[0] if unique else None,
        "last_settle_ts": unique[-1] if unique else None,
        "span_days": round(span, 6) if span is not None else None,
        "cycles": cycles,
        "error": read_error,
    }


def expected_live_tail_rows(stats: Optional[Dict],
                            observed_at: Optional[float]) -> Optional[int]:
    """Settles that could have fallen due since the archive's last row.

    Uses the coin's FASTEST observed cycle (largest plausible tail), so a
    shortfall is only called unexplained when even the most generous tail
    cannot cover it.  ``None`` when the inputs cannot answer.
    """
    if not stats or not observed_at or not stats.get("cycles"):
        return None
    last = stats.get("last_settle_ts")
    if not last:
        return None
    cycle_s = min(stats["cycles"]) * 3600.0
    if cycle_s <= 0:
        return None
    delta = float(observed_at) - float(last)
    if delta <= 0:
        return 0
    return int(math.floor(delta / cycle_s))


def venue_shortfall_rows(stats: Optional[Dict],
                         venue: Optional[Dict]) -> Optional[int]:
    """``venue totalCount - valid_rows`` (>0 means the venue holds more)."""
    v = (venue or {}).get("total_count")
    if stats is None or v is None:
        return None
    return int(v) - int(stats["valid_rows"])


def unexplained_shortfall_rows(stats: Optional[Dict], venue: Optional[Dict],
                               observed_at: Optional[float]) -> Optional[int]:
    """Shortfall the live tail cannot explain (0 when fully explained)."""
    deficit = venue_shortfall_rows(stats, venue)
    if deficit is None or deficit <= 0:
        return deficit
    tail = expected_live_tail_rows(stats, observed_at)
    if tail is None:
        return deficit
    return max(0, deficit - tail)


def page_boundary_cap(stats: Optional[Dict]) -> bool:
    """True when ``raw_rows`` sits exactly on a ``collect_coin`` page edge."""
    if not stats:
        return False
    raw = stats.get("raw_rows") or 0
    return raw >= PAGE_SIZE and raw % PAGE_SIZE == 0


def classify(stats: Optional[Dict], venue: Optional[Dict],
             observed_at: Optional[float]) -> str:
    """One outcome per coin -- see the module docstring for the vocabulary.

    * probe error                -> ``failed_or_error``
    * no archive file            -> ``missing_archive``
    * shortfall beyond the tail  -> ``truncated``
    * otherwise                  -> ``complete``

    Without venue data the page-boundary signature is the only available
    truncation signal; it is used as an UNCONFIRMED fallback.
    """
    if venue and venue.get("error"):
        return FAILED
    if stats is None:
        return MISSING
    v = (venue or {}).get("total_count")
    if v is None:
        return TRUNCATED if page_boundary_cap(stats) else COMPLETE
    shortfall = unexplained_shortfall_rows(stats, venue, observed_at)
    if shortfall is None:
        return COMPLETE
    return TRUNCATED if shortfall > 0 else COMPLETE


def build_entry(coin: str, stats: Optional[Dict], venue: Optional[Dict],
                observed_at: Optional[float], funding_present: bool) -> Dict:
    """Assemble one manifest row (keys are stable and self-describing)."""
    v = (venue or {}).get("total_count")
    shortfall = venue_shortfall_rows(stats, venue)
    unexplained = unexplained_shortfall_rows(stats, venue, observed_at)
    return {
        "coin": coin,
        "in_bar_universe": True,
        "funding_archive_present": bool(funding_present),
        "settlement_archive_present": stats is not None,
        "raw_rows": (stats or {}).get("raw_rows"),
        "valid_rows": (stats or {}).get("valid_rows"),
        "malformed_rows": (stats or {}).get("malformed_rows"),
        "duplicate_rows": (stats or {}).get("duplicate_rows"),
        "unique_ts": (stats or {}).get("unique_ts"),
        "first_settle_ts": (stats or {}).get("first_settle_ts"),
        "last_settle_ts": (stats or {}).get("last_settle_ts"),
        "span_days": (stats or {}).get("span_days"),
        "cycles": (stats or {}).get("cycles"),
        "read_error": (stats or {}).get("error"),
        "venue_symbol": (venue or {}).get("symbol"),
        "venue_total_count": v,
        "venue_total_pages": (venue or {}).get("total_pages"),
        "venue_error": (venue or {}).get("error"),
        "in_venue_tickers": (venue or {}).get("in_venue_tickers"),
        "venue_shortfall_rows": shortfall,
        "expected_live_tail_rows": expected_live_tail_rows(stats, observed_at),
        "unexplained_shortfall_rows": unexplained,
        "page_boundary_cap": page_boundary_cap(stats),
        "outcome": classify(stats, venue, observed_at),
    }


def probe_venue(coins: Iterable[str],
                probe: Callable[[str], Dict]) -> Dict[str, Dict]:
    """``{coin: probe(coin)}``; the probe owns pacing and error capture."""
    return {coin: probe(coin) for coin in coins}


def _live_probe_factory() -> Callable[[str], Dict]:
    """A probe that reads the venue once per coin, errors isolated per coin.

    One extra request caches the current ticker list; membership is
    recorded as evidence (a bar-universe coin absent from it is why the
    backfill's ticker-derived universe skipped it) -- never as a claim
    about why the venue dropped it.
    """
    from .. import mexc
    from ..settlements import SETTLEMENT_LIMITER

    tickers: Dict[str, bool] = {}

    def probe(coin: str) -> Dict:
        if not tickers:
            try:
                tickers.update({s: True for s in mexc.tickers()})
            except Exception:  # noqa: BLE001 - membership is best-effort
                pass
        sym = f"{coin}_USDT"
        out = {"symbol": sym, "total_count": None, "total_pages": None,
               "error": None, "in_venue_tickers": bool(tickers.get(sym))}
        try:
            SETTLEMENT_LIMITER.acquire()
            data = mexc.funding_history(sym, 1, 1)
            out["total_count"] = data.get("totalCount")
            out["total_pages"] = data.get("totalPage")
        except Exception as e:  # noqa: BLE001 - surface, never mask
            out["error"] = f"{type(e).__name__}: {e}"
        return out

    return probe


def _list_gz(directory: str) -> List[str]:
    if not os.path.isdir(directory):
        return []
    return sorted(p[:-7] for p in os.listdir(directory) if p.endswith(".csv.gz"))


def build_manifest(settlements_dir: str, bar_coins: Iterable[str],
                   funding_dir: Optional[str] = None,
                   venue: Optional[Dict[str, Dict]] = None,
                   observed_at: Optional[float] = None) -> Dict:
    """Full per-coin manifest for the bar universe.  Pure and read-only."""
    bar_coins = sorted(bar_coins)
    observed_at = int(time.time()) if observed_at is None else int(observed_at)
    funding = set(_list_gz(funding_dir)) if funding_dir else set()
    settlements = set(_list_gz(settlements_dir))
    venue = venue or {}

    entries = []
    for coin in bar_coins:
        stats = read_coin_stats(settlements_dir, coin)
        entries.append(build_entry(coin, stats, venue.get(coin), observed_at,
                                   coin in funding))

    outcomes = {k: 0 for k in OUTCOMES}
    for e in entries:
        outcomes[e["outcome"]] += 1

    present = [e for e in entries if e["settlement_archive_present"]]
    truncated = [e for e in entries if e["outcome"] == TRUNCATED]
    totals = {
        "raw_rows": sum(e["raw_rows"] or 0 for e in present),
        "valid_rows": sum(e["valid_rows"] or 0 for e in present),
        "malformed_rows": sum(e["malformed_rows"] or 0 for e in present),
        "duplicate_rows": sum(e["duplicate_rows"] or 0 for e in present),
        "venue_shortfall_rows": sum(e["venue_shortfall_rows"] or 0
                                    for e in present),
        "unexplained_shortfall_rows": sum(e["unexplained_shortfall_rows"] or 0
                                          for e in truncated),
    }
    return {
        "generated_at": observed_at,
        "observed_at": observed_at,
        "bar_universe_n": len(entries),
        "settlement_archive_n": len(settlements),
        "funding_archive_n": len(funding),
        "page_size": PAGE_SIZE,
        "outcomes": outcomes,
        "truncated_coins": [e["coin"] for e in truncated],
        "totals": totals,
        "settlement_dirs": {"settlements_dir": settlements_dir,
                            "funding_dir": funding_dir},
        "notes": {
            "backfill_api_errors_recorded": False,
            "backfill_api_errors_note": (
                "proto.settlements.backfill returns per-coin errors but the "
                "backfill run did not persist them; this manifest's "
                "venue_error column reflects the reconciliation probe only."),
            "failure_vs_delisting": (
                "A coin absent from the venue's current ticker list is "
                "recorded as absent evidence, not as delisted; the venue "
                "still serving its funding history is the only fact used."),
        },
        "coins": entries,
        "settlement_only_coins": sorted(settlements - set(bar_coins)),
    }


_FIELDS = ["coin", "outcome", "settlement_archive_present",
           "funding_archive_present", "raw_rows", "valid_rows",
           "malformed_rows", "duplicate_rows", "unique_ts",
           "first_settle_ts", "last_settle_ts", "span_days", "cycles",
           "venue_symbol", "venue_total_count", "venue_total_pages",
           "venue_error", "in_venue_tickers", "venue_shortfall_rows",
           "expected_live_tail_rows", "unexplained_shortfall_rows",
           "page_boundary_cap"]


def write_json(manifest: Dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)


def write_csv(manifest: Dict, path: str) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS, extrasaction="ignore")
        w.writeheader()
        for e in manifest["coins"]:
            row = dict(e)
            if isinstance(row.get("cycles"), list):
                row["cycles"] = ",".join(str(c) for c in row["cycles"])
            w.writerow(row)


def main(argv=None) -> int:
    """CLI: python3 -m proto.quant.settlement_audit [--out MANIFEST.json].

    Read-only audit of the settlement backfill against the bar universe.
    The only network step is the per-coin venue probe (disable with
    ``--no-venue``); nothing is written except ``--out`` / ``--csv``.
    """
    import argparse

    ap = argparse.ArgumentParser(
        prog="python3 -m proto.quant.settlement_audit",
        description=__doc__.split("\n")[0])
    ap.add_argument("--settlements-dir", default="data/funding_settlements")
    ap.add_argument("--bar-dir", default="data/bars")
    ap.add_argument("--funding-dir", default="data/funding")
    ap.add_argument("--out", default=None, help="write JSON manifest here")
    ap.add_argument("--csv", default=None, help="write CSV manifest here")
    ap.add_argument("--no-venue", action="store_true",
                    help="skip the venue probe (offline; truncation unconfirmed)")
    ap.add_argument("--json", action="store_true", help="print manifest to stdout")
    a = ap.parse_args(argv)

    from .bars import list_coins

    bar_coins = list_coins(a.bar_dir)
    observed_at = int(time.time())
    venue = None
    if not a.no_venue:
        venue = probe_venue(bar_coins, _live_probe_factory())
    else:
        venue = {c: {"symbol": f"{c}_USDT", "total_count": None,
                     "total_pages": None, "error": None,
                     "in_venue_tickers": None} for c in bar_coins}

    manifest = build_manifest(
        a.settlements_dir, bar_coins,
        funding_dir=a.funding_dir if os.path.isdir(a.funding_dir) else None,
        venue=venue, observed_at=observed_at)

    if a.out:
        write_json(manifest, a.out)
    if a.csv:
        write_csv(manifest, a.csv)
    if a.json:
        print(json.dumps(manifest, indent=2, default=str))
    else:
        o = manifest["outcomes"]
        print(f"bar universe: {manifest['bar_universe_n']} coins; "
              f"settlement archive: {manifest['settlement_archive_n']}")
        for k in OUTCOMES:
            print(f"  {k:<16} {o[k]}")
        if manifest["truncated_coins"]:
            print("truncated:", ", ".join(manifest["truncated_coins"]))
        print(f"unexplained shortfall rows: "
              f"{manifest['totals']['unexplained_shortfall_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
