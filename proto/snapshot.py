"""Close-time snapshot: exactly what was on screen, back on screen at launch.

On close (GUI close button / Ctrl-Q, TUI q / Esc, headless end / Ctrl-C)
the app pickles its current cards (+ cached trade plans) to `.last_entries`
next to the database. On launch that file renders instantly — no API wait,
no window heuristic, no duplicate coins — and the first live scan replaces
it wholesale.

Pickle is safe here: the file is written and read by the same app on the
same machine, version-stamped, and any corruption (or version mismatch)
falls back to the DB snapshot, then to a blank table. Nothing here touches
orders or keys — Scorecards and Plans are plain data.
"""

import os
import time

VERSION = 1
FILENAME = ".last_entries"


def path_for(db_path):
    """Snapshot path living next to the database it was taken from."""
    base = os.path.dirname(os.path.abspath(db_path)) or "."
    return os.path.join(base, FILENAME)


def save(db_path, cards, plans=None, meta=None):
    """Write the snapshot. Never raises — a failed save must not break close."""
    try:
        import pickle
        payload = {"version": VERSION, "saved_at": time.time(),
                   "cards": list(cards or []),
                   "plans": dict(plans or {}),
                   "meta": dict(meta or {})}
        tmp = path_for(db_path) + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(payload, f, protocol=4)
        os.replace(tmp, path_for(db_path))
        return True
    except Exception:
        return False


def load(db_path, max_age_s=7 * 86400):
    """(cards, plans, saved_at) or (None, None, None).

    Stale (> max_age_s) or unreadable files are treated as missing — a
    week-old board is worse than a blank one waiting on live data.
    """
    try:
        import pickle
        with open(path_for(db_path), "rb") as f:
            payload = pickle.load(f)
        if not isinstance(payload, dict) or payload.get("version") != VERSION:
            return None, None, None
        saved_at = payload.get("saved_at") or 0
        if time.time() - saved_at > max_age_s:
            return None, None, None
        cards = payload.get("cards") or []
        plans = payload.get("plans") or {}
        if not isinstance(cards, list) or not isinstance(plans, dict):
            return None, None, None
        return cards, plans, saved_at
    except Exception:
        return None, None, None

