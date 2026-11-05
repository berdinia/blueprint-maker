"""Shared pipeline context and structured logging."""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config.settings import Settings
from ..inventory.register import SourceRegister
from ..io.artifacts import ArtifactStore

REGISTER_FILE = "source-register.json"
RELATIONSHIPS_FILE = "relationships.json"
DECISIONS_FILE = "decisions.json"

_SECRET_HINTS = ("key", "token", "secret", "password", "authorization")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("<redacted>" if any(h in k.lower() for h in _SECRET_HINTS) else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


class JsonLinesHandler(logging.Handler):
    """Appends one JSON object per record to ``<work>/logs/pipeline.jsonl``."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def emit(self, record: logging.LogRecord) -> None:
        entry: dict[str, Any] = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("event", "stage", "source_id", "request_id", "status", "data"):
            if hasattr(record, key):
                entry[key] = _redact(getattr(record, key))
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
        except OSError:  # logging must never break the pipeline
            pass


@dataclass
class PipelineContext:
    settings: Settings
    store: ArtifactStore = field(init=False)
    log: logging.Logger = field(init=False)

    def __post_init__(self) -> None:
        self.store = ArtifactStore(self.settings.paths.work_dir)
        self.log = logging.getLogger("blueprint")
        logs = self.settings.paths.work_dir / "logs" / "pipeline.jsonl"
        if not any(isinstance(h, JsonLinesHandler) and h.path == logs for h in self.log.handlers):
            for h in [h for h in self.log.handlers if isinstance(h, JsonLinesHandler)]:
                self.log.removeHandler(h)
            self.log.addHandler(JsonLinesHandler(logs))
        self.log.setLevel(logging.INFO)

    # -- inputs ---------------------------------------------------------
    @property
    def register_path(self) -> Path:
        return self.settings.paths.register_dir / REGISTER_FILE

    @property
    def relationships_path(self) -> Path:
        return self.settings.paths.register_dir / RELATIONSHIPS_FILE

    @property
    def decisions_path(self) -> Path:
        return self.settings.paths.register_dir / DECISIONS_FILE

    def register(self) -> SourceRegister:
        return SourceRegister.load(self.register_path)

    def event(self, msg: str, *, event: str, level: int = logging.INFO, **fields: Any) -> None:
        self.log.log(level, msg, extra={"event": event, **fields})

    def rel(self, p: str | os.PathLike[str]) -> str:
        """Path relative to the work dir, for artifact references (never absolute)."""
        return Path(p).resolve().relative_to(self.settings.paths.work_dir).as_posix()
