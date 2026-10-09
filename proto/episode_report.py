"""
Episode reporting and cohort statistics (spec 11).

Read-only aggregation over the episode tables. The unit of measurement is
the episode, never the scan observation: a repeated flagged scan for one
coin belongs to one episode and is counted once.

Every count block carries an explicit `denominator` label so a rate can
never be quoted against an unnamed base:
- fill rate      = FILLED / (FILLED + UNFILLED)
- unfilled rate  = UNFILLED / started
- stop/TP2/expiry rates are over TERMINAL FILLED plans (STOPPED, TP2,
  EXPIRED); the to-date touch rate over all FILLED plans is labeled
  "to date" and reported separately.

PENDING_ENTRY, UNAVAILABLE and OPEN plans are never losses and never
appear in a win/loss or return denominator.

Coin-cluster bootstrap 95% intervals appear only with >= 30 distinct coin
clusters (spec 11); below that, point estimates and counts only.

HARD PROHIBITION (spec 10, 14): no net P&L, no net win rate and no
simulated trade return is produced anywhere in this module's output,
because no approved exit policy exists. `disclosure` states this.
"""

import random
import time

HORIZONS = ("1h", "4h", "24h", "7d")

ENTRY_STATUSES = ("PENDING_ENTRY", "FILLED", "UNFILLED", "UNAVAILABLE")
TRADE_STATUSES = ("OPEN", "STOPPED", "TP2", "EXPIRED", "UNAVAILABLE")
TERMINAL_TRADE_STATUSES = ("STOPPED", "TP2", "EXPIRED")
CLOSE_REASONS = ("REARM_CONFIRMED", "REVERSAL", "COVERAGE_LOST")

MIN_EXPLORATORY_EPISODES = 20
MIN_EXPLORATORY_COINS = 10
MIN_BOOTSTRAP_COIN_CLUSTERS = 30
_BOOTSTRAP_ITERATIONS = 2000
_BOOTSTRAP_SEED = 20261009  # deterministic: same DB -> same interval

SUPPORTED_DIMENSIONS = ("direction", "score_band", "setup")
SCORE_BANDS = ((0, 40), (40, 60), (60, 80), (80, 101))


def _utc_day(ts):
    """UTC day label (YYYY-MM-DD) for a unix timestamp."""
    return time.strftime("%Y-%m-%d", time.gmtime(int(ts or 0)))


def _pct(numerator, denominator):
    """Percent or 0.0 when the denominator is zero. Never raises."""
    if not denominator:
        return 0.0
    return 100.0 * numerator / denominator


def _rate(numerator, denominator_count, denominator_label):
    """One rate with both its numeric base and its named base.

    `denominator` is the label (what the rate is over) and
    `denominator_count` is the number, so a rate can never be quoted
    against an unnamed base.
    """
    return {"numerator": numerator,
            "denominator": denominator_label,
            "denominator_count": denominator_count,
            "pct": _pct(numerator, denominator_count)}


def _bootstrap_note(coin_clusters):
    return (f"coin-cluster bootstrap 95% intervals require >= "
            f"{MIN_BOOTSTRAP_COIN_CLUSTERS} distinct coin clusters; "
            f"this cohort has {coin_clusters}")


# Spec 11 cohort keys -> signal_episode columns.
_RULE_VERSION_COLUMNS = {
    "flag_rule": "flag_rule_version",
    "plan_rule": "plan_rule_version",
    "episode_rule": "episode_rule_version",
    "outcome_rule": "outcome_rule_version",
    "cost_model": "cost_model_version",
}


def _cohort_clause(store, config_hash, rule_versions):
    """WHERE fragment and params selecting one immutable cohort.

    Unknown rule-version keys raise: silently ignoring one would merge
    cohorts that spec 14 requires to stay separate.
    """
    clause = ""
    params = []
    if config_hash is not None:
        clause += " AND config_hash=?"
        params.append(config_hash)
    if rule_versions:
        for key in sorted(rule_versions):
            if key not in _RULE_VERSION_COLUMNS:
                raise ValueError(
                    f"unknown rule version {key!r}: expected one of"
                    f" {sorted(_RULE_VERSION_COLUMNS)}")
            clause += f" AND {_RULE_VERSION_COLUMNS[key]}=?"
            params.append(rule_versions[key])
    return clause, params


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------

