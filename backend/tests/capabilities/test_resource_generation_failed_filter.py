"""Regression test: resources with ``render_status=\"failed\"`` must be
filtered out BEFORE quality review.

Pre-fix, when :class:`ManimVideoAgent` failed to generate valid Manim
code, it returned a :class:`Resource` whose ``format_specific`` had
``render_status=\"failed\"`` and content like \"视频生成失败 — 重新提交
请求或简化主题描述\". The resource was added to the package and went
through quality review, where the reviewer (correctly) gave it
``verdict=reject, score=0.10``. The reject filter then stripped it
from the final package — but the user saw two confusing things:

  1. ``已过滤 1 个质量不达标的资源（保留 5/6）`` — a \"rejected\" card
     in the trace panel with no clear connection to the original
     \"video generation failed\" message
  2. The ``video_rendering`` stage still fired with zero pending
     videos, so the trace showed a no-op ▶ video_rendering

The fix: filter out resources whose payload self-reports a hard
failure (video ``render_status=\"failed\"``) **before** quality review.
The reviewer only sees resources that have a chance of being
publishable, and the user gets a clear \"video generation skipped\"
message via the stream observation.
"""

from __future__ import annotations

import sys

import pytest
from tutor.capabilities.resource_generation import (
    _is_failed_resource,
    _is_malformed_resource,
)
from tutor.services.resource_package.schema import (
    CodeResource,
    Resource,
    ResourceType,
    VideoResource,
)


def _video(render_status: str = "pending") -> Resource:
    return Resource(
        type=ResourceType.VIDEO,
        title="测试视频",
        content="some content",
        format_specific=VideoResource(
            manim_code="class MainScene(Scene): pass",
            scene_class="MainScene",
            render_status=render_status,  # type: ignore[arg-type]
        ).model_dump(),
    )


def _code(execution_status: str = "success") -> Resource:
    return Resource(
        type=ResourceType.CODE,
        title="测试代码",
        content="print('hi')",
        format_specific=CodeResource(
            language="python",
            code="print('hi')",
            execution_status=execution_status,  # type: ignore[arg-type]
            artifacts=[],
        ).model_dump(),
    )


def test_failed_video_is_filtered() -> None:
    r = _video(render_status="failed")
    assert _is_failed_resource(r) is True


def test_pending_video_is_not_filtered() -> None:
    r = _video(render_status="pending")
    assert _is_failed_resource(r) is False


def test_ready_video_is_not_filtered() -> None:
    r = _video(render_status="ready")
    assert _is_failed_resource(r) is False


def test_failed_code_is_not_filtered() -> None:
    # Code resources can fail at execution (env missing, timeout) but
    # the code itself may still be educational. Let the quality
    # reviewer decide, not the pre-filter.
    r = _code(execution_status="failed")
    assert _is_failed_resource(r) is False


def test_successful_code_is_not_filtered() -> None:
    r = _code(execution_status="success")
    assert _is_failed_resource(r) is False


def test_resource_without_format_specific_is_not_filtered() -> None:
    r = Resource(type=ResourceType.VIDEO, title="x", content="y", format_specific={})
    assert _is_failed_resource(r) is False


def test_pending_video_without_executable_manim_contract_is_malformed() -> None:
    resource = Resource(
        type=ResourceType.VIDEO,
        title="empty video",
        content="storyboard only",
        format_specific={"render_status": "pending"},
    )

    assert _is_malformed_resource(resource) is True


def test_resource_with_blank_public_content_is_malformed() -> None:
    resource = Resource(
        type=ResourceType.DOCUMENT,
        title="blank",
        content="   ",
    )

    assert _is_malformed_resource(resource) is True


def test_pending_video_with_executable_manim_contract_is_usable() -> None:
    resource = Resource(
        type=ResourceType.VIDEO,
        title="usable video",
        content="storyboard",
        format_specific={
            "render_status": "pending",
            "manim_code": "class MainScene(Scene): pass",
            "scene_class": "MainScene",
        },
    )

    assert _is_malformed_resource(resource) is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))
