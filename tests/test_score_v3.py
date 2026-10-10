"""Shadow score `score-v3-dis` tests (docs/grok-review.md, section 'Tests that
change if this is approved').

Covers: economic flow symmetry, initiation as AGE (not distance to the
extreme), direction from 5-minute structure only, the 1-hour OI clock and its
45-90 minute gap check, mirrored-path symmetry, the opposing multipliers and
the higher-timeframe bonus, logging, and that the live v2 score/flag fields
never move because of any shadow input. Offline only.
"""

import math
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import bybit
from proto import indicators as ind
from proto import scan as scanmod
from proto import scorer
from proto.store import Store

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


T0 = 1_700_000_100   # 5-minute aligned


def make_bars(closes, vols):
    out, prev = [], closes[0]
    for i, (c, v) in enumerate(zip(closes, vols)):
        o = prev
        out.append({"ts": T0 + i * 300, "o": o,
                    "h": max(o, c) * 1.0004, "l": min(o, c) * 0.9996,
                    "c": c, "vol": v, "amount": v * c})
        prev = c
    return out


def mirror(bars):
    """Exact geometric mirror (1/x on prices, highs/lows swapped)."""
    out = []
    for b in bars:
        h, l = 1.0 / b["l"], 1.0 / b["h"]
        c = 1.0 / b["c"]
        out.append({"ts": b["ts"], "o": 1.0 / b["o"], "h": max(h, l),
                    "l": min(h, l), "c": c, "vol": b["vol"],
                    "amount": b["vol"] * c})
    return out


def breakout_bars(after):
    """70 flat bars, a +1% breakout on bar 70, then `after` more bars that keep
    climbing slowly (so price stays pinned to the window high). Tail volume is
    held constant so only the AGE of the break differs between callers."""
    closes = [100.0 + (0.05 if i % 2 else 0.0) for i in range(70)]
    vols = [1000.0] * 70
    px = 101.0
    closes.append(px)
    vols.append(4000.0)
    for _ in range(after):
        px *= 1.001
        closes.append(px)
        vols.append(4000.0)
    return make_bars(closes, vols)


GOOD = dict(book_lean=0.0, change_1h_pct=1.0, funding_rate=0.0001,
            funding_cap=0.0018)

print("=== breakout_age / volume_ratio helpers ===")
b1 = breakout_bars(1)      # break on bar 70, 71 bars total -> latest is age 1
check("age of a break two bars ago is 1", ind.breakout_age(b1, +1) == 1,
      str(ind.breakout_age(b1, +1)))
check("no downside break in an up path", ind.breakout_age(b1, -1) is None)
check("bad side -> None", ind.breakout_age(b1, 0) is None)
b0 = breakout_bars(0)
check("latest bar broke out -> age 0", ind.breakout_age(b0, +1) == 0,
      str(ind.breakout_age(b0, +1)))
check("channel excludes the bar itself (flat path has no break)",
      ind.breakout_age(make_bars([100.0] * 80, [1000.0] * 80), +1) is None)
check("too little history for a 48-bar channel -> None",
      ind.breakout_age(make_bars([100.0 + i for i in range(40)],
                                 [1000.0] * 40), +1) is None)
for bars in (b0, b1, make_bars([100.0] * 20, [1000.0] * 20)):
    r = ind.volume_ratio(bars)
    ve = ind.volume_expansion(bars)
    expect = 0.0 if r is None else max(0.0, min(1.0, (r - 1.0) / 3.0))
    check("volume_ratio maps onto volume_expansion", abs(ve - expect) < 1e-12,
          f"{ve} vs {expect}")

print("\n=== initiation is AGE, not distance to the extreme ===")
young = scorer.score_v3(breakout_bars(1), **GOOD)
old = scorer.score_v3(breakout_bars(37), **GOOD)    # 36 bars later
check("young break is LONG", young["direction_v3"] == "LONG",
      young["direction_v3"])
check("old break is LONG", old["direction_v3"] == "LONG", old["direction_v3"])
check("initiation = exp(-age/18) for the young break",
      abs(young["initiation"] - round(math.exp(-1 / 18.0), 4)) < 1e-9,
      str(young["initiation"]))
check("initiation = exp(-age/18) for the old break",
      abs(old["initiation"] - round(math.exp(-37 / 18.0), 4)) < 1e-9,
      str(old["initiation"]))
