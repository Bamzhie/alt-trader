"""
End-to-end scan against live MEXC data. Read-only: fetches, scores, plans.

Run:  python3 -m proto.scan [--top N] [--stake X] [--coins N]
"""

import argparse
import re
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import mexc
from . import indicators as ind
from . import planner as pl
from .scorer import score_coin, volume_price_component
from .store import Store

SYNTH_MARKERS = ("STOCK", "XAU", "XAG", "USOIL", "SOXL", "SPX", "NDX", "GLD", "SLV")

# Loop concurrency (spec SS3): 12 workers, a 100ms stagger on the first wave
# (thundering-herd guard), and a venue-wide 20 req/s cap every request passes
# through. The adapter's own retry (0.4s x 2^attempt, 3 tries) IS the
# specified backoff; once it exhausts, the loop counts the failure.
MAX_WORKERS = 12
STAGGER_S = 0.1
REQ_PER_S = 20


class RateLimiter:
    """Thread-safe rolling-window requests-per-second cap."""

    def __init__(self, rate=REQ_PER_S, window=1.0):
        self.rate = rate
        self.window = window
        self._times = deque()
        self._lock = threading.Lock()

    def acquire(self):
        while True:
            with self._lock:
                now = time.monotonic()
                while self._times and now - self._times[0] >= self.window:
                    self._times.popleft()
                if len(self._times) < self.rate:
                    self._times.append(now)
                    return
                wait = self.window - (now - self._times[0])
            time.sleep(max(wait, 0.001))


LIMITER = RateLimiter()

# Venue health (spec SS6: degraded venue marked and excluded, never silent).
# `ok` reflects the LAST attempt; `fails` is the cumulative counted-failure
# total; `last_err` the most recent failure reason. build_universe degrades
# around a failed venue AND records it here so the UI can mark it.
VENUE_STATE = {}


def _venue(venue, err=None):
    st = VENUE_STATE.setdefault(venue, {"ok": True, "fails": 0, "last_err": None})
    if err is None:
        st["ok"] = True
    else:
        st["ok"] = False
        st["fails"] += 1
        st["last_err"] = f"{type(err).__name__}: {err}"


def venue_health():
    """Per-venue {ok, fails, last_err} for the UI. Copies, not aliases."""
    return {v: dict(st) for v, st in VENUE_STATE.items()}


def _fnum(v):
    """Venue number or 0.0 - a garbage field degrades, never crashes.

    Venue feeds are untrusted text: a non-numeric lastPrice/amount24 must
    not raise out of the universe build or the scan sort.
    """
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def canon(sym):
    s = sym.upper().strip()
    s = re.sub(r"[-_]?(USDT|USDC|USD)[\-_]?(PERP|SWAP)?$", "", s)
    s = re.sub(r"[\-_](PERP|SWAP|PRP)$", "", s)
    s = re.sub(r"(PERP|SWAP|PRP)$", "", s)
    return s.strip("_-")


# Non-crypto conceptPlate tags. MEXC tags EVERY contract with conceptPlate
# categories; these tokens identify TradFi instruments (equities, indices,
# commodities, metals, FX) as opposed to crypto. This is a STRUCTURAL signal
# from the venue, not a name guess, and is the primary filter.
#
# Verified across the full live universe: mc-trade-zone-Stock (464 contracts),
# -tradfi (495), -stockindex (66), -Commodities (19), -metals (13),
# -metalsfutures (17), -Forex (8), -japanstock (10), -semiconductors (27),
# -aerospace (12), -TechGiants (9).
#
# A crypto contract can legitimately carry -ai, -DeFi, -MEME, -web3, -ETF and
# similar, so those are deliberately NOT in this list.
NON_CRYPTO_PLATES = {
    "mc-trade-zone-Stock",
    "mc-trade-zone-tradfi",
    "mc-trade-zone-stockindex",
    "mc-trade-zone-Commodities",
    "mc-trade-zone-metals",
    "mc-trade-zone-metalsfutures",
    "mc-trade-zone-Forex",
    "mc-trade-zone-japanstock",
    "mc-trade-zone-semiconductors",
    "mc-trade-zone-aerospace",
    "mc-trade-zone-aerospacedefense",
    "mc-trade-zone-TechGiants",
    "mc-trade-zone-privity",
}

