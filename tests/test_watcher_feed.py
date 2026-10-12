"""Read-only watcher feed + pause/disable flags (Task 2).

Self-checking script: `python3 tests/test_watcher_feed.py`.
Covers contained missing-DB failure, empty fresh-DB panel, flags
round-trip via set/get, locked-DB read mapping to WatcherUnavailable,
read-only enforcement, older-DB tolerance (missing trail/be columns),
and latest-eval selection.
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.watcher_feed import (  # noqa: E402
    WatcherUnavailable,
    fetch_latest_evals,
    fetch_setups,
    get_flags,
    load_panel,
    open_ro,
    set_paused,
    set_rule_disabled,
)

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def temp_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return path


def temp_empty_db():
    """Fresh DB file with no tables at all."""
    path = temp_path()
    conn = sqlite3.connect(path)
    conn.close()
    return path


def temp_watcher_db():
    """Temp DB with the real watcher tables (one setup + two evals)."""
    from proto.watcher import emit_setup

    path = temp_path()
    conn = sqlite3.connect(path)
    sid = emit_setup(conn, {"rule_id": "t1-break-v1", "coin": "AAA",
                            "src_bar_ts": 1000, "entry": 100.0,
                            "direction": "LONG"})
    conn.execute(
        "INSERT INTO watcher_eval (setup_id, bar_ts, state, monitoring,"
        " ret_gross, ret_net, note) VALUES (?,?,?,?,?,?,?)",
        (sid, 1300, "active", "ok", 0.01, 0.009, "first"))
    conn.execute(
        "INSERT INTO watcher_eval (setup_id, bar_ts, state, monitoring,"
        " ret_gross, ret_net, note) VALUES (?,?,?,?,?,?,?)",
        (sid, 1600, "active", "ok", 0.02, 0.018, "second"))
    conn.commit()
    conn.close()
    return path, sid


# 1. Missing DB is contained.
try:
    load_panel("/nonexistent/x.db")
    check("missing db raises WatcherUnavailable", False, "no raise")
except WatcherUnavailable:
    check("missing db raises WatcherUnavailable", True)
except Exception as e:  # noqa: BLE001
    check("missing db raises WatcherUnavailable", False,
          f"wrong exc: {type(e).__name__}: {e}")

# 2. Fresh DB (no tables) is an empty panel, never raises.
try:
    panel = load_panel(temp_empty_db())
    check("fresh db is empty panel", panel.groups["active"] == [],
          f"{panel.groups!r}")
except Exception as e:  # noqa: BLE001
    check("fresh db is empty panel", False,
          f"{type(e).__name__}: {e}")

# 3. Read path returns real rows + latest eval.
try:
    path, sid = temp_watcher_db()
    conn = open_ro(path)
    try:
        rows = fetch_setups(conn)
        evals = fetch_latest_evals(conn)
    finally:
        conn.close()
    check("fetch_setups returns the emitted row",
          len(rows) == 1 and rows[0]["id"] == sid
          and rows[0]["rule_id"] == "t1-break-v1"
          and rows[0]["coin"] == "AAA",
          f"{rows!r}")
    check("fetch_latest_evals keeps the latest bar per setup",
          set(evals) == {sid} and evals[sid]["bar_ts"] == 1600
          and evals[sid]["ret_gross"] == 0.02,
          f"{evals!r}")
    panel = load_panel(path)
    check("load_panel composes rows into the shared view-model",
          panel.by_id.get(sid, {}).get("coin") == "AAA"
          and panel.groups["active"] == [sid]
          and panel.last_bar_ts == 1600,
          f"{panel.groups!r} last={panel.last_bar_ts!r}")
except Exception as e:  # noqa: BLE001
    check("read path returns rows + latest eval", False,
          f"{type(e).__name__}: {e}")

# 4. Flags round-trip via set/get on a temp DB.
try:
    path = temp_empty_db()
    rw = sqlite3.connect(path)
    check("absent flags default to (False, ())",
          get_flags(rw) == (False, ()),
          repr(get_flags(rw)))
    set_paused(rw, True)
    check("paused round-trips True", get_flags(rw)[0] is True,
          repr(get_flags(rw)))
    set_rule_disabled(rw, "t1-break-v1", True)
    paused, disabled = get_flags(rw)
    check("disabled rule round-trips",
          paused is True and disabled == ("t1-break-v1",),
          repr((paused, disabled)))
    set_paused(rw, False)
    set_rule_disabled(rw, "t1-break-v1", False)
    check("flags clear back to (False, ())",
          get_flags(rw) == (False, ()),
          repr(get_flags(rw)))
    rw.close()
    # Flags surface in the composed panel notices.
    rw = sqlite3.connect(path)
    set_paused(rw, True)
    rw.close()
    panel = load_panel(path)
    check("paused flag surfaces in panel notices",
          any("paused" in n.lower() for n in panel.notices),
          repr(panel.notices))
except Exception as e:  # noqa: BLE001
    check("flags round-trip via set/get", False,
          f"{type(e).__name__}: {e}")

# 5. Locked-DB read maps to WatcherUnavailable.
try:
    path, _sid = temp_watcher_db()
    holder = sqlite3.connect(path, timeout=30)
    holder.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    holder.commit()
    holder.execute("BEGIN EXCLUSIVE")
    holder.execute(
        "INSERT INTO meta (key, value) VALUES ('probe', '1')")
    try:
        load_panel(path)
        check("locked db maps to WatcherUnavailable", False, "no raise")
    except WatcherUnavailable:
        check("locked db maps to WatcherUnavailable", True)
    except Exception as e:  # noqa: BLE001
        check("locked db maps to WatcherUnavailable", False,
              f"wrong exc: {type(e).__name__}: {e}")
    finally:
        holder.rollback()
        holder.close()
except Exception as e:  # noqa: BLE001
    check("locked db maps to WatcherUnavailable", False,
          f"setup failed: {type(e).__name__}: {e}")

# 6. Read-only handle cannot write.
try:
    path, _sid = temp_watcher_db()
    conn = open_ro(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS _probe (a)")
        check("read-only handle rejects writes", False, "write allowed")
    except sqlite3.OperationalError:
        check("read-only handle rejects writes", True)
    finally:
        conn.close()
except Exception as e:  # noqa: BLE001
    check("read-only handle rejects writes", False,
          f"{type(e).__name__}: {e}")

# 7. Older DBs (no trail/be columns) still read.
try:
    path = temp_path()
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE watcher_setup (id TEXT PRIMARY KEY,"
        " rule_id TEXT, coin TEXT, direction TEXT, src_bar_ts INTEGER,"
        " entry REAL, state TEXT, monitoring TEXT)")
    conn.execute(
        "INSERT INTO watcher_setup (id, rule_id, coin, direction,"
        " src_bar_ts, entry, state, monitoring)"
        " VALUES ('r:OLD:5', 'r', 'OLD', 'LONG', 5, 10.0,"
        " 'active', 'ok')")
    conn.commit()
    conn.close()
    conn = open_ro(path)
    try:
        rows = fetch_setups(conn)
    finally:
        conn.close()
    check("older DB without trail/be columns still reads",
          len(rows) == 1 and rows[0]["id"] == "r:OLD:5"
          and rows[0]["coin"] == "OLD",
          f"{rows!r}")
except Exception as e:  # noqa: BLE001
    check("older DB without trail/be columns still reads", False,
          f"{type(e).__name__}: {e}")

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
