"""Artifact store with sidecar metadata and hash-based freshness.

Each artifact ``<rel>`` has a sidecar ``<rel>.meta.json`` recording the
artifact's SHA-256, the hashes of every input it was derived from, the
configuration hash, the model identity and the content schema version.

Freshness is decided from those hashes — never from modification times:

* ``CURRENT``     artifact hash matches its metadata and all expectations match
* ``STALE``       inputs, configuration, model or schema differ from expectations
* ``CORRUPT``     the artifact's bytes no longer match the recorded hash
* ``NO_METADATA`` artifact exists without metadata (e.g. interrupted write)
* ``MISSING``     artifact does not exist

Errors are written to ``<rel>.error.json`` and never replace ``<rel>``.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import enum
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .. import __version__
from ..errors import ArtifactError
from .atomic import atomic_write_bytes, atomic_write_json, canonical_json, sha256_file

META_SUFFIX = ".meta.json"
ERROR_SUFFIX = ".error.json"
META_SCHEMA = "blueprint.artifact-meta"
META_VERSION = "1.0.0"


class Freshness(str, enum.Enum):
    CURRENT = "CURRENT"
    STALE = "STALE"
    CORRUPT = "CORRUPT"
    NO_METADATA = "NO_METADATA"
    MISSING = "MISSING"


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ArtifactMeta:
    artifact: str
    sha256: str
    size: int
    stage: str
    inputs: dict[str, str] = field(default_factory=dict)
    config_hash: str | None = None
    model: str | None = None
    content_schema: str | None = None
    producer: str = f"blueprint-pipeline {__version__}"
    created_at: str = field(default_factory=utc_now)
    schema: str = META_SCHEMA
    schema_version: str = META_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ArtifactMeta:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    def same_derivation(self, other: ArtifactMeta) -> bool:
        return (
            self.sha256 == other.sha256
            and self.inputs == other.inputs
            and self.config_hash == other.config_hash
            and self.model == other.model
            and self.content_schema == other.content_schema
        )


@dataclass
class FreshnessReport:
    state: Freshness
    reasons: list[str] = field(default_factory=list)
    meta: ArtifactMeta | None = None

    @property
    def is_current(self) -> bool:
        return self.state is Freshness.CURRENT


class ArtifactStore:
    """Writes artifacts under ``root`` with metadata sidecars."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    # -- paths -------------------------------------------------------------
    def path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        root = self.root.resolve()
        if p != root and root not in p.parents:
            raise ArtifactError(f"artifact path escapes store root: {rel}")
        return p

    def meta_path(self, rel: str) -> Path:
        return self.path(rel + META_SUFFIX)

    def error_path(self, rel: str) -> Path:
        return self.path(rel + ERROR_SUFFIX)

    # -- writing -----------------------------------------------------------
    def write_bytes(
        self,
        rel: str,
        data: bytes,
        *,
        stage: str,
        inputs: Mapping[str, str] | None = None,
        config_hash: str | None = None,
        model: str | None = None,
        content_schema: str | None = None,
        extra: Mapping[str, Any] | None = None,
        validate: Any = None,
    ) -> ArtifactMeta:
        from .atomic import sha256_bytes

        new = ArtifactMeta(
            artifact=rel,
            sha256=sha256_bytes(data),
            size=len(data),
            stage=stage,
            inputs=dict(sorted((inputs or {}).items())),
            config_hash=config_hash,
            model=model,
            content_schema=content_schema,
            extra=dict(extra or {}),
        )
        existing = self.read_meta(rel)
        target = self.path(rel)
        if (
            existing is not None
            and existing.same_derivation(new)
            and target.exists()
            and sha256_file(target) == new.sha256
        ):
            # Idempotent rerun: identical bytes from identical inputs.
            self._clear_error(rel)
            return existing
        atomic_write_bytes(target, data, validate=validate)
        atomic_write_json(self.meta_path(rel), new.to_dict())
        self._clear_error(rel)
        return new

    def write_json(self, rel: str, obj: Any, **kwargs: Any) -> ArtifactMeta:
        data = canonical_json(obj).encode("utf-8")

        def _validate(p: Path) -> None:
            with open(p, encoding="utf-8") as f:
                json.load(f)

        return self.write_bytes(rel, data, validate=_validate, **kwargs)

    def write_error(self, rel: str, payload: Mapping[str, Any]) -> Path:
        """Record a failure for ``rel`` without touching the last valid artifact."""
        body = {"artifact": rel, "recorded_at": utc_now(), **payload}
        p = self.error_path(rel)
        atomic_write_json(p, body)
        return p

    def _clear_error(self, rel: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.error_path(rel).unlink()

    # -- reading -----------------------------------------------------------
    def read_meta(self, rel: str) -> ArtifactMeta | None:
        mp = self.meta_path(rel)
        if not mp.exists():
            return None
        try:
            with open(mp, encoding="utf-8") as f:
                return ArtifactMeta.from_dict(json.load(f))
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def read_json(self, rel: str) -> Any:
        with open(self.path(rel), encoding="utf-8") as f:
            return json.load(f)

    def read_error(self, rel: str) -> dict[str, Any] | None:
        p = self.error_path(rel)
        if not p.exists():
            return None
        with open(p, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
            return data

    def check(
        self,
        rel: str,
        *,
        inputs: Mapping[str, str] | None = None,
        config_hash: str | None = None,
        model: str | None = None,
        content_schema: str | None = None,
    ) -> FreshnessReport:
        """Compare an artifact with what the caller expects it to derive from.

        Any expectation passed as ``None`` is not checked.
        """
        target = self.path(rel)
        if not target.exists():
            return FreshnessReport(Freshness.MISSING, ["artifact does not exist"])
        meta = self.read_meta(rel)
        if meta is None:
            return FreshnessReport(Freshness.NO_METADATA, ["metadata sidecar missing or invalid"])
        actual = sha256_file(target)
        if actual != meta.sha256:
            return FreshnessReport(
                Freshness.CORRUPT,
                [f"content hash {actual[:12]} != recorded {meta.sha256[:12]}"],
                meta,
            )
        reasons: list[str] = []
        if inputs is not None:
            exp = dict(inputs)
            for k in sorted(set(exp) | set(meta.inputs)):
                if exp.get(k) != meta.inputs.get(k):
                    reasons.append(
                        f"input {k!r}: recorded {str(meta.inputs.get(k))[:12]} "
                        f"!= current {str(exp.get(k))[:12]}"
                    )
        if config_hash is not None and config_hash != meta.config_hash:
            reasons.append("configuration hash changed")
        if model is not None and model != meta.model:
            reasons.append(f"model changed: {meta.model!r} -> {model!r}")
        if content_schema is not None and content_schema != meta.content_schema:
            reasons.append(f"schema changed: {meta.content_schema!r} -> {content_schema!r}")
        if reasons:
            return FreshnessReport(Freshness.STALE, reasons, meta)
        return FreshnessReport(Freshness.CURRENT, [], meta)
