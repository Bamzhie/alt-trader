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


def plan_touches(direction, stop, tp1, tp2, fut):
    """First-touch (1-based bars) of stop/TP1/TP2 over future bars.

    Direction-aware (LONG: stop on lows, targets on highs; SHORT mirrored).
    A bar touching both stop and a target counts the STOP (conservative:
    adverse fills fail first). Untouched levels return None — never
    zero-filled, so a later run with more bars can still resolve them.
    """
    touch = {"stop": None, "tp1": None, "tp2": None}
    for i, b in enumerate(fut, 1):
        if direction == "LONG":
            stop_hit = b["l"] <= stop
            t1 = b["h"] >= tp1
            t2 = b["h"] >= tp2
        else:
            stop_hit = b["h"] >= stop
            t1 = b["l"] <= tp1
            t2 = b["l"] <= tp2
        if stop_hit:
            # Terminal: the trade is over. Targets hit on EARLIER bars stay
            # recorded; targets on this or later bars never happened to a
            # stopped-out position and must not count.
            if touch["stop"] is None:
                touch["stop"] = i
            break
        if t1 and touch["tp1"] is None:
            touch["tp1"] = i
        if t2 and touch["tp2"] is None:
            touch["tp2"] = i
        if touch["tp2"] is not None:
            break
    return touch


def resolve_plans(store, symbol_map, data_dir="data/bars"):
    """Walk logged plans bar-by-bar for first touches. Returns rows written.

    Reads Store.planned() (unterminated only: no row yet, or neither stop
    nor TP2 hit). Prefers collector bars (full local history), falls back
    to REST. A run rewrites unterminated rows as more bars arrive; terminal
    rows (stop or TP2 touched) are never revisited.
    """
    from . import mexc
    rows = store.planned()
    done = 0
    rest_cache = {}
    for sid, coin, direction, price, ts, stop, tp1, tp2 in rows:
        sym = symbol_map.get(coin)
        if not sym or stop is None or tp1 is None or tp2 is None:
            continue
        try:
            col = collector.read_bars(data_dir, coin)
        except Exception:
            col = None
        fut_col = [b for b in (col or []) if b["ts"] > ts]
        # Gap fill: collection may have started AFTER the signal, leaving
        # early touches invisible to collector bars alone. If the local
        # file's earliest bar postdates the signal by more than one 5m
        # step, merge REST bars for the missing head (REST covers ~7d
        # back). Collector wins timestamp ties.
        bars = list(fut_col)
        col_starts_late = bool(col) and min(b["ts"] for b in col) > ts + 300
        if not fut_col or col_starts_late:
            if sym not in rest_cache:
                try:
                    rest_cache[sym] = mexc.klines(sym, "5m",
                                                 limit=REST_MAX_BARS)
                except Exception:
                    rest_cache[sym] = None
            rest_fut = [b for b in (rest_cache[sym] or []) if b["ts"] > ts]
            if rest_fut:
                seen = {b["ts"] for b in bars}
                bars = sorted(bars + [b for b in rest_fut
                                      if b["ts"] not in seen],
                              key=lambda b: b["ts"])
        if not bars:
            continue
        touch = plan_touches(direction, stop, tp1, tp2, bars)
        store.log_plan_outcome(
            sid, touch["stop"] is not None, touch["tp1"] is not None,
            touch["tp2"] is not None, touch["stop"], touch["tp1"],
            touch["tp2"], len(bars))
        done += 1
    return done


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
    rest_cache = {}   # sym -> bars|None: one REST fetch per coin per run,
    col_cache = {}    # coin -> bars|None: one file read per coin per run
    for sid, coin, price, direction, ts in rows:
        sym = symbol_map.get(coin)
        if not sym or price <= 0:
            continue
        for h in horizons:
            if store.has_outcome(sid, h):
                continue
            need = HORIZON_BARS[h]
            if bar_source == "rest+collector" and need <= REST_MAX_BARS:
                if sym not in rest_cache:
                    try:
                        rest_cache[sym] = mexc.klines(sym, "5m",
                                                     limit=REST_MAX_BARS)
                    except Exception:
                        rest_cache[sym] = None
                bars = rest_cache[sym]
            else:
                if coin not in col_cache:
                    try:
                        col_cache[coin] = collector.read_bars(data_dir, coin)
                    except Exception:
                        col_cache[coin] = None
                bars = col_cache[coin]
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
