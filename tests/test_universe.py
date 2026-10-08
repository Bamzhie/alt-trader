"""Stake-aware universe rotation tests: disjoint groups, shortfall spill,
rotation pointer persistence. Offline: fake tickers/details fixtures only.

Fixture naming is deliberately INVERTED against volume (COIN199 is the most
liquid coin), so an implementation that sorts by symbol instead of 24h volume
produces visibly wrong groups.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import scan as scanmod
from proto.store import Store

FAILURES = []

N = 200
STAKE = 0.10


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def C(i):
    return f"COIN{i:03d}"


def make_fixture(tradeable_count):
    """200 fake coins. Rank 0 = highest 24h volume, named COIN199 (inverted).
    The first `tradeable_count` coins by volume have min_notional $0.05
    (tradeable at $0.10 stake); the rest are $5.00 (not tradeable)."""
    tk, det = {}, {}
    for rank in range(N):
        sym = C(N - 1 - rank) + "_USDT"
        tk[sym] = {"lastPrice": 1.0, "amount24": float((N - rank) * 1_000_000),
                   "riseFallRate": 0.01, "fundingRate": 0.0}
        det[sym] = {"minVol": 1,
                    "contractSize": 0.05 if rank < tradeable_count else 5.0}
    return tk, det


def install(tk, det, bybit_set=None):
    """Patch scan's venue feeds. Returns a restore callable."""
    orig = (scanmod.mexc.tickers, scanmod.mexc.details, scanmod._bybit_symbol_set)
    scanmod.mexc.tickers = lambda: tk
    scanmod.mexc.details = lambda: det
    scanmod._bybit_symbol_set = lambda: bybit_set

    def restore():
        (scanmod.mexc.tickers, scanmod.mexc.details,
         scanmod._bybit_symbol_set) = orig
    return restore


def test_groups_disjoint_and_fill_150():
    print("=== 80 tradeable + 40 tail + 30 rotation fill 150, disjoint ===")
    tk, det = make_fixture(tradeable_count=100)
    restore = install(tk, det, bybit_set=None)
    try:
        uni = scanmod.build_universe(STAKE)
    finally:
        restore()

    coins = [c for _, c in uni]
    check("len == 150", len(uni) == 150, str(len(uni)))
    check("no duplicate coins", len(coins) == len(set(coins)),
          f"{len(coins)} vs {len(set(coins))}")

    # Group 1: 80 stake-tradeable coins, ranked by 24h volume (rank 0 first).
    check("group1 = 80 tradeable, vol-ranked",
          coins[:80] == [C(i) for i in range(199, 119, -1)], str(coins[:80]))
    ok = True
    for sym, coin in uni[:80]:
        n = scanmod.mexc.min_notional(sym, det[sym], tk[sym]["lastPrice"])
        ok = ok and n is not None and n <= STAKE
    check("group1 min_notional <= stake", ok)

    # Group 2: tail = the 40 lowest-volume coins (no Bybit map available).
    check("group2 = tail, 40 lowest-vol slice",
          coins[80:120] == [C(i) for i in range(40)], str(coins[80:120]))

    # Group 3: rotation = 30 from the remainder at pointer 0, disjoint.
    check("group3 = rotation 30 from remainder",
          coins[120:150] == [C(i) for i in range(119, 89, -1)],
          str(coins[120:150]))
    check("groups pairwise disjoint",
          len(set(coins[:80]) & set(coins[80:120])) == 0
          and len(set(coins[:80]) & set(coins[120:150])) == 0
          and len(set(coins[80:120]) & set(coins[120:150])) == 0)


def test_shortfall_spills():
    print("=== only 10 tradeable -> shortfall spills, still 150 ===")
    tk, det = make_fixture(tradeable_count=10)
    restore = install(tk, det, bybit_set=None)
    try:
        uni = scanmod.build_universe(STAKE)
    finally:
        restore()

    coins = [c for _, c in uni]
    check("len == 150", len(uni) == 150, str(len(uni)))
    check("no duplicate coins", len(coins) == len(set(coins)),
          f"{len(coins)} vs {len(set(coins))}")
    check("group1 = all 10 tradeable",
          coins[:10] == [C(i) for i in range(199, 189, -1)], str(coins[:10]))
    ok = True
    for sym, coin in uni[:10]:
        n = scanmod.mexc.min_notional(sym, det[sym], tk[sym]["lastPrice"])
        ok = ok and n is not None and n <= STAKE
    check("group1 min_notional <= stake", ok)
    check("group2 = tail 40 lowest-vol",
          coins[10:50] == [C(i) for i in range(40)], str(coins[10:50]))
    check("group3 = rotation 30",
          coins[50:80] == [C(i) for i in range(189, 159, -1)],
          str(coins[50:80]))
    check("spill = 70 more from remainder",
          coins[80:150] == [C(i) for i in range(159, 89, -1)],
          str(coins[80:150]))


