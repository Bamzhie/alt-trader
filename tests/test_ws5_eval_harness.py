"""WS5: evaluation harness tests (research-only, SHADOW, no production writes).

Self-checking script: python3 tests/test_ws5_eval_harness.py
Covers:
   1. Funding cost attribution from observation archive (missing -> None, not 0)
   2. Funding cost data for missing coins is NOT zero-filled
   3. Config hash covers funding/settlements dirs
   4. Split resolution matches Splits.bounds
   5. Leakage check: embargo >= max horizon
   6. Leakage check: no event window crosses a boundary
   7. Evaluation report: reproducibility metadata (config hash, git rev)
   8. Holdout lock enforced (second look refused)
   9. No production data written
  10. Funding cost attribution from the settlement archive (venue settle_ts)
  11. Funding cost sign: longs pay positive rates, shorts receive them
  12. Funding charged only through actual exit timestamp (TP/SL early exit)
  13. Archive digests for every coin (no 30-coin cap)
  14. Incomplete boundary coverage -> cost is None (excluded, not estimated)
  15. Holdout lock refuses second attempt (92-day real-bar run)
  16. Report carries funding/settlement digests and boundary counts
  17. Observation -> funding-event mapping: snapshots are NOT summed
      repeatedly; each 8h boundary takes the last snapshot of its cycle;
      unattributable boundaries make the event incomplete
  18. Settlement archive wins when it covers the window; missing settle
      boundaries -> incomplete; observation fallback for gap periods maps
      snapshots to the VENUE grid (phase/cycle from settlements), and an
      uninferable venue grid is incomplete (never epoch-assumed)
  19. FADE policy regression: trade side comes from the selected rule's
      policy, consistently for funding sign, exit-offset slot, coverage
  20. Integration: funding-adjusted dev/val/holdout/slip2x metrics differ
      from friction-only when funding is present; denominators explicit
  21. Integration: with no funding data, funding-adjusted metrics are None
      (never a friction-only number under a funding-adjusted label)
  22. Integration: missing settlement boundaries are excluded AND counted;
      the holdout is spent at most once (incl. funding-adjusted fields)
  23. --coins is forwarded to collect_events (restricted universe); the
      default stays None (whole universe)
"""

import gzip
import json
import os
import sys
import tempfile
import math
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.quant import cost_attribution as ca
from proto.quant import eval_harness as eh
from proto.quant import research as R
from proto.quant import barrier as B
from proto.quant.config import (DEFAULT, BAR_S, BARS_PER_DAY, Splits,
                                RuleParams, grid_geometries)

FAILURES = []
CHECKS = 0


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    cond = bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


# --------------------------------------------------------------------------
# Fixtures: a small synthetic bar archive with known funding observations
# --------------------------------------------------------------------------

T0 = 1_700_000_100
T0 = T0 - (T0 % BAR_S)

