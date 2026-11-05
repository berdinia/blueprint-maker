"""Source registry — the canonical list of source documents for a reconstruction job.

The registry is the source of truth for:
- source_id (authoritative, caller-supplied or manifest-supplied)
- source filename, basename, hash (stored separately, not in the model)
- source families (grouping of related fragments/copies)
- canonical model references

The model NEVER determines the authoritative source_id. It may *suggest* a source_id,
but that suggestion is recorded as advisory only and is always overwritten.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Source:
    source_id: str
    filename: Optional[str] = None
    basename: Optional[str] = None
    content_hash: Optional[str] = None  # NOT embedded in fixtures — loaded on demand
    source_family: str = "DEFAULT"
    fragment_type: Optional[str] = None
    is_copy: bool = False
    copy_of: Optional[str] = None
    status: str = "pending"
    extracted_at: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # Never serialize the raw hash into a fixture file
        d.pop("content_hash", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Source":
        valid = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in d.items() if k in valid})

@dataclass
class SourceFamily:
    family_id: str
    description: str = ""
    members: list[str] = field(default_factory=list)
    copy_of: str = ""
    is_annotated_copy: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

class SourceRegistry:
    """A loadable source registry.

    The registry is stored as a JSON file. It contains the source metadata
    needed to reconstruct a job, but *not* raw content or raw hashes.
    """
    def __init__(self, path: str = "source-registry.json"):
        self.path = path
        self.sources: dict[str, Source] = {}
        self.families: dict[str, SourceFamily] = {}
        self._loaded = False

    def load(self) -> "SourceRegistry":
        if not os.path.exists(self.path):
            return self
        with open(self.path) as f:
            raw = json.load(f)
        for s in raw.get("sources", []):
            src = Source.from_dict(s)
            self.sources[src.source_id] = src
        for fam in raw.get("families", []):
            sf = SourceFamily(**{k: v for k, v in fam.items()
                                if k in SourceFamily.__dataclass_fields__})
            self.families[sf.family_id] = sf
        self._loaded = True
        return self

    def save(self, out_path: Optional[str] = None) -> None:
        path = out_path or self.path
        data = {
            "sources": [s.to_dict() for s in self.sources.values()],
            "families": [f.to_dict() for f in self.families.values()],
        }
        tmp = str(path) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.rename(tmp, str(path))

    # --- authoritative identity resolution ------------------------------------
    def resolve_source_id(self, requested: str, model_advisory: Optional[str] = None) -> dict:
        """Resolve the authoritative source_id from the registry.

        The authoritative source_id is always taken from the registry or caller.
        The advisory from the model is recorded but NEVER used for resolution.
        """
        match = None
        for sid, src in self.sources.items():
            if src.source_id == requested or model_advisory == sid:
                match = src
                break
        authoritative = match.source_id if match else requested
        advisory = model_advisory
        mismatch = bool(model_advisory and authoritative != model_advisory)
        return {
            "authoritative": authoritative,
            "advisory": advisory,
            "mismatch": mismatch,
            "source_id_mismatch": mismatch,
            "resolved_from": "registry" if match else "caller",
        }

    @property
    def is_unresolved(self) -> bool:
        return any(not s.extracted_at for s in self.sources.values()
                   if s.status != "extracted")
