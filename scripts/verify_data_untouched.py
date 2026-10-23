#!/usr/bin/env python3
"""Read-only manifest of ``data/`` to prove the archive is never mutated.

Writes a manifest listing ``(relative path, size, sha256)`` for every regular
file under ``data/`` to a path YOU specify (the default is under
``/tmp/opencode/alt-radar/`` so the script never writes into ``data/``
without explicit owner action). The ``--check`` mode compares the on-disk tree
against that manifest.

Write-detection policy during ``--check``:
  * Files that were present when the manifest was written must NOT change or
    disappear. A change is a FAIL.
  * New files are allowed ONLY under the collector's append-only paths:
    ``data/bars``, ``data/prices``, ``data/funding``, ``data/funding_settlements``.
    A new file elsewhere is a FAIL. This distinguishes legitimate live
    collector activity (new bars/prices) from unauthorized writes to protected
    databases or to ``data/`` outside the append-only roots.

The manifest file itself and any files under ``data/research/`` (including
the planned research database ``altradar-research.db``) are EXCLUDED from
scan so they can be created and modified normally by the research tooling.

Usage:
    python3 scripts/verify_data_untouched.py                          # write manifest
    python3 scripts/verify_data_untouched.py --check                  # verify manifest
    python3 scripts/verify_data_untouched.py --base-dir data --manifest /tmp/manifest.json --check
"""
import argparse
import hashlib
import json
import os
import sys
import time

DEFAULT_BASE_DIR = os.path.join("data")
# Default to /tmp so the script never writes under data/ without explicit
# owner action. Use --manifest to place it elsewhere.
DEFAULT_MANIFEST = os.path.join("/tmp", "opencode", "alt-radar",
                                ".untouched-manifest.json")

# Paths where the collector appends new files. New files here are allowed
# between manifest writes; modifications or deletions of existing files are not.
APPEND_ONLY_PREFIXES = ("data/bars", "data/prices", "data/funding",
                        "data/funding_settlements")

# Paths that the research tooling owns and that are excluded from scanning.
RESEARCH_OWNED = ("data/research",)


def _sha256(path, buf_size=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(buf_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_research_owned(rel):
    return rel.startswith(RESEARCH_OWNED[0] + "/") or rel in RESEARCH_OWNED


def _walk(root):
    """Yield (relpath, size, sha256) for every regular file under root.

    Excludes the manifest file itself, files under data/research/ (the research
    DB and the manifest live there and are owned by the research tooling), and
    Python bytecode caches.
    """
    root = os.path.realpath(root)
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in sorted(files):
            full = os.path.join(dirpath, f)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if _is_research_owned(rel):
                continue
            if rel.endswith("/.untouched-manifest.json"):
                continue
            if "__pycache__" in rel or ".pytest_cache" in rel:
                continue
            out.append((rel, os.path.getsize(full), _sha256(full)))
    return out


def _is_new_allowed(rel):
    """True if ``rel`` is a newly-allowed file in an append-only path."""
    for prefix in APPEND_ONLY_PREFIXES:
        if rel.startswith(prefix + "/"):
            return True
    return False


def write_manifest(base_dir, manifest_path):
    entries = _walk(base_dir)
    os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
    data = {
        "base_dir": os.path.relpath(base_dir),
        "created": int(time.time()),
        "counts": len(entries),
        "append_only_prefixes": list(APPEND_ONLY_PREFIXES),
        "files": [
            {"path": rel, "size": size, "sha256": sha}
            for rel, size, sha in entries
        ],
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return data


def check_manifest(base_dir, manifest_path):
    if not os.path.exists(manifest_path):
        print(f"FAIL: manifest not found: {manifest_path}", file=sys.stderr)
        print("      run without --check first to create it", file=sys.stderr)
        return 1

    with open(manifest_path, encoding="utf-8") as fh:
        recorded = json.load(fh)

    old = {f["path"]: (f["size"], f["sha256"]) for f in recorded["files"]}
    current = _walk(base_dir)
    now = {rel: (size, sha) for rel, size, sha in current}

    problems = []

    # Deleted files (excluding research-owned paths which are never in `old`)
    for rel in sorted(old):
        if rel not in now:
            problems.append(f"DELETED: {rel}")

    # Modified files
    for rel in sorted(old):
        if rel in now:
            o_size, o_sha = old[rel]
            n_size, n_sha = now[rel]
            if o_sha != n_sha:
                problems.append(f"MODIFIED: {rel} (sha256 changed)")
            elif o_size != n_size:
                problems.append(f"MODIFIED: {rel} (size changed)")

    # New files — only allowed in append-only paths
    for rel, _size, _sha in current:
        if rel not in old:
            if _is_new_allowed(rel):
                continue
            problems.append(f"UNEXPECTED NEW FILE: {rel}")

    if problems:
        print(f"FAIL: {len(problems)} data tree change(s):")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"PASS: {len(now)} files unchanged (manifest {len(old)} entries)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-dir", default=DEFAULT_BASE_DIR,
                    help="directory to scan (default: %(default)s)")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST,
                    help="manifest path (default: %(default)s)")
    ap.add_argument("--check", action="store_true",
                    help="verify against an existing manifest instead of writing one")
    args = ap.parse_args(argv)

    if args.check:
        return check_manifest(args.base_dir, args.manifest)
    data = write_manifest(args.base_dir, args.manifest)
    print(f"Wrote {data['counts']} file entries to {args.manifest}")
    print(f"Append-only prefixes: {', '.join(data['append_only_prefixes'])}")
    print(f"Excluded: research-owned paths ({', '.join(RESEARCH_OWNED)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
