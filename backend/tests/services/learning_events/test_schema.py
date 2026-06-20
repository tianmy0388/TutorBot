"""Tests for :mod:`tutor.services.learning_events.schema`."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from tutor.services.learning_events.schema import (
    ActionType,
    AssessmentDimension,
    AssessmentReport,
    DimensionScore,
    EventType,
    LearningEvent,
    RecommendedAction,
    StrategyDecision,
    TrajectoryTrend,
)


# ---------------------------------------------------------------------------
# LearningEvent
# ---------------------------------------------------------------------------


def test_event_to_from_dict_roundtrip():
    e = LearningEvent(
        user_id="alice",
        event_type=EventType.EXERCISE_COMPLETED,
        target_id="ex-001",
        concept_id="LSTM",
        duration_seconds=120,
        score=0.85,
        correct=True,
        metadata={"question_count": 5},
    )
    d = e.to_dict()
    e2 = LearningEvent.from_dict(d)
    assert e2.user_id == e.user_id
    assert e2.event_type == e.event_type
    assert e2.target_id == e.target_id
    assert e2.duration_seconds == 120
    assert e2.score == pytest.approx(0.85)
    assert e2.correct is True


def test_event_default_id_is_uuid():
    e = LearningEvent()
    assert len(e.event_id) == 32  # uuid4 hex


def test_event_default_timestamp_is_now():
    e = LearningEvent()
    delta = (datetime.now(timezone.utc) - e.created_at).total_seconds()
    assert delta < 1.0


# ---------------------------------------------------------------------------
# DimensionScore
# ---------------------------------------------------------------------------


def test_dimension_score_clamps_high():
    s = DimensionScore(dimension=AssessmentDimension.KNOWLEDGE_MASTERY, score=2.0)
    assert s.score == 1.0


def test_dimension_score_clamps_low():
    s = DimensionScore(dimension=AssessmentDimension.KNOWLEDGE_MASTERY, score=-1.0)
    assert s.score == 0.0


def test_dimension_score_rejects_nan():
    with pytest.raises(ValueError):
        DimensionScore(dimension=AssessmentDimension.KNOWLEDGE_MASTERY, score=float("nan"))


# ---------------------------------------------------------------------------
# AssessmentReport
# ---------------------------------------------------------------------------


def test_assessment_report_to_dict():
    r = AssessmentReport(
        user_id="alice",
        overall_score=0.75,
        trajectory=TrajectoryTrend.IMPROVING,
        weak_concepts=["LSTM"],
        strong_concepts=["RNN"],
        recommendations=["review LSTM"],
    )
    d = r.to_dict()
    assert d["user_id"] == "alice"
    assert d["overall_score"] == 0.75
    assert d["trajectory"] == "improving"
    assert d["weak_concepts"] == ["LSTM"]
    assert d["recommendations"] == ["review LSTM"]


def test_assessment_report_default_window():
    r = AssessmentReport(user_id="x")
    assert r.event_window_hours == 168


# ---------------------------------------------------------------------------
# RecommendedAction / StrategyDecision
# ---------------------------------------------------------------------------


def test_recommended_action_to_dict():
    a = RecommendedAction(
        action_type=ActionType.RECOMMEND_TUTORING,
        target_concept="LSTM",
        target_resource_type="tutoring",
        rationale="weak concept",
        priority=2,
    )
    d = a.to_dict()
    assert d["action_type"] == "recommend_tutoring"
    assert d["target_concept"] == "LSTM"
    assert d["priority"] == 2


def test_strategy_decision_to_dict():
    s = StrategyDecision(
        user_id="alice",
        actions=[
            RecommendedAction(action_type=ActionType.NO_ACTION, priority=5),
        ],
        overall_directive="保持当前路径",
    )
    d = s.to_dict()
    assert d["user_id"] == "alice"
    assert len(d["actions"]) == 1
    assert d["overall_directive"] == "保持当前路径"
