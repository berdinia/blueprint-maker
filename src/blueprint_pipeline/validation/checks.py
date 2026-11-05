"""Check result primitives shared by all validators."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Check:
    check_id: str
    category: str
    status: str  # PASS | WARN | FAIL | SKIP
    detail: str
    required: bool = True
    refs: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not d["refs"]:
            d.pop("refs")
        if not d["data"]:
            d.pop("data")
        return d


class CheckList:
    def __init__(self, category: str) -> None:
        self.category = category
        self.items: list[Check] = []

    def add(
        self,
        check_id: str,
        ok: bool,
        detail: str,
        *,
        warn_only: bool = False,
        required: bool = True,
        refs: list[str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Check:
        status = "PASS" if ok else ("WARN" if warn_only else "FAIL")
        c = Check(check_id, self.category, status, detail, required, refs or [], data or {})
        self.items.append(c)
        return c

    def skip(self, check_id: str, detail: str, *, required: bool = True) -> Check:
        c = Check(check_id, self.category, "SKIP", detail, required)
        self.items.append(c)
        return c


def overall_status(checks: list[Check]) -> str:
    """FAIL if any required check failed; PARTIAL if any required check was
    skipped or any check warned; PASS otherwise. SKIP never counts as PASS."""
    if any(c.status == "FAIL" and c.required for c in checks):
        return "FAIL"
    if any(c.status == "SKIP" and c.required for c in checks) or any(
        c.status in ("WARN", "FAIL") for c in checks
    ):
        return "PARTIAL"
    return "PASS" if checks else "BLOCKED"
