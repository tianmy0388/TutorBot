"""Job schemas — Pydantic models for async background tasks.

A :class:`Job` represents one execution of a capability. Unlike a turn
(which is a synchronous WS stream), a Job persists in the DB so the
frontend can re-subscribe after a page reload, cancel it from another
tab, or queue multiple jobs in parallel.

Status lifecycle:

    pending → running → completed
                  ↘  failed
                  ↘  cancelled

Phase 5.2 design notes:

- We do NOT introduce Celery/Arq/Redis. JobRunner uses an in-process
  asyncio.Task pool; JobStore persists state. If we ever need to scale
  beyond one process, JobStore is the single source of truth and the
  Runner can be replaced.
- ``events`` is a JSON list of serialized :class:`StreamEvent` dicts.
  We keep the full trace (not just summaries) so a subscriber that
  connects mid-run can replay the latest state on reconnect.
- ``result`` is the parsed result payload from the final ``result``
  event (so the frontend doesn't have to JSON-parse ``content``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):
    """Lifecycle state of a :class:`Job`."""

    PENDING = "pending"        # accepted, not yet started
    RUNNING = "running"        # capability task in flight
    COMPLETED = "completed"    # DONE received, result captured
    FAILED = "failed"          # exception or unrecoverable error
    CANCELLED = "cancelled"    # cancelled by user or orchestrator


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


class JobSubmit(BaseModel):
    """Client-supplied payload for ``submit_job``."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = "anonymous"
    message: str = ""
    capability: str | None = None
    language: str = "zh"
    session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class Job(BaseModel):
    """Persisted record for one async capability execution."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    user_id: str = "anonymous"
    session_id: str = ""
    capability: str = "resource_generation"

    # Inputs
    message: str = ""
    language: str = "zh"
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Lifecycle
    status: JobStatus = JobStatus.PENDING
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None

    # Output
    result: dict[str, Any] | None = None
    event_count: int = 0
    last_seq: int = 0

    # Cached serialized events for replay (capped; see JobRunner)
    events: list[dict[str, Any]] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def to_summary(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "capability": self.capability,
            "status": self.status.value,
            "message_preview": (self.message[:60] + "…") if len(self.message) > 60 else self.message,
            "language": self.language,
            "event_count": self.event_count,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": (
                round((self.finished_at - self.started_at).total_seconds(), 2)
                if self.started_at and self.finished_at
                else None
            ),
            "has_result": self.result is not None,
            "error": self.error,
        }

    def to_full_dict(self) -> dict[str, Any]:
        d = self.to_summary()
        d["message"] = self.message
        d["language"] = self.language
        d["metadata"] = self.metadata
        d["result"] = self.result
        d["events"] = self.events
        return d


__all__ = [
    "Job",
    "JobStatus",
    "JobSubmit",
]