"""Process-lock module for the blueprint reconstruction pipeline.

Prevents two extraction or review jobs from running concurrently.
The lock is a simple advisory file lock; it is not a production-grade
distributed lock, but it guards against concurrent local runs.

Acquire the lock *before* loading or invoking a large model.
Release it on normal exit and handled exceptions only.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class ProcessLock:
    pid: int
    started_at: float
    lock_path: str
    acquired_by: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProcessLock":
        return cls(**d)


def stale_lock(lock_path: str, timeout_s: float = 600.0) -> bool:
    """Return True if the lock file exists but is stale."""
    if not os.path.exists(lock_path):
        return False
    try:
        with open(lock_path) as f:
            data = json.load(f)
        started = float(data.get("started_at", 0))
        age = time.time() - started
        pid = data.get("pid", 0)
        # Liveness probe. A fast /proc path on Linux, but guard every /proc
        # access so a missing pseudo-filesystem (macOS) cannot crash into the
        # outer handler and mis-report a LIVE lock as stale.
        if pid:
            proc = f"/proc/{pid}"
            if os.path.exists(proc):
                return False  # Linux: pid is alive
            # Cross-platform fallback: a signal probe.
            try:
                os.kill(pid, 0)  # raises ProcessLookupError when pid is gone
                return False      # process is alive (or is us): lock is live
            except ProcessLookupError:
                pass             # pid gone -> may be stale; fall through to age
            except PermissionError:
                return False     # exists but not ours; still a held lock
        return age > timeout_s
    except (json.JSONDecodeError, OSError, ValueError):
        return True


def acquire_lock(lock_path: str, pid: int | None = None, timeout_s: float = 600.0,
                 acquired_by: str = "") -> tuple[bool, Optional[ProcessLock]]:
    """Try to acquire the process lock.

    Returns ``(True, None)`` on success, ``(False, lock)`` if blocked.
    Stale lock is detected and the caller decides whether to reclaim.
    """
    pid = pid or os.getpid()
    lp = Path(lock_path)

    # Check for a stale lock first
    if os.path.exists(lp) and stale_lock(str(lp), timeout_s=timeout_s):
        # Stale: record it but reclaim
        pass

    if os.path.exists(lp):
        try:
            with open(lp) as f:
                existing = ProcessLock.from_dict(json.load(f))
            return False, existing
        except (json.JSONDecodeError, OSError):
            pass  # corrupt lock — reclaim

    # Acquire
    lock = ProcessLock(
        pid=pid,
        started_at=time.time(),
        lock_path=str(lp),
        acquired_by=acquired_by,
    )
    tmp = str(lp) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lock.to_dict(), f)
    os.rename(tmp, str(lp))
    return True, None


def release_lock(lock_path: str) -> None:
    """Release the process lock by removing the lock file."""
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        pass