def summary(store, *, config_hash=None, rule_versions=None, now=None):
    """
    Cohort summary with explicit denominators.

    Returns a dict with:
    - cohort: config_hash / rule versions selected, config hashes seen, and
      a mixed-configs warning (a mixed DB reports each cohort separately).
    - started: all episodes in the cohort, including NO_PLAN.
    - open: count, OPEN/REARMING split.
    - closed: count, by close reason.
    - after_gap: count and denominator.
    - plan: PLANNED / NO_PLAN counts (denominator: started).
    - entry: entry_status counts (denominator: planned episodes).
    - trade: trade_status counts and tp1_before_stop (denominator: filled
      plans).
    - horizons: per-horizon matured/pending/unavailable counts.
    - rates: fill_rate, unfilled_rate, stop/TP2/expiry rates over terminal
      filled plans, to_date touch counts.
    - coverage: coins, max observation gap, gaps > 15m.
    - concentration: top-coin share, busiest UTC day share.
    - statistics: coin clusters and an optional coin-cluster bootstrap CI.
    - disclosure: explicit statement that no net P&L is reported.
    """
    if store is None:
        return _empty_summary()
    conn = store.conn
    clause, params = _cohort_clause(store, config_hash, rule_versions)

    hashes = [r[0] for r in conn.execute(
        "SELECT DISTINCT config_hash FROM signal_episode"
        " WHERE config_hash IS NOT NULL").fetchall()]

    episodes = [dict(zip(_EP_FIELDS, r)) for r in conn.execute(
        "SELECT " + ", ".join(_EP_FIELDS) + " FROM signal_episode"
        + (" WHERE 1=1" + clause if clause else ""), params).fetchall()]
    episode_ids = [e["id"] for e in episodes]

    counts = _counts(episodes)
    outcomes = _outcomes(conn, episode_ids)
    counts["entry"] = outcomes["entry"]
    counts["trade"] = outcomes["trade"]
    horizons = _horizons(conn, episode_ids)
    rates = _rates(counts, outcomes)
    coverage = _coverage(conn, episode_ids)
    concentration = _concentration(episodes)
    statistics = _statistics(counts, outcomes, episodes)
    return {
        "cohort": {
            "config_hash": config_hash,
            "rule_versions": rule_versions,
            "config_hashes_seen": hashes,
            "mixed_configs_warning": len(hashes) > 1,
            "mixed_configs_note": (
                "episodes from different config hashes are separate "
                "cohorts: report each one separately, never merged"),
        },
        "started": counts["started"],
        "open": counts["open"],
        "closed": counts["closed"],
        "after_gap": counts["after_gap"],
        "plan": counts["plan"],
        "entry": counts["entry"],
        "trade": counts["trade"],
        "horizons": horizons,
        "rates": rates,
        "coverage": coverage,
        "concentration": concentration,
        "statistics": statistics,
        "disclosure": {
            "net_pnl_reported": False,
            "net_win_rate_reported": False,
            "simulated_trade_return_reported": False,
            "exit_policy": ("no approved exit policy exists: TP1/TP2 "
                            "allocation and post-TP1 stop policy are "
                            "undecided, so only gross price/touch outcomes "
                            "and fixed-horizon status are reported"),
            "unit": "episode (one per coin/direction/config episode)",
        },
    }


_EP_FIELDS = (
    "id", "coin", "direction", "start_ts", "state", "close_reason",
    "after_gap", "plan_status", "config_hash", "flag_rule_version",
    "plan_rule_version", "episode_rule_version", "outcome_rule_version",
    "cost_model_version", "qualifying_obs", "non_qualifying_obs",
    "unknown_obs", "late_obs",
)


