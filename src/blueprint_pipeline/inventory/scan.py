"""Source inventory: compare the register with the files actually present.

Evidence is immutable: the inventory only reads. Findings:

``MISSING_FILE``                    registered file absent → that source is BLOCKED
``CHECKSUM_MISMATCH``               file bytes differ from the registered checksum → BLOCKED
``CHECKSUM_RECORDED``               register had no checksum; current value recorded
``UNREGISTERED_FILE``               file present but not registered (ignored until registered)
``FILENAME_REGISTER_DISAGREEMENT``  filename mentions a different registered source ID
``EXTENSION_MISMATCH``              extension disagrees with detected content type
``UNSUPPORTED_MEDIA``               content type cannot be normalized locally
``EXACT_DUPLICATE``                 two registered sources have identical bytes
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..io.atomic import sha256_file
from .register import SourceRegister

SUPPORTED_RASTER = {"image/png", "image/jpeg", "image/tiff", "image/webp", "image/gif", "image/bmp"}
IGNORED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
_EXT = {
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/tiff": {".tif", ".tiff"},
    "image/webp": {".webp"},
    "image/gif": {".gif"},
    "image/bmp": {".bmp"},
    "application/pdf": {".pdf"},
    "image/heic": {".heic", ".heif"},
}


def sniff_media_type(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(32)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:2] == b"BM":
        return "image/bmp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"heif"):
        return "image/heic"
    return "application/octet-stream"


@dataclass
class Finding:
    code: str
    severity: str  # INFO | WARN | BLOCK
    message: str
    refs: list[str] = field(default_factory=list)


@dataclass
class InventoryEntry:
    source_id: str
    path: str
    exists: bool
    sha256: str | None
    size: int | None
    media_type: str | None
    registered_sha256: str | None
    checksum_status: str  # MATCH | RECORDED | MISMATCH | MISSING_FILE
    usable: bool


def _tokens(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def run_inventory(register: SourceRegister, input_dir: Path) -> dict[str, Any]:
    findings: list[Finding] = []
    entries: list[InventoryEntry] = []
    registered_paths = set()
    by_sha: dict[str, list[str]] = defaultdict(list)
    id_tokens = {sid: _tokens(sid) for sid in register.ids()}

    for rec in register.records():
        registered_paths.add(rec.path)
        p = input_dir / rec.path
        if not p.is_file():
            findings.append(
                Finding(
                    "MISSING_FILE", "BLOCK", f"{rec.source_id}: file not found", [rec.source_id]
                )
            )
            entries.append(
                InventoryEntry(
                    rec.source_id,
                    rec.path,
                    False,
                    None,
                    None,
                    None,
                    rec.sha256,
                    "MISSING_FILE",
                    False,
                )
            )
            continue
        digest = sha256_file(p)
        media = sniff_media_type(p)
        status = "MATCH"
        usable = True
        if rec.sha256 is None:
            status = "RECORDED"
            findings.append(
                Finding(
                    "CHECKSUM_RECORDED",
                    "INFO",
                    f"{rec.source_id}: no registered checksum; recorded current value",
                    [rec.source_id],
                )
            )
        elif rec.sha256 != digest:
            status = "MISMATCH"
            usable = False
            findings.append(
                Finding(
                    "CHECKSUM_MISMATCH",
                    "BLOCK",
                    f"{rec.source_id}: file content differs from registered checksum "
                    "(evidence must be immutable; register a new source instead)",
                    [rec.source_id],
                )
            )
        if media not in SUPPORTED_RASTER:
            usable = False
            findings.append(
                Finding(
                    "UNSUPPORTED_MEDIA",
                    "BLOCK",
                    f"{rec.source_id}: content type {media} cannot be normalized locally; "
                    "convert it to PNG with a local tool and register the converted file",
                    [rec.source_id],
                )
            )
        ext = p.suffix.lower()
        if media in _EXT and ext not in _EXT[media]:
            findings.append(
                Finding(
                    "EXTENSION_MISMATCH",
                    "WARN",
                    f"{rec.source_id}: extension {ext!r} but content is {media}",
                    [rec.source_id],
                )
            )
        stem = _tokens(p.stem)
        for other, tok in id_tokens.items():
            if (
                other != rec.source_id
                and tok
                and tok in stem
                and id_tokens[rec.source_id] not in stem
            ):
                findings.append(
                    Finding(
                        "FILENAME_REGISTER_DISAGREEMENT",
                        "WARN",
                        f"{rec.source_id}: filename mentions {other!r}; the register is "
                        "authoritative and the filename is ignored for identity",
                        [rec.source_id, other],
                    )
                )
        by_sha[digest].append(rec.source_id)
        entries.append(
            InventoryEntry(
                rec.source_id,
                rec.path,
                True,
                digest,
                p.stat().st_size,
                media,
                rec.sha256,
                status,
                usable,
            )
        )

    for _sha, ids in sorted(by_sha.items()):
        if len(ids) > 1:
            findings.append(
                Finding(
                    "EXACT_DUPLICATE",
                    "INFO",
                    f"identical bytes: {', '.join(sorted(ids))} (one evidence origin)",
                    sorted(ids),
                )
            )

    unregistered: list[str] = []
    if input_dir.is_dir():
        for f in sorted(input_dir.rglob("*")):
            if not f.is_file() or f.name in IGNORED_NAMES or f.name.startswith("."):
                continue
            rel = f.relative_to(input_dir).as_posix()
            if rel not in registered_paths:
                unregistered.append(rel)
                findings.append(
                    Finding(
                        "UNREGISTERED_FILE",
                        "WARN",
                        f"file present but not registered: {rel}",
                        [],
                    )
                )

    blocked = [e.source_id for e in entries if not e.usable]
    status = "PASS"
    if blocked:
        status = "PARTIAL" if len(blocked) < len(entries) else "BLOCKED"
    elif any(f.severity == "WARN" for f in findings):
        status = "PARTIAL"
    if not entries:
        status = "BLOCKED"
    return {
        "status": status,
        "synthetic": register.synthetic,
        "sources": [asdict(e) for e in entries],
        "unregistered_files": unregistered,
        "findings": [asdict(f) for f in findings],
        "blocked_source_ids": blocked,
    }
