"""Versioned JSON Schemas (draft 2020-12) and validation helpers.

Every persisted document carries ``schema`` (``blueprint.<name>``) and
``schema_version`` (semver). :data:`CURRENT_VERSIONS` lists the version this
release writes; :func:`validate_document` checks both the declared identity
and the JSON Schema. See ``docs/SCHEMAS.md`` for the versioning policy.
"""

from __future__ import annotations

import functools
import json
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator

from ..errors import SchemaValidationError

CURRENT_VERSIONS: dict[str, str] = {
    "source-register": "1.0.0",
    "relationships": "1.0.0",
    "extraction-response": "1.0.0",
    "extraction-record": "1.0.0",
    "evidence": "1.0.0",
    "decisions": "1.0.0",
    "canonical-model": "1.0.0",
    "review-response": "1.0.0",
    "review-record": "1.0.0",
    "validation-report": "1.0.0",
    "legacy-house-model": "0",
}

# Model-output contracts do not carry schema/schema_version fields.
_UNVERSIONED_DOCUMENTS = {"extraction-response", "review-response", "legacy-house-model"}


@functools.cache
def load_schema(name: str) -> dict[str, Any]:
    if name not in CURRENT_VERSIONS:
        raise KeyError(f"unknown schema {name!r}")
    text = resources.files(__package__).joinpath(f"{name}.schema.json").read_text("utf-8")
    data: dict[str, Any] = json.loads(text)
    Draft202012Validator.check_schema(data)
    return data


@functools.cache
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(name))


def schema_errors(doc: Any, name: str) -> list[str]:
    """Return human-readable validation errors (empty list when valid)."""
    errors: list[str] = []
    if name not in _UNVERSIONED_DOCUMENTS and isinstance(doc, dict):
        expected = f"blueprint.{name}"
        if doc.get("schema") != expected:
            errors.append(f"schema identifier is {doc.get('schema')!r}, expected {expected!r}")
        declared = doc.get("schema_version")
        current = CURRENT_VERSIONS[name]
        if declared is not None and str(declared).split(".")[0] != current.split(".")[0]:
            errors.append(
                f"schema_version {declared!r} is not compatible with {current!r}; "
                "run 'blueprint migrate-model' or regenerate the document"
            )
    for err in sorted(_validator(name).iter_errors(doc), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        msg = err.message
        if len(msg) > 300:
            msg = msg[:300] + "…"
        errors.append(f"{loc}: {msg}")
    return errors


def validate_document(doc: Any, name: str) -> None:
    errs = schema_errors(doc, name)
    if errs:
        raise SchemaValidationError(name, errs)


def schema_tag(name: str) -> str:
    return f"{name}@{CURRENT_VERSIONS[name]}"


__all__ = [
    "CURRENT_VERSIONS",
    "load_schema",
    "schema_errors",
    "schema_tag",
    "validate_document",
]
