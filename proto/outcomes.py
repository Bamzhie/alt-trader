"""Forward outcome resolution. Pure + I/O split for testability."""
HORIZON_BARS = {"1h": 12, "4h": 48, "24h": 288, "7d": 2016}  # 5m bars

def signed_return(entry, later, direction):
    if entry <= 0:
        return 0.0
    r = (later - entry) / entry * 100.0
    return r if direction == "LONG" else -r

def resolve_pending(store, symbol_map, horizons=("1h", "4h", "24h", "7d")):
    from . import mexc
    import time
    rows = store.pending_outcomes()
    done = 0
    for sid, coin, price, direction, ts in rows:
        sym = symbol_map.get(coin)
        if not sym or price <= 0:
            continue
        try:
            bars = mexc.klines(sym, "5m", limit=2000)
        except Exception:
            continue
        fut = [b for b in bars if b["ts"] > ts]
        if not fut:
            continue
        for h in horizons:
            need = HORIZON_BARS[h]
            if len(fut) < need:
                continue
            if store.has_outcome(sid, h):
                continue
            window = fut[:need]
            rets = [signed_return(price, b["c"], direction) for b in window]
            store.log_outcome(sid, h, rets[-1], max(rets), min(rets))
            done += 1
    return done
