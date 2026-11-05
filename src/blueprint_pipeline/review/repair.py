"""Bounded, auditable presentation repair.

Repairs adjust *presentation only* (label size, tag-only labels, line
weights). They never change the canonical model. At most
``review.max_repair_cycles`` (≤ 2) automated cycles run; after that, or when
no deterministic adjustment exists for an issue, the result is escalated to
human review.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..rendering.scene import RenderOverrides

MAX_CYCLES_HARD_LIMIT = 2


def propose_adjustment(
    issues: list[dict[str, Any]], current: RenderOverrides
) -> tuple[RenderOverrides | None, list[str]]:
    """Map review/validation issues to one deterministic adjustment.

    Returns (new_overrides or None, notes). None means: no automatic repair
    is available — escalate to a human.
    """
    cats = {i["category"] for i in issues}
    notes: list[str] = []
    new = current
    if cats & {"collision", "label_placement"}:
        if current.label_scale > 0.75:
            new = replace(new, label_scale=round(current.label_scale * 0.85, 4))
            notes.append("reduce label size by 15% to resolve collisions/placement")
        elif not current.prefer_tags:
            new = replace(new, prefer_tags=True)
            notes.append("switch room labels to schedule tags")
    elif "legibility" in cats and current.label_scale < 1.6:
        new = replace(new, label_scale=round(min(1.6, current.label_scale * 1.2), 4))
        notes.append("increase label size by 20% for legibility")
    if cats & {"line_weight", "hierarchy"} and current.line_weight_scale < 2.0:
        new = replace(new, line_weight_scale=round(min(2.0, current.line_weight_scale * 1.25), 4))
        notes.append("increase line weights by 25%")
    unrepairable = sorted(
        cats - {"collision", "label_placement", "legibility", "line_weight", "hierarchy"}
    )
    if unrepairable:
        notes.append(f"no automatic repair for: {unrepairable}")
    if new == current:
        return None, notes or ["no adjustment available"]
    return new, notes
