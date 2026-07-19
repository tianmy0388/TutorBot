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
    public_resource_dump,
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


def test_video_resource_parses_bounded_transient_repair_candidate_state() -> None:
    resource = Resource(
        type=ResourceType.VIDEO,
        title="repair candidate",
        format_specific={
            "manim_code": "ORIGINAL SOURCE",
            "render_status": "failed",
            "repair_candidate_code": "from manim import *\n",
            "repair_candidate_failure": {
                "error_code": "repair_render_failed",
                "summary": "renderer failed internally",
                "traceback_tail": ["safe diagnostic"],
                "log_artifact_key": "manim_logs/child/repair-render.log",
            },
        },
    )

    parsed = resource.parsed_format_specific()

    assert isinstance(parsed, VideoResource)
    assert parsed.repair_candidate_code == "from manim import *\n"
    assert parsed.repair_candidate_failure is not None
    assert parsed.repair_candidate_failure.error_code == "repair_render_failed"


@pytest.mark.parametrize(
    "candidate_state",
    [
        {"repair_candidate_code": "x" * 100_001},
        {
            "repair_candidate_failure": {
                "error_code": "x" * 121,
                "summary": "safe",
                "traceback_tail": [],
            }
        },
        {
            "repair_candidate_failure": {
                "error_code": "repair_failed",
                "summary": "x" * 241,
                "traceback_tail": [],
            }
        },
        {
            "repair_candidate_failure": {
                "error_code": "repair_failed",
                "summary": "safe",
                "traceback_tail": ["x" * 501],
            }
        },
        {
            "repair_candidate_failure": {
                "error_code": "repair_failed",
                "summary": "safe",
                "traceback_tail": ["safe"] * 41,
            }
        },
    ],
)
def test_video_resource_rejects_oversized_transient_candidate_state(
    candidate_state,
) -> None:
    with pytest.raises(ValidationError):
        VideoResource.model_validate(
            {
                "manim_code": "ORIGINAL SOURCE",
                "render_status": "failed",
                **candidate_state,
            }
        )


@pytest.mark.parametrize(
    ("render_status", "state_fields"),
    (
        ("pending", {}),
        (
            "ready",
            {
                "video_url": "/static/manim/MainScene.mp4",
                "artifact_key": "manim_videos/MainScene.mp4",
            },
        ),
        (
            "failed",
            {
                "render_error_code": "process_exit",
                "render_error": "Manim exited before producing a video",
            },
        ),
    ),
)
def test_video_render_job_id_round_trips_through_strict_schema(
    render_status,
    state_fields,
):
    resource = Resource(
        type=ResourceType.VIDEO,
        title="durable video",
        format_specific={
            "manim_code": "from manim import *",
            "scene_class": "MainScene",
            "render_status": render_status,
            "render_job_id": f"child-{render_status}",
            **state_fields,
        },
    )

    reloaded = Resource.model_validate(resource.model_dump(mode="json"))
    parsed = reloaded.parsed_format_specific()

    assert isinstance(parsed, VideoResource)
    assert parsed.render_job_id == f"child-{render_status}"
    assert parsed.model_dump()["render_job_id"] == f"child-{render_status}"


def test_legacy_video_without_render_job_id_still_parses():
    resource = Resource(
        type=ResourceType.VIDEO,
        title="legacy video",
        format_specific={
            "manim_code": "from manim import *",
            "render_status": "ready",
            "video_url": "/static/manim/legacy.mp4",
        },
    )

    parsed = resource.parsed_format_specific()

    assert isinstance(parsed, VideoResource)
    assert parsed.render_job_id is None


def test_public_video_projection_hides_legacy_raw_traceback() -> None:
    raw = (
        "+--- Traceback (most recent call last) ---+\n"
        "E:\\private\\workspace\\scene.py API_KEY=secret-value"
    )
    resource = Resource(
        type=ResourceType.VIDEO,
        title="legacy failed video",
        format_specific={
            "manim_code": "from manim import *\nclass MainScene(Scene): pass",
            "render_status": "failed",
            "render_error": raw,
        },
    )

    public = public_resource_dump(resource)
    serialized = str(public)

    assert "Traceback" not in serialized
    assert "E:\\private" not in serialized
    assert "secret-value" not in serialized
    assert public["format_specific"]["render_error"] == (
        "渲染流程未生成可播放视频。"
    )
    assert public["format_specific"]["manim_code"] == resource.format_specific["manim_code"]


def test_public_video_projection_resanitizes_structured_failure() -> None:
    resource = Resource(
        type=ResourceType.VIDEO,
        title="structured failed video",
        format_specific={
            "render_status": "failed",
            "render_failure": {
                "error_code": "process_exit",
                "summary": "Failed at E:\\private\\scene.py",
                "traceback_tail": ["File E:\\private\\scene.py", "ValueError: bad input"],
                "log_artifact_key": "manim_logs/job/error.log",
            },
        },
    )

    public = public_resource_dump(resource)
    failure = public["format_specific"]["render_failure"]

    assert "E:\\private" not in failure["summary"]
    assert "E:\\private" not in "\n".join(failure["traceback_tail"])
    assert "ValueError: bad input" in failure["traceback_tail"]
    assert failure["log_artifact_key"] == "manim_logs/job/error.log"


