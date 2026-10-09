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
    oi_change_pct   REAL,
    -- Episode metadata (nullable for legacy rows)
    obs_ts          INTEGER,          -- wall-clock time when result known
    data_quality    TEXT,             -- "OK" or "DEGRADED"
    degraded_reasons TEXT,             -- optional degradation reason codes
    last_bar_ts     INTEGER,          -- most recent bar timestamp seen
    ticker_ts       INTEGER,          -- ticker snapshot timestamp
    stake           REAL,             -- operator stake at signal time
    log_threshold   REAL,             -- threshold at signal time
    leverage_cap    INTEGER,          -- leverage cap at signal time
    config_hash     TEXT,             -- config hash for epoch/cohort
    code_rev        TEXT              -- scorer/planner code version
);
CREATE INDEX IF NOT EXISTS idx_signal_ts_coin ON signal_log(ts, coin);
-- idx_signal_config is created in _migrate() once signal_log.config_hash
-- is guaranteed to exist: an unguarded index here breaks a legacy DB whose
-- signal_log predates the column.

-- Episode tables (additive, nullable legacy columns)
CREATE TABLE IF NOT EXISTS signal_episode (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coin            TEXT NOT NULL,
    venue           TEXT NOT NULL DEFAULT 'MEXC',
    direction       TEXT NOT NULL,         -- LONG or SHORT
    first_signal_id INTEGER NOT NULL REFERENCES signal_log(id),
    start_ts        INTEGER NOT NULL,      -- obs_ts of first qualifying observation
    end_ts          INTEGER,               -- closed timestamp
    state           TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN, REARMING, CLOSED
    close_reason    TEXT,                  -- REARM_CONFIRMED, REVERSAL, COVERAGE_LOST
    after_gap       INTEGER DEFAULT 0,     -- 1 if started after gap
    last_valid_ts   INTEGER,               -- last QUALIFYING/NON_QUALIFYING obs
    last_qualifying_ts INTEGER,            -- last QUALIFYING obs
    rearm_start_ts  INTEGER,               -- when re-arming began
    close_ts        INTEGER,               -- when the episode closed
    plan_status     TEXT DEFAULT 'PLANNED', -- PLANNED or NO_PLAN
    no_plan_reason  TEXT,                  -- reason for NO_PLAN
    -- Observation counts
    qualifying_obs  INTEGER DEFAULT 0,
    non_qualifying_obs INTEGER DEFAULT 0,
    unknown_obs     INTEGER DEFAULT 0,
    late_obs        INTEGER DEFAULT 0,
    -- Version and config
    config_hash     TEXT,
    config_json     TEXT,
    flag_rule_version    INTEGER,
    plan_rule_version    TEXT,
    episode_rule_version TEXT,
    outcome_rule_version TEXT,
    cost_model_version   TEXT
);
CREATE INDEX IF NOT EXISTS idx_episode_coin_state ON signal_episode(coin, state);
CREATE INDEX IF NOT EXISTS idx_episode_config_state ON signal_episode(config_hash, state);
-- Partial unique index for open/rearming episodes only
CREATE UNIQUE INDEX IF NOT EXISTS idx_episode_open_unique 
    ON signal_episode(coin, config_hash) WHERE state IN ('OPEN', 'REARMING');

CREATE TABLE IF NOT EXISTS episode_observation (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id      INTEGER REFERENCES signal_episode(id),
    signal_id       INTEGER REFERENCES signal_log(id),
    coin            TEXT NOT NULL,
    config_hash     TEXT NOT NULL,
    obs_ts          INTEGER NOT NULL,
    obs_class       INTEGER NOT NULL,    -- ObservationClass enum
    late            INTEGER DEFAULT 0,
    degraded_reasons TEXT,
    attempt_id      TEXT,
    coverage        REAL
);
CREATE INDEX IF NOT EXISTS idx_ep_obs_ts_coin ON episode_observation(obs_ts, coin);
CREATE INDEX IF NOT EXISTS idx_ep_obs_signal ON episode_observation(signal_id);

CREATE TABLE IF NOT EXISTS episode_cursor (
    coin            TEXT NOT NULL,
    config_hash     TEXT NOT NULL,
    last_processed_obs_ts INTEGER NOT NULL,
    PRIMARY KEY (coin, config_hash)
);

-- Migration table to track episode epoch
CREATE TABLE IF NOT EXISTS episode_meta (
    key   TEXT PRIMARY KEY,
    value INTEGER   -- epoch_ts
);