def test_rotation_pointer_persists_and_advances():
    print("=== rotation pointer advances and survives reopen ===")
    db = os.path.join(tempfile.mkdtemp(), "uni.db")
    st = Store(db)
    check("fresh pointer == 0", scanmod.rotation_pointer(st) == 0,
          str(scanmod.rotation_pointer(st)))

    tk, det = make_fixture(tradeable_count=100)
    restore = install(tk, det, bybit_set=None)
    try:
        uni1 = scanmod.build_universe(STAKE, store=st)
        check("cycle1 fills 150", len(uni1) == 150, str(len(uni1)))
        check("cycle1 pointer == 30 (rotation window consumed)",
              scanmod.rotation_pointer(st) == 30,
              str(scanmod.rotation_pointer(st)))
        check("cycle1 rotation window",
              [c for _, c in uni1[120:150]] == [C(i) for i in range(119, 89, -1)])

        uni2 = scanmod.build_universe(STAKE, store=st)
        check("cycle2 pointer == 60", scanmod.rotation_pointer(st) == 60,
              str(scanmod.rotation_pointer(st)))
        check("cycle2 rotation window moved",
              [c for _, c in uni2[120:150]] == [C(i) for i in range(89, 59, -1)],
              str([c for _, c in uni2[120:150]]))
        check("cycle2 differs from cycle1", uni2 != uni1)
        check("cycle2 still disjoint, 150",
              len(uni2) == 150 and len({c for _, c in uni2}) == 150)
    finally:
        restore()

    check("advance_rotation adds consumed",
          scanmod.advance_rotation(st, 5) == 65
          and scanmod.rotation_pointer(st) == 65,
          str(scanmod.rotation_pointer(st)))
    st.close()

    st2 = Store(db)
    check("pointer survives reopen", scanmod.rotation_pointer(st2) == 65,
          str(scanmod.rotation_pointer(st2)))
    st2.close()


def test_tail_prefers_mexc_only_when_bybit_map_available():
    print("=== tail = MEXC-only coins when the Bybit map answers ===")
    tk, det = make_fixture(tradeable_count=100)
    # Bybit lists everything EXCEPT COIN040..COIN079 -> those 40 are MEXC-only.
    bybit_set = {C(i) for i in range(N)} - {C(i) for i in range(40, 80)}
    restore = install(tk, det, bybit_set=bybit_set)
    try:
        uni = scanmod.build_universe(STAKE)
    finally:
        restore()

    coins = [c for _, c in uni]
    check("len == 150", len(uni) == 150, str(len(uni)))
    check("no duplicate coins", len(coins) == len(set(coins)))
    check("tail = the 40 MEXC-only coins",
          coins[80:120] == [C(i) for i in range(40, 80)],
          str(coins[80:120]))
    # Fallback would have picked the lowest-volume coin (COIN000, Bybit-listed).
    check("lowest-vol Bybit coin NOT in tail", C(0) not in coins[80:120],
          str(coins[80:120]))
    check("groups disjoint",
          len(set(coins[:80]) & set(coins[80:120])) == 0
          and len(set(coins[:80]) & set(coins[120:150])) == 0
          and len(set(coins[80:120]) & set(coins[120:150])) == 0)


def test_missing_detail_row_fails_closed():
    print("=== coin with no detail row: unknown min_notional, not tradeable ===")
    tk, det = make_fixture(tradeable_count=100)
    del det[C(199) + "_USDT"]           # most liquid coin has no detail row
    restore = install(tk, det, bybit_set=None)
    try:
        uni = scanmod.build_universe(STAKE)
    finally:
        restore()

    coins = [c for _, c in uni]
    check("no crash, len == 150", len(uni) == 150, str(len(uni)))
    check("no duplicate coins", len(coins) == len(set(coins)))
    check("unknown-minimum coin excluded from tradeable group",
          C(199) not in coins[:80], str(coins[:80]))
    check("but still covered by a group", C(199) in coins)


def run(fn):
    print(f"--- {fn.__name__}")
    try:
        fn()
    except Exception as e:
        print(f"  FAIL  {fn.__name__}  raised {type(e).__name__}: {e}")
        FAILURES.append(fn.__name__)


for t in (test_groups_disjoint_and_fill_150,
          test_shortfall_spills,
          test_rotation_pointer_persists_and_advances,
          test_tail_prefers_mexc_only_when_bybit_map_available,
          test_missing_detail_row_fails_closed):
    run(t)

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
