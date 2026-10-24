"""Historical backtest engine: replay versioned calculations over data/bars/.

This engine connects the existing quant primitives (stream, barrier, roe,
stats) to the +100% / -250% ROE evaluator.  It generates its OWN candidates by
replaying FeatureStream over the bar archive; it does NOT read quant.db or
signals.db.

ROE convention (matches roe.py):
  simple return  move = exp(gross_log) - 1   (long)  / 1 - exp(-gross_log)  (short)
  roe            = leverage * (move - cost_frac)

Pass criterion: a coin "passes" if its selected signal achieves +100% ROE
before -250% ROE.  A single signal per coin is selected (configurable rule).
"""
import math

from . import barrier as B
from . import stats as S
from .bars import load_coin, btc_returns, contiguous_runs
from .config import DEFAULT, BARS_PER_DAY, BAR_S, Splits, RuleParams, Geometry
from .stream import FeatureStream, Cusum
from .research import SHOCK_HS, MAX_H
from .config import GRID_RVOL

TARGET_ROE = 1.0      # +100%
STOP_ROE = 2.5        # -250% (passed as positive; sign applied by context)
_DEFAULT_LEVERAGE = 10.0


def _btc7d_map(btc):
    """ts -> 7-day BTC log return (used by residual_shock candidates)."""
    ts, c = btc["ts"], btc["c"]
    out = {}
    lag = 7 * BARS_PER_DAY
    for i in range(lag, len(ts)):
        if ts[i] - ts[i - lag] == lag * BAR_S:
            out[ts[i]] = math.log(c[i] / c[i - lag])
    return out


class BacktestContext:
    """Shared state for a backtest run: BTC market factor + archive span.

    Loaded once and passed to evaluate_coin so BTC bars and geometry are not
    re-read for every coin.
    """

    def __init__(self, data_dir, cfg=DEFAULT):
        self.data_dir = data_dir
        self.cfg = cfg
        self.btc = load_coin(data_dir, "BTC")
        if self.btc is None:
            raise ValueError("BTC bars required as the market factor")
        self.btc_returns = btc_returns(self.btc)
        self.btc7d = _btc7d_map(self.btc)
        self.t_min = min(self.btc["ts"])
        self.t_max = max(self.btc["ts"])
        self.meta = {"t_min": self.t_min, "t_max": self.t_max,
                     "n_days": (self.t_max - self.t_min) / 86400.0}

    def btc_ret(self, ts):
        return self.btc_returns.get(ts, 0.0)


def _geometry_index(geom, geoms):
    """Index of `geom` in the grid produced by grid_geometries(), or -1."""
    for i, g in enumerate(geoms):
        if g.horizon_bars == geom.horizon_bars and g.k_stop == geom.k_stop \
                and g.r_tp == geom.r_tp:
            return i
    return -1


def _resolve_sideg(gross_log, side):
    """Simple price move from a gross log return, per roe.empirical_rates."""
    gl = gross_log
    if side > 0:
        return math.exp(gl) - 1.0
    return 1.0 - math.exp(-gl)


def _net_roe(gross_log, side, cost_frac, leverage):
    """ROE from a gross log label: leverage * (move - cost).  Matches roe.py."""
    move = _resolve_sideg(gross_log, side)
    return leverage * (move - cost_frac)