# Fallback name lists, for contracts that carry no conceptPlate tags at all.
EXPLICIT_NON_CRYPTO = {
    "NAS100", "NAS100M", "US100", "US500", "US30", "SPX", "SPX500", "NDX",
    "NASDAQ", "NASDAQ100", "DJI", "DOWJONES", "S&P500", "DEFI", "VIX",
    "XAU", "XAG", "GOLD", "SILVER", "USOIL", "WTI", "BRENT",
    "COPPER", "ALUMINUM", "NATGAS", "GAS", "SOIL", "PLATINUM", "PALLADIUM",
    "AUDI", "CAD", "CHF", "AUD", "EUR", "GBP", "JPY", "CNH", "CNY",
}


def is_synthetic(coin, detail_row=None):
    """
    True for equity / index / commodity / FX contracts, not crypto perps.

    Layered because no single signal is sufficient:

    1. conceptPlate tags (PRIMARY, structural). MEXC tags every contract with
       zone categories; TradFi tokens like mc-trade-zone-Stock identify
       AAPL_USDT, OPENAI_USDT and NAS100 without any name guessing. This is
       the layer that reliably catches contracts whose ticker matches their
       display name.
    2. EXPLICIT_NON_CRYPTO - for contracts with no conceptPlate tags.
    3. displayNameEn mismatch - synthetic contracts name the real-world
       instrument they track (AAPLSTOCK -> AAPL_USDT, NVIDIA -> NVDA_USDT)
       whereas crypto perps name their own coin.
    4. Substring markers - last-resort for obvious cases.
    """
    if detail_row:
        plates = detail_row.get("conceptPlate") or []
        if any(p in NON_CRYPTO_PLATES for p in plates):
            return True
    if coin in EXPLICIT_NON_CRYPTO:
        return True
    name = ""
    if detail_row:
        name = str(detail_row.get("displayNameEn") or "")
    if name:
        sym_part = name.split()[0].replace("(", "").replace(")", "")
        base = coin.split("_")[0].split("-")[0]
        if sym_part and base and sym_part.replace("_USDT", "") not in (base, base + "STOCK"):
            return True
    return any(t in coin for t in SYNTH_MARKERS)


# Universe groups for one scan cycle (spec SS3): three DISJOINT groups, deduped
# by coin, filled in priority order 80 -> 40 -> 30, shortfall spilling into the
# remainder so the budget still fills.
GROUP_TRADEABLE = 80    # min_notional <= stake, ranked by 24h quote volume
GROUP_TAIL = 40         # MEXC-only when Bybit's map is known, else lowest-volume
GROUP_ROTATION = 30     # rolling window over the remainder, pointer in meta
ROTATION_KEY = "rotation_ptr"


def rotation_pointer(store):
    """Rotation offset into the remainder group, persisted in meta.

    0 when unset or unparsable - a bad pointer must never stop the scan.
    """
    try:
        return int(store.get_meta(ROTATION_KEY, 0) or 0)
    except (TypeError, ValueError):
        return 0


def advance_rotation(store, consumed):
    """Move the rotation pointer forward by `consumed` remainder coins.

    Returns the new pointer. Read-time modulo against the current remainder
    keeps it in range; the stored value only ever grows, so the window keeps
    sweeping forward even as the remainder itself changes between cycles.
    """
    ptr = rotation_pointer(store) + max(0, int(consumed))
    store.set_meta(ROTATION_KEY, ptr)
    return ptr


# Bybit symbol map for the OI leg (spec SS4): {coin: Bybit raw symbol}.
# Filled as a side effect of _bybit_symbol_set() when the venue answers -
# ONE Bybit ticker request per universe refresh (10 min cached in the app),
# never one per coin - and CLEARED when that fetch fails, so a degraded
# Bybit means every coin runs funding-only for the cycle (the failure is
# already counted once at the refresh) instead of firing 150 OI requests
# at a dead venue. analyse_one only ever READS this map.
BYBIT_MAP = {}


