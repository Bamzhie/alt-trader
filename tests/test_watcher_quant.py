"""Watcher quant pure-function tests (Task 2).

Self-checking script: check()/FAILURES/ALL PASS pattern per
tests/test_score_v3.py. Run: python3 tests/test_watcher_quant.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import scorer
from proto import watcher as w

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def approx(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) <= tol


DAY = 86400
T0 = 1_700_000_000 - (1_700_000_000 % DAY)  # a UTC midnight


def bar(ts, o, h, l, c, vol=1000.0):
    return {"ts": ts, "o": o, "h": h, "l": l, "c": c, "vol": vol,
            "amount": vol * c}


print("=== gk_vol: hand-calc on 3 fixed bars ===")
bars3 = [
    bar(1000, 100.0, 101.0, 99.0, 100.5),
    bar(1300, 100.5, 102.0, 100.0, 101.0),
    bar(1600, 101.0, 101.0, 101.0, 101.0),  # zero range -> 0
]
gks = w.gk_vol(bars3)
want0 = 0.5 * math.log(101.0 / 99.0) ** 2 - (2 * math.log(2) - 1) * math.log(100.5 / 100.0) ** 2
want1 = 0.5 * math.log(102.0 / 100.0) ** 2 - (2 * math.log(2) - 1) * math.log(101.0 / 100.5) ** 2
check("gk bar 0 hand value", approx(gks[0], want0, 1e-12), f"{gks[0]} vs {want0}")
check("gk bar 1 hand value", approx(gks[1], want1, 1e-12), f"{gks[1]} vs {want1}")
check("gk zero-range bar is 0.0", gks[2] == 0.0, str(gks[2]))
check("gk floored at 0 (never negative)", all(g is None or g >= 0.0 for g in gks), str(gks))
check("gk length matches bars", len(gks) == 3, str(len(gks)))

print("\n=== ewma: seed = mean of first 24 ===")
vals = [float(i + 1) for i in range(30)]  # 1..30
e = w.ewma(vals, span=24)
check("first 23 outputs are None", all(v is None for v in e[:23]), str(e[:5]))
check("seed is mean(1..24)=12.5", approx(e[23], 12.5), str(e[23]))
lam = 2.0 / 25.0
want24 = lam * 25.0 + (1 - lam) * 12.5
check("first step after seed", approx(e[24], want24), f"{e[24]} vs {want24}")
check("None input -> None output at that position",
      w.ewma([None, 4.0, 6.0], span=2)[0] is None, "")
check("seed skips leading Nones (mean of first 24 valid)",
      approx(w.ewma([None] + vals, span=24)[24], 12.5),
      str(w.ewma([None] + vals, span=24)[24]))
e2 = w.ewma([None, 4.0, 6.0], span=2)
check("span=2 seed = mean(4,6)=5.0", approx(e2[2], 5.0), str(e2))

print("\n=== tod_profile: prior-only, <30 samples absent ===")
# 14 prior days of 5m bars would be big; use sparse synthetic timestamps.
# Bucket b of ts = (ts // 1800) % 48. Pick two buckets and control occupancy.
def ts_at(day, bucket, k=0):
    # a ts inside `day` (days since T0) falling in half-hour bucket `bucket`
    base = T0 + day * DAY
    return base + bucket * 1800 + k * 7  # +k*7s keeps same bucket for small k


prior_ts, prior_vals = [], []
for day in range(14):
    for i in range(40):  # bucket 5: 40 samples/day -> plenty
        prior_ts.append(ts_at(day, 5, i))
        prior_vals.append(10.0)
    for i in range(40):  # other buckets: baseline 5.0 (majority)
        for bkt in (20, 21, 22):
            prior_ts.append(ts_at(day, bkt, i))
            prior_vals.append(5.0)
# Today (day 14): extreme values that must NOT leak into the profile.
today_ts = [ts_at(14, 5, i) for i in range(40)]
today_vals = [1000.0] * 40
prof = w.tod_profile(prior_ts + today_ts, prior_vals + today_vals)
med_all = 5.0  # overwhelming majority are 5.0
check("bucket 5 ratio = 10/5 = 2.0 (today excluded)",
      approx(prof.get(5), 10.0 / med_all), str(prof.get(5)))
check("bucket 20 ratio = 1.0", approx(prof.get(20), 1.0), str(prof.get(20)))
check("bucket with <30 samples is absent (not 1.0 default)",
      33 not in prof and prof.get(33) is None, str(prof.get(33, "absent-ok")))
thin_ts, thin_vals = [], []
for i in range(10):  # bucket 5: only 10 prior samples -> absent
    thin_ts.append(ts_at(0, 5, i))
    thin_vals.append(3.0)
for i in range(40):  # bucket 20: 40 prior samples -> present
    thin_ts.append(ts_at(0, 20, i))
    thin_vals.append(5.0)
thin_ts.append(ts_at(1, 5, 0))  # today: sets the prior/today boundary
thin_vals.append(999.0)
thin = w.tod_profile(thin_ts, thin_vals)
check("bucket with 10 samples absent", 5 not in thin, str(thin))
check("bucket with 40 samples present", 20 in thin, str(thin))

print("\n=== sigma_hat: warm-up 60, forming bar excluded ===")


def days_of_bars(n_days, vol=1000.0, close_fn=None, start=None):
    """n_days of 5m bars (288/day); today = last day, so prior-only
    profiles see n_days - 1 days of history."""
    t0 = T0 if start is None else start
    out = []
    for i in range(n_days * 288):
        c = 100.0 + (0.01 if i % 2 else 0.0)
        if close_fn is not None:
            c = close_fn(i)
        out.append(bar(t0 + i * 300, 100.0, 100.5, 99.5, c, vol=vol))
    return out


long_bars = days_of_bars(15)
now = long_bars[-1]["ts"] + 300
s_full = w.sigma_hat(long_bars, now)
check("15d history -> float >= 0", isinstance(s_full, float) and s_full >= 0.0,
      str(s_full))
short = days_of_bars(1)[:59]
check("59 bars -> None (warm-up 60)",
      w.sigma_hat(short, short[-1]["ts"] + 300) is None,
      str(w.sigma_hat(short, short[-1]["ts"] + 300)))
forming = long_bars + [bar(now - 100, 100.0, 200.0, 50.0, 150.0, vol=1e9)]
s_with_forming = w.sigma_hat(forming, now)
check("forming bar (ts+300>now) excluded", s_with_forming == s_full,
      f"{s_with_forming} vs {s_full}")

print("\n=== resid_vol: own profile on e^2 ===")
r_ts = [b["ts"] for b in long_bars]
r_e = [0.001 * (1 if i % 2 else -1) for i in range(len(long_bars))]
rv = w.resid_vol(r_ts, r_e)
check("15d residuals -> float >= 0", isinstance(rv, float) and rv >= 0.0, str(rv))
check("value ~0.001 for constant |e|=0.001", abs(rv - 0.001) < 1e-9, str(rv))
check("short series -> None", w.resid_vol(r_ts[:10], r_e[:10]) is None,
      str(w.resid_vol(r_ts[:10], r_e[:10])))
# Own-profile check: scaling all e by 2 must scale resid_vol by ~2 even if a
# raw s_vol profile would say otherwise (never reuses raw s_vol).
rv2 = w.resid_vol(r_ts, [2 * x for x in r_e])
check("resid_vol scales with |e| (own profile)", approx(rv2, 2 * rv, 1e-9 * max(1.0, rv2)),
      f"{rv2} vs 2*{rv}")

print("\n=== cusum: symmetry, fire-side reset, refractory ===")
zs = [0.1, 0.4, 0.9, 1.5, 2.2, 1.8, 0.2, -0.1, 0.3, 1.1, 2.6, 2.9, 0.5, -2.0,
      -2.5, -1.0, 0.2, 0.8, 1.9, 2.4]
a = w.cusum(zs)
b = w.cusum([-z for z in zs])
check("mirror z->-z swaps long/short indices",
      a["long_idx"] == b["short_idx"] and a["short_idx"] == b["long_idx"],
      f"{a} vs {b}")
strong = [0.0] * 5 + [3.0] * 10
c = w.cusum(strong)
check("sustained +3 drift fires long", len(c["long_idx"]) >= 1, str(c))
check("no short fire on pure-up drift", c["short_idx"] == [], str(c))
if c["long_idx"]:
    gaps = [j - i for i, j in zip(c["long_idx"], c["long_idx"][1:])]
    check("6-bar refractory between same-side fires",
          all(g > 6 for g in gaps), str(c["long_idx"]))
check("empty input -> empty fires",
      w.cusum([]) == {"long_idx": [], "short_idx": []}, str(w.cusum([])))

print("\n=== beta_ols: no intercept, winsorized, zero-var None ===")
check("zero BTC variance -> None",
      w.beta_ols([0.01, -0.02, 0.03], [0.0, 0.0, 0.0]) is None, "")
r_btc = [0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.015, -0.005]
r_alt = [2 * x for x in r_btc]
ols = w.beta_ols(r_alt + [999.0], r_btc + [0.5])  # last bar excluded (current)
check("r = 2x BTC -> ols ~2.0 (current bar excluded)",
      ols is not None and abs(ols - 2.0) < 1e-9, str(ols))
check("beta_shrunk = 0.5*ols + 0.5",
      approx(w.beta_shrunk(r_alt + [999.0], r_btc + [0.5]), 0.5 * ols + 0.5),
      str(w.beta_shrunk(r_alt + [999.0], r_btc + [0.5])))
out_b = w.beta_ols([0.01] * 10 + [50.0] + [0.01] * 10 + [0.0],
                   [0.01] * 21 + [0.0])
check("wild non-current outlier winsorized (-> ~1.0)",
      out_b is not None and abs(out_b - 1.0) < 0.05, str(out_b))

print("\n=== rvol_adj: 3-bar mean / 48-bar median, seasonal ===")
now2 = long_bars[-1]["ts"] + 300
rvol = w.rvol_adj(long_bars, now2)
check("flat volume -> rvol ~1.0", rvol is not None and abs(rvol - 1.0) < 1e-9, str(rvol))
spike = [dict(b) for b in long_bars]
for s in spike[-3:]:
    s["vol"] = 3000.0
    s["amount"] = 3000.0 * s["c"]
rvol_spike = w.rvol_adj(spike, now2)
check("3x recent volume -> rvol ~3.0",
      rvol_spike is not None and abs(rvol_spike - 3.0) < 1e-9, str(rvol_spike))
check("<48 closed bars -> None",
      w.rvol_adj(long_bars[:40], long_bars[39]["ts"] + 300) is None, "")
single_day = days_of_bars(1)
check("no prior day -> seasonal missing -> None, not 1.0",
      w.rvol_adj(single_day, single_day[-1]["ts"] + 300) is None,
      str(w.rvol_adj(single_day, single_day[-1]["ts"] + 300)))

print("\n=== btc_drift_7d: 2016-bar 7d window ===")
check("2015 bars -> None", w.btc_drift_7d([100.0] * 2015) is None, "")
drift = w.btc_drift_7d([100.0] * 2015 + [110.0])
check("2016 bars 100->110 = 0.10", approx(drift, 0.10), str(drift))
drift2 = w.btc_drift_7d([90.0] + [100.0] * 2015 + [115.0])
check("uses last 2016 bars only", approx(drift2, (115.0 - 100.0) / 100.0), str(drift2))
check("empty -> None", w.btc_drift_7d([]) is None, "")

print("\n=== L5/initiation reuses scorer.score_v3 (call, not copy) ===")
import inspect as _inspect
src = _inspect.getsource(w.v3_snapshot)
check("v3_snapshot delegates to scorer.score_v3",
      "scorer.score_v3" in src or "score_v3" in src, "")
snap = w.v3_snapshot(long_bars)
direct = scorer.score_v3(long_bars)
check("snapshot matches direct scorer call",
      snap["initiation"] == direct["initiation"]
      and snap["direction_v3"] == direct["direction_v3"], str(snap))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
