"""Statistics for clustered, overlapping, multiply-tested trading evidence.

Rows are never treated as independent: events on one UTC day share market
moves, so intervals resample whole DAYS.  Cumulative incidence handles
right-censoring (stops resolve early, targets late).  The max-t test accounts
for having tried many configurations.  The e-process allows daily monitoring
of a shadow stream without inflating false positives.
"""
import math
import random


def _by_day(values, days):
    s, n = {}, {}
    for v, d in zip(values, days):
        s[d] = s.get(d, 0.0) + v
        n[d] = n.get(d, 0) + 1
    ds = sorted(s)
    return ds, [s[d] for d in ds], [n[d] for d in ds]


def day_cluster_mean(values, days, B=2000, seed=1):
    """(mean, lo95, hi95, n_days) with a day-block bootstrap."""
    if not values:
        return float("nan"), float("nan"), float("nan"), 0
    ds, S, N = _by_day(values, days)
    D = len(ds)
    tot_s, tot_n = sum(S), sum(N)
    mean = tot_s / tot_n
    rng = random.Random(seed)
    ms = []
    for _ in range(B):
        ss = nn = 0
        for _ in range(D):
            j = rng.randrange(D)
            ss += S[j]
            nn += N[j]
        ms.append(ss / nn)
    ms.sort()
    lo = ms[int(0.025 * (B - 1))]
    hi = ms[int(0.975 * (B - 1))]
    return mean, lo, hi, D


