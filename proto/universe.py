"""Venue universe helpers shared by bar/funding/settlement/price collection.

Extracted from the retired scanner so collection no longer imports scoring,
planning, or the signals store.
"""

import re
import threading
import time
from collections import deque

from . import mexc

SYNTH_MARKERS = ("STOCK", "XAU", "XAG", "USOIL", "SOXL", "SPX", "NDX", "GLD",
                 "SLV")

# Loop concurrency: 12 workers, a 100ms stagger on the first wave
# (thundering-herd guard), and a venue-wide 20 req/s cap every request passes
# through.
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
            time.sleep(max(0.0, wait))


LIMITER = RateLimiter()


def _fnum(v):
    """Venue number or 0.0 - a garbage field degrades, never crashes."""
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
# commodities, metals, FX) as opposed to crypto.
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
    """True for equity / index / commodity / FX contracts, not crypto perps."""
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
        if sym_part and base and sym_part.replace("_USDT", "") not in (
                base, base + "STOCK"):
            return True
    return any(t in coin for t in SYNTH_MARKERS)


def build_universe(stake=None, budget=None, store=None):
    """Every eligible MEXC USDT perp, 24h-volume ranked, as [(symbol, coin)].

    ``stake`` and ``store`` are accepted for caller compatibility and
    ignored; eligibility is structural only. ``budget=None`` means the full
    eligible set.
    """
    tk = mexc.tickers()
    det = mexc.details()

    pool = {}
    for sym in tk:
        if not sym.endswith("_USDT"):
            continue
        coin = canon(sym)
        if not coin or is_synthetic(coin, det.get(sym)):
            continue
        pool.setdefault(coin, []).append(sym)
    out = []
    for coin, syms in pool.items():
        # Keep the plain COIN_USDT form when two symbols canonicalise to one
        # coin so the choice is stable.
        out.append((sorted(syms, key=lambda s: (len(s), s))[0], coin))
    out.sort(key=lambda sc: (-_fnum(tk.get(sc[0], {}).get("amount24")),
                             sc[0]))
    if budget is not None:
        out = out[:max(0, int(budget))]
    return out
