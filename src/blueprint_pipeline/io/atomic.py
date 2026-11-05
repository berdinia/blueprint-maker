"""Atomic file writes and content hashing.

Every write goes to a uniquely named temporary file in the destination
directory, is flushed and fsynced, optionally validated, and is then moved into
place with :func:`os.replace` (atomic on POSIX and Windows for same-volume
paths). A failed validation leaves the previous file untouched.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import ArtifactError


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, fixed separators, UTF-8, trailing newline.

    Floats are emitted by ``json`` using ``repr`` which is deterministic for a
    given value. Callers that need stable rounding round before serialising.
    """
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _fsync_dir(directory: Path) -> None:
    # Directory fsync makes the rename durable on POSIX; not supported on Windows.
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(
    path: str | os.PathLike[str],
    data: bytes,
    validate: Callable[[Path], None] | None = None,
) -> str:
    """Atomically write ``data`` to ``path`` and return its SHA-256.

    ``validate`` receives the temporary path and must raise to abort the write.
    On any failure the temporary file is removed and the destination is left as
    it was.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if validate is not None:
            validate(tmp)
        os.replace(tmp, dest)
        _fsync_dir(dest.parent)
    except BaseException as exc:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        if isinstance(exc, (ArtifactError, KeyboardInterrupt, SystemExit)):
            raise
        raise ArtifactError(f"atomic write to {dest.name} aborted: {exc}") from exc
    return sha256_bytes(data)


def atomic_write_text(
    path: str | os.PathLike[str],
    text: str,
    validate: Callable[[Path], None] | None = None,
) -> str:
    return atomic_write_bytes(path, text.encode("utf-8"), validate=validate)


def _validate_json_file(p: Path) -> None:
    with open(p, encoding="utf-8") as f:
        json.load(f)


def atomic_write_json(path: str | os.PathLike[str], obj: Any) -> str:
    """Write ``obj`` as canonical JSON, re-parsing the temp file before rename."""
    return atomic_write_text(path, canonical_json(obj), validate=_validate_json_file)


def read_json(path: str | os.PathLike[str]) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
