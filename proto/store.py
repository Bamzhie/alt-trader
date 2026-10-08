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
    flag_version    INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_signal_ts_coin ON signal_log(ts, coin);
CREATE INDEX IF NOT EXISTS idx_signal_flagged ON signal_log(flagged);

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
                flag_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,2)""",
            (int(time.time()), sc.coin, sc.venue, 1 if flagged else 0,
             sc.score, sc.lean, sc.direction, sc.earlyness,
             sc.magnitude_parts.get("VOL"), sc.magnitude_parts.get("BOOK"),
             sc.magnitude_parts.get("OI"),
             sc.lean_parts.get("VOL"), sc.lean_parts.get("BOOK"),
             sc.lean_parts.get("OI"),
             sc.price, sc.change_24h_pct, sc.quote_vol_24h, sc.spread_pct,
             sc.funding_rate, sc.min_notional,
             ",".join(v.code for v in sc.vetoes),
             1 if sc.tradeable else 0, tier))
        self.conn.commit()
        return cur.lastrowid

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

    def pending_outcomes(self, limit=500):
        return list(self.conn.execute(
            "SELECT id,coin,price,direction,ts FROM signal_log ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall())

    def close(self):
        self.conn.close()