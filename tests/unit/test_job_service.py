"""JobService actually runs work and records success."""

from __future__ import annotations

import time

import pytest

from app.services.job_service import JobService

pytestmark = pytest.mark.gui


def test_submit_runs_work_to_completion() -> None:
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    service = JobService()
    record = service.submit(
        job_type="indexing",
        title="Rebuild",
        work=lambda handle: {"ok": True},
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = service.get(record.id)
        assert current is not None
        if current.status in ("succeeded", "failed", "cancelled"):
            assert current.status == "succeeded"
            return
        QApplication.processEvents()
        time.sleep(0.02)
    raise AssertionError("job did not finish")
