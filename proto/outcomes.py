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
import math

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


# ==========================================================================
# Episode plan resolver (Task 4) — separate from the legacy resolver above.
#
# The legacy functions (plan_touches, resolve_plans, signed_return,
# resolve_pending) are untouched: they read plan_log/plan_outcome/outcome_log
# and stay frozen for the measurement week. Everything below is additive and
# reads only the episode_* tables.
#
# Determinism rules (design spec 8 and 9), all recomputable from bars:
#   - a closed 5m slot is complete only with exactly one valid bar;
#   - only fully closed bars are eligible (bar close <= now);
#   - entry: open_ts > T0 and open_ts + 300 <= T0 + 3600;
#   - LONG fills at entry_high when the low reaches it, SHORT at entry_low
#     when the high reaches it, so a gap through the band still fills at the
#     ADVERSE edge, and the fill precedes any stop on that bar;
#   - the fill timestamp is the fill bar's CLOSE;
#   - a fill-bar stop is terminal and target touches on it are ignored;
#   - later bars are stop-first; TP1 alone is never terminal; TP2 is
#     terminal; expiry is the bar closing at fill_ts + 288*300;
#   - a missing slot stays recoverable for six days, then the affected
#     result becomes UNAVAILABLE. Never zero-filled.
# ==========================================================================

BAR_SECONDS = 300
ENTRY_VALIDITY_S = 60 * 60        # entry validity window: 60 minutes
HOLD_HORIZON_BARS = 288           # 24 hours of 5m bars
RECOVERABLE_S = 6 * 24 * 3600     # REST backfill reach: six days

EPISODE_HORIZONS = ("1h", "4h", "24h", "7d")
EPISODE_HORIZON_BARS = {"1h": 12, "4h": 48, "24h": 288, "7d": 2016}


def bar_close_ts(bar):
    """Close timestamp of a 5m bar = open_ts + 300."""
    ts = bar.get("open_ts", bar.get("ts"))
    if ts is None:
        raise ValueError("bar has no open timestamp")
    return int(ts) + BAR_SECONDS


def is_valid_bar(bar, *, now):
    """A bar is usable only once it has fully CLOSED.

    A bar the venue may still revise (close in the future relative to `now`)
    never decides a fill, a stop, a target or a horizon.
    """
    try:
        o, h, l, c = (float(bar[k]) for k in ("o", "h", "l", "c"))
        finite = all(math.isfinite(v) for v in (o, h, l, c))
        closes = bar_close_ts(bar) <= int(now)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return (finite and l <= min(o, c) <= max(o, c) <= h
            and closes)


def slot_recoverable(slot_ts, *, now):
    """A missing slot is recoverable while it is younger than six days."""
    return (int(now) - int(slot_ts)) <= RECOVERABLE_S