def _bybit_symbol_set():
    """Coins Bybit also lists, or None when that map is unavailable.

    Also refreshes BYBIT_MAP (coin -> raw Bybit symbol) on success and
    clears it on failure, so the OI leg and the universe tail agree on
    whether Bybit is currently usable.

    None means "we don't know" and the tail group falls back to the
    lowest-volume slice, so guaranteed coverage never depends on Bybit being
    up. Any failure shape - BybitError, TimeoutError, ValueError - degrades
    to the fallback AND is recorded in venue health: degraded is marked and
    counted, never silent (spec SS6).
    """
    try:
        from . import bybit
        rows = bybit.tickers()
        if not rows:
            _venue("bybit", RuntimeError("empty ticker list"))
            BYBIT_MAP.clear()
            return None
        _venue("bybit")
        BYBIT_MAP.clear()
        BYBIT_MAP.update({canon(s): s for s in rows})
        return set(BYBIT_MAP)
    except Exception as e:
        _venue("bybit", e)
        BYBIT_MAP.clear()
        return None


def _bybit_oi(coin, price, quality_errors=None, oi_extra=None):
    """(pct_change, notional_usdt) for a Bybit-shared coin; (None, None) else.

    Reads BYBIT_MAP - filled by the universe refresh - so this lookup never
    issues a Bybit request of its own: an MEXC-only coin or an unknown /
    degraded map is None by construction. A failed fetch is COUNTED in
    venue health (degraded and marked, never silent - spec SS6) and degrades
    to None, the funding-only path (spec SS4): it never fails the coin.

    Notional is approximate (latest OI units x MEXC last price, marked ~):
    it exists so a percent can be read against its base - a +68% off a $5k
    base is not a +68% off $5M. ONE request serves both numbers.

    `oi_extra` (optional dict) receives the matched 1-hour OI fields the
    shadow score uses (`oi_change_1h_pct`, `oi_gap_s`) from that SAME
    request; absent when the adapter returns a plain 2-tuple.
    """
    sym = BYBIT_MAP.get(coin)
    if not sym:
        return None, None
    try:
        from . import bybit
        res = _gated(bybit.oi_state, sym)
        pct, units = res
        if oi_extra is not None:
            oi_extra.update(getattr(res, "detail", None) or {})
        notion = units * price if units and price and price > 0 else None
        return pct, notion
    except Exception as e:
        _venue("bybit", e)
        if quality_errors is not None:
            quality_errors.append(f"Bybit OI fetch failed: {type(e).__name__}")
        return None, None


# Guaranteed scan universe: 80 stake-tradeable + 40 tail + 30 rotation,
# disjoint, spilling to fill. One constant so build_universe's default and
# the CLI's --coins default can never drift apart (a smaller --coins would
# silently drop the guaranteed tail from every scan).
UNIVERSE_BUDGET = 150


def find_symbol(coin, tickers, details):
    """Raw venue symbol for a coin name, or None.

    Pure lookup over bulk ticker/detail maps: canonical-name match on USDT
    perps, synthetics excluded. Powers on-demand scoring for coins outside
    the current scan rotation (the Find box's Enter path).
    """
    want = canon(str(coin or ""))
    if not want:
        return None
    for sym in tickers:
        if not sym.endswith("_USDT"):
            continue
        if canon(sym) == want and not is_synthetic(canon(sym),
                                                   details.get(sym)):
            return sym
    return None


