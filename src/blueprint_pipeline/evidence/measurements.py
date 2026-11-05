"""Length parsing.

Raw transcriptions are never rewritten. This module derives a *candidate*
numeric value in millimetres alongside the raw text, and reports uncertainty
instead of guessing: a number without a unit is not converted, and a comma that
could be a decimal or thousands separator is not interpreted.
"""

from __future__ import annotations

import re

_UNITS_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0}
_METRIC = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)\s*\.?\s*$", re.IGNORECASE)
_FEET_INCH = re.compile(
    r"^\s*(\d+)\s*(?:'|ft|feet|′|’)\s*(?:-?\s*(\d+(?:\.\d+)?)\s*"
    r"(?:\"|in|inch|inches|″|”|'')?)?\s*$",
    re.IGNORECASE,
)
_INCH = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:\"|in|inch|inches|″|”|'')\s*$", re.IGNORECASE)
_BARE = re.compile(r"^\s*\d+(?:\.\d+)?\s*$")


def parse_length(raw: str | None) -> tuple[float | None, str | None, str | None]:
    """Return ``(value_mm, unit, uncertainty)``.

    ``value_mm`` is ``None`` whenever the text cannot be converted without an
    assumption; ``uncertainty`` then says why.
    """
    if raw is None:
        return None, None, "no text"
    s = raw.strip()
    if not s:
        return None, None, "no text"
    if "," in s:
        return None, None, "comma is ambiguous (decimal or thousands separator); not converted"
    m = _METRIC.match(s)
    if m:
        unit = m.group(2).lower()
        return round(float(m.group(1)) * _UNITS_MM[unit], 1), unit, None
    m = _FEET_INCH.match(s)
    if m:
        feet = int(m.group(1))
        inches = float(m.group(2)) if m.group(2) else 0.0
        if inches >= 12:
            return None, "ft-in", "inches component >= 12; not converted"
        return round(feet * 304.8 + inches * 25.4, 1), "ft-in", None
    m = _INCH.match(s)
    if m:
        return round(float(m.group(1)) * 25.4, 1), "in", None
    if _BARE.match(s):
        return None, None, "number without unit; not converted"
    if re.search(r"\d\s*[x×]\s*\d", s, re.IGNORECASE):
        return None, None, "compound dimension; record each axis as its own observation"
    return None, None, "unrecognised length format"


def values_agree(a: float, b: float, *, abs_tol_mm: float = 50.0, rel_tol: float = 0.02) -> bool:
    return abs(a - b) <= max(abs_tol_mm, rel_tol * max(abs(a), abs(b)))
