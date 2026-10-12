"""Read-only watcher feed + pause/disable flags (Task 2).

Single read path per front end: open the DB read-only (``mode=ro``) ->
fetch ``watcher_setup`` rows + latest ``watcher_eval`` per setup + flags
from ``meta`` -> :func:`proto.watcher_view.build_panel` -> render.

Failure containment: a missing/locked DB raises :class:`WatcherUnavailable`
so front ends render a "watcher data unavailable" panel with the scanner
intact. A fresh DB without watcher tables yields an empty panel, never a
raise. Only writers here are :func:`set_paused` / :func:`set_rule_disabled`
(spec-mandated safety switches, persisted to ``meta``); UIs call those on
user action, never the read path.

Stdlib ``sqlite3`` only. Read-only stays read-only: no order placement, no
key storage, no execution code.
"""

import os
import sqlite3

from proto import watcher_report as _report
from proto.watcher_view import WatcherPanel, build_panel

__all__ = [
    "WatcherUnavailable",
    "open_ro",
    "fetch_setups",
    "fetch_latest_evals",
    "get_flags",
    "set_paused",
    "set_rule_disabled",
    "load_panel",
    "correlated_lines",
]

# Spec S4 columns of watcher_setup, in DDL order, plus the trail-runtime
# columns that ensure_watcher_tables adds via ALTER (be_pending/be_moved).
# Older DBs may lack the trailing columns; fetch_setups PRAGMA-guards and
# selects only the columns present.
SETUP_COLUMNS = (
    "id", "rule_id", "coin", "symbol", "src", "direction", "src_bar_ts",
    "entry", "b_stop", "a_tgt", "stop_px", "tgt_px", "stop_dist_pct",
    "tgt_dist_pct", "N_bars", "trail", "state", "substate", "monitoring",
    "price_source", "cost_total", "cost_floor", "funding_state",
    "funding_projected", "fund_stress", "shadow_selected", "ev_r", "p_lb",
    "invalid_price", "last_eval_bar", "unavailable_bars", "created_at",
    "closed_at", "be_pending", "be_moved",
)

EVAL_COLUMNS = (
    "setup_id", "bar_ts", "state", "monitoring", "ret_gross", "ret_net",
    "note",
)

PAUSED_KEY = "watcher_paused"
DISABLED_PREFIX = "watcher_disabled:"

# Short reader timeout: a locked watcher DB must degrade to
# WatcherUnavailable promptly, never hang a UI refresh.
RO_TIMEOUT_S = 2.0


class WatcherUnavailable(Exception):
    """The watcher DB cannot be read (missing, locked, corrupt, wrong
    permissions). Front ends catch this and render a contained
    "watcher data unavailable" panel with the scanner intact."""


def _table_columns(conn, table):
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def open_ro(db_path: str) -> sqlite3.Connection:
    """Open ``db_path`` read-only (``file:...?mode=ro``).

    A missing file raises :class:`WatcherUnavailable` (``mode=ro`` never
    creates it); any other open failure (locked, permissions, corrupt)
    maps to :class:`WatcherUnavailable` as well. The probe query forces
    the lazy connect to fail here, not on first render.
    """
    if not os.path.isfile(db_path):
        raise WatcherUnavailable(f"watcher db missing: {db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True,
                               timeout=RO_TIMEOUT_S)
        conn.execute("SELECT 1")
    except sqlite3.Error as e:
        raise WatcherUnavailable(
            f"watcher db unavailable: {db_path}: {e}") from e
    return conn


