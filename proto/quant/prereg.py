"""Preregistration records + the append-only variant registry (workstream C).

A preregistration record is the machine-readable answer to two questions:

  1. **What was declared BEFORE the evaluation?**  Feature definitions, the
     point-in-time membership rule, the horizons, the event rules, the cost
     model, the splits, the embargo, the statistics, the promotion
     criteria, the declared experiment family and the config / data / code
     hashes.
  2. **What did every tested variant actually show?**  The variant registry
     lists EVERY tested variant -- the failures included -- with its own
     result (metric, value, denominator, interval) and the hash of the
     evidence file that carries the numbers.

Two rules make the record worth keeping: the registry is **append-only**
(a registered ``variant_id`` keeps its result forever; a changed result is
a validation error, never an overwrite; an identical result is idempotent)
and **denominators are mandatory** (``n`` is required for every variant that
is not ``pending``).

The record lives in a **sidecar JSON** next to the hypothesis markdown
(``docs/hypotheses/<ID>.json``); the markdown front-matter format is
untouched by this module, so the shipped ``docs/hypotheses/*.md`` files
keep validating clean under ``proto.hypothesis_registry``.

THE SEALED HOLDOUT: nothing here evaluates a holdout window, spends the
one-shot holdout or writes the holdout lock.  A record may *declare* a
holdout window, but the holdout is only ever evaluated by
``proto.quant.research.holdout_once``, under its own one-shot lock, and
only after validation kept a config.

HOW A CODE HASH IS MEANT TO BE PRODUCED
--------------------------------------
``code_hash(root, files)`` fingerprints the WORKING TREE (the git
revision of ``root`` plus the digests of the named files).  That is right
for code that has not run yet and WRONG for a result produced earlier:
the pipeline files of the run may have changed since, and hashing the
tree would certify code that never produced the numbers.  For a completed
run use ``code_hash_at(root, rev, files)``, which reads the contents FROM
the revision the run used (``git cat-file blob <rev>:<path>``) and mixes
that revision into the digest.  The shipped HYD-0002 record does exactly
this: the sweep ran from ``da5f43c`` and five of its eight pipeline files
have changed since.  The record carries the revision and the file list
(``statistics.code_git_rev`` / ``statistics.code_files``), so the
fingerprint can be recomputed and checked.

Standard library only.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, replace

PREREG_VERSION = "prereg-v1"

#: Statuses a preregistration record may carry (the hypothesis-registry
#: vocabulary, reused so one status means the same thing in both files).
STATUSES = ("proposed", "registered", "testing", "validated", "rejected")

#: Statuses a tested variant may carry.
VARIANT_STATUSES = ("pending", "passed", "failed", "rejected")

#: Horizon labels a preregistration record may declare.  This is the
#: QUANT label vocabulary (the label horizon of the evaluated rule), not
#: the episode vocabulary of ``proto.hypothesis_registry`` -- "12h" is a
#: legitimate quant horizon (144 bars of the declared grid) and is missing
#: from the episode vocabulary, which is left untouched.
HORIZONS = ("1h", "4h", "12h", "24h", "7d")

#: Status-gated required-field sets (mirroring ``hypothesis_registry``,
#: extended with the machine-readable fields).
BASE_FIELDS = ("id", "title", "status", "hypothesis")
REGISTERED_EXTRA = ("feature_definitions", "universe",
                    "point_in_time_membership", "horizons", "event_rules",
                    "costs", "splits", "embargo_days", "statistics", "family",
                    "config_hash", "data_hash", "code_hash")
TESTING_EXTRA = ("promotion_criteria", "variants")

ID_RE = re.compile(r"^HYD-[0-9]{4}$")
HASH_RE = re.compile(r"^[0-9a-f]{4,}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: The embargo a horizon needs: 7 days for a "7d" label, at least 1 day
#: otherwise (mirrors ``hypothesis_registry``).
LONG_HORIZON = "7d"
LONG_HORIZON_EMBARGO_DAYS = 7.0
SHORT_HORIZON_EMBARGO_DAYS = 1.0


class PreregError(ValueError):
    """Raised when the append-only registry refuses an operation.

    ``errors`` carries the machine-readable violation list so a caller can
    surface it the same way ``validate`` does.
    """

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = list(errors or [message])


# --------------------------------------------------------------------- types


@dataclass(frozen=True)
class Variant:
    """One tested variant of a hypothesis.

    ``result`` carries the metric, the value, the DENOMINATOR (``n``) and
    the interval, plus a free-form ``detail``; ``evidence`` lists the
    files that carry the numbers, each with the sha256 read at
    registration.
    """

    variant_id: str
    hypothesis_id: str
    description: str
    config_hash: str
    result: dict
    tested_ts: int
    evidence: list


@dataclass(frozen=True)
class PreregRecord:
    """A versioned preregistration record for one hypothesis."""

    id: str
    title: str
    status: str
    hypothesis: str
    feature_definitions: list
    universe: str
    point_in_time_membership: str
    horizons: list
    event_rules: list
    costs: dict
    splits: dict
    embargo_days: float
    statistics: dict
    promotion_criteria: list
    family: list
    config_hash: str
    data_hash: str
    code_hash: str
    variants: list
    created_ts: int
    updated_ts: int


# ------------------------------------------------------------------- helpers


def _present(value):
    """A value counts as present unless it is None, empty or NaN."""
    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, float):
        return value == value
    if isinstance(value, (int,)):
        return True
    if isinstance(value, str):
        return len(value.strip()) > 0
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def _is_number(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    return False


def _repo_root():
    """The repository root (the directory that holds ``proto/``)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def _result_key(result):
    """A canonical key for a result dict (order independent)."""
    try:
        return json.dumps(result, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(result)


def _variants_key(variants):
    return json.dumps([_result_key(v.result) for v in variants])


# ------------------------------------------------------------- hash helpers


def file_sha256(path) -> str:
    """sha256 of a file, as 64 lowercase hex characters.

    Read-only; a missing file raises ``FileNotFoundError`` -- the digest of
    a file that is not there is never invented.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def data_hash(paths: dict) -> str:
    """sha256 over the sorted ``(name, sha256)`` pairs of the given files.

    ``paths`` maps a name to a file path.  The files are read, never
    written.  A missing file raises ``FileNotFoundError`` naming it -- a
    data hash that silently skipped a file would certify the wrong data.
    """
    if not isinstance(paths, dict) or not paths:
        raise ValueError("data_hash needs a non-empty {name: path} mapping")
    parts = []
    for name in sorted(paths):
        path = paths[name]
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"data_hash: input file is missing: {name} -> {path}")
        parts.append(f"{name}:{file_sha256(path)}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def git_rev(root: str):
    """The git revision of ``root`` (or None when git is unavailable)."""
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    rev = out.stdout.strip()
    return rev or None


def code_hash(root: str, files: list) -> str:
    """sha256 over the git revision (if available) and the file digests.

    ``files`` are paths relative to ``root``; a missing file raises
    ``FileNotFoundError`` naming it (the code hash must cover exactly the
    code that ran).
    """
    parts = []
    rev = git_rev(root)
    if rev:
        parts.append(f"git:{rev}")
    for rel in sorted(files):
        path = os.path.join(str(root), rel)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"code_hash: source file is missing: {rel} -> {path}")
        parts.append(f"{rel}:{file_sha256(path)}")
    if not parts:
        raise ValueError("code_hash needs at least one file")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def code_hash_at(root: str, rev: str, files: list) -> str:
    """The same construction as ``code_hash``, pinned to a git revision.

    The file contents are read from the named revision, so a result
    produced by an older revision is fingerprinted by THAT code and not by
    whatever the working tree holds now.
    """
    parts = [f"git:{rev}"]
    for rel in sorted(files):
        try:
            out = subprocess.run(["git", "-C", str(root), "cat-file", "blob",
                                  f"{rev}:{rel}"],
                                 capture_output=True, timeout=15)
        except Exception as exc:
            raise FileNotFoundError(
                f"code_hash_at: cannot read {rel} at {rev}: {exc}") from exc
        if out.returncode != 0:
            raise FileNotFoundError(
                f"code_hash_at: {rel} does not exist at {rev}")
        parts.append(f"{rel}:{hashlib.sha256(out.stdout).hexdigest()}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


# ------------------------------------------------------------- serialisation


def variant_to_dict(variant: Variant) -> dict:
    return {
        "variant_id": variant.variant_id,
        "hypothesis_id": variant.hypothesis_id,
        "description": variant.description,
        "config_hash": variant.config_hash,
        "result": variant.result,
        "tested_ts": variant.tested_ts,
        "evidence": [dict(e) for e in (variant.evidence or [])],
    }


def variant_from_dict(data: dict) -> Variant:
    if not isinstance(data, dict):
        raise ValueError("prereg: a variant must be a JSON object")
    return Variant(
        variant_id=data.get("variant_id", ""),
        hypothesis_id=data.get("hypothesis_id", ""),
        description=data.get("description", ""),
        config_hash=data.get("config_hash", ""),
        result=data.get("result") or {},
        tested_ts=int(data.get("tested_ts") or 0),
        evidence=list(data.get("evidence") or []))


def record_to_dict(record: PreregRecord) -> dict:
    """The JSON payload of a record (stable key order, no timestamps added)."""
    out = {"prereg_version": PREREG_VERSION}
    for field in ("id", "title", "status", "hypothesis", "feature_definitions",
                  "universe", "point_in_time_membership", "horizons",
                  "event_rules", "costs", "splits", "embargo_days",
                  "statistics", "promotion_criteria", "family", "config_hash",
                  "data_hash", "code_hash"):
        out[field] = getattr(record, field)
    out["variants"] = [variant_to_dict(v) for v in (record.variants or [])]
    out["created_ts"] = record.created_ts
    out["updated_ts"] = record.updated_ts
    return out


def record_from_dict(data: dict) -> PreregRecord:
    if not isinstance(data, dict):
        raise ValueError("prereg: a record must be a JSON object")
    return PreregRecord(
        id=data.get("id", ""),
        title=data.get("title", ""),
        status=data.get("status", ""),
        hypothesis=data.get("hypothesis", ""),
        feature_definitions=list(data.get("feature_definitions") or []),
        universe=data.get("universe", ""),
        point_in_time_membership=data.get("point_in_time_membership", ""),
        horizons=list(data.get("horizons") or []),
        event_rules=list(data.get("event_rules") or []),
        costs=dict(data.get("costs") or {}),
        splits=dict(data.get("splits") or {}),
        embargo_days=data.get("embargo_days", 0.0),
        statistics=dict(data.get("statistics") or {}),
        promotion_criteria=list(data.get("promotion_criteria") or []),
        family=list(data.get("family") or []),
        config_hash=data.get("config_hash", ""),
        data_hash=data.get("data_hash", ""),
        code_hash=data.get("code_hash", ""),
        variants=[variant_from_dict(v) for v in (data.get("variants") or [])],
        created_ts=int(data.get("created_ts") or 0),
        updated_ts=int(data.get("updated_ts") or 0))


# --------------------------------------------------------------- record CRUD


def new_record(id, title, status="registered", hypothesis="", *,
               feature_definitions=None, universe="",
               point_in_time_membership="", horizons=None, event_rules=None,
               costs=None, splits=None, embargo_days=1.0, statistics=None,
               promotion_criteria=None, family=None, config_hash="",
               data_hash="", code_hash="", variants=None,
               created_ts=None, updated_ts=None) -> PreregRecord:
    """Build a preregistration record (timestamps default to now)."""
    now = int(time.time())
    return PreregRecord(
        id=id,
        title=title,
        status=status,
        hypothesis=hypothesis,
        feature_definitions=list(feature_definitions or []),
        universe=universe,
        point_in_time_membership=point_in_time_membership,
        horizons=list(horizons or []),
        event_rules=list(event_rules or []),
        costs=dict(costs or {}),
        splits=dict(splits or {}),
        embargo_days=embargo_days,
        statistics=dict(statistics or {}),
        promotion_criteria=list(promotion_criteria or []),
        family=list(family or []),
        config_hash=config_hash,
        data_hash=data_hash,
        code_hash=code_hash,
        variants=list(variants or []),
        created_ts=int(created_ts if created_ts is not None else now),
        updated_ts=int(updated_ts if updated_ts is not None else now))


def save(record: PreregRecord, directory) -> str:
    """Write ``<directory>/<ID>.json`` atomically; return the path.

    The write goes to a temporary file in the same directory and is then
    renamed over the target, so a reader never sees a half-written record.
    """
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{record.id}.json")
    payload = json.dumps(record_to_dict(record), indent=2, default=str) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=f".{record.id}.", suffix=".tmp",
                               dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def load(directory, id) -> PreregRecord:
    """Load ``<directory>/<ID>.json``."""
    path = os.path.join(directory, f"{id}.json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return record_from_dict(data)


def load_all(directory) -> list:
    """Load every preregistration record in ``directory`` (sorted by id).

    Files that are not preregistration records are skipped; a file that
    claims to be one but cannot be parsed is an error, not a silent skip.
    """
    out = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        try:
            data = json.loads(raw)
        except ValueError:
            if "prereg_version" in raw:
                raise ValueError(f"prereg: cannot parse {path}")
            continue
        if not isinstance(data, dict) or "prereg_version" not in data:
            continue
        out.append(record_from_dict(data))
    out.sort(key=lambda r: r.id)
    return out


# ----------------------------------------------------------------- validation


def _resolve_evidence(path):
    """The sha256 of an evidence file, or None when it cannot be found."""
    candidates = [path]
    if not os.path.isabs(path):
        candidates.append(os.path.join(os.getcwd(), path))
        candidates.append(os.path.join(_repo_root(), path))
    for candidate in candidates:
        if os.path.isfile(candidate):
            try:
                return file_sha256(candidate)
            except OSError:
                return None
    return None


def _required_fields(status):
    # An unknown status is gated like the markdown registry does it: only
    # the base fields are demanded, and the status error is the finding.
    if not status or status not in STATUSES:
        return list(BASE_FIELDS)
    if status == "proposed":
        return list(BASE_FIELDS)
    if status == "registered":
        return list(BASE_FIELDS) + list(REGISTERED_EXTRA)
    return list(BASE_FIELDS) + list(REGISTERED_EXTRA) + list(TESTING_EXTRA)


def _declares_setup(status):
    """True when the status means the record must declare the whole setup.

    A ``proposed`` record is a bare idea: like the markdown registry's
    ``proposed`` entries it passes the window, hash and family rules
    vacuously instead of reporting them as missing.
    """
    return status in ("registered", "testing", "validated", "rejected")


def validate(record, *, known_hypothesis_ids=None) -> list:
    """Validate one preregistration record.

    Returns a list of ``<rule>: <detail>`` strings; ``[]`` means valid.
    The vocabulary mirrors ``proto.hypothesis_registry`` where the rule is
    the same rule (``required-field:``, ``ordering:``, ``embargo:``,
    ``within-window:``, ``unknown-hypothesis:``) and adds the
    machine-readable ones (``bad-hash:``, ``variant-result-changed:``).
    """
    if record is None:
        return ["required-field: record is required"]
    errors = []

    # --- status + required fields
    status = record.status.strip() if isinstance(record.status, str) else ""
    if not status:
        errors.append("required-field: status is required")
    elif status not in STATUSES:
        errors.append(f"unknown-status: {record.status!r} is not a known status")
    for name in _required_fields(status):
        # "status" and "id" have their own checks below; reporting them
        # twice would duplicate every error they raise.
        if name in ("status", "id"):
            continue
        if not _present(getattr(record, name, None)):
            errors.append(f"required-field: {name} is required")

    # --- id
    if not _present(record.id):
        errors.append("required-field: id is required")
    elif not ID_RE.match(str(record.id).strip()):
        errors.append(f"bad-id: {record.id!r} does not match ^HYD-[0-9]{{4}}$")

    if _declares_setup(status):
        # --- declared family
        family = record.family
        if not isinstance(family, list) or not family:
            errors.append("required-field: family is required")
        else:
            for hid in family:
                if not isinstance(hid, str) or not hid.strip():
                    errors.append("bad-family: family entries must be "
                                  "non-empty hypothesis ids")
                elif (known_hypothesis_ids is not None
                      and hid not in known_hypothesis_ids):
                    errors.append(f"unknown-hypothesis: {hid!r} is not a known "
                                  "hypothesis id")

        # --- hashes
        for name in ("config_hash", "data_hash", "code_hash"):
            value = getattr(record, name, None)
            if not _present(value):
                errors.append(f"required-field: {name} is required")
            elif not isinstance(value, str) or not HASH_RE.match(value.strip()):
                errors.append(f"bad-hash: {name} must be a non-empty lowercase "
                              "hex string")

        # --- horizons: every declared horizon must be in the vocabulary
        horizons = record.horizons
        if isinstance(horizons, list):
            for horizon in horizons:
                if not isinstance(horizon, str) or horizon not in HORIZONS:
                    errors.append(
                        f"bad-horizon: {horizon!r} is not a known horizon "
                        f"(one of {', '.join(HORIZONS)})")

        # --- splits: ordering + embargo
        errors.extend(_validate_splits(record))

    # --- variants
    errors.extend(_validate_variants(record, known_hypothesis_ids))
    return errors


def _validate_splits(record):
    errors = []
    splits = record.splits
    parsed = {}
    if not isinstance(splits, dict) or not splits:
        return ["required-field: splits is required"]
    for key in ("dev", "val", "holdout"):
        bounds = splits.get(key)
        if bounds is None:
            errors.append(f"within-window: {key} window is required")
            continue
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            errors.append(f"within-window: {key} window needs [lo, hi]")
            continue
        lo, hi = bounds
        if not _is_number(lo) or not _is_number(hi):
            errors.append(f"within-window: {key} window bounds must be numbers")
            continue
        if not float(lo) < float(hi):
            errors.append(f"within-window: {key} window needs lo < hi")
            continue
        parsed[key] = (float(lo), float(hi))

    if len(parsed) == 3:
        dev, val, hold = parsed["dev"], parsed["val"], parsed["holdout"]
        for first, second, label in ((dev[1], val[0], "dev_end"),
                                     (val[0], val[1], "val_start"),
                                     (val[1], hold[0], "holdout_start")):
            if first > second:
                errors.append(f"ordering: {label} {first} > {second}")

    horizons = record.horizons
    required_embargo = (LONG_HORIZON_EMBARGO_DAYS
                        if isinstance(horizons, list) and LONG_HORIZON in horizons
                        else SHORT_HORIZON_EMBARGO_DAYS)
    embargo = record.embargo_days
    if embargo is None:
        pass                    # already reported by the required-field pass
    elif not _is_number(embargo):
        errors.append("required-field: embargo_days must be a number")
        return errors
    else:
        embargo = float(embargo)
        if embargo < required_embargo:
            errors.append(f"embargo: embargo_days {embargo:g} < "
                          f"{required_embargo:g} required by horizons "
                          f"{horizons}")
    if len(parsed) == 3 and embargo is not None and _is_number(embargo):
        dev, val, hold = parsed["dev"], parsed["val"], parsed["holdout"]
        for gap, label in ((val[0] - dev[1], "dev/val"),
                           (hold[0] - val[1], "val/holdout")):
            if gap < embargo:
                errors.append(f"embargo: {label} gap {gap:g}d < {embargo:g}d")
    return errors


def _validate_variants(record, known_hypothesis_ids=None):
    errors = []
    variants = record.variants
    if variants is None:
        return []
    if not isinstance(variants, list):
        return ["bad-variants: variants must be a list"]
    seen = {}
    family = record.family if isinstance(record.family, list) else []
    for variant in variants:
        vid = getattr(variant, "variant_id", None)
        if not _present(vid):
            errors.append("required-field: variant_id is required")
            continue
        if not _present(getattr(variant, "hypothesis_id", None)):
            errors.append(f"required-field: variant {vid} hypothesis_id is "
                          "required")
        else:
            hid = variant.hypothesis_id
            if known_hypothesis_ids is not None and hid not in known_hypothesis_ids:
                errors.append(f"unknown-hypothesis: {hid!r} is not a known "
                              "hypothesis id")
            if family and hid not in family:
                errors.append(f"family-mismatch: variant {vid} cites "
                              f"hypothesis {hid!r}, which is not in the "
                              "declared family")
        result = getattr(variant, "result", None)
        if not isinstance(result, dict):
            errors.append(f"bad-result: variant {vid} result must be a dict")
            continue
        status = result.get("status")
        if status not in VARIANT_STATUSES:
            errors.append(f"unknown-variant-status: {status!r} is not a known "
                          "variant status")
        if status != "pending":
            n = result.get("n")
            if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
                errors.append(f"missing-denominator: variant {vid} result must "
                              "carry a denominator n")
        for entry in (getattr(variant, "evidence", None) or []):
            path = entry.get("path") if isinstance(entry, dict) else None
            sha = entry.get("sha256") if isinstance(entry, dict) else None
            if not _present(path):
                errors.append(f"required-field: variant {vid} evidence path "
                              "is required")
                continue
            if not isinstance(sha, str) or not SHA256_RE.match(sha):
                errors.append(f"bad-evidence-hash: variant {vid} evidence "
                              f"{path} needs a 64-hex sha256")
                continue
            actual = _resolve_evidence(path)
            if actual is None:
                errors.append(f"evidence-missing: variant {vid} evidence file "
                              f"not found: {path}")
            elif actual != sha:
                errors.append(f"evidence-hash-mismatch: variant {vid} evidence "
                              f"{path} sha256 {actual} != recorded {sha}")
        key = _result_key(result)
        if vid in seen:
            if seen[vid] != key:
                errors.append(f"variant-result-changed: variant {vid} appears "
                              "twice with different results")
        else:
            seen[vid] = key
    return errors


def register_variant(record: PreregRecord, variant: Variant, directory):
    """Register one tested variant; return the updated record.

    The registry is **append-only**: a new ``variant_id`` is appended (and
    written); the same id with an identical result is idempotent; the same
    id with a DIFFERENT result raises
    ``PreregError("variant-result-changed: ...")`` and changes nothing on
    disk.  The comparison is made against the record in hand AND against
    the stored file, so a result cannot be changed from another process.
    """
    incoming = _result_key(variant.result)
    existing = _find_variant(record, variant.variant_id)
    if existing is not None and _result_key(existing.result) != incoming:
        raise PreregError(
            f"variant-result-changed: variant {variant.variant_id!r} is "
            f"already registered with a different result; the variant "
            f"registry is append-only",
            errors=[f"variant-result-changed: variant "
                    f"{variant.variant_id!r} already registered with a "
                    f"different result"])
    stored = None
    stored_path = os.path.join(str(directory), f"{record.id}.json")
    if os.path.exists(stored_path):
        try:
            stored = load(directory, record.id)
        except (ValueError, OSError) as exc:
            # The file EXISTS but cannot be read as a record.  Treating it
            # as absent would let save() overwrite it and lose a
            # registered variant -- the append-only guard must refuse
            # instead of silently skipping the comparison.
            raise PreregError(
                f"stored-record-unreadable: {stored_path} exists but cannot "
                f"be read as a preregistration record ({exc}); refusing to "
                f"write over it",
                errors=[f"stored-record-unreadable: {stored_path} exists but "
                        f"cannot be read as a preregistration record"]) from exc
    if stored is not None:
        existing = _find_variant(stored, variant.variant_id)
        if existing is not None and _result_key(existing.result) != incoming:
            raise PreregError(
                f"variant-result-changed: variant {variant.variant_id!r} is "
                f"already registered in {directory} with a different result; "
                f"the variant registry is append-only",
                errors=[f"variant-result-changed: variant "
                        f"{variant.variant_id!r} already registered with a "
                        f"different result"])
    existing = _find_variant(record, variant.variant_id)
    if existing is not None:
        # Identical result: idempotent.  Write only when the stored file is
        # missing or does not carry the same variants.
        if stored is None or _variants_key(stored.variants) != _variants_key(record.variants):
            save(record, directory)
        return record
    updated = replace(record,
                      variants=list(record.variants) + [variant],
                      updated_ts=int(time.time()))
    save(updated, directory)
    return updated


def _find_variant(record, variant_id):
    for variant in (getattr(record, "variants", None) or []):
        if getattr(variant, "variant_id", None) == variant_id:
            return variant
    return None
