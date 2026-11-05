"""The shipped source must not gain network access outside the inference client."""

from __future__ import annotations

import subprocess
import sys

from conftest import REPO


def _audit(path):
    return subprocess.run(
        [sys.executable, str(REPO / "scripts/audit_network_usage.py"), str(path)],
        capture_output=True,
        text=True,
    )


def test_source_tree_passes_network_audit():
    r = _audit(REPO / "src")
    assert r.returncode == 0, r.stdout


def test_audit_flags_new_network_code(tmp_path):
    (tmp_path / "sneaky.py").write_text("import requests\nURL = 'https://ocr.example.net/upload'\n")
    r = _audit(tmp_path)
    assert r.returncode == 1
    assert "network imports ['requests']" in r.stdout and "non-loopback URL" in r.stdout