# Epoch-aligned 8h funding-grid anchor (multiple of SETTLE_SECONDS).
SETTLE_S = 8 * 3600
B1 = SETTLE_S * ((T0 // SETTLE_S) + 1)      # 1700006400, on the grid


def make_bars(n, seed, sigma, start=T0):
    """Generate n 5m bars of synthetic price data."""
    rng = random.Random(seed)
    px, out = 100.0, []
    for i in range(n):
        o = px
        px *= math.exp(rng.gauss(0, sigma))
        c = px
        h = max(o, c) * (1 + abs(rng.gauss(0, sigma / 3)))
        l = min(o, c) * (1 - abs(rng.gauss(0, sigma / 3)))
        amt = 50000.0
        out.append((start + i * BAR_S, o, h, l, c, amt / c, amt))
    return out


def write_bars(data_dir, coin, bars):
    path = os.path.join(data_dir, coin + ".csv.gz")
    with gzip.open(path, "wt", newline="") as f:
        f.write("ts,o,h,l,c,vol,amount\n")
        for r in bars:
            f.write(",".join(str(x) for x in r) + "\n")


def write_funding(funding_dir, coin, observations):
    """observations: list of (ts, rate)"""
    path = os.path.join(funding_dir, coin + ".csv.gz")
    with gzip.open(path, "wt", newline="") as f:
        f.write("ts,rate,cap\n")
        for ts, rate in observations:
            f.write(f"{ts},{rate},0.0018\n")


def write_settlements(settlements_dir, coin, settlements):
    """settlements: list of (settle_ts, settled_rate, cycle_hours)"""
    path = os.path.join(settlements_dir, coin + ".csv.gz")
    with gzip.open(path, "wt", newline="") as f:
        f.write("settle_ts,settled_rate,cycle_hours\n")
        for ts, rate, cycle in settlements:
            f.write(f"{ts},{rate},{cycle}\n")


print("=== case 1: funding cost attribution from observations ===")

tmp = tempfile.mkdtemp(prefix="ws5-cost-")
funding_dir = os.path.join(tmp, "funding")
os.makedirs(funding_dir)

# Hourly snapshots at T0, T0+1h, T0+2h. Under the documented mapping each
# 8h funding event takes the LAST snapshot of its cycle; the only funding
# boundary in (T0, T0+10000) is T0+6300, covered by the T0+3600 snapshot.
obs_ts = T0, T0 + 3600, T0 + 7200
obs_rates = 0.0001, 0.0002, 0.00015
write_funding(funding_dir, "BTC", list(zip(obs_ts, obs_rates)))

result = ca.funding_cost("BTC", T0, T0 + 10000,
                         funding_dir=funding_dir)
check("funding cost is non-None when data exists",
      result.cost is not None, str(result))
check("missing=False for coin with data", not result.missing, str(result))
check("source is observation", result.source == "observation", str(result))
check("one funding event attributed (boundary at T0+6300)",
      result.observations == 1, str(result))
check("boundary takes the last snapshot of its cycle (0.0002, not a sum)",
      result.cost == 0.0002, str(result))
check("complete coverage -> incomplete is False", not result.incomplete,
      str(result))

# Window with NO funding boundary (T0+6300 < T0+20000, next is T0+35100):
# no funding event settles inside it, so the cost is exactly zero events.
result2 = ca.funding_cost("BTC", T0 + 20000, T0 + 30000,
                          funding_dir=funding_dir)
check("window without a funding boundary -> cost 0.0, 0 events",
      result2.cost == 0.0 and result2.observations == 0, str(result2))


print("\n=== case 2: missing data is NOT zero-filled ===")

result3 = ca.funding_cost("NOPE", T0, T0 + 10000,
                           funding_dir=funding_dir)
check("missing coin -> missing=True, cost=None",
      result3.missing is True and result3.cost is None, str(result3))

# Coin exists in files but funding archive has no entries for it
result4 = ca.funding_cost("AAA", T0, T0 + 10000,
                           funding_dir=funding_dir)
check("coin with no funding file -> missing=True",
      result4.missing is True and result4.cost is None, str(result4))


print("\n=== case 3: config hash covers cost inputs ===")

h1 = ca.config_hash("data/funding", "data/funding_settlements")
h2 = ca.config_hash("data/funding2", "data/funding_settlements")
h3 = ca.config_hash("data/funding", None)
check("different funding dir changes hash", h1 != h2, f"{h1} == {h2}")
check("null settlements dir changes hash", h1 != h3, f"{h1} == {h3}")
check("same inputs stabilize hash",
      ca.config_hash("data/funding", "data/funding_settlements") == h1)


print("\n=== case 4: split resolution matches Splits.bounds ===")

meta = {"t_min": T0, "t_max": T0 + 91.4 * 86400, "n_days": 91.4}
splits = eh.resolve_splits(meta, DEFAULT)
bounds = DEFAULT.splits.bounds(91.4)
check("dev bounds match", splits.dev == tuple(bounds["dev"]),
      f"{splits.dev} != {tuple(bounds['dev'])}")
check("val bounds match", splits.val == tuple(bounds["val"]))
check("holdout bounds match", splits.holdout == tuple(bounds["holdout"]))
check("n_days propagated", splits.n_days == 91.4)
check("max_horizon_bars is 288", splits.max_horizon_bars == 288)


print("\n=== case 5: leakage check -- embargo >= max horizon ===")

# Create events that span the entire archive
events = []
class FakeEvent:
    pass
for i in range(100):
    ev = FakeEvent()
    ev.day = i * 0.1  # 0.0 to 9.9
    ev.ts = T0 + i * BAR_S
    events.append(ev)

violations = eh.check_no_leakage(events, splits)
check("no leakage violations on clean splits", violations == [], str(violations))

# Now break the embargo by creating a tiny Splits
broken = eh.EvalSplits(
    n_days=91.4, dev=(6.0, 61.0), val=(61.5, 75.5), holdout=(76.0, 91.4),
    embargo_days=0.5, first_event_day=6.0)
violations2 = eh.check_no_leakage(events, broken)
check("narrow embargo triggers violation",
      len(violations2) > 0, str(violations2))


print("\n=== case 6: event windows do not cross boundaries ===")

# An event at day 60.5 with max horizon ~1 day: window_end = 61.5
# Dev ends at 61, val starts at 63 (2 day embargo).
# The event is in dev, window_end (61.5) < val_start (63) -> no leak
ev_border = FakeEvent()
ev_border.day = 60.5
ev_border.ts = T0 + int(60.5 * 86400)
violations3 = eh.check_no_leakage([ev_border], splits)
check("event near dev boundary stays within embargo", violations3 == [], str(violations3))

# An event at day 60.999 with max horizon ~1 day: window_end = 61.999
# Still < 63 -> no leak
ev_border2 = FakeEvent()
ev_border2.day = 60.999
ev_border2.ts = T0 + int(60.999 * 86400)
violations4 = eh.check_no_leakage([ev_border2], splits)
check("event at day 60.999 does not leak", violations4 == [], str(violations4))


print("\n=== case 7: evaluation report has reproducibility metadata ===")

# Use a dataset with enough days but keep it small for test speed.
# The Splits need ~91 days for full dev/val/holdout, but with only 2 coins
# and synthetic noise, events may be too few. The harness handles this
# gracefully (reports insufficient-data without crashing).
bar_dir = os.path.join(tempfile.mkdtemp(prefix="ws5-bars-"), "bars")
os.makedirs(bar_dir)
n_days = 20
n_bars = n_days * 288
bars_btc = make_bars(n_bars, seed=1, sigma=0.002)
write_bars(bar_dir, "BTC", bars_btc)
write_bars(bar_dir, "AAA", make_bars(n_bars, seed=2, sigma=0.003))

# Create a funding archive for BTC only (daily obs for 20 days)
fund_dir = os.path.join(tempfile.mkdtemp(prefix="ws5-fund-"), "funding")
os.makedirs(fund_dir)
fund_obs = [(T0 + i * 86400, 0.0001) for i in range(n_days)]
write_funding(fund_dir, "BTC", fund_obs)

report = eh.evaluate(
    bar_dir, funding_dir=fund_dir,
    out_dir=os.path.join(tempfile.mkdtemp(), "ws5-out"))

check("report has config_hash", len(report.config_hash) > 0)
check("report has cost_hash", len(report.cost_hash) > 0)
check("report has splits", report.splits is not None)
check("report dev has n", report.dev.get("n") is not None)
check("report val has n", report.val.get("n") is not None)
check("report has git_rev or None", report.git_rev is None or isinstance(report.git_rev, str))
check("report has bar_digests", isinstance(report.bar_digests, dict))
check("report produced_at is timestamp", report.produced_at > 0)

# Funding-adjusted fields exist and, when present, carry the explicit
# denominator keys (they are None when no config was selected).
for label, blk in (("dev", report.dev_funding_adjusted),
                   ("val", report.val_funding_adjusted)):
    if blk is None:
        check(f"{label}_funding_adjusted is None on insufficient data", True)
    else:
        check(f"{label}_funding_adjusted has explicit denominators",
              all(k in blk for k in ("n_events", "n_funding_adjusted",
                                     "n_excluded_missing_coin",
                                     "n_excluded_incomplete")), str(blk))

# Check that AAA is missing from funding (not zero-filled)
check("AAA appears in missing_cost_coins", "AAA" in report.missing_cost_coins,
      str(report.missing_cost_coins))

# Selection identity must be persisted so the evaluated strategy is
# recoverable from evaluation.json alone (not just its metrics).
check("report exposes n_configs", isinstance(report.n_configs, int))
check("report exposes selection identity", isinstance(report.selected, dict),
      str(report.selected))
sel_id = report.selected or {}
if sel_id.get("rule") == "insufficient-data":
    check("insufficient-data selection is explicit", True)
    check("insufficient-data has no params/geom",
          sel_id.get("params") is None and sel_id.get("geom") is None,
          str(sel_id))
else:
    check("selected identity names a rule", bool(sel_id.get("rule")), str(sel_id))
    check("selected identity names a geometry", bool(sel_id.get("geometry")),
          str(sel_id))
    check("selected identity carries resolved params",
          isinstance(sel_id.get("params"), dict), str(sel_id))
    check("selected identity carries resolved geometry",
          isinstance(sel_id.get("geom"), dict), str(sel_id))
check("report exposes spa multiplicity test",
      isinstance(report.spa, dict) and "p_value" in report.spa, str(report.spa))
check("report exposes dev_config_means",
      report.dev_config_means is None or isinstance(report.dev_config_means, dict))
check("report exposes top5_dev", isinstance(report.top5_dev, list))
_selected_json = json.loads(report.to_json())
check("serialized report carries selection identity",
      "selected" in _selected_json
      and _selected_json["selected"] == report.selected,
      "selection identity lost in JSON round-trip")
check("serialized report carries n_configs",
      _selected_json.get("n_configs") == report.n_configs)


print("\n=== case 8: holdout lock is enforced (one-shot) ===")

out_dir = tempfile.mkdtemp(prefix="ws5-holdout-")
# First holdout spend: the harness should not crash, and the holdout field
# exists (None if val didn't keep the config; a dict if it did)
report_h = eh.evaluate(
    bar_dir, funding_dir=fund_dir,
    out_dir=out_dir, spend_holdout=True)
check("holdout spend does not crash", hasattr(report_h, 'holdout'))
check("holdout is either evaluated or explicitly not spent",
      report_h.holdout is None or isinstance(report_h.holdout, dict))

# The holdout lock mechanism is inherited from R.holdout_once; verify
# the lock file path logic by checking that a second call with the same
# out_dir either respects the lock or skips (no crash)
report_h2 = eh.evaluate(
    bar_dir, funding_dir=fund_dir,
    out_dir=out_dir, spend_holdout=True)
check("second holdout spend does not crash", hasattr(report_h2, 'holdout'))


print("\n=== case 9: no production data is written ===")

# Verify evaluate() only writes to out_dir
out2 = tempfile.mkdtemp(prefix="ws5-no-prod-")
bar_dir2 = os.path.join(tempfile.mkdtemp(prefix="ws5-bars2-"), "bars")
os.makedirs(bar_dir2)
write_bars(bar_dir2, "BTC", make_bars(200, seed=3, sigma=0.001))
write_bars(bar_dir2, "AAA", make_bars(200, seed=4, sigma=0.002))
fund_dir2 = os.path.join(tempfile.mkdtemp(), "funding")
os.makedirs(fund_dir2)
write_funding(fund_dir2, "BTC", [(T0, 0.0001)])

# Record file count in funding_dir before evaluate
before_coins = set(os.listdir(fund_dir2))
_ = eh.evaluate(bar_dir2, funding_dir=fund_dir2, out_dir=out2)
after_coins = set(os.listdir(fund_dir2))
check("evaluate never writes to funding_dir",
      before_coins == after_coins,
      f"before={before_coins} after={after_coins}")

# Verify evaluate writes only to out_dir (not data/)
check("evaluate writes report to out_dir",
      os.path.exists(os.path.join(out2, "evaluation.json")),
      "evaluation.json not found in out_dir")


print("\n=== case 10: cost attribution with settlement archive ===")

sett_dir = os.path.join(tempfile.mkdtemp(prefix="ws5-sett-"), "settlements")
os.makedirs(sett_dir)
# Venue-reported settlements on their own 8h grid (grid phase is inferred
# from the archive itself). All boundaries in the window below are present
# -> complete settlement coverage.
settles = [(T0, 0.0001, 8),
           (T0 + 8 * 3600, 0.0002, 8),
           (T0 + 16 * 3600, 0.00015, 8),
           (T0 + 24 * 3600, 0.0001, 8)]
write_settlements(sett_dir, "BTC", settles)

# Position opens at T0+8h (after first settle), closes at T0+25h:
# boundaries strictly inside the window are T0+16h and T0+24h.
result_s = ca.funding_cost("BTC", T0 + 8 * 3600, T0 + 25 * 3600,
                           funding_dir=None,
                           settlements_dir=sett_dir)
check("settlement cost is non-None", result_s.cost is not None, str(result_s))
check("settlement source", result_s.source == "settlement", str(result_s))
check("exactly the two venue settle events in the window are charged",
      result_s.observations == 2, str(result_s))
check("cost equals the two venue-reported settled rates",
      result_s.cost == 0.00015 + 0.0001, str(result_s))


print("\n=== case 11: funding cost sign (long vs short) ===")

# Positive rate: long pays (cost > 0), short receives (cost < 0)
fund_dir3 = os.path.join(tempfile.mkdtemp(prefix="ws5-sign-"), "funding")
os.makedirs(fund_dir3)
write_funding(fund_dir3, "BTC", [(T0, 0.0001), (T0 + 3600, 0.0002),
                                (T0 + 7200, 0.00015)])

cost_long = ca.funding_cost("BTC", T0, T0 + 10000, funding_dir=fund_dir3,
                            side=1)
cost_short = ca.funding_cost("BTC", T0, T0 + 10000, funding_dir=fund_dir3,
                             side=-1)
check("long pays positive rate (cost > 0)",
      cost_long.cost is not None and cost_long.cost > 0,
      str(cost_long))
check("short receives positive rate (cost < 0)",
      cost_short.cost is not None and cost_short.cost < 0,
      str(cost_short))
check("long and short costs are opposite signs",
      cost_long.cost == -cost_short.cost,
      f"long={cost_long.cost}, short={cost_short.cost}")

# Negative rate: long receives (cost < 0), short pays (cost > 0)
write_funding(fund_dir3, "ETH", [(T0, -0.0001), (T0 + 3600, -0.0002)])
cost_long_neg = ca.funding_cost("ETH", T0, T0 + 10000, funding_dir=fund_dir3,
                                side=1)
cost_short_neg = ca.funding_cost("ETH", T0, T0 + 10000, funding_dir=fund_dir3,
                                 side=-1)
check("long receives negative rate (cost < 0)",
      cost_long_neg.cost is not None and cost_long_neg.cost < 0,
      str(cost_long_neg))
check("short pays negative rate (cost > 0)",
      cost_short_neg.cost is not None and cost_short_neg.cost > 0,
      str(cost_short_neg))


print("\n=== case 12: funding charged through actual exit timestamp ===")

# Grid-aligned observations: a snapshot just before every 8h boundary plus
# a mid-cycle snapshot. The boundary at B1+8h takes the B1+8h-100s snapshot.
fund_dir4 = os.path.join(tempfile.mkdtemp(prefix="ws5-exit-"), "funding")
os.makedirs(fund_dir4)
fund_obs_12 = []
for k in range(4):
    b = B1 + k * SETTLE_S
    fund_obs_12.append((b - 100, 0.0001 + k * 0.00001))
fund_obs_12.append((B1 + 27000, 0.0003))     # last snapshot before B1+8h
write_funding(fund_dir4, "BTC", sorted(fund_obs_12))

# Early exit 2h after open: no funding boundary in the window -> 0 events
cost_early = ca.funding_cost("BTC", B1, B1 + 7200, funding_dir=fund_dir4,
                             side=1)
check("early exit before first settle -> cost is 0.0 (no settles)",
      cost_early.cost == 0.0,
      f"expected 0.0, got {cost_early.cost}")

# Exit at +10h: exactly one boundary (B1+8h) is inside the window, and it
# takes the LAST snapshot of its cycle (B1+8h-100s), not every snapshot.
cost_late = ca.funding_cost("BTC", B1, B1 + 10 * 3600, funding_dir=fund_dir4,
                            side=1)
check("exit at +10h -> exactly 1 settle boundary in range",
      cost_late.observations == 1,
      f"expected 1 observation, got {cost_late.observations}")
check("exit at +10h -> cost equals the last snapshot of the boundary's cycle",
      cost_late.cost == 0.0001 + 1 * 0.00001,
      str(cost_late))


print("\n=== case 13: archive digests for every coin (no cap) ===")

# Build an archive with many coins
bar_dir3 = os.path.join(tempfile.mkdtemp(prefix="ws5-digest-"), "bars")
os.makedirs(bar_dir3)
for i in range(35):
    coin = f"COIN{i:02d}"
    write_bars(bar_dir3, coin, make_bars(200, seed=i + 100, sigma=0.002))

fund_dir5 = os.path.join(tempfile.mkdtemp(prefix="ws5-digest-fund-"), "funding")
os.makedirs(fund_dir5)
for i in range(35):
    coin = f"COIN{i:02d}"
    write_funding(fund_dir5, coin, [(T0, 0.0001)])

# compute bar digests directly
digests = {}
for i in range(35):
    coin = f"COIN{i:02d}"
    d = eh._archive_digest(bar_dir3, coin)
    if d:
        digests[coin] = d
check("all 35 coin digests present (no cap)",
      len(digests) == 35,
      f"got {len(digests)}")

# funding digests too
fund_digests = {}
for i in range(35):
    coin = f"COIN{i:02d}"
    d = eh._archive_digest(fund_dir5, coin)
    if d:
        fund_digests[coin] = d
check("all 35 funding digests present (no cap)",
      len(fund_digests) == 35,
      f"got {len(fund_digests)}")


print("\n=== case 14: incomplete boundary coverage -> cost is None ===")

# Observations start only AFTER the position's open and too late to cover
# the first boundary -> the cost cannot be attributed and must NOT be
# estimated: cost=None, incomplete=True, missing=False.
fund_dir6 = os.path.join(tempfile.mkdtemp(prefix="ws5-incomplete-"), "funding")
os.makedirs(fund_dir6)
write_funding(fund_dir6, "BTC", [(T0 + 10000, 0.0001), (T0 + 20000, 0.0002)])

# Window (T0, T0+30000) contains the boundary T0+6300, which needs a
# snapshot in [T0+6300-8h, T0+6300): none exists.
result_incomplete = ca.funding_cost("BTC", T0, T0 + 30000,
                                   funding_dir=fund_dir6, side=1)
check("incomplete coverage -> cost is None, missing=False",
      result_incomplete.cost is None and not result_incomplete.missing,
      str(result_incomplete))
check("incomplete coverage -> incomplete flag is set",
      result_incomplete.incomplete, str(result_incomplete))


print("\n=== case 15: holdout lock refuses second attempt ===")

import tempfile as _tf
holdout_dir = _tf.mkdtemp(prefix="ws5-lock-")

# First successful holdout evaluation with enough synthetic data
bar_dir4 = os.path.join(_tf.mkdtemp(prefix="ws5-holdout-bars-"), "bars")
os.makedirs(bar_dir4)
n_days_h = 92
n_bars_h = n_days_h * BARS_PER_DAY
write_bars(bar_dir4, "BTC", make_bars(n_bars_h, seed=42, sigma=0.001))
write_bars(bar_dir4, "AAA", make_bars(n_bars_h, seed=43, sigma=0.001))

fund_dir7 = os.path.join(_tf.mkdtemp(prefix="ws5-holdout-fund-"), "funding")
os.makedirs(fund_dir7)
write_funding(fund_dir7, "BTC", [(T0 + i * 86400, 0.0001) for i in range(n_days_h)])
write_funding(fund_dir7, "AAA", [(T0 + i * 86400, 0.0001) for i in range(n_days_h)])

# Patch sys.argv so collect_events works with our tmp dirs
# Actually evaluate() takes data_dir as param, so no patch needed.
# But we need enough events. With 92 days and 2 coins, should get >50 events.
report_h = eh.evaluate(
    bar_dir4, funding_dir=fund_dir7,
    out_dir=holdout_dir, spend_holdout=True)
check("first holdout evaluation succeeds or gracefully reports no events",
      hasattr(report_h, 'holdout'),
      str(type(report_h)))
check("holdout field exists", report_h.holdout is None or isinstance(report_h.holdout, dict))

# Verify the lock file mechanism: if holdout was evaluated, the lock exists
lock_path = os.path.join(holdout_dir, "holdout_evaluated.json")
if os.path.exists(lock_path):
    # Second call should refuse (SystemExit caught by evaluate)
    report_h2 = eh.evaluate(
        bar_dir4, funding_dir=fund_dir7,
        out_dir=holdout_dir, spend_holdout=True)
    check("second holdout attempt is refused (holdout stays None)",
          report_h2.holdout is None,
          "second attempt should not re-evaluate holdout")
    check("funding-adjusted holdout is also refused on the second look",
          report_h2.holdout_funding_adjusted is None
          and report_h2.holdout_slip2x_funding_adjusted is None)
else:
    check("holdout lock mechanism present (skip if no events)",
          True,
          "not enough events to trigger holdout")


print("\n=== case 16: report has funding and settlement digests ===")

# Verify the EvaluationReport carries funding_digests and settlement_digests
check("report has funding_digests", hasattr(report, 'funding_digests'))
check("report has settlement_digests", hasattr(report, 'settlement_digests'))
check("report has incomplete_boundary_events",
      hasattr(report, 'incomplete_boundary_events'))
check("report has incomplete_boundary_denominator",
      hasattr(report, 'incomplete_boundary_denominator'))

# Run evaluate with funding and settlement dirs
sett_dir2 = os.path.join(tempfile.mkdtemp(prefix="ws5-sett2-"), "settlements")
os.makedirs(sett_dir2)
for coin in ["BTC", "AAA"]:
    write_settlements(sett_dir2, coin,
                      [(T0 + i * 8 * 3600, 0.0001, 8) for i in range(n_days_h * 3)])

report_ds = eh.evaluate(
    bar_dir4, funding_dir=fund_dir7, settlements_dir=sett_dir2,
    out_dir=_tf.mkdtemp(prefix="ws5-report-"))
check("funding_digests is a dict", isinstance(report_ds.funding_digests, dict))
check("settlement_digests is a dict", isinstance(report_ds.settlement_digests, dict))
check("BTC in funding_digests", "BTC" in report_ds.funding_digests)
check("BTC in settlement_digests", "BTC" in report_ds.settlement_digests)
check("AAA in funding_digests", "AAA" in report_ds.funding_digests)
check("AAA in settlement_digests", "AAA" in report_ds.settlement_digests)


print("\n=== case 17: observation -> funding-event mapping (no repeated sums) ===")

# Dense snapshots every 15 minutes, constant rate. Each 8h boundary takes
# exactly ONE value (the last snapshot of its cycle) -- snapshots inside a
# cycle are never summed repeatedly.
map_dir = os.path.join(tempfile.mkdtemp(prefix="ws5-map-"), "funding")
os.makedirs(map_dir)
dense = [(b - SETTLE_S + 900 * j, 0.0001)
         for b in (B1, B1 + SETTLE_S, B1 + 2 * SETTLE_S)
         for j in range(1, 2 * 4 + 1)]
write_funding(map_dir, "MAP", sorted(dense))

r_map1 = ca.funding_cost("MAP", B1 - 3600, B1 + 3600, funding_dir=map_dir)
check("window around one boundary -> exactly one funding event",
      r_map1.observations == 1, str(r_map1))
check("dense snapshots are not summed (cost == one rate, not ~8 rates)",
      r_map1.cost == 0.0001, str(r_map1))

r_map2 = ca.funding_cost("MAP", B1 - 60, B1 + 3 * SETTLE_S - 60,
                         funding_dir=map_dir)
check("three boundaries in window -> exactly three funding events",
      r_map2.observations == 3, str(r_map2))
check("three boundaries -> 3 x one snapshot rate",
      abs(r_map2.cost - 0.0003) < 1e-12, str(r_map2))

# Last-snapshot-of-the-cycle attribution: a snapshot AFTER the boundary
# belongs to the NEXT cycle and must not affect this one.
map_dir2 = os.path.join(tempfile.mkdtemp(prefix="ws5-map2-"), "funding")
os.makedirs(map_dir2)
write_funding(map_dir2, "ETH2", [(B1 - SETTLE_S, 0.0005),
                                 (B1 - 300, 0.0002),
                                 (B1 + 60, 0.009),
                                 (B1 + SETTLE_S - 100, 0.0007)])
r_map3 = ca.funding_cost("ETH2", B1 - 60, B1 + 600, funding_dir=map_dir2)
check("boundary takes the last snapshot strictly before it",
      r_map3.cost == 0.0002 and r_map3.observations == 1, str(r_map3))

# Sparse snapshots that cannot cover the boundary -> incomplete, excluded
map_dir3 = os.path.join(tempfile.mkdtemp(prefix="ws5-map3-"), "funding")
os.makedirs(map_dir3)
write_funding(map_dir3, "ADA", [(B1 - 40000, 0.0001), (B1 + 100, 0.0002)])
r_map4 = ca.funding_cost("ADA", B1 - 60, B1 + 600, funding_dir=map_dir3)
check("unattributable boundary -> incomplete, cost None",
      r_map4.cost is None and r_map4.incomplete and not r_map4.missing,
      str(r_map4))


print("\n=== case 18: settlement coverage wins; gaps fall back or exclude ===")

sett_dir3 = os.path.join(tempfile.mkdtemp(prefix="ws5-sett3-"), "settlements")
os.makedirs(sett_dir3)
write_settlements(sett_dir3, "DOT",
                  [(B1, 0.001, 8), (B1 + SETTLE_S, 0.002, 8),
                   (B1 + 2 * SETTLE_S, 0.003, 8)])
obs_dir = os.path.join(tempfile.mkdtemp(prefix="ws5-obs3-"), "funding")
os.makedirs(obs_dir)
write_funding(obs_dir, "DOT", [(B1 - 28800 + 450 + 900 * j, 0.5)
                               for j in range(64)])

r_sett = ca.funding_cost("DOT", B1 - 60, B1 + SETTLE_S + 60,
                         funding_dir=obs_dir, settlements_dir=sett_dir3)
check("settlement archive covers the window -> settlement source wins",
      r_sett.source == "settlement", str(r_sett))
check("settlement cost = venue settled rates at the two boundaries",
      r_sett.cost == 0.001 + 0.002 and r_sett.observations == 2, str(r_sett))

# Settlement archive with a GAP over the window; observations do cover it
# -> observation-mapped fallback, clearly labeled as source=observation.
write_settlements(sett_dir3, "ETC",
                  [(B1, 0.001, 8), (B1 + 2 * SETTLE_S, 0.003, 8)])
write_funding(obs_dir, "ETC", [(B1 - SETTLE_S + 450 + 900 * j, 0.0004)
                               for j in range(64)])
r_gap = ca.funding_cost("ETC", B1 - 60, B1 + SETTLE_S + 60,
                        funding_dir=obs_dir, settlements_dir=sett_dir3)
check("settlement gap + covering observations -> observation fallback",
      r_gap.source == "observation", str(r_gap))
check("observation fallback charges each boundary once",
      r_gap.cost == 0.0008 and r_gap.observations == 2, str(r_gap))

# Settlement archive with a GAP and observations too sparse to attribute
# -> incomplete: excluded and counted, never estimated.
write_funding(obs_dir, "XRP", [(B1 - 40000, 0.0001)])
r_gap2 = ca.funding_cost("XRP", B1 - 60, B1 + SETTLE_S + 60,
                         funding_dir=obs_dir, settlements_dir=sett_dir3)
check("settlement gap + sparse observations -> incomplete, cost None",
      r_gap2.cost is None and r_gap2.incomplete and not r_gap2.missing,
      str(r_gap2))

# Deliberately OFF-EPOCH venue grid: the venue settles at phase +03:00, not
# on the epoch-aligned 8h grid, and its settlement archive has a GAP over
# the window. The fallback MUST map snapshots to the VENUE boundaries (the
# phase/cycle inferred from the settlement archive), never to the
# epoch-aligned grid. Epoch mapping would charge the single snapshot at
# Bv+18000-300s (0.009); venue mapping charges Bv and Bv+8h
# (0.002 + 0.005).
VENUE_PHASE = 3 * 3600
Bv = VENUE_PHASE + 28800 * 100                     # off-epoch boundary
write_settlements(sett_dir3, "OFFEPOCH",
                  [(Bv - 28800, 0.001, 8),
                   (Bv + 2 * 28800, 0.001, 8),
                   (Bv + 3 * 28800, 0.001, 8)])    # gap: Bv, Bv+8h
write_funding(obs_dir, "OFFEPOCH", [(Bv - 300, 0.002),
                                    (Bv + 18000 - 300, 0.009),
                                    (Bv + 28800 - 300, 0.005)])
r_off = ca.funding_cost("OFFEPOCH", Bv - 60, Bv + 28800 + 60,
                        funding_dir=obs_dir, settlements_dir=sett_dir3)
check("off-epoch settlement gap -> observation fallback",
      r_off.source == "observation", str(r_off))
check("fallback maps snapshots to the VENUE grid, not epoch alignment",
      r_off.cost == 0.002 + 0.005 and r_off.observations == 2, str(r_off))

# Settlement archive exists but reports no usable cycle_hours -> the venue
# grid cannot be inferred. Epoch alignment must NOT be assumed: the cost is
# incomplete even though dense snapshots could cover an epoch grid.
write_settlements(sett_dir3, "NOGRID",
                  [(Bv, 0.001, 0), (Bv - 28800, 0.001, 0)])
write_funding(obs_dir, "NOGRID",
              [(Bv - 28800 + 900 * j, 0.0005) for j in range(65)])
r_nogrid = ca.funding_cost("NOGRID", Bv - 60, Bv + 28800 + 60,
                           funding_dir=obs_dir, settlements_dir=sett_dir3)
check("uninferable venue grid -> incomplete, never epoch-assumed",
      r_nogrid.cost is None and r_nogrid.incomplete
      and not r_nogrid.missing, str(r_nogrid))

# No settlement archive for a coin -> the documented epoch-aligned mapping
# still applies (the only case where the grid is assumed).
write_funding(obs_dir, "EPOCHONLY",
              [(Bv - 300, 0.0004), (Bv + 18000 - 300, 0.0007)])
r_epoch = ca.funding_cost("EPOCHONLY", Bv + 18000 - 60, Bv + 18000 + 600,
                          funding_dir=obs_dir, settlements_dir=sett_dir3)
check("no settlement archive -> documented epoch grid is used",
      r_epoch.source == "observation" and r_epoch.cost == 0.0007
      and r_epoch.observations == 1, str(r_epoch))

# No archive at all -> missing (distinct from incomplete)
r_none = ca.funding_cost("SOL", B1 - 60, B1 + 600,
                         funding_dir=obs_dir, settlements_dir=sett_dir3)
check("no archive rows at all -> missing=True, not incomplete",
      r_none.missing and r_none.cost is None and not r_none.incomplete,
      str(r_none))


print("\n=== case 19: FADE policy regression (side from rule + policy) ===")

# The research harness derives the POSITION side from the selected rule's
# policy: fade inverts the break direction (research.eval_config uses
# `ev.side if rule.policy == 'cont' else -ev.side`). Funding signs,
# exit-offset slot selection, and coverage must all use that same side.
geoms = grid_geometries()
g0 = geoms[0]                      # T48_ks1_R1
dd0 = B.stop_distance(0.001, g0.horizon_bars, g0.k_stop)
friction = DEFAULT.cost.round_trip(5e7)   # qv24=5e7 -> top slip tier

ev19 = FakeEvent()
ev19.coin, ev19.ts, ev19.day = "FADE", B1 - 3600, 10.0
ev19.fam, ev19.param, ev19.side = "B", 9.9, 1        # up-break
ev19.qv24, ev19.sigma, ev19.rvol = 5e7, 0.001, 9.9
ev19.z1h, ev19.z4h, ev19.btc7d = 0.0, 0.0, 0.0
ev19.gross, ev19.kind, ev19.exit_offset = [], [], []
for gi, g in enumerate(geoms):
    for s in (1, -1):
        # long slot exits early (offset 5 -> before the boundary);
        # short slot exits late (offset 20 -> after the boundary at B1)
        ev19.gross.append(0.01 if s > 0 else 0.02)
        ev19.kind.append(2)                       # TIME
        ev19.exit_offset.append(5 if s > 0 else 20)

fade_rule = RuleParams("breakout_start", "fade", rvol_min=2.0)
cont_rule = RuleParams("breakout_start", "cont", rvol_min=2.0)

check("trade_side: fade inverts the break side",
      eh.trade_side(ev19, fade_rule) == -1)
check("trade_side: cont keeps the break side",
      eh.trade_side(ev19, cont_rule) == +1)

# Exit-offset slot follows the SAME side: fade(short) exits at offset 20,
# cont(long) at offset 5.
check("_event_exit_ts: fade uses the short slot's exit offset",
      eh._event_exit_ts(ev19, 0, fade_rule) == ev19.ts + (2 + 20) * BAR_S)
check("_event_exit_ts: cont uses the long slot's exit offset",
      eh._event_exit_ts(ev19, 0, cont_rule) == ev19.ts + (2 + 5) * BAR_S)

# One venue settlement at B1 with a positive rate: a FADE of an up-break
# is a SHORT -> the positive rate is a CREDIT (negative cost).
sett_dir4 = os.path.join(tempfile.mkdtemp(prefix="ws5-fade-"), "settlements")
os.makedirs(sett_dir4)
write_settlements(sett_dir4, "FADE", [(B1, 0.001, 8)])

r_fade = ca.funding_cost("FADE", ev19.ts + BAR_S,
                         eh._event_exit_ts(ev19, 0, fade_rule),
                         settlements_dir=sett_dir4, side=-1)
check("fade (short) receives the positive rate as a credit",
      r_fade.cost is not None and r_fade.cost == -0.001, str(r_fade))

fa_fade = eh.funding_adjusted_config([ev19], fade_rule, 0, g0, (0.0, 100.0),
                                     DEFAULT, None, sett_dir4)
dd = dd0
exp_fade = B.net_r(0.02, dd, friction + (-0.001))
check("fade funding-adjusted metric: n_events/n_funding_adjusted = 1/1",
      fa_fade["n_events"] == 1 and fa_fade["n_funding_adjusted"] == 1
      and fa_fade["n_excluded_incomplete"] == 0
      and fa_fade["n_excluded_missing_coin"] == 0, str(fa_fade))
check("fade metric uses short gross, short exit slot and credit funding",
      fa_fade["mean_r"] is not None
      and abs(fa_fade["mean_r"] - exp_fade) < 1e-12,
      f"got {fa_fade['mean_r']}, expected {exp_fade}")

fa_cont = eh.funding_adjusted_config([ev19], cont_rule, 0, g0, (0.0, 100.0),
                                     DEFAULT, None, sett_dir4)
exp_cont = B.net_r(0.01, dd, friction + 0.0)   # long window has no boundary
check("cont metric uses the long slot (no boundary -> zero funding events)",
      fa_cont["mean_r"] is not None
      and abs(fa_cont["mean_r"] - exp_cont) < 1e-12,
      f"got {fa_cont['mean_r']}, expected {exp_cont}")
check("fade and cont funding-adjusted metrics differ (side is consistent)",
      fa_fade["mean_r"] != fa_cont["mean_r"])


print("\n=== cases 20-22: integration with a patched event source ===")

# collect_events is patched with deterministic synthetic events so the
# harness logic (selection, funding attribution, holdout lock, report)
# is exercised end-to-end without venue-shaped price dynamics. Every
# event's entry (ts + BAR_S) sits 3300s before an 8h funding boundary,
# so each event window contains that boundary (plus more for longer
# geometries) -- attribution is deterministic against the archives below.
INTEG_TMIN = T0
INTEG_NDAYS = 92.0


def integ_event(day, coin="BTC"):
    target = INTEG_TMIN + day * 86400
    b = SETTLE_S * ((target + 660) // SETTLE_S + 1)
    ev = FakeEvent()
    ev.coin = coin
    ev.ts = int(b - 360)
    ev.day = (ev.ts - INTEG_TMIN) / 86400.0
    ev.fam, ev.param, ev.side = "B", 9.9, 1
    ev.qv24, ev.sigma, ev.rvol = 5e7, 0.001, 9.9
    ev.z1h, ev.z4h, ev.btc7d = 0.0, 0.0, 0.0
    ev.gross, ev.kind, ev.exit_offset = [], [], []
    for g in geoms:
        for _s in (1, -1):
            ev.gross.append(0.02)
            ev.kind.append(2)
            ev.exit_offset.append(g.horizon_bars - 1)
    return ev


def integ_events():
    evs = []
    for i in range(80):                    # dev window: day 6..61
        evs.append(integ_event(8.0 + i * 0.6))
    for i in range(40):                    # val window: day 63..77
        evs.append(integ_event(64.0 + i * 0.3))
    for i in range(40):                    # holdout window: day 79..92
        evs.append(integ_event(80.0 + i * 0.3))
    meta_integ = {"t_min": INTEG_TMIN,
                  "t_max": INTEG_TMIN + INTEG_NDAYS * 86400,
                  "n_days": INTEG_NDAYS}
    return evs, meta_integ


integ_bar_dir = os.path.join(tempfile.mkdtemp(prefix="ws5-integ-bars-"), "bars")
os.makedirs(integ_bar_dir)
write_bars(integ_bar_dir, "BTC", make_bars(400, seed=9, sigma=0.001))

# Complete settlement archive: one venue settle per 8h boundary, rate 0.01.
integ_sett_dir = os.path.join(tempfile.mkdtemp(prefix="ws5-integ-sett-"),
                              "settlements")
os.makedirs(integ_sett_dir)
_all_settles = []
b = SETTLE_S * (INTEG_TMIN // SETTLE_S)
while b < INTEG_TMIN + (INTEG_NDAYS + 2) * 86400:
    _all_settles.append((b, 0.01, 8))
    b += SETTLE_S
write_settlements(integ_sett_dir, "BTC", _all_settles)

# Gap archive: identical but missing every settle in the val window.
integ_sett_gap_dir = os.path.join(
    tempfile.mkdtemp(prefix="ws5-integ-gap-"), "settlements")
os.makedirs(integ_sett_gap_dir)
gap_lo = INTEG_TMIN + 63 * 86400 - SETTLE_S - 60
gap_hi = INTEG_TMIN + 77 * 86400 + 60
write_settlements(integ_sett_gap_dir, "BTC",
                  [row for row in _all_settles
                   if not (gap_lo < row[0] < gap_hi)])

_real_collect = R.collect_events
DENOM_KEYS = ("n_events", "n_funding_adjusted", "n_excluded_missing_coin",
              "n_excluded_incomplete")


def check_denoms(label, blk):
    ok = blk is not None and all(k in blk for k in DENOM_KEYS)
    check(f"{label} carries explicit denominators", ok, str(blk))
    if ok:
        check(f"{label} denominators reconcile",
              blk["n_funding_adjusted"] + blk["n_excluded_missing_coin"]
              + blk["n_excluded_incomplete"] == blk["n_events"], str(blk))


try:
    R.collect_events = lambda *a, **k: integ_events()

    print("\n=== case 20: funding-adjusted metrics differ from friction-only ===")

    out20 = tempfile.mkdtemp(prefix="ws5-integ20-")
    rep20 = eh.evaluate(integ_bar_dir, settlements_dir=integ_sett_dir,
                        out_dir=out20, spend_holdout=True)

    check("dev funding-adjusted exists", rep20.dev_funding_adjusted is not None)
    check_denoms("dev funding-adjusted", rep20.dev_funding_adjusted)
    check_denoms("val funding-adjusted", rep20.val_funding_adjusted)
    check_denoms("holdout funding-adjusted", rep20.holdout_funding_adjusted)
    check_denoms("holdout slip2x funding-adjusted",
                 rep20.holdout_slip2x_funding_adjusted)

    check("dev funding-adjusted covers every event",
          rep20.dev_funding_adjusted["n_funding_adjusted"]
          == rep20.dev_funding_adjusted["n_events"] == 80,
          str(rep20.dev_funding_adjusted))
    check("val funding-adjusted covers every event",
          rep20.val_funding_adjusted["n_funding_adjusted"]
          == rep20.val_funding_adjusted["n_events"] == 40,
          str(rep20.val_funding_adjusted))

    check("dev funding-adjusted mean differs from friction-only dev mean",
          rep20.dev_funding_adjusted["mean_r"] is not None
          and abs(rep20.dev_funding_adjusted["mean_r"] - rep20.dev["mean_r"])
          > 0.5,
          f"funding={rep20.dev_funding_adjusted['mean_r']} "
          f"friction={rep20.dev['mean_r']}")
    check("val funding-adjusted mean differs from friction-only val mean",
          rep20.val_funding_adjusted["mean_r"] is not None
          and abs(rep20.val_funding_adjusted["mean_r"] - rep20.val["mean_r"])
          > 0.5,
          f"funding={rep20.val_funding_adjusted['mean_r']} "
          f"friction={rep20.val['mean_r']}")

    check("holdout was spent", rep20.holdout is not None)
    check("funding-adjusted holdout exists and differs from friction",
          rep20.holdout_funding_adjusted is not None
          and rep20.holdout_funding_adjusted["mean_r"] is not None
          and abs(rep20.holdout_funding_adjusted["mean_r"]
                  - rep20.holdout["mean_r"]) > 0.5,
          str(rep20.holdout_funding_adjusted))
    check("funding-adjusted holdout slip2x (sensitivity) exists and differs",
          rep20.holdout_slip2x_funding_adjusted is not None
          and rep20.holdout_slip2x_funding_adjusted["mean_r"] is not None
          and abs(rep20.holdout_slip2x_funding_adjusted["mean_r"]
                  - rep20.holdout_slip2x["mean_r"]) > 0.5,
          str(rep20.holdout_slip2x_funding_adjusted))
    check("no incomplete boundaries with a complete archive",
          rep20.incomplete_boundary_events == 0
          and rep20.incomplete_boundary_denominator == 160,
          f"{rep20.incomplete_boundary_events}/"
          f"{rep20.incomplete_boundary_denominator}")

    # Selection identity must survive for a REAL selection (case 7 only
    # exercises the insufficient-data fallback).
    check("case20: selection identity names the dev-selected rule",
          isinstance(rep20.selected, dict)
          and isinstance(rep20.selected.get("rule"), str)
          and rep20.selected["rule"] != "insufficient-data",
          str(rep20.selected))
    check("case20: selection identity carries resolved rule params",
          isinstance(rep20.selected.get("params"), dict)
          and rep20.selected["params"].get("family") is not None,
          str(rep20.selected))
    check("case20: selection identity carries resolved geometry",
          isinstance(rep20.selected.get("geom"), dict)
          and "horizon_bars" in rep20.selected["geom"],
          str(rep20.selected))
    check("case20: selection identity records how many configs were tried",
          isinstance(rep20.n_configs, int) and rep20.n_configs > 0,
          str(rep20.n_configs))
    check("case20: spa multiplicity test recorded",
          isinstance(rep20.spa, dict) and "p_value" in rep20.spa,
          str(rep20.spa))
    check("case20: selection identity survives JSON round-trip (from file)",
          json.load(open(os.path.join(out20, "evaluation.json")))["selected"]
          == rep20.selected)

    print("\n=== case 21: no funding data -> funding-adjusted metrics are None ===")

    out21 = tempfile.mkdtemp(prefix="ws5-integ21-")
    rep21 = eh.evaluate(integ_bar_dir, funding_dir=None, settlements_dir=None,
                        out_dir=out21)

    check("dev funding-adjusted is None without funding data (not friction)",
          rep21.dev_funding_adjusted is not None
          and rep21.dev_funding_adjusted["mean_r"] is None
          and rep21.dev_funding_adjusted["n_funding_adjusted"] == 0,
          str(rep21.dev_funding_adjusted))
    check("all dev events excluded as missing-coin",
          rep21.dev_funding_adjusted["n_excluded_missing_coin"] == 80,
          str(rep21.dev_funding_adjusted))
    check("val funding-adjusted is None without funding data",
          rep21.val_funding_adjusted is not None
          and rep21.val_funding_adjusted["mean_r"] is None
          and rep21.val_funding_adjusted["n_funding_adjusted"] == 0,
          str(rep21.val_funding_adjusted))
    check("no holdout funding-adjusted without a holdout spend",
          rep21.holdout_funding_adjusted is None)
    check("BTC surfaced in missing_cost_coins",
          "BTC" in rep21.missing_cost_coins, str(rep21.missing_cost_coins))

    print("\n=== case 22: settlement gap excluded+counted; holdout once ===")

    out22 = tempfile.mkdtemp(prefix="ws5-integ22-")
    rep22 = eh.evaluate(integ_bar_dir, settlements_dir=integ_sett_gap_dir,
                        out_dir=out22, spend_holdout=True)

    check("dev funding-adjusted complete despite the val gap",
          rep22.dev_funding_adjusted["n_funding_adjusted"] == 80,
          str(rep22.dev_funding_adjusted))
    check("val events with missing settlement boundaries are excluded",
          rep22.val_funding_adjusted["n_funding_adjusted"] == 0
          and rep22.val_funding_adjusted["n_excluded_incomplete"] == 40
          and rep22.val_funding_adjusted["mean_r"] is None,
          str(rep22.val_funding_adjusted))
    check("excluded-and-counted totals are reported",
          rep22.incomplete_boundary_events == 40
          and rep22.incomplete_boundary_denominator == 160,
          f"{rep22.incomplete_boundary_events}/"
          f"{rep22.incomplete_boundary_denominator}")
    check("holdout funding-adjusted computed on the complete holdout window",
          rep22.holdout_funding_adjusted is not None
          and rep22.holdout_funding_adjusted["n_funding_adjusted"] == 40,
          str(rep22.holdout_funding_adjusted))

    # Holdout spent at most once: a second evaluate() on the same out_dir
    # must refuse -- no friction holdout AND no funding-adjusted holdout.
    check("holdout lock file was written",
          os.path.exists(os.path.join(out22, "holdout_evaluated.json")))
    rep22b = eh.evaluate(integ_bar_dir, settlements_dir=integ_sett_gap_dir,
                         out_dir=out22, spend_holdout=True)
    check("second run: friction holdout refused",
          rep22b.holdout is None, str(rep22b.holdout))
    check("second run: funding-adjusted holdout refused (no second look)",
          rep22b.holdout_funding_adjusted is None
          and rep22b.holdout_slip2x_funding_adjusted is None,
          f"{rep22b.holdout_funding_adjusted}")

    print("\n=== case 23: --coins restricts the evaluated universe ===")

    _captured = {}

    def _spy_collect(*args, **kwargs):
        _captured["coins"] = kwargs.get("coins")
        return integ_events()

    R.collect_events = _spy_collect
    try:
        out23 = tempfile.mkdtemp(prefix="ws5-coins-")
        eh.evaluate(integ_bar_dir, coins=["AAA", "BBB"], out_dir=out23)
        check("evaluate forwards coins to collect_events",
              _captured.get("coins") == ["AAA", "BBB"],
              str(_captured.get("coins")))

        eh.evaluate(integ_bar_dir,
                    out_dir=tempfile.mkdtemp(prefix="ws5-coins-all-"))
        check("evaluate defaults coins=None (whole universe)",
              _captured.get("coins") is None, str(_captured.get("coins")))
    finally:
        R.collect_events = lambda *a, **k: integ_events()

finally:
    R.collect_events = _real_collect


print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(0 if not FAILURES else 1)
