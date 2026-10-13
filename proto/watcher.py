"""Watcher sidecar: stable criteria-triggered setups.

Quant section (Task 2). Pure stdlib functions, no I/O, no network.
Closed bars only: a bar counts iff ``bar["ts"] + 300 <= now``.
L5/initiation come from the existing ``proto.scorer.score_v3`` (called,
never reimplemented) via :func:`v3_snapshot`.
"""

import math

from proto import scorer
from proto.watcher_journal import ensure_journal as _ensure_journal
from proto.watcher_journal import log_event as _log_event

BAR_SEC = 300
HALF_HOUR_SEC = 1800
N_TOD_BUCKETS = 48
EWMA_SPAN = 24
WARMUP_BARS = 60
TOD_LOOKBACK_DAYS = 14
TOD_MIN_SAMPLES = 30
CUSUM_K = 0.5
CUSUM_H = 4.0
CUSUM_REFRACTORY = 6
BTC_DRIFT_BARS = 2016  # 7d of 5m bars

_LN2_TERM = 2.0 * math.log(2.0) - 1.0


def _closed(bars, now):
    """Bars with ``ts + 300 <= now`` (forming bar excluded)."""
    return [b for b in bars if b["ts"] + BAR_SEC <= now]


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def tod_bucket(ts):
    """Half-hour bucket key ``(ts // 1800) % 48``."""
    return (ts // HALF_HOUR_SEC) % N_TOD_BUCKETS


def gk_vol(bars):
    """Per-bar Garman-Klass variance, floored at 0.

    ``gk = 0.5*ln(H/L)^2 - (2ln2-1)*ln(C/O)^2``. Non-positive or missing
    prices -> None for that bar.
    """
    out = []
    for b in bars:
        try:
            o, h, l, c = b["o"], b["h"], b["l"], b["c"]
        except (KeyError, TypeError):
            out.append(None)
            continue
        if (o is None or h is None or l is None or c is None
                or o <= 0 or h <= 0 or l <= 0 or c <= 0 or h < l):
            out.append(None)
            continue
        gk = 0.5 * math.log(h / l) ** 2 - _LN2_TERM * math.log(c / o) ** 2
        out.append(max(0.0, gk))
    return out


def ewma(vals, span=24):
    """EWMA with ``lambda = 2/(span+1)``, seeded at the mean of the first
    ``span`` valid values. Positions before the seed (and None inputs) are
    None; Nones never enter the seed or move the average."""
    lam = 2.0 / (span + 1.0)
    out = [None] * len(vals)
    seed_vals = []
    avg = None
    for i, v in enumerate(vals):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        if avg is None:
            seed_vals.append(v)
            if len(seed_vals) == span:
                avg = sum(seed_vals) / span
                out[i] = avg
        else:
            avg = lam * v + (1.0 - lam) * avg
            out[i] = avg
    return out


def _prior_rows(keys, vals, lookback_days):
    """(bucket, value) pairs strictly before today 00:00 UTC and within
    ``lookback_days``. Today is the UTC day of max(keys); today's rows are
    never in the profile."""
    pairs = [(k, v) for k, v in zip(keys, vals)
             if k is not None and v is not None]
    if not pairs:
        return []
    today = (max(k for k, _ in pairs) // 86400) * 86400
    lo = today - lookback_days * 86400
    return [(tod_bucket(k), v) for k, v in pairs if lo <= k < today]


def _seasonal_ratio(keys, vals, lookback_days):
    """Median same-bucket / median all over prior-only rows. Buckets with
    <30 samples are absent (never a 1.0 default)."""
    rows = _prior_rows(keys, vals, lookback_days)
    if not rows:
        return {}
    overall = _median([v for _, v in rows])
    if overall is None or overall == 0:
        return {}
    by_bucket = {}
    for b, v in rows:
        by_bucket.setdefault(b, []).append(v)
    prof = {}
    for b, vs in by_bucket.items():
        if len(vs) >= TOD_MIN_SAMPLES:
            prof[b] = _median(vs) / overall
    return prof


def tod_profile(keys, vals, lookback_days=14):
    """Seasonal vol profile over bar timestamps ``keys`` and per-bar values
    ``vals`` (e.g. GK). Prior-only; buckets with <30 samples are absent."""
    return _seasonal_ratio(list(keys), list(vals), lookback_days)


def sigma_hat(bars, now):
    """TOD-adjusted EWMA vol as a per-5m SD. None when <60 closed bars, when
    the seasonal bucket for the latest bar is missing, or when the EWMA has
    no valid value at the latest bar. Forming bar excluded."""
    closed = _closed(bars, now)
    if len(closed) < WARMUP_BARS:
        return None
    gks = gk_vol(closed)
    prof = _seasonal_ratio([b["ts"] for b in closed], gks, TOD_LOOKBACK_DAYS)
    adj = []
    for b, g in zip(closed, gks):
        if g is None:
            adj.append(None)
            continue
        s = prof.get(tod_bucket(b["ts"]))
        adj.append(g / s if s else None)
    series = ewma(adj, span=EWMA_SPAN)
    last = series[-1] if series else None
    if last is None or last < 0:
        return None
    return math.sqrt(last)


def resid_vol(ts_list, e_list, lookback_days=14):
    """Residual-vol SD from residuals ``e`` with its OWN seasonal profile on
    ``e^2`` (raw ``s_vol`` is never reused). None when <60 values or the
    latest bucket lacks its own profile."""
    ts_list = list(ts_list)
    e_list = list(e_list)
    if len(ts_list) < WARMUP_BARS or len(e_list) < WARMUP_BARS:
        return None
    e2 = [x * x if x is not None else None for x in e_list]
    prof = _seasonal_ratio(ts_list, e2, lookback_days)
    adj = []
    for t, v in zip(ts_list, e2):
        if v is None:
            adj.append(None)
            continue
        s = prof.get(tod_bucket(t))
        adj.append(v / s if s else None)
    series = ewma(adj, span=EWMA_SPAN)
    last = series[-1] if series else None
    if last is None or last < 0:
        return None
    return math.sqrt(last)


def cusum(zs, k=0.5, H=4.0):
    """Symmetric two-sided CUSUM on standardized residuals.

    ``S+ = max(0, S+ + z - k)``, ``S- = max(0, S- - z - k)``; long fires on
    ``S+ > H``, short on ``S- > H``. Fire-side reset with a 6-bar refractory
    on the firing side only. Mirror ``z -> -z`` swaps long/short indices.
    """
    long_idx, short_idx = [], []
    sp = sm = 0.0
    ref_long = ref_short = 0
    for i, z in enumerate(zs):
        if z is None or (isinstance(z, float) and math.isnan(z)):
            if ref_long:
                ref_long -= 1
            if ref_short:
                ref_short -= 1
            continue
        sp = max(0.0, sp + z - k)
        sm = max(0.0, sm - z - k)
        if ref_long:
            ref_long -= 1
        elif sp > H:
            long_idx.append(i)
            sp = 0.0
            ref_long = CUSUM_REFRACTORY
        if ref_short:
            ref_short -= 1
        elif sm > H:
            short_idx.append(i)
            sm = 0.0
            ref_short = CUSUM_REFRACTORY
    return {"long_idx": long_idx, "short_idx": short_idx}


def _mad(xs, med):
    dev = sorted(abs(x - med) for x in xs)
    n = len(dev)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return dev[mid]
    return 0.5 * (dev[mid - 1] + dev[mid])


def _winsorize(xs, n_mad=5.0):
    med = _median(xs)
    if med is None:
        return list(xs)
    mad = _mad(xs, med)
    if mad == 0:
        # (Near-)constant series: any deviation is an outlier -> clip to median.
        return [med] * len(xs)
    lo, hi = med - n_mad * mad, med + n_mad * mad
    return [min(hi, max(lo, x)) for x in xs]


def beta_ols(r, r_btc):
    """OLS beta with no intercept. Current bar excluded, both legs winsorized
    at median +/- 5*MAD, zero BTC variance -> None."""
    r = list(r)
    r_btc = list(r_btc)
    n = min(len(r), len(r_btc))
    if n < 3:  # need >= 2 points after dropping the current bar
        return None
    xs = _winsorize(r_btc[:n - 1])
    ys = _winsorize(r[:n - 1])
    denom = sum(x * x for x in xs)
    if denom == 0:
        return None
    return sum(x * y for x, y in zip(xs, ys)) / denom


def beta_shrunk(r, r_btc):
    """``0.5 * beta_ols + 0.5``; None when OLS is unavailable."""
    ols = beta_ols(r, r_btc)
    if ols is None:
        return None
    return 0.5 * ols + 0.5


def rvol_adj(bars, now):
    """Raw 3-bar mean / 48-bar median volume, divided by the same 48-bucket
    prior-only seasonal profile built on volume. None when <48 closed bars
    or the latest bucket lacks a profile (never a 1.0 default)."""
    closed = _closed(bars, now)
    if len(closed) < 48:
        return None
    vols = [b.get("vol") for b in closed]
    if any(v is None or v < 0 for v in vols[-48:]):
        return None
    med48 = _median(vols[-48:])
    if med48 is None or med48 == 0:
        return None
    base = (sum(vols[-3:]) / 3.0) / med48
    prof = _seasonal_ratio([b["ts"] for b in closed], vols, TOD_LOOKBACK_DAYS)
    s = prof.get(tod_bucket(closed[-1]["ts"]))
    if not s:
        return None
    return base / s


def btc_drift_7d(btc_closes_5m):
    """Trailing-7d BTC drift ``(c[-1]-c[-2016])/c[-2016]`` on 5m closes.
    Fewer than 2016 bars -> None."""
    closes = list(btc_closes_5m)
    if len(closes) < BTC_DRIFT_BARS:
        return None
    c0 = closes[-BTC_DRIFT_BARS]
    c1 = closes[-1]
    if c0 is None or c1 is None or c0 == 0:
        return None
    return (c1 - c0) / c0


def v3_snapshot(bars, **kw):
    """L5/initiation/direction via the existing scorer (delegate, not a copy)."""
    return scorer.score_v3(bars, **kw)


# ---------------------------------------------------------------------------
# Task 3: geometry + costs + funding + breakeven trail.
#
# Distances are return fractions throughout; no price rounding (tick rules
# unverified). Funding sign: a positive MEXC rate means longs pay, so
# ``fund_cost = sign(direction) * sum(rates)``; the short rebate is floored
# at 0 in ``c_total`` while the signed leg is kept for the net column.
# ---------------------------------------------------------------------------

LONG = "LONG"
SHORT = "SHORT"


def _dir_sign(direction):
    if direction == LONG:
        return 1.0
    if direction == SHORT:
        return -1.0
    raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")


def fund_cost(direction, rates):
    """Signed funding leg: ``sign(direction) * sum(rates)`` over scheduled
    settlements in ``[entry, entry+N]``. Long ``+sum``, short ``-sum``."""
    return _dir_sign(direction) * sum(rates)


def fund_stress(fund_now, n_settlements):
    """Named stress scenario (not historical net): ``3 * fund_now * n``."""
    return 3.0 * fund_now * n_settlements


def geometry(sigma, N, c_total, k_s, k_t):
    """``(b_stop, a_tgt)`` as fractions: ``b = max(k_s*sigma*sqrt(N),
    10*c_total)``, ``a = k_t*sigma*sqrt(N)``."""
    return (max(k_s * sigma * math.sqrt(N), 10.0 * c_total),
            k_t * sigma * math.sqrt(N))


def levels(entry, direction, b_stop, a_tgt):
    """``(stop_px, tgt_px)`` from fractional distances. LONG stop sits
    below entry, SHORT mirrors above; no rounding."""
    if direction == LONG:
        return (entry * (1.0 - b_stop), entry * (1.0 + a_tgt))
    if direction == SHORT:
        return (entry * (1.0 + b_stop), entry * (1.0 - a_tgt))
    raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")


def apply_breakeven(state, close):
    """Breakeven trail step; returns a new state dict (input unmutated).

    Trigger: 5m close at >= ``entry + 1*b_stop`` (LONG) or <=
    ``entry - 1*b_stop`` (SHORT). The trigger bar only arms ``pending``
    (pre-trigger touches keep the original stop); the move ``stop -> entry``
    applies on the next call (next bar open). Post-move the state is
    immutable, and same-bar touches use the moved stop with the stop-first
    rule. State keys: ``entry, direction, b_stop, stop_px, tgt_px, moved,
    pending``.
    """
    s = dict(state)
    if s.get("moved", False):
        return s
    if s.get("pending", False):
        s["stop_px"] = s["entry"]
        s["moved"] = True
        s["pending"] = False
        return s
    entry, b, direction = s["entry"], s["b_stop"], s["direction"]
    if direction == LONG:
        if close >= entry * (1.0 + b):
            s["pending"] = True
    elif direction == SHORT:
        if close <= entry * (1.0 - b):
            s["pending"] = True
    else:
        raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")
    return s


# ---------------------------------------------------------------------------
# Task 4: event study + N selection + kill gate.
#
# Horizons fixed: {12, 48, 96, 288} bars of 5m (1h/4h/8h/24h); the kill gate
# checks {12, 48, 288}. Costs enter only as sensitivity bands, never to pick
# N. No I/O.
# ---------------------------------------------------------------------------

N_GRID = (12, 48, 96, 288)
KILL_HORIZONS = (12, 48, 288)


def cumret(c0, ch, direction):
    """Cumulative signed return ``dir * ln(ch / c0)`` (+1 long, -1 short)."""
    return _dir_sign(direction) * math.log(ch / c0)


def _mean(xs):
    return sum(xs) / len(xs)


def _sd_pop(xs, mean):
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    return math.sqrt(var) if var > 0 else 0.0


def event_score(cand, plac):
    """Placebo-adjusted score ``(mean_c - mean_p) / SD_c`` (population SD).

    ``SD_c == 0`` (or empty input) -> 0.0, never NaN. Dimensionless.
    """
    cand = list(cand)
    plac = list(plac)
    if not cand or not plac:
        return 0.0
    if min(cand) == max(cand):
        # Zero variance (exact): score undefined -> 0.0, never NaN.
        # (Avoids float dust from sum()/len() making var a tiny positive.)
        return 0.0
    mean_c, mean_p = _mean(cand), _mean(plac)
    sd_c = _sd_pop(cand, mean_c)
    if sd_c == 0:
        return 0.0
    return (mean_c - mean_p) / sd_c


def select_N(curves):
    """``argmax`` of per-horizon scores over the fixed grid {12,48,96,288}.

    Extra keys ignored; ties keep the earliest horizon.
    """
    best_h, best_s = None, None
    for h in N_GRID:
        s = curves[h]
        if best_s is None or s > best_s:
            best_h, best_s = h, s
    return best_h


def kill_check(gross, net):
    """True (= kill) only when gross<=0 at all {12,48,288} AND net<=0 at all
    three. Any positive horizon on either leg -> False (no kill)."""
    gross_kill = all(gross[h] <= 0 for h in KILL_HORIZONS)
    net_kill = all(net[h] <= 0 for h in KILL_HORIZONS)
    return bool(gross_kill and net_kill)


# ---------------------------------------------------------------------------
# Task 5: triggers (T1-break + CUSUM-raw) + H freeze.
#
# Emission is structural (trigger + vetoes). Stale/missing funding never
# blocks: the setup is still emitted with ``funding="unavailable"`` (gross
# available, net EV unavailable, selector silent for it — Task 7).
# Veto thresholds mirror ``scorer`` screener floors: vol >= $250k,
# spread <= 3%, |1h| < 12%, |24h| < 35%.
# ---------------------------------------------------------------------------

T1_RULE_ID = "t1-break-v1"
CUSUM_RULE_ID = "cusum-raw-v1"
T1_MAX_AGE = 6
T1_MIN_L5 = 0.15
T1_MIN_BTC_DRIFT = 0.05
H_GRID = (3.0, 4.0, 5.0, 6.0)
H_MAX_ALERTS_PER_DAY = 2500.0
FUNDING_STALE_H = 8.0

MIN_QUOTE_VOLUME_24H = 250_000.0
MAX_SPREAD_PCT = 3.0
LATE_MOVE_1H_PCT = 12.0
LATE_MOVE_24H_PCT = 35.0


def vetoes_ok(vol, spread, m1h, m24h):
    """Veto screen. Returns the list of breach reasons (empty = pass).

    Boundaries: ``vol`` must be >= $250k, ``spread`` <= 3%,
    ``|m1h|`` < 12%, ``|m24h|`` < 35%. Named reasons, never silent.
    """
    reasons = []
    if vol is None or vol < MIN_QUOTE_VOLUME_24H:
        reasons.append(f"low_volume: 24h quote volume {vol} below $250k floor")
    if spread is None or spread > MAX_SPREAD_PCT:
        reasons.append(f"wide_spread: spread {spread}% above 3% cap")
    if m1h is None or abs(m1h) >= LATE_MOVE_1H_PCT:
        reasons.append(f"late_move_1h: already moved {m1h}% in 1h")
    if m24h is None or abs(m24h) >= LATE_MOVE_24H_PCT:
        reasons.append(f"late_move_24h: already moved {m24h}% in 24h")
    return reasons


def freeze_H(dev_windows):
    """Freeze ``H_cusum``: smallest grid H in {3,4,5,6} with <=2500 alerts/day
    universe-wide on the dev window. ``dev_windows`` maps H -> alerts/day.
    None qualifies -> tightest 6.0. Frozen after (never refit live)."""
    for h in H_GRID:
        rate = dev_windows.get(h)
        if rate is not None and rate <= H_MAX_ALERTS_PER_DAY:
            return float(h)
    return float(H_GRID[-1])


def _funding_label(ctx):
    """``"ok"`` or ``"unavailable"``. Stale (>8h) / missing never blocks."""
    state = ctx.get("funding_state", "missing")
    if state == "ok":
        age_h = ctx.get("funding_age_h", 0.0)
        if age_h is not None and age_h > FUNDING_STALE_H:
            return "unavailable"
        return "ok"
    return "unavailable"


def _setup_id(rule_id, coin, src_bar_ts):
    return f"{rule_id}:{coin}:{src_bar_ts}"


def t1_candidate(ctx):
    """T1-break candidate: 48-bar Donchian exit, age<=6, L5>0.15 LONG,
    trailing-7d BTC drift >+5%, vetoes clean, src bar closed.

    L5/initiation/age/direction come from ``scorer.score_v3`` (called, never
    copied). Returns the setup dict, or None (reason in
    ``t1_candidate.last_reason``).
    """
    t1_candidate.last_reason = ""
    coin = ctx.get("coin")
    bars = ctx.get("bars", [])
    now = ctx.get("now")
    src_bar_ts = ctx.get("src_bar_ts")
    if src_bar_ts is None or now is None or src_bar_ts + BAR_SEC > now:
        t1_candidate.last_reason = "forming_bar: src bar not closed"
        return None
    try:
        v3 = v3_snapshot(bars, **ctx.get("score_kwargs", {}))
    except Exception as e:  # noqa: BLE001 - no candidate on scorer failure
        t1_candidate.last_reason = f"scorer_unavailable: {e}"
        return None
    direction = v3.get("direction_v3")
    if direction != LONG:
        t1_candidate.last_reason = f"not_long: direction_v3={direction}"
        return None
    l5 = v3.get("parts", {}).get("l5")
    if l5 is None or l5 <= T1_MIN_L5:
        t1_candidate.last_reason = f"weak_l5: L5={l5} <= 0.15"
        return None
    age = v3.get("parts", {}).get("age")
    if age is None or age > T1_MAX_AGE:
        t1_candidate.last_reason = f"stale_break: age={age} > 6"
        return None
    drift = btc_drift_7d(ctx.get("btc_closes", []) or [])
    if drift is None:
        t1_candidate.last_reason = "btc_drift_unavailable: need 2016 BTC 5m closes"
        return None
    if drift <= T1_MIN_BTC_DRIFT:
        t1_candidate.last_reason = f"btc_drift_low: {drift:.4f} <= +5%"
        return None
    breaches = vetoes_ok(ctx.get("quote_vol_24h"), ctx.get("spread_pct"),
                         ctx.get("move_1h_pct"), ctx.get("move_24h_pct"))
    if breaches:
        t1_candidate.last_reason = "veto: " + "; ".join(breaches)
        return None
    return {
        "id": _setup_id(T1_RULE_ID, coin, src_bar_ts),
        "rule_id": T1_RULE_ID, "coin": coin, "src": "t1_break",
        "direction": LONG, "src_bar_ts": src_bar_ts, "entry": ctx.get("entry"),
        "l5": l5, "age": age, "initiation": v3.get("initiation"),
        "btc_drift_7d": drift, "funding": _funding_label(ctx),
    }


t1_candidate.last_reason = ""


def cusum_candidate(ctx):
    """CUSUM-raw candidate: fire-side ``s_value > H`` on the raw alt-perp z
    path (``cusum()`` from Task 2 owns the S+/S- accumulation; this gates one
    firing), vetoes clean, src bar closed. ``side`` LONG/SHORT mirrors.

    Returns the setup dict, or None (reason in
    ``cusum_candidate.last_reason``).
    """
    cusum_candidate.last_reason = ""
    coin = ctx.get("coin")
    now = ctx.get("now")
    src_bar_ts = ctx.get("src_bar_ts")
    if src_bar_ts is None or now is None or src_bar_ts + BAR_SEC > now:
        cusum_candidate.last_reason = "forming_bar: src bar not closed"
        return None
    side = ctx.get("side")
    if side not in (LONG, SHORT):
        cusum_candidate.last_reason = f"no_side: side={side!r}"
        return None
    s_value = ctx.get("s_value")
    h = ctx.get("H", CUSUM_H)
    if s_value is None or h is None or not s_value > h:
        cusum_candidate.last_reason = f"no_fire: s={s_value} <= H={h}"
        return None
    breaches = vetoes_ok(ctx.get("quote_vol_24h"), ctx.get("spread_pct"),
                         ctx.get("move_1h_pct"), ctx.get("move_24h_pct"))
    if breaches:
        cusum_candidate.last_reason = "veto: " + "; ".join(breaches)
        return None
    src = "cusum_long" if side == LONG else "cusum_short"
    return {
        "id": _setup_id(CUSUM_RULE_ID, coin, src_bar_ts),
        "rule_id": CUSUM_RULE_ID, "coin": coin, "src": src,
        "direction": side, "src_bar_ts": src_bar_ts, "entry": ctx.get("entry"),
        "s_value": s_value, "H": h, "funding": _funding_label(ctx),
    }


cusum_candidate.last_reason = ""


# ---------------------------------------------------------------------------
# Task 6: lifecycle state machine + persistence + recovery.
#
# Standalone sqlite3 conn-taking functions (no Store dependency, so legacy
# scan/scorer/store paths are untouched). Per-bar order (spec S3):
# gap/stale -> unavailable overlay, else barrier resolution with the stop
# first, else close-based invalidation (never masks a touch), else time
# expiry. Emission consumes cusum() fire events, never raw bars.
# ---------------------------------------------------------------------------

WATCHER_SETUP_DDL = """
CREATE TABLE IF NOT EXISTS watcher_setup (
  id TEXT PRIMARY KEY,          -- rule_id:coin:src_bar_ts
  rule_id TEXT NOT NULL,        -- t1-break-v1 | cusum-raw-v1 | ...
  coin TEXT NOT NULL, symbol TEXT NOT NULL,
  src TEXT NOT NULL,            -- t1_break | cusum_long | cusum_short
  direction TEXT NOT NULL,      -- LONG | SHORT
  src_bar_ts INTEGER NOT NULL, entry REAL NOT NULL,
  b_stop REAL NOT NULL, a_tgt REAL NOT NULL,
  stop_px REAL NOT NULL, tgt_px REAL NOT NULL,
  stop_dist_pct REAL NOT NULL, tgt_dist_pct REAL NOT NULL,
  N_bars INTEGER NOT NULL, trail TEXT NOT NULL DEFAULT 'none',
  state TEXT NOT NULL DEFAULT 'active',        -- active | invalidated | expired | resolved
  substate TEXT,                -- stop | target | time | gap | thesis
  monitoring TEXT NOT NULL DEFAULT 'ok',       -- ok | unavailable
  price_source TEXT NOT NULL DEFAULT 'perp_trades',
  cost_total REAL, cost_floor INTEGER DEFAULT 0,
  funding_state TEXT, funding_projected REAL, fund_stress REAL,
  shadow_selected INTEGER DEFAULT 0, ev_r REAL, p_lb REAL,
  invalid_price REAL, last_eval_bar INTEGER, unavailable_bars INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL, closed_at INTEGER
);
"""

WATCHER_EVAL_DDL = """
CREATE TABLE IF NOT EXISTS watcher_eval (
  setup_id TEXT REFERENCES watcher_setup(id),
  bar_ts INTEGER NOT NULL, state TEXT NOT NULL, monitoring TEXT NOT NULL,
  ret_gross REAL, ret_net REAL, note TEXT,
  PRIMARY KEY (setup_id, bar_ts)
);
"""

WATCHER_CHECKPOINT_DDL = """
CREATE TABLE IF NOT EXISTS watcher_checkpoint (
  coin TEXT NOT NULL, rule_id TEXT NOT NULL, watermark INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (coin, rule_id)
);
"""

# Composite per-bar states returned by eval_bar. Stored split into
# (state, substate): resolved_stop -> (resolved, stop), resolved_target ->
# (resolved, target), expired_time -> (expired, time), expired_gap ->
# (expired, gap), invalidated -> (invalidated, thesis).
TERMINAL_STATES = ("resolved_stop", "resolved_target", "expired_time",
                   "expired_gap", "invalidated")

# Gap-expiry: 48 consecutive unavailable bars -> censored expired_gap.
GAP_EXPIRY_BARS = 48

# Full-recompute reconciliation tolerance: mismatch beyond this ->
# monitoring=unavailable (state overlay preserved).
RECOMPUTE_TOL = 1e-9

# Per-(coin, rule) emission cooldowns (T1 24h, CUSUM 12h): suppress
# duplicate alerts, not independence claims.
EMIT_COOLDOWN_S = {T1_RULE_ID: 86400, CUSUM_RULE_ID: 43200}


def _split_state(composite):
    if composite == "active":
        return ("active", None)
    if composite in ("resolved_stop", "resolved_target"):
        return ("resolved", composite.rsplit("_", 1)[1])
    if composite in ("expired_time", "expired_gap"):
        return ("expired", composite.rsplit("_", 1)[1])
    if composite == "invalidated":
        return ("invalidated", "thesis")
    raise ValueError(f"unknown lifecycle state {composite!r}")


def _join_state(state, substate):
    if state == "active":
        return "active"
    if state == "resolved":
        return f"resolved_{substate}"
    if state == "expired":
        return f"expired_{substate}"
    if state == "invalidated":
        return "invalidated"
    raise ValueError(f"unknown stored state {(state, substate)!r}")


def ensure_watcher_tables(conn):
    """Create watcher_setup / watcher_eval (exact spec S4 DDL) plus the
    watcher_checkpoint watermark table. Additive; idempotent on reopen.
    Trail runtime columns (be_pending/be_moved) are added after the verbatim
    DDL so the spec CREATE TABLE text stays exact."""
    conn.executescript(WATCHER_SETUP_DDL)
    conn.executescript(WATCHER_EVAL_DDL)
    conn.executescript(WATCHER_CHECKPOINT_DDL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(watcher_setup)")}
    for col in ("be_pending", "be_moved"):
        if col not in cols:
            conn.execute(
                f"ALTER TABLE watcher_setup ADD COLUMN {col} INTEGER DEFAULT 0")
    conn.commit()


def cusum_fire_ids(zs, k=0.5, H=4.0):
    """(side, index) fire events from cusum(). Emission consumes these fire
    events, never raw bars, so one firing yields one candidate (no
    double-count)."""
    res = cusum(zs, k=k, H=H)
    fires = ([("LONG", i) for i in res["long_idx"]]
             + [("SHORT", i) for i in res["short_idx"]])
    fires.sort(key=lambda t: t[1])
    return fires


def _setup_row(setup):
    rule_id = setup["rule_id"]
    coin = setup["coin"]
    src_bar_ts = setup["src_bar_ts"]
    sid = setup.get("id") or _setup_id(rule_id, coin, src_bar_ts)
    entry = setup["entry"]
    direction = setup["direction"]
    b_stop = setup.get("b_stop", 0.0)
    a_tgt = setup.get("a_tgt", 0.0)
    stop_px = setup.get("stop_px")
    tgt_px = setup.get("tgt_px")
    if stop_px is None or tgt_px is None:
        stop_px, tgt_px = levels(entry, direction, b_stop, a_tgt)
    import time as _t
    return {
        "id": sid, "rule_id": rule_id, "coin": coin,
        "symbol": setup.get("symbol", coin),
        "src": setup.get("src", ""),
        "direction": direction, "src_bar_ts": src_bar_ts, "entry": entry,
        "b_stop": b_stop, "a_tgt": a_tgt,
        "stop_px": stop_px, "tgt_px": tgt_px,
        "stop_dist_pct": setup.get("stop_dist_pct", b_stop * 100.0),
        "tgt_dist_pct": setup.get("tgt_dist_pct", a_tgt * 100.0),
        "N_bars": setup.get("N_bars", 288),
        "trail": setup.get("trail", "none"),
        "price_source": setup.get("price_source", "perp_trades"),
        "cost_total": setup.get("cost_total"),
        "cost_floor": setup.get("cost_floor", 0),
        "funding_state": setup.get("funding"),
        "created_at": setup.get("created_at", int(_t.time())),
    }


def emit_setup(conn, setup):
    """Persist one setup; returns its id. Dedupe: an exact
    rule_id:coin:src_bar_ts repeat returns the same id with no new row.
    Cooldown: a new src bar within the per-rule window (T1 24h, CUSUM 12h)
    of an existing same-(coin, rule) row is suppressed (existing id, no
    new row)."""
    ensure_watcher_tables(conn)
    row = _setup_row(setup)
    hit = conn.execute("SELECT id FROM watcher_setup WHERE id=?",
                       (row["id"],)).fetchone()
    if hit:
        return hit[0]
    cooldown = EMIT_COOLDOWN_S.get(row["rule_id"], 0)
    if cooldown:
        prior = conn.execute(
            "SELECT id FROM watcher_setup WHERE coin=? AND rule_id=?"
            " AND ABS(src_bar_ts - ?) < ? ORDER BY src_bar_ts DESC LIMIT 1",
            (row["coin"], row["rule_id"], row["src_bar_ts"],
             cooldown)).fetchone()
        if prior:
            return prior[0]
    cur = conn.execute(
        """INSERT OR IGNORE INTO watcher_setup
           (id, rule_id, coin, symbol, src, direction, src_bar_ts, entry,
            b_stop, a_tgt, stop_px, tgt_px, stop_dist_pct, tgt_dist_pct,
            N_bars, trail, state, monitoring, price_source, cost_total,
            cost_floor, funding_state, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'active', 'ok',
                   ?,?,?,?,?)""",
        (row["id"], row["rule_id"], row["coin"], row["symbol"], row["src"],
         row["direction"], row["src_bar_ts"], row["entry"], row["b_stop"],
         row["a_tgt"], row["stop_px"], row["tgt_px"], row["stop_dist_pct"],
         row["tgt_dist_pct"], row["N_bars"], row["trail"],
         row["price_source"], row["cost_total"], row["cost_floor"],
         row["funding_state"], row["created_at"]))
    inserted = cur.rowcount
    conn.commit()
    if inserted:
        _ensure_journal(conn)
        _log_event(conn, "emission", row["coin"],
                   f"{row['id']} emitted ({row['direction']} "
                   f"via {row['rule_id']})")
    return row["id"]


def _load_setup(conn, setup_id):
    cur = conn.execute("SELECT * FROM watcher_setup WHERE id=?", (setup_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown setup {setup_id!r}")
    return dict(zip([d[0] for d in cur.description], row))


def _stop_touch(direction, low, high, stop_px):
    if direction == LONG:
        return low <= stop_px
    return high >= stop_px


def _tgt_touch(direction, low, high, tgt_px):
    if direction == LONG:
        return high >= tgt_px
    return low <= tgt_px


def eval_bar(conn, setup_id, bar, ctx):
    """Evaluate one ordered closed bar; returns (state, monitoring).

    Per-bar order: gap/stale -> unavailable overlay (else barrier
    resolution, stop first), else close-based invalidation (never masks a
    touch), else time expiry. apply_breakeven runs exactly once per ordered
    closed bar for trail='breakeven' setups (trail='none' rows skip the step
    entirely, keeping stored grid geometry); post-move touches use the moved
    stop with the stop-first rule. Terminal rows are immutable; eval writes
    are idempotent (INSERT OR IGNORE). Recompute mismatch beyond 1e-9 forces
    monitoring=unavailable with the state preserved.
    """
    ensure_watcher_tables(conn)
    import time as _t
    srow = _load_setup(conn, setup_id)
    composite = _join_state(srow["state"], srow["substate"])
    if composite in TERMINAL_STATES:
        return (composite, srow["monitoring"])
    ctx = dict(ctx or {})
    now = ctx.get("now", int(_t.time()))
    bar_ts = (bar or {}).get("ts", ctx.get("bar_ts"))
    if bar_ts is None:
        raise ValueError("eval_bar needs bar['ts'] or ctx['bar_ts']")

    def _write(state, monitoring, note, ret_gross=None, **extra):
        st, sub = _split_state(state)
        fields = {"state": st, "substate": sub, "monitoring": monitoring,
                  "last_eval_bar": bar_ts}
        fields.update(extra)
        sets = ", ".join(f"{k}=?" for k in fields)
        # Terminal guard: only a live (active) row may be rewritten, so a
        # row that became terminal concurrently is never overwritten.
        conn.execute(f"UPDATE watcher_setup SET {sets} WHERE id=? AND state='active'",
                     tuple(fields.values()) + (setup_id,))
        conn.execute(
            """INSERT OR IGNORE INTO watcher_eval
               (setup_id, bar_ts, state, monitoring, ret_gross, ret_net, note)
               VALUES (?,?,?,?,?,?,?)""",
            (setup_id, bar_ts, state, monitoring, ret_gross, None, note))
        conn.commit()
        # Append-only journal transitions (Task 2): invalidation + expiry
        # only. Resolutions and live bars write no journal row; the
        # sweep-complete kind is written by the scan loop.
        if state == "invalidated":
            _log_event(conn, "invalidation", srow["coin"],
                       f"{setup_id} invalidated: {note}")
        elif state in ("expired_time", "expired_gap"):
            _log_event(conn, "expiry", srow["coin"],
                       f"{setup_id} expired ({state}): {note}")

    # 1. Gap/stale -> unavailable overlay; 48 consecutive -> expired_gap.
    stale = bool(ctx.get("missing")) or bar is None
    if not stale:
        try:
            o, h, l, c = bar["o"], bar["h"], bar["l"], bar["c"]
        except (KeyError, TypeError):
            o = h = l = c = None
        if (o is None or h is None or l is None or c is None
                or bar["ts"] + BAR_SEC > now):
            stale = True
    if stale:
        ub = (srow["unavailable_bars"] or 0) + 1
        if ub >= GAP_EXPIRY_BARS:
            _write("expired_gap", "unavailable",
                   f"censored: {ub} consecutive unavailable bars",
                   unavailable_bars=ub, closed_at=int(_t.time()))
            return ("expired_gap", "unavailable")
        _write("active", "unavailable", "gap/stale bar: monitoring unavailable",
               unavailable_bars=ub)
        return ("active", "unavailable")

    # Ordered closed bar: exactly one breakeven step (breakeven arm only),
    # then barriers. The step runs ONLY for trail='breakeven' setups: a
    # trail='none' row keeps its pre-registered grid geometry untouched
    # (stored stop/tgt), never armed, never moved.
    if srow.get("trail") == "breakeven":
        trail = {"entry": srow["entry"], "direction": srow["direction"],
                 "b_stop": srow["b_stop"], "stop_px": srow["stop_px"],
                 "tgt_px": srow["tgt_px"],
                 "pending": bool(srow.get("be_pending")),
                 "moved": bool(srow.get("be_moved"))}
        trail = apply_breakeven(trail, bar["c"])
        stop_px, tgt_px = trail["stop_px"], trail["tgt_px"]
        trail_extra = {"be_pending": 1 if trail.get("pending") else 0,
                       "be_moved": 1 if trail.get("moved") else 0}
        if trail.get("moved") and not srow.get("be_moved"):
            trail_extra["stop_px"] = stop_px
    else:
        stop_px, tgt_px = srow["stop_px"], srow["tgt_px"]
        trail_extra = {}

    recompute_bad = False
    if ("recompute_expected" in ctx and "recompute_actual" in ctx
            and ctx["recompute_expected"] is not None
            and ctx["recompute_actual"] is not None):
        recompute_bad = (abs(ctx["recompute_actual"]
                             - ctx["recompute_expected"]) > RECOMPUTE_TOL)

    # 2. Barriers, stop first: same-bar stop+target -> stop.
    low, high = bar["l"], bar["h"]
    direction = srow["direction"]
    try:
        ret_gross = cumret(srow["entry"], bar["c"], direction)
    except (ValueError, ZeroDivisionError):
        ret_gross = None
    mon = "unavailable" if recompute_bad else "ok"
    extra_note = "; recompute mismatch" if recompute_bad else ""
    if _stop_touch(direction, low, high, stop_px):
        _write("resolved_stop", mon,
               "stop touch (same-bar stop+target -> stop)" + extra_note,
               ret_gross=ret_gross, unavailable_bars=0,
               closed_at=int(_t.time()), **trail_extra)
        return ("resolved_stop", mon)
    if _tgt_touch(direction, low, high, tgt_px):
        _write("resolved_target", mon, "target touch" + extra_note,
               ret_gross=ret_gross, unavailable_bars=0,
               closed_at=int(_t.time()), **trail_extra)
        return ("resolved_target", mon)

    # 3. Close-based invalidation (never masks a touch: barriers won above).
    if ctx.get("invalidate"):
        _write("invalidated", mon, "close-based invalidation" + extra_note,
               ret_gross=ret_gross, unavailable_bars=0,
               invalid_price=ctx.get("invalid_price"),
               closed_at=int(_t.time()), **trail_extra)
        return ("invalidated", mon)

    # 4. Time expiry.
    age = ctx.get("age_bars")
    if age is None:
        age = (bar_ts - srow["src_bar_ts"]) // BAR_SEC
    if age >= srow["N_bars"]:
        _write("expired_time", mon,
               f"time-bar: age {age} >= N {srow['N_bars']}" + extra_note,
               ret_gross=ret_gross, unavailable_bars=0,
               closed_at=int(_t.time()), **trail_extra)
        return ("expired_time", mon)

    _write("active", mon, "no touch" + extra_note,
           ret_gross=ret_gross, unavailable_bars=0, **trail_extra)
    return ("active", mon)


def checkpoint(conn, coin, rule, watermark):
    """Atomic hourly watermark per (coin, rule). Idempotent upsert; the
    watermark never rewinds (MAX)."""
    ensure_watcher_tables(conn)
    import time as _t
    conn.execute(
        """INSERT INTO watcher_checkpoint (coin, rule_id, watermark, updated_at)
           VALUES (?,?,?,?)
           ON CONFLICT(coin, rule_id) DO UPDATE SET
             watermark=MAX(watcher_checkpoint.watermark, excluded.watermark),
             updated_at=excluded.updated_at""",
        (coin, rule, int(watermark), int(_t.time())))
    conn.commit()
    row = conn.execute(
        "SELECT watermark FROM watcher_checkpoint WHERE coin=? AND rule_id=?",
        (coin, rule)).fetchone()
    return row[0] if row else int(watermark)


def replay_since(conn, coin, rule):
    """Restart recovery: watermark plus live (non-terminal) setup ids for
    (coin, rule). Re-emits dedupe by id and terminal rows stay immutable,
    so replaying missed closed bars after this point is idempotent with no
    duplicate actives."""
    ensure_watcher_tables(conn)
    row = conn.execute(
        "SELECT watermark FROM watcher_checkpoint WHERE coin=? AND rule_id=?",
        (coin, rule)).fetchone()
    actives = [r[0] for r in conn.execute(
        "SELECT id FROM watcher_setup WHERE coin=? AND rule_id=?"
        " AND state='active' ORDER BY src_bar_ts",
        (coin, rule)).fetchall()]
    return {"watermark": row[0] if row else None, "active_ids": actives}


# ---------------------------------------------------------------------------
# Task 7: cost composer + shadow selector (shadow-only) + flag writer.
#
# The selector NEVER suppresses setups: it only returns the shadow-picked id
# set, and apply_shadow_flags writes the shadow_selected/ev_r/p_lb columns
# while leaving state/monitoring (and every scanner table) untouched.
# Funding-unavailable setups are never selected (gross available, net EV
# unavailable). Scorer calibration (logistic/Beta/p-hat-LB training) is
# follow-up work per the plan; p_lb here is the frozen interface fed by the
# hand-set scorer v0.
# ---------------------------------------------------------------------------

SELECTOR_K = 15
EV_HURDLE = 0.1
CLUSTER_CAP = 2        # max selected per |rho| > 0.7 cluster
CLUSTER_RHO = 0.7      # cluster edge threshold (computed upstream on
                       # trailing-7d 1h residuals; setups sharing a `cluster`
                       # label are treated as one correlated set)
WATCHER_PAUSED = False
WATCHER_DISABLED_RULES = ()


def cost_total(base_cost, direction, rates):
    """Round-trip cost composer (Task 3 deferred minor, defined once here).

    ``max(0, base_cost + fund_cost(direction, rates))``: the long funding leg
    raises the total, the short rebate lowers it floored at 0, and the signed
    leg stays available via fund_cost for the net-return column.
    """
    return max(0.0, base_cost + fund_cost(direction, rates))


def net_ret(gross_ret, base_cost, direction, rates):
    """Signed net return for the net column: gross minus the FULL signed cost
    (base + signed funding leg, no floor). None gross -> None."""
    if gross_ret is None:
        return None
    return gross_ret - (base_cost + fund_cost(direction, rates))


def ev_r(p_lb, a, b, c):
    """Selector expectancy in R: ``[p_lb*a - (1-p_lb)*b - c_total] / b``.

    Time-expiry and invalidation are non-wins in training labels (0R beyond
    costs in selector EV; realized returns are reported separately).
    ``b <= 0`` (or any missing input) -> -inf: unselectable, never an error.
    """
    if (p_lb is None or a is None or b is None or c is None
            or b <= 0):
        return float("-inf")
    return (p_lb * a - (1.0 - p_lb) * b - c) / b


def _cluster_key(cluster):
    if cluster is None:
        return None
    if isinstance(cluster, (str, int, float, bool, tuple, frozenset)):
        return cluster
    return repr(cluster)


def shadow_select(setups, K=15, ev_hurdle=0.1, paused=False,
                  disabled=()):
    """Shadow-only pick: ``EV_R >= ev_hurdle``, ranked by EV_R, K/day ceiling,
    at most CLUSTER_CAP (2) per |rho|>0.7 cluster. Returns ``set[id]``.

    Each setup dict carries ``id`` plus ``p_lb`` (aliases ``p_hat_lb``/``p``),
    ``a`` (``a_tgt``), ``b`` (``b_stop``), ``c`` (``c_total``/``cost_total``),
    ``funding`` (``funding_state``; ``"unavailable"`` -> never selected) and
    an optional ``cluster`` label (missing or None -> its own id, i.e.
    uncorrelated and never capped with another setup).
    Missing p/a/b/c, NaN EV, paused, or a disabled rule_id -> silent skip.
    Emission, state, monitoring, ranking and flags are never touched here.
    """
    if paused:
        return set()
    off = set(disabled or ())
    scored = []
    for s in setups or []:
        sid = (s.get("id") if isinstance(s, dict) else None)
        if sid is None:
            continue
        if s.get("rule_id") in off:
            continue
        fund = s.get("funding", s.get("funding_state", "ok"))
        if fund == "unavailable":
            continue
        p = s.get("p_lb", s.get("p_hat_lb", s.get("p")))
        a = s.get("a", s.get("a_tgt"))
        b = s.get("b", s.get("b_stop"))
        c = s.get("c", s.get("c_total", s.get("cost_total")))
        ev = ev_r(p, a, b, c)
        if not ev >= ev_hurdle:
            continue
        scored.append((ev, sid, _cluster_key(
            s.get("cluster") if s.get("cluster") is not None else sid)))
    scored.sort(key=lambda t: (-t[0], str(t[1])))
    picked, per_cluster = set(), {}
    for ev, sid, cluster in scored:
        if len(picked) >= K:
            break
        n = per_cluster.get(cluster, 0)
        if n >= CLUSTER_CAP:
            continue
        per_cluster[cluster] = n + 1
        picked.add(sid)
    return picked


def apply_shadow_flags(conn, selected, ev_by_id=None, p_by_id=None):
    """Write shadow columns only (shadow_selected/ev_r/p_lb) for every
    watcher_setup row. Rows leaving the selected set are cleared to
    ``(0, NULL, NULL)`` so stale EV never lingers on a deselected setup.
    State, substate, monitoring and all scanner tables are never touched,
    so unselected setups stay exactly as emitted (active)."""
    ensure_watcher_tables(conn)
    selected = set(selected or [])
    ev_by_id = ev_by_id or {}
    p_by_id = p_by_id or {}
    ids = [r[0] for r in conn.execute("SELECT id FROM watcher_setup").fetchall()]
    for sid in ids:
        if sid in selected:
            sets = ["shadow_selected=1"]
            params = []
            if sid in ev_by_id and ev_by_id[sid] is not None:
                sets.append("ev_r=?")
                params.append(ev_by_id[sid])
            if sid in p_by_id and p_by_id[sid] is not None:
                sets.append("p_lb=?")
                params.append(p_by_id[sid])
        else:
            sets = ["shadow_selected=0", "ev_r=NULL", "p_lb=NULL"]
            params = []
        params.append(sid)
        conn.execute(
            f"UPDATE watcher_setup SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()
    return len(selected & set(ids))