def build_universe(stake, budget=UNIVERSE_BUDGET, store=None):
    """
    Stake-aware scan universe: disjoint groups that fill `budget`.

    Groups, in priority order, deduped by coin:
      1. tradeable - min_notional <= stake (an unknown minimum is fail-closed
         and excluded), ranked by 24h quote volume, first 80.
      2. tail - coins Bybit does not list (MEXC-only) when that map is
         available, otherwise the 40 lowest-volume coins. Lowest volume first
         either way, so the thin end of the book gets guaranteed coverage.
      3. rotation - 30 from whatever remains, starting at meta:rotation_ptr.
    Any group shortfall spills across the rest of the remainder so the budget
    still fills; the three groups never overlap.

    With `store`, the pointer is read from and advanced in meta by the number
    of remainder coins consumed, so the window sweeps forward every cycle and
    survives restarts. `budget=None` means uncapped (full universe).

    Returns [(symbol, coin)].
    """
    tk = mexc.tickers()
    det = mexc.details()

    pool = []
    for sym, row in tk.items():
        if not sym.endswith("_USDT"):
            continue
        coin = canon(sym)
        if not coin or is_synthetic(coin, det.get(sym)):
            continue
        pool.append((sym, coin))
    pool.sort(key=lambda sc: _fnum(tk.get(sc[0], {}).get("amount24")),
              reverse=True)

    picked = []
    used = set()

    def take(items):
        """Append unused items until the budget is full. Returns count taken."""
        n = 0
        for it in items:
            if budget is not None and len(picked) >= budget:
                break
            if it[1] in used:
                continue
            picked.append(it)
            used.add(it[1])
            n += 1
        return n

    def stake_ok(sc):
        price = _fnum(tk.get(sc[0], {}).get("lastPrice"))
        row = det.get(sc[0]) or {}
        n = mexc.min_notional(sc[0], row, price)
        return n is not None and n <= stake

    # 1. tradeable by stake, vol-ranked
    take([sc for sc in pool if stake_ok(sc)][:GROUP_TRADEABLE])

    # 2. tail: MEXC-only when the Bybit map answers, else lowest-volume slice
    bybit_set = _bybit_symbol_set()
    tail_pool = [sc for sc in pool if sc[1] not in used]
    if bybit_set is not None:
        tail_pool = [sc for sc in tail_pool if sc[1] not in bybit_set]
    take(list(reversed(tail_pool))[:GROUP_TAIL])

    # 3. rotation window over the remainder, then shortfall spill
    remainder = [sc for sc in pool if sc[1] not in used]
    consumed = 0
    if remainder:
        n = len(remainder)
        ptr = (rotation_pointer(store) if store is not None else 0) % n
        window = [remainder[(ptr + i) % n] for i in range(min(GROUP_ROTATION, n))]
        consumed += take(window)
        start = (ptr + len(window)) % n
        consumed += take([remainder[(start + i) % n] for i in range(n)])

    if store is not None and consumed:
        advance_rotation(store, consumed)
    return picked


# Measurement universe policy (design §4): the full set of MEXC perpetual
# contracts that pass the existing supported-symbol and structural
# eligibility filters, independent of the interactive top-N budget and the
# rotation window. Any change to these filters is a new policy version, which
# changes the config hash and therefore the episode cohort.
MEASUREMENT_UNIVERSE_POLICY = "mexc_full_v1"


def eligible_pool(tickers, details):
    """Every eligible (symbol, coin, detail_row), 24h-volume ranked.

    The structural eligibility shared by the measurement universe and the
    interactive universe: USDT-quoted contracts only, synthetics (equity /
    index / commodity / FX plates) excluded, deduped by canonical coin name.
    Deliberately NOT stake-filtered and NOT capped - measurement must cover
    every eligible coin each cycle, so a coin the interactive scan rotates
    out of is still measured.

    Deterministic order (volume desc, then symbol) keeps the measurement
    universe reproducible across cycles and machines.
    """
    pool = {}
    for sym, row in tickers.items():
        if not sym.endswith("_USDT"):
            continue
        coin = canon(sym)
        if not coin or is_synthetic(coin, details.get(sym)):
            continue
        pool.setdefault(coin, []).append((sym, row))
    out = []
    for coin, syms in pool.items():
        # Two symbols canonicalising to one coin: keep the plain COIN_USDT
        # form (e.g. AAA_USDT over AAA-PERP_USDT) so the choice is stable.
        sym, row = sorted(syms, key=lambda sr: (len(sr[0]), sr[0]))[0]
        out.append((sym, coin, details.get(sym) or {}))
    out.sort(key=lambda sc: (-_fnum(tickers.get(sc[0], {}).get("amount24")),
                             sc[0]))
    return out


def build_measurement_universe(tickers, details, stake=None):
    """All-eligible measurement universe as [(symbol, coin, detail_row)].

    Same supported-symbol and structural eligibility filters as the current
    MEXC universe (see eligible_pool), with no top-N budget and no rotation:
    `stake` is accepted for interface symmetry with build_universe and is
    deliberately unused, because a small stake must never shrink the
    measurement universe (design §4).
    """
    return eligible_pool(tickers, details)


def _gated(fn, *args, **kw):
    """One venue request, admitted through the venue-wide rate cap."""
    LIMITER.acquire()
    return fn(*args, **kw)