check("2-bar breakout scores above the same extremity 36 bars later",
      young["score_v3"] > old["score_v3"],
      f"young={young['score_v3']} old={old['score_v3']}")
check("36 bars later is a small fraction of the young score",
      old["score_v3"] < 0.25 * young["score_v3"],
      f"young={young['score_v3']} old={old['score_v3']}")
def stalled_bars(after):
    """Break on bar 70, then `after` bars consolidating just under the new
    high (no further channel exit)."""
    closes = [100.0 + (0.05 if i % 2 else 0.0) for i in range(70)] + [101.0]
    closes += [101.0 if i % 2 else 100.95 for i in range(after)]
    return make_bars(closes, [1000.0] * 70 + [4000.0] * (1 + after))


check("stalled at the high: age counts from the break",
      ind.breakout_age(stalled_bars(36), +1) == 36,
      str(ind.breakout_age(stalled_bars(36), +1)))
st_young = scorer.score_v3(stalled_bars(2), **GOOD)
st_old = scorer.score_v3(stalled_bars(36), **GOOD)
check("stalled-at-high fixture: young > old by age alone",
      st_young["score_v3"] > st_old["score_v3"],
      f"{st_young['score_v3']} vs {st_old['score_v3']}")
grind = scorer.score_v3(breakout_bars(48), **GOOD)
check("a 4h grind of repeated new highs decays on age (not pinned at age 0)",
      grind["initiation"] < 0.10, str(grind["initiation"]))
dip = make_bars([100.0 + (0.05 if i % 2 else 0.0) for i in range(70)]
                + [101.0, 101.2, 100.0, 100.05, 100.0, 101.5],
                [1000.0] * 70 + [4000.0] * 6)
check("a fresh break after a dip back inside starts a NEW run (age 0)",
      ind.breakout_age(dip, +1) == 0, str(ind.breakout_age(dip, +1)))

for age, want in ((0, 1.00), (6, 0.72), (12, 0.51), (18, 0.37),
                  (36, 0.14), (48, 0.07)):
    check(f"table: age {age} -> {want:.2f}",
          abs(math.exp(-age / 18.0) - want) < 0.006)

print("\n=== no break: building flow vs nothing ===")
# Range-bound with a real volume build but no channel exit.
rng_closes = [100.0 + (0.05 if i % 2 else 0.0) for i in range(70)]
rng_closes += [100.06, 100.08, 100.10, 100.13]      # drifting up, inside range
rng_vols = [1000.0] * 70 + [3500.0] * 4
rng = make_bars(rng_closes, rng_vols)
rv = scorer.score_v3(rng, **GOOD)
check("range-bound, volume >= 2x median, lean coherent -> initiation 0.50",
      rv["direction_v3"] == "LONG" and rv["initiation"] == 0.5,
      f"{rv['direction_v3']} {rv['initiation']} parts={rv['parts']}")
quiet = make_bars(rng_closes, [1000.0] * 74)
qv = scorer.score_v3(quiet, **GOOD)
check("range-bound, no volume build -> initiation 0 and score 0",
      qv["initiation"] == 0.0 and qv["score_v3"] == 0.0,
      f"{qv['initiation']} {qv['score_v3']}")

print("\n=== NEUTRAL: |L5| <= 0.15 scores 0 ===")
flat = make_bars([100.0 + (0.02 if i % 2 else 0.0) for i in range(80)],
                 [1000.0] * 80)
fv = scorer.score_v3(flat, **GOOD, oi_change_1h_pct=4.0, oi_gap_s=3600)
check("flat noise -> NEUTRAL / 0", fv["direction_v3"] == "NEUTRAL"
      and fv["score_v3"] == 0.0, f"{fv['direction_v3']} {fv['score_v3']}")
check("NEUTRAL earns no opening / book credit",
      fv["opening_for"] == 0 and fv["book_agree"] == 0)

print("\n=== OI quadrant: economic mirror, matched 1h clock ===")
up = breakout_bars(1)
dn = mirror(up)
GAP = 3600
cases = [
    (+1.0, +4.0, "opening", +1),    # new longs
    (-1.0, +4.0, "opening", -1),    # new shorts, same magnitude
    (-1.0, -4.0, "unwind", -1),     # long liquidation
    (+1.0, -4.0, "unwind", +1),     # short covering
]
for px, oi, want_state, want_side in cases:
    st, sd = scorer.flow_state_v3(px, oi, GAP)
    check(f"price {px:+.0f}% OI {oi:+.0f}% -> {want_state}, side {want_side:+d}",
          (st, sd) == (want_state, want_side), f"{st} {sd}")

