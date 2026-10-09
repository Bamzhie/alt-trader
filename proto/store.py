"""
SQLite persistence: signal log + outcome log.

Implements spec SS6.1 and SS6.1a. The shadow-logging requirement matters: a log
containing only flagged signals is a numerator with no denominator, so a vetoed
coin that later pumps leaves no trace and the record cannot show what the
scanner missed.
"""

import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    coin            TEXT    NOT NULL,
    venue           TEXT    NOT NULL DEFAULT 'MEXC',
    flagged         INTEGER NOT NULL DEFAULT 1,   -- 0 = shadow row
    score           REAL,
    lean            REAL,
    direction       TEXT,
    earlyness       REAL,
    mag_vol         REAL,
    mag_book        REAL,
    mag_oi          REAL,
    lean_vol        REAL,
    lean_book       REAL,
    lean_oi         REAL,
    price           REAL,
    change_24h_pct  REAL,
    quote_vol_24h   REAL,
    spread_pct      REAL,
    funding_rate    REAL,
    min_notional    REAL,
    veto_codes      TEXT,
    tradeable       INTEGER NOT NULL DEFAULT 1,
    tier            INTEGER NOT NULL DEFAULT 2,
    -- 1 = pre-v2 history (stake-agnostic flag rule), 2 = current rule.
    -- Historical rows stay comparable by filtering on flag_version (SS6.1).
    flag_version    INTEGER DEFAULT 1,
    -- ~USDT open interest behind OIΔ% (NULL when unavailable/MEXC-only).
    oi_notional     REAL,
    -- OI percent behind OIΔ% (NULL when unavailable; kept so a relaunch
    -- from saved rows still shows the percent, not just funding-only).
    oi_change_pct   REAL
);
CREATE INDEX IF NOT EXISTS idx_signal_ts_coin ON signal_log(ts, coin);
CREATE INDEX IF NOT EXISTS idx_signal_flagged ON signal_log(flagged);

-- Current board: exactly one row per coin, upserted every scan. The same
-- coin is NEVER a new signal twice here — signal_log stays the append-only
-- journal (time series for analysis), coin_state is the materialized "now"
-- (what the table shows, what launch reads, first-seen tracking).
CREATE TABLE IF NOT EXISTS coin_state (
    coin            TEXT    PRIMARY KEY,
    venue           TEXT    NOT NULL DEFAULT 'MEXC',
    flagged         INTEGER NOT NULL DEFAULT 0,
    score           REAL,
    lean            REAL,
    direction       TEXT,
    earlyness       REAL,
    mag_vol         REAL,
    mag_book        REAL,
    mag_oi          REAL,
    lean_vol        REAL,
    lean_book       REAL,
    lean_oi         REAL,
    price           REAL,
    change_24h_pct  REAL,
    quote_vol_24h   REAL,
    spread_pct      REAL,
    funding_rate    REAL,
    oi_change_pct   REAL,
    oi_notional     REAL,
    min_notional    REAL,
    veto_codes      TEXT,
    tradeable       INTEGER NOT NULL DEFAULT 1,
    tier            INTEGER NOT NULL DEFAULT 2,
    signal_id       INTEGER REFERENCES signal_log(id),
    first_seen      INTEGER,
    last_seen       INTEGER,
    scans_seen      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS outcome_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id   INTEGER NOT NULL REFERENCES signal_log(id),
    horizon     TEXT    NOT NULL,     -- '1h' | '4h' | '24h' | '7d'
    return_pct  REAL,
    max_fav     REAL,                 -- best excursion in our favour
    max_adv     REAL,                 -- worst excursion against us
    resolved_at INTEGER,
    FOREIGN KEY (signal_id) REFERENCES signal_log(id)
);
CREATE INDEX IF NOT EXISTS idx_outcome_signal ON outcome_log(signal_id);

