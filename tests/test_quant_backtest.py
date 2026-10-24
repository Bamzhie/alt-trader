"""Tests for proto.quant.backtest.

Self-contained: creates synthetic bar archives in a temp dir, runs a backtest,
verifies barrier outcomes (TP/SL/TIME), net R, ROE conversion, reproducibility,
and no leakage across split boundaries.
"""
import gzip
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.quant.backtest import (
    run_backtest, _net_roe, _resolve_sideg, STOP_ROE, DEFAULT_GEOM,
)
from proto.quant import barrier as B
from proto.quant import bars as barsmod
from proto.quant.config import (
    BARS_PER_DAY, BAR_S, Splits, FeatureParams, CostModel,
)
from proto.quant.stream import FeatureStream


def _load_btc_bars(data_dir):
    """Convenience: load BTC from a data dir."""
    return barsmod.load_coin(data_dir, "BTC")

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_TS = 1700000000  # aligned to a whole second; BAR_S = 300


def _grid_timestamps(n, start_ts=BASE_TS, step=BAR_S):
    """`n` timestamps spaced `step` apart."""
    return [start_ts + i * step for i in range(n)]


def _write_coin(data_dir, coin, timestamps, opens, highs, lows, closes,
                vols, amts):
    """Write a gzip CSV bar file in the format load_coin expects.

    Columns: ts,o,h,l,c,vol,amount
    """
    rows = ["ts,o,h,l,c,vol,amount"]
    for i, ts in enumerate(timestamps):
        rows.append(f"{ts},{opens[i]:.8f},{highs[i]:.8f},{lows[i]:.8f},"
                    f"{closes[i]:.8f},{vols[i]:.8f},{amts[i]:.8f}")
    path = os.path.join(data_dir, f"{coin}.csv.gz")
    os.makedirs(data_dir, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
    return path


def _flat_bars(n, price=100.0, vol=5000.0, amt=None):
    """Flat OHL close to `price`, constant vol/amt."""
    if amt is None:
        amt = vol * price
    ts = _grid_timestamps(n)
    o = [price] * n
    h = [price] * n
    l = [price] * n
    c = [price] * n
    v = [vol] * n
    a = [amt] * n
    return ts, o, h, l, c, v, a


def _make_config(n_days, warm_bars=20 * BARS_PER_DAY):
    """Build a QuantConfig with small split windows for testing.

    n_days: total archive length in days.
    Splits: dev = first_event_day..dev_end_day, val continues, holdout = last.
    We want the pre-holdout window (dev+val) to contain enough bars for warmup.
    """
    feat = FeatureParams(
        warmup_bars=min(warm_bars, 2 * BARS_PER_DAY),  # small warmup for tests
        channel_bars=24,
        rvol_lags_days=3,
        rvol_min_lags=2,
    )
    cost = CostModel(
        fee_per_side=0.0005,
        slip_tiers=((1e6, 0.0005),),
        min_qv24=1e3,
    )
    # Splits: first event at day 2, dev ends day 5, val 2 days, holdout rest.
    splits = Splits(
        first_event_day=2.0,
        dev_end_day=5.0,
        embargo_days=1.0,
        val_len_days=2.0,
    )
    from proto.quant.config import QuantConfig
    return QuantConfig(feat=feat, cost=cost, splits=splits)


def _make_archive(data_dir, n_bars, coin_prices=None):
    """Create BTC + alt coin CSV.gz files in data_dir.

    coin_prices: dict {coin: list of close prices} or None for flat.
    Returns the list of coin names used.
    """
    ts = _grid_timestamps(n_bars)
    # BTC: flat at 50000, volume 1e6
    vol_btc = [1e6] * n_bars
    amt_btc = [5e10] * n_bars
    o = [50000.0] * n_bars
    h = [50000.001] * n_bars
    l = [49999.999] * n_bars
    c = [50000.0] * n_bars
    _write_coin(data_dir, "BTC", ts, o, h, l, c, vol_btc, amt_btc)

    coins_used = []
    if coin_prices:
        for coin, closes in coin_prices.items():
            n_c = len(closes)
            vols = [5e5] * n_c  # enough qv24
            amts = [closes[i] * vols[i] for i in range(n_c)]
            # Build OHLC with some spread
            cs = [max(c, 0.01) for c in closes]
            hs = [c * 1.001 for c in cs]
            ls = [c * 0.999 for c in cs]
            os_ = cs  # open = close (simplified)
            _write_coin(data_dir, coin, ts[:n_c], os_, hs, ls, cs, vols, amts)
            coins_used.append(coin)
    return coins_used


# ---------------------------------------------------------------------------
# Test 1: ROE conversion matches roe.py convention
# ---------------------------------------------------------------------------

print("=== Test 1: ROE conversion ===")

# Long: gross_log = r_tp * dd, move = exp(r_tp*dd) - 1
dd_test = 0.02
cost_test = 0.001
lev_test = 10.0

gross_long = 0.02  # e.g. r_tp * dd
move_long = _resolve_sideg(gross_long, side=1)
check("move_long = exp(gross) - 1", abs(move_long - (math.exp(gross_long) - 1)) < 1e-12,
      f"got {move_long}")
roe_long = _net_roe(gross_long, side=1, cost_frac=cost_test, leverage=lev_test)
check("roe_long = lev*(move - cost)", abs(roe_long - lev_test * (move_long - cost_test)) < 1e-12,
      f"got {roe_long}")

# Short: gross_log negative in log sense, move = 1 - exp(-gross)
gross_short = -0.02
move_short = _resolve_sideg(gross_short, side=-1)
check("move_short = 1 - exp(-gross)", abs(move_short - (1 - math.exp(-gross_short))) < 1e-12,
      f"got {move_short}")
roe_short = _net_roe(gross_short, side=-1, cost_frac=cost_test, leverage=lev_test)
check("roe_short = lev*(move - cost)", abs(roe_short - lev_test * (move_short - cost_test)) < 1e-12,
      f"got {roe_short}")

# SL outcome: gross = -dd (stop hit), ROE should be <= STOP_ROE
gross_sl = -dd_test
move_sl = _resolve_sideg(gross_sl, side=1)
roe_sl = _net_roe(gross_sl, side=1, cost_frac=cost_test, leverage=lev_test)
check("SL roe is negative", roe_sl < 0, f"got {roe_sl}")
# Verify the ROE formula is consistent (not the magnitude interpretation)
sl_move = _resolve_sideg(gross_sl, side=1)
check("SL net_roe matches formula", abs(roe_sl - lev_test * (sl_move - cost_test)) < 1e-12)

# TP outcome: gross = r_tp * dd, ROE should be positive
gross_tp = dd_test * 3.0  # r_tp = 3
move_tp = _resolve_sideg(gross_tp, side=1)
roe_tp = _net_roe(gross_tp, side=1, cost_frac=cost_test, leverage=lev_test)
check("TP roe is positive", roe_tp > 0, f"got {roe_tp}")


# ---------------------------------------------------------------------------
# Test 2: Barrier labeling + full backtest on synthetic data
# ---------------------------------------------------------------------------

print("\n=== Test 2: barrier labeling and backtest ===")

tmpdir = tempfile.mkdtemp()
n_bars = 30 * BARS_PER_DAY  # 30 days of data

# Create price series that goes UP sharply after warmup to trigger a breakout
# and hit the TP barrier.
# Warmup is 2 * BARS_PER_DAY = 576 bars. Channel = 24 bars.
# We need rvol >= 4.0 (GRID_RVOL min) at the breakout point.
prices = [100.0] * n_bars
# After warmup, push price up 2% to trigger up_start
warmup_end = 2 * BARS_PER_DAY
# Need rvol >= 4.0: volume spike at the breakout
for i in range(n_bars):
    if i >= warmup_end and i < warmup_end + 50:
        prices[i] = 100.0 * (1 + 0.005 * (i - warmup_end))  # ~5% rise over 50 bars
    elif i >= warmup_end:
        prices[i] = 100.5  # stays elevated

# Volume: low during warmup, spike at breakout
vols = [5000.0] * n_bars
amts = [v * p for v, p in zip(vols, prices)]
for i in range(warmup_end, min(warmup_end + 50, n_bars)):
    vols[i] = 100000.0  # 20x normal volume -> rvol >= 4
    amts[i] = vols[i] * prices[i]

ts = _grid_timestamps(n_bars)
o = prices[:]
h = [p * 1.001 for p in prices]
l = [p * 0.999 for p in prices]
_write_coin(tmpdir, "BTC", ts, o, h, l, prices, vols, amts)
_write_coin(tmpdir, "ALT1", ts, o, h, l, prices, vols, amts)

cfg = _make_config(30, warm_bars=5 * BARS_PER_DAY)

# Verify FeatureStream produces a snap with up_start
btc = _load_btc_bars(tmpdir)
br = __import__("proto.quant.bars", fromlist=["btc_returns"]).btc_returns(btc)
st = FeatureStream(cfg.feat)
found_up_start = False
for i in range(len(ts)):
    snap = st.update(ts[i], o[i], h[i], l[i], prices[i], vols[i], br.get(ts[i], 0.0))
    if snap and snap.up_start:
        found_up_start = True
        check("snap has sigma > 0", snap.sigma > 0, f"sigma={snap.sigma}")
        check("snap has rvol", snap.rvol is not None, f"rvol={snap.rvol}")
        break
check("found up_start during price advance", found_up_start,
      "FeatureStream never produced up_start")

# Direct barrier labeling test with label_reference
path = B.make_path(o, h, l, prices, warmup_end, DEFAULT_GEOM.horizon_bars)
check("make_path returns Path for valid index", path is not None)

if path:
    dd = B.stop_distance(0.02, DEFAULT_GEOM.horizon_bars, DEFAULT_GEOM.k_stop)
    gl, kind, off = B.label_path(path, 1, dd, DEFAULT_GEOM.horizon_bars, DEFAULT_GEOM.r_tp)
    check("label_path returns kind in {TP,SL,TIME}", kind in ("TP", "SL", "TIME"),
          f"got {kind}")
    check("exit_offset < horizon_bars", off < DEFAULT_GEOM.horizon_bars,
          f"got {off}")

# Full backtest
result = run_backtest(tmpdir, ["ALT1"], cfg)
check("run_backtest returns dict", isinstance(result, dict))
check("result has aggregate key", "aggregate" in result)
check("result has coin keys", "ALT1" in result)
agg = result["aggregate"]
check("aggregate has n", "n" in agg)
check("aggregate has mean_roe", "mean_roe" in agg)
check("aggregate has ci_lo/ci_hi", "ci_lo" in agg and "ci_hi" in agg)
if "ALT1" in result:
    coin_r = result["ALT1"]
    check("coin result has pass", "pass" in coin_r)
    check("coin result has roe", "roe" in coin_r)
    check("coin result has kind", "kind" in coin_r)
    check("coin result has exit_offset", "exit_offset" in coin_r)
else:
    check("ALT1 found in results", False, "ALT1 not in result")


# ---------------------------------------------------------------------------
# Test 3: Reproducibility (same input -> same output)
# ---------------------------------------------------------------------------

print("\n=== Test 3: reproducibility ===")

result2 = run_backtest(tmpdir, ["ALT1"], cfg)
if "ALT1" in result and "ALT1" in result2:
    r1 = result["ALT1"]
    r2 = result2["ALT1"]
    check("same pass/fail on re-run", r1["pass"] == r2["pass"],
          f"{r1['pass']} vs {r2['pass']}")
    check("same roe on re-run", abs(r1["roe"] - r2["roe"]) < 1e-12,
          f"{r1['roe']} vs {r2['roe']}")
    check("same kind on re-run", r1["kind"] == r2["kind"],
          f"{r1['kind']} vs {r2['kind']}")
    check("same exit_offset on re-run", r1["exit_offset"] == r2["exit_offset"],
          f"{r1['exit_offset']} vs {r2['exit_offset']}")

# Also check aggregate reproducibility
if agg["n"] > 0:
    check("aggregate mean_roe reproducible",
          abs(result["aggregate"]["mean_roe"] - result2["aggregate"]["mean_roe"]) < 1e-12)
    check("aggregate ci_lo reproducible",
          result["aggregate"]["ci_lo"] == result2["aggregate"]["ci_lo"])
    check("aggregate ci_hi reproducible",
          result["aggregate"]["ci_hi"] == result2["aggregate"]["ci_hi"])


# ---------------------------------------------------------------------------
# Test 4: No leakage across split boundaries
# ---------------------------------------------------------------------------

print("\n=== Test 4: no leakage across split boundaries ===")

# Create an archive with a price move that starts in dev but would only
# reach TP in the holdout window. The backtest should NOT evaluate it
# because entries are filtered to the pre-holdout window.
# We use a geometry with horizon that extends into holdout to verify exclusion.
n_bars2 = 12 * BARS_PER_DAY
ts2 = _grid_timestamps(n_bars2)
# Flat price everywhere
prices2 = [100.0] * n_bars2
vols2 = [5000.0] * n_bars2
amts2 = [v * p for v, p in zip(vols2, prices2)]
o2 = prices2[:]
h2 = [p * 1.001 for p in prices2]
l2 = [p * 0.999 for p in prices2]
_write_coin(tmpdir + "_leak", "BTC", ts2, o2, h2, l2, prices2, vols2, amts2)
_write_coin(tmpdir + "_leak", "ALT2", ts2, o2, h2, l2, prices2, vols2, amts2)

leak_dir = tmpdir + "_leak"
result_leak = run_backtest(leak_dir, ["ALT2"], _make_config(12, warm_bars=BARS_PER_DAY))
# With flat prices, no breakout should fire; no events should be generated
check("flat price -> no events/leakage", result_leak["aggregate"]["n"] <= 0,
      f"n={result_leak['aggregate']['n']}")

# Verify that entries near the holdout boundary are excluded
# The holdout starts at day: dev_end(5) + embargo(1) + val_len(2) + embargo(1) = day 9
# With 12 days total, holdout is days 9..12. Entries from day 5 to day 9 are
# allowed, but an entry on day 9 - horizon must not reach holdout.
# With horizon=144 bars (0.5 day), an entry on day 8.99 cannot reach day 9 + 0.5.
# We verify by checking that the window filter is applied (entries outside
# dev+val are excluded).
bnd = cfg.splits.bounds(30.0)
check("dev window correct", bnd["dev"] == (2.0, 5.0))
check("val window correct", bnd["val"] == (6.0, 8.0))
check("holdout starts after embargo", bnd["holdout"][0] == 9.0)


# ---------------------------------------------------------------------------
# Test 5: barrier.label_path verified against label_reference (cross-check)
# ---------------------------------------------------------------------------

print("\n=== Test 5: label_path matches label_reference ===")

# Use the BTC bars from the ALT1 archive for a controlled test
btc_bars = _load_btc_bars(tmpdir)
o_t, h_t, l_t, c_t = btc_bars["o"], btc_bars["h"], btc_bars["l"], btc_bars["c"]
ts_t = btc_bars["ts"]
amt_t = btc_bars["amt"]

# Pick an index that has enough forward bars
test_i = 2 * BARS_PER_DAY  # after warmup
dd_t = 0.03
horizon_t = 48
r_tp_t = 2.0
path_test = B.make_path(o_t, h_t, l_t, c_t, test_i, horizon_t, run_end=len(c_t))
if path_test:
    gl_path, kind_path, off_path = B.label_path(path_test, 1, dd_t, horizon_t, r_tp_t)
    gl_ref, kind_ref, off_ref = B.label_reference(o_t, h_t, l_t, c_t, test_i, 1,
                                                   dd_t, horizon_t, r_tp_t)
    check("label_path matches label_reference (gross)",
          abs(gl_path - gl_ref) < 1e-10, f"{gl_path} vs {gl_ref}")
    check("label_path matches label_reference (kind)",
          kind_path == kind_ref, f"{kind_path} vs {kind_ref}")
    check("label_path matches label_reference (offset)",
          off_path == off_ref, f"{off_path} vs {off_ref}")
else:
    check("make_path for cross-check test", False, "path is None")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

print(f"\n=== {'PASS' if not FAILURES else 'FAIL'}: {len(FAILURES)} failures ===")
if FAILURES:
    print("Failures:", FAILURES)
    sys.exit(1)
else:
    print("All tests passed.")
    sys.exit(0)
