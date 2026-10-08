"""
End-to-end scan against live MEXC data. Read-only: fetches, scores, plans.

Run:  python3 -m proto.scan [--top N] [--stake X] [--coins N]
"""

import argparse
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import mexc
from . import indicators as ind
from . import planner as pl
from .scorer import score_coin

SYNTH_MARKERS = ("STOCK", "XAU", "XAG", "USOIL", "SOXL", "SPX", "NDX", "GLD", "SLV")


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


def build_universe():
    """USDT perps only, synthetics removed. Returns [(symbol, coin)]."""
    tk = mexc.tickers()
    det = mexc.details()
    out = []
    for sym, row in tk.items():
        if not sym.endswith("_USDT"):
            continue
        coin = canon(sym)
        if not coin or is_synthetic(coin, det.get(sym)):
            continue
        out.append((sym, coin))
    return out


def analyse_one(sym, coin, detail, tk_row, stake):
    """Fetch bars + book for one coin and score it. Returns a Scorecard or None."""
    try:
        price = float(tk_row.get("lastPrice") or 0)
        if price <= 0:
            return None
        quote_vol = float(tk_row.get("amount24") or 0)
        change_24h = float(tk_row.get("riseFallRate") or 0) * 100

        bars = mexc.klines(sym, "5m", limit=200)
        bids, asks = mexc.depth(sym, limit=20)

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

        # Funding comes free on the bulk ticker row - no extra request.
        funding = float(tk_row.get("fundingRate") or 0.0)
        fund_cap = float(tk_row.get("maxFundingRate") or 0.0018)

        sc = score_coin(
            coin, bars, bids, asks,
            quote_vol_24h=quote_vol, spread_pct=spread_pct, change_1h_pct=change_1h,
            price=price, change_24h_pct=change_24h,
            funding_rate=funding, funding_cap=fund_cap, oi_change_pct=None,
            min_notional=min_not, venue="MEXC", tier=2)
        return sc
    except mexc.MexcError:
        return None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--stake", type=float, default=0.10)
    ap.add_argument("--coins", type=int, default=120, help="how many coins to analyse")
    args = ap.parse_args()

    print(f"ALT RADAR prototype scan  ·  stake ${args.stake:.2f}  ·  read-only\n")

    t0 = time.time()
    uni = build_universe()
    det = mexc.details()
    tk = mexc.tickers()
    print(f"universe: {len(uni)} USDT perps (synthetics removed)  "
          f"[{time.time()-t0:.1f}s]")

    # Rank by 24h quote volume so the scan budget goes to coins with real
    # liquidity, then take the top N for analysis.
    ranked = sorted(
        uni,
        key=lambda sc: float(tk.get(sc[0], {}).get("amount24") or 0),
        reverse=True,
    )[: args.coins]

    print(f"analysing top {len(ranked)} by 24h volume ...\n")
    t1 = time.time()

    cards = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {
            ex.submit(analyse_one, sym, coin, det.get(sym, {}), tk.get(sym, {}), args.stake): coin
            for sym, coin in ranked
        }
        for f in as_completed(futs):
            sc = f.result()
            if sc:
                cards.append(sc)

    print(f"scored {len(cards)} coins  [{time.time()-t1:.1f}s]\n")

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


if __name__ == "__main__":
    main()