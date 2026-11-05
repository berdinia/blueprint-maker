"""Cross-process heavy-job lock.

Only one large local-model job (extraction or visual review) may run at a time
on a machine, regardless of how many launchers or shells are involved.

Mechanism
---------
* The lock is an OS advisory lock (``fcntl.flock``) on ``<work>/locks/heavy-job.lock``.
  The kernel releases it when the holding process exits for any reason, so two
  live processes can never hold it simultaneously, and there is no
  check-then-create race.
* While held, the file contains an *owner record*: PID, hostname, operation,
  model, source ID, start time and a random lock ID.
* On a clean release the record is replaced by ``{"state": "released", ...}``.
  The file itself is never unlinked (unlinking flock files reintroduces races).

States reported by :meth:`HeavyJobLock.acquire`
-----------------------------------------------
``LOCK_ACQUIRED``                 acquired; owner record written
``LOCK_BUSY``                     another live process holds the lock
``LOCK_STALE_REQUIRES_REVIEW``    the flock is free but the previous owner never
                                  released cleanly (crash/kill). The local model
                                  server may still be processing the abandoned
                                  request, so the operator must review and clear
                                  it with ``blueprint lock clear --stale``.
``LOCK_RELEASED``                 returned by :meth:`release`

Platforms
---------
POSIX (macOS, Linux) uses ``fcntl.flock``. On platforms without ``fcntl`` the
lock falls back to exclusive file creation (``O_CREAT|O_EXCL``) plus PID
liveness checks; that fallback cannot survive a crash without operator review
and is documented as less robust.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import enum
import json
import os
import socket
import time
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO, Any

from ..errors import LockError

try:  # pragma: no cover - platform dependent
    import fcntl

    HAVE_FLOCK = True
except ImportError:  # pragma: no cover
    HAVE_FLOCK = False


class LockState(str, enum.Enum):
    LOCK_ACQUIRED = "LOCK_ACQUIRED"
    LOCK_BUSY = "LOCK_BUSY"
    LOCK_STALE_REQUIRES_REVIEW = "LOCK_STALE_REQUIRES_REVIEW"
    LOCK_RELEASED = "LOCK_RELEASED"


@dataclass(frozen=True)
class LockOwner:
    lock_id: str
    pid: int
    hostname: str
    operation: str
    model: str | None
    source_id: str | None
    started_at: str
    state: str = "held"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LockOwner:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass(frozen=True)
class LockInspection:
    path: str
    held_by_live_process: bool
    record: dict[str, Any] | None
    state: str  # FREE | HELD | STALE_REQUIRES_REVIEW | CORRUPT_RECORD
    detail: str


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    except OSError:
        return False
    return True


def _read_record(fh: IO[str]) -> tuple[dict[str, Any] | None, bool]:
    """Return (record, corrupt)."""
    fh.seek(0)
    text = fh.read()
    if not text.strip():
        return None, False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, True
    if not isinstance(data, dict):
        return None, True
    return data, False


def _write_record(fh: IO[str], record: dict[str, Any]) -> None:
    fh.seek(0)
    fh.truncate()
    fh.write(json.dumps(record, sort_keys=True))
    fh.flush()
    os.fsync(fh.fileno())


class HeavyJobLock:
    """Context-manageable cross-process lock for heavy model jobs.

    >>> with HeavyJobLock(path, operation="extract", model="m", source_id="SYN-1"):
    ...     run_model()
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        operation: str,
        model: str | None = None,
        source_id: str | None = None,
        wait_s: float = 0.0,
        poll_s: float = 0.5,
    ) -> None:
        self.path = Path(path)
        self.operation = operation
        self.model = model
        self.source_id = source_id
        self.wait_s = wait_s
        self.poll_s = poll_s
        self._fh: IO[str] | None = None
        self.owner: LockOwner | None = None
        self.last_state: LockState | None = None
        self.blocking_record: dict[str, Any] | None = None

    # -- acquisition ----------------------------------------------------------
    def acquire(self) -> LockState:
        if self._fh is not None:
            raise LockError("LOCK_ACQUIRED", "lock already held by this object")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(self.wait_s, 0.0)
        while True:
            state = self._try_once()
            if state is not LockState.LOCK_BUSY or time.monotonic() >= deadline:
                self.last_state = state
                return state
            time.sleep(min(self.poll_s, max(deadline - time.monotonic(), 0.01)))

    def _try_once(self) -> LockState:
        if not HAVE_FLOCK:  # pragma: no cover
            return self._try_once_excl()
        fh = open(self.path, "a+", encoding="utf-8")  # noqa: SIM115 - held for lock lifetime
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            record, _ = _read_record(fh)
            fh.close()
            self.blocking_record = record
            return LockState.LOCK_BUSY
        # We hold the flock. Inspect the previous owner record.
        record, corrupt = _read_record(fh)
        if corrupt or (record is not None and record.get("state") == "held"):
            self.blocking_record = record
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
            return LockState.LOCK_STALE_REQUIRES_REVIEW
        owner = LockOwner(
            lock_id=uuid.uuid4().hex,
            pid=os.getpid(),
            hostname=socket.gethostname(),
            operation=self.operation,
            model=self.model,
            source_id=self.source_id,
            started_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        )
        _write_record(fh, owner.to_dict())
        self._fh = fh
        self.owner = owner
        return LockState.LOCK_ACQUIRED

    def _try_once_excl(self) -> LockState:  # pragma: no cover - non-POSIX fallback
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            try:
                record = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                record = None
            self.blocking_record = record
            if (
                record
                and record.get("hostname") == socket.gethostname()
                and pid_alive(int(record.get("pid", 0)))
            ):
                return LockState.LOCK_BUSY
            return LockState.LOCK_STALE_REQUIRES_REVIEW
        fh = os.fdopen(fd, "w+", encoding="utf-8")
        owner = LockOwner(
            lock_id=uuid.uuid4().hex,
            pid=os.getpid(),
            hostname=socket.gethostname(),
            operation=self.operation,
            model=self.model,
            source_id=self.source_id,
            started_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        )
        _write_record(fh, owner.to_dict())
        self._fh = fh
        self.owner = owner
        return LockState.LOCK_ACQUIRED

    # -- release --------------------------------------------------------------
    def release(self) -> LockState:
        fh, owner = self._fh, self.owner
        if fh is None or owner is None:
            return LockState.LOCK_RELEASED
        try:
            record, _ = _read_record(fh)
            # Only overwrite our own record.
            if record is not None and record.get("lock_id") == owner.lock_id:
                released = {
                    "state": "released",
                    "lock_id": owner.lock_id,
                    "operation": owner.operation,
                    "released_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                }
                _write_record(fh, released)
        finally:
            if HAVE_FLOCK:
                with contextlib.suppress(OSError):
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
            if not HAVE_FLOCK:  # pragma: no cover
                with contextlib.suppress(FileNotFoundError):
                    self.path.unlink()
            self._fh = None
            self.owner = None
        self.last_state = LockState.LOCK_RELEASED
        return LockState.LOCK_RELEASED

    def __enter__(self) -> HeavyJobLock:
        state = self.acquire()
        if state is not LockState.LOCK_ACQUIRED:
            raise LockError(state.value, describe_block(state, self.blocking_record))
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def describe_block(state: LockState, record: dict[str, Any] | None) -> str:
    who = ""
    if record:
        who = (
            f" (operation={record.get('operation')!r}, pid={record.get('pid')}, "
            f"host={record.get('hostname')!r}, model={record.get('model')!r}, "
            f"source={record.get('source_id')!r}, since={record.get('started_at')})"
        )
    if state is LockState.LOCK_BUSY:
        return f"heavy-job lock is held by a live process{who}; refusing to start"
    if state is LockState.LOCK_STALE_REQUIRES_REVIEW:
        return (
            f"previous heavy job did not release the lock cleanly{who}. The local model "
            "server may still be busy. Inspect with 'blueprint lock inspect' and clear "
            "with 'blueprint lock clear --stale' once the model server is idle."
        )
    return state.value


def inspect_lock(path: str | os.PathLike[str]) -> LockInspection:
    """Report lock state without changing it."""
    p = Path(path)
    if not p.exists():
        return LockInspection(str(p), False, None, "FREE", "no lock file")
    if not HAVE_FLOCK:  # pragma: no cover
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return LockInspection(str(p), False, None, "CORRUPT_RECORD", "unreadable record")
        alive = rec.get("hostname") == socket.gethostname() and pid_alive(int(rec.get("pid", 0)))
        return LockInspection(
            str(p), alive, rec, "HELD" if alive else "STALE_REQUIRES_REVIEW", "fallback lock"
        )
    with open(p, "a+", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            record, _ = _read_record(fh)
            return LockInspection(str(p), True, record, "HELD", "held by a live process")
        try:
            record, corrupt = _read_record(fh)
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    if corrupt:
        return LockInspection(str(p), False, None, "CORRUPT_RECORD", "owner record unreadable")
    if record is None or record.get("state") != "held":
        return LockInspection(str(p), False, record, "FREE", "released cleanly")
    return LockInspection(
        str(p),
        False,
        record,
        "STALE_REQUIRES_REVIEW",
        "owner exited without releasing; review before clearing",
    )


def clear_stale_lock(path: str | os.PathLike[str], *, allow_other_host: bool = False) -> str:
    """Clear a stale owner record. Never clears a lock held by a live process.

    Returns a short description of what was done; raises LockError if refused.
    """
    info = inspect_lock(path)
    if info.state == "FREE":
        return "lock is free; nothing to clear"
    if info.held_by_live_process:
        raise LockError("LOCK_BUSY", "refusing to clear: lock is held by a live process")
    rec = info.record or {}
    host = rec.get("hostname")
    if host and host != socket.gethostname() and not allow_other_host:
        raise LockError(
            "LOCK_STALE_REQUIRES_REVIEW",
            f"stale record was written on another host ({host!r}); "
            "re-run with --allow-other-host after confirming that host is idle",
        )
    pid = int(rec.get("pid", 0) or 0)
    if host == socket.gethostname() and pid_alive(pid):
        raise LockError(
            "LOCK_STALE_REQUIRES_REVIEW",
            f"recorded pid {pid} is still alive on this host; confirm it is not a model job",
        )
    if not HAVE_FLOCK:  # pragma: no cover
        Path(path).unlink()
        return "stale fallback lock removed"
    with open(path, "a+", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockError("LOCK_BUSY", "lock became held while clearing") from exc
        try:
            _write_record(
                fh,
                {
                    "state": "cleared",
                    "cleared_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                    "previous": rec,
                },
            )
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return "stale owner record cleared"


@contextlib.contextmanager
def heavy_job(
    path: str | os.PathLike[str],
    *,
    operation: str,
    model: str | None,
    source_id: str | None,
    wait_s: float = 0.0,
    poll_s: float = 0.5,
) -> Iterator[HeavyJobLock]:
    lock = HeavyJobLock(
        path, operation=operation, model=model, source_id=source_id, wait_s=wait_s, poll_s=poll_s
    )
    with lock:
        yield lock
