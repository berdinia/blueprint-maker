#!/usr/bin/env python3
"""Local, offline privacy scanner for an export directory. It never uploads anything.

The sensitive-term dictionary is PRIVATE and is never shipped: pass it with
``--terms <file.json>`` or ``BP_SENSITIVE_TERMS``. The file may be a JSON list
or ``{"terms": [...]}``. Without a dictionary the scan is a no-op and says so.

Exit codes: 0 no hits, 1 hits found (classify before release), 2 bad usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BINARY_EXT = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".zip",
    ".tar",
    ".tgz",
    ".gz",
    ".bz2",
    ".dmg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".tiff",
    ".pdf",
    ".pyc",
    ".so",
    ".dylib",
    ".dll",
}
SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
}


def load_terms(path: str | None) -> list[str]:
    if not path:
        return []
    p = Path(path)
    if not p.is_file():
        sys.stderr.write(f"terms file not found: {p}\n")
        sys.exit(2)
    data = json.loads(p.read_text(encoding="utf-8"))
    terms = data.get("terms", []) if isinstance(data, dict) else data
    return [str(t) for t in terms if t]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("directory")
    ap.add_argument("--terms", help="private JSON terms dictionary")
    args = ap.parse_args()
    terms = load_terms(args.terms or os.environ.get("BP_SENSITIVE_TERMS"))
    if not terms:
        sys.stderr.write("[warn] no private terms dictionary supplied; scan is a no-op\n")
    hits = 0
    for root, dirs, files in os.walk(args.directory):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            path = Path(root, name)
            if path.suffix.lower() in BINARY_EXT:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                for term in terms:
                    if term in line:
                        hits += 1
                        # Report location and term index only: never echo private text.
                        rel = path.relative_to(args.directory)
                        print(f"HIT: {rel}:{i} term#{terms.index(term)}")
    print(f"TOTAL HITS: {hits}")
    return 0 if hits == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