def tf_lean(sym, interval, limit=200, min_bars=30, quality_errors=None):
    """volume_price lean for one higher timeframe, or None when unavailable.

    None means "no evidence": a failed fetch or a thin history degrades to
    no-bonus scoring instead of dropping the coin, mirroring the way a
    missing Bybit map degrades the universe tail. Transport failures are
    COUNTED in MEXC venue health (visible, never silent); the caller notes
    which timeframe went missing on the card.
    """
    try:
        bars = _gated(mexc.klines, sym, interval, limit=limit)
    except Exception as e:
        _venue("mexc", e)
        if quality_errors is not None:
            quality_errors.append(
                f"MEXC {interval} fetch failed: {type(e).__name__}")
        return None
    if not bars or len(bars) < min_bars:
        return None
    return volume_price_component(bars)[1]


def analyse_one(sym, coin, detail, tk_row, stake, errors=None,
                attach_plans=False, max_leverage=None):
    """Fetch bars + book for one coin and score it. Returns a Scorecard or None.

    None is ALWAYS a counted failure: the reason is recorded in
    `errors[coin]` (when an errors dict is supplied) so the loop can surface
    `failed N/150` and the detail view can show the per-coin last error.
    Every failure shape counts the same way - MexcError after the adapter's
    retries, TimeoutError from a socket read, ValueError from a garbage
    venue field (deferred Task 1/2 notes) - none may escape and kill the
    loop. MTF fetch failures are NOT per-coin failures: tf_lean degrades
    those to no-bonus evidence by design; the Bybit OI leg degrades the
    same way (_bybit_oi: None for funding-only scoring, the venue failure
    counted in health, the coin still scored).
    """
    def fail(reason):
        if errors is not None:
            errors[coin] = reason
        return None

    try:
        price = float(tk_row.get("lastPrice") or 0)
        if price <= 0:
            return fail("no usable lastPrice")
        quote_vol = float(tk_row.get("amount24") or 0)
        change_24h = float(tk_row.get("riseFallRate") or 0) * 100

        bars = _gated(mexc.klines, sym, "5m", limit=200)
        bids, asks = _gated(mexc.depth, sym, limit=20)

        # Higher timeframes for the MTF alignment bonus (spec SS4):
        # 1H trend, 4H swing bias, same volume_price lean formula.
        quality_errors = []
        lean_1h = tf_lean(sym, "1H", quality_errors=quality_errors)
        lean_4h = tf_lean(sym, "4H", quality_errors=quality_errors)

        # 1h move, from 5m bars: 12 bars.
        change_1h = 0.0
        if len(bars) >= 13:
            change_1h = (bars[-1]["c"] - bars[-13]["c"]) / bars[-13]["c"] * 100

        # spread
        spread_pct = 0.0
        if bids and asks:
            bp, ap = bids[0][0], asks[0][0]
            if ap > 0:
                spread_pct = (ap - bp) / ap * 100

        min_not = mexc.min_notional(sym, detail, price)

        # Staleness guard (F-02): ticker snapshot (refreshed on a TTL) can lag
        # the freshly fetched candles/book. A >1% price skew is noted on the
        # card so direction/levels are never read as fresher than they are.
        # (Funding has no fresher source; it rides the same snapshot.)
        _skew_note = None
        if bars:
            _close = bars[-1]["c"]
            if _close > 0:
                _skew = abs(price - _close) / _close * 100
                if _skew > 1.0:
                    _skew_note = (f"DATA ticker/candle skew {_skew:.1f}% — price, "
                                  f"24h move, volume and funding come from "
                                  f"the cached snapshot, candles/book are fresh")

        # Funding comes free on the bulk ticker row - no extra request.
        funding = float(tk_row.get("fundingRate") or 0.0)
        fund_cap = float(tk_row.get("maxFundingRate") or 0.0018)

        # OI leg (spec SS4): Bybit open-interest change for shared coins
        # only. MEXC-only coins, an unknown symbol map, or a failed fetch
        # all degrade to None -> funding-only scoring, counted, never fatal.
        oi_extra = {}
        oi_pct, oi_notion = _bybit_oi(coin, price, quality_errors, oi_extra)

        sc = score_coin(
            coin, bars, bids, asks,
            quote_vol_24h=quote_vol, spread_pct=spread_pct, change_1h_pct=change_1h,
            price=price, change_24h_pct=change_24h,
            funding_rate=funding, funding_cap=fund_cap, oi_change_pct=oi_pct,
            oi_notional=oi_notion,
            lean_1H=lean_1h, lean_4H=lean_4h,
            oi_change_1h_pct=oi_extra.get("oi_change_1h_pct"),
            oi_gap_s=oi_extra.get("oi_gap_s"),
            min_notional=min_not, venue="MEXC", tier=2)
        for iv, lean in (("1H", lean_1h), ("4H", lean_4h)):
            if lean is None:
                sc.notes.append(f"MTF {iv} unavailable — scored with no MTF "
                                f"bonus (missing evidence, not neutral)")
        if _skew_note is not None:
            sc.notes.append(_skew_note)
        if quality_errors:
            sc.notes.append("DATA transient input failure: "
                            + "; ".join(quality_errors))
        if attach_plans and sc.direction in ("LONG", "SHORT") and not sc.vetoes:
            # Score-consistent plan snapshot for the measurement log: swing
            # from the SAME bars the score used (no extra request, no drift
            # between evidence and levels). Unplannable geometry -> None.
            try:
                swing = (ind.swing_low(bars) if sc.direction == "LONG"
                         else ind.swing_high(bars))
                if swing is None:
                    swing = (bars[-1]["l"] if sc.direction == "LONG"
                             else bars[-1]["h"])
                # max_leverage: operator cap (None = planner default, i.e.
                # the venue maximum — identical behaviour for every caller
                # that does not thread a cap).
                kw = ({"max_leverage": max_leverage}
                      if max_leverage is not None else {})
                sc.plan = pl.build_plan(sc, stake=stake, swing_ref=swing,
                                        **kw)
            except (ValueError, IndexError):
                sc.plan = None
        return sc
    except Exception as e:
        return fail(f"{type(e).__name__}: {e}")


