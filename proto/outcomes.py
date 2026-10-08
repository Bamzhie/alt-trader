"""Forward outcome resolution. Pure + I/O split for testability.

Bar mapping (spec SS5 "Outcome timing", binding):
  fut        = 5m bars with ts > signal.ts; fut[0] is the entry bar
  horizon h  = signed return from signal price to close of fut[need-1]
  excursions = max/min signed returns over fut[:need]
  fewer than `need` future bars -> horizon stays unresolved (never zero-filled)

Bar sources (`bar_source`):
  "rest+collector" (default) - horizons that fit the MEXC klines cap come from
    REST; 7d (2016 bars > 2000 cap) reads collector files ONLY, never REST.
  "collector" - every horizon reads collector files (offline / REST degraded).

The resolver takes the venue symbol map, never the scan rotation: a coin that
left rotation still resolves from its collector bars (spec SS5 coverage).
"""
from . import collector

HORIZON_BARS = {"1h": 12, "4h": 48, "24h": 288, "7d": 2016}  # 5m bars
REST_MAX_BARS = 2000  # MEXC klines hard cap: 7d (2016 bars) never fits
_MISSING = object()


def signed_return(entry, later, direction):
    if entry <= 0:
        return 0.0
    r = (later - entry) / entry * 100.0
    return r if direction == "LONG" else -r


def resolve_pending(store, symbol_map, horizons=("1h", "4h", "24h", "7d"),
                    bar_source="rest+collector", data_dir="data/bars"):
    """Resolve pending signals into outcome rows. Returns rows written.

    symbol_map is {coin: symbol} for the venue as a whole - not the scan
    rotation - so rotated-out coins resolve too, from collector bars.
    """
    from . import mexc
    if bar_source not in ("rest+collector", "collector"):
        raise ValueError(f"unknown bar_source: {bar_source!r}")
    rows = store.pending_outcomes()
    done = 0
    for sid, coin, price, direction, ts in rows:
        sym = symbol_map.get(coin)
        if not sym or price <= 0:
            continue
        rest_bars = _MISSING  # REST fetched at most once per signal
        col_bars = _MISSING   # collector files read at most once per signal
        for h in horizons:
            if store.has_outcome(sid, h):
                continue
            need = HORIZON_BARS[h]
            if bar_source == "rest+collector" and need <= REST_MAX_BARS:
                if rest_bars is _MISSING:
                    try:
                        rest_bars = mexc.klines(sym, "5m", limit=REST_MAX_BARS)
                    except Exception:
                        rest_bars = None
                bars = rest_bars
            else:
                if col_bars is _MISSING:
                    try:
                        col_bars = collector.read_bars(data_dir, coin)
                    except Exception:
                        col_bars = None
                bars = col_bars
            if not bars:
                continue
            fut = [b for b in bars if b["ts"] > ts]
            if len(fut) < need:
                continue
            window = fut[:need]
            rets = [signed_return(price, b["c"], direction) for b in window]
            store.log_outcome(sid, h, rets[-1], max(rets), min(rets))
            done += 1
    return done
