"""Tests for the deterministic intent router (Task 4).

The router must classify a user message into a capability and a resource
shape WITHOUT calling any LLM. The precedence rules and the
video/PPT-explicit-only rule are the key product decisions.
"""

from __future__ import annotations

import pytest
from tutor.services.intent.router import (
    classify,
)


@pytest.mark.parametrize(
    ("message", "capability"),
    [
        ("解释一下注意力机制", "tutoring"),
        ("生成一份代码示例", "resource_generation"),
        ("给我做一次测验", "assessment"),
        ("查看我的学习画像", "profile"),
        ("下一步该学什么", "path_planning"),
    ],
)
def test_router_covers_every_public_capability(message: str, capability: str) -> None:
    decision = classify(message)
    assert decision.capability == capability
    assert 0.0 <= decision.confidence <= 1.0
    assert decision.reason


def test_explanation_routes_to_tutoring() -> None:
    decision = classify("解释 self-attention 是什么")
    assert decision.capability == "tutoring"
    assert decision.resource_plan is None


def test_explanation_in_english_routes_to_tutoring() -> None:
    decision = classify("Explain backpropagation")
    assert decision.capability == "tutoring"


def test_explicit_resource_request_routes_to_resource_generation() -> None:
    decision = classify("为 Transformer 制定学习资源")
    assert decision.capability == "resource_generation"
    assert decision.resource_plan is not None
    # Default plan: document, mindmap, exercise are mandatory
    plan = decision.resource_plan
    assert "document" in plan.recommended
    assert "mindmap" in plan.recommended
    assert "exercise" in plan.recommended
    # No video unless explicitly requested
    assert "video" not in plan.recommended
    assert "ppt" not in plan.recommended


def test_explicit_animation_request_includes_video() -> None:
    decision = classify("生成一个注意力机制的动画")
    assert decision.capability == "resource_generation"
    assert decision.resource_plan is not None
    assert "video" in decision.resource_plan.recommended


def test_explicit_video_request_includes_video() -> None:
    decision = classify("制作一个讲解 Transformer 的视频")
    assert decision.resource_plan is not None
    assert "video" in decision.resource_plan.recommended


def test_explicit_ppt_request_includes_ppt() -> None:
    decision = classify("准备一份 Transformer 的 PPT 课件")
    assert decision.resource_plan is not None
    assert "ppt" in decision.resource_plan.recommended


def test_comparison_query_excludes_video() -> None:
    decision = classify("对比 RNN 和 LSTM 的区别")
    assert decision.capability == "tutoring" or decision.capability == "resource_generation"
    if decision.resource_plan is not None:
        assert "video" not in decision.resource_plan.recommended


def test_profile_question_routes_to_profile() -> None:
    decision = classify("查看我的学习画像")
    assert decision.capability == "profile"


def test_assessment_routes_to_assessment() -> None:
    decision = classify("评估一下我的掌握情况")
    assert decision.capability == "assessment"


def test_path_planning_routes_to_path_planning() -> None:
    decision = classify("为我规划下一步学习路径")
    assert decision.capability == "path_planning"


def test_explicit_capability_overrides_keywords() -> None:
    decision = classify("解释 self-attention", explicit_capability="path_planning")
    assert decision.capability == "path_planning"


def test_default_unknown_message_routes_to_tutoring() -> None:
    # No keywords match — must NOT silently pick resource_generation
    # (which used to be the legacy default and caused surprise generations).
    decision = classify("asdfghjkl")
    assert decision.capability == "tutoring"
    assert decision.resource_plan is None


def test_explanation_never_includes_video() -> None:
    # Even with "解释" and a hot topic, no video unless the user says so.
    decision = classify("详细解释 self-attention")
    assert decision.capability == "tutoring"
    assert decision.resource_plan is None


def test_video_never_included_by_default() -> None:
    decision = classify("为深度学习生成学习资源")
    plan = decision.resource_plan
    assert plan is not None
    assert "video" not in plan.recommended
    assert "ppt" not in plan.recommended


def test_decision_carries_topic_when_present() -> None:
    decision = classify("解释 self-attention")
    assert decision.topic == "self-attention"


def test_router_is_pure_and_deterministic() -> None:
    msg = "为 Transformer 制定学习资源"
    plan_id = "plan_test"
    d1 = classify(msg, plan_id_factory=lambda: plan_id)
    d2 = classify(msg, plan_id_factory=lambda: plan_id)
    assert d1 == d2
    # Plan ids are equal because the factory is deterministic.
    assert d1.resource_plan is not None
    assert d1.resource_plan.plan_id == plan_id


def test_exercise_request_routes_to_resource_generation() -> None:
    decision = classify("给我一些关于 Transformer 的练习题")
    assert decision.capability == "resource_generation"
    assert decision.resource_plan is not None
    assert "exercise" in decision.resource_plan.recommended


def test_learning_request_is_resource_generation() -> None:
    decision = classify("我想系统学习一下 Transformer")
    assert decision.capability == "resource_generation"
    assert decision.resource_plan is not None
