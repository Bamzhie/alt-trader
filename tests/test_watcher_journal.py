"""Task 2: capped event journal + dashboard snapshot pairs.

Offline only: no network. Pins the Task 2 interface contract:

- ensure_journal(conn) creates watcher_events(ts, kind, coin, text) with
  EXACTLY those 4 columns (no tier column) plus dashboard_snapshot(...).
- log_event(conn, kind, coin, text) appends; beyond JOURNAL_CAP=200 rows
  the oldest rows are pruned.
- write_snapshot(conn, ...) appends a dashboard_snapshot row.
- snapshot_pair(conn, now) -> (latest, prior_or_None); prior is the
  nearest snapshot at or before now-24h, else None (caller shows
  "comparison unavailable" rather than inferring a value).

Run: python3 tests/test_watcher_journal.py
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def fresh_conn():
    return sqlite3.connect(":memory:")


# --------------------------------------------------------------------------
print("=== journal cap prunes oldest ===")
from proto.watcher_journal import (  # noqa: E402
    JOURNAL_CAP,
    ensure_journal,
    log_event,
    snapshot_pair,
    write_snapshot,
)

check("JOURNAL_CAP is 200", JOURNAL_CAP == 200, str(JOURNAL_CAP))

conn = fresh_conn()
ensure_journal(conn)
for i in range(205):
    log_event(conn, "emission", "C", f"e{i}")
count = conn.execute("SELECT COUNT(*) FROM watcher_events").fetchone()[0]
oldest = conn.execute(
    "SELECT text FROM watcher_events ORDER BY rowid ASC LIMIT 1").fetchone()[0]
check("cap holds at 200 rows", count == 200, f"count={count}")
check("oldest pruned first (e5 survives)", oldest == "e5",
      f"oldest={oldest!r}")
conn.close()

# --------------------------------------------------------------------------
print("\n=== journal schema is exactly (ts, kind, coin, text) ===")
conn = fresh_conn()
ensure_journal(conn)
cols = [r[1] for r in conn.execute("PRAGMA table_info(watcher_events)")]
check("watcher_events has exactly ts/kind/coin/text",
      cols == ["ts", "kind", "coin", "text"], str(cols))
check("no tier column on watcher_events", "tier" not in cols, str(cols))
conn.close()

# --------------------------------------------------------------------------
print("\n=== invalid kinds rejected ===")
conn = fresh_conn()
ensure_journal(conn)
rejected = False
try:
    log_event(conn, "resolved", "C", "bogus kind")
except ValueError:
    rejected = True
check("unknown kind raises ValueError", rejected)
check("rejected write lands no row",
      conn.execute("SELECT COUNT(*) FROM watcher_events").fetchone()[0] == 0)
for kind in ("emission", "invalidation", "expiry", "sweep-complete"):
    log_event(conn, kind, "C", f"{kind} ok")
check("all four spec kinds accepted",
      conn.execute("SELECT COUNT(*) FROM watcher_events").fetchone()[0] == 4)
conn.close()

# --------------------------------------------------------------------------
print("\n=== snapshot pair without history ===")
fresh = fresh_conn()
ensure_journal(fresh)
latest, prior = snapshot_pair(fresh, 9999)
check("latest is None on a fresh db", latest is None, str(latest))
check("prior is None without history (caller shows unavailable)",
      prior is None, str(prior))
fresh.close()

# --------------------------------------------------------------------------
print("\n=== snapshot pair with 24h history ===")
conn = fresh_conn()
ensure_journal(conn)
write_snapshot(conn, 100, 90, 5, 2, 0.8)  # ts assigned internally
snap0 = conn.execute(
    "SELECT ts FROM dashboard_snapshot ORDER BY rowid DESC LIMIT 1").fetchone()[0]
# Backdate that row to >24h ago, then write a fresh one.
conn.execute("UPDATE dashboard_snapshot SET ts=? WHERE ts=?",
             (snap0 - 90000, snap0))
conn.commit()
write_snapshot(conn, 110, 100, 7, 3, 0.9)
latest, prior = snapshot_pair(conn, snap0 - 90000 + 86400 + 10)
check("latest returned", latest is not None
      and latest["active_setups"] == 7, str(latest))
check("prior is the snapshot at or before now-24h",
      prior is not None and prior["active_setups"] == 5, str(prior))
conn.close()

# --------------------------------------------------------------------------
print("\n=== recent-only history yields no prior ===")
conn = fresh_conn()
ensure_journal(conn)
write_snapshot(conn, 110, 100, 7, 3, 0.9)
latest, prior = snapshot_pair(conn, 10 ** 9)
check("latest returned", latest is not None, str(latest))
check("prior None when no snapshot covers now-24h", prior is None,
      str(prior))
conn.close()

# --------------------------------------------------------------------------
print("\n=== watcher lifecycle writes journal transitions ===")
from proto import watcher as W  # noqa: E402

conn = fresh_conn()
W.ensure_watcher_tables(conn)
ensure_journal(conn)
setup = {
    "rule_id": W.T1_RULE_ID, "coin": "JRN", "src_bar_ts": 1000,
    "src": "t1_break", "direction": "LONG", "entry": 100.0,
    "b_stop": 0.02, "a_tgt": 0.04, "N_bars": 2,
}
sid = W.emit_setup(conn, setup)
kinds = [r[0] for r in conn.execute(
    "SELECT kind FROM watcher_events ORDER BY rowid")]
check("emission logged on emit_setup", "emission" in kinds, str(kinds))
bar = {"ts": 2000, "o": 100.0, "h": 101.0, "l": 99.5, "c": 100.5}
W.eval_bar(conn, sid, bar, {"now": 99999, "invalidate": True,
                           "invalid_price": 100.5})
kinds = [r[0] for r in conn.execute(
    "SELECT kind FROM watcher_events ORDER BY rowid")]
check("invalidation logged on invalidate transition",
      "invalidation" in kinds, str(kinds))
setup2 = dict(setup, coin="JRN2", src_bar_ts=3000)
sid2 = W.emit_setup(conn, setup2)
W.eval_bar(conn, sid2, {"ts": 9000, "o": 100.0, "h": 101.0,
                       "l": 99.5, "c": 100.5},
           {"now": 99999, "age_bars": 5})
kinds = [r[0] for r in conn.execute(
    "SELECT kind FROM watcher_events ORDER BY rowid")]
check("expiry logged on time-expiry transition", "expiry" in kinds,
      str(kinds))
conn.close()

print("\n" + ("ALL PASS" if not FAILURES
              else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
