"""Background job manager.

Long work runs on a Qt thread pool, never on the UI thread. The frontend learns
about it through :class:`~app.bridge.job_bridge.JobBridge`, which forwards the
signal below over the WebChannel.

Privacy: a job's ``detail`` string is shown in the UI *and* written to the local
log, so it must never contain decrypted private content. Jobs that touch a
private layer are marked ``privacy="private"`` and their detail is limited to
counts and stage names.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app.domain.ids import new_job_id
from app.domain.jobs import JobEvent, JobRecord, JobType, PrivacyClass
from app.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class JobHandle:
    """Passed to the worker function so it can report progress and see cancels."""

    def __init__(self, record: JobRecord, service: JobService) -> None:
        self._record = record
        self._service = service
        self._cancelled = False

    @property
    def id(self) -> str:
        return self._record.id

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True

    def progress(self, value: float, detail: str = "") -> None:
        self._service._emit_progress(self._record.id, value, detail)


class _JobRunnable(QRunnable):
    def __init__(
        self,
        service: JobService,
        handle: JobHandle,
        work: Callable[[JobHandle], dict[str, Any]],
    ) -> None:
        super().__init__()
        self._service = service
        self._handle = handle
        self._work = work

    def run(self) -> None:  # pragma: no cover - exercised via JobService tests
        service = self._service
        handle = self._handle
        service._transition(handle.id, "running")
        try:
            result = self._work(handle)
            if handle.is_cancelled:
                service._transition(handle.id, "cancelled")
            else:
                service._transition(handle.id, "succeeded", data=result)
        except Exception as exc:
            logger.exception("job.failed", job_id=handle.id)
            service._transition(
                handle.id,
                "failed",
                error_code="internal",
                error_message=type(exc).__name__,
            )


class JobService(QObject):
    """Owns every background job. One instance per application."""

    job_event = Signal(str)  # JSON-encoded JobEvent

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._jobs: dict[str, JobRecord] = {}
        self._handles: dict[str, JobHandle] = {}
        self._pool = QThreadPool.globalInstance()

    def submit(
        self,
        *,
        job_type: JobType,
        title: str,
        work: Callable[[JobHandle], dict[str, Any]],
        layer_id: str | None = None,
        privacy: PrivacyClass = "none",
        cancellable: bool = True,
    ) -> JobRecord:
        record = JobRecord(
            id=new_job_id(),
            type=job_type,
            title=title,
            status="queued",
            layer_id=layer_id,
            privacy=privacy,
            cancellable=cancellable,
            started_at=_now(),
        )
        handle = JobHandle(record, self)
        self._jobs[record.id] = record
        self._handles[record.id] = handle
        self._publish("created", record)
        self._pool.start(_JobRunnable(self, handle, work))
        return record

    def list_jobs(self) -> list[JobRecord]:
        return list(self._jobs.values())

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        handle = self._handles.get(job_id)
        record = self._jobs.get(job_id)
        if handle is None or record is None or not record.cancellable:
            return False
        if record.status in ("succeeded", "failed", "cancelled"):
            return False
        handle.cancel()
        return True

    # -- internal ------------------------------------------------------------

    def _emit_progress(self, job_id: str, value: float, detail: str) -> None:
        record = self._jobs.get(job_id)
        if record is None:
            return
        record.progress = max(0.0, min(1.0, value))
        record.detail = detail
        self._publish("progress", record)

    def _transition(
        self,
        job_id: str,
        status: str,
        *,
        data: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        record = self._jobs.get(job_id)
        if record is None:
            return
        record.status = status  # type: ignore[assignment]
        if status in ("succeeded", "failed", "cancelled"):
            record.ended_at = _now()
            if status == "succeeded":
                record.progress = 1.0
        record.error_code = error_code
        record.error_message = error_message
        kind = {
            "running": "progress",
            "succeeded": "succeeded",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(status, "progress")
        self._publish(kind, record, data or {})

    def _publish(
        self,
        kind: str,
        record: JobRecord,
        data: dict[str, Any] | None = None,
    ) -> None:
        event = JobEvent(kind=kind, job=record, data=data or {})  # type: ignore[arg-type]
        self.job_event.emit(event.model_dump_json())
