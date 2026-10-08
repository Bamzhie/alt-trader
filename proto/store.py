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
    tier            INTEGER NOT NULL DEFAULT 2
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
        self.conn.commit()

    def log_signal(self, sc, flagged, tier=2):
        """Insert one scorecard. flagged=False writes a shadow row."""
        cur = self.conn.execute(
            """INSERT INTO signal_log
               (ts, coin, venue, flagged, score, lean, direction, earlyness,
                mag_vol, mag_book, mag_oi, lean_vol, lean_book, lean_oi,
                price, change_24h_pct, quote_vol_24h, spread_pct,
                funding_rate, min_notional, veto_codes, tradeable, tier)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
        return {"rows": total, "flagged": flagged, "coins": coins,
                "longs": longs, "shorts": shorts}

    def close(self):
        self.conn.close()