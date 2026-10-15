"""WS5 hypothesis registry reader + validator (stdlib only).

Reads ``docs/hypotheses/HYD-####-slug.md`` front-matter (flat-YAML subset)
and validates entries against the split-discipline + status rules in
``docs/superpowers/specs/2026-10-15-hypothesis-registry-design.md``.

Read-only: never writes, never deletes.
"""

import datetime
import math
import os
import re

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ID_RE = re.compile(r"^HYD-[0-9]{4}$")
INT_RE = re.compile(r"^[+-]?[0-9]+$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

STATUSES = ("proposed", "registered", "testing", "validated", "rejected")
HORIZONS = ("1h", "4h", "24h", "7d")

BASE_FIELDS = ("id", "title", "status", "prediction")
REGISTERED_EXTRA = ("universe", "horizon", "dev_start", "dev_end",
                    "val_start", "val_end", "holdout_start", "metric",
                    "threshold", "denominator", "costs", "comparisons")
TESTING_EXTRA = ("rule_version", "config_hash")

DATE_FIELDS = ("dev_start", "dev_end", "val_start", "val_end",
               "holdout_start", "holdout_end")


def _unquote(value):
    if (isinstance(value, str) and len(value) >= 2
            and value[0] == value[-1] and value[0] in ("'", '"')):
        return value[1:-1]
    return value


def _safe_repr(value):
    """repr() that cannot raise on oversized ints (digit-cap ValueError)."""
    try:
        return repr(value)
    except (ValueError, OverflowError):
        return f"<{type(value).__name__} too large to display>"


def _present(value):
    """A value counts as present unless None or empty/whitespace (post-quote-strip)."""
    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return len(_unquote(value).strip()) > 0
    return True


def _as_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            num = float(value)
        except (ValueError, OverflowError):
            # Large Python ints overflow float(): not a usable number,
            # never an exception out of validation.
            return None
        return num if math.isfinite(num) else None
    if isinstance(value, str):
        text = _unquote(value).strip()
        if INT_RE.match(text):
            try:
                return float(int(text))
            except (ValueError, OverflowError):
                return None
        try:
            num = float(text)
        except (ValueError, OverflowError):
            return None
        return num if math.isfinite(num) else None
    return None


def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = _unquote(value).strip()
        if INT_RE.match(text):
            try:
                return int(text)
            except (ValueError, OverflowError):
                return None
    return None


def _parse_date(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        if isinstance(value, datetime.datetime):
            return value.date()
        return value
    if not isinstance(value, str):
        return None
    text = _unquote(value).strip()
    if not DATE_RE.match(text):
        return None
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_block(filename, lines, close_idx, errors):
    """Parse flat-mapping lines[1:close_idx]; file line numbers start at 1."""
    mapping = {}
    n = len(lines)
    for i in range(1, close_idx):
        raw = lines[i]
        lineno = i + 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in raw:
            errors.append(f"parse: {filename}:{lineno}: tabs not supported")
            continue
        if raw[0] in (" ",):
            errors.append(f"parse: {filename}:{lineno}: indentation not supported")
            continue
        if stripped == "-" or stripped.startswith("- "):
            errors.append(f"parse: {filename}:{lineno}: lists not supported")
            continue
        if ":" not in stripped:
            errors.append(f"parse: {filename}:{lineno}: missing ': ' separator")
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        if not KEY_RE.match(key):
            errors.append(f"parse: {filename}:{lineno}: invalid key: {key}")
            continue
        if key in mapping:
            errors.append(f"parse: {filename}:{lineno}: duplicate key: {key}")
            continue
        value = value.strip()
        if value == "":
            nxt = None
            for j in range(i + 1, close_idx):
                s = lines[j].strip()
                if not s or s.startswith("#"):
                    continue
                nxt = lines[j]
                break
            if nxt is not None and nxt[0] in (" ", "\t"):
                errors.append(
                    f"parse: {filename}:{lineno}: nested value not supported")
                continue
            mapping[key] = ""
            continue
        if value[0] in ("|", ">") and (len(value) == 1 or value[1] in (" ", "\t")):
            errors.append(
                f"parse: {filename}:{lineno}: multiline values not supported")
            continue
        if value[0] in ("&", "*"):
            errors.append(f"parse: {filename}:{lineno}: anchors not supported")
            continue
        if value[0] == "[":
            errors.append(f"parse: {filename}:{lineno}: lists not supported")
            continue
        if value[0] in ("'", '"'):
            if len(value) < 2 or value[-1] != value[0]:
                errors.append(
                    f"parse: {filename}:{lineno}: unterminated quoted value")
                continue
            inner = value[1:-1]
            mapping[key] = _coerce_scalar(inner)
            continue
        cut = value.find(" #")
        if cut != -1:
            value = value[:cut].strip()
        mapping[key] = _coerce_scalar(value)
    return mapping


def _coerce_scalar(text):
    if INT_RE.match(text):
        try:
            return int(text)
        except (ValueError, OverflowError):
            # Oversized digit strings (int-string conversion cap):
            # keep the raw text — numeric consumers degrade to
            # "not a number" instead of raising out of validation.
            return text
    try:
        return float(text)
    except (ValueError, OverflowError):
        return text


def _parse_file(filename, raw_bytes):
    """Return (entry|None, parse_errors). Never raises on bad content."""
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None, [f"parse: {filename}:1: file is not valid UTF-8"]
    if "\r" in text:
        return None, [f"parse: {filename}:1: CRLF line endings not supported"]
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, [f"parse: {filename}:1: missing front-matter block"]
    close_idx = None
    for j in range(1, len(lines)):
        if lines[j].strip() == "---":
            close_idx = j
            break
    if close_idx is None:
        return None, [f"parse: {filename}:1: unclosed front-matter block"]
    errors = []
    mapping = _parse_block(filename, lines, close_idx, errors)
    if errors:
        return None, errors
    mapping["_source_file"] = filename
    mapping["_body"] = "\n".join(lines[close_idx + 1:])
    return mapping, []


def _iter_markdown_files(directory):
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return
    for name in names:
        if not name.endswith(".md"):
            continue
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            yield name, path


def load_registry(directory):
    """Parse front-matter of every ``*.md`` file; skip files with parse errors."""
    entries = []
    for filename, path in _iter_markdown_files(directory):
        with open(path, "rb") as fh:
            raw = fh.read()
        entry, _ = _parse_file(filename, raw)
        if entry is not None:
            entries.append(entry)
    return entries


def validate(entry):
    """Single-entry rules 1-5. Returns ``<rule>: <detail>`` strings; [] = valid."""
    if not isinstance(entry, dict):
        entry = {}
    errors = []
    fields = {k: v for k, v in entry.items()
              if not (isinstance(k, str) and k.startswith("_"))}

    status_raw = fields.get("status")
    status = _unquote(status_raw).strip() if isinstance(status_raw, str) else None
    if not status:
        errors.append("required-field: status is required")
        required = list(BASE_FIELDS)
    elif status not in STATUSES:
        errors.append(f"unknown-status: {_safe_repr(status_raw)} is not a known status")
        required = list(BASE_FIELDS)
    elif status == "proposed":
        required = list(BASE_FIELDS)
    elif status == "registered":
        required = list(BASE_FIELDS) + list(REGISTERED_EXTRA)
    else:
        required = (list(BASE_FIELDS) + list(REGISTERED_EXTRA)
                    + list(TESTING_EXTRA))

    for field in required:
        if field in ("id", "threshold", "comparisons", "horizon"):
            continue
        if not _present(fields.get(field)):
            errors.append(f"required-field: {field} is required")

    id_raw = fields.get("id")
    if not _present(id_raw):
        errors.append("required-field: id is required")
    elif not (isinstance(id_raw, str) and ID_RE.match(_unquote(id_raw).strip())):
        errors.append(f"bad-id: {_safe_repr(id_raw)} does not match ^HYD-[0-9]{{4}}$")

    if "threshold" in required:
        if not _present(fields.get("threshold")):
            errors.append("required-field: threshold is required")
        elif _as_number(fields.get("threshold")) is None:
            errors.append(
                f"required-field: threshold {_safe_repr(fields.get('threshold'))} "
                "must be numeric")

    if "comparisons" in required:
        comp = _as_int(fields.get("comparisons")) \
            if _present(fields.get("comparisons")) else None
        if comp is None or comp < 1:
            errors.append(
                f"required-field: comparisons {_safe_repr(fields.get('comparisons'))} "
                "must be an integer >= 1")

    horizon = None
    if "horizon" in required:
        horizon_raw = fields.get("horizon")
        horizon = _unquote(horizon_raw).strip() \
            if isinstance(horizon_raw, str) else None
        if not horizon:
            errors.append("required-field: horizon is required")
            horizon = None
        elif horizon not in HORIZONS:
            errors.append(
                f"required-field: horizon {_safe_repr(horizon_raw)} must be one of "
                "1h, 4h, 24h, 7d")
            horizon = None

    parsed = {}
    for field in DATE_FIELDS:
        value = fields.get(field)
        if not _present(value):
            continue
        day = _parse_date(value)
        if day is None:
            errors.append(
                f"bad-date: {field} {_safe_repr(value)} is not a YYYY-MM-DD calendar day")
        else:
            parsed[field] = day

    for start, end, label in (("dev_start", "dev_end", "dev"),
                              ("val_start", "val_end", "val")):
        start_on = _present(fields.get(start))
        end_on = _present(fields.get(end))
        if start_on != end_on:
            errors.append(
                f"within-window: {label} window needs both start and end")
        elif (start in parsed and end in parsed
                and not parsed[start] < parsed[end]):
            errors.append(
                f"within-window: {start} {parsed[start]} >= "
                f"{end} {parsed[end]}")
    if _present(fields.get("holdout_end")) and not _present(fields.get("holdout_start")):
        errors.append("within-window: holdout_end without holdout_start")
    elif ("holdout_start" in parsed and "holdout_end" in parsed
            and not parsed["holdout_start"] < parsed["holdout_end"]):
        errors.append(
            f"within-window: holdout_start {parsed['holdout_start']} >= "
            f"holdout_end {parsed['holdout_end']}")

    for first, second in (("dev_end", "val_start"),
                          ("val_start", "val_end"),
                          ("val_end", "holdout_start")):
        if (first in parsed and second in parsed
                and parsed[first] > parsed[second]):
            errors.append(
                f"ordering: {first} {parsed[first]} > "
                f"{second} {parsed[second]}")

    # Embargo needs a resolved horizon. With a missing/invalid horizon
    # and complete window pairs, fail closed: a silent 1-day default
    # would let entries pass embargo validation that was never evaluated.
    # (Missing/invalid horizon is separately a required-field error for
    # registered+; bare proposed entries with no windows pass vacuously.)
    embargo_pairs = (("dev_end", "val_start", "dev/val"),
                     ("val_end", "holdout_start", "val/holdout"))
    if horizon in HORIZONS:
        embargo_days = 7 if horizon == "7d" else 1
        for first, second, label in embargo_pairs:
            if first in parsed and second in parsed:
                gap = (parsed[second] - parsed[first]).days
                if gap < embargo_days:
                    errors.append(
                        f"embargo: {label} gap {gap}d < {embargo_days}d")
    else:
        for first, second, label in embargo_pairs:
            if first in parsed and second in parsed:
                errors.append(
                    f"embargo: {label} cannot evaluate "
                    "without a valid horizon")

    return errors


def load_and_validate(directory):
    """Return ``(entries, errors)`` aggregating parse errors, per-entry
    violations (prefixed with their file), and cross-file duplicate ids."""
    entries = []
    errors = []
    seen = {}
    for filename, path in _iter_markdown_files(directory):
        with open(path, "rb") as fh:
            raw = fh.read()
        entry, parse_errors = _parse_file(filename, raw)
        errors.extend(parse_errors)
        if entry is None:
            continue
        entries.append(entry)
        for violation in validate(entry):
            errors.append(f"{filename}: {violation}")
        entry_id = entry.get("id")
        # Group by the same normalized id validate() checks (unquoted +
        # stripped), so a quoted/padded spelling still collides instead
        # of silently missing the duplicate.
        if isinstance(entry_id, str):
            group_key = _unquote(entry_id).strip()
            if group_key:
                seen.setdefault(group_key, []).append(filename)
    for entry_id in sorted(seen):
        files = sorted(seen[entry_id])
        if len(files) > 1:
            errors.append(
                f"duplicate-id: {entry_id} in {', '.join(files)}")
    return entries, errors
