"""SQLite store for the quant module (its own file; the signals journal is
never touched).  Append-mostly: events are immutable once written, outcomes
are written once, counters are aggregated."""
import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS quant_event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id     TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    coin        TEXT NOT NULL,
    side        INTEGER NOT NULL,            -- +1 long / -1 short (the trade side)
    bar_ts      INTEGER NOT NULL,            -- open ts of the signal bar
    mode        TEXT NOT NULL,               -- SHADOW | ACTIONABLE
    role        TEXT NOT NULL,               -- candidate | control
    state       TEXT NOT NULL,               -- EMITTED | RESOLVED
    ref_price   REAL NOT NULL,               -- close of the signal bar
    stop        REAL NOT NULL,
    tp          REAL NOT NULL,
    stop_frac   REAL NOT NULL,
    horizon     INTEGER NOT NULL,            -- bars from entry
    r_tp        REAL NOT NULL,
    cost_frac   REAL NOT NULL,               -- round-trip friction assumed
    sigma       REAL NOT NULL,
    qv24        REAL NOT NULL,
    features    TEXT NOT NULL,               -- json
    created_ts  INTEGER NOT NULL,
    UNIQUE(rule_id, coin, bar_ts)
);
CREATE INDEX IF NOT EXISTS idx_qe_state ON quant_event(state);
CREATE TABLE IF NOT EXISTS quant_outcome (
    event_id    INTEGER PRIMARY KEY REFERENCES quant_event(id),
    kind        TEXT NOT NULL,               -- TP | SL | TIME
    entry_price REAL NOT NULL,
    exit_ts     INTEGER NOT NULL,
    exit_offset INTEGER NOT NULL,            -- bars after the entry bar
    gross_log   REAL NOT NULL,
    net_r       REAL NOT NULL,
    resolved_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS quant_suppressed (
    day TEXT NOT NULL, rule_id TEXT NOT NULL, reason TEXT NOT NULL,
    n INTEGER NOT NULL, PRIMARY KEY (day, rule_id, reason)
);
CREATE TABLE IF NOT EXISTS quant_watermark (
    coin TEXT PRIMARY KEY, last_bar_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS quant_meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_ts INTEGER NOT NULL
);
"""


class QuantStore:
    def __init__(self, path="data/quant.db"):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # --- events --------------------------------------------------------
    def insert_event(self, e):
        """Insert; returns id, or None if (rule, coin, bar) already exists."""
        try:
            cur = self.conn.execute(
                """INSERT INTO quant_event (rule_id, config_hash, coin, side,
                   bar_ts, mode, role, state, ref_price, stop, tp, stop_frac,
                   horizon, r_tp, cost_frac, sigma, qv24, features, created_ts)
                   VALUES (?,?,?,?,?,?,?,'EMITTED',?,?,?,?,?,?,?,?,?,?,?)""",
                (e["rule_id"], e["config_hash"], e["coin"], e["side"],
                 e["bar_ts"], e["mode"], e["role"], e["ref_price"], e["stop"],
                 e["tp"], e["stop_frac"], e["horizon"], e["r_tp"],
                 e["cost_frac"], e["sigma"], e["qv24"],
                 json.dumps(e.get("features", {}), sort_keys=True),
                 int(time.time())))
        except sqlite3.IntegrityError:
            return None
        self.conn.commit()
        return cur.lastrowid

    def open_events(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM quant_event WHERE state='EMITTED' ORDER BY bar_ts")]

    def resolve(self, event_id, kind, entry_price, exit_ts, exit_offset,
                gross_log, net_r):
        self.conn.execute(
            """INSERT OR IGNORE INTO quant_outcome (event_id, kind, entry_price,
               exit_ts, exit_offset, gross_log, net_r, resolved_ts)
               VALUES (?,?,?,?,?,?,?,?)""",
            (event_id, kind, entry_price, exit_ts, exit_offset, gross_log,
             net_r, int(time.time())))
        self.conn.execute("UPDATE quant_event SET state='RESOLVED' WHERE id=?",
                          (event_id,))
        self.conn.commit()

    def last_event_ts(self, rule_id, coin):
        r = self.conn.execute(
            "SELECT MAX(bar_ts) FROM quant_event WHERE rule_id=? AND coin=?",
            (rule_id, coin)).fetchone()
        return r[0]

    def count_events_day(self, rule_id, day_start, day_end, coin=None):
        q = ("SELECT COUNT(*) FROM quant_event WHERE rule_id=? AND bar_ts>=? "
             "AND bar_ts<?")
        a = [rule_id, day_start, day_end]
        if coin:
            q += " AND coin=?"
            a.append(coin)
        return self.conn.execute(q, a).fetchone()[0]

    # --- aggregates / state -------------------------------------------
    def bump_suppressed(self, day, rule_id, reason, n=1):
        self.conn.execute(
            """INSERT INTO quant_suppressed (day, rule_id, reason, n)
               VALUES (?,?,?,?) ON CONFLICT(day, rule_id, reason)
               DO UPDATE SET n = n + excluded.n""", (day, rule_id, reason, n))

    def set_watermark(self, coin, ts):
        self.conn.execute(
            "INSERT INTO quant_watermark (coin,last_bar_ts) VALUES (?,?) "
            "ON CONFLICT(coin) DO UPDATE SET last_bar_ts=excluded.last_bar_ts",
            (coin, ts))

    def watermarks(self):
        return {r["coin"]: r["last_bar_ts"] for r in
                self.conn.execute("SELECT coin,last_bar_ts FROM quant_watermark")}

    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT INTO quant_meta (key,value,updated_ts) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_ts=excluded.updated_ts", (key, str(value), int(time.time())))
        self.conn.commit()

    def get_meta(self, key, default=None):
        r = self.conn.execute("SELECT value FROM quant_meta WHERE key=?",
                              (key,)).fetchone()
        return r[0] if r else default

    def commit(self):
        self.conn.commit()

    def resolved_rows(self, rule_id=None):
        q = ("SELECT e.*, o.kind, o.net_r, o.exit_offset, o.gross_log "
             "FROM quant_event e JOIN quant_outcome o ON o.event_id=e.id")
        a = []
        if rule_id:
            q += " WHERE e.rule_id=?"
            a.append(rule_id)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY e.bar_ts", a)]

    def all_events(self, rule_id=None):
        q = "SELECT * FROM quant_event"
        a = []
        if rule_id:
            q += " WHERE rule_id=?"
            a.append(rule_id)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY bar_ts", a)]
