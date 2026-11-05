"""Cross-process heavy-job lock, exercised with real separate processes."""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import signal
import subprocess
import sys
import time

import pytest

from blueprint_pipeline.errors import LockError
from blueprint_pipeline.orchestration.lock import (
    HeavyJobLock,
    LockState,
    clear_stale_lock,
    inspect_lock,
)

pytestmark = pytest.mark.slow
CTX = mp.get_context("spawn")  # independent interpreters, as with separate launchers


def _contend(path, barrier, active, overlap, results):
    barrier.wait()
    lock = HeavyJobLock(path, operation="extract", model="m", source_id="SYN-X")
    state = lock.acquire()
    if state is LockState.LOCK_ACQUIRED:
        with active.get_lock():
            active.value += 1
            if active.value > 1:
                overlap.value = 1
        time.sleep(0.3)
        with active.get_lock():
            active.value -= 1
        lock.release()
    results.put(state.value)


def test_only_one_process_holds_the_lock(tmp_path):
    path = str(tmp_path / "heavy.lock")
    n = 6
    barrier, active, overlap, results = (
        CTX.Barrier(n),
        CTX.Value("i", 0),
        CTX.Value("i", 0),
        CTX.Queue(),
    )
    procs = [
        CTX.Process(target=_contend, args=(path, barrier, active, overlap, results))
        for _ in range(n)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
    states = [results.get(timeout=5) for _ in procs]
    assert overlap.value == 0
    assert states.count("LOCK_ACQUIRED") >= 1
    assert set(states) <= {"LOCK_ACQUIRED", "LOCK_BUSY"}


HOLDER = """
import sys, time
from blueprint_pipeline.orchestration.lock import HeavyJobLock
lock = HeavyJobLock(sys.argv[1], operation="review", model="syn-review", source_id=None)
assert lock.acquire().value == "LOCK_ACQUIRED"
print("held", flush=True)
time.sleep(60)
"""


def _spawn_holder(path):
    p = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(path)], stdout=subprocess.PIPE, text=True
    )
    assert p.stdout.readline().strip() == "held"
    return p


def test_blocked_job_never_removes_the_holders_lock(tmp_path):
    path = tmp_path / "heavy.lock"
    holder = _spawn_holder(path)
    try:
        lock = HeavyJobLock(path, operation="extract")
        assert lock.acquire() is LockState.LOCK_BUSY
        assert lock.blocking_record["operation"] == "review"
        assert lock.blocking_record["pid"] == holder.pid
        lock.release()  # releasing an unacquired lock is a no-op
        info = inspect_lock(path)
        assert info.state == "HELD" and info.held_by_live_process
        with pytest.raises(LockError, match="held by a live process"):
            clear_stale_lock(path)
        assert HeavyJobLock(path, operation="extract").acquire() is LockState.LOCK_BUSY
    finally:
        holder.kill()
        holder.wait()


def test_crashed_holder_leaves_stale_lock_requiring_review(tmp_path):
    path = tmp_path / "heavy.lock"
    holder = _spawn_holder(path)
    os.kill(holder.pid, signal.SIGKILL)
    holder.wait()
    lock = HeavyJobLock(path, operation="extract")
    assert lock.acquire() is LockState.LOCK_STALE_REQUIRES_REVIEW
    assert inspect_lock(path).state == "STALE_REQUIRES_REVIEW"
    assert clear_stale_lock(path) == "stale owner record cleared"
    assert json.loads(path.read_text())["previous"]["pid"] == holder.pid
    lock2 = HeavyJobLock(path, operation="extract")
    assert lock2.acquire() is LockState.LOCK_ACQUIRED
    lock2.release()


def test_context_manager_releases_on_exception_and_records_owner(tmp_path):
    path = tmp_path / "heavy.lock"
    with (
        pytest.raises(RuntimeError),
        HeavyJobLock(path, operation="extract", model="m", source_id="SYN-1") as lk,
    ):
        rec = json.loads(path.read_text())
        assert {rec[k] for k in ("operation", "model", "source_id")} == {"extract", "m", "SYN-1"}
        assert rec["pid"] == os.getpid() and rec["hostname"] and rec["started_at"]
        assert lk.owner is not None
        raise RuntimeError("boom")
    assert inspect_lock(path).state == "FREE"
    assert json.loads(path.read_text())["state"] == "released"


def test_bounded_wait(tmp_path):
    path = tmp_path / "heavy.lock"
    holder = _spawn_holder(path)
    try:
        t0 = time.monotonic()
        lock = HeavyJobLock(path, operation="extract", wait_s=1.0, poll_s=0.2)
        assert lock.acquire() is LockState.LOCK_BUSY
        assert 0.9 <= time.monotonic() - t0 < 5
        with pytest.raises(LockError), HeavyJobLock(path, operation="extract"):
            pass
    finally:
        holder.kill()
        holder.wait()