-- Logged trade plan per flagged signal: the levels the measurement judges.
-- One row per signal at most (a signal's plan is frozen at log time).
CREATE TABLE IF NOT EXISTS plan_log (
    signal_id   INTEGER PRIMARY KEY REFERENCES signal_log(id),
    direction   TEXT    NOT NULL,
    entry_low   REAL,
    entry_high  REAL,
    stop        REAL,
    tp1         REAL,
    tp2         REAL,
    leverage    INTEGER,
    notional    REAL,
    max_loss    REAL,
    logged_at   INTEGER
);

-- First-touch outcomes of the logged plan, walked bar by bar.
-- Same-bar stop+target touch counts the STOP (conservative: fills fail
-- against you first). NULL touch = not touched in the bars examined.
CREATE TABLE IF NOT EXISTS plan_outcome (
    signal_id   INTEGER PRIMARY KEY REFERENCES signal_log(id),
    stop_hit    INTEGER NOT NULL DEFAULT 0,
    tp1_hit     INTEGER NOT NULL DEFAULT 0,
    tp2_hit     INTEGER NOT NULL DEFAULT 0,
    bars_to_stop INTEGER,
    bars_to_tp1  INTEGER,
    bars_to_tp2  INTEGER,
    bars_examined INTEGER,
    resolved_at INTEGER
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, path):
        self.path = path
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """Add signal_log.flag_version to a pre-v2 database and backfill.

        Existing rows predate the stake-aware flag rule, so they are version 1;
        every row written from now on stamps 2. Idempotent: safe on reopen.
        """
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(signal_log)")}
        if "flag_version" not in cols:
            self.conn.execute(
                "ALTER TABLE signal_log ADD COLUMN flag_version INTEGER DEFAULT 1")
        self.conn.execute(
            "UPDATE signal_log SET flag_version=1 WHERE flag_version IS NULL")
        if "oi_notional" not in cols:
            self.conn.execute(
                "ALTER TABLE signal_log ADD COLUMN oi_notional REAL")
        if "oi_change_pct" not in cols:
            self.conn.execute(
                "ALTER TABLE signal_log ADD COLUMN oi_change_pct REAL")
        self._backfill_coin_state()

    def _backfill_coin_state(self):
        """One coin_state row per coin from the latest signal_log row.

        Runs when coin_state is empty but the journal has rows (upgrades and
        fresh checkouts against a copied DB): newest row per coin wins, ties
        broken by id. Idempotent — a non-empty coin_state is left alone.
        """
        n_state = self.conn.execute(
            "SELECT COUNT(*) FROM coin_state").fetchone()[0]
        if n_state:
            return
        self.conn.execute(
            """INSERT INTO coin_state
               (coin, venue, flagged, score, lean, direction, earlyness,
                mag_vol, mag_book, mag_oi, lean_vol, lean_book, lean_oi,
                price, change_24h_pct, quote_vol_24h, spread_pct,
                funding_rate, oi_change_pct, oi_notional, min_notional,
                veto_codes, tradeable, tier, signal_id, first_seen,
                last_seen, scans_seen)
               SELECT coin, venue, flagged, score, lean, direction, earlyness,
                mag_vol, mag_book, mag_oi, lean_vol, lean_book, lean_oi,
                price, change_24h_pct, quote_vol_24h, spread_pct,
                funding_rate, oi_change_pct, oi_notional, min_notional,
                veto_codes, tradeable, tier, id,
                (SELECT MIN(ts) FROM signal_log s2 WHERE s2.coin = coin),
                ts, (SELECT COUNT(*) FROM signal_log s3 WHERE s3.coin = coin)
               FROM signal_log s1
               WHERE id = (SELECT MAX(id) FROM signal_log s4
                           WHERE s4.coin = s1.coin)""")

    def latest_rows(self, window_s=180):
        """Most recent scan cycle's rows, newest scan first by score.

        A cycle logs its cards within seconds, so rows within `window_s` of
        MAX(ts) are one scan. Returns [] on an empty DB. Powers instant
        launch: the table renders saved rows while the live fetch runs.
        """
        row = self.conn.execute("SELECT MAX(ts) FROM signal_log").fetchone()
        if not row or row[0] is None:
            return []
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(signal_log)")]
        have_oi = "oi_change_pct" in cols
        have_notion = "oi_notional" in cols
        return [
            dict(zip(["coin", "venue", "flagged", "score", "lean",
                      "direction", "earlyness", "mag_vol", "mag_book",
                      "mag_oi", "lean_vol", "lean_book", "lean_oi", "price",
                      "change_24h_pct", "quote_vol_24h", "spread_pct",
                      "funding_rate", "min_notional", "veto_codes",
                      "tradeable", "tier", "ts",
                      "oi_change_pct", "oi_notional"], r))
            for r in self.conn.execute(
                f"""SELECT coin, venue, flagged, score, lean, direction,
                           earlyness, mag_vol, mag_book, mag_oi, lean_vol,
                           lean_book, lean_oi, price, change_24h_pct,
                           quote_vol_24h, spread_pct, funding_rate,
                           min_notional, veto_codes, tradeable, tier, ts,
                           {'oi_change_pct' if have_oi else 'NULL'},
                           {'oi_notional' if have_notion else 'NULL'}
                    FROM signal_log WHERE ts >= ? ORDER BY score DESC""",
                (row[0] - window_s,))]

    def log_signal(self, sc, flagged, tier=2):
        """Insert one scorecard. flagged=False writes a shadow row.

        flagged must already be computed with the stake-aware rule
        (Scorecard.is_actionable(stake) and score >= threshold); this method
        only stamps the row with the flag rule version it was written under.
        """
        cur = self.conn.execute(
            """INSERT INTO signal_log
               (ts, coin, venue, flagged, score, lean, direction, earlyness,
                mag_vol, mag_book, mag_oi, lean_vol, lean_book, lean_oi,
                price, change_24h_pct, quote_vol_24h, spread_pct,
                funding_rate, min_notional, veto_codes, tradeable, tier,
                flag_version, oi_notional, oi_change_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,2,?,?)""",
            (int(time.time()), sc.coin, sc.venue, 1 if flagged else 0,
             sc.score, sc.lean, sc.direction, sc.earlyness,
             sc.magnitude_parts.get("VOL"), sc.magnitude_parts.get("BOOK"),
             sc.magnitude_parts.get("OI"),
             sc.lean_parts.get("VOL"), sc.lean_parts.get("BOOK"),
             sc.lean_parts.get("OI"),
             sc.price, sc.change_24h_pct, sc.quote_vol_24h, sc.spread_pct,
             sc.funding_rate, sc.min_notional,
             ",".join(v.code for v in sc.vetoes),
             1 if sc.tradeable else 0, tier, sc.oi_notional,
             sc.oi_change_pct))
        self.conn.commit()
        return cur.lastrowid

    def log_plan(self, signal_id, plan):
        """Freeze a flagged signal's trade plan for stop/TP measurement."""
        import time as _t
        self.conn.execute(
            """INSERT OR REPLACE INTO plan_log
               (signal_id, direction, entry_low, entry_high, stop, tp1, tp2,
                leverage, notional, max_loss, logged_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (signal_id, plan.direction, plan.entry_low, plan.entry_high,
             plan.stop, plan.tp1, plan.tp2, plan.leverage, plan.notional,
             plan.max_loss, int(_t.time())))
        self.conn.commit()

    def planned(self, limit=2000):
        """(signal_id, coin, direction, entry/ts..., stop, tp1, tp2, ts)
        for flagged signals with a logged plan and no TERMINAL outcome.

        Terminal = stop touched or TP2 touched (the trade is over). Rows
        with only TP1 (or nothing yet) stay in the set so later runs with
        more bars keep resolving them. Ordered least-recently-examined
        first (unexamined first): with a LIMIT cap, a large unterminated
        set advances every run instead of reselecting the same oldest rows
        forever. Then oldest signal first within the same examined state.
        """
        return list(self.conn.execute(
            """SELECT p.signal_id, s.coin, s.direction, s.price, s.ts,
                      p.stop, p.tp1, p.tp2
               FROM plan_log p JOIN signal_log s ON s.id = p.signal_id
               LEFT JOIN plan_outcome o ON o.signal_id = p.signal_id
               WHERE s.flagged = 1
                 AND (o.signal_id IS NULL
                      OR (o.stop_hit = 0 AND o.tp2_hit = 0))
               ORDER BY o.resolved_at ASC, s.ts ASC LIMIT ?""",
            (limit,)).fetchall())

    def log_plan_outcome(self, signal_id, stop_hit, tp1_hit, tp2_hit,
                         bars_to_stop, bars_to_tp1, bars_to_tp2,
                         bars_examined):
        import time as _t
        self.conn.execute(
            """INSERT OR REPLACE INTO plan_outcome
               (signal_id, stop_hit, tp1_hit, tp2_hit, bars_to_stop,
                bars_to_tp1, bars_to_tp2, bars_examined, resolved_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (signal_id, 1 if stop_hit else 0, 1 if tp1_hit else 0,
             1 if tp2_hit else 0, bars_to_stop, bars_to_tp1, bars_to_tp2,
             bars_examined, int(_t.time())))
        self.conn.commit()

    def plan_stats(self, hours=None):
        """Plan-level touch rates for a signal-time cohort.

        Two cohorts, because a young plan with untouched levels is pending,
        not a miss: "all rows" is the to-date touch rate (moves as bars
        arrive); "terminal" (stop touched OR TP2 touched — the trade is
        over) is the finished-trade rate. `hours=None` means all history;
        otherwise the cohort is signals logged in the last `hours`. Each row
        is a scan signal, so a pair repeated across scans is counted again.
        """
        since = time.time() - hours * 3600 if hours is not None else None
        clause = " WHERE s.flagged=1"
        params = ()
        if since is not None:
            clause += " AND s.ts>?"
            params = (since,)
        rows = self.conn.execute(
            "SELECT o.stop_hit, o.tp1_hit, o.tp2_hit FROM plan_outcome o"
            " JOIN signal_log s ON s.id=o.signal_id" + clause,
            params).fetchall()
        plans_logged = self.conn.execute(
            "SELECT COUNT(*) FROM plan_log p JOIN signal_log s "
            "ON s.id=p.signal_id" + clause, params).fetchone()[0]
        signal_count, coin_count = self.conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT s.coin) FROM signal_log s" + clause,
            params).fetchone()
        n = len(rows)
        base = {"window_hours": hours, "signals": signal_count,
                "coins": coin_count, "plans_logged": plans_logged,
                "planned": 0, "measured": 0,
                "stop_hit": 0, "tp1_hit": 0, "tp2_hit": 0,
                "stop_pct": 0.0, "tp1_pct": 0.0, "tp2_pct": 0.0,
                "terminal": 0, "terminal_pct": 0.0,
                "t_stop_hit": 0, "t_tp1_hit": 0, "t_tp2_hit": 0,
                "t_stop_pct": 0.0, "t_tp1_pct": 0.0, "t_tp2_pct": 0.0}
        if not n:
            return base
        sh = sum(r[0] for r in rows)
        t1 = sum(r[1] for r in rows)
        t2 = sum(r[2] for r in rows)
        term = [r for r in rows if r[0] or r[2]]
        nt = len(term)
        tsh = sum(r[0] for r in term)
        tt1 = sum(r[1] for r in term)
        tt2 = sum(r[2] for r in term)
        out = {**base, "planned": n, "measured": n,
               "stop_hit": sh, "tp1_hit": t1, "tp2_hit": t2,
               "stop_pct": 100.0 * sh / n, "tp1_pct": 100.0 * t1 / n,
               "tp2_pct": 100.0 * t2 / n, "terminal": nt,
               "terminal_pct": 100.0 * nt / n,
               "t_stop_hit": tsh, "t_tp1_hit": tt1, "t_tp2_hit": tt2,
               "t_stop_pct": 100.0 * tsh / nt if nt else 0.0,
               "t_tp1_pct": 100.0 * tt1 / nt if nt else 0.0,
               "t_tp2_pct": 100.0 * tt2 / nt if nt else 0.0}
        return out

    def upsert_current(self, sc, flagged, signal_id, tier=2):
        """Refresh this coin's board row (insert or update, never duplicate).

        Called once per scored card per scan, right after log_signal. The
        journal keeps every observation; coin_state keeps exactly one row
        per coin: latest fields win, first_seen is sticky, scans_seen counts
        observations. Returns the coin.
        """
        import time as _t
        now = int(_t.time())
        self.conn.execute(
            """INSERT INTO coin_state
               (coin, venue, flagged, score, lean, direction, earlyness,
                mag_vol, mag_book, mag_oi, lean_vol, lean_book, lean_oi,
                price, change_24h_pct, quote_vol_24h, spread_pct,
                funding_rate, oi_change_pct, oi_notional, min_notional,
                veto_codes, tradeable, tier, signal_id, first_seen,
                last_seen, scans_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
               ON CONFLICT(coin) DO UPDATE SET
                venue=excluded.venue, flagged=excluded.flagged,
                score=excluded.score, lean=excluded.lean,
                direction=excluded.direction, earlyness=excluded.earlyness,
                mag_vol=excluded.mag_vol, mag_book=excluded.mag_book,
                mag_oi=excluded.mag_oi, lean_vol=excluded.lean_vol,
                lean_book=excluded.lean_book, lean_oi=excluded.lean_oi,
                price=excluded.price, change_24h_pct=excluded.change_24h_pct,
                quote_vol_24h=excluded.quote_vol_24h,
                spread_pct=excluded.spread_pct,
                funding_rate=excluded.funding_rate,
                oi_change_pct=excluded.oi_change_pct,
                oi_notional=excluded.oi_notional,
                min_notional=excluded.min_notional,
                veto_codes=excluded.veto_codes, tradeable=excluded.tradeable,
                tier=excluded.tier, signal_id=excluded.signal_id,
                first_seen=min(first_seen, excluded.first_seen),
                last_seen=excluded.last_seen,
                scans_seen=scans_seen+1""",
            (sc.coin, sc.venue, 1 if flagged else 0,
             sc.score, sc.lean, sc.direction, sc.earlyness,
             sc.magnitude_parts.get("VOL"), sc.magnitude_parts.get("BOOK"),
             sc.magnitude_parts.get("OI"),
             sc.lean_parts.get("VOL"), sc.lean_parts.get("BOOK"),
             sc.lean_parts.get("OI"),
             sc.price, sc.change_24h_pct, sc.quote_vol_24h, sc.spread_pct,
             sc.funding_rate, sc.oi_change_pct, sc.oi_notional,
             sc.min_notional,
             ",".join(v.code for v in sc.vetoes),
             1 if sc.tradeable else 0, tier, signal_id, now, now))
        self.conn.commit()
        return sc.coin

    def current_rows(self):
        """Whole board, score first: one dict per coin (same shape as
        latest_rows, plus first_seen/scans_seen). Launch reads this."""
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(coin_state)")]
        if "coin" not in cols:
            return []
        return [
            dict(zip(["coin", "venue", "flagged", "score", "lean",
                      "direction", "earlyness", "mag_vol", "mag_book",
                      "mag_oi", "lean_vol", "lean_book", "lean_oi", "price",
                      "change_24h_pct", "quote_vol_24h", "spread_pct",
                      "funding_rate", "min_notional", "veto_codes",
                      "tradeable", "tier", "ts",
                      "oi_change_pct", "oi_notional",
                      "signal_id", "first_seen", "scans_seen"], r))
            for r in self.conn.execute(
                """SELECT coin, venue, flagged, score, lean, direction,
                          earlyness, mag_vol, mag_book, mag_oi, lean_vol,
                          lean_book, lean_oi, price, change_24h_pct,
                          quote_vol_24h, spread_pct, funding_rate,
                          min_notional, veto_codes, tradeable, tier,
                          last_seen, oi_change_pct, oi_notional,
                          signal_id, first_seen, scans_seen
                   FROM coin_state ORDER BY score DESC""")]

    def count(self, flagged=None):
        if flagged is None:
            return self.conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM signal_log WHERE flagged=?", (1 if flagged else 0,)
        ).fetchone()[0]

    def set_meta(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)",
                          (key, str(value)))
        self.conn.commit()

    def get_meta(self, key, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def distinct_coins(self):
        return self.conn.execute(
            "SELECT COUNT(DISTINCT coin) FROM signal_log").fetchone()[0]

    def stats(self):
        c = self.conn
        total = c.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0]
        flagged = c.execute("SELECT COUNT(*) FROM signal_log WHERE flagged=1").fetchone()[0]
        coins = c.execute("SELECT COUNT(DISTINCT coin) FROM signal_log").fetchone()[0]
        longs = c.execute("SELECT COUNT(*) FROM signal_log WHERE direction='LONG'").fetchone()[0]
        shorts = c.execute("SELECT COUNT(*) FROM signal_log WHERE direction='SHORT'").fetchone()[0]
        outcomes = c.execute("SELECT COUNT(*) FROM outcome_log").fetchone()[0]
        return {"rows": total, "flagged": flagged, "coins": coins,
                "longs": longs, "shorts": shorts, "outcomes": outcomes}

    def log_outcome(self, signal_id, horizon, return_pct, max_fav, max_adv):
        import time as _t
        self.conn.execute(
            "INSERT INTO outcome_log (signal_id,horizon,return_pct,max_fav,max_adv,resolved_at)"
            " VALUES (?,?,?,?,?,?)",
            (signal_id, horizon, return_pct, max_fav, max_adv, int(_t.time())))
        self.conn.commit()

    def has_outcome(self, signal_id, horizon):
        row = self.conn.execute(
            "SELECT 1 FROM outcome_log WHERE signal_id=? AND horizon=?",
            (signal_id, horizon)).fetchone()
        return bool(row)

    def pending_outcomes(self, limit=None):
        """Unresolved-signal candidates, OLDEST FIRST.

        Ordering is part of the contract: the resolver visits rows once per
        pass, so DESC order starved the oldest signals forever once the log
        outgrew the cap. Default `limit=None` pages through every row - no
        silent truncation. `limit` stays for callers that want to bound a
        single pass (it now bounds the OLDEST rows, matching the order).
        """
        sql = "SELECT id,coin,price,direction,ts FROM signal_log ORDER BY id ASC"
        if limit is None:
            return list(self.conn.execute(sql).fetchall())
        return list(self.conn.execute(sql + " LIMIT ?", (limit,)).fetchall())

    def close(self):
        self.conn.close()