-- Frozen trade plan attached to a PLANNED episode (spec 5.4).
-- One immutable row per episode: later scans never supply a plan, and a
-- re-resolver pass must never rewrite the levels it judges.
CREATE TABLE IF NOT EXISTS episode_plan (
    episode_id      INTEGER PRIMARY KEY REFERENCES signal_episode(id),
    signal_id       INTEGER REFERENCES signal_log(id),
    direction       TEXT    NOT NULL,        -- LONG or SHORT
    entry_low       REAL    NOT NULL,
    entry_high      REAL    NOT NULL,
    stop            REAL    NOT NULL,
    tp1             REAL    NOT NULL,
    tp2             REAL    NOT NULL,
    leverage        INTEGER,
    notional        REAL,
    max_loss        REAL,
    -- Fill-based R multiples for TP1/TP2 (spec 8.3): the reward per unit
    -- of risk the plan implied, frozen with the plan so a report can quote
    -- it without recomputing from levels.
    r_multiple_tp1  REAL,
    r_multiple_tp2  REAL,
    warnings        TEXT,
    frozen_at       INTEGER
);

-- Forward resolution of one planned episode from closed 5m bars (spec 8).
-- Terminal statuses are immutable; unresolved work stays pending and is
-- never zero-filled.
CREATE TABLE IF NOT EXISTS episode_outcome (
    episode_id       INTEGER PRIMARY KEY REFERENCES signal_episode(id),
    -- PENDING_ENTRY | FILLED | UNFILLED | UNAVAILABLE
    entry_status     TEXT    NOT NULL DEFAULT 'PENDING_ENTRY',
    fill_bar_open_ts INTEGER,               -- open_ts of the bar that filled
    fill_price       REAL,                  -- modeled fill (adverse edge)
    fill_ts          INTEGER,               -- close of the fill bar
    -- OPEN | STOPPED | TP2 | EXPIRED | UNAVAILABLE
    -- Nullable: an UNFILLED/UNAVAILABLE entry has no trade to describe, so
    -- reporting records a NULL trade_status rather than a fake state.
    trade_status     TEXT    DEFAULT 'OPEN',
    stop_index       INTEGER,               -- bar index from the fill bar
    tp1_index        INTEGER,
    tp2_index        INTEGER,
    -- Reporting-module column names (spec 11): the SAME bar indices the
    -- resolver writes above, aliased so episode_report.py reads them by
    -- its own name. The resolver keeps both in sync on every write; they
    -- are never written independently.
    stop_bar_idx     INTEGER,
    tp1_bar_idx      INTEGER,
    tp2_bar_idx      INTEGER,
    tp1_before_stop  INTEGER DEFAULT 0,
    exit_ts          INTEGER,
    exit_price       REAL,
    mfe_pct          REAL,                  -- best excursion from the fill
    mae_pct          REAL,                  -- worst excursion from the fill
    resolved_through_ts INTEGER,            -- bar-close watermark of this pass
    resolved_at      INTEGER
);

