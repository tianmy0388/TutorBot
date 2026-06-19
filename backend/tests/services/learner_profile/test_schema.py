"""Tests for :mod:`tutor.services.learner_profile.schema`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from tutor.services.learner_profile.schema import (
    CognitiveStyle,
    ErrorPattern,
    GoalType,
    KnowledgeMap,
    LearnerProfile,
    ModalityPreferences,
    MotivationProfile,
    PaceProfile,
    ProfileDiff,
    Urgency,
    apply_diff,
    empty_profile,
)


# ---------------------------------------------------------------------------
# KnowledgeMap
# ---------------------------------------------------------------------------


def test_knowledge_map_init_empty():
    km = KnowledgeMap()
    assert km.scores == {}
    assert km.average_mastery() == 0.0


def test_knowledge_map_set_and_get():
    km = KnowledgeMap()
    km.set("反向传播", 0.7)
    assert km.get("反向传播") == 0.7
    assert km.get("missing", 0.1) == 0.1


def test_knowledge_map_clamp_to_unit_interval():
    km = KnowledgeMap()
    km.set("foo", 1.5)  # > 1
    assert km.scores["foo"] == 1.0
    km.set("bar", -0.5)
    assert km.scores["bar"] == 0.0


def test_knowledge_map_update_delta():
    km = KnowledgeMap()
    km.set("foo", 0.5)
    new = km.update("foo", 0.3)
    assert new == 0.8
    new = km.update("foo", -1.0)  # clamps to 0
    assert new == 0.0


def test_knowledge_map_validator_rejects_nan():
    with pytest.raises(ValidationError):
        KnowledgeMap(scores={"foo": float("nan")})


def test_knowledge_map_weak_strong():
    km = KnowledgeMap(scores={"a": 0.2, "b": 0.5, "c": 0.9, "d": 0.85})
    assert set(km.weak_concepts()) == {"a"}
    assert set(km.strong_concepts()) == {"c", "d"}


# ---------------------------------------------------------------------------
# ProfileDiff application
# ---------------------------------------------------------------------------


def test_apply_diff_knowledge_delta():
    p = empty_profile(user_id="u1")
    p.knowledge_map.set("foo", 0.5)
    diff = ProfileDiff(knowledge_delta={"foo": 0.2, "bar": 0.3})
    apply_diff(p, diff)
    assert p.knowledge_map.get("foo") == pytest.approx(0.7)
    assert p.knowledge_map.get("bar") == pytest.approx(0.3)
    assert p.version == 2


def test_apply_diff_knowledge_set_overrides_after_delta():
    p = empty_profile()
    p.knowledge_map.set("foo", 0.5)
    diff = ProfileDiff(
        knowledge_delta={"foo": 0.1},
        knowledge_set={"foo": 0.9},
    )
    apply_diff(p, diff)
    assert p.knowledge_map.get("foo") == pytest.approx(0.9)


def test_apply_diff_cognitive_style():
    p = empty_profile()
    diff = ProfileDiff(cognitive_style=CognitiveStyle.ACTIVE)
    apply_diff(p, diff)
    assert p.cognitive_style == CognitiveStyle.ACTIVE


def test_apply_diff_error_pattern_upsert():
    p = empty_profile()
    ep1 = ErrorPattern(concept="LSTM", mistake_type="gate_confusion", frequency=1)
    apply_diff(p, ProfileDiff(error_pattern=ep1))
    assert len(p.error_patterns) == 1
    assert p.error_patterns[0].frequency == 1

    # Same concept + mistake_type → bump
    ep2 = ErrorPattern(concept="LSTM", mistake_type="gate_confusion", frequency=1)
    apply_diff(p, ProfileDiff(error_pattern=ep2))
    assert len(p.error_patterns) == 1
    assert p.error_patterns[0].frequency == 2

    # New mistake_type → append
    ep3 = ErrorPattern(concept="LSTM", mistake_type="cell_state_misuse", frequency=1)
    apply_diff(p, ProfileDiff(error_pattern=ep3))
    assert len(p.error_patterns) == 2


def test_apply_diff_pace_and_motivation_replace():
    p = empty_profile()
    p.learning_pace.avg_session_duration_min = 30
    new_pace = PaceProfile(avg_session_duration_min=60, preferred_chunk_size_min=10)
    new_mot = MotivationProfile(goal_type=GoalType.EXAM_PREP, urgency=Urgency.HIGH)
    diff = ProfileDiff(learning_pace=new_pace, motivation=new_mot)
    apply_diff(p, diff)
    assert p.learning_pace.avg_session_duration_min == 60
    assert p.motivation.goal_type == GoalType.EXAM_PREP


def test_apply_diff_modality_replace():
    p = empty_profile()
    p.modality.video = 0.5
    new_mod = ModalityPreferences(video=0.9, diagram=0.95)
    apply_diff(p, ProfileDiff(modality=new_mod))
    assert p.modality.video == 0.9
    assert p.modality.diagram == 0.95
    # Other fields unchanged
    assert p.modality.text == pytest.approx(0.6)


def test_apply_diff_metadata_shallow_merge():
    p = empty_profile()
    p.metadata["course"] = "AI"
    diff = ProfileDiff(metadata_merge={"school": "PKU", "course": "ML"})
    apply_diff(p, diff)
    assert p.metadata == {"course": "ML", "school": "PKU"}


def test_apply_diff_empty_is_noop():
    p = empty_profile()
    apply_diff(p, ProfileDiff())
    assert p.version == 1  # not bumped


def test_apply_diff_bumps_version_and_updates_timestamp():
    p = empty_profile()
    before = p.updated_at
    apply_diff(p, ProfileDiff(knowledge_delta={"x": 0.1}))
    assert p.version == 2
    assert p.updated_at >= before


# ---------------------------------------------------------------------------
# LearnerProfile summary
# ---------------------------------------------------------------------------


def test_profile_summary_keys():
    p = empty_profile(user_id="u1")
    summary = p.to_summary()
    for key in (
        "user_id",
        "version",
        "cognitive_style",
        "knowledge_count",
        "avg_mastery",
        "weak_concepts",
        "strong_concepts",
        "error_pattern_count",
        "goal",
        "urgency",
        "self_efficacy",
        "modality_dominant",
        "session_duration_min",
        "updated_at",
    ):
        assert key in summary


# ---------------------------------------------------------------------------
# Validators reject bad data
# ---------------------------------------------------------------------------


def test_modality_validators_clamp():
    mp = ModalityPreferences(video=2.0, diagram=-0.5)
    assert mp.video == 1.0
    assert mp.diagram == 0.0


def test_motivation_efficacy_in_range():
    mp = MotivationProfile(self_efficacy=1.5)
    assert mp.self_efficacy == 1.0
    mp = MotivationProfile(self_efficacy=-0.3)
    assert mp.self_efficacy == 0.0


def test_pace_non_negative():
    with pytest.raises(ValidationError):
        PaceProfile(avg_session_duration_min=-1)
