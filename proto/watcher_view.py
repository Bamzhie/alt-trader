"""Shared watcher view-model: pure stdlib panel builder for all front ends.

Input: ``watcher_setup`` rows + latest ``watcher_eval`` per setup (plain
dicts) + pause/disable flags. Output: :class:`WatcherPanel` with per-state
id groups, per-setup detail dicts, id-stable selection, and notices.

Pure: stdlib only, no GUI imports, no I/O, no network. Selection is by
setup id (``rule:coin:src_bar_ts``), never row index, so it survives
refresh reorders. All state wording comes from :data:`STATE_WORDS`;
front ends must render those verbs verbatim.
"""

from dataclasses import dataclass, field

RESEARCH_BANNER = "WATCHER (research, shadow-only — scanner untouched; no orders)"

STATE_WORDS = {"active": "active", "invalidated": "invalidated",
               "expired": "expired", "resolved": "resolved"}

GROUP_KEYS = ("active", "unavailable", "invalidated", "expired", "resolved")

SILENCE_VALID = "no active setups — silence is valid"


def _setup_id(row):
    sid = row.get("id")
    if sid:
        return str(sid)
    rule = row.get("rule_id", row.get("rule", "?"))
    return f"{rule}:{row.get('coin', '?')}:{row.get('src_bar_ts', '?')}"


def _state_word(state):
    """Verbatim STATE_WORDS verb for a stored state (tolerates composite
    ``resolved_stop`` / ``expired_time`` / ``state/substate`` spellings)."""
    if state in STATE_WORDS:
        return STATE_WORDS[state]
    base = str(state or "").split("/")[0].split("_")[0]
    # Composite spellings: resolved_stop/resolved_target -> resolved, etc.
    for prefix, word in (("resolved", "resolved"), ("expired", "expired"),
                         ("invalidated", "invalidated"), ("active", "active")):
        if base == prefix or str(state or "").startswith(prefix):
            return STATE_WORDS[word]
    return STATE_WORDS.get(base, STATE_WORDS["active"])


def _detail(row, evaluation):
    evaluation = evaluation or {}
    missing = evaluation.get("missing")
    if not isinstance(missing, list):
        missing = list(missing) if isinstance(missing, (tuple, set)) else []
    return {
        "rule": row.get("rule_id", row.get("rule")),
        "coin": row.get("coin"),
        "direction": row.get("direction"),
        "src_bar_ts": row.get("src_bar_ts"),
        "entry": row.get("entry"),
        "stop_px": row.get("stop_px"),
        "tgt_px": row.get("tgt_px"),
        "invalid_price": row.get("invalid_price"),
        "state": _state_word(row.get("state")),
        "monitoring": row.get("monitoring", "ok"),
        "missing": missing,
        "cost": {"total": row.get("cost_total"),
                 "floor": row.get("cost_floor", 0),
                 "funding_state": row.get("funding_state", row.get("funding"))},
        "ev_r": row.get("ev_r"),
        "p_lb": row.get("p_lb"),
        "shadow_selected": bool(row.get("shadow_selected")),
    }


@dataclass
class WatcherPanel:
    groups: dict = field(default_factory=lambda: {k: [] for k in GROUP_KEYS})
    by_id: dict = field(default_factory=dict)
    selected_id: str | None = None
    notices: list = field(default_factory=list)
    asof_ts: int = 0
    last_bar_ts: int | None = None


def build_panel(rows, evals, selected_id=None, paused=False, disabled=()):
    """Build a :class:`WatcherPanel` from setup rows + latest evals.

    ``rows``: ``watcher_setup`` mappings; ``evals``: setup-id -> latest
    ``watcher_eval`` mapping. ``asof_ts`` is the max observed
    ``src_bar_ts``/eval ``bar_ts`` (0 when no data); ``last_bar_ts`` the
    max eval ``bar_ts`` (None when no evals). Unknown ``selected_id``
    falls back to the first active setup (None when none active).
    """
    rows = [dict(r) if not isinstance(r, dict) else r for r in (rows or [])]
    evals = evals or {}
    groups = {k: [] for k in GROUP_KEYS}
    by_id = {}
    ordered = sorted(rows, key=lambda r: (r.get("src_bar_ts") or 0, _setup_id(r)))
    for row in ordered:
        sid = _setup_id(row)
        by_id[sid] = _detail(row, evals.get(sid))
        if row.get("monitoring") == "unavailable":
            groups["unavailable"].append(sid)
        else:
            word = _state_word(row.get("state"))
            groups[word if word in groups else "active"].append(sid)

    if selected_id in by_id:
        selected = selected_id
    else:
        selected = groups["active"][0] if groups["active"] else None

    notices = [RESEARCH_BANNER]
    if paused:
        notices.append("paused — setups listed but not acted on (shadow-only)")
    if disabled:
        notices.append(f"disabled rules: {','.join(disabled)}")
    if not by_id:
        notices.append(SILENCE_VALID)

    asof_ts = 0
    for row in rows:
        ts = row.get("src_bar_ts")
        if isinstance(ts, (int, float)) and int(ts) > asof_ts:
            asof_ts = int(ts)
    last_bar_ts = None
    for ev in evals.values():
        ts = (ev or {}).get("bar_ts") if isinstance(ev, dict) else None
        if isinstance(ts, (int, float)):
            if last_bar_ts is None or int(ts) > last_bar_ts:
                last_bar_ts = int(ts)
            if int(ts) > asof_ts:
                asof_ts = int(ts)
    if by_id and not groups["active"] \
            and len(groups["unavailable"]) == len(by_id):
        notices.append(
            f"monitoring unavailable — {len(by_id)} setups "
            f"(data as of {asof_ts})")

    return WatcherPanel(groups=groups, by_id=by_id, selected_id=selected,
                        notices=notices, asof_ts=asof_ts,
                        last_bar_ts=last_bar_ts)
