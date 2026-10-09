"""Collector tests: dedupe, vol+amount preserved, full-universe coverage.

Contract (plan Task 5 / spec SS5 "Collector coverage"): the collector covers
EVERY universe coin regardless of the 150-coin scan rotation, via an uncapped
build_universe(stake=inf, budget=None).
"""
import os, sys, tempfile, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proto import collector as col
FAILURES=[]
def check(n,c,d=""):
    print(f"  {'PASS' if c else 'FAIL'}  {n}"+("" if c else f"  {d}"))
    if not c: FAILURES.append(n)
tmp=tempfile.mkdtemp()
bars=[{"ts":100+i,"o":1.0,"h":1.1,"l":0.9,"c":1.0+i*0.01,"vol":10.0,"amount":10.0+i*0.1} for i in range(5)]
n1=col.append_bars(tmp,"TEST",bars)
n2=col.append_bars(tmp,"TEST",bars)
check("first write 5", n1==5, str(n1))
check("rewrite dedupes 0 new", n2==0, str(n2))
rows=col.read_bars(tmp,"TEST")
check("read back 5", len(rows)==5, str(len(rows)))
check("vol+amount present", all("vol" in r and "amount" in r for r in rows))


def test_collect_full_universe_covers_every_coin():
    """collect_full_universe(data_dir) -> counts for EVERY universe coin,
    not the 150-coin rotation: build_universe called with stake=inf,
    budget=None (uncapped), bars fetched and persisted per coin."""
    print("=== test_collect_full_universe_covers_every_coin ===")
    from proto import mexc as mexcmod
    from proto import scan as scanmod
    N = 200                      # venue universe; the scan rotation caps at 150
    tdir = tempfile.mkdtemp()
    uni_calls = []
    started = threading.Barrier(8)
    orig = (scanmod.build_universe, mexcmod.klines)

    def fake_build_universe(stake, budget=150, store=None):
        uni_calls.append((stake, budget))
        return [(f"COIN{i:03d}_USDT", f"COIN{i:03d}") for i in range(N)]

    def fake_klines(sym, interval="5m", limit=200):
        started.wait(timeout=3)
        return [{"ts": 1700000000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.0,
                 "vol": 10.0, "amount": 10.0}]

    scanmod.build_universe = fake_build_universe
    mexcmod.klines = fake_klines
    try:
        counts = col.collect_full_universe(tdir, limit_per_coin=1)
    finally:
        scanmod.build_universe, mexcmod.klines = orig

    check("build_universe called uncapped: stake=inf, budget=None",
          uni_calls == [(float("inf"), None)], str(uni_calls))
    check(f"counts covers all {N} coins (no 150 rotation cap)",
          len(counts) == N, str(len(counts)))
    check("coin beyond the 150 rotation still collected",
          counts.get("COIN199") == 1, str(counts.get("COIN199")))
    check("every coin has its bars on disk",
          all(len(col.read_bars(tdir, f"COIN{i:03d}")) == 1 for i in range(N)))


def run(fn):
    print(f"--- {fn.__name__}")
    try:
        fn()
    except Exception as e:
        print(f"  FAIL  {fn.__name__}  raised {type(e).__name__}: {e}")
        FAILURES.append(fn.__name__)


run(test_collect_full_universe_covers_every_coin)

print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED")
sys.exit(1 if FAILURES else 0)