def _empty_summary():
    """Zeroed summary shape: same keys as a populated one, so a caller
    can never KeyError on an empty cohort."""
    return {
        "cohort": {"config_hash": None, "rule_versions": None,
                   "config_hashes_seen": [],
                   "mixed_configs_warning": False, "mixed_configs_note": ""},
        "started": 0,
        "open": {"count": 0, "OPEN": 0, "REARMING": 0,
                 "denominator": "started"},
        "closed": {"count": 0, "by_reason": {r: 0 for r in CLOSE_REASONS},
                   "denominator": "started"},
        "after_gap": {"count": 0, "denominator": "started"},
        "plan": {"PLANNED": 0, "NO_PLAN": 0, "denominator": "started"},
        "entry": {"denominator": "planned episodes",
                  **{s: 0 for s in ENTRY_STATUSES}},
        "trade": {"denominator": "filled plans",
                  **{s: 0 for s in TRADE_STATUSES}, "tp1_before_stop": 0},
        "horizons": {h: {"matured": 0, "pending": 0, "unavailable": 0,
                         "denominator": "filled plans with horizon rows"}
                     for h in HORIZONS},
        "rates": {
            "fill_rate": _rate(0, 0, "filled + unfilled plans"),
            "unfilled_rate": _rate(0, 0, "started"),
            "terminal_filled": 0,
            "stop_rate": _rate(0, 0, "terminal filled plans"),
            "tp2_rate": _rate(0, 0, "terminal filled plans"),
            "expiry_rate": _rate(0, 0, "terminal filled plans"),
            "tp1_before_stop_rate": _rate(0, 0, "terminal stopped plans"),
            "to_date_touch": {"label": "to date (not terminal; moves as"
                                        " bars arrive)",
                              "denominator": "filled plans",
                              "filled": 0, "stop_touches": 0,
                              "tp2_touches": 0},
        },
        "coverage": {"coins": 0, "max_observation_gap_s": 0,
                     "observation_gaps_over_15m": 0,
                     "denominator": "cohort episodes"},
        "concentration": {"top_coin": None, "top_coin_share_pct": 0.0,
                          "busiest_utc_day": None,
                          "busiest_day_share_pct": 0.0,
                          "denominator": "started"},
        "statistics": {"coin_clusters": 0, "bootstrap": None,
                       "bootstrap_note": _bootstrap_note(0),
                       "unit": "episode; coins are not independent samples"},
        "disclosure": {"net_pnl_reported": False,
                       "net_win_rate_reported": False,
                       "simulated_trade_return_reported": False,
                       "exit_policy": "no approved exit policy exists",
                       "unit": "episode"},
    }


def _counts(episodes):
    started = len(episodes)
    open_episodes = [e for e in episodes if e["state"] in ("OPEN", "REARMING")]
    closed = [e for e in episodes if e["state"] == "CLOSED"]
    by_reason = {r: 0 for r in CLOSE_REASONS}
    for e in closed:
        if e["close_reason"] in by_reason:
            by_reason[e["close_reason"]] += 1
        else:
            by_reason[e["close_reason"] or "UNKNOWN"] = (
                by_reason.get(e["close_reason"] or "UNKNOWN", 0) + 1)
    planned = sum(1 for e in episodes if e["plan_status"] == "PLANNED")
    no_plan = sum(1 for e in episodes if e["plan_status"] == "NO_PLAN")
    return {
        "started": started,
        "open": {"count": len(open_episodes),
                 "OPEN": sum(1 for e in open_episodes
                             if e["state"] == "OPEN"),
                 "REARMING": sum(1 for e in open_episodes
                                 if e["state"] == "REARMING"),
                 "denominator": "started"},
        "closed": {"count": len(closed), "by_reason": by_reason,
                   "denominator": "started"},
        "after_gap": {"count": sum(1 for e in episodes if e["after_gap"]),
                      "denominator": "started"},
        "plan": {"PLANNED": planned, "NO_PLAN": no_plan,
                 "denominator": "started"},
    }


def _outcomes(conn, episode_ids):
    """entry_status / trade_status counts over this cohort's episodes."""
    out = {"by_id": {},
           "entry": {"denominator": "planned episodes",
                     **{s: 0 for s in ENTRY_STATUSES}},
           "trade": {"denominator": "filled plans",
                     **{s: 0 for s in TRADE_STATUSES}, "tp1_before_stop": 0}}
    if not episode_ids:
        return out
    placeholders = ",".join("?" for _ in episode_ids)
    rows = conn.execute(
        "SELECT episode_id, entry_status, trade_status, tp1_before_stop,"
        " fill_ts, stop_bar_idx, tp2_bar_idx FROM episode_outcome"
        f" WHERE episode_id IN ({placeholders})", episode_ids).fetchall()
    terminal = []
    for ep_id, entry, trade, tp1_stop, fill_ts, stop_idx, tp2_idx in rows:
        out["by_id"][ep_id] = {"entry_status": entry, "trade_status": trade,
                               "tp1_before_stop": tp1_stop or 0,
                               "tp2_touched": tp2_idx is not None,
                               "stop_touched": stop_idx is not None}
        if entry in ENTRY_STATUSES:
            out["entry"][entry] += 1
        else:
            out["entry"][entry or "UNKNOWN"] = (
                out["entry"].get(entry or "UNKNOWN", 0) + 1)
        if entry == "FILLED":
            key = trade if trade in TRADE_STATUSES else "UNKNOWN"
            out["trade"][key] += 1
            if trade == "STOPPED" and tp1_stop:
                out["trade"]["tp1_before_stop"] += 1
            if trade in TERMINAL_TRADE_STATUSES:
                terminal.append(ep_id)
    out["terminal"] = terminal
    return out