def evaluate_coin(ctx, coin, cfg=DEFAULT, geometry=None, splits=None,
                  rule_params=None):
    """Walk FeatureStream for one coin; label each candidate; apply policy.

    Returns a list of dicts (one per candidate signal), each carrying the
    resolved barrier outcome for the selected geometry, net R, ROE, and the
    policy-adjusted side.  Candidates are filtered to the split window and
    gap-safe (a label path never crosses a timestamp gap).

    Parameters
    ----------
    geometry : Geometry or None
        If None, the first grid geometry is used (T=144, k_stop=3.0, r_tp=1.0).
    splits : Splits or None
        If None, ctx.cfg.splits is used.
    rule_params : RuleParams or None
        If None, a default breakout_start fade rule is used.
    """
    geoms = [geometry] if geometry is not None else [DEFAULT_GEOM]
    geom = geoms[0]
    gi = _geometry_index(geom, _geometry_list())

    rp = rule_params or DEFAULT_RULE
    sp = splits or ctx.cfg.splits
    cost = ctx.cfg.cost

    bars = load_coin(ctx.data_dir, coin)
    if not bars:
        return []

    o, h, l, c, amt, ts = (bars[k] for k in ("o", "h", "l", "c", "amt", "ts"))

    # --- window bounds: filter to the pre-holdout window (dev + validation)
    #    so no outcome can leak into a sealed holdout.
    n_days = ctx.meta["n_days"]
    bounds = sp.bounds(n_days)
    dev_lo, dev_hi = bounds["dev"]
    _, val_hi = bounds["val"]
    hold_lo = bounds["holdout"][0]
    # Pre-holdout window: dev + validation.  Embargo is enforced by the split
    # (val ends 2d before holdout starts) so an entry here can never reach into
    # the holdout via its horizon.
    win_lo = ts[0] + dev_lo * 86400.0
    win_hi = ts[0] + val_hi * 86400.0  # entries must start before this
    hold_start_ts = ts[0] + hold_lo * 86400.0
    horizon_s = geom.horizon_bars * BAR_S

    # Pre-compute cost per event qv24 (constant per coin per window, like roe.py).
    # The trailing-24h window at the last bar of the window must be contiguous.
    # For per-event cost we use the event's own qv24 (matches research.py).

    rho_min = min(GRID_RVOL)
    results = []

    for (run_a, run_z) in contiguous_runs(ts):
        st = FeatureStream(ctx.cfg.feat)
        cus = {hh: Cusum(hh, ctx.cfg.feat.cusum_k, ctx.cfg.feat.cusum_cooldown)
               for hh in SHOCK_HS}
        for i in range(run_a, run_z):
            t_i = ts[i]
            # --- split window filter
            if t_i < win_lo or t_i >= win_hi:
                continue
            # --- gap safety: the label horizon must not cross into holdout
            if t_i + horizon_s >= hold_start_ts:
                continue
            try:
                snap = st.update(t_i, o[i], h[i], l[i], c[i], amt[i],
                                 ctx.btc_ret(t_i))
            except ValueError:
                st = FeatureStream(ctx.cfg.feat)
                cus = {hh: Cusum(hh, ctx.cfg.feat.cusum_k, ctx.cfg.feat.cusum_cooldown)
                       for hh in SHOCK_HS}
                continue
            if snap is None:
                continue

            # --- candidate detection (mirrors research.collect_events)
            cands = []
            if rp.family == "breakout_start":
                if (snap.up_start or snap.dn_start) and snap.rvol is not None \
                        and snap.rvol >= rho_min:
                    cands.append(("B", snap.rvol,
                                  1 if snap.up_start else -1))
            elif rp.family == "residual_shock":
                for hh, cu in cus.items():
                    s = cu.update(snap.n, snap.z)
                    if s:
                        cands.append(("S", hh, s))
            if not cands:
                continue
            if snap.qv24 < cost.min_qv24:
                continue
            if snap.sigma <= 1e-6:
                continue

            # --- barrier labeling across the grid
            path = B.make_path(o, h, l, c, i, MAX_H, run_end=run_z)
            if path is None:
                continue

            dd = B.stop_distance(snap.sigma, geom.horizon_bars, geom.k_stop)
            # For long side, side=+1; for short, side=-1.
            # Geom index maps: geom_idx * 2 + (0 if side>0 else 1)
            gross_long, kind_long, off_long = B.label_path(
                path, 1, dd, geom.horizon_bars, geom.r_tp)
            gross_short, kind_short, off_short = B.label_path(
                path, -1, dd, geom.horizon_bars, geom.r_tp)

            cost_frac = cost.round_trip(snap.qv24)
            if cost_frac is None:
                continue

            for fam, param, raw_side in cands:
                # policy: "fade" reverses the raw signal side; "cont" keeps it.
                side = -raw_side if rp.policy == "fade" else raw_side
                if side > 0:
                    gross_log, kind, exit_off = gross_long, kind_long, off_long
                else:
                    gross_log, kind, exit_off = gross_short, kind_short, off_short
                nr = B.net_r(gross_log, dd, cost_frac)
                roe = _net_roe(gross_log, side, cost_frac, _DEFAULT_LEVERAGE)
                results.append({
                    "coin": coin,
                    "ts": snap.ts,
                    "day": snap.ts / 86400.0,
                    "fam": fam,
                    "param": param,
                    "side": side,
                    "policy": rp.policy,
                    "sigma": snap.sigma,
                    "qv24": snap.qv24,
                    "rvol": snap.rvol,
                    "geom": geom.key(),
                    "geom_idx": gi,
                    "cost_frac": cost_frac,
                    "leverage": _DEFAULT_LEVERAGE,
                    "gross_log": gross_log,
                    "kind": kind,
                    "exit_offset": exit_off,
                    "net_r": nr,
                    "roe": roe,
                })
    return results


