"""File-backed status records for user-triggered background jobs."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .config import settings


logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobRegistry:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._tasks: set[asyncio.Task[None]] = set()

    def _path(self, job_id: str) -> Path:
        normalized = str(UUID(job_id))
        return self.root / f"{normalized}.json"

    def _write(self, record: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(record["id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)

    def get(self, job_id: str) -> dict[str, Any] | None:
        try:
            path = self._path(job_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not read job status %s", job_id)
            return None

    def submit(
        self,
        name: str,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        job_id = str(uuid4())
        record: dict[str, Any] = {
            "id": job_id,
            "name": name,
            "status": "queued",
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
        self._write(record)

        async def run() -> None:
            record.update(status="running", started_at=_now())
            self._write(record)
            try:
                result = await asyncio.to_thread(function, *args, **kwargs)
                record.update(status="succeeded", result=result)
            except asyncio.CancelledError:
                record.update(status="cancelled", error="application shutdown")
                raise
            except Exception as exc:  # job failures belong in the status record
                logger.exception("Background job %s failed", job_id)
                record.update(status="failed", error=str(exc)[:1000])
            finally:
                record["finished_at"] = _now()
                self._write(record)

        task = asyncio.create_task(run(), name=f"job:{name}:{job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return record

    async def shutdown(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


job_registry = JobRegistry(settings.DATA_DIR / "jobs")