long_open = scorer.score_v3(up, book_lean=0.0, change_1h_pct=1.0,
                            oi_change_1h_pct=4.0, oi_gap_s=GAP,
                            funding_rate=0.0, funding_cap=0.0018)
short_open = scorer.score_v3(dn, book_lean=0.0, change_1h_pct=-1.0,
                             oi_change_1h_pct=4.0, oi_gap_s=GAP,
                             funding_rate=0.0, funding_cap=0.0018)
check("price up + OI up on a LONG: opening_for", long_open["opening_for"] == 1)
check("price down + OI up on a SHORT: opening_for (same economics)",
      short_open["opening_for"] == 1, str(short_open))
check("new shorts earn the same opening credit as new longs",
      abs(long_open["score_v3"] - short_open["score_v3"])
      <= 0.12 * long_open["score_v3"],
      f"L={long_open['score_v3']} S={short_open['score_v3']}")
liq = scorer.score_v3(dn, book_lean=0.0, change_1h_pct=-1.0,
                      oi_change_1h_pct=-4.0, oi_gap_s=GAP,
                      funding_rate=0.0, funding_cap=0.0018)
check("long liquidation (price down, OI down) earns zero opening points",
      liq["flow_state"] == "unwind" and liq["opening_for"] == 0
      and liq["opening_against"] == 0, str(liq))
check("price down + OI down scores below price down + OI up",
      liq["score_v3"] < short_open["score_v3"],
      f"unwind={liq['score_v3']} opening={short_open['score_v3']}")
cover = scorer.score_v3(up, book_lean=0.0, change_1h_pct=1.0,
                        oi_change_1h_pct=-4.0, oi_gap_s=GAP,
                        funding_rate=0.0, funding_cap=0.0018)
check("short covering does not add points and does not zero the score",
      cover["opening_for"] == 0 and cover["score_v3"] > 0, str(cover))

print("\n=== OI clock: 24h figure never enters; gap check 45-90 min ===")
no1h = scorer.score_v3(up, **GOOD, oi_change_1h_pct=None)
check("no 1h OI -> unavailable, opening_for 0",
      no1h["flow_state"] == "unavailable" and no1h["opening_for"] == 0)
for gap, ok in ((2699, False), (2700, True), (5400, True), (5401, False),
                (7200, False)):
    r = scorer.score_v3(up, **GOOD, oi_change_1h_pct=4.0, oi_gap_s=gap)
    check(f"sample gap {gap}s -> {'used' if ok else 'unavailable'}",
          (r["flow_state"] == "opening") == ok, r["flow_state"])
flat1h = scorer.score_v3(up, book_lean=0.0, change_1h_pct=0.10,
                         oi_change_1h_pct=4.0, oi_gap_s=GAP,
                         funding_rate=0.0, funding_cap=0.0018)
check("|1h price| < 0.15pp -> flat, no flow sign, no opening credit",
      flat1h["flow_state"] == "flat" and flat1h["opening_for"] == 0
      and flat1h["opening_against"] == 0, str(flat1h["flow_state"]))
against = scorer.score_v3(up, book_lean=0.0, change_1h_pct=-1.0,
                          oi_change_1h_pct=4.0, oi_gap_s=GAP,
                          funding_rate=0.0, funding_cap=0.0018)
check("opening on the OTHER side of L5 -> opening_against",
      against["opening_against"] == 1 and against["opening_for"] == 0)
base = scorer.score_v3(up, book_lean=0.0, change_1h_pct=1.0,
                       oi_change_1h_pct=None, oi_gap_s=None,
                       funding_rate=0.0, funding_cap=0.0018)
check("opening_against is a halving, not a zero",
      0 < against["score_v3"]
      and abs(against["score_v3"] - 0.5 * base["score_v3"]) <= 0.1,
      f"against={against['score_v3']} base={base['score_v3']}")

print("\n=== bybit.oi_state carries the matched 1h window ===")
orig_get = bybit._get


def fake_oi(rows):
    bybit._get = lambda path, **kw: {"list": rows}


