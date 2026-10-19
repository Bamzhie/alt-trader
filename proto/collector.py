"""5m forward bar collector. Starts the Tier-2 validation clock.

Append contract (unchanged): bars are appended to ``<data_dir>/<coin>.csv.gz``
and a bar whose timestamp is already archived is never written twice.

Faster dedupe (WS-F): the archive used to be re-read in full on EVERY append
(``read_bars`` parses the whole gzip file - ~28k rows per coin per cycle).
A small persisted index (``<data_dir>/.bar_index.json``) remembers, per
coin, the archive's ``(size, mtime_ns)``, its row count and its timestamp
range.  When the file on disk still matches that record AND the archive is
perfectly contiguous, the recorded ``max_ts`` is the exact dedupe cutoff
and the archive is not read at all.  Anything inconsistent (a rewritten
file, a gap, an off-grid batch) falls back to the full read, so a stale or
wrong index can never lose a bar.

The index is only an accelerator: it never decides WHAT is stored, only
whether the archive can be skipped.
"""
import csv, gzip, json, os, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor

HEADER = ["ts","o","h","l","c","vol","amount"]
COLLECT_WORKERS = 8
BAR_S = 300
INDEX_FILE = ".bar_index.json"

# Depth of the current collect batch: while a batch is running the index is
# flushed once at the end instead of once per coin.
_BATCH_DEPTH = 0
_BATCH_LOCK = threading.Lock()
_INDEX_CACHE = {}
_INDEX_CACHE_LOCK = threading.Lock()


def _path(data_dir, coin):
    return os.path.join(data_dir, f"{coin}.csv.gz")

def read_bars(data_dir, coin):
    p = _path(data_dir, coin)
    if not os.path.exists(p):
        return []
    out = []
    with gzip.open(p, "rt", newline="") as f:
        for r in csv.DictReader(f):
            out.append({"ts": int(r["ts"]), "o": float(r["o"]), "h": float(r["h"]),
                        "l": float(r["l"]), "c": float(r["c"]),
                        "vol": float(r["vol"]), "amount": float(r["amount"])})
    # Sort by timestamp to ensure chronological order - necessary because
    # batches are appended in sorted order but a sorted batch can be appended
    # after newer timestamps already exist in the archive.
    out.sort(key=lambda b: b["ts"])
    return out


def index_path(data_dir):
    """Path of the bar index that belongs to *data_dir*."""
    return os.path.join(data_dir, INDEX_FILE)


class BarIndex:
    """Per-coin archive fingerprint: coin -> {max_ts, min_ts, size, ...}.

    ``rows``/``min_ts``/``max_ts`` describe the ARCHIVE as it is on disk;
    ``contiguous`` says whether the archive is a gap-free run of 5m bars
    (only then may ``max_ts`` be used as a dedupe cutoff).
    """

    def __init__(self, path, now_fn=time.time):
        self.path = path
        self.now_fn = now_fn
        self.entries = {}
        self.dirty = False
        self._lock = threading.Lock()
        self.load()

    # -- persistence ----------------------------------------------------
    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return self.entries
        if isinstance(data, dict):
            for coin, entry in data.items():
                if isinstance(entry, dict):
                    self.entries[coin] = entry
        return self.entries

    def flush(self):
        """Write the index atomically (temp file + replace)."""
        if not self.dirty:
            return None
        d = os.path.dirname(os.path.abspath(self.path)) or "."
        try:
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-barindex-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.entries, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            self.dirty = False
            return self.path
        except OSError:
            return None

    # -- lookups --------------------------------------------------------
    def get(self, coin):
        with self._lock:
            entry = self.entries.get(coin)
            return dict(entry) if isinstance(entry, dict) else None

    def record(self, coin, entry):
        with self._lock:
            self.entries[coin] = entry
            self.dirty = True

    def usable(self, coin, size, mtime_ns):
        """The index entry for *coin* if it matches the file on disk.

        Returns ``None`` when the file changed (the caller must re-read).
        """
        entry = self.get(coin)
        if not entry:
            return None
        if entry.get("size") != size or entry.get("mtime_ns") != mtime_ns:
            return None
        return entry


def get_index(data_dir):
    """The (cached) bar index for *data_dir*."""
    path = index_path(data_dir)
    with _INDEX_CACHE_LOCK:
        idx = _INDEX_CACHE.get(path)
        if idx is None:
            idx = BarIndex(path)
            _INDEX_CACHE[path] = idx
        return idx


