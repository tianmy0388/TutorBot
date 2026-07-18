"""Tests for :mod:`tutor.services.resource_package.schema`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tutor.services.resource_package.schema import (
    CodeResource,
    DocumentResource,
    ExerciseQuestion,
    ExerciseResource,
    Resource,
    ResourcePackage,
    ResourceReview,
    ResourceType,
    ReviewVerdict,
    VideoResource,
    build_resource,
)


def test_artifact_ref_serializes_only_portable_key() -> None:
    from tutor.services.resource_package.schema import ArtifactRef

    ref = ArtifactRef.model_validate(
        {
            "name": "figure_1.png",
            "artifact_key": "code_runs/run_1/figure_1.png",
            "kind": "png",
        }
    )

    assert ref.model_dump() == {
        "name": "figure_1.png",
        "artifact_key": "code_runs/run_1/figure_1.png",
        "kind": "png",
    }


def test_artifact_ref_accepts_legacy_relative_path_without_reserializing_path() -> None:
    from tutor.services.resource_package.schema import ArtifactRef

    ref = ArtifactRef.model_validate(
        {"name": "old.png", "path": "code_runs/old/old.png", "kind": "png"}
    )

    assert ref.artifact_key == "code_runs/old/old.png"
    assert "path" not in ref.model_dump()


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------


def test_resource_minimal():
    r = Resource(type=ResourceType.DOCUMENT, title="Test")
    assert r.resource_id  # auto-generated
    assert r.difficulty == 2
    assert r.confidence_score == 0.7
    assert r.estimated_minutes == 5
    assert r.tags == []


def test_resource_title_required():
    with pytest.raises(ValidationError):
        Resource(type=ResourceType.DOCUMENT, title="")


def test_resource_difficulty_in_range():
    Resource(type=ResourceType.DOCUMENT, title="x", difficulty=1)
    Resource(type=ResourceType.DOCUMENT, title="x", difficulty=5)
    with pytest.raises(ValidationError):
        Resource(type=ResourceType.DOCUMENT, title="x", difficulty=0)
    with pytest.raises(ValidationError):
        Resource(type=ResourceType.DOCUMENT, title="x", difficulty=6)


def test_resource_confidence_clamped():
    r = Resource(type=ResourceType.DOCUMENT, title="x", confidence_score=2.0)
    assert r.confidence_score == 1.0
    r = Resource(type=ResourceType.DOCUMENT, title="x", confidence_score=-0.5)
    assert r.confidence_score == 0.0


def test_resource_all_types_supported():
    for t in ResourceType:
        r = Resource(type=t, title=f"Test {t.value}")
        assert r.type == t


def test_resource_parsed_format_specific_returns_model():
    payload = DocumentResource(sections=[{"title": "A", "content": "..."}])
    r = Resource(
        type=ResourceType.DOCUMENT,
        title="x",
        format_specific=payload.model_dump(),
    )
    parsed = r.parsed_format_specific()
    assert isinstance(parsed, DocumentResource)
    assert len(parsed.sections) == 1


def test_resource_parsed_format_specific_wrong_type_returns_none():
    r = Resource(
        type=ResourceType.DOCUMENT,
        title="x",
        format_specific={"not_a_real_field": 1},
    )
    assert r.parsed_format_specific() is None


def test_build_resource_helper():
    r = build_resource(
        type=ResourceType.MINDMAP,
        title="m",
        difficulty=3,
        generated_by=["test"],
    )
    assert r.type == ResourceType.MINDMAP
    assert r.generated_by == ["test"]


# ---------------------------------------------------------------------------
# DocumentResource
# ---------------------------------------------------------------------------


def test_document_resource_basic():
    d = DocumentResource(sections=[{"title": "A", "content": "..."}])
    assert d.has_math is False
    assert len(d.sections) == 1


# ---------------------------------------------------------------------------
# ExerciseResource
# ---------------------------------------------------------------------------


def test_exercise_question_validates_difficulty():
    ExerciseQuestion(id="q1", type="single_choice", question="x", difficulty=2)
    with pytest.raises(ValidationError):
        ExerciseQuestion(id="q1", type="single_choice", question="x", difficulty=10)


def test_exercise_resource_aggregates():
    qs = [
        ExerciseQuestion(id="q1", type="single_choice", question="?", difficulty=2),
        ExerciseQuestion(id="q2", type="true_false", question="?", difficulty=1),
    ]
    er = ExerciseResource(questions=qs, total_questions=2)
    assert er.total_questions == 2
    assert len(er.questions) == 2


# ---------------------------------------------------------------------------
# VideoResource
# ---------------------------------------------------------------------------


def test_video_resource_render_status():
    v = VideoResource(manim_code="...", render_status="rendering")
    assert v.render_status == "rendering"
    v2 = VideoResource(manim_code="", scene_class="X")
    assert v2.render_status == "pending"


def test_video_resource_accepts_structured_render_failure_and_log_manifest():
    v = VideoResource(
        manim_code="from manim import *",
        render_status="failed",
        render_error_code="missing_external_asset",
        render_failure={
            "error_code": "missing_external_asset",
            "summary": "asset missing",
            "traceback_tail": ["FileNotFoundError: person.svg"],
            "log_artifact_key": "manim_logs/child/attempt-01.log",
        },
        artifacts=[
            {
                "name": "attempt-01.log",
                "kind": "render_log",
                "artifact_key": "manim_logs/child/attempt-01.log",
            }
        ],
    )

    assert v.render_failure["error_code"] == "missing_external_asset"
    assert v.artifacts[0].kind == "render_log"


# ---------------------------------------------------------------------------
# CodeResource
# ---------------------------------------------------------------------------


def test_code_resource_basic():
    c = CodeResource(code="print('hi')", language="python")
    assert c.execution_status == "not_run"


# ---------------------------------------------------------------------------
# ResourceReview
# ---------------------------------------------------------------------------


def test_review_verdict_enum():
    r = ResourceReview(
        resource_id="abc", verdict=ReviewVerdict.PASS, quality_score=0.9
    )
    assert r.verdict == ReviewVerdict.PASS


def test_review_quality_score_clamped():
    r = ResourceReview(resource_id="abc", verdict=ReviewVerdict.PASS, quality_score=2.0)
    assert r.quality_score == 1.0


# ---------------------------------------------------------------------------
# ResourcePackage
# ---------------------------------------------------------------------------


def test_package_by_type_and_summary():
    pkg = ResourcePackage(
        topic="LSTM",
        resources=[
            Resource(type=ResourceType.DOCUMENT, title="d", estimated_minutes=10),
            Resource(type=ResourceType.EXERCISE, title="e", estimated_minutes=15),
            Resource(type=ResourceType.VIDEO, title="v", estimated_minutes=5),
        ],
    )
    assert len(pkg.by_type(ResourceType.DOCUMENT)) == 1
    assert pkg.has_type(ResourceType.MINDMAP) is False
    assert pkg.has_type(ResourceType.EXERCISE) is True
    assert pkg.total_minutes() == 30
    s = pkg.summary()
    assert s["resource_count"] == 3
    assert set(s["types"]) == {"document", "exercise", "video"}


def test_package_avg_confidence():
    pkg = ResourcePackage(
        topic="x",
        resources=[
            Resource(type=ResourceType.DOCUMENT, title="a", confidence_score=0.8),
            Resource(type=ResourceType.DOCUMENT, title="b", confidence_score=0.6),
        ],
    )
    assert pkg.summary()["avg_confidence"] == pytest.approx(0.7)