def entry_slots(t0, validity_s=ENTRY_VALIDITY_S):
    """Every eligible entry bar open_ts for an episode starting at T0.

    Spec 8.1: open_ts > T0 and open_ts + 300 <= T0 + validity. A bar must
    lie fully inside the validity window.
    """
    t0 = int(t0)
    end = t0 + int(validity_s)
    first_slot = (t0 // BAR_SECONDS + 1) * BAR_SECONDS
    return [ts for ts in range(first_slot, end + 1, BAR_SECONDS)
            if ts + BAR_SECONDS <= end]


def entry_slot_ok(open_ts, t0, validity_s=ENTRY_VALIDITY_S):
    """Whether one slot open_ts is inside the entry validity window."""
    open_ts, t0 = int(open_ts), int(t0)
    return open_ts > t0 and open_ts + BAR_SECONDS <= t0 + int(validity_s)


def index_bars(bars):
    """Map open_ts -> the bars for that slot.

    Duplicate bars for one slot are ambiguous (the venue may have revised
    one), so callers must treat a slot with more than one bar as NOT
    complete and never resolve anything from it. `complete_slots` and the
    resolvers above do exactly that.
    """
    by_slot = {}
    for b in bars:
        # The collector's stable on-disk contract calls this field `ts`;
        # resolver helpers use the more explicit `open_ts` name.
        if "open_ts" not in b and "ts" in b:
            b = {**b, "open_ts": b["ts"]}
        try:
            ts = int(b["open_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        # Invalid OHLC must not count toward a complete slot or touch a level.
        try:
            o, h, l, c = (float(b[k]) for k in ("o", "h", "l", "c"))
            if (not all(math.isfinite(v) for v in (o, h, l, c))
                    or not l <= min(o, c) <= max(o, c) <= h):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        b = {**b, "o": o, "h": h, "l": l, "c": c}
        by_slot.setdefault(ts, []).append(b)
    return {ts: blist[0] for ts, blist in by_slot.items() if len(blist) == 1}


def complete_slots(bars, slots):
    """Slots that are complete: exactly one (closed) valid bar each.

    `slots` is the ordered list of slot open_ts values to check. A slot with
    no bar, or with more than one, is absent from the result.
    """
    by_slot = index_bars(bars)
    return {ts for ts in slots if ts in by_slot}


def _signed_pct(entry, later, direction):
    if entry <= 0:
        return 0.0
    r = (later - entry) / entry * 100.0
    return r if direction == "LONG" else -r


def _touches_stop(bar, direction, stop):
    if direction == "LONG":
        return bar["l"] <= stop
    return bar["h"] >= stop


def _touches(bar, direction, level):
    if direction == "LONG":
        return bar["h"] >= level
    return bar["l"] <= level


def _touches_entry(bar, direction, entry_high, entry_low):
    """Whether a bar filled the resting entry order (spec 8.2).

    This is deliberately NOT the target test: a LONG fills when price trades
    DOWN through the adverse edge (low <= entry_high), a SHORT when it trades
    UP through entry_low (high >= entry_low). Price that gaps through the
    whole band still fills a resting limit at the adverse edge, and because
    any bar reaching the stop has already traded through that edge, the fill
    precedes the stop on the same bar.
    """
    if direction == "LONG":
        return bar["l"] <= entry_high
    return bar["h"] >= entry_low


def resolve_entry(bars, t0, plan, *, now):
    """Resolve the entry window for one planned episode.

    `plan` carries direction, entry_low, entry_high, stop, tp1, tp2.
    Returns a dict with:
      status            PENDING_ENTRY | FILLED | UNFILLED | UNAVAILABLE
      fill_bar_open_ts  open_ts of the bar that filled (FILLED only)
      fill_price        the adverse edge of the entry band (FILLED only)
      fill_ts           close of the fill bar (FILLED only)
      missing_slots     slots with no complete bar, recoverable ones first

    A missing slot BEFORE any fill is found keeps the entry pending while it
    is recoverable and makes it UNAVAILABLE afterwards; UNFILLED is never
    declared on incomplete bars. A fill found before a later gap is FILLED.
    """
    direction = plan["direction"]
    entry_low = float(plan["entry_low"])
    entry_high = float(plan["entry_high"])
    slots = entry_slots(t0)
    by_slot = index_bars(bars)

    missing = []
    for ts in slots:
        if ts in by_slot:
            b = by_slot[ts]
            if (is_valid_bar(b, now=now)
                    and _touches_entry(b, direction, entry_high, entry_low)):
                # Fill price is the ADVERSE edge of the band, which is where
                # a resting limit order would have executed.
                fill_price = entry_high if direction == "LONG" else entry_low
                return {"status": "FILLED", "fill_bar_open_ts": ts,
                        "fill_price": fill_price, "fill_ts": bar_close_ts(b),
                        "missing_slots": missing}
            continue
        # An incomplete slot before any fill: recoverable or not.
        if slot_recoverable(ts, now=now):
            missing.append(ts)
            continue
        return {"status": "UNAVAILABLE", "fill_bar_open_ts": None,
                "fill_price": None, "fill_ts": None,
                "missing_slots": missing + [ts]}

    if missing:
        # Window elapsed but a recoverable slot is still absent: a missing
        # bar might have contained the fill, so never call it UNFILLED.
        return {"status": "PENDING_ENTRY", "fill_bar_open_ts": None,
                "fill_price": None, "fill_ts": None,
                "missing_slots": missing}

    # Every eligible slot present and closed, and none touched the band.
    return {"status": "UNFILLED", "fill_bar_open_ts": None,
            "fill_price": None, "fill_ts": None, "missing_slots": []}


def resolve_trade(bars, plan, *, fill_bar_open_ts, fill_price, now):
    """Resolve one filled plan forward from its fill bar.

    Returns a dict with status (OPEN | STOPPED | TP2 | EXPIRED | UNAVAILABLE),
    first-touch indices from the fill bar, tp1_before_stop, exit time/price
    and MFE/MAE from intrabar highs and lows over the span examined.

    Spec 8.5: the fill bar's stop is terminal and its target touches are
    ignored (OHLC cannot order them); later bars are stop-first; TP1 alone
    is never terminal; TP2 is terminal; expiry is the bar closing at
    fill_ts + 288 bars, exited at that bar's close. A result at bar k needs
    every slot from the fill bar to k present, so a gap leaves the trade
    OPEN while recoverable and UNAVAILABLE once it is not.
    """
    direction = plan["direction"]
    stop = float(plan["stop"])
    tp1 = float(plan["tp1"])
    tp2 = float(plan["tp2"])

    fill_ts = int(fill_bar_open_ts) + BAR_SECONDS
    fill_price = float(fill_price)
    by_slot = index_bars(bars)

    # Span examined so far: consecutive closed slots from the fill bar.
    limit = HOLD_HORIZON_BARS  # index of the expiry bar (288 for 24h)
    touched = {"stop": None, "tp1": None, "tp2": None}
    status = "OPEN"
    exit_ts = exit_price = None
    mfe_pct = mae_pct = None
    high_seen = low_seen = None

    i = 0
    while i <= limit:
        ts = int(fill_bar_open_ts) + BAR_SECONDS * i
        if ts not in by_slot:
            # A missing slot stops the walk: anything after it is unknown,
            # so the result never jumps ahead of the gap.
            if slot_recoverable(ts, now=now):
                break  # still OPEN, revisit when the slot is backfilled
            return {"status": "UNAVAILABLE", "stop": touched["stop"],
                    "tp1": touched["tp1"], "tp2": touched["tp2"],
                    "tp1_before_stop": (1 if touched["tp1"] is not None
                                        and touched["stop"] is not None else 0),
                    "exit_ts": None, "exit_price": None,
                    "mfe_pct": None, "mae_pct": None}
        b = by_slot[ts]
        if not is_valid_bar(b, now=now):
            break  # the bar may still be revised: no decision on it yet

        high = max(b["h"], b["o"], b["c"])
        low = min(b["l"], b["o"], b["c"])
        high_seen = high if high_seen is None else max(high_seen, high)
        low_seen = low if low_seen is None else min(low_seen, low)
        mfe_pct = _signed_pct(fill_price, high_seen, direction)
        mae_pct = _signed_pct(fill_price, low_seen, direction)

        if i == 0:
            # Fill bar: a stop touch is terminal and target touches on this
            # bar are ignored, because OHLC cannot show whether the target
            # traded before the entry.
            if _touches_stop(b, direction, stop):
                touched["stop"] = 0
                status, exit_ts, exit_price = "STOPPED", bar_close_ts(b), stop
                break
            if _touches(b, direction, tp1):
                touched["tp1"] = 0
            if _touches(b, direction, tp2):
                touched["tp2"] = 0
            i += 1
            continue

        # Later bars: stop-first, so a bar touching both counts the stop.
        if _touches_stop(b, direction, stop):
            touched["stop"] = i
            status, exit_ts, exit_price = "STOPPED", bar_close_ts(b), stop
            break
        if _touches(b, direction, tp1) and touched["tp1"] is None:
            touched["tp1"] = i
        if _touches(b, direction, tp2) and touched["tp2"] is None:
            touched["tp2"] = i
        if touched["tp2"] is not None:
            status, exit_ts, exit_price = "TP2", bar_close_ts(b), tp2
            break
        if i == limit:
            # Expiry: neither stop nor TP2 through the bar closing at
            # fill_ts + 24h.
            status, exit_ts, exit_price = "EXPIRED", bar_close_ts(b), b["c"]
            break
        i += 1

    tp1_before_stop = (1 if (touched["stop"] is not None
                             and touched["tp1"] is not None
                             and touched["tp1"] < touched["stop"]) else 0)
    return {"status": status, "stop": touched["stop"], "tp1": touched["tp1"],
            "tp2": touched["tp2"], "tp1_before_stop": tp1_before_stop,
            "exit_ts": exit_ts, "exit_price": exit_price,
            "mfe_pct": mfe_pct, "mae_pct": mae_pct}


def resolve_horizons(bars, plan, *, fill_bar_open_ts, fill_price, now,
                     horizons=EPISODE_HORIZONS):
    """Fixed-horizon descriptive returns from the MODELED fill (spec 9).

    Each horizon measures the signed return from the fill price to the close
    of the bar closing at fill_ts + h, with MFE/MAE from intrabar highs and
    lows over the same span. These ignore stops and targets and are
    independent of the plan's hold. PENDING until the end bar exists,
    MATURED when the span is complete, UNAVAILABLE on an unrecoverable gap.
    Never zero-filled.
    """
    direction = plan["direction"]
    fill_price = float(fill_price)
    by_slot = index_bars(bars)
    out = []
    for h in horizons:
        need = EPISODE_HORIZON_BARS[h]
        row = {"horizon": h, "status": "PENDING", "return_pct": None,
               "mfe_pct": None, "mae_pct": None}
        high_seen = low_seen = None
        complete = True
        for i in range(need):
            ts = int(fill_bar_open_ts) + BAR_SECONDS * i
            if ts not in by_slot:
                if not slot_recoverable(ts, now=now):
                    row["status"] = "UNAVAILABLE"
                complete = False
                break
            b = by_slot[ts]
            if not is_valid_bar(b, now=now):
                complete = False
                break
            high = max(b["h"], b["o"], b["c"])
            low = min(b["l"], b["o"], b["c"])
            high_seen = high if high_seen is None else max(high_seen, high)
            low_seen = low if low_seen is None else min(low_seen, low)
        if row["status"] == "UNAVAILABLE":
            out.append(row)
            continue
        if not complete:
            out.append(row)  # PENDING: revisit when the span completes
            continue
        end_ts = int(fill_bar_open_ts) + BAR_SECONDS * need
        end_bar = by_slot.get(end_ts - BAR_SECONDS)
        row["status"] = "MATURED"
        row["return_pct"] = _signed_pct(fill_price, end_bar["c"], direction)
        row["mfe_pct"] = _signed_pct(fill_price, high_seen, direction)
        row["mae_pct"] = _signed_pct(fill_price, low_seen, direction)
        out.append(row)
    return out


# Terminal statuses, mirrored from Store so the resolver never rewrites
# evidence. Kept as plain tuples here to avoid an import cycle: the episode
# resolvers depend only on what Store persists, never on Store itself.
#
# FILLED is NOT row-terminal: a filled entry with an OPEN trade still has to
# resolve, so only a final trade state or a no-trade entry state freezes it.
_TERMINAL_ENTRY = ("UNFILLED", "UNAVAILABLE")
_FINAL_FILL = ("FILLED",)
_TERMINAL_TRADE = ("STOPPED", "TP2", "EXPIRED", "UNAVAILABLE")


def _default_bar_loader(coin, data_dir, symbol=None):
    """Merge collector history with REST recovery, normalizing timestamps.

    Collector bars win timestamp ties because the local series is the source
    used for the longer 7d horizons. REST supplies recent missing slots while
    they remain within the venue's backfill window.
    """
    try:
        bars = collector.read_bars(data_dir, coin)
    except Exception:
        bars = []
    normalized = [{**b, "open_ts": b.get("open_ts", b.get("ts"))}
                  for b in bars]
    if symbol:
        try:
            from . import mexc
            rest = mexc.klines(symbol, "5m", limit=REST_MAX_BARS)
        except Exception:
            rest = []
        seen = {b["open_ts"] for b in normalized}
        for bar in rest:
            ts = bar.get("open_ts", bar.get("ts"))
            if ts is not None and ts not in seen:
                normalized.append({**bar, "open_ts": ts})
                seen.add(ts)
    return sorted((b for b in normalized if b.get("open_ts") is not None),
                  key=lambda b: int(b["open_ts"]))


def resolve_episode_plans(store, symbol_map, *, now, bar_loader=None,
                          data_dir="data/bars"):
    """Resolve every planned episode's entry, trade and fixed horizons.

    `bar_loader(coin, data_dir) -> list[bar]` is injected so unit tests stay
    offline; production passes a REST+collector loader. Returns a summary
    dict of what this pass did.

    Resolution is forward-only and idempotent: a planned episode with no
    venue symbol is skipped (never fetched), a terminal outcome row is never
    rewritten, and unresolved work keeps being refined as bars arrive.
    Plan tracking is independent of episode lifecycle (spec 6.5), so a
    closed or reversed episode's plan still resolves.
    """
    if bar_loader is None:
        loader = lambda coin, directory: _default_bar_loader(
            coin, directory, symbol_map.get(coin))
    else:
        loader = bar_loader
    # Coverage expiry must progress even for delisted coins which have no
    # remaining plan bars or venue symbol (spec §6.3).
    from .measurement import sweep_coverage
    sweep_coverage(store, now)
    summary = {"resolved": 0, "filled": 0, "unfilled": 0, "unavailable": 0,
               "skipped": 0, "pending": 0, "trade_stopped": 0, "trade_tp2": 0,
               "trade_expired": 0, "horizons_matured": 0}
    bars_cache = {}

    for ep in store.planned_episodes():
        coin = ep["coin"]
        if not symbol_map.get(coin):
            summary["skipped"] += 1
            continue
        if coin not in bars_cache:
            try:
                bars_cache[coin] = loader(coin, data_dir) or []
            except Exception:
                bars_cache[coin] = []
        bars = bars_cache[coin]
        if not bars:
            summary["pending"] += 1
            continue

        plan = {"direction": ep["direction"], "entry_low": ep["entry_low"],
                "entry_high": ep["entry_high"], "stop": ep["stop"],
                "tp1": ep["tp1"], "tp2": ep["tp2"]}
        ep_id = ep["episode_id"]
        t0 = int(ep["start_ts"])

        prior = store.episode_outcome_row(ep_id)
        if prior and prior["entry_status"] in _TERMINAL_ENTRY:
            continue  # immutable: already resolved

        # Entry. A fill already recorded in an earlier pass is kept, so the
        # fill bar is never re-decided by a weaker bar set.
        entry = ({"status": prior["entry_status"],
                  "fill_bar_open_ts": prior["fill_bar_open_ts"],
                  "fill_price": prior["fill_price"],
                  "fill_ts": prior["fill_ts"], "missing_slots": []}
                 if prior and prior["entry_status"] in _FINAL_FILL
                 else resolve_entry(bars, t0, plan, now=now))
        if entry["status"] == "PENDING_ENTRY":
            store.upsert_episode_outcome(ep_id, entry_status="PENDING_ENTRY")
            summary["pending"] += 1
            continue

        summary["resolved"] += 1
        if entry["status"] == "UNFILLED":
            summary["unfilled"] += 1
        elif entry["status"] == "UNAVAILABLE":
            summary["unavailable"] += 1

        store.upsert_episode_outcome(
            ep_id, entry_status=entry["status"],
            fill_bar_open_ts=entry["fill_bar_open_ts"],
            fill_price=entry["fill_price"], fill_ts=entry["fill_ts"],
            resolved_through_ts=now)

        if entry["status"] != "FILLED":
            continue  # UNFILLED / UNAVAILABLE: no trade, no horizons

        summary["filled"] += 1
        if prior is None or prior["trade_status"] not in _TERMINAL_TRADE:
            trade = resolve_trade(bars, plan,
                                  fill_bar_open_ts=entry["fill_bar_open_ts"],
                                  fill_price=entry["fill_price"], now=now)
            summary[f"trade_{trade['status'].lower()}"] = \
                summary.get(f"trade_{trade['status'].lower()}", 0) + 1
            store.upsert_episode_outcome(
                ep_id, trade_status=trade["status"], stop_index=trade["stop"],
                tp1_index=trade["tp1"], tp2_index=trade["tp2"],
                tp1_before_stop=trade["tp1_before_stop"],
                exit_ts=trade["exit_ts"], exit_price=trade["exit_price"],
                mfe_pct=trade["mfe_pct"], mae_pct=trade["mae_pct"],
                resolved_through_ts=now)

        for row in resolve_horizons(bars, plan,
                                    fill_bar_open_ts=entry["fill_bar_open_ts"],
                                    fill_price=entry["fill_price"], now=now):
            store.upsert_episode_horizon(
                ep_id, row["horizon"], row["status"],
                return_pct=row["return_pct"], mfe_pct=row["mfe_pct"],
                mae_pct=row["mae_pct"])
            if row["status"] == "MATURED":
                summary["horizons_matured"] += 1

    return summary
