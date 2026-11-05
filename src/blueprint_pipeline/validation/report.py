"""Validation report: machine-readable JSON plus a concise Markdown summary."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .checks import Check, overall_status


def build_report(checks: list[Check], artifacts: dict[str, str]) -> dict[str, Any]:
    counts = Counter(c.status for c in checks)
    return {
        "schema": "blueprint.validation-report",
        "schema_version": "1.0.0",
        "status": overall_status(checks),
        "counts": {k: counts.get(k, 0) for k in ("PASS", "WARN", "FAIL", "SKIP")},
        "artifacts": dict(sorted(artifacts.items())),
        "checks": [c.to_dict() for c in checks],
    }


def report_markdown(report: dict[str, Any]) -> str:
    c = report["counts"]
    lines = [
        "# Mechanical validation",
        "",
        f"**Status: {report['status']}** — PASS {c['PASS']}, WARN {c['WARN']}, "
        f"FAIL {c['FAIL']}, SKIP {c['SKIP']} (SKIP never counts as PASS)",
        "",
        "| Category | Check | Status | Detail |",
        "|---|---|---|---|",
    ]
    order = {"FAIL": 0, "WARN": 1, "SKIP": 2, "PASS": 3}
    for ch in sorted(
        report["checks"], key=lambda x: (order[x["status"]], x["category"], x["check_id"])
    ):
        detail = ch["detail"].replace("|", "/").replace("\n", " ")
        if len(detail) > 160:
            detail = detail[:157] + "..."
        lines.append(f"| {ch['category']} | {ch['check_id']} | {ch['status']} | {detail} |")
    lines += ["", "## Artifacts", ""]
    for name, sha in report["artifacts"].items():
        lines.append(f"- `{name}` sha256 `{sha[:16]}…`")
    return "\n".join(lines) + "\n"
