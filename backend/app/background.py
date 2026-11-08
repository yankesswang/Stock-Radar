"""Lifecycle helpers for long-running asyncio jobs owned by the API process."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import BinaryIO


logger = logging.getLogger(__name__)

JobFactory = Callable[[], Awaitable[None]]


class LeaderLock:
    """An OS-backed lock proving this process owns singleton background jobs."""

    def __init__(self, handle: BinaryIO, path: Path):
        self._handle = handle
        self.path = path

    def release(self) -> None:
        if self._handle.closed:
            return
        if os.name == "nt":  # pragma: no cover - exercised on Windows deployments
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()


def try_acquire_leader_lock(path: str | Path) -> LeaderLock | None:
    """Acquire a non-blocking process lock, returning ``None`` for followers."""
    lock_path = Path(path).expanduser()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        if os.name == "nt":  # pragma: no cover - exercised on Windows deployments
            import msvcrt

            handle.seek(0)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        handle.close()
        return None
    return LeaderLock(handle, lock_path)


def _report_task_result(task: asyncio.Task[None]) -> None:
    """Surface unexpected job termination instead of losing it in create_task()."""
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logger.error(
            "Background job %s stopped unexpectedly",
            task.get_name(),
            exc_info=(type(exc), exc, exc.__traceback__),
        )


def start_background_jobs(
    jobs: Iterable[tuple[str, JobFactory]],
) -> list[asyncio.Task[None]]:
    """Start named jobs and retain handles for application shutdown."""
    tasks: list[asyncio.Task[None]] = []
    for name, factory in jobs:
        task = asyncio.create_task(factory(), name=name)
        task.add_done_callback(_report_task_result)
        tasks.append(task)
    return tasks


async def stop_background_jobs(tasks: Iterable[asyncio.Task[None]]) -> None:
    """Cancel jobs and wait until each one has released its resources."""
    task_list = list(tasks)
    for task in task_list:
        if not task.done():
            task.cancel()
    if task_list:
        await asyncio.gather(*task_list, return_exceptions=True)