try:
    hour = 3_600_000
    fake_oi([{"openInterest": "104", "timestamp": str(10 * hour)},
             {"openInterest": "100", "timestamp": str(9 * hour)},
             {"openInterest": "80", "timestamp": str(1 * hour)}])
    res = bybit.oi_state("XUSDT")
    pct24, units = res
    check("oi_state still unpacks as a 2-tuple", units == 104.0
          and abs(pct24 - 30.0) < 1e-9, f"{pct24} {units}")
    check("1h change = newest vs previous sample",
          abs(res.detail["oi_change_1h_pct"] - 4.0) < 1e-9, str(res.detail))
    check("gap recorded in seconds", res.detail["oi_gap_s"] == 3600.0)

    fake_oi([{"openInterest": "104", "timestamp": str(10 * hour)},
             {"openInterest": "100", "timestamp": str(8 * hour)}])
    res = bybit.oi_state("XUSDT")
    check("2h sample gap -> 1h change withheld (None)",
          res.detail["oi_change_1h_pct"] is None
          and res.detail["oi_gap_s"] == 7200.0, str(res.detail))

    fake_oi([{"openInterest": "100", "timestamp": str(hour)}])
    res = bybit.oi_state("XUSDT")
    check("single sample -> (None, units) and no 1h change",
          res[0] is None and res.detail["oi_change_1h_pct"] is None)
finally:
    bybit._get = orig_get

print("\n=== scan wiring: 1h OI reaches the shadow score ===")
orig = (scanmod.mexc.klines, scanmod.mexc.depth, scanmod.LIMITER,
        scanmod.STAGGER_S, scanmod.VENUE_STATE, bybit.oi_state,
        dict(scanmod.BYBIT_MAP))
try:
    scanmod.LIMITER = scanmod.RateLimiter(rate=10_000)
    scanmod.STAGGER_S = 0
    scanmod.VENUE_STATE = {}
    scanmod.BYBIT_MAP.clear()
    scanmod.BYBIT_MAP["SHARED"] = "SHAREDUSDT"
    scanmod.mexc.klines = lambda sym, interval="5m", limit=200: breakout_bars(1)
    scanmod.mexc.depth = lambda sym, limit=20: ([(100.9, 100.0)],
                                                [(101.1, 100.0)])
    TK = {"lastPrice": 101.0, "amount24": 5_000_000.0, "riseFallRate": 0.01,
          "fundingRate": 0.0001, "maxFundingRate": 0.0018}
    DETAIL = {"minVol": 1, "contractSize": 0.05}

    class Res(tuple):
        detail = None

    def oi_ok(sym):
        r = Res((12.0, 1000.0))     # 24h change is large and positive
        r.detail = {"oi_change_1h_pct": 3.0, "oi_gap_s": 3600.0}
        return r

    bybit.oi_state = oi_ok
    c = scanmod.analyse_one("SHARED_USDT", "SHARED", DETAIL, TK, 10.0)
    check("card carries the 1h OI change from the same request",
          c is not None and c.oi_change_1h_pct == 3.0,
          str(getattr(c, "oi_change_1h_pct", "no card")))
    check("flow_state uses the 1h pair (opening)", c.flow_state == "opening",
          str(c.flow_state))
    check("v2 OI field keeps the ~24h figure", c.oi_change_pct == 12.0,
          str(c.oi_change_pct))

    bybit.oi_state = lambda sym: (12.0, 1000.0)       # plain 2-tuple (old shape)
    c = scanmod.analyse_one("SHARED_USDT", "SHARED", DETAIL, TK, 10.0)
    check("a plain 2-tuple adapter -> 24h OI present, 1h unavailable",
          c.oi_change_pct == 12.0 and c.flow_state == "unavailable"
          and c.oi_change_1h_pct is None, f"{c.flow_state}")
    check("24h OI alone never makes opening_for", c.opening_for == 0)
finally:
    (scanmod.mexc.klines, scanmod.mexc.depth, scanmod.LIMITER,
     scanmod.STAGGER_S, scanmod.VENUE_STATE, bybit.oi_state) = orig[:6]
    scanmod.BYBIT_MAP.clear()
    scanmod.BYBIT_MAP.update(orig[6])

print("\n=== direction: book / funding / OI never set or flip the side ===")
for bl in (-1.0, -0.6, 0.0, 0.6, 1.0):
    r = scorer.score_v3(up, book_lean=bl, change_1h_pct=1.0,
                        funding_rate=-0.0017, funding_cap=0.0018,
                        oi_change_1h_pct=-9.0, oi_gap_s=GAP)
    check(f"book lean {bl:+.1f}: direction_v3 stays LONG",
          r["direction_v3"] == "LONG", r["direction_v3"])
