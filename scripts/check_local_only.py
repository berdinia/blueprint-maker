#!/usr/bin/env python3
"""Local, offline privacy scanner for the clean export.

Part of the Phase 7 privacy validation. It NEVER uploads anything.

The sensitive-term dictionary is loaded from a PRIVATE file (kept under
PRIVATE_AUDIT_ROOT, never shipped in the export). No real private terms are
hard-coded in this scanner so that shipping the scanner itself leaks nothing.

Usage:
    python scripts/check_local_only.py <directory> --terms <path-to-private-terms.json>
    python scripts/check_local_only.py <directory>        # uses BP_SENSITIVE_TERMS env var

Exit codes:
    0  no hits
    1  one or more hits (review / classify before release)
    2  misuse / bad arguments
"""

import os
import sys
import json
import argparse
from typing import List

# No real private terms are hard-coded in the shipped scanner. A clean export
# therefore ships with an EMPTY built-in list; real terms arrive via --terms
# (a private dictionary under PRIVATE_AUDIT_ROOT) or the BP_SENSITIVE_TERMS env
# var. The built-in example below is empty on purpose.
EXAMPLE_SENSITIVE_TERMS: List[str] = []


def _load_terms() -> List[str]:
    """Load term dictionary from BP_SENSITIVE_TERMS, default to empty build-in."""
    terms = list(EXAMPLE_SENSITIVE_TERMS)
    p = os.environ.get("BP_SENSITIVE_TERMS")
    if p and os.path.exists(p):
        try:
            with open(p) as f:
                data = json.load(f)
            # Accept either a flat list or a dict with "terms".
            if isinstance(data, dict) and "terms" in data:
                terms = list(data.get("terms", []))
            elif isinstance(data, list):
                terms = data
        except Exception as e:
            sys.stderr.write(f"Failed to load terms from {p}: {e}\n")
    return [t for t in terms if t]


# Binary extensions to skip in a text scan.
BINARY_EXT = {
    ".db", ".sqlite", ".sqlite3", ".log", ".zip", ".tar", ".tgz",
    ".gz", ".bz2", ".dmg", ".png", ".jpg", ".jpeg", ".gif", ".bmp",
    ".tiff", ".pdf", ".dxf", ".svg", ".pyc", ".o", ".a", ".so",
    ".dll", ".dylib",
}

# Directories that are excluded from an export by policy.
SKIP_DIRS = {
    ".git", ".DS_Store", "__pycache__", ".venv", "venv", "cache", "tmp",
    ".ruff_cache", ".mypy_cache", ".pytest_cache", "node_modules",
}


def scan_file(path, terms, relroot) -> list:
    """Return a list of (line_no, matched_term) hits for this file."""
    hits = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, 1):
                for term in terms:
                    if term in line:
                        hits.append((i, term, line.rstrip()[:120]))
    except Exception:
        return hits
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("directory", help="Export directory to scan")
    ap.add_argument("--terms", help="Path to a JSON terms dictionary file")
    args = ap.parse_args()
    term_path = args.terms or os.environ.get("BP_SENSITIVE_TERMS")

    terms = _load_terms()
    if term_path and os.path.exists(term_path):
        with open(term_path) as f:
            data = json.load(f)
        if isinstance(data, dict) and "terms" in data:
            terms = list(data.get("terms", []))
        elif isinstance(data, list):
            terms = data
        terms = [t for t in terms if t]
    else:
        sys.stderr.write("[warn] no private terms dictionary supplied "
                         "(--terms / BP_SENSITIVE_TERMS); scan is a no-op "
                         "on synthetic terms.\n")

    target = args.directory
    all_hits = []
    for root, dirs, files in os.walk(target):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            fpath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext in BINARY_EXT:
                continue
            hits = scan_file(fpath, terms, target)
            for line_no, term, ctx in hits:
                rel = os.path.relpath(fpath, target)
                print(f"HIT: {rel}:{line_no} -> '{term}' in: {ctx}")
                all_hits.append((rel, line_no, term))

    print(f"\nTOTAL HITS: {len(all_hits)}")
    sys.exit(0 if not all_hits else 1)


if __name__ == "__main__":
    main()
