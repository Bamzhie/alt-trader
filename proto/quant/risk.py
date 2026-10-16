"""Plan levels, position sizing and exposure context (informational only).

This app never trades.  Sizing answers "if I took this, what would 5% risk
mean" under the operator's stated setup: fixed fractional risk per trade,
cross margin.  Under cross margin, liquidation depends on the WHOLE account,
so the per-trade leverage cap and the aggregate-open-risk cap below are the
protections; the isolated-margin liquidation distance is shown as a floor
check, not as the real liquidation price.
"""
import math

from . import barrier as B

MAINT_MARGIN = 0.005         # assumed maintenance margin fraction (not venue-verified)


def plan_levels(entry, side, sigma, geom):
    """Stop/target from volatility (not from a swing point)."""
    dd = B.stop_distance(sigma, geom.horizon_bars, geom.k_stop)
    if side > 0:
        stop = entry * math.exp(-dd)
        tp = entry * math.exp(geom.r_tp * dd)
    else:
        stop = entry * math.exp(dd)
        tp = entry * math.exp(-geom.r_tp * dd)
    return {"entry": entry, "stop": stop, "tp": tp, "dd": dd,
            "stop_frac": abs(stop / entry - 1.0),
            "tp_frac": abs(tp / entry - 1.0)}


def size_position(equity, stop_frac, risk_params):
    """Notional so that a stop-out loses risk_frac of equity, leverage-capped."""
    risk_amt = equity * risk_params.risk_frac
    notional = risk_amt / stop_frac
    leverage = notional / equity
    capped = False
    if leverage > risk_params.max_leverage:
        leverage = risk_params.max_leverage
        notional = equity * leverage
        capped = True
    realized_risk = notional * stop_frac
    liq_dist = max(1.0 / leverage - MAINT_MARGIN, 0.0)
    warnings = []
    if capped:
        warnings.append(f"leverage capped at {risk_params.max_leverage:g}x: "
                        f"real risk {realized_risk / equity:.1%} of equity, "
                        f"below the {risk_params.risk_frac:.0%} target")
    if stop_frac > 0.8 * liq_dist:
        warnings.append("stop is not comfortably inside the isolated-margin "
                        "liquidation distance")
    return {"notional": notional, "leverage": leverage,
            "risk_amount": realized_risk, "risk_frac_eff": realized_risk / equity,
            "capped": capped, "liq_distance": liq_dist, "warnings": warnings}


def correlation_clusters(returns_by_coin, threshold=0.6, min_len=24):
    """Greedy single-linkage clusters on return correlation.

    returns_by_coin: {coin: [r...]} aligned, equal-length recent returns.
    Coins with too little history get their own cluster (never merged by
    guess).  Deterministic (sorted).
    """
    coins = sorted(returns_by_coin)
    usable = [c for c in coins if len(returns_by_coin[c]) >= min_len]
    cl = {}
    nid = 0

    def corr(a, b):
        n = min(len(a), len(b))
        a, b = a[-n:], b[-n:]
        ma, mb = sum(a) / n, sum(b) / n
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((y - mb) ** 2 for y in b)
        if va <= 0 or vb <= 0:
            return 0.0
        return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)

    for c in usable:
        joined = None
        for o in usable:
            if o == c or o not in cl:
                continue
            if corr(returns_by_coin[c], returns_by_coin[o]) >= threshold:
                joined = cl[o]
                break
        if joined is None:
            joined = nid
            nid += 1
        cl[c] = joined
    for c in coins:
        if c not in cl:
            cl[c] = nid
            nid += 1
    return cl


class Exposure:
    """Open informational positions: aggregate risk and per-cluster counts."""
    def __init__(self, risk_params):
        self.rp = risk_params
        self.open = {}               # event_id -> (cluster, risk_frac_eff)

    def add(self, event_id, cluster, risk_frac_eff):
        self.open[event_id] = (cluster, risk_frac_eff)

    def remove(self, event_id):
        self.open.pop(event_id, None)

    def total_risk(self):
        return sum(r for _, r in self.open.values())

    def cluster_count(self, cluster):
        return sum(1 for c, _ in self.open.values() if c == cluster)

    def can_add(self, cluster, risk_frac_eff):
        if self.total_risk() + risk_frac_eff > self.rp.max_open_risk_frac + 1e-12:
            return False, "aggregate open risk cap"
        if self.cluster_count(cluster) >= self.rp.max_corr_cluster_open:
            return False, "correlated-cluster cap"
        return True, ""