opp = scorer.score_v3(up, book_lean=-1.0, **{k: v for k, v in GOOD.items()
                                             if k != "book_lean"})
check("opposing book: book_agree 0 (a note, not a penalty)",
      opp["book_agree"] == 0)
conf = scorer.score_v3(up, book_lean=0.5, **{k: v for k, v in GOOD.items()
                                             if k != "book_lean"})
check("confirming book adds exactly 10 inner points (book_agree 1)",
      conf["book_agree"] == 1 and conf["score_v3"] > opp["score_v3"])
weak = scorer.score_v3(up, book_lean=0.2, **{k: v for k, v in GOOD.items()
                                             if k != "book_lean"})
check("book lean must EXCEED 0.20 to confirm", weak["book_agree"] == 0)

print("\n=== funding is a flag, never a score term ===")
f_with = scorer.score_v3(up, **{**GOOD, "funding_rate": 0.0015})
f_none = scorer.score_v3(up, **{**GOOD, "funding_rate": 0.0})
f_against = scorer.score_v3(up, **{**GOOD, "funding_rate": -0.0015})
check("crowd with the side flagged 'with'", f_with["funding_crowd"] == "with")
check("crowd against the side flagged 'against'",
      f_against["funding_crowd"] == "against")
check("sub-50%-of-cap funding is 'neutral'", f_none["funding_crowd"] == "neutral")
check("funding changes the label only, never the score",
      f_with["score_v3"] == f_none["score_v3"] == f_against["score_v3"])

print("\n=== higher-timeframe multiplier and bonus ===")
plain = scorer.score_v3(up, **GOOD)
opp4 = scorer.score_v3(up, **GOOD, lean_4H=-0.5)
check("strong opposed 4H lean x0.70",
      abs(opp4["score_v3"] - round(plain["score_v3"] * 0.7, 1)) <= 0.1,
      f"{opp4['score_v3']} vs {plain['score_v3']}")
weak4 = scorer.score_v3(up, **GOOD, lean_4H=-0.10)
check("weak 4H opposition is not 'strong'", weak4["score_v3"] == plain["score_v3"])
both = scorer.score_v3(up, **GOOD, lean_1H=0.5, lean_4H=0.5)
check("1H and 4H strong and matching: +6",
      abs(both["score_v3"] - min(100.0, plain["score_v3"] + 6.0)) <= 0.1,
      f"{both['score_v3']} vs {plain['score_v3']}")
only4 = scorer.score_v3(up, **GOOD, lean_1H=None, lean_4H=0.5)
check("missing 1H data blocks the +6 (not a zero lean)",
      only4["score_v3"] == plain["score_v3"])
mixed = scorer.score_v3(up, **GOOD, lean_1H=-0.5, lean_4H=0.5)
check("1H opposing does not earn the bonus",
      mixed["score_v3"] == plain["score_v3"])
stack = scorer.score_v3(up, book_lean=0.0, change_1h_pct=-1.0,
                        oi_change_1h_pct=4.0, oi_gap_s=GAP,
                        funding_rate=0.0, funding_cap=0.0018, lean_4H=-0.5)
check("opening_against and 4H opposition stack (0.5 x 0.7)",
      abs(stack["score_v3"] - round(base["score_v3"] * 0.35, 1)) <= 0.15,
      f"{stack['score_v3']} vs base {base['score_v3']}")
cap = scorer.score_v3(up, book_lean=0.9, change_1h_pct=1.0,
                      oi_change_1h_pct=4.0, oi_gap_s=GAP,
                      funding_rate=0.0, funding_cap=0.0018,
                      lean_1H=0.9, lean_4H=0.9)
check("score never exceeds 100", 0.0 <= cap["score_v3"] <= 100.0,
      str(cap["score_v3"]))

