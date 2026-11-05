"""Structured-output parsing for local model responses.

Strict by design: the response must contain exactly one JSON object. Two
tolerances are applied and recorded as notes, never silently:

* a ``<think>…</think>`` reasoning block emitted by some local models is removed;
* a single surrounding Markdown code fence is removed.

Leading or trailing prose is *not* discarded — it is a parse failure, because a
model that adds prose has not followed the output contract.
"""

from __future__ import annotations

import json
import re
from typing import Any

_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def parse_json_object(text: str) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """Return ``(object, error, notes)``; exactly one of object/error is set."""
    notes: list[str] = []
    if text is None or not text.strip():
        return None, "empty content", notes
    s = text
    m = _THINK.match(s)
    if m:
        s = s[m.end() :]
        notes.append("removed leading <think> block")
    m = _FENCE.match(s)
    if m:
        s = m.group(1)
        notes.append("removed surrounding code fence")
    s = s.strip()
    if not s:
        return None, "no content after removing reasoning/fence", notes
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON at line {exc.lineno} col {exc.colno}: {exc.msg}", notes
    if not isinstance(obj, dict):
        return None, f"top-level JSON value is {type(obj).__name__}, expected object", notes
    return obj, None, notes
