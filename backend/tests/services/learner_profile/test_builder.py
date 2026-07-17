"""Tests for :mod:`tutor.services.learner_profile.builder`."""

from __future__ import annotations

import pytest
from tutor.services.learner_profile.builder import (
    DialogueSignal,
    ExerciseResult,
    ProfileBuilder,
)
from tutor.services.learner_profile.schema import (
    CognitiveStyle,
    GoalType,
    LearnerProfile,
    ProfileDiff,
    Urgency,
)


@pytest.fixture
async def builder(tmp_path):
    b = ProfileBuilder()
    # Override the store to use a tmp path
    from tutor.services.learner_profile.store import ProfileStore

    b.store = ProfileStore(tmp_path / "builder_test.db")
    await b.initialize()
    yield b
    await b.store.close()


@pytest.mark.asyncio
async def test_create_blank(builder: ProfileBuilder):
    p = await builder.create_blank("u1")
    assert p.user_id == "u1"
    assert p.version == 1
    assert len(p.knowledge_map.scores) == 0


@pytest.mark.asyncio
async def test_ingest_signal_applies_diff(builder: ProfileBuilder):
    signal = DialogueSignal(
        raw_text="我是CS研一，想学LSTM，之前学过基础NN",
        extracted_features={
            "major": "CS",
            "level": "graduate",
            "knowledge": {"神经网络基础": 0.8, "RNN": 0.3},
            "cognitive_style": "visual",
            "motivation": {
                "goal_type": "project_build",
                "urgency": "high",
                "self_efficacy": 0.6,
                "goal_description": "完成 LSTM 项目",
            },
            "modality": {"video": 0.9, "diagram": 0.85},
            "confidence": 0.85,
        },
    )
    profile, diff = await builder.ingest_signal("u2", signal)
    assert profile.user_id == "u2"
    assert profile.knowledge_map.get("神经网络基础") == pytest.approx(0.8)
    assert profile.knowledge_map.get("RNN") == pytest.approx(0.3)
    assert profile.cognitive_style == CognitiveStyle.VISUAL
    assert profile.motivation.goal_type == GoalType.PROJECT_BUILD
    assert profile.motivation.urgency == Urgency.HIGH
    assert profile.modality.video == pytest.approx(0.9)
    assert profile.metadata.get("major") == "CS"
    assert not diff.is_empty()


@pytest.mark.asyncio
async def test_ingest_signal_empty_returns_current(builder: ProfileBuilder):
    signal = DialogueSignal(raw_text="hi", extracted_features={}, confidence=0.1)
    profile, diff = await builder.ingest_signal("u3", signal)
    assert diff.is_empty()
    assert profile.user_id == "u3"


@pytest.mark.asyncio
async def test_ingest_exercise_correct(builder: ProfileBuilder):
    result = ExerciseResult(concept="反向传播", correct=True, difficulty=3)
    profile, diff = await builder.ingest_exercise("u4", result)
    # First apply: starts from 0
    assert profile.knowledge_map.get("反向传播") > 0
    assert diff.knowledge_delta["反向传播"] > 0


@pytest.mark.asyncio
async def test_ingest_exercise_incorrect(builder: ProfileBuilder):
    result = ExerciseResult(
        concept="反向传播", correct=False, mistake_type="chain_rule_sign"
    )
    profile, diff = await builder.ingest_exercise("u5", result)
    assert diff.knowledge_delta["反向传播"] == pytest.approx(-0.05)
    assert diff.error_pattern is not None
    assert diff.error_pattern.mistake_type == "chain_rule_sign"
    # Apply to store
    await builder.ingest_exercise("u5", result)
    p2 = await builder.get("u5")
    assert len(p2.error_patterns) == 1


@pytest.mark.asyncio
async def test_exercise_accumulates_mastery(builder: ProfileBuilder):
    """A series of correct answers should monotonically increase mastery."""
    for _ in range(5):
        await builder.ingest_exercise(
            "u6", ExerciseResult(concept="X", correct=True, difficulty=5)
        )
    p = await builder.get("u6")
    assert p.knowledge_map.get("X") == pytest.approx(0.25 * 5, abs=1e-6) or p.knowledge_map.get("X") == 1.0


@pytest.mark.asyncio
async def test_merge_diffs_accumulates_knowledge(builder: ProfileBuilder):
    diffs = [
        ProfileDiff(knowledge_delta={"X": 0.1}),
        ProfileDiff(knowledge_delta={"X": 0.2}),
        ProfileDiff(knowledge_delta={"X": 0.05}),
    ]
    p = await builder.merge_diffs("u7", diffs, source="test_merge")
    assert p.knowledge_map.get("X") == pytest.approx(0.35)


@pytest.mark.asyncio
async def test_recommended_resource_types(builder: ProfileBuilder):
    p = LearnerProfile()
    p.modality.video = 0.95
    p.modality.diagram = 0.9
    p.modality.code = 0.4
    types = builder.recommended_resource_types(p, top_k=3)
    assert types[0] in ("video", "mindmap")  # highest prefs first
    assert len(types) <= 3


@pytest.mark.asyncio
async def test_recommended_chunk_size(builder: ProfileBuilder):
    from tutor.services.learner_profile.schema import (
        CognitiveStyle,
        LearnerProfile,
        PaceProfile,
    )

    p = LearnerProfile(learning_pace=PaceProfile(preferred_chunk_size_min=20))
    p.cognitive_style = CognitiveStyle.VISUAL
    assert builder.recommended_chunk_size(p) == 24  # 20 * 1.2

    p.cognitive_style = CognitiveStyle.ACTIVE
    assert builder.recommended_chunk_size(p) == 16  # 20 * 0.8

    p.cognitive_style = CognitiveStyle.VERBAL
    assert builder.recommended_chunk_size(p) == 20


@pytest.mark.asyncio
async def test_mastery_breakdown(builder: ProfileBuilder):
    p = LearnerProfile()
    p.knowledge_map.set("a", 0.2)
    p.knowledge_map.set("b", 0.5)
    p.knowledge_map.set("c", 0.9)
    breakdown = builder.mastery_breakdown(p)
    assert breakdown["count"] == 3
    assert breakdown["weak"] == ["a"]
    assert "c" in breakdown["strong"]
    assert 0 < breakdown["average"] < 1


def test_aggregate_scored_events_is_deterministic_and_tracks_evidence() -> None:
    import math

    from tutor.services.learning_events.schema import EventType, LearningEvent

    builder = ProfileBuilder(store=None)
    profile = LearnerProfile(user_id="u")
    events = [
        LearningEvent(
            sequence=index,
            user_id="u",
            event_type=EventType.EXERCISE_SCORED,
            concept_id="attention",
            score=score,
            metadata={"resource_format": resource_format},
        )
        for index, score, resource_format in (
            (1, 0.2, "video"),
            (2, 0.6, "video"),
            (3, 1.0, "exercise"),
        )
    ]

    first = builder.aggregate_events(profile, events, through_sequence=3)
    second = builder.aggregate_events(profile, list(reversed(events)), through_sequence=3)

    assert first.knowledge_map.scores == second.knowledge_map.scores
    assert first.knowledge_map.get("attention") == pytest.approx(0.616)
    assert first.metadata["concept_confidence"]["attention"] == pytest.approx(
        1 - math.exp(-1)
    )
    assert first.metadata["preferred_resource_formats"] == ["video", "exercise"]
    assert first.event_watermark == 3