def _horizons(conn, episode_ids):
    out = {h: {"matured": 0, "pending": 0, "unavailable": 0,
               "denominator": "filled plans with horizon rows"}
           for h in HORIZONS}
    if not episode_ids:
        return out
    placeholders = ",".join("?" for _ in episode_ids)
    for horizon, status in conn.execute(
            "SELECT horizon, status FROM episode_horizon"
            f" WHERE episode_id IN ({placeholders})",
            episode_ids).fetchall():
        if horizon not in out:
            continue
        key = {"MATURED": "matured", "PENDING": "pending",
               "UNAVAILABLE": "unavailable"}.get(status)
        if key:
            out[horizon][key] += 1
    return out


def _rates(counts, outcomes):
    """Rate block. Every rate names its denominator.

    Terminal stop/TP2/expiry rates are over TERMINAL FILLED plans
    (STOPPED, TP2, EXPIRED). OPEN, UNFILLED, PENDING_ENTRY and UNAVAILABLE
    are never losses and never appear here. The to-date touch counts are
    reported separately and labeled.
    """
    entry = outcomes["entry"]
    filled = entry.get("FILLED", 0)
    unfilled = entry.get("UNFILLED", 0)
    terminal = len(outcomes.get("terminal", []))
    trade = outcomes["trade"]
    stopped = trade.get("STOPPED", 0)
    tp2 = trade.get("TP2", 0)
    expired = trade.get("EXPIRED", 0)
    # To-date touches over all FILLED plans: they move as bars arrive and
    # are NOT terminal results (spec 11 "to date").
    to_date_stop = sum(1 for o in outcomes["by_id"].values()
                       if o["entry_status"] == "FILLED" and o["stop_touched"])
    to_date_tp2 = sum(1 for o in outcomes["by_id"].values()
                      if o["entry_status"] == "FILLED" and o["tp2_touched"])
    return {
        "fill_rate": _rate(filled, filled + unfilled,
                           "filled + unfilled plans"),
        "unfilled_rate": _rate(unfilled, counts["started"], "started"),
        "terminal_filled": terminal,
        "stop_rate": _rate(stopped, terminal, "terminal filled plans"),
        "tp2_rate": _rate(tp2, terminal, "terminal filled plans"),
        "expiry_rate": _rate(expired, terminal, "terminal filled plans"),
        "tp1_before_stop_rate": _rate(
            trade.get("tp1_before_stop", 0), stopped,
            "terminal stopped plans"),
        "to_date_touch": {
            "label": "to date (not terminal; moves as bars arrive)",
            "denominator": "filled plans",
            "filled": filled, "stop_touches": to_date_stop,
            "tp2_touches": to_date_tp2,
        },
    }


def _coverage(conn, episode_ids):
    """Collector coverage: distinct coins, largest observation gap, and the
    count of >15-minute gaps between consecutive valid observations.

    The gap is measured per coin across the whole cohort (every linked
    observation of that coin), not within one episode: collector coverage
    is about the data stream, and an episode boundary must not hide a gap.
    """
    out = {"coins": 0, "max_observation_gap_s": 0,
           "observation_gaps_over_15m": 0,
           "denominator": "cohort episodes"}
    if not episode_ids:
        return out
    placeholders = ",".join("?" for _ in episode_ids)
    rows = conn.execute(
        "SELECT eo.coin, eo.obs_ts FROM episode_observation eo"
        f" WHERE eo.episode_id IN ({placeholders})"
        " AND eo.obs_ts IS NOT NULL ORDER BY eo.coin, eo.obs_ts",
        episode_ids).fetchall()
    per_coin = {}
    for coin, ts in rows:
        per_coin.setdefault(coin, []).append(ts)
    max_gap = 0
    gap_events = 0
    for ts_list in per_coin.values():
        for a, b in zip(ts_list, ts_list[1:]):
            gap = b - a
            max_gap = max(max_gap, gap)
            if gap > 15 * 60:
                gap_events += 1
    out["max_observation_gap_s"] = max_gap
    out["observation_gaps_over_15m"] = gap_events
    coin_rows = conn.execute(
        "SELECT DISTINCT coin FROM signal_episode"
        f" WHERE id IN ({placeholders})", episode_ids).fetchall()
    out["coins"] = len(coin_rows)
    return out