def score_universe(ranked, det, tk, stake, analyse=None, errors=None,
                   stagger=STAGGER_S, max_workers=MAX_WORKERS,
                   attach_plans=False, max_leverage=None):
    """Concurrent per-coin fetch+score (spec SS3).

    12 workers; the first wave of submissions is staggered 100ms apart so
    the venue never sees a thundering herd at t=0; every venue request
    inside analyse_one passes through LIMITER (20 req/s venue-wide).
    Coins that fail are excluded from the returned scorecards AND recorded
    in `errors` as {coin: reason} - never silently dropped.
    """
    analyse = analyse or analyse_one
    # attach_plans rides along only when the analyser accepts it (analyse_one
    # does; third-party analysers keep the old 6-arg call working).
    import inspect as _inspect
    _takes_plans = (analyse is analyse_one or "attach_plans"
                    in _inspect.signature(analyse).parameters)
    cards = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = []
        for i, (sym, coin) in enumerate(ranked):
            kw = {"errors": errors}
            if _takes_plans:
                kw["attach_plans"] = attach_plans
                if max_leverage is not None:
                    # Scan-time attached plans honour the operator's cap.
                    kw["max_leverage"] = max_leverage
            futs.append(ex.submit(analyse, sym, coin, det.get(sym, {}),
                                  tk.get(sym, {}), stake, **kw))
            if i < max_workers and stagger:
                time.sleep(stagger)
        for f in as_completed(futs):
            sc = f.result()
            if sc:
                cards.append(sc)
    return cards


