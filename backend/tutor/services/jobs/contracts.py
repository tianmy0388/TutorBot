"""Typed job result contract.

Every terminal job emits a single :class:`JobResultContract`. The
frontend uses it to render the visible ``assistant_message``, surface
``warnings``/``error``, and re-subscribe from ``event_cursor``.

Why a contract (and not a free-form dict)?

- The no-output regression in this codebase came from the frontend
  guessing event ownership. A typed terminal result eliminates the
  guess by making the visible chat content a required field.
- The contract forces the runner to commit to one of four states
  (``succeeded`` / ``partial`` / ``failed`` / ``cancelled``) instead
  of conflating "no result event" with success.
- Warnings and errors are first-class so unverified claims survive
  to the UI rather than being silently swallowed.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class JobTerminalStatus(str, Enum):  # noqa: UP042 - wire enum compatibility
    """Terminal outcome of a job.

    Distinct from :class:`~tutor.services.jobs.schema.JobStatus`:
    ``JobStatus`` tracks the persistent lifecycle (``PENDING``,
    ``RUNNING``, ``SUCCEEDED`` …), whereas ``JobTerminalStatus`` is
    the value carried inside the contract and is the *only* thing
    the frontend should switch on to render the terminal UI.
    """

    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobProgress(BaseModel):
    """Snapshot of in-flight job progress."""

    model_config = ConfigDict(extra="forbid")

    stage: str = ""
    percent: float = Field(0.0, ge=0.0, le=100.0)
    active_agents: list[str] = Field(default_factory=list)


class JobError(BaseModel):
    """Stable error payload.

    ``code`` is a short, machine-stable identifier (used for i18n and
    analytics). ``message`` is the user-facing string. ``diagnostic``
    is an opaque protected-artifact key; raw details never enter this contract.
    ``retryable`` tells the UI whether to expose a retry affordance.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    diagnostic: str = ""
    retryable: bool = True


class ArtifactResult(BaseModel):
    """One resource produced by the job."""

    model_config = ConfigDict(extra="forbid")

    resource_type: str
    status: str = "succeeded"  # "succeeded" | "failed"
    resource_id: str | None = None
    title: str | None = None
    duration_seconds: float = 0.0
    agents: list[str] = Field(default_factory=list)
    error: JobError | None = None
    # Free-form metadata for resource-specific outputs (paths, ids).
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobWarning(BaseModel):
    """Non-fatal issue that the UI must surface.

    Warnings are how we preserve unverified claims: instead of
    silently dropping them, we attach a warning so reviewers can
    see exactly what was not grounded in a source.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    resource_type: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class FollowUpTaskContract(BaseModel):
    """Durable public projection of an internal ``FollowUpTaskSpec``."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["video_render", "profile_update", "path_rebuild"]
    payload: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str = Field(min_length=1)


class JobResultContract(BaseModel):
    """The single, typed terminal result of a job.

    Required fields:
    - ``job_id`` and ``capability`` echo the originating job.
    - ``status`` is one of :class:`JobTerminalStatus`.
    - ``assistant_message`` is *always* a non-empty user-facing
      summary — this is what the chat pane shows after the job
      ends. Empty strings fail validation, which is exactly the
      guard that prevents the no-output regression.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str
    capability: str
    status: JobTerminalStatus
    assistant_message: str = Field(min_length=1)

    progress: JobProgress = Field(default_factory=JobProgress)
    artifacts: list[ArtifactResult] = Field(default_factory=list)
    # **2026-07-08 fix (187b2955):** resources that were already emitted
    # to the stream before a timeout / cancellation / late-stage error
    # (e.g. safety check, video render). The capability now streams
    # ``RESOURCE`` events incrementally, so even when ``status`` is
    # FAILED or PARTIAL the user can still see the partial result.
    # ``artifacts`` stays the canonical "delivered" set (used for
    # status inference); ``partial_artifacts`` is for observability +
    # UI rendering when ``artifacts`` is empty.
    partial_artifacts: list[ArtifactResult] = Field(default_factory=list)
    warnings: list[JobWarning] = Field(default_factory=list)
    follow_up_tasks: list[FollowUpTaskContract] = Field(default_factory=list)
    error: JobError | None = None
    event_cursor: int = 0
    finished_at: datetime | None = None


__all__ = [
    "ArtifactResult",
    "FollowUpTaskContract",
    "JobError",
    "JobProgress",
    "JobResultContract",
    "JobTerminalStatus",
    "JobWarning",
]
