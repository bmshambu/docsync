"""In-memory background job manager with cancel support."""

from __future__ import annotations

import asyncio
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    kind: str
    status: str = "pending"   # pending | running | cancelling | completed | failed
    progress: float = 0.0
    stage: str = ""
    logs: list[dict] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    def log(self, message: str, progress: float | None = None, stage: str | None = None):
        if progress is not None:
            self.progress = round(progress, 4)
        if stage is not None:
            self.stage = stage
        self.logs.append({"ts": _now(), "message": message, "progress": self.progress})
        self.updated_at = _now()

    def cancel(self):
        if self.status == "running":
            self.cancel_event.set()
            self.status = "cancelling"
            self.log("Stop requested — finishing in-flight documents then building partial graph…",
                     stage="cancelling")
            self.updated_at = _now()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "stage": self.stage,
            "logs": self.logs,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}

    def create(self, kind: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def run(self, job: Job, coro_factory: Callable[[Callable, asyncio.Event], Awaitable[dict]]):
        def emit(message: str, progress: float | None = None, stage: str | None = None):
            job.log(message, progress=progress, stage=stage)

        async def _runner():
            job.status = "running"
            job.log("Job started.", progress=0.0, stage="start")
            try:
                result = await coro_factory(emit, job.cancel_event)
                job.result = result
                job.status = "completed"
                job.log("Job completed.", progress=1.0, stage="done")
            except Exception as exc:
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                job.log(f"Job failed: {job.error}", stage="error")
                traceback.print_exc()

        asyncio.create_task(_runner())


job_manager = JobManager()
