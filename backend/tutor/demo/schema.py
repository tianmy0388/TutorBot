"""Schemas for competition demo scenarios.

The demo API intentionally returns ready-to-render snapshots instead of
forcing the frontend to stitch profile, path, package, and assessment
records from several endpoints. That keeps the competition route fast
and deterministic while still reusing the project's core domain shapes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DemoScenario(BaseModel):
    """Lightweight scenario card shown by the frontend."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    course: str
    topic: str
    description: str
    persona: str
    goal: str
    estimated_minutes: int = 12
    tags: list[str] = Field(default_factory=list)
    live_prompt: str = ""


class DemoLoadRequest(BaseModel):
    """Request body for loading a demo scenario."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = "competition-demo"
    session_id: str = ""
    persist: bool = True
    mode: Literal["seeded", "live"] = "seeded"


class DemoCheckpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = "competition-demo"
    answer: str
    elapsed_seconds: int = Field(default=30, ge=0, le=3600)


class DemoCheckpointResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correct: bool
    concept: str
    previous_mastery: float
    updated_mastery: float
    profile_version: int
    recommendation: str
    next_path_node: str


class AgentTraceEvent(BaseModel):
    """One visible multi-agent step for the demo timeline."""

    model_config = ConfigDict(extra="forbid")

    id: str
    agent: str
    role: str
    stage: str
    status: Literal["queued", "running", "succeeded", "warning", "failed"] = "succeeded"
    input_summary: str
    output_summary: str
    duration_ms: int = 0
    confidence: float = 0.8
    artifacts: list[str] = Field(default_factory=list)


class DemoLoadResult(BaseModel):
    """Full snapshot returned after a scenario is loaded."""

    model_config = ConfigDict(extra="forbid")

    scenario: DemoScenario
    user_id: str
    session_id: str
    profile: dict[str, Any]
    path: dict[str, Any]
    package: dict[str, Any]
    assessment: dict[str, Any]
    strategy: dict[str, Any]
    agent_trace: list[AgentTraceEvent]
    learning_loop: list[dict[str, Any]]
    teacher_panel: dict[str, Any]
    runtime_warnings: list[str] = Field(default_factory=list)
    live_prompt: str = ""
    mode: Literal["seeded", "live"] = "seeded"
    live_job_id: str = ""
    live_job_status: str = ""
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    loaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


__all__ = [
    "AgentTraceEvent",
    "DemoCheckpointRequest",
    "DemoCheckpointResult",
    "DemoLoadRequest",
    "DemoLoadResult",
    "DemoScenario",
]
