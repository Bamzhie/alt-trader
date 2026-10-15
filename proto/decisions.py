"""WS7 append-only promotion decision log + validator.

The log (``docs/decisions/log.jsonl``) is human-owned: no service writes
it. Each line is one JSON object::

    {"id": "DEC-####", "ts": <UTC epoch int>, "hypothesis_id": "HYD-####",
     "decision": "promote" | "hold", "reasons": [<non-empty strings>],
     "checks": {<object, content unchecked>},
     "decided_by": <non-empty string>}

Manual append procedure (follow exactly):

1. Read the existing log and assign the next id from it (max existing
   DEC number + 1; an empty log starts at ``DEC-0001``).
2. Validate the candidate object with ``validate_decision`` AND the
   proposed combined log (existing decisions plus the candidate) with
   ``validate_log`` BEFORE appending — both must be clean. The
   preflight runs against a COPY: copy the current log to a temp file,
   append the candidate line to the copy, and run
   ``validate_log(copy, known_ids)``. The real log is modified only by
   step 3, after the copy validates clean. If either check fails, fix
   the object and do not append.
3. Append exactly one ``\\n``-terminated JSON line to the real log.
4. Post-append, re-run ``validate_log`` on the real log. A failure here
   is REPORTED, not rolled back — append-only history is never
   rewritten, so the line stands and is corrected by a superseding
   entry. (Pre-append clean + post-append failure indicates a validator
   bug, fixed separately.)

``known_ids`` is the set of hypothesis ids from the registry
(existence check only); it may be any iterable of id strings. This
module never imports the registry and never writes the log.
"""

import json
import re
import time

DECISION_ID_RE = re.compile(r"^DEC-[0-9]{4}$")

DECISIONS = ("promote", "hold")


def validate_decision(obj, known_ids):
    """Validate one decision object. Returns a list of error codes.

    Single-object scope: never reports ``duplicate-id`` (one object
    cannot collide with itself — that check lives in ``validate_log``).
    """
    if not isinstance(obj, dict):
        obj = {}
    known = set(known_ids)
    errors = []

    if not isinstance(obj.get("id"), str) or not DECISION_ID_RE.match(obj["id"]):
        errors.append("bad-id")

    ts = obj.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, int) or ts > time.time():
        errors.append("bad-ts")

    hid = obj.get("hypothesis_id")
    if not isinstance(hid, str) or hid not in known:
        errors.append("unknown-hypothesis")

    if obj.get("decision") not in DECISIONS:
        errors.append("bad-decision")

    reasons = obj.get("reasons")
    if (not isinstance(reasons, list) or not reasons
            or not all(isinstance(r, str) and r for r in reasons)):
        errors.append("empty-reasons")

    if not isinstance(obj.get("checks"), dict):
        errors.append("bad-checks")

    if not isinstance(obj.get("decided_by"), str) or not obj["decided_by"]:
        errors.append("empty-decider")

    return errors


def validate_log(path, known_ids):
    """Validate the whole log file. Returns ``(decisions, errors)``.

    ``decisions`` is the list of parsed decision objects; ``errors`` is
    a list of strings. Per-line problems are reported as
    ``"line <n>: <code>"`` using the ``validate_decision`` codes; a
    repeated decision id is reported once as
    ``"duplicate-id: DEC-0003 at lines 4, 9"``, naming every line.
    An empty log validates as ``([], [])``.
    """
    known = set(known_ids)
    decisions = []
    errors = []
    seen = {}

    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                # JSONDecodeError subclasses ValueError, as does the
                # int-string digit cap on oversized JSON integers: both
                # are per-line data defects, never an exception out of
                # validation. The affected line is named.
                errors.append(f"line {lineno}: bad-json")
                continue
            if not isinstance(obj, dict):
                errors.append(f"line {lineno}: bad-object")
                continue
            decisions.append(obj)
            for code in validate_decision(obj, known):
                errors.append(f"line {lineno}: {code}")
            dec_id = obj.get("id")
            if isinstance(dec_id, str):
                seen.setdefault(dec_id, []).append(lineno)

    for dec_id, lines in seen.items():
        if len(lines) > 1:
            errors.append(
                f"duplicate-id: {dec_id} at lines {', '.join(map(str, lines))}")

    return decisions, errors
