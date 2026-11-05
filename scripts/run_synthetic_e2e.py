#!/usr/bin/env python3
"""Compatibility wrapper: synthetic end-to-end run.

Equivalent to ``blueprint run-synthetic``. Unlike the previous script it does
not overwrite a tracked report in the repository root; pass ``--report FILE``.
"""

import sys

from blueprint_pipeline.cli import main

if __name__ == "__main__":
    sys.exit(main(["run-synthetic", *sys.argv[1:]]))
