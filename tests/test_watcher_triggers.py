"""Watcher triggers T1-break + CUSUM-raw + H freeze (Task 5).

Self-checking script: `python3 tests/test_watcher_triggers.py`.
Covers vetoes_ok boundaries, freeze_H grid, t1_candidate gates
(age<=6, L5>0.15 LONG, BTC drift>+5%, vetoes, closed-bar gate,
stale/missing funding still emits with funding=unavailable),
cusum_candidate long/short mirror + vetoes.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import watcher

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def flat_bar(ts, px=100.0, vol=1000.0):
    return {"ts": ts, "o": px, "h": px + 0.5, "l": px - 0.5, "c": px,
            "vol": vol, "amount": vol * px}


def rising_bars(n_flat=49, n_rise=7, step=1.0):
    """Flat channel then a steady grind up. Breakout run length == n_rise,
    so age == n_rise - 1."""
    bars, ts, px = [], 1_700_000_000, 100.0
    for _ in range(n_flat):
        bars.append(flat_bar(ts)); ts += 300
    for _ in range(n_rise):
        px += step
        bars.append({"ts": ts, "o": px - step, "h": px + 0.1, "l": px - step,
                     "c": px, "vol": 1000.0, "amount": 1000.0 * px})
        ts += 300
    return bars


def falling_bars(n_flat=49, n_fall=7, step=1.0):
    bars, ts, px = [], 1_700_000_000, 100.0
    for _ in range(n_flat):
        bars.append(flat_bar(ts)); ts += 300
    for _ in range(n_fall):
        px -= step
        bars.append({"ts": ts, "o": px + step, "h": px + step, "l": px - 0.1,
                     "c": px, "vol": 1000.0, "amount": 1000.0 * px})
        ts += 300
    return bars


def drift_closes(pct):
    closes = [100.0] * 2016
    closes[-1] = 100.0 * (1.0 + pct)
    return closes


def t1_ctx(**kw):
    bars = kw.pop("bars", rising_bars(n_rise=7))  # age 6
    now = bars[-1]["ts"] + 300
    ctx = {"coin": "DOGE", "bars": bars, "now": now,
           "src_bar_ts": bars[-1]["ts"], "entry": bars[-1]["c"],
           "btc_closes": drift_closes(0.051),
           "quote_vol_24h": 500_000.0, "spread_pct": 0.1,
           "move_1h_pct": 1.0, "move_24h_pct": 5.0,
           "funding_state": "ok"}
    ctx.update(kw)
    return ctx


print("=== vetoes_ok ===")
check("clean pass -> []", watcher.vetoes_ok(500_000, 0.1, 1.0, 5.0) == [])
check("vol floor 250k passes", watcher.vetoes_ok(250_000, 0.1, 1.0, 5.0) == [])
check("vol 249k breaches named low_volume",
      any("low_volume" in r or "volume" in r for r in watcher.vetoes_ok(249_000, 0.1, 1.0, 5.0)),
      str(watcher.vetoes_ok(249_000, 0.1, 1.0, 5.0)))
check("spread 3.0 passes", watcher.vetoes_ok(500_000, 3.0, 1.0, 5.0) == [])
check("spread 3.1 breaches named wide_spread",
      any("spread" in r for r in watcher.vetoes_ok(500_000, 3.1, 1.0, 5.0)),
      str(watcher.vetoes_ok(500_000, 3.1, 1.0, 5.0)))
check("1h 11.9 passes", watcher.vetoes_ok(500_000, 0.1, 11.9, 5.0) == [])
check("1h 12.0 breaches named late_move",
      any("late_move" in r or "1h" in r for r in watcher.vetoes_ok(500_000, 0.1, 12.0, 5.0)),
      str(watcher.vetoes_ok(500_000, 0.1, 12.0, 5.0)))
check("24h 34.9 passes", watcher.vetoes_ok(500_000, 0.1, 1.0, 34.9) == [])
check("24h 35.0 breaches named late_move",
      any("late_move" in r or "24h" in r for r in watcher.vetoes_ok(500_000, 0.1, 1.0, 35.0)),
      str(watcher.vetoes_ok(500_000, 0.1, 1.0, 35.0)))
check("all four breach at once",
      len(watcher.vetoes_ok(249_000, 3.1, 12.0, 35.0)) == 4,
      str(watcher.vetoes_ok(249_000, 3.1, 12.0, 35.0)))

print("\n=== freeze_H ===")
check("smallest H with <=2500/day",
      watcher.freeze_H({3: 5000.0, 4: 2400.0, 5: 1000.0, 6: 300.0}) == 4.0)
check("H=3 qualifies -> 3.0",
      watcher.freeze_H({3: 100.0, 4: 50.0, 5: 10.0, 6: 1.0}) == 3.0)
check("none qualify -> tightest 6.0",
      watcher.freeze_H({3: 9000.0, 4: 8000.0, 5: 5000.0, 6: 3000.0}) == 6.0)
check("boundary 2500 qualifies",
      watcher.freeze_H({3: 2500.0, 4: 100.0, 5: 100.0, 6: 100.0}) == 3.0)

print("\n=== t1_candidate ===")
s = watcher.t1_candidate(t1_ctx())
check("age 6 fires", s is not None, str(getattr(watcher.t1_candidate, "last_reason", "")))
check("rule_id t1-break-v1", s is not None and s["rule_id"] == "t1-break-v1", str(s))
check("id rule:coin:src_bar_ts",
      s is not None and s["id"] == f"t1-break-v1:DOGE:{t1_ctx()['src_bar_ts']}", str(s))
check("direction LONG", s is not None and s["direction"] == "LONG", str(s))

s7 = watcher.t1_candidate(t1_ctx(bars=rising_bars(n_rise=8)))  # age 7
# NOTE: src_bar_ts/now must follow the longer bars; t1_ctx rebuilds them.
check("age 7 blocked",
      s7 is None and "age" in (watcher.t1_candidate.last_reason or ""), str(s7))

check("drift +4.9% blocks",
      watcher.t1_candidate(t1_ctx(btc_closes=drift_closes(0.049))) is None)
check("drift +5.1% passes",
      watcher.t1_candidate(t1_ctx(btc_closes=drift_closes(0.051))) is not None)
check("drift missing (<2016 bars) blocks, no default",
      watcher.t1_candidate(t1_ctx(btc_closes=[100.0] * 2015)) is None)
check("short L5 never T1",
      watcher.t1_candidate(t1_ctx(bars=falling_bars())) is None)

blocked = watcher.t1_candidate(t1_ctx(spread_pct=3.1))
check("veto breach blocks with named reason",
      blocked is None and "spread" in (watcher.t1_candidate.last_reason or ""),
      str(blocked))

forming = t1_ctx()
forming["now"] = forming["src_bar_ts"] + 299  # src bar still forming
check("forming src bar -> unavailable, never a candidate",
      watcher.t1_candidate(forming) is None)

stale = watcher.t1_candidate(t1_ctx(funding_state="stale"))
check("stale funding still emits with funding=unavailable",
      stale is not None and stale.get("funding") == "unavailable", str(stale))
missing = watcher.t1_candidate(t1_ctx(funding_state="missing"))
check("missing funding still emits with funding=unavailable",
      missing is not None and missing.get("funding") == "unavailable", str(missing))

print("\n=== cusum_candidate ===")


def cus_ctx(**kw):
    bars = rising_bars()
    now = bars[-1]["ts"] + 300
    ctx = {"coin": "DOGE", "now": now, "src_bar_ts": bars[-1]["ts"],
           "entry": bars[-1]["c"], "side": "LONG", "s_value": 4.5, "H": 4.0,
           "quote_vol_24h": 500_000.0, "spread_pct": 0.1,
           "move_1h_pct": 1.0, "move_24h_pct": 5.0, "funding_state": "ok"}
    ctx.update(kw)
    return ctx


cl = watcher.cusum_candidate(cus_ctx())
check("CUSUM long fires", cl is not None, str(getattr(watcher.cusum_candidate, "last_reason", "")))
check("rule_id cusum-raw-v1",
      cl is not None and cl["rule_id"] == "cusum-raw-v1", str(cl))
cs = watcher.cusum_candidate(cus_ctx(side="SHORT"))
check("CUSUM short mirrors (fires SHORT)",
      cs is not None and cs["direction"] == "SHORT", str(cs))
check("long/short ids share rule:coin:ts shape",
      cl is not None and cs is not None
      and cl["id"].split(":")[0] == "cusum-raw-v1"
      and cs["id"].split(":")[0] == "cusum-raw-v1")
check("s <= H blocks",
      watcher.cusum_candidate(cus_ctx(s_value=4.0)) is None)
check("CUSUM veto breach blocks",
      watcher.cusum_candidate(cus_ctx(quote_vol_24h=249_000)) is None
      and "volume" in (watcher.cusum_candidate.last_reason or ""))
check("CUSUM forming bar blocks",
      watcher.cusum_candidate(cus_ctx(now=cus_ctx()["src_bar_ts"] + 299)) is None)
cf = watcher.cusum_candidate(cus_ctx(funding_state="stale"))
check("CUSUM stale funding still emits unavailable",
      cf is not None and cf.get("funding") == "unavailable", str(cf))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