def _concentration(episodes):
    """Share of episodes from the top coin and the busiest UTC day."""
    if not episodes:
        return {"top_coin": None, "top_coin_share_pct": 0.0,
                "busiest_utc_day": None, "busiest_day_share_pct": 0.0,
                "denominator": "started"}
    coins = {}
    days = {}
    for e in episodes:
        coins[e["coin"]] = coins.get(e["coin"], 0) + 1
        day = _utc_day(e["start_ts"])
        days[day] = days.get(day, 0) + 1
    n = len(episodes)
    top_coin, top_n = max(coins.items(), key=lambda kv: (kv[1], kv[0]))
    top_day, day_n = max(days.items(), key=lambda kv: (kv[1], kv[0]))
    return {"top_coin": top_coin, "top_coin_share_pct": _pct(top_n, n),
            "busiest_utc_day": top_day,
            "busiest_day_share_pct": _pct(day_n, n),
            "denominator": "started"}


def _statistics(counts, outcomes, episodes):
    """Coin-cluster bootstrap; altcoins co-move, so the coin is the unit."""
    coins = {e["coin"] for e in episodes}
    n_coins = len(coins)
    stats = {"coin_clusters": n_coins, "bootstrap": None,
             "bootstrap_note": _bootstrap_note(n_coins),
             "unit": "episode; coins are not independent samples"}
    if n_coins < MIN_BOOTSTRAP_COIN_CLUSTERS:
        return stats
    # Per-episode outcome flags, resampled by coin cluster.
    by_coin = {}
    for e in episodes:
        o = outcomes["by_id"].get(e["id"], {})
        entry = o.get("entry_status")
        trade = o.get("trade_status")
        by_coin.setdefault(e["coin"], []).append(
            {"filled": entry == "FILLED",
             "unfilled": entry == "UNFILLED",
             "terminal": trade in TERMINAL_TRADE_STATUSES,
             "stopped": trade == "STOPPED",
             "tp2": trade == "TP2",
             "expired": trade == "EXPIRED"})
    stats["bootstrap"] = _bootstrap_interval(by_coin)
    return stats


def _bootstrap_interval(by_coin):
    rng = random.Random(_BOOTSTRAP_SEED)
    coin_list = sorted(by_coin)
    fill_pts, stop_pts, tp2_pts, exp_pts = [], [], [], []
    for _ in range(_BOOTSTRAP_ITERATIONS):
        sample = []
        for _ in coin_list:
            sample.extend(by_coin[rng.choice(coin_list)])
        denom = sum(1 for s in sample if s["filled"] or s["unfilled"])
        if denom:
            fill_pts.append(100.0 * sum(1 for s in sample if s["filled"])
                            / denom)
        term = [s for s in sample if s["terminal"]]
        if term:
            stop_pts.append(100.0 * sum(1 for s in term if s["stopped"])
                            / len(term))
            tp2_pts.append(100.0 * sum(1 for s in term if s["tp2"]) / len(term))
            exp_pts.append(100.0 * sum(1 for s in term if s["expired"])
                           / len(term))

    def bounds(pts):
        if not pts:
            return [0.0, 0.0]
        pts = sorted(pts)
        lo = pts[int(0.025 * (len(pts) - 1))]
        hi = pts[int(0.975 * (len(pts) - 1))]
        return [round(lo, 4), round(hi, 4)]

    return {"level": "95%", "method": "coin-cluster bootstrap",
            "resamples": _BOOTSTRAP_ITERATIONS, "seed": _BOOTSTRAP_SEED,
            "fill_rate_ci": bounds(fill_pts),
            "stop_rate_ci": bounds(stop_pts),
            "tp2_rate_ci": bounds(tp2_pts),
            "expiry_rate_ci": bounds(exp_pts)}