def test_public_exercise_projection_hides_every_canonical_answer() -> None:
    questions = [
        {
            "id": "single",
            "type": "single_choice",
            "question": "VISIBLE_PROMPT",
            "options": [
                {"label": "A", "text": "VISIBLE_OPTION"},
                {"label": "B", "text": "other"},
            ],
            "answer": "SECRET-S",
            "explanation": "SECRET_EXPLANATION_SINGLE",
        },
        {"id": "multiple", "type": "multiple_choice", "question": "multiple", "answer": ["SECRET-M1", "SECRET-M2"]},
        {"id": "boolean", "type": "true_false", "question": "boolean", "answer": True},
        {"id": "fill", "type": "fill_blank", "question": "fill", "answer": "SECRET-F"},
        {
            "id": "short",
            "type": "short_answer",
            "question": "short",
            "answer": "(开放式回答)",
            "accepted_answers": ["SECRET-SA"],
            "explanation": "SECRET_EXPLANATION_SHORT",
        },
    ]
    resource = Resource(
        type=ResourceType.EXERCISE,
        title="ordinary exercises",
        content=(
            "### VISIBLE_PROMPT\n\n**答案**：SECRET_ANSWER\n\n"
            "**解析**：SECRET_CONTENT_EXPLANATION"
        ),
        format_specific={"questions": questions},
    )

    public = public_resource_dump(resource)
    projected = public["format_specific"]["questions"]

    assert [question["id"] for question in projected] == [
        "single",
        "multiple",
        "boolean",
        "fill",
        "short",
    ]
    assert all("answer" not in question for question in projected)
    assert all("accepted_answers" not in question for question in projected)
    assert all("explanation" not in question for question in projected)
    assert public["content"] == ""
    assert projected[0]["question"] == "VISIBLE_PROMPT"
    assert projected[0]["options"][0]["text"] == "VISIBLE_OPTION"
    assert "SECRET-" not in str(public)


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


def test_public_video_dump_sanitizes_legacy_repair_history() -> None:
    resource = Resource(
        type=ResourceType.VIDEO,
        title="video",
        format_specific={
            "render_status": "failed",
            "repair_history": [
                {
                    "job_id": "legacy-child",
                    "failed_revision": 1,
                    "status": "failed",
                    "error_code": "repair_failed",
                    "summary": (
                        "provider-token=private-value at "
                        "C:\\private\\scene.py "
                        + ("x" * 1000)
                    ),
                    "traceback": "SECRET UNBOUNDED TRACE " + ("y" * 2000),
                    "log_artifact_key": "C:\\private\\raw.log",
                    "unexpected": "SECRET EXTRA FIELD",
                }
            ],
        },
    )

    public = public_resource_dump(resource)
    history = public["format_specific"]["repair_history"]

    assert len(history) == 1
    assert set(history[0]) <= {
        "job_id",
        "failed_revision",
        "status",
        "error_code",
        "summary",
        "log_artifact_key",
    }
    assert len(history[0]["summary"]) <= 200
    assert "private-value" not in str(history)
    assert "C:\\private" not in str(history)
    assert "UNBOUNDED" not in str(history)
    assert "EXTRA FIELD" not in str(history)
    assert "log_artifact_key" not in history[0]


def test_public_video_dump_sanitizes_legacy_structured_render_failure() -> None:
    resource = Resource(
        type=ResourceType.VIDEO,
        title="video",
        format_specific={
            "render_status": "failed",
            "render_failure": {
                "error_code": "provider-token=SECRET_CODE " + ("x" * 500),
                "summary": "api_key=SECRET_SUMMARY C:\\private\\scene.py",
                "traceback_tail": [
                    "provider-token=SECRET_TRACE C:\\private\\worker.py"
                ],
                "log_artifact_key": "C:\\private\\operator.log",
                "unexpected": "SECRET EXTRA",
            },
        },
    )

    failure = public_resource_dump(resource)["format_specific"]["render_failure"]

    assert set(failure) <= {
        "error_code",
        "summary",
        "traceback_tail",
        "log_artifact_key",
    }
    assert len(failure["error_code"]) <= 120
    assert "SECRET" not in str(failure)
    assert "C:\\private" not in str(failure)
    assert "log_artifact_key" not in failure
    assert "unexpected" not in failure


def test_public_video_dump_sanitizes_legacy_top_level_render_error_code() -> None:
    resource = Resource(
        type=ResourceType.VIDEO,
        title="video",
        format_specific={
            "render_status": "failed",
            "render_error": "legacy failure",
            "render_error_code": "provider-token=SECRET_CODE " + ("x" * 500),
        },
    )

    public = public_resource_dump(resource)["format_specific"]

    assert len(public["render_error_code"]) <= 120
    assert "SECRET_CODE" not in public["render_error_code"]


def test_public_video_dump_omits_private_repair_candidate_state() -> None:
    resource = Resource(
        type=ResourceType.VIDEO,
        title="video",
        format_specific={
            "render_status": "failed",
            "repair_candidate_code": "PRIVATE GENERATED SOURCE",
            "repair_candidate_failure": {
                "error_code": "provider-token=SECRET",
                "summary": "C:\\private\\scene.py",
                "traceback_tail": ["PRIVATE TRACE"],
            },
        },
    )

    public = public_resource_dump(resource)["format_specific"]

    assert "repair_candidate_code" not in public
    assert "repair_candidate_failure" not in public