print("\n=== mirrored price path: L5 flips, score magnitude matches ===")
for after in (1, 12, 37):
    u = breakout_bars(after)
    d = mirror(u)
    ru = scorer.score_v3(u, book_lean=0.5, change_1h_pct=1.0,
                         oi_change_1h_pct=4.0, oi_gap_s=GAP,
                         funding_rate=0.0, funding_cap=0.0018)
    rd = scorer.score_v3(d, book_lean=-0.5, change_1h_pct=-1.0,
                         oi_change_1h_pct=4.0, oi_gap_s=GAP,
                         funding_rate=0.0, funding_cap=0.0018)
    check(f"after={after}: L5 flips sign",
          ru["parts"]["l5"] > 0 > rd["parts"]["l5"],
          f"{ru['parts']['l5']} {rd['parts']['l5']}")
    check(f"after={after}: directions mirror",
          (ru["direction_v3"], rd["direction_v3"]) == ("LONG", "SHORT"))
    check(f"after={after}: same break age",
          ru["parts"]["age"] == rd["parts"]["age"],
          f"{ru['parts']['age']} {rd['parts']['age']}")
    check(f"after={after}: scores within 12%",
          abs(ru["score_v3"] - rd["score_v3"])
          <= 0.12 * max(ru["score_v3"], 1e-9),
          f"{ru['score_v3']} vs {rd['score_v3']}")
    check(f"after={after}: opening/book credit mirrors",
          (ru["opening_for"], ru["book_agree"])
          == (rd["opening_for"], rd["book_agree"]))

print("\n=== shadow never moves the live v2 card ===")
BID = [(100.0, 50.0), (99.0, 30.0)]
ASK = [(101.0, 50.0), (102.0, 30.0)]
kw = dict(quote_vol_24h=5e6, spread_pct=0.2, change_1h_pct=1.0, price=101.0,
          change_24h_pct=3.0, funding_rate=0.0001, funding_cap=0.0018,
          oi_change_pct=4.0, min_notional=0.01, tier=1,
          lean_1H=0.5, lean_4H=0.5)
a = scorer.score_coin("X", breakout_bars(5), BID, ASK, **kw)
b = scorer.score_coin("X", breakout_bars(5), BID, ASK, **kw,
                      oi_change_1h_pct=-7.0, oi_gap_s=3600)
c2 = scorer.score_coin("X", breakout_bars(5), BID, ASK, **kw,
                       oi_change_1h_pct=7.0, oi_gap_s=99999)
for nm, other in (("1h OI -7", b), ("1h OI +7 bad gap", c2)):
    same = (a.score == other.score and a.lean == other.lean
            and a.direction == other.direction
            and a.earlyness == other.earlyness
            and a.magnitude_parts == other.magnitude_parts
            and a.lean_parts == other.lean_parts
            and a.actionable == other.actionable)
    check(f"v2 score/lean/direction/earlyness identical ({nm})", same)
check("shadow fields populated on a scored card",
      a.score_v3 is not None and a.score_rule == "score-v3-dis"
      and a.direction_v3 in ("LONG", "SHORT", "NEUTRAL"))
thin = scorer.score_coin("T", make_bars([100.0] * 10, [1000.0] * 10), BID, ASK,
                         **kw)
check("too few bars: no shadow score (None, not 0)",
      thin.score_v3 is None and thin.score_rule is None)
check("shadow does not change who is actionable/flaggable",
      a.is_actionable(10.0) == b.is_actionable(10.0))

print("\n=== logging: additive columns, legacy cards write NULLs ===")
tmp = tempfile.mkdtemp()
st = Store(os.path.join(tmp, "t.db"))
cols = {r[1] for r in st.conn.execute("PRAGMA table_info(signal_log)")}
want = {"score_v3", "direction_v3", "initiation", "flow_state",
        "oi_change_1h_pct", "opening_for", "opening_against", "book_agree",
        "funding_crowd", "score_rule"}
check("signal_log has every shadow column", want <= cols, str(want - cols))
a2 = scorer.score_coin("LOGME", breakout_bars(3), BID, ASK, **kw,
                       oi_change_1h_pct=3.0, oi_gap_s=3600)
sid = st.log_signal(a2, flagged=False, tier=2)
row = st.conn.execute(
    "SELECT score, flagged, flag_version, score_v3, direction_v3, initiation,"
    " flow_state, oi_change_1h_pct, opening_for, opening_against, book_agree,"
    " funding_crowd, score_rule FROM signal_log WHERE id=?", (sid,)).fetchone()
check("v2 columns still written as before",
      row[0] == a2.score and row[1] == 0 and row[2] == 2)
check("shadow columns written", row[3] == a2.score_v3
      and row[4] == a2.direction_v3 and row[5] == a2.initiation
      and row[6] == "opening" and row[7] == 3.0 and row[8] == a2.opening_for
      and row[9] == a2.opening_against and row[10] == a2.book_agree
      and row[11] == a2.funding_crowd and row[12] == "score-v3-dis", str(row))