-- Fixed-horizon descriptive returns from the modeled fill (spec 9).
-- Ignores stops and targets; independent of the plan's hold.
CREATE TABLE IF NOT EXISTS episode_horizon (
    episode_id      INTEGER REFERENCES signal_episode(id),
    horizon         TEXT    NOT NULL,       -- 1h | 4h | 24h | 7d
    status          TEXT    NOT NULL,       -- PENDING | MATURED | UNAVAILABLE
    return_pct      REAL,
    mfe_pct         REAL,
    mae_pct         REAL,
    PRIMARY KEY (episode_id, horizon)
);

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
        if "obs_ts" not in cols:
            self.conn.execute(
                "ALTER TABLE signal_log ADD COLUMN obs_ts INTEGER")
        if "data_quality" not in cols:
            self.conn.execute(
                "ALTER TABLE signal_log ADD COLUMN data_quality TEXT")
        if "degraded_reasons" not in cols:
            self.conn.execute(
                "ALTER TABLE signal_log ADD COLUMN degraded_reasons TEXT")
        if "last_bar_ts" not in cols:
            self.conn.execute(
                "ALTER TABLE signal_log ADD COLUMN last_bar_ts INTEGER")
        if "ticker_ts" not in cols:
            self.conn.execute(
                "ALTER TABLE signal_log ADD COLUMN ticker_ts INTEGER")
        if "stake" not in cols:
            self.conn.execute(
                "ALTER TABLE signal_log ADD COLUMN stake REAL")
        if "log_threshold" not in cols:
            self.conn.execute(
                "ALTER TABLE signal_log ADD COLUMN log_threshold REAL")
        if "leverage_cap" not in cols:
            self.conn.execute(
                "ALTER TABLE signal_log ADD COLUMN leverage_cap INTEGER")
        if "config_hash" not in cols:
            self.conn.execute(
                "ALTER TABLE signal_log ADD COLUMN config_hash TEXT")
        if "code_rev" not in cols:
            self.conn.execute(
                "ALTER TABLE signal_log ADD COLUMN code_rev TEXT")
        if "config_hash" in cols:
            # Safe now: the column exists on both fresh and migrated DBs.
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_signal_config"
                " ON signal_log(config_hash)")
        ep_cols = {r[1] for r in
                   self.conn.execute("PRAGMA table_info(signal_episode)")}
        for new_col in ("last_valid_ts", "last_qualifying_ts", "rearm_start_ts",
                        "close_ts"):
            if new_col not in ep_cols:
                self.conn.execute(
                    f"ALTER TABLE signal_episode ADD COLUMN {new_col} INTEGER")
        if "last_obs_ts" in ep_cols:
            # One-time backfill from the pre-REARMING schema: last_obs_ts was
            # advanced only by QUALIFYING/NON_QUALIFYING, which is exactly
            # last_valid_ts.
            self.conn.execute(
                "UPDATE signal_episode SET last_valid_ts = COALESCE("
                "last_valid_ts, last_obs_ts),"
                " last_qualifying_ts = COALESCE(last_qualifying_ts,"
                " last_obs_ts) WHERE last_obs_ts IS NOT NULL")
        # Indexes on episode_plan/episode_outcome columns are created here,
        # guarded on column presence: the SCHEMA string runs before any
        # migration, so an index there would break a legacy DB whose table
        # predates the column (the config_hash regression).
        self._migrate_episode_indexes()
        self._backfill_coin_state()

    def _migrate_episode_indexes(self):
        """Guarded indexes on episode_plan / episode_outcome (spec 11).

        Guarded on column presence so a DB created by an older schema (no
        signal_id, no r_multiple_*, no *_bar_idx) never hits
        "no such column" while opening.
        """
        plan_cols = {r[1] for r in
                     self.conn.execute("PRAGMA table_info(episode_plan)")}
        if "signal_id" in plan_cols:
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ep_plan_signal"
                " ON episode_plan(signal_id)")
        out_cols = {r[1] for r in
                    self.conn.execute("PRAGMA table_info(episode_outcome)")}
        for idx_col in ("trade_status", "entry_status"):
            if idx_col in out_cols:
                self.conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_ep_outcome_{idx_col}"
                    f" ON episode_outcome({idx_col})")
        hor_cols = {r[1] for r in
                    self.conn.execute("PRAGMA table_info(episode_horizon)")}
        if "status" in hor_cols:
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ep_horizon_status"
                " ON episode_horizon(status)")

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

    # ------------------------------------------------------------------
    # Episode measurement (Tasks 1-2). Additive: legacy APIs untouched.
    # ------------------------------------------------------------------

    def record_measurement_observation(self, signal_id, *, obs_ts,
                                       data_quality, degraded_reasons=None,
                                       last_bar_ts=None, ticker_ts=None,
                                       stake=None, log_threshold=None,
                                       leverage_cap=None, config_hash=None,
                                       code_rev=None):
        """Attach measurement metadata to an existing signal_log row.

        Additive: legacy rows keep NULLs. Called once per logged scan card.
        """
        self.conn.execute(
            """UPDATE signal_log SET obs_ts=?, data_quality=?, degraded_reasons=?,
                   last_bar_ts=?, ticker_ts=?, stake=?, log_threshold=?,
                   leverage_cap=?, config_hash=?, code_rev=?
               WHERE id=?""",
            (int(obs_ts), data_quality, degraded_reasons,
             int(last_bar_ts) if last_bar_ts is not None else None,
             int(ticker_ts) if ticker_ts is not None else None,
             float(stake) if stake is not None else None,
             float(log_threshold) if log_threshold is not None else None,
             int(leverage_cap) if leverage_cap is not None else None,
             config_hash, code_rev, int(signal_id)))
        self.conn.commit()

    def log_measurement_attempt(self, coin, config_hash, obs_ts,
                                observation_class, *, signal_id=None,
                                degraded_reasons=None, coverage=None,
                                attempt_id=None):
        """Record one measurement attempt (QUALIFYING/NON_QUALIFYING/UNKNOWN).

        Distinct from the episode lifecycle write: an attempt is a fact about
        the fetch, so it is journaled even when no episode exists. Returns the
        inserted row id.
        """
        cur = self.conn.execute(
            """INSERT INTO episode_observation
                   (episode_id, signal_id, coin, config_hash, obs_ts,
                    obs_class, late, degraded_reasons, attempt_id, coverage)
               VALUES (?,?,?,?,?,?,0,?,?,?)""",
            (None, signal_id, coin, config_hash, int(obs_ts),
             int(observation_class), degraded_reasons, attempt_id, coverage))
        self.conn.commit()
        return cur.lastrowid

    def insert_episode(self, **kw):
        """Insert a new episode row; returns its id. Caller manages the txn."""
        cols = ", ".join(kw.keys())
        marks = ", ".join("?" for _ in kw)
        cur = self.conn.execute(
            f"INSERT INTO signal_episode ({cols}) VALUES ({marks})",
            tuple(kw.values()))
        return cur.lastrowid

    def update_episode(self, episode_id, **kw):
        """Update episode fields by id. Caller manages the txn."""
        sets = ", ".join(f"{k}=?" for k in kw)
        self.conn.execute(
            f"UPDATE signal_episode SET {sets} WHERE id=?",
            tuple(kw.values()) + (episode_id,))

    def get_cursor(self, coin, config_hash):
        row = self.conn.execute(
            "SELECT last_processed_obs_ts FROM episode_cursor"
            " WHERE coin=? AND config_hash=?", (coin, config_hash)).fetchone()
        return row[0] if row else None

    def set_cursor(self, coin, config_hash, ts):
        """Monotonic cursor advance: never rewinds."""
        self.conn.execute(
            """INSERT INTO episode_cursor (coin, config_hash,
                    last_processed_obs_ts) VALUES (?,?,?)
               ON CONFLICT(coin, config_hash) DO UPDATE SET
                 last_processed_obs_ts =
                   MAX(episode_cursor.last_processed_obs_ts,
                       excluded.last_processed_obs_ts)""",
            (coin, config_hash, int(ts)))

    def stale_open_episodes(self, now, max_gap_s):
        """Non-closed episodes whose last valid observation is older than the gap.

        last_valid_ts (advanced only by QUALIFYING/NON_QUALIFYING) is the
        coverage clock, falling back to the cursor, then start_ts. UNKNOWN
        fetches never advance it, so a run of failures cannot hide a gap.
        Returns (id, coin, config_hash, last_touch) tuples.
        """
        return [row for row in self.conn.execute(
            """SELECT e.id, e.coin, e.config_hash,
                      COALESCE(e.last_valid_ts, c.last_processed_obs_ts,
                               e.start_ts) AS last_touch
                 FROM signal_episode e
                 LEFT JOIN episode_cursor c
                   ON c.coin=e.coin AND c.config_hash=e.config_hash
                WHERE e.state != 'CLOSED' AND e.close_ts IS NULL
                ORDER BY e.id""").fetchall()
                if int(now) - (row[3] or 0) > int(max_gap_s)]

    def link_observation(self, attempt_id, episode_id):
        """Bind a previously unlinked attempt to an episode."""
        self.conn.execute(
            "UPDATE episode_observation SET episode_id=? WHERE attempt_id=?",
            (episode_id, attempt_id))
        self.conn.commit()

    @staticmethod
    def _episode_columns(conn):
        cur = conn.execute("SELECT * FROM signal_episode LIMIT 0")
        return [description[0] for description in cur.description]

    def open_episode(self, coin, config_hash):
        """The single non-closed episode row for (coin, config_hash), or None.

        Rows come back as dicts. The invariant enforced by
        idx_episode_open_unique means there is at most one.
        """
        cur = self.conn.execute(
            "SELECT * FROM signal_episode WHERE coin=? AND config_hash=?"
            " AND state != 'CLOSED' ORDER BY id DESC LIMIT 1",
            (coin, config_hash))
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip(self._episode_columns(self.conn), row))

    # ------------------------------------------------------------------
    # Episode plan resolver (Task 4). Additive: the legacy plan/outcome
    # APIs (planned, log_plan, log_plan_outcome, plan_stats,
    # pending_outcomes, log_outcome, has_outcome) are untouched and read
    # only the legacy plan_log/plan_outcome/outcome_log tables.
    # ------------------------------------------------------------------

    # Statuses that are final: once written they are never rewritten, so a
    # later pass with the same (or fewer) bars cannot undo evidence.
    #
    # The ENTRY group splits in two, because entry and trade resolve on
    # different clocks. UNFILLED and UNAVAILABLE end the row outright (there
    # is no trade to run), but FILLED is only final for the FILL: a filled
    # entry with an OPEN trade must keep resolving, so treating FILLED as
    # row-terminal would freeze every trade at OPEN forever.
    _TERMINAL_ENTRY = ("UNFILLED", "UNAVAILABLE")
    _FINAL_FILL = ("FILLED",)
    _TERMINAL_TRADE = ("STOPPED", "TP2", "EXPIRED", "UNAVAILABLE")
    _TERMINAL_HORIZON = ("MATURED", "UNAVAILABLE")

    _OUTCOME_FIELDS = ("entry_status", "fill_bar_open_ts", "fill_price",
                       "fill_ts", "trade_status", "stop_index", "tp1_index",
                       "tp2_index", "stop_bar_idx", "tp1_bar_idx",
                       "tp2_bar_idx", "tp1_before_stop", "exit_ts",
                       "exit_price", "mfe_pct", "mae_pct", "resolved_through_ts")

    # Bar-index fields that exist under two names (resolver: *_index,
    # reporting: *_bar_idx). A write to one name is mirrored to the other so
    # both readers always see the same value.
    _BAR_IDX_ALIASES = {"stop_index": "stop_bar_idx",
                        "stop_bar_idx": "stop_index",
                        "tp1_index": "tp1_bar_idx",
                        "tp1_bar_idx": "tp1_index",
                        "tp2_index": "tp2_bar_idx",
                        "tp2_bar_idx": "tp2_index"}

    def insert_episode_plan(self, episode_id, signal_id=None, direction=None,
                            entry_low=None, entry_high=None, stop=None,
                            tp1=None, tp2=None, leverage=None, notional=None,
                            max_loss=None, warnings=None, frozen_at=None):
        """Freeze the plan attached to a PLANNED episode.

        Immutable by contract (spec 7): an episode has exactly one frozen
        plan, so a second insert for the same episode is ignored rather than
        rewriting the levels every later resolver pass judges.
        """
        import time as _t
        self.conn.execute(
            """INSERT OR IGNORE INTO episode_plan
               (episode_id, signal_id, direction, entry_low, entry_high,
                stop, tp1, tp2, leverage, notional, max_loss, warnings,
                frozen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (int(episode_id), signal_id, direction, entry_low, entry_high,
             stop, tp1, tp2, leverage, notional, max_loss, warnings,
             int(frozen_at) if frozen_at is not None else int(_t.time())))
        self.conn.commit()

    def episode_plan_row(self, episode_id):
        """The frozen plan for one episode as a dict, or None."""
        cur = self.conn.execute("SELECT * FROM episode_plan WHERE episode_id=?",
                                (int(episode_id),))
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip([d[0] for d in cur.description], row))

    def upsert_episode_outcome(self, episode_id, **fields):
        """Write or refine the forward resolution of one planned episode.

        Forward-only (spec 8): a row already in a terminal entry or trade
        state is never rewritten, and a terminal state is never replaced by
        a pending one. Unresolved work (PENDING_ENTRY / OPEN) keeps being
        refined as more closed bars arrive.
        """
        unknown = set(fields) - set(self._OUTCOME_FIELDS)
        if unknown:
            raise ValueError(
                f"unknown episode_outcome fields: {sorted(unknown)}")

        # Mirror bar-index writes to the aliased column so both the resolver
        # (*_index) and the reporting module (*_bar_idx) always read the same
        # first-touch indices.
        for name, alias in self._BAR_IDX_ALIASES.items():
            if name in fields:
                fields[alias] = fields[name]

        cur = self.conn.execute(
            "SELECT entry_status, trade_status FROM episode_outcome"
            " WHERE episode_id=?", (int(episode_id),))
        row = cur.fetchone()

        # Terminal means each group is final on its own terms: a FILLED entry
        # with an OPEN trade is NOT terminal, because the trade still has to
        # resolve. Only a final entry state OR a final trade state freezes
        # the row - a STOPPED/TP2/EXPIRED/UNAVAILABLE trade ends it, and so
        # does a UNFILLED or entry-UNAVAILABLE entry (no trade to run).
        if row is not None and (row[0] in self._TERMINAL_ENTRY
                                or row[1] in self._TERMINAL_TRADE):
            return  # terminal rows are immutable
        if not fields:
            return

        # A FILLED entry is final for the fill itself, so a later pass may
        # not rewrite the fill bar, price or time. Refinements then apply to
        # the trade only.
        if row is not None and row[0] in self._FINAL_FILL:
            for k in ("entry_status", "fill_bar_open_ts", "fill_price",
                      "fill_ts"):
                fields.pop(k, None)
            if not fields:
                return

        if fields.get("entry_status") == "PENDING_ENTRY":
            # No fill yet: no fill bar, price or time may be recorded.
            for k in ("fill_bar_open_ts", "fill_price", "fill_ts"):
                fields.pop(k, None)
        if fields.get("trade_status") == "OPEN":
            for k in ("exit_ts", "exit_price"):
                fields.pop(k, None)

        if row is None:
            fields.setdefault("entry_status", "PENDING_ENTRY")
            fields.setdefault("trade_status", "OPEN")
            cols = ", ".join(fields)
            marks = ", ".join("?" for _ in fields)
            self.conn.execute(
                f"INSERT INTO episode_outcome (episode_id, {cols})"
                f" VALUES (?, {marks})",
                (int(episode_id),) + tuple(fields.values()))
        else:
            # Never write the same state back over itself.
            if fields.get("entry_status") == row[0]:
                fields.pop("entry_status")
            if fields.get("trade_status") == row[1]:
                fields.pop("trade_status")
            if not fields:
                return
            sets = ", ".join(f"{k}=?" for k in fields)
            self.conn.execute(
                f"UPDATE episode_outcome SET {sets} WHERE episode_id=?",
                tuple(fields.values()) + (int(episode_id),))
        self.conn.commit()

    def upsert_episode_horizon(self, episode_id, horizon, status,
                               return_pct=None, mfe_pct=None, mae_pct=None):
        """Write one fixed-horizon row for one episode.

        MATURED and UNAVAILABLE are terminal: a horizon that has matured is
        never downgraded back to PENDING by a later, weaker bar set.
        """
        cur = self.conn.execute(
            "SELECT status FROM episode_horizon WHERE episode_id=?"
            " AND horizon=?", (int(episode_id), horizon))
        row = cur.fetchone()
        if row is not None and row[0] in self._TERMINAL_HORIZON:
            return
        self.conn.execute(
            """INSERT INTO episode_horizon
               (episode_id, horizon, status, return_pct, mfe_pct, mae_pct)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(episode_id, horizon) DO UPDATE SET
                 status=excluded.status, return_pct=excluded.return_pct,
                 mfe_pct=excluded.mfe_pct, mae_pct=excluded.mae_pct""",
            (int(episode_id), horizon, status, return_pct, mfe_pct, mae_pct))
        self.conn.commit()

    def episode_outcome_row(self, episode_id):
        cur = self.conn.execute("SELECT * FROM episode_outcome"
                                " WHERE episode_id=?", (int(episode_id),))
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip([d[0] for d in cur.description], row))

    def planned_episodes(self, limit=None):
        """Episodes with a frozen plan that need forward resolution.

        Only PLANNED episodes: a NO_PLAN episode has no levels to resolve
        and is never backfilled with one. The legacy plan_log/plan_outcome
        tables are never read here, so episode metrics stay separate from
        legacy ones.
        """
        sql = """SELECT e.id AS episode_id, e.coin, e.venue, e.direction,
                        e.start_ts, e.config_hash, e.plan_status,
                        p.signal_id, p.entry_low, p.entry_high, p.stop,
                        p.tp1, p.tp2
                   FROM signal_episode e
                   JOIN episode_plan p ON p.episode_id = e.id
                  WHERE e.plan_status = 'PLANNED'
                  ORDER BY e.id"""
        cur = self.conn.execute(sql)
        cols = [d[0] for d in cur.description]
        out = [dict(zip(cols, r)) for r in cur.fetchall()]
        return out[:limit] if limit is not None else out
