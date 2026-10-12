"""replay tests: sampling, signing, regimes. Offline: synthetic bars only."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import replay as rpmod

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def mk_bars(closes, vol=1000.0, start=1_700_000_000, step=300):
    out = []
    for i, cl in enumerate(closes):
        op = closes[i - 1] if i else cl
        out.append({"ts": start + i * step, "o": op, "h": cl * 1.004,
                    "l": cl * 0.996, "c": cl, "vol": vol,
                    "amount": vol * cl})
    return out


# steady climb: lean must go LONG, forward signed returns positive by construction
up = [100 * (1.002 ** i) for i in range(400)]
res = list(rpmod.replay_coin(mk_bars(up), step=48))
check("rising path yields LONG samples", len(res) > 0 and
      all(lean > 0 for _, lean, _, _ in res), str(len(res)))
check("signed 4h returns positive on a steady climb",
      all(r["4h"] is not None and r["4h"] > 0 for _, _, _, r in
          [x for x in res if x[3]["4h"] is not None]),
      str([x[3]["4h"] for x in res][:3]))

# mirror: lean flips, magnitude of signed returns mirrors
dn = [1.0 / c * 10000 for c in up]
res_dn = list(rpmod.replay_coin(mk_bars(dn), step=48))
check("mirror path leans SHORT", len(res_dn) > 0 and
      all(lean < 0 for _, lean, _, _ in res_dn))
r_up = [x[3]["4h"] for x in res if x[3]["4h"] is not None]
r_dn = [x[3]["4h"] for x in res_dn if x[3]["4h"] is not None]
check("signed returns mirror (same sign, magnitude within 15%)",
      len(r_up) == len(r_dn) and len(r_up) > 0
      and all(a > 0 and b > 0 and abs(a - b) / max(a, 1e-9) < 0.15
              for a, b in zip(r_up, r_dn)),
      f"{r_up[:2]} vs {r_dn[:2]}")

# flat: nothing directional -> no samples
flat = [100.0] * 400
check("flat path yields no samples",
      list(rpmod.replay_coin(mk_bars(flat), step=48)) == [])

# short history yields nothing, never crashes
check("short history safe", list(rpmod.replay_coin(mk_bars(up[:30]))) == [])

# regimes from BTC drift
btc = [(1_700_000_000 + i * 300, 100 * (1.001 ** i)) for i in range(2500)]
check("up regime", rpmod.regime_at(btc, 1_700_000_000 + 2499 * 300) == "up")
check("chop regime",
      rpmod.regime_at([(t, 100.0) for t, _ in btc], 1_700_000_000 + 2499 * 300)
      == "chop")
check("unknown on empty", rpmod.regime_at([], 1_700_000_000) == "unknown")

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
