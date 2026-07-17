"""Internal result returned by capabilities to the job runner.

This module deliberately does not define a second public terminal schema.
``CapabilityResult`` is the in-process hand-off; ``JobResultContract`` remains
the persisted and client-facing terminal contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from tutor.services.resource_package.schema import ArtifactRef


@dataclass(frozen=True)
class FollowUpTaskSpec:
    """Description of deferred work; persistence is owned by the job layer."""

    kind: Literal["video_render", "profile_update", "path_rebuild"]
    payload: dict[str, Any]
    dedupe_key: str


@dataclass(frozen=True)
class CapabilityResult:
    """Successful capability output before runner terminalization."""

    assistant_message: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[ArtifactRef, ...] = ()
    follow_up_tasks: tuple[FollowUpTaskSpec, ...] = ()


__all__ = ["CapabilityResult", "FollowUpTaskSpec"]