def block_cluster_mean(values, days, block=3, B=2000, seed=1):
    """(mean, lo95, hi95, n_days) with a MOVING-BLOCK bootstrap.

    ``day_cluster_mean`` resamples whole UTC days INDEPENDENTLY.  That is
    right when one day's rows are the dependent unit, and wrong when the
    rows of ADJACENT days are dependent too - which is the case whenever a
    label window is longer than a day: a trade entered late on one day and a
    trade entered early the next share the same price path.

    This estimator resamples whole BLOCKS of `block` CONSECUTIVE observed
    days (a block never spans a day with no rows: an archive gap makes a
    block cover more calendar time, which is conservative, not optimistic),
    so the dependence inside a block is carried into the resample.  The block
    length
    is a declared assumption: the interval generally widens as the block
    grows (up to bootstrap noise), so a reader who believes the dependence
    is longer should read the longer block.  ``block=1`` reproduces
    ``day_cluster_mean`` exactly.

    Blocks are drawn WITHOUT wrap-around: a block is always a genuine
    consecutive stretch of the observed days, so no artificial neighbour is
    created at the end of the series.
    """
    if not values:
        return float("nan"), float("nan"), float("nan"), 0
    ds, S, N = _by_day(values, days)
    D = len(ds)
    tot_s, tot_n = sum(S), sum(N)
    mean = tot_s / tot_n
    block = max(1, min(int(block), D))
    starts = D - block + 1               # valid first-day positions of a block
    n_blocks = -(-D // block)            # ceil: the resample covers ~D days
    rng = random.Random(seed)
    ms = []
    for _ in range(B):
        ss = nn = 0
        for _ in range(n_blocks):
            j = rng.randrange(starts)
            for k in range(block):
                ss += S[j + k]
                nn += N[j + k]
        ms.append(ss / nn)
    ms.sort()
    lo = ms[int(0.025 * (B - 1))]
    hi = ms[int(0.975 * (B - 1))]
    return mean, lo, hi, D


def day_cluster_t(values, days):
    """Studentized mean using day-level sums (ratio estimator), for ranking."""
    if len(values) < 3:
        return 0.0
    ds, S, N = _by_day(values, days)
    D = len(ds)
    if D < 3:
        return 0.0
    m = sum(S) / sum(N)
    # linearized variance of the ratio estimator
    resid = [S[j] - m * N[j] for j in range(D)]
    nbar = sum(N) / D
    var = sum(r * r for r in resid) / (D - 1) / (nbar * nbar) / D
    return m / math.sqrt(var) if var > 0 else 0.0


def spa_max_t(config_values, config_days, B=1000, seed=7):
    """Reality-check style p-value for the BEST of many configurations.

    config_values / config_days: list (one per config) of parallel lists.
    Null: every configuration has zero mean (each is centered at its own
    estimate).  Returns (best_index, best_t, p_value).

    Configurations observed on fewer than 3 distinct days are excluded from
    selection: day_cluster_t is undefined there and returns 0.0, which would
    otherwise beat any genuinely negative configuration and win the max-t
    search spuriously.
    """
    K = len(config_values)
    all_days = sorted({d for ds in config_days for d in ds})
    di = {d: j for j, d in enumerate(all_days)}
    D = len(all_days)
    S = [[0.0] * D for _ in range(K)]
    N = [[0] * D for _ in range(K)]
    for k in range(K):
        for v, d in zip(config_values[k], config_days[k]):
            S[k][di[d]] += v
            N[k][di[d]] += 1
    mu = []
    for k in range(K):
        n = sum(N[k])
        mu.append(sum(S[k]) / n if n else 0.0)
    obs = [day_cluster_t(config_values[k], config_days[k]) for k in range(K)]
    eligible = [k for k in range(K) if len(set(config_days[k])) >= 3]
    if not eligible:
        return 0, 0.0, 1.0
    best = max(eligible, key=lambda k: obs[k])
    rng = random.Random(seed)
    # per-config bootstrap sd of the centered mean
    boots = [[] for _ in range(K)]
    for _ in range(B):
        idx = [rng.randrange(D) for _ in range(D)]
        for k in range(K):
            sk, nk = S[k], N[k]
            ss = nn = 0
            for j in idx:
                ss += sk[j] - mu[k] * nk[j]
                nn += nk[j]
            boots[k].append(ss / nn if nn else 0.0)
    sds = []
    for k in range(K):
        b = boots[k]
        m = sum(b) / len(b)
        sds.append(math.sqrt(sum((x - m) ** 2 for x in b) / (len(b) - 1)) or 1e-12)
    cnt = 0
    for t in range(B):
        mx = max(boots[k][t] / sds[k] for k in eligible)
        if mx >= obs[best]:
            cnt += 1
    return best, obs[best], (cnt + 1) / (B + 1)


def aalen_johansen(times, causes, n_causes, horizon):
    """Cumulative incidence by cause with right-censoring.

    times:   observed time for each unit (offset in bars)
    causes:  0 = censored at `time`; 1..n_causes = event cause
    Returns {cause: [(t, F_cause(t))]} evaluated at each distinct event time
    up to `horizon`.
    """
    pairs = sorted(zip(times, causes))
    n_at_risk = len(pairs)
    surv = 1.0
    F = [0.0] * (n_causes + 1)
    out = {c: [] for c in range(1, n_causes + 1)}
    i = 0
    while i < len(pairs):
        t = pairs[i][0]
        if t > horizon:
            break
        d = [0] * (n_causes + 1)
        cens = 0
        j = i
        while j < len(pairs) and pairs[j][0] == t:
            if pairs[j][1] == 0:
                cens += 1
            else:
                d[pairs[j][1]] += 1
            j += 1
        dtot = sum(d)
        if dtot and n_at_risk > 0:
            for c in range(1, n_causes + 1):
                F[c] += surv * d[c] / n_at_risk
            surv *= 1.0 - dtot / n_at_risk
        for c in range(1, n_causes + 1):
            out[c].append((t, F[c]))
        n_at_risk -= dtot + cens
        i = j
    return out


class EProcess:
    """Mixture likelihood-ratio e-process for Bernoulli outcomes vs p0.

    Mixture over alternatives p1 on a grid in (p0, p_max].  Under H0 (true
    hit rate <= p0) the e-value is a supermartingale, so rejecting when it
    reaches 1/alpha is valid under continuous peeking (Ville).  Feed
    CLUSTER-level outcomes (e.g. one per day) when events are correlated.
    """
    def __init__(self, p0, p_max=0.95, grid=40):
        self.p0 = p0
        self.ps = [p0 + (p_max - p0) * (j + 1) / grid for j in range(grid)]
        self.logs = [0.0] * grid

    def update(self, hit):
        for j, p1 in enumerate(self.ps):
            self.logs[j] += math.log(p1 / self.p0 if hit
                                     else (1 - p1) / (1 - self.p0))
        return self.e_value()

    def e_value(self):
        m = max(self.logs)
        return math.exp(m) * sum(math.exp(x - m) for x in self.logs) / len(self.logs)


def calibration_slope(pred, y):
    """OLS slope of realized y on predicted EV (1.0 = well calibrated)."""
    n = len(pred)
    if n < 3:
        return float("nan")
    mp, my = sum(pred) / n, sum(y) / n
    vp = sum((p - mp) ** 2 for p in pred)
    if vp <= 0:
        return float("nan")
    return sum((p - mp) * (v - my) for p, v in zip(pred, y)) / vp
