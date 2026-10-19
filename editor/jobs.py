"""In-memory background job registry."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

JobStatus = Literal["queued", "running", "done", "error"]


@dataclass
class Job:
    id: str
    status: JobStatus = "queued"
    progress: float = 0.0
    message: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._active_detect_job_id: str | None = None

    def create(self) -> Job:
        job = Job(id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        progress: float | None = None,
        message: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = max(0.0, min(1.0, progress))
            if message is not None:
                job.message = message
            if result is not None:
                job.result = result
            if error is not None:
                job.error = error

    def running_detect_job_id(self) -> str | None:
        with self._lock:
            if not self._active_detect_job_id:
                return None
            job = self._jobs.get(self._active_detect_job_id)
            if job and job.status in ("queued", "running"):
                return self._active_detect_job_id
            self._active_detect_job_id = None
            return None

    def claim_detect_job(self, job_id: str) -> str | None:
        """Return busy job id if another detect job is active, else claim this one."""
        with self._lock:
            if self._active_detect_job_id and self._active_detect_job_id != job_id:
                other = self._jobs.get(self._active_detect_job_id)
                if other and other.status in ("queued", "running"):
                    return self._active_detect_job_id
            self._active_detect_job_id = job_id
            return None

    def release_detect_job(self, job_id: str) -> None:
        with self._lock:
            if self._active_detect_job_id == job_id:
                self._active_detect_job_id = None

    def run_in_thread(self, job_id: str, fn: Callable[[], None]) -> None:
        def wrapper() -> None:
            try:
                self.update(job_id, status="running", progress=0.0, message="starting")
                fn()
                self.update(job_id, status="done", progress=1.0, message="complete")
            except Exception as exc:
                self.update(
                    job_id,
                    status="error",
                    error=str(exc),
                    message=str(exc),
                )

        threading.Thread(target=wrapper, daemon=True).start()

    def to_api(self, job: Job) -> dict[str, Any]:
        return {
            "id": job.id,
            "status": job.status,
            "progress": job.progress,
            "message": job.message,
            "result": job.result,
            "error": job.error,
        }
