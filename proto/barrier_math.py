"""Barrier math port for the watcher (stdlib only).

Formulas ported verbatim from ~/Downloads/barrier_math.py (Monte Carlo
verified reference). No numpy; Phi via math.erf.
"""

import math


def Phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def p_tp_first(a: float, b: float, mu: float, sigma: float) -> float:
    """P(hit +a before -b) for Brownian motion with drift mu, vol sigma."""
    if abs(mu) < 1e-14:
        return b / (a + b)
    k = 2 * mu / sigma ** 2
    return (1 - math.exp(-k * b)) / (1 - math.exp(-k * (a + b)))


def noise_touch_prob(x_ratio: float, n_bars: int) -> float:
    """Driftless P(min over T <= -b): 2*Phi(-x / sqrt(n))."""
    return 2 * Phi(-x_ratio / math.sqrt(n_bars))


def breakeven_p(a_R: float, c_R: float) -> float:
    """Breakeven hit probability with costs: (1 + c_R) / (1 + a_R)."""
    return (1 + c_R) / (1 + a_R)


def friction_R(cost_pct: float, stop_pct: float) -> float:
    """Round-trip cost in R: cost % / stop distance %."""
    return cost_pct / stop_pct


def cluster_z(p: float, p0: float, n: int, frac: float) -> float:
    """z of observed share vs null with effective fraction of rows."""
    se = math.sqrt(p0 * (1 - p0) / (n * frac))
    return (p - p0) / se
