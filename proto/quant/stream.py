"""Streaming per-coin features, one update per newly CLOSED 5-minute bar.

The same class is used by the research harness and by the live watcher, so
backtest and production cannot drift apart.  All quantities are causal: the
volatility, beta and RVOL baselines used to judge bar t are known before t.

Features:
  sigma    Garman-Klass EWMA volatility per bar (lagged: excludes the bar)
  z        BTC-residual return / lagged residual vol
  res      BTC-residual log return of the bar (beta known before the bar)
  up_start/dn_start  first bar of a run of closes outside the prior-N-bar
                     channel (the channel excludes the bar itself)
  up_age/dn_age      bars since the start of the most recent such run
  rvol     time-of-day relative volume: latest-3-bar quote volume over the
           median of the same slot on each of the previous <=14 days
  qv24     trailing 24h quote volume (liquidity / cost tier input)
  z1h/z4h  trailing residual momentum in residual-vol units
"""
import math
from collections import deque

from .config import FeatureParams, BARS_PER_DAY
from .bars import valid_bar

LN2K = 2.0 * math.log(2.0) - 1.0


class Snap:
    __slots__ = ("n", "ts", "close", "sigma", "z", "res", "sig_res", "beta",
                 "up_start", "dn_start", "up_age", "dn_age", "rvol", "qv24",
                 "z1h", "z4h", "ret1h")

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


class FeatureStream:
    def __init__(self, params=None):
        p = params or FeatureParams()
        self.p = p
        self.n = 0
        self.prev_c = None
        self.s2 = None
        self.cov = 0.0
        self.varb = 1e-9
        self.res2 = None
        self.hi_q = deque(maxlen=p.channel_bars)
        self.lo_q = deque(maxlen=p.channel_bars)
        self.up_prev = False
        self.dn_prev = False
        self.up_run_start = None
        self.dn_run_start = None
        self.amt_ring = [0.0] * BARS_PER_DAY
        self.qv = 0.0
        self.a_last = deque([0.0, 0.0], maxlen=2)
        self.ring_n = BARS_PER_DAY * p.rvol_lags_days + 4
        self.a3_ring = [0.0] * self.ring_n
        self.res_q = deque(maxlen=48)
        self.close_q = deque(maxlen=13)

    def _rvol(self, a3_now):
        p = self.p
        vals = []
        for d in range(1, p.rvol_lags_days + 1):
            lag = BARS_PER_DAY * d
            j = self.n - lag
            if j < 2:
                break
            vals.append(self.a3_ring[j % self.ring_n])
        if len(vals) < p.rvol_min_lags:
            return None
        vals.sort()
        k = len(vals)
        med = vals[k // 2] if k % 2 else 0.5 * (vals[k // 2 - 1] + vals[k // 2])
        if med <= 0:
            return None
        return a3_now / med

    def update(self, ts, o, h, l, c, amt, btc_r):
        """Feed one closed bar; returns a Snap once warm, else None.

        Invalid bars raise ValueError - the caller must treat the coin as
        unavailable (features go missing), never skip silently.
        """
        if not valid_bar(o, h, l, c):
            raise ValueError("invalid bar")
        p = self.p
        n = self.n
        gk = 0.5 * math.log(h / l) ** 2 - LN2K * math.log(c / o) ** 2
        if gk < 0.0:
            gk = 0.0
        r = math.log(c / self.prev_c) if self.prev_c else 0.0

        if self.s2 is None:
            self.s2 = max(gk, 1e-10)
            self.res2 = max(r * r, 1e-10)
        sigma = math.sqrt(self.s2)
        beta = self.cov / max(self.varb, 1e-12)
        beta = 0.0 if beta < 0.0 else (p.beta_cap if beta > p.beta_cap else beta)
        res = r - beta * btc_r
        sig_res = math.sqrt(self.res2)
        z = res / max(sig_res, 1e-9)

        # breakout flags against the PRIOR channel
        up = dn = False
        if len(self.hi_q) == p.channel_bars:
            up = c > max(self.hi_q)
            dn = c < min(self.lo_q)
        up_start = up and not self.up_prev
        dn_start = dn and not self.dn_prev
        if up_start:
            self.up_run_start = n
        if dn_start:
            self.dn_run_start = n
        up_age = None if self.up_run_start is None else n - self.up_run_start
        dn_age = None if self.dn_run_start is None else n - self.dn_run_start

        # liquidity and RVOL
        slot = n % BARS_PER_DAY
        self.qv += amt - self.amt_ring[slot]
        self.amt_ring[slot] = amt
        a3_now = amt + self.a_last[0] + self.a_last[1]
        rvol = self._rvol(a3_now) if n >= 2 else None
        self.a3_ring[n % self.ring_n] = a3_now
        self.a_last.append(amt)

        # residual momentum
        self.res_q.append(res)
        self.close_q.append(c)
        z1h = z4h = None
        if len(self.res_q) >= 12:
            s12 = sum(list(self.res_q)[-12:])
            z1h = s12 / max(sig_res * math.sqrt(12), 1e-9)
        if len(self.res_q) >= 48:
            z4h = sum(self.res_q) / max(sig_res * math.sqrt(48), 1e-9)
        ret1h = (math.log(c / self.close_q[0])
                 if len(self.close_q) == 13 else None)

        # advance state AFTER computing everything that must be causal
        lam = p.lam_sigma
        self.s2 = lam * self.s2 + (1 - lam) * gk
        self.res2 = lam * self.res2 + (1 - lam) * res * res
        lb = p.lam_beta
        self.cov = lb * self.cov + (1 - lb) * r * btc_r
        self.varb = lb * self.varb + (1 - lb) * btc_r * btc_r
        self.hi_q.append(h)
        self.lo_q.append(l)
        self.up_prev, self.dn_prev = up, dn
        self.prev_c = c
        self.n = n + 1

        if n < p.warmup_bars:
            return None
        s = Snap()
        s.n = n; s.ts = ts; s.close = c; s.sigma = sigma; s.z = z; s.res = res
        s.sig_res = sig_res; s.beta = beta
        s.up_start = up_start; s.dn_start = dn_start
        s.up_age = up_age; s.dn_age = dn_age
        s.rvol = rvol; s.qv24 = self.qv
        s.z1h = z1h; s.z4h = z4h; s.ret1h = ret1h
        return s


class Cusum:
    """Two-sided CUSUM on standardized residual returns, with cooldown.

    h controls the EVENT RATE and is chosen from a target rate, never from
    outcomes.  State resets after each event.
    """
    def __init__(self, h, k=0.5, cooldown=12):
        self.h, self.k, self.cooldown = h, k, cooldown
        self.su = self.sd = 0.0
        self.last = -10 ** 9

    def update(self, n, z):
        self.su = max(0.0, self.su + z - self.k)
        self.sd = max(0.0, self.sd - z - self.k)
        if (self.su > self.h or self.sd > self.h) and n - self.last >= self.cooldown:
            side = 1 if self.su > self.h else -1
            self.su = self.sd = 0.0
            self.last = n
            return side
        return 0