# --------------------------------------------------------------------------
# breakdowns
# --------------------------------------------------------------------------

def breakdowns(store, *, dimension, min_episodes=MIN_EXPLORATORY_EPISODES,
               min_coins=MIN_EXPLORATORY_COINS):
    """
    Exploratory cohort breakdowns (spec 11).

    `dimension` is one of "direction", "score_band" or "setup". Every cell
    is labeled exploratory and shown only when it holds at least
    `min_episodes` episodes across at least `min_coins` coins.

    Each cell carries its own counts and the same explicit denominators as
    `summary`: fill rate over filled+unfilled, terminal rates over terminal
    filled plans. No net P&L appears.
    """
    if dimension not in SUPPORTED_DIMENSIONS:
        raise ValueError(
            f"unsupported breakdown dimension {dimension!r}: expected one of"
            f" {SUPPORTED_DIMENSIONS}")
    if store is None:
        return []
    conn = store.conn
    rows = [dict(zip(_EP_FIELDS + ("score",), r)) for r in conn.execute(
        "SELECT " + ", ".join(_EP_FIELDS)
        + ", (SELECT s.score FROM signal_log s WHERE s.id = e.first_signal_id)"
        " AS score FROM signal_episode e").fetchall()]
    if not rows:
        return []
    episode_ids = [r["id"] for r in rows]
    outcomes = _outcomes(conn, episode_ids)

    cells = {}
    for e in rows:
        key = _cell_key(e, dimension)
        if key is None:
            continue
        cell = cells.setdefault(key, {"episodes": [], "coins": set()})
        cell["episodes"].append(e)
        cell["coins"].add(e["coin"])

    out = []
    for key in sorted(cells, key=lambda k: (str(k))):
        cell = cells[key]
        if len(cell["episodes"]) < min_episodes:
            continue
        if len(cell["coins"]) < min_coins:
            continue
        eps = cell["episodes"]
        counts = _counts(eps)
        # Cell-local outcome views: the same denominator rules as summary.
        entry_tot = {"denominator": "planned episodes",
                     **{s: 0 for s in ENTRY_STATUSES}}
        trade_tot = {"denominator": "filled plans",
                     **{s: 0 for s in TRADE_STATUSES}, "tp1_before_stop": 0}
        cell_by_id = {}
        for e in eps:
            o = outcomes["by_id"].get(e["id"])
            if not o:
                continue
            cell_by_id[e["id"]] = o
            entry_tot[o["entry_status"]] = entry_tot.get(
                o["entry_status"], 0) + 1
            if o["entry_status"] == "FILLED":
                trade_tot[o["trade_status"]] = trade_tot.get(
                    o["trade_status"], 0) + 1
                if o["trade_status"] == "STOPPED" and o["tp1_before_stop"]:
                    trade_tot["tp1_before_stop"] += 1
        counts["entry"] = entry_tot
        counts["trade"] = trade_tot
        cell_outcomes = {"by_id": cell_by_id, "entry": entry_tot,
                         "trade": trade_tot, "terminal": [
                             o for o in cell_by_id.values()
                             if o["trade_status"] in TERMINAL_TRADE_STATUSES]}
        rates = _rates(counts, cell_outcomes)
        out.append({
            "dimension": dimension,
            "value": key,
            "exploratory": True,
            "exploratory_label": ("exploratory: not a primary metric; "
                                  "cells require enough episodes and coins"),
            "episodes": len(eps),
            "coins": len(cell["coins"]),
            "min_episodes": min_episodes,
            "min_coins": min_coins,
            "counts": {"plan": counts["plan"], "entry": counts["entry"],
                       "trade": counts["trade"]},
            "rates": rates,
        })
    return out


def _cell_key(episode, dimension):
    if dimension == "direction":
        return episode["direction"]
    if dimension == "setup":
        return episode["plan_status"]
    # score_band: the originating signal's score, banded
    score = episode.get("score")
    if score is None:
        return None
    for lo, hi in SCORE_BANDS:
        if lo <= float(score) < hi:
            return f"{lo}-{hi - 1}"
    return None
