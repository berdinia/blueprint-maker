"""Turn a transport result into a final :class:`InferenceOutcome`.

Order: transport → parse → provenance/normalisation hook → schema → semantic.
The first failing phase determines the status; later phases are not run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..schemas import schema_errors
from .parsing import parse_json_object
from .types import InferenceOutcome, InferenceStatus, TransportResult

PreSchemaHook = Callable[[dict[str, Any]], tuple[dict[str, Any], list[dict[str, Any]]]]
SemanticCheck = Callable[[dict[str, Any]], tuple[list[str], list[dict[str, Any]]]]


def evaluate(
    transport: TransportResult,
    *,
    schema_name: str,
    pre_schema: PreSchemaHook | None = None,
    semantic: SemanticCheck | None = None,
) -> InferenceOutcome:
    if transport.status is not InferenceStatus.SUCCESS:
        return InferenceOutcome(status=transport.status, transport=transport)
    obj, err, notes = parse_json_object(transport.content)
    if err is not None or obj is None:
        return InferenceOutcome(
            status=InferenceStatus.PARSE_FAILED,
            transport=transport,
            parse_error=err,
            parse_notes=notes,
        )
    warnings: list[dict[str, Any]] = []
    if pre_schema is not None:
        obj, w = pre_schema(obj)
        warnings.extend(w)
    errs = schema_errors(obj, schema_name)
    if errs:
        return InferenceOutcome(
            status=InferenceStatus.SCHEMA_FAILED,
            transport=transport,
            parsed=obj,
            parse_notes=notes,
            schema_errors=errs,
            warnings=warnings,
        )
    sem_errors: list[str] = []
    if semantic is not None:
        sem_errors, w = semantic(obj)
        warnings.extend(w)
    return InferenceOutcome(
        status=InferenceStatus.SEMANTIC_FAILED if sem_errors else InferenceStatus.SUCCESS,
        transport=transport,
        parsed=obj,
        parse_notes=notes,
        semantic_errors=sem_errors,
        warnings=warnings,
    )