def cli_parser():
    """Argument parser for `python3 -m proto.scan`, extracted so tests can
    pin the defaults - specifically that --coins equals UNIVERSE_BUDGET."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--stake", type=float, default=0.10)
    ap.add_argument("--coins", type=int, default=UNIVERSE_BUDGET,
                    help="how many coins to analyse (default: the full "
                         "universe budget, so the guaranteed tail is scanned)")
    ap.add_argument("--db", default="data/signals.db",
                    help="signal db; rotation pointer persists here across runs")
    ap.add_argument("--no-logs", action="store_true",
                    help="in-memory store: rotation pointer does not persist")
    return ap


def main():
    args = cli_parser().parse_args()

    print(f"ALT RADAR prototype scan  ·  stake ${args.stake:.2f}  ·  read-only\n")

    t0 = time.time()
    # Same wiring as the app: a Store is what carries the rotation pointer, so
    # every scan path advances it. File db by default (survives restarts);
    # --no-logs keeps the run disk-free via an in-memory store.
    store = Store(":memory:") if args.no_logs else Store(args.db)
    uni = build_universe(args.stake, store=store)
    det = mexc.details()
    tk = mexc.tickers()
    print(f"universe: {len(uni)} USDT perps (synthetics removed)  "
          f"[{time.time()-t0:.1f}s]")

    # Rank by 24h quote volume so the scan budget goes to coins with real
    # liquidity, then take the top N for analysis.
    ranked = sorted(
        uni,
        key=lambda sc: _fnum(tk.get(sc[0], {}).get("amount24")),
        reverse=True,
    )[: args.coins]

    print(f"analysing top {len(ranked)} by 24h volume ...\n")
    t1 = time.time()

    errors = {}
    cards = score_universe(ranked, det, tk, args.stake, errors=errors)

    print(f"scored {len(cards)} coins · failed {len(errors)}/{len(ranked)}  "
          f"[{time.time()-t1:.1f}s]\n")
    for v, st in venue_health().items():
        if not st["ok"]:
            print(f"  WARNING: {v.upper()} degraded ({st['fails']} failures) - "
                  f"{st['last_err']}")
    for coin, reason in sorted(errors.items()):
        print(f"  failed {coin}: {reason}")
    if errors:
        print()

    cards.sort(key=lambda c: c.score, reverse=True)
    top = cards[: args.top]

    print(f"{'#':<3}{'DIR':<5}{'COIN':<14}{'PRICE':>13}{'24H%':>8}{'VOL24':>11}"
          f"{'LEAN':>7}{'EARLY':>7}{'SCORE':>7}  NOTES")
    print("─" * 100)
    for i, c in enumerate(top, 1):
        arrow = {"LONG": "▲", "SHORT": "▼", "NEUTRAL": "•"}.get(c.direction, "•")
        notes = []
        if c.vetoes:
            notes.append("veto:" + ",".join(v.code for v in c.vetoes))
        if c.min_notional and c.min_notional > args.stake:
            notes.append("WATCH(min notional)")
        vol = getattr(c, "quote_vol_24h", 0.0)
        vol_s = f"${vol/1e6:.1f}M" if vol >= 1e6 else f"${vol/1e3:.0f}K"
        print(f"{i:<3}{arrow:<5}{c.coin:<14}{c.price:>13.8g}{c.change_24h_pct:>7.1f}%"
              f"{vol_s:>11}{c.lean:>7.2f}{c.earlyness:>7.2f}{c.score:>7.1f}"
              f"  {' '.join(notes)}")

    # Show a full plan for the best actionable coin. A coin can be actionable
    # for scoring yet geometrically unplannable - e.g. a SHORT whose most
    # recent swing high sits BELOW current price, so no valid stop exists.
    # Iterate until we find one that produces a valid plan rather than crashing
    # on the first failure.
    print()
    for c in top:
        if not c.actionable:
            continue
        sym_lookup = dict((cn, sy) for sy, cn in uni)
        if c.coin not in sym_lookup:
            continue
        try:
            bars = mexc.klines(sym_lookup[c.coin], "5m", limit=200)
            swing = (ind.swing_low(bars) if c.direction == "LONG"
                     else ind.swing_high(bars))
            if swing is None:
                swing = bars[-1]["l"] if c.direction == "LONG" else bars[-1]["h"]
            plan = pl.build_plan(c, stake=args.stake, swing_ref=swing)
        except (ValueError, IndexError) as e:
            print(f"── {c.coin} ({c.direction}) skipped: no valid structural stop "
                  f"({type(e).__name__}) — move has already extended past structure")
            continue
        print(f"── TRADE PLAN for {c.coin} ({c.direction}) " + "─" * 40)
        print(pl.format_plan(plan, c))
        break
    else:
        print("no coin in this scan produced a valid trade plan (all vetoed, "
              "unplannable, or min-notional blocked at this stake)")

    print(f"\ntotal {time.time()-t0:.1f}s")
    store.close()


if __name__ == "__main__":
    main()