def evaluate_cycle(ctx, coins, rule_params=None, geometry=None, cfg=DEFAULT):
    """Evaluate one rule+geometry across coins; return per-coin pass/fail.

    A coin "passes" if its best (selected) signal achieves +100% ROE before
    -250% ROE.  We select the signal with the highest net R per coin and check
    its ROE against the target/stop.

    Returns {coin: {pass, roe, kind, exit_offset, ...}} for coins that have at
    least one candidate signal.
    """
    rp = rule_params or DEFAULT_RULE
    per_coin = {}
    for coin in coins:
        if coin == "BTC":
            continue
        events = evaluate_coin(ctx, coin, cfg, geometry, ctx.cfg.splits, rp)
        if not events:
            continue
        # pick the best signal by net R (the one most likely to pass)
        best = max(events, key=lambda e: e["net_r"])
        roe = best["roe"]
        if best["kind"] == "TP":
            passed = roe >= TARGET_ROE       # +100% ROE
        elif best["kind"] == "SL":
            passed = False                   # stop hit first => fail
        else:
            passed = roe >= TARGET_ROE       # TIME: only pass if +100% reached
        per_coin[best["coin"]] = {
            "pass": passed,
            "roe": roe,
            "kind": best["kind"],
            "exit_offset": best["exit_offset"],
            "net_r": best["net_r"],
            "ts": best["ts"],
            "side": best["side"],
            "geom": best["geom"],
        }
    return per_coin


# module-level defaults (frozen, versioned like config.py)
DEFAULT_GEOM = Geometry(horizon_bars=144, k_stop=3.0, r_tp=1.0)
DEFAULT_RULE = RuleParams(family="breakout_start", policy="fade", rvol_min=4.0)


def _geometry_list():
    from .config import grid_geometries
    return grid_geometries()


def run_backtest(data_dir, coins, cfg=DEFAULT, splits=None,
                 rule_params=None, geometry=None):
    """Orchestrate: load BTC, walk each coin, summarize with block_cluster_mean.

    Returns:
        {coin: {pass, roe, kind, exit_offset, ...},
         aggregate: {pass_rate, n, mean_roe, ci_lo, ci_hi}}
    """
    ctx = BacktestContext(data_dir, cfg)
    # Use the full pre-holdout window (dev + val) for measurement, matching
    # the roe.py CLI which measures on dev+val only.
    sp = splits or cfg.splits
    rp = rule_params or DEFAULT_RULE
    geom = geometry or DEFAULT_GEOM

    coin_results = evaluate_cycle(ctx, coins, rp, geom, cfg)

    # --- aggregate
    roes = []
    roe_days = []
    pass_count = 0
    n = 0
    for coin, r in coin_results.items():
        roes.append(r["roe"])
        # day index relative to archive start
        roe_days.append(int(ctx.meta["t_min"] and
                            (r["ts"] - ctx.t_min) / 86400.0))
        if r["pass"]:
            pass_count += 1
        n += 1

    if roes:
        m, lo, hi, _ = S.block_cluster_mean(
            roes, roe_days, block=3, B=600)
        mean_roe = m
        ci_lo = lo
        ci_hi = hi
    else:
        mean_roe = float("nan")
        ci_lo = float("nan")
        ci_hi = float("nan")

    aggregate = {
        "pass_rate": pass_count / n if n else float("nan"),
        "n": n,
        "mean_roe": mean_roe,
        "ci_lo": ci_lo,
        "ci_hi": ci_hi,
    }

    out = dict(coin_results)
    out["aggregate"] = aggregate
    return out