class Legacy:
    """A scorecard from before the shadow fields existed."""
    coin, venue, score, lean, direction, earlyness = "OLD", "MEXC", 30.0, .4, "LONG", .5
    magnitude_parts, lean_parts = {}, {}
    price = change_24h_pct = quote_vol_24h = spread_pct = funding_rate = 0.0
    min_notional, vetoes, tradeable, oi_notional, oi_change_pct = 1.0, [], True, None, None


sid = st.log_signal(Legacy(), flagged=False, tier=2)
row = st.conn.execute("SELECT score_v3, score_rule FROM signal_log WHERE id=?",
                      (sid,)).fetchone()
check("a card without shadow fields logs NULLs, not an error",
      row == (None, None), str(row))

print("\n=== migration: an older DB gains the columns, rows untouched ===")
old_path = os.path.join(tmp, "old.db")
con = sqlite3.connect(old_path)
con.executescript("""
CREATE TABLE signal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
    coin TEXT NOT NULL, venue TEXT NOT NULL DEFAULT 'MEXC',
    flagged INTEGER NOT NULL DEFAULT 1, score REAL, lean REAL, direction TEXT,
    earlyness REAL, mag_vol REAL, mag_book REAL, mag_oi REAL, lean_vol REAL,
    lean_book REAL, lean_oi REAL, price REAL, change_24h_pct REAL,
    quote_vol_24h REAL, spread_pct REAL, funding_rate REAL, min_notional REAL,
    veto_codes TEXT, tradeable INTEGER NOT NULL DEFAULT 1,
    tier INTEGER NOT NULL DEFAULT 2, flag_version INTEGER DEFAULT 1,
    oi_notional REAL, oi_change_pct REAL);
INSERT INTO signal_log (ts, coin, score, direction) VALUES (1, 'KEEP', 27.5, 'LONG');
""")
con.commit()
con.close()
st2 = Store(old_path)
cols2 = {r[1] for r in st2.conn.execute("PRAGMA table_info(signal_log)")}
check("old DB gains every shadow column", want <= cols2, str(want - cols2))
kept = st2.conn.execute(
    "SELECT score, direction, score_v3, score_rule FROM signal_log").fetchone()
check("old rows keep their values and get NULL shadow fields",
      kept == (27.5, "LONG", None, None), str(kept))
Store(old_path)    # idempotent on reopen
check("reopening is idempotent", True)

print("\n=== GUI model: labeled shadow column, detail lines, saved rows ===")
from gui import model as gmodel

check("SHADOW column text: score with its own side arrow",
      gmodel.format_shadow(a2) == f"{a2.score_v3:.1f} "
      f"{gmodel.direction_arrow(a2.direction_v3)}", gmodel.format_shadow(a2))
check("no shadow computed -> en dash", gmodel.format_shadow(thin) == "–")
check("a legacy card object -> en dash", gmodel.format_shadow(Legacy()) == "–")
lines = gmodel.shadow_lines(a2)
check("detail block is labeled as not used for ranking or flags",
      lines and "score-v3-dis" in lines[0] and "not used" in lines[0],
      str(lines[:1]))
check("detail block shows initiation and flow state",
      "initiation" in lines[1] and "flow opening" in lines[1], lines[1])
check("no detail block without a shadow score", gmodel.shadow_lines(thin) == [])
txt = gmodel.detail_text(a2, stake=10.0)
check("detail_text includes the shadow block", "SHADOW SCORE" in txt)
check("detail_text unchanged for a card without a shadow",
      "SHADOW SCORE" not in gmodel.detail_text(thin, stake=10.0))
st.conn.row_factory = sqlite3.Row
saved = [dict(r) for r in st.conn.execute(
    "SELECT * FROM signal_log WHERE coin='LOGME'")]
rebuilt = gmodel.snapshot_cards(saved)
check("saved rows rebuild the shadow fields (relaunch keeps the column)",
      rebuilt and rebuilt[0].score_v3 == a2.score_v3
      and rebuilt[0].flow_state == "opening", str(rebuilt and rebuilt[0].score_v3))
check("live rank is untouched: sort keys do not include the shadow",
      "shadow" not in __import__("proto.app", fromlist=["x"]).SORT_KEYS
      and "v3" not in __import__("proto.app", fromlist=["x"]).SORT_KEYS)

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