def fetch_setups(conn) -> list:
    """All ``watcher_setup`` rows as dicts (spec S4 columns by name).

    Empty list when the table is absent (fresh DB) -- never raises. Older
    DBs missing trailing (trail/be) columns are tolerated via PRAGMA: only
    present columns are selected.
    """
    have = _table_columns(conn, "watcher_setup")
    if not have:
        return []
    cols = [c for c in SETUP_COLUMNS if c in have]
    if not cols:
        return []
    try:
        cur = conn.execute(
            f"SELECT {', '.join(cols)} FROM watcher_setup ORDER BY src_bar_ts")
    except sqlite3.Error:
        return []
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def fetch_latest_evals(conn) -> dict:
    """Latest ``watcher_eval`` row per setup id (highest ``bar_ts`` wins).

    Empty dict when the table is absent. Eval dicts carry the spec S4
    eval columns (``bar_ts`` drives ``asof_ts``/``last_bar_ts`` in the
    shared view-model).
    """
    have = _table_columns(conn, "watcher_eval")
    if not have:
        return {}
    cols = [c for c in EVAL_COLUMNS if c in have]
    if "setup_id" not in cols or "bar_ts" not in cols:
        return {}
    try:
        cur = conn.execute(
            f"SELECT {', '.join(cols)} FROM watcher_eval ORDER BY bar_ts")
    except sqlite3.Error:
        return {}
    latest = {}
    for r in cur.fetchall():
        row = dict(zip(cols, r))
        latest[row["setup_id"]] = row
    return latest


def _truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def get_flags(conn) -> tuple:
    """``(paused, disabled)`` from ``meta`` keys ``watcher_paused`` and
    ``watcher_disabled:<rule>``. Absent ``meta`` table or keys ->
    ``(False, ())``; only truthy disable values are listed (sorted)."""
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except sqlite3.Error:
        return (False, ())
    if "meta" not in tables:
        return (False, ())
    try:
        paused_row = conn.execute("SELECT value FROM meta WHERE key=?",
                                  (PAUSED_KEY,)).fetchone()
        paused = bool(paused_row) and _truthy(paused_row[0])
        disabled = tuple(sorted(
            key[len(DISABLED_PREFIX):]
            for key, value in conn.execute(
                "SELECT key, value FROM meta WHERE key LIKE ?",
                (DISABLED_PREFIX + "%",)).fetchall()
            if _truthy(value)))
    except sqlite3.Error:
        return (False, ())
    return (paused, disabled)


def set_paused(conn_rw, paused: bool) -> None:
    """Persist the global pause switch (writer only; UIs call on action)."""
    conn_rw.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn_rw.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (PAUSED_KEY, "1" if paused else "0"))
    conn_rw.commit()


def set_rule_disabled(conn_rw, rule: str, disabled: bool) -> None:
    """Persist a per-trigger disable switch (writer only; UIs call on
    action). Clearing writes ``0`` so the key history stays visible."""
    conn_rw.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn_rw.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (DISABLED_PREFIX + rule, "1" if disabled else "0"))
    conn_rw.commit()


def load_panel(db_path, selected_id=None) -> WatcherPanel:
    """Compose open + fetch + flags + ``build_panel`` for one DB path.

    Any sqlite failure (missing/locked/corrupt) maps to
    :class:`WatcherUnavailable`; a table-less fresh DB yields an empty
    panel. Selection is by setup id (stable across refresh reorders).
    """
    try:
        conn = open_ro(db_path)
    except WatcherUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 - containment: never leak raw errors
        raise WatcherUnavailable(f"watcher db unavailable: {db_path}: {e}")
    try:
        try:
            rows = fetch_setups(conn)
            evals = fetch_latest_evals(conn)
            paused, disabled = get_flags(conn)
        except sqlite3.Error as e:
            raise WatcherUnavailable(
                f"watcher db unreadable: {db_path}: {e}") from e
    finally:
        conn.close()
    return build_panel(rows, evals, selected_id=selected_id, paused=paused,
                       disabled=disabled)


def correlated_lines(db_path, clusters=None, rho=None) -> list:
    """CORRELATED OPENS section lines for one DB path (Task 6 shared helper).

    The ONE helper every watcher surface renders: TUI table + detail,
    tkinter detail, Qt detail. Calls
    :func:`proto.watcher_report.correlated_opens` and shapes the lines
    exactly like :func:`proto.watcher_report.format_watcher_report`'s
    CORRELATED OPENS section (same header, same per-group shape, same
    proxy note). A missing/locked/unreadable DB yields ``[]`` (no
    section), never a raise, so draw paths stay contained.
    """
    try:
        conn = open_ro(db_path)
    except Exception:
        return []
    try:
        return _report.correlated_section_lines(conn, clusters, rho)
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
