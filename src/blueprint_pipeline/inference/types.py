"""Inference outcome categories and records."""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any


class InferenceStatus(str, enum.Enum):
    """Mutually exclusive outcome of one model interaction.

    Ordered by pipeline position: a request is only ``SUCCESS`` when transport,
    HTTP, stream assembly, parsing, schema validation and semantic validation
    have all passed. HTTP 200 alone is never success.
    """

    TRANSPORT_FAILED = "TRANSPORT_FAILED"  # connect/send/receive failure or timeout
    HTTP_FAILED = "HTTP_FAILED"  # non-2xx status
    MODEL_ABORTED = "MODEL_ABORTED"  # server/model reported an error or abnormal stop
    EMPTY_RESPONSE = "EMPTY_RESPONSE"  # 2xx with no content
    STREAM_INCOMPLETE = "STREAM_INCOMPLETE"  # stream ended without a terminal event
    PARSE_FAILED = "PARSE_FAILED"  # content is not a single JSON object
    SCHEMA_FAILED = "SCHEMA_FAILED"  # JSON does not match the expected schema
    SEMANTIC_FAILED = "SEMANTIC_FAILED"  # schema-valid but semantically unusable
    SUCCESS = "SUCCESS"


TRANSPORT_LEVEL = {
    InferenceStatus.TRANSPORT_FAILED,
    InferenceStatus.HTTP_FAILED,
    InferenceStatus.MODEL_ABORTED,
    InferenceStatus.EMPTY_RESPONSE,
    InferenceStatus.STREAM_INCOMPLETE,
}


@dataclass
class AttemptRecord:
    attempt: int
    endpoint_role: str  # "primary" | "native"
    endpoint: str  # scheme://host:port only
    started_at: str
    duration_s: float
    outcome: str
    http_status: int | None = None
    error: str | None = None
    body_sent: bool = False
    retry_decision: str | None = None


@dataclass
class TransportResult:
    """Result of the transport + HTTP + stream-assembly phases."""

    status: InferenceStatus  # SUCCESS here means "content assembled"
    request_id: str
    model_requested: str
    api: str
    content: str = ""
    raw: bytes = b""
    http_status: int | None = None
    error: str | None = None
    finish_reason: str | None = None
    model_reported: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    attempts: list[AttemptRecord] = field(default_factory=list)
    fallback_used: bool = False

    def summary(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d.pop("raw")
        d.pop("content")
        d["raw_bytes"] = len(self.raw)
        d["content_chars"] = len(self.content)
        return d


@dataclass
class InferenceOutcome:
    """Final classification of a model interaction after all checks."""

    status: InferenceStatus
    transport: TransportResult
    parsed: dict[str, Any] | None = None
    parse_error: str | None = None
    parse_notes: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    semantic_errors: list[str] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status is InferenceStatus.SUCCESS

    def stage_statuses(self) -> dict[str, str]:
        """Per-phase verdicts, so transport success is never mistaken for success."""
        t = self.transport.status
        transport_ok = t is InferenceStatus.SUCCESS
        out = {
            "transport_status": "SUCCESS" if transport_ok else t.value,
            "parse_status": "NOT_RUN",
            "schema_status": "NOT_RUN",
            "semantic_status": "NOT_RUN",
        }
        if not transport_ok:
            return out
        out["parse_status"] = "FAILED" if self.parse_error else "SUCCESS"
        if self.parse_error:
            return out
        out["schema_status"] = "FAILED" if self.schema_errors else "SUCCESS"
        if self.schema_errors:
            return out
        out["semantic_status"] = "FAILED" if self.semantic_errors else "SUCCESS"
        return out
