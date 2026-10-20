"""WS-E: the immutable, hash-chained decision log.

This is a NEW artifact: ``docs/decisions/chain.jsonl``.  It is NOT the
human-owned ``docs/decisions/log.jsonl`` (which stays exactly as it is --
0 bytes, human-owned, never written by any service) and it never migrates,
reads or rewrites it.  The two files are separate on purpose: the owner
decides when (and whether) to migrate.

A chain entry is one JSON object per line::

    {"id": "DEC-0001", "ts": <epoch int>,
     "kind": "promote" | "reject" | "continue" | "hold",
     "subject": "<rule id or config id>",
     "hypothesis_id": "HYD-0001" | null,
     "reasons": ["<non-empty string>", ...],
     "decided_by": "<non-empty string>",
     "evidence": [{"path": "...", "sha256": "<64 hex>"}, ...],
     "prev_hash": "<64 hex>" | null,
     "hash": "<64 hex>"}

``hash = sha256(canonical_json(entry_without_hash))`` -- the canonical JSON
is the sorted-key, compact UTF-8 encoding of the entry with the ``hash``
field removed.  ``prev_hash`` is the previous entry's hash (``null`` for the
first one), so the file is a chain: editing an entry breaks its own hash,
and it breaks the link of the entry that follows it.

Tamper-evidence has one honest limit, stated here so nobody over-claims:
a hash chain detects an edit, but a determined editor who rewrites an entry
AND recomputes every hash after it produces a self-consistent file again.
The chain's anchor is therefore its TAIL hash, which a reader has to
record out of band (a message, a ticket, the next entry's ``prev_hash``).
Without an anchor the chain proves internal consistency, not authorship.

Rules enforced here (all refusals, never silent writes):

  * **Append-only.**  The file is opened in append mode; nothing is ever
    rewritten or truncated.  The write is one ``\\n``-terminated line,
    flushed and ``fsync``-ed.
  * **Never extend a broken chain.**  ``append`` validates the whole chain
    first and refuses when it is invalid -- that is how an out-of-band
    edit is caught instead of being silently extended.
  * **Concurrency.**  The tail is re-checked under an exclusive lock
    immediately before the write: a writer that appended in between is
    detected and refused.
  * **Evidence is verified at write time.**  Every cited file is re-hashed
    and compared with the recorded sha256; a missing file or a changed
    file is a refusal.  A decision must not cite evidence that has since
    changed.
  * **The human-owned log is never written.**  ``append`` refuses a path
    whose file name is ``log.jsonl``.

The id rule and the hypothesis-id vocabulary are reused from
``proto.decisions`` (the human log's validator) and
``proto.hypothesis_registry``; neither module is modified.

**The hypothesis linkage is ADVISORY.**  ``append`` has no ``known_ids``
parameter (the published signature has none), so a supplied
``hypothesis_id`` is checked for FORMAT only (``HYD-####``) and may be
``null``: whether the hypothesis actually exists is the caller's business
(the human log's ``proto.decisions.validate_log`` is the existence check
for the human log).  A chain entry therefore says "this decision claims to
be about HYD-0007", not "this decision is provably about HYD-0007".

**Where the file lives.**  ``CHAIN_RELPATH`` names the canonical location
the owner chooses (``docs/decisions/chain.jsonl``).  ``append`` takes an
EXPLICIT path -- the caller decides where the chain lives, and nothing
here creates the file until the first append (this module never creates
it by itself).

Stdlib only.  This module never promotes a rule, never enables ACTIONABLE
output and never touches the holdout.
"""
import hashlib
import json
import os
import re
import time

from proto.decisions import DECISION_ID_RE
from proto.hypothesis_registry import ID_RE as HYPOTHESIS_ID_RE

try:                                    # POSIX advisory locking (Linux)
    import fcntl
except ImportError:                     # pragma: no cover - non-POSIX
    fcntl = None

DECISION_LOG_VERSION = "decision-chain-v1"

