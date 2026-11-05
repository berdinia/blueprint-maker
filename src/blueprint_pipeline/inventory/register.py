"""Source register: the authority for source identity.

The register is maintained by the operator. Filenames are clues, never facts:
identity comes from the register entry, and the pipeline (not a model) attaches
that identity to every derived artifact.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..errors import ProvenanceError, SchemaValidationError
from ..io.atomic import read_json
from ..schemas import schema_errors


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    path: str
    sha256: str | None = None
    declared_fragment_class: str | None = None
    notes: str = ""


class SourceRegister:
    def __init__(self, records: list[SourceRecord], *, synthetic: bool = False) -> None:
        self.synthetic = synthetic
        self._by_id: dict[str, SourceRecord] = {}
        for r in records:
            self._by_id[r.source_id] = r

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> SourceRegister:
        p = Path(path)
        if not p.is_file():
            raise ProvenanceError(
                f"source register not found at {p}; create it before running the pipeline"
            )
        doc = read_json(p)
        return cls.from_document(doc)

    @classmethod
    def from_document(cls, doc: Any) -> SourceRegister:
        errs = schema_errors(doc, "source-register")
        if errs:
            raise SchemaValidationError("source-register", errs)
        records: list[SourceRecord] = []
        seen_ids: dict[str, int] = {}
        seen_ids_ci: dict[str, str] = {}
        seen_paths: dict[str, str] = {}
        problems: list[str] = []
        for i, s in enumerate(doc["sources"]):
            sid = s["source_id"]
            if sid in seen_ids:
                problems.append(f"duplicate source_id {sid!r} (entries {seen_ids[sid]} and {i})")
                continue
            folded = sid.casefold()
            if folded in seen_ids_ci:
                problems.append(
                    f"source_id {sid!r} differs only in case from {seen_ids_ci[folded]!r}"
                )
            seen_ids[sid] = i
            seen_ids_ci[folded] = sid
            rel = s["path"]
            pp = PurePosixPath(rel.replace("\\", "/"))
            if pp.is_absolute() or ".." in pp.parts:
                problems.append(f"{sid}: path must be relative to the input directory: {rel!r}")
                continue
            norm = str(pp)
            if norm in seen_paths:
                problems.append(f"{sid}: path {rel!r} already registered to {seen_paths[norm]!r}")
                continue
            seen_paths[norm] = sid
            records.append(
                SourceRecord(
                    source_id=sid,
                    path=norm,
                    sha256=s.get("sha256"),
                    declared_fragment_class=s.get("declared_fragment_class"),
                    notes=s.get("notes", ""),
                )
            )
        if problems:
            raise ProvenanceError(
                "source register is inconsistent:\n  - " + "\n  - ".join(problems)
            )
        return cls(records, synthetic=bool(doc.get("synthetic", False)))

    # -- lookups -----------------------------------------------------------
    def get(self, source_id: str) -> SourceRecord:
        try:
            return self._by_id[source_id]
        except KeyError:
            raise ProvenanceError(
                f"source_id {source_id!r} is not in the source register; register it before use"
            ) from None

    def __contains__(self, source_id: object) -> bool:
        return source_id in self._by_id

    def ids(self) -> list[str]:
        return sorted(self._by_id)

    def records(self) -> list[SourceRecord]:
        return [self._by_id[k] for k in self.ids()]

    def by_path(self) -> dict[str, SourceRecord]:
        return {r.path: r for r in self._by_id.values()}