def _archive_entry(coin, path, rows, min_ts, max_ts):
    """Build the index entry for an archive described by its row count."""
    contiguous = False
    if rows and max_ts is not None and min_ts is not None:
        span = max_ts - min_ts
        contiguous = (span % BAR_S == 0
                      and span // BAR_S + 1 == rows)
    try:
        st = os.stat(path)
        size, mtime_ns = st.st_size, st.st_mtime_ns
    except OSError:
        size, mtime_ns = None, None
    return {"max_ts": max_ts, "min_ts": min_ts, "size": size,
            "mtime_ns": mtime_ns, "rows": rows, "contiguous": contiguous}


def _dedupe_cutoff(index, coin, path, bars):
    """The exact dedupe cutoff for *bars*, or None when it cannot be known.

    Returns ``(cutoff_lo, cutoff_hi)``: an incoming bar with a timestamp
    inside that closed range is already archived.  ``None`` means "read the
    archive" (the caller falls back to the full read).
    """
    try:
        st = os.stat(path)
    except OSError:
        return None
    entry = index.usable(coin, st.st_size, st.st_mtime_ns)
    if not entry or not entry.get("contiguous"):
        return None
    lo = entry.get("min_ts")
    hi = entry.get("max_ts")
    if not isinstance(lo, int) or not isinstance(hi, int):
        return None
    for b in bars:
        ts = b.get("ts")
        if not isinstance(ts, int):
            return None
        # Off-grid or older-than-the-archive bars need the real row set.
        if ts < lo or (ts - lo) % BAR_S != 0:
            return None
    return (lo, hi)


def append_bars(data_dir, coin, bars, index=None):
    """Append *bars* for *coin*, skipping the ones already archived.

    Returns the number of NEW rows written.  With a matching bar index the
    archive is not read at all; otherwise the full archive is read and the
    exact row set decides (unchanged behaviour).
    """
    os.makedirs(data_dir, exist_ok=True)
    index = index if index is not None else get_index(data_dir)
    p = _path(data_dir, coin)
    cutoff = _dedupe_cutoff(index, coin, p, bars)
    if cutoff is not None:
        lo, hi = cutoff
        new = [b for b in sorted(bars, key=lambda b: b["ts"])
               if not (lo <= b["ts"] <= hi)]
        old_rows = None
    else:
        rows = read_bars(data_dir, coin)
        seen = {b["ts"] for b in rows}
        new = [b for b in sorted(bars, key=lambda b: b["ts"])
               if b["ts"] not in seen]
        old_rows = rows
    if not new:
        return 0
    exists = os.path.exists(p)
    with gzip.open(p, "at", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        if not exists:
            w.writeheader()
        for b in new:
            w.writerow({k: b[k] for k in HEADER})
    # Refresh the index for this coin.
    if cutoff is not None:
        prev = index.get(coin) or {}
        rows_total = int(prev.get("rows") or 0) + len(new)
        min_ts = prev.get("min_ts")
        max_ts = prev.get("max_ts")
        for b in new:
            ts = b["ts"]
            if isinstance(min_ts, int) and ts < min_ts:
                min_ts = ts
            if isinstance(max_ts, int) and ts > max_ts:
                max_ts = ts
        index.record(coin, _archive_entry(coin, p, rows_total, min_ts, max_ts))
    else:
        all_ts = sorted({b["ts"] for b in (old_rows or [])}
                        | {b["ts"] for b in new})
        index.record(coin, _archive_entry(
            coin, p, len(all_ts),
            all_ts[0] if all_ts else None,
            all_ts[-1] if all_ts else None))
    with _BATCH_LOCK:
        depth = _BATCH_DEPTH
    if depth == 0:
        index.flush()
    return len(new)


class CollectCounts(dict):
    """``{coin: new_rows}`` plus the per-coin errors of the same cycle.

    A dict subclass: every existing consumer keeps working unchanged, while
    the daemon can tell a partial cycle (some coins failed) from a total
    failure (every coin failed) without guessing.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.errors = {}
        self.stopped = False

    @property
    def attempted(self):
        return len(self) + len(self.errors)


def collect_full_universe(data_dir="data/bars", limit_per_coin=200,
                          max_workers=COLLECT_WORKERS, stop_event=None):
    """Fetch 5m bars for EVERY universe coin, regardless of scan rotation.

    build_universe(stake=inf, budget=None) is uncapped (spec SS5: the
    collector covers the full universe, not the 150-coin scan rotation), so a
    coin that leaves rotation keeps accumulating bars for outcome resolution.

    One bad coin never fails the cycle: its error is counted in
    ``counts.errors`` and every other coin still lands.  ``stop_event``
    (WS-F) stops the cycle between coins so a SIGTERM drains promptly.
    """
    from .scan import build_universe
    from . import mexc
    uni = build_universe(float("inf"), budget=None)
    index = get_index(data_dir)
    counts = CollectCounts()

    def collect_one(item):
        sym, coin = item
        if stop_event is not None and stop_event.is_set():
            return (coin, None, "stopped")
        try:
            # Share the scan/measurement request cap so collector workers
            # cannot create an unbounded venue burst.
            from .scan import LIMITER
            LIMITER.acquire()
            bars = mexc.klines(sym, "5m", limit=limit_per_coin)
        except Exception as e:
            return (coin, None, f"{type(e).__name__}: {e}")
        try:
            return (coin, append_bars(data_dir, coin, bars, index=index), None)
        except Exception as e:
            return (coin, None, f"{type(e).__name__}: {e}")

    global _BATCH_DEPTH
    with _BATCH_LOCK:
        _BATCH_DEPTH += 1
    try:
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
            for coin, count, error in pool.map(collect_one, uni):
                if error == "stopped":
                    counts.stopped = True
                    continue
                if error is not None:
                    counts.errors[coin] = error
                    continue
                counts[coin] = count
    finally:
        with _BATCH_LOCK:
            _BATCH_DEPTH -= 1
        index.flush()
    return counts