#: The decision vocabulary of the chain.  The human log
#: (``proto.decisions.DECISIONS``) knows "promote" and "hold"; the chain
#: adds "reject" and "continue".
DECISION_KINDS = ("promote", "reject", "continue", "hold")

#: Where the chain lives, relative to the repository root.  The file is NOT
#: created by this module -- it is created by the first ``append`` to it.
CHAIN_RELPATH = os.path.join("docs", "decisions", "chain.jsonl")

#: The human-owned log this module must never write.
HUMAN_LOG_BASENAME = "log.jsonl"

HASH_RE = re.compile(r"^[0-9a-f]{64}$")

#: The id sequence is 4 digits wide (``DEC-0001``), like the human log.
MAX_ID_NUMBER = 9999


class DecisionError(ValueError):
    """A refusal: the decision was NOT written.  ``reason`` says why."""


def _canonical(obj):
    """The canonical JSON encoding used for hashing (sorted keys, compact)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def entry_hash(entry: dict) -> str:
    """sha256 over the canonical JSON of the entry WITHOUT its ``hash`` field.

    The entry may carry extra fields: they are part of the hashed body, so
    an out-of-band edit of any field changes the hash.
    """
    if not isinstance(entry, dict):
        raise DecisionError("an entry must be a dict")
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _read_entries(path):
    """(entries, errors): the parsed entries of the chain file and the
    per-line parse errors.  A missing file is an empty chain."""
    if not os.path.exists(path):
        return [], []
    entries = []
    errors = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                errors.append(f"line {lineno}: bad-json")
                continue
            if not isinstance(obj, dict):
                errors.append(f"line {lineno}: bad-object")
                continue
            entries.append((lineno, obj))
    return entries, errors


def load(path: str) -> list[dict]:
    """Every parsed entry of the chain file, in file order.

    A missing file is an empty chain (``[]``).  Malformed lines are skipped
    here; ``validate_chain`` is what reports them.
    """
    entries, _errors = _read_entries(path)
    return [obj for _lineno, obj in entries]


def next_id(path: str) -> str:
    """The next id for the chain file: "DEC-0001", "DEC-0002", ...

    The number is the highest valid id already in the file plus one (the
    human log's procedure).  A missing or empty file starts at DEC-0001.
    """
    highest = 0
    for _lineno, obj in _read_entries(path)[0]:
        mid = obj.get("id")
        if isinstance(mid, str) and DECISION_ID_RE.match(mid):
            highest = max(highest, int(mid.split("-")[1]))
    if highest >= MAX_ID_NUMBER:
        raise DecisionError(
            f"the id sequence is exhausted (DEC-{MAX_ID_NUMBER:04d})")
    return f"DEC-{highest + 1:04d}"


def validate_chain(path: str) -> list[str]:
    """Every problem in the chain file, one string per problem.  [] = valid.

    Detected: a broken hash chain, an edited entry (its content no longer
    matches its hash), a duplicate id, a bad id format, a non-monotonic
    timestamp, an unknown kind, empty reasons, an empty ``decided_by`` and
    an evidence entry without a 64-hex sha256 (plus malformed lines and a
    missing/!malformed hash or prev_hash).
    """
    errors = []
    entries, parse_errors = _read_entries(path)
    errors.extend(parse_errors)

    seen = {}
    prev_computed = None          # the RECOMPUTED hash of the previous entry
    prev_ts = None
    for lineno, entry in entries:
        eid = entry.get("id")
        if not isinstance(eid, str) or not DECISION_ID_RE.match(eid):
            errors.append(f"line {lineno}: bad-id: {eid!r}")
        else:
            seen.setdefault(eid, []).append(lineno)

        ts = entry.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, int):
            errors.append(f"line {lineno}: bad-ts: {ts!r}")
        else:
            if prev_ts is not None and ts < prev_ts:
                errors.append(f"line {lineno}: non-monotonic timestamp: "
                              f"{ts} < {prev_ts}")
            prev_ts = ts

        kind = entry.get("kind")
        if kind not in DECISION_KINDS:
            errors.append(f"line {lineno}: unknown kind: {kind!r}")

        subject = entry.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            errors.append(f"line {lineno}: empty subject")

        reasons = entry.get("reasons")
        if (not isinstance(reasons, list) or not reasons
                or not all(isinstance(r, str) and r.strip() for r in reasons)):
            errors.append(f"line {lineno}: empty reasons")

        decided_by = entry.get("decided_by")
        if not isinstance(decided_by, str) or not decided_by.strip():
            errors.append(f"line {lineno}: empty decided_by")

        evidence = entry.get("evidence")
        if not isinstance(evidence, list):
            errors.append(f"line {lineno}: bad evidence: {evidence!r}")
        else:
            for i, item in enumerate(evidence):
                if not isinstance(item, dict):
                    errors.append(f"line {lineno}: evidence {i}: not an object")
                    continue
                path_ = item.get("path")
                if not isinstance(path_, str) or not path_.strip():
                    errors.append(f"line {lineno}: evidence {i}: missing path")
                sha = item.get("sha256")
                if not isinstance(sha, str) or not HASH_RE.match(sha):
                    errors.append(f"line {lineno}: evidence {i}: sha256 is not "
                                  "64 lowercase hex characters")

        computed = entry_hash(entry)
        stored = entry.get("hash")
        if not isinstance(stored, str) or not HASH_RE.match(stored):
            errors.append(f"line {lineno}: bad hash: {stored!r}")
        elif stored != computed:
            errors.append(f"line {lineno}: hash mismatch: the entry content "
                          "does not match its recorded hash")

        prev_hash = entry.get("prev_hash")
        if prev_hash is not None and (not isinstance(prev_hash, str)
                                      or not HASH_RE.match(prev_hash)):
            errors.append(f"line {lineno}: bad prev_hash: {prev_hash!r}")
        elif prev_hash != prev_computed:
            errors.append(f"line {lineno}: broken link: prev_hash does not "
                          "match the previous entry's hash")
        prev_computed = computed

    for eid, lines in seen.items():
        if len(lines) > 1:
            errors.append(f"duplicate-id: {eid} at lines "
                          f"{', '.join(map(str, lines))}")
    return errors


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_evidence(evidence):
    """Normalise the evidence list and verify every cited file.  Returns the
    normalised list; raises DecisionError when a file is missing or its
    content no longer matches the recorded hash."""
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list):
        raise DecisionError("evidence must be a list")
    out = []
    for i, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise DecisionError(f"evidence {i} is not an object")
        path_ = item.get("path")
        sha = item.get("sha256")
        if not isinstance(path_, str) or not path_.strip():
            raise DecisionError(f"evidence {i} has no path")
        if not isinstance(sha, str) or not HASH_RE.match(sha):
            raise DecisionError(f"evidence {i} has no 64-hex sha256")
        if not os.path.isfile(path_):
            raise DecisionError(f"evidence file is missing: {path_}")
        actual = _sha256_file(path_)
        if actual != sha:
            raise DecisionError(
                f"evidence file changed since it was cited: {path_} "
                f"(recorded {sha}, on disk {actual})")
        out.append({"path": path_, "sha256": sha})
    return out


def append(path: str, *, kind: str, subject: str, reasons: list[str],
           evidence: list, hypothesis_id: str | None = None,
           decided_by: str, ts: int | None = None) -> dict:
    """Append ONE decision to the chain file and return the written entry.

    The write is atomic and append-only: the file is opened in append mode,
    one ``\\n``-terminated line is written, flushed and ``fsync``-ed.  The
    file is never rewritten or truncated.

    Refusals (nothing is written, a ``DecisionError`` is raised):

      * an unknown ``kind``, an empty ``subject``, empty ``reasons``, an
        empty ``decided_by`` or a malformed ``hypothesis_id``;
      * evidence that is missing on disk or whose content no longer
        matches the recorded sha256;
      * a chain file that is already invalid (an out-of-band edit is
        caught here instead of being silently extended);
      * a ``ts`` that is not STRICTLY AFTER the tail's ``ts``, or an
        explicit ``ts`` in the future.  A timestamp that goes backwards
        would make the chain invalid (and every later append impossible),
        so it is refused at write time instead of poisoning the chain;
      * a concurrent writer: the tail is re-checked under an exclusive lock
        immediately before the write;
      * a path whose file name is the human-owned ``log.jsonl``.

    The default ``ts`` is the current second, raised to ``tail_ts + 1``
    when the clock has not moved past the previous decision: two decisions
    in the same second still produce a strictly increasing chain (the
    raised second is at most one second ahead of the clock).
    """
    if os.path.basename(path) == HUMAN_LOG_BASENAME:
        raise DecisionError(
            f"{HUMAN_LOG_BASENAME} is the human-owned log; this module never "
            "writes it (the chain is a separate artifact)")
    if kind not in DECISION_KINDS:
        raise DecisionError(f"unknown kind: {kind!r} (expected one of "
                            f"{', '.join(DECISION_KINDS)})")
    if not isinstance(subject, str) or not subject.strip():
        raise DecisionError("the subject must be a non-empty string")
    if (not isinstance(reasons, list) or not reasons
            or not all(isinstance(r, str) and r.strip() for r in reasons)):
        raise DecisionError("the reasons must be a non-empty list of "
                            "non-empty strings")
    if not isinstance(decided_by, str) or not decided_by.strip():
        raise DecisionError("decided_by must be a non-empty string")
    if hypothesis_id is not None:
        if (not isinstance(hypothesis_id, str)
                or not HYPOTHESIS_ID_RE.match(hypothesis_id)):
            raise DecisionError(f"hypothesis_id must match "
                                f"{HYPOTHESIS_ID_RE.pattern}: {hypothesis_id!r}")

    evidence_norm = _check_evidence(evidence)

    # The chain must be intact before it is extended.
    errors = validate_chain(path)
    if errors:
        raise DecisionError("the chain file is not valid, refusing to extend "
                            "it: " + "; ".join(errors))

    entries = load(path)
    prev_hash = entries[-1].get("hash") if entries else None
    tail_ts = entries[-1].get("ts") if entries else None
    now = int(time.time())
    if ts is None:
        ts = now
        if isinstance(tail_ts, int) and ts <= tail_ts:
            # The clock has not moved past the previous decision: keep the
            # chain strictly increasing in time.
            ts = tail_ts + 1
    else:
        if isinstance(ts, bool) or not isinstance(ts, int):
            raise DecisionError(f"ts must be an epoch integer: {ts!r}")
        if ts > now:
            raise DecisionError(f"the ts is in the future: {ts} > {now}")
        if isinstance(tail_ts, int) and ts <= tail_ts:
            raise DecisionError(
                f"the ts must be strictly after the previous decision's ts "
                f"({tail_ts}); a ts that goes backwards (or repeats it) "
                "would make the chain invalid and every later append "
                "impossible")

    entry = {
        "id": next_id(path),
        "ts": ts,
        "kind": kind,
        "subject": subject,
        "hypothesis_id": hypothesis_id,
        "reasons": list(reasons),
        "decided_by": decided_by,
        "evidence": evidence_norm,
        "prev_hash": prev_hash,
    }
    entry["hash"] = entry_hash(entry)
    line = json.dumps(entry, sort_keys=True) + "\n"

    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)

    existed = os.path.exists(path)
    size_before = os.path.getsize(path) if existed else 0
    with open(path, "a", encoding="utf-8") as fh:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            # The tail must still be the one the entry was built on: a
            # concurrent writer between the read and the write is refused.
            size_now = os.fstat(fh.fileno()).st_size
            if size_now != size_before:
                raise DecisionError(
                    "the chain file changed while the decision was being "
                    "written (another writer appended); refusing to write")
            if existed:
                tail = load(path)
                tail_hash = tail[-1].get("hash") if tail else None
                if tail_hash != prev_hash:
                    raise DecisionError(
                        "the chain tail changed while the decision was being "
                        "written; refusing to write")
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return entry
