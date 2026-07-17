"""Capability-to-runner result contract tests."""

from __future__ import annotations

import importlib.util

import pytest
from tutor.core.capability_result import CapabilityResult, FollowUpTaskSpec
from tutor.services.resource_package.schema import ArtifactRef


def test_capability_result_module_exists() -> None:
    assert importlib.util.find_spec("tutor.core.capability_result") is not None


def test_capability_result_has_independent_defaults() -> None:
    first = CapabilityResult()
    second = CapabilityResult()

    first.payload["answer"] = 42

    assert second.payload == {}
    assert first.artifacts == ()
    assert first.follow_up_tasks == ()


def test_capability_result_carries_portable_artifacts_and_follow_ups() -> None:
    artifact = ArtifactRef(
        name="lesson.pptx",
        kind="pptx",
        artifact_key="ppt/pkg-1/lesson.pptx",
    )
    follow_up = FollowUpTaskSpec(
        kind="video_render",
        payload={"package_id": "pkg-1", "resource_id": "video-1"},
        dedupe_key="video:pkg-1:video-1",
    )

    result = CapabilityResult(
        assistant_message="资源包已生成",
        payload={"package_id": "pkg-1"},
        artifacts=(artifact,),
        follow_up_tasks=(follow_up,),
    )

    assert result.artifacts[0].artifact_key == "ppt/pkg-1/lesson.pptx"
    assert result.follow_up_tasks[0].kind == "video_render"
    with pytest.raises(AttributeError):
        result.assistant_message = "changed"  # type: ignore[misc]
