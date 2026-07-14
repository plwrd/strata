"""Job status and the push channel for progress events."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from PySide6.QtCore import QObject, Signal, Slot

from app.bridge.envelope import EmptyRequest, bridge_method
from app.domain.jobs import JobRecord
from app.services.container import Services


class JobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: list[JobRecord] = Field(default_factory=list)


class CancelJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=128)


class CancelJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cancelled: bool


class JobBridge(QObject):
    """The only object that *pushes* to the frontend.

    Everything else is request/response. Events carry a ``JobRecord``, which is
    privacy-classified and never contains decrypted content.
    """

    jobEvent = Signal(str)

    def __init__(self, services: Services, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services
        self._services.jobs.job_event.connect(self.jobEvent)

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(EmptyRequest)
    def list_jobs(self, _request: EmptyRequest) -> JobListResponse:
        return JobListResponse(jobs=self._services.jobs.list_jobs())

    @Slot(str, result=str)  # type: ignore[arg-type]
    @bridge_method(CancelJobRequest)
    def cancel_job(self, request: CancelJobRequest) -> CancelJobResponse:
        return CancelJobResponse(cancelled=self._services.jobs.cancel(request.job_id))
