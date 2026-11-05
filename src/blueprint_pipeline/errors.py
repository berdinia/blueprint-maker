"""Exception hierarchy. Every error raised deliberately by the framework derives
from :class:`BlueprintError` so callers can distinguish them from bugs."""

from __future__ import annotations


class BlueprintError(Exception):
    """Base class for deliberate framework errors."""


class ConfigError(BlueprintError):
    """Configuration is missing, malformed or violates the privacy policy.

    Raised before any inference starts."""


class PrivacyPolicyError(ConfigError):
    """An endpoint or option would allow evidence to leave the permitted network."""


class ProvenanceError(BlueprintError):
    """Source identity cannot be established authoritatively."""


class SchemaValidationError(BlueprintError):
    """A document failed JSON Schema validation."""

    def __init__(self, schema_name: str, errors: list[str]) -> None:
        self.schema_name = schema_name
        self.errors = errors
        preview = "; ".join(errors[:5])
        more = f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""
        super().__init__(f"{schema_name}: {preview}{more}")


class LockError(BlueprintError):
    """The heavy-job lock could not be acquired."""

    def __init__(self, state: str, message: str) -> None:
        self.state = state
        super().__init__(message)


class ArtifactError(BlueprintError):
    """An artifact could not be written, read or verified."""


class StageBlocked(BlueprintError):
    """A stage cannot run because a prerequisite is missing or stale."""
