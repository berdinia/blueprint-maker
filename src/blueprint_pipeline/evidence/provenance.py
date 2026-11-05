"""Authoritative source identity.

The caller (via the source register) determines ``source_id``. A model may echo
a source ID; that echo is recorded for audit and compared, but it is always
overwritten by the authoritative value before anything else reads the payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProvenanceResult:
    authoritative_source_id: str
    model_reported_source_id: str | None
    source_id_mismatch: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "authoritative_source_id": self.authoritative_source_id,
            "model_reported_source_id": self.model_reported_source_id,
            "source_id_mismatch": self.source_id_mismatch,
            "resolved_from": "manifest",
        }


def enforce_source_identity(
    parsed: dict[str, Any], expected_source_id: str
) -> tuple[dict[str, Any], ProvenanceResult, list[dict[str, str]]]:
    """Return a copy of ``parsed`` carrying the authoritative ``source_id``.

    The model's own value is preserved in the returned :class:`ProvenanceResult`
    and any disagreement is reported as a validation warning.
    """
    warnings: list[dict[str, str]] = []
    model_value = parsed.get("source_id")
    model_sid: str | None
    if model_value is None:
        model_sid = None
    elif isinstance(model_value, str):
        model_sid = model_value
    else:
        model_sid = repr(model_value)
        warnings.append(
            {
                "code": "SOURCE_ID_INVALID_TYPE",
                "message": f"model returned a non-string source_id ({type(model_value).__name__})",
            }
        )
    mismatch = model_sid not in (None, expected_source_id)
    if mismatch:
        warnings.append(
            {
                "code": "SOURCE_ID_MISMATCH",
                "message": (
                    f"model reported source_id {model_sid!r}; authoritative value "
                    f"{expected_source_id!r} from the source register was applied"
                ),
            }
        )
    out = dict(parsed)
    out["source_id"] = expected_source_id
    return out, ProvenanceResult(expected_source_id, model_sid, mismatch), warnings
