#!/usr/bin/env python3
"""Static audit: which modules can open network connections?

Fails (exit 1) if any module outside the allowlist imports a networking
library, or if any source file contains a non-loopback URL literal. Run it on
every change received from outside the private environment:

    python scripts/audit_network_usage.py src
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

NETWORK_MODULES = {
    "http",
    "http.client",
    "urllib",
    "urllib.request",
    "urllib3",
    "requests",
    "httpx",
    "aiohttp",
    "socket",
    "ssl",
    "ftplib",
    "smtplib",
    "websocket",
    "websockets",
    "grpc",
    "boto3",
    "openai",
    "anthropic",
    "google",
    "azure",
}
PURE_MODULES = {"urllib.parse"}  # string parsing only; opens no connections
# module path suffix -> networking imports it may use, and why
ALLOWED = {
    "inference/client.py": {"http.client", "socket"},  # the single local inference client
    "testing/fake_server.py": {"http.server", "http"},  # loopback-only test server
    "orchestration/lock.py": {"socket"},  # socket.gethostname() for the lock record
}
URL = re.compile(
    r"""https?://(?!127\.0\.0\.1|localhost|\[::1\]|example\.(?:org|com)|json-schema\.org|www\.w3\.org)[A-Za-z0-9.-]+"""
)


def main(root: str) -> int:
    problems = []
    for path in sorted(Path(root).rglob("*.py")):
        rel = path.as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                used |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                used.add(node.module)
        net = {
            m
            for m in used
            if m not in PURE_MODULES
            and (m in NETWORK_MODULES or m.split(".")[0] in NETWORK_MODULES)
        }
        allowed = next((v for k, v in ALLOWED.items() if rel.endswith(k)), set())
        extra = {
            m
            for m in net
            if m not in allowed and m.split(".")[0] not in {a.split(".")[0] for a in allowed}
        }
        if extra:
            problems.append(f"{rel}: network imports {sorted(extra)}")
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if URL.search(line):
                problems.append(f"{rel}:{i}: non-loopback URL literal")
    for p in problems:
        print(p)
    print(f"network audit: {'FAIL' if problems else 'PASS'} ({len(problems)} finding(s))")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "src"))
