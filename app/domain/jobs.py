"""Background job domain model.

Every long operation (indexing, unlocking, AI requests, exports, backups, graph
layout, key rotation, synchronisation) is a job. Jobs are cancellable and
observable, and never block the UI thread.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JobType = Literal[
    "indexing",
    "encryption",
    "unlock",
    "ai_request",
    "export",
    "import",
    "backup",
    "restore",
    "graph_layout",
    "embedding",
    "sync",
    "key_rotation",
]

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]

# Whether a job touches decrypted private content. Used to decide what may be
# written into logs, notifications and crash reports.
PrivacyClass = Literal["public", "private", "mixed", "none"]


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: JobType
    title: str
    status: JobStatus = "queued"
    progress: float = 0.0
    detail: str = ""
    layer_id: str | None = None
    privacy: PrivacyClass = "none"
    cancellable: bool = True
    started_at: str | None = None
    ended_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class JobEvent(BaseModel):
    """Pushed to the frontend over ``JobBridge.jobEvent``."""

    model_config = ConfigDict(extra="forbid")

    v: int = 1
    kind: Literal["created", "progress", "succeeded", "failed", "cancelled"]
    job: JobRecord
    data: dict[str, object] = Field(default_factory=dict)
