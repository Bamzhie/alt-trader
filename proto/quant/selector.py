"""Budgeted, auditable selection.  Every suppressed candidate is counted with
a reason; nothing is dropped silently.

Gates, in order:
  rule_disabled -> liquidity -> cost_r -> cooldown -> coin_day_cap
  then, for PROMOTED rules only: ev_below_hurdle -> daily_budget -> exposure
Shadow (unpromoted) rules skip the EV/budget/exposure gates because they are
research logging, not recommendations; they have their own bounded daily cap.
"""
from .config import BAR_S, BARS_PER_DAY


def utc_day(ts):
    return ts - (ts % 86400)


class Selector:
    def __init__(self, cfg, store, exposure=None):
        self.cfg = cfg
        self.store = store
        self.exposure = exposure

    def decide(self, spec, coin, bar_ts, qv24, stop_frac, promotion=None,
               cluster=None, risk_frac_eff=0.0):
        """Returns (emit: bool, reason: str, mode: 'SHADOW'|'ACTIONABLE',
        cost_frac: float|None)."""
        sel = self.cfg.selector
        rid = spec.rule_id
        mode = "ACTIONABLE" if promotion else "SHADOW"
        if self.store.get_meta(f"disable:{rid}") == "1":
            return False, "rule_disabled", mode, None
        cost = self.cfg.cost.round_trip(qv24)
        if cost is None:
            return False, "liquidity", mode, None
        if cost / stop_frac > sel.max_cost_r:
            return False, "cost_r", mode, cost
        last = self.store.last_event_ts(rid, coin)
        if last is not None and bar_ts - last < sel.cooldown_bars * BAR_S:
            return False, "cooldown", mode, cost
        d0 = utc_day(bar_ts)
        if self.store.count_events_day(rid, d0, d0 + 86400, coin) >= sel.max_per_coin_per_day:
            return False, "coin_day_cap", mode, cost
        if mode == "SHADOW":
            if self.store.count_events_day(rid, d0, d0 + 86400) >= sel.shadow_max_per_day:
                return False, "shadow_daily_cap", mode, cost
            return True, "", mode, cost
        ev = promotion.get("expected_net_r", 0.0)
        if ev < sel.min_ev_r:
            return False, "ev_below_hurdle", mode, cost
        n_act = self.store.conn.execute(
            "SELECT COUNT(*) FROM quant_event WHERE mode='ACTIONABLE' AND "
            "bar_ts>=? AND bar_ts<?", (d0, d0 + 86400)).fetchone()[0]
        if n_act >= sel.max_per_day:
            return False, "daily_budget", mode, cost
        if self.exposure is not None:
            ok, why = self.exposure.can_add(cluster, risk_frac_eff)
            if not ok:
                return False, "exposure:" + why, mode, cost
        return True, "", mode, cost
