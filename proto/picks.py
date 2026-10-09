"""Picks, watch list, new listings, and daily top-20 tracking.

Top 10 picks  = current scan's best actionable coins (is_actionable(stake)
                and score >= threshold), ranked by score. What to trade NOW.
Watch list    = highest-score coins blocked only by stake (WATCH: min
                notional > stake). What becomes tradable as stake compounds.
New listings  = coins first seen in the log within `days` (default 7),
                with their latest score. Fresh venues for early signals.
Daily top 20  = the measurement set for the 3–7 day review: per UTC day,
                top 20 flagged rows by score, with outcome hit-rates at
                1h/4h/24h for whatever has resolved so far.
"""

NEW_DAYS = 7
TOP_N = 10
WATCH_N = 10
NEW_N = 15
DAILY_N = 20


def top_picks(cards, stake, threshold, n=TOP_N):
    """Top `n` actionable cards by score (trade-now list)."""
    from proto.scorer import fits_stake
    return sorted(
        (c for c in cards
         if not getattr(c, "vetoes", None)
         and getattr(c, "direction", "NEUTRAL") != "NEUTRAL"
         and fits_stake(getattr(c, "min_notional", None), stake)
         and (c.score or 0) >= threshold),
        key=lambda c: -(c.score or 0))[:n]


def watch_list(cards, stake, n=WATCH_N):
    """Top `n` non-vetoed directional cards blocked ONLY by stake.

    min_notional unknown also lands here (fail-closed: cannot confirm it
    fits, so it waits with the watch list rather than the picks).
    """
    from proto.scorer import fits_stake

    def blocked(c):
        return not fits_stake(getattr(c, "min_notional", None), stake)

    return sorted(
        (c for c in cards
         if not getattr(c, "vetoes", None)
         and getattr(c, "direction", "NEUTRAL") != "NEUTRAL"
         and blocked(c)),
        key=lambda c: -(c.score or 0))[:n]


def new_listings(store, days=NEW_DAYS, n=NEW_N):
    """Coins first logged within `days`, with latest score/direction.

    [(coin, first_seen_ts, score, direction)] newest-first. Pure history:
    first-seen = MIN(ts) per coin, latest row supplies the score.
    """
    if store is None:
        return []
    import time
    rows = store.conn.execute(
        "SELECT coin, MIN(ts) AS first_seen FROM signal_log"
        " GROUP BY coin HAVING first_seen > ?"
        " ORDER BY first_seen DESC",
        (time.time() - days * 86400,)).fetchall()
    out = []
    for coin, first_seen in rows:
        r = store.conn.execute(
            "SELECT score, direction, ts FROM signal_log WHERE coin=?"
            " ORDER BY ts DESC LIMIT 1", (coin,)).fetchone()
        if r:
            out.append({"coin": coin, "first_seen": first_seen,
                        "score": r[0], "direction": r[1], "ts": r[2]})
    out.sort(key=lambda d: -(d["score"] or 0))
    return out[:n]


def daily_top20(store, days=7, n=DAILY_N):
    """Per-UTC-day top `n` flagged rows by score + their outcome hit-rates.

    [{"day": "YYYY-MM-DD", "picks": [{"coin","score","direction","price"}],
      "hits": {"1h": {"resolved","wins","pct"}, ...}}] newest day last.
    Unresolved horizons show resolved=0 (pending, never zero-filled).
    """
    import time
    out = []
    if store is None:
        return out
    now = time.time()
    for back in range(days - 1, -1, -1):
        day_start = (now // 86400 - back) * 86400
        day = time.strftime("%Y-%m-%d", time.gmtime(day_start))
        picks = store.conn.execute(
            "SELECT coin, score, direction, price, id FROM signal_log"
            " WHERE flagged=1 AND ts>=? AND ts<?"
            " ORDER BY score DESC LIMIT ?",
            (day_start, day_start + 86400, n)).fetchall()
        ids = [p[4] for p in picks]
        hits = {}
        for h in ("1h", "4h", "24h", "7d"):
            if not ids:
                hits[h] = {"resolved": 0, "wins": 0, "pct": 0.0}
                continue
            q = (f"SELECT COUNT(*), SUM(CASE WHEN o.return_pct>0 THEN 1"
                 f" ELSE 0 END) FROM outcome_log o WHERE o.horizon=? AND"
                 f" o.signal_id IN ({','.join('?' * len(ids))})")
            cnt, wins = store.conn.execute(q, (h, *ids)).fetchone()
            cnt = cnt or 0
            wins = wins or 0
            hits[h] = {"resolved": cnt, "wins": wins,
                       "pct": 100.0 * wins / cnt if cnt else 0.0}
        out.append({"day": day,
                    "picks": [{"coin": p[0], "score": p[1],
                               "direction": p[2], "price": p[3]}
                              for p in picks],
                    "hits": hits})
    return out
