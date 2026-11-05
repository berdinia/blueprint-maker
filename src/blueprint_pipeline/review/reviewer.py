"""Independent visual review of the rendered PNG.

The reviewer judges presentation only. Its record keeps transport, parse and
schema status separate from the review verdict, and binds the verdict to the
SHA-256 of the exact PNG that was reviewed.

Verdict rules
-------------
* HTTP 200 is not review success; parsed JSON is not review success.
* Any failure before a schema-valid response → ``NOT_REVIEWED``.
* ``legible: false`` → ``FAIL`` regardless of the model's own verdict.
* Any in-scope issue of severity major/blocking → ``FAIL``.
* Issues in categories outside the permitted list are recorded under
  ``out_of_scope_items`` and ignored (the reviewer may not judge facts).
* A record whose ``reviewed_artifact_sha256`` differs from the current PNG is
  stale and must never be presented as current.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ..inference.evaluate import evaluate
from ..inference.types import InferenceStatus, TransportResult
from ..io.artifacts import ArtifactStore
from ..io.atomic import sha256_file
from ..validation.checks import Check

ALLOWED_CATEGORIES = (
    "legibility",
    "clipping",
    "collision",
    "hierarchy",
    "line_weight",
    "label_placement",
    "dimension_placement",
    "rendering_defect",
)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def interpret_review(
    transport: TransportResult,
    *,
    artifact_rel: str,
    artifact_sha256: str,
    cycle: int,
    model: str | None,
) -> dict[str, Any]:
    outcome = evaluate(transport, schema_name="review-response")
    st = outcome.stage_statuses()
    record: dict[str, Any] = {
        "schema": "blueprint.review-record",
        "schema_version": "1.0.0",
        "reviewer": {"kind": "LOCAL_MODEL", "model": model},
        "request_id": transport.request_id,
        "transport_status": st["transport_status"],
        "parse_status": st["parse_status"],
        "schema_status": st["schema_status"],
        "review_verdict": "NOT_REVIEWED",
        "legible": None,
        "reviewed_artifact": artifact_rel,
        "reviewed_artifact_sha256": artifact_sha256,
        "issues": [],
        "out_of_scope_items": [],
        "cycle": cycle,
        "error": None,
        "created_at": _now(),
    }
    if outcome.status is not InferenceStatus.SUCCESS or outcome.parsed is None:
        record["error"] = (
            transport.error
            or outcome.parse_error
            or "; ".join(outcome.schema_errors[:5])
            or outcome.status.value
        )
        return record
    p = outcome.parsed
    in_scope, out_scope = [], []
    for issue in p.get("issues", []):
        cat = str(issue.get("category", "")).strip().lower()
        if cat in ALLOWED_CATEGORIES:
            in_scope.append(
                {
                    "category": cat,
                    "severity": issue.get("severity", "major"),
                    "description": str(issue.get("description", ""))[:1000],
                    "location": issue.get("location"),
                }
            )
        else:
            out_scope.append(
                {"category": cat, "description": str(issue.get("description", ""))[:500]}
            )
    serious = any(i["severity"] in ("major", "blocking") for i in in_scope)
    legible = bool(p["legible"])
    verdict = "PASS" if legible and p["verdict"] == "PASS" and not serious else "FAIL"
    record.update(
        legible=legible,
        review_verdict=verdict,
        issues=in_scope,
        out_of_scope_items=out_scope,
    )
    return record


def simulated_review(
    layout_checks: list[Check], *, artifact_rel: str, artifact_sha256: str, cycle: int
) -> dict[str, Any]:
    """Deterministic stand-in reviewer for synthetic runs and tests.

    Derives presentation issues from the mechanical layout checks. Clearly
    labelled ``SIMULATED`` so it can never be mistaken for an independent review.
    """
    mapping = {
        "text_text_collisions": "collision",
        "text_wall_collisions": "collision",
        "text_clipping": "clipping",
        "page_edge_clipping": "clipping",
        "room_labels_placed": "label_placement",
        "title_block_separation": "hierarchy",
    }
    issues = [
        {
            "category": mapping[c.check_id],
            "severity": "major" if c.status == "FAIL" else "minor",
            "description": c.detail[:300],
            "location": None,
        }
        for c in layout_checks
        if c.check_id in mapping and c.status in ("FAIL", "WARN")
    ]
    serious = any(i["severity"] == "major" for i in issues)
    return {
        "schema": "blueprint.review-record",
        "schema_version": "1.0.0",
        "reviewer": {"kind": "SIMULATED", "model": None},
        "request_id": None,
        "transport_status": "NOT_APPLICABLE",
        "parse_status": "NOT_APPLICABLE",
        "schema_status": "NOT_APPLICABLE",
        "review_verdict": "FAIL" if serious else "PASS",
        "legible": not serious,
        "reviewed_artifact": artifact_rel,
        "reviewed_artifact_sha256": artifact_sha256,
        "issues": issues,
        "out_of_scope_items": [],
        "cycle": cycle,
        "error": None,
        "created_at": _now(),
    }


def review_currency(record: dict[str, Any] | None, store: ArtifactStore) -> tuple[str, str]:
    """Return (state, reason): CURRENT, STALE, or MISSING."""
    if record is None:
        return "MISSING", "no review record"
    p = store.path(record["reviewed_artifact"])
    if not p.exists():
        return "STALE", "reviewed artifact no longer exists"
    actual = sha256_file(p)
    if actual != record["reviewed_artifact_sha256"]:
        return "STALE", (
            f"reviewed {record['reviewed_artifact_sha256'][:12]} but current artifact is {actual[:12]}"
        )
    return "CURRENT", "review is bound to the current artifact"
