"""Tests for the typed job result contract.

The contract is the only thing the frontend should rely on to render
terminal state, so we keep it minimal, strict, and explicit about
required fields like ``assistant_message``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tutor.services.jobs.contracts import (
    ArtifactResult,
    JobError,
    JobProgress,
    JobResultContract,
    JobTerminalStatus,
    JobWarning,
)


def test_terminal_result_requires_visible_message() -> None:
    with pytest.raises(ValidationError):
        JobResultContract(
            job_id="j1",
            capability="tutoring",
            status="succeeded",
            assistant_message="",
        )


def test_partial_result_lists_successes_and_failures() -> None:
    result = JobResultContract(
        job_id="j1",
        capability="resource_generation",
        status="partial",
        assistant_message="已生成 2 项，1 项失败",
        artifacts=[ArtifactResult(resource_type="document", status="succeeded")],
        warnings=[JobWarning(code="ARTIFACT_FAILED", message="video failed")],
    )
    assert result.status == JobTerminalStatus.PARTIAL
    assert result.assistant_message == "已生成 2 项，1 项失败"
    assert len(result.artifacts) == 1
    assert result.warnings[0].code == "ARTIFACT_FAILED"


def test_contract_defaults_to_succeeded_with_progress() -> None:
    contract = JobResultContract(
        job_id="j2",
        capability="resource_generation",
        status="succeeded",
        assistant_message="完成",
    )
    assert contract.progress.stage == ""
    assert contract.progress.percent == 0.0
    assert contract.progress.active_agents == []
    assert contract.artifacts == []
    assert contract.warnings == []
    assert contract.error is None
    assert contract.event_cursor == 0


def test_artifact_carries_error_for_failed_resource() -> None:
    artifact = ArtifactResult(
        resource_type="video",
        status="failed",
        agents=["manim_renderer"],
        error=JobError(code="MANIM_RENDER_FAILED", message="渲染失败", retryable=True),
    )
    assert artifact.status == "failed"
    assert artifact.error is not None
    assert artifact.error.code == "MANIM_RENDER_FAILED"
    assert artifact.error.retryable is True


def test_warning_keeps_resource_type_optional() -> None:
    warning = JobWarning(code="UNVERIFIED_CLAIM", message="未经验证")
    assert warning.resource_type is None
    assert warning.context == {}


def test_job_error_round_trip() -> None:
    err = JobError(
        code="MISSING_RESULT",
        message="能力未返回结构化结果",
        diagnostic="Traceback ...",
        retryable=False,
    )
    payload = err.model_dump(mode="json")
    rebuilt = JobError.model_validate(payload)
    assert rebuilt.code == "MISSING_RESULT"
    assert rebuilt.retryable is False


def test_progress_percent_must_be_in_range() -> None:
    with pytest.raises(ValidationError):
        JobProgress(percent=150.0)
    progress = JobProgress(percent=42.5, stage="rendering", active_agents=["video_agent"])
    assert progress.percent == 42.5
    assert progress.stage == "rendering"
    assert progress.active_agents == ["video_agent"]


def test_contract_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        JobResultContract(
            job_id="j3",
            capability="tutoring",
            status="mystery",  # type: ignore[arg-type]
            assistant_message="hi",
        )


def test_contract_serializes_to_json_safe_dict() -> None:
    contract = JobResultContract(
        job_id="j4",
        capability="resource_generation",
        status="succeeded",
        assistant_message="ok",
        progress=JobProgress(stage="done", percent=100.0, active_agents=["a"]),
        artifacts=[ArtifactResult(resource_type="document", status="succeeded")],
    )
    payload = contract.model_dump(mode="json")
    # Status is the underlying string, not the enum name
    assert payload["status"] == "succeeded"
    assert payload["artifacts"][0]["resource_type"] == "document"
