"""Shadow-only contract: the embedded watcher always records SHADOW.

Self-checking script: `python3 tests/test_quant_shadow_only.py`.
A passed promotion in promotions.json is a research fact — it must never
switch live watcher emission to ACTIONABLE (that requires a separately
approved stage). Covers: promoted rules still emit SHADOW-only rows.
"""

import dataclasses
import gzip
import math
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.quant import engine as E
from proto.quant import gates as gatesmod
from proto.quant import qstore
from proto.quant.config import BAR_S, DEFAULT
from proto.quant.registry import REGISTERED

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


T0 = 1_700_000_000 - (1_700_000_000 % BAR_S)
K = 8 * 288


def make_series(n, seed, sigma, amt, plant=None):
    rng = random.Random(seed)
    px, out = 100.0, []
    for i in range(n):
        o = px
        r = rng.gauss(0, sigma)
        if plant and i == plant[0]:
            r = plant[1]
        px = o * math.exp(r)
        a = amt * (0.7 + 0.6 * rng.random())
        if plant and i == plant[0]:
            a *= plant[2]
        out.append((T0 + i * BAR_S, o, max(o, px) * 1.0005,
                    min(o, px) * 0.9995, px, a / px, a))
    return out


def write(dirp, coin, rows, mode="wt"):
    p = os.path.join(dirp, coin + ".csv.gz")
    with gzip.open(p, mode, newline="") as f:
        if mode == "wt":
            f.write("ts,o,h,l,c,vol,amount\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")


def relaxed(cfg=DEFAULT):
    s = dataclasses.replace(cfg.selector, cooldown_bars=0)
    return dataclasses.replace(cfg, selector=s)


def planted_series(n, k, sigma, amt, jump=0.04, vol_mult=40, seed0=2):
    """Series whose bar k exits a channel with volume: emits under relaxed()."""
    from proto.quant.stream import FeatureStream
    for seed in range(seed0, seed0 + 200):
        rows = make_series(n, seed, sigma, amt, plant=(k, jump, vol_mult))
        st = FeatureStream(DEFAULT.feat)
        snap = None
        for r in rows[:k + 1]:
            snap = st.update(r[0], r[1], r[2], r[3], r[4], r[6], 0.0)
        if snap is not None and snap.up_start and snap.rvol and snap.rvol >= 4:
            return rows
    raise RuntimeError("no suitable seed")


print("=== promoted rules still emit SHADOW-only ===")
d = tempfile.mkdtemp()
bars = os.path.join(d, "bars")
os.makedirs(bars)
cfg = relaxed()

btc = make_series(K + 400, 1, 0.001, 5e6)
aaa = planted_series(K + 400, K, 0.002, 20000)
write(bars, "BTC", btc[:K])
write(bars, "AAA", aaa[:K])
clock = [btc[K - 1][0] + BAR_S]
st = qstore.QuantStore(os.path.join(d, "q.db"))
w = E.Watcher(bars, st, cfg, now_fn=lambda: clock[0])

# Pass every registered rule for this exact config: a research fact that
# must NOT switch live emission to ACTIONABLE.
for spec in REGISTERED:
    gatesmod.record(os.path.dirname(st.path), spec.rule_id,
                    cfg.config_hash(), True, ["fixture pass"],
                    {"n": 250, "days": 30, "coins": 20, "mean_r": 0.15,
                     "ci_lo": 0.12, "ci_hi": 0.18})
r = w.step()
check("start-up replays history without emitting",
      r["emitted"] == 0 and r["ok"])
write(bars, "BTC", btc[K:K + 1], "at")
write(bars, "AAA", aaa[K:K + 1], "at")
clock[0] = btc[K][0] + BAR_S
r = w.step()
evs = st.all_events()
check("promoted watcher emits events", len(evs) > 0,
      "emitted=%s" % r["emitted"])
modes = sorted({e["mode"] for e in evs})
check("every emitted row is SHADOW despite passed promotions",
      modes == ["SHADOW"], str(modes))
n_actionable = st.conn.execute(
    "SELECT COUNT(*) FROM quant_event WHERE mode='ACTIONABLE'").fetchone()[0]
check("zero ACTIONABLE rows persisted", n_actionable == 0, str(n_actionable))
st.close()

print()
if FAILURES:
    print("FAIL  tests/test_quant_shadow_only.py  (%s)" % FAILURES)
    sys.exit(1)
print("ALL PASS  tests/test_quant_shadow_only.py")
