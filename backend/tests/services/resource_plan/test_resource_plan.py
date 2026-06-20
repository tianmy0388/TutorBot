"""Tests for the resource plan service (Task 4)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tutor.services.learner_profile.schema import (
    LearnerProfile,
    ModalityPreferences,
)
from tutor.services.resource_plan.schema import (
    ResourcePlan,
    ResourcePlanRequest,
    SelectedResourceTypes,
    SUPPORTED_RESOURCE_TYPES,
)
from tutor.services.resource_plan.service import (
    build_default_plan,
    recommend_for_profile,
)


def _profile(modality: ModalityPreferences | None = None) -> LearnerProfile:
    return LearnerProfile(
        user_id="u1",
        version=1,
        cognitive_style="visual",
        modality=modality or ModalityPreferences(),
    )


def test_default_plan_contains_core_three() -> None:
    plan = build_default_plan(topic="Transformer", explicit_types=set())
    assert "document" in plan.recommended
    assert "mindmap" in plan.recommended
    assert "exercise" in plan.recommended
    assert "video" not in plan.recommended
    assert "ppt" not in plan.recommended
    assert plan.topic == "Transformer"


def test_explicit_types_are_added_to_recommended() -> None:
    plan = build_default_plan(topic="X", explicit_types={"video", "ppt"})
    assert "video" in plan.recommended
    assert "ppt" in plan.recommended


def test_profile_text_modality_adds_reading() -> None:
    prof = _profile(
        ModalityPreferences(
            text=0.95, video=0.1, code=0.1, audio=0.1,
            interactive=0.3, diagram=0.3, exercise=0.3,
        )
    )
    plan = recommend_for_profile(topic="X", profile=prof, explicit_types=set())
    assert "reading" in plan.recommended
    assert "video" not in plan.recommended
    assert "ppt" not in plan.recommended


def test_profile_code_modality_adds_code() -> None:
    prof = _profile(
        ModalityPreferences(
            text=0.3, code=0.9, video=0.1, audio=0.1,
            interactive=0.3, diagram=0.3, exercise=0.3,
        )
    )
    plan = recommend_for_profile(topic="X", profile=prof, explicit_types=set())
    assert "code" in plan.recommended


def test_video_only_when_explicitly_requested() -> None:
    prof = _profile(
        ModalityPreferences(
            text=0.1, video=0.99, code=0.1, audio=0.1,
            interactive=0.1, diagram=0.1, exercise=0.1,
        )
    )
    plan = recommend_for_profile(topic="X", profile=prof, explicit_types=set())
    assert "video" not in plan.recommended
    plan_with_explicit = recommend_for_profile(
        topic="X", profile=prof, explicit_types={"video"},
    )
    assert "video" in plan_with_explicit.recommended


def test_ppt_only_when_explicitly_requested() -> None:
    prof = _profile(
        ModalityPreferences(
            text=0.99, video=0.1, code=0.1, audio=0.1,
            interactive=0.1, diagram=0.1, exercise=0.1,
        )
    )
    plan = recommend_for_profile(topic="X", profile=prof, explicit_types=set())
    assert "ppt" not in plan.recommended


def test_supported_resource_types_constant() -> None:
    assert SUPPORTED_RESOURCE_TYPES == {
        "document", "mindmap", "exercise", "reading", "video", "code", "ppt",
    }


def test_selected_resource_types_validates_unknown() -> None:
    with pytest.raises(ValidationError):
        SelectedResourceTypes(types=["document", "unknown_type"])


def test_resource_plan_request_message_required() -> None:
    with pytest.raises(ValidationError):
        ResourcePlanRequest(message="")  # empty message invalid
    with pytest.raises(ValidationError):
        ResourcePlanRequest()  # type: ignore[call-arg]


def test_resource_plan_serialization() -> None:
    plan = build_default_plan(topic="X", explicit_types={"video"})
    payload = plan.model_dump(mode="json")
    assert payload["topic"] == "X"
    assert "document" in payload["recommended"]
    assert "video" in payload["recommended"]
    assert "estimated_seconds" in payload


def test_comparison_query_in_router_excludes_video() -> None:
    from tutor.services.intent.router import classify

    decision = classify("对比 Transformer 和 RNN 的差异")
    if decision.resource_plan is not None:
        assert "video" not in decision.resource_plan.recommended
