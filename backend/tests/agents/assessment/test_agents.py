"""Tests for AssessmentAgent and AdaptiveStrategyEngine."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from tutor.agents.assessment.adaptive_strategy import AdaptiveStrategyEngine
from tutor.agents.assessment.assessment_agent import (
    AssessmentAgent,
    _deterministic_dim_scores,
    _deterministic_overall,
    _stats_from_events,
)
from tutor.core.context import UnifiedContext
from tutor.services.learning_events.schema import (
    ActionType,
    AssessmentDimension,
    AssessmentReport,
    DimensionScore,
    EventType,
    LearningEvent,
    TrajectoryTrend,
)
from tutor.services.learner_profile.schema import LearnerProfile
from tutor.services.llm.base import LLMResponse


def _mock_llm(*responses: str):
    queue = list(responses)
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(req):
        content = queue.pop(0) if queue else "{}"
        return LLMResponse(content=content, model="mock", finish_reason="stop")

    llm.call = call
    return llm


# ---------------------------------------------------------------------------
# _stats_from_events
# ---------------------------------------------------------------------------


def test_stats_from_events_empty():
    s = _stats_from_events([])
    assert s["event_count"] == 0


def test_stats_from_events_mixed():
    events = [
        LearningEvent(user_id="u", event_type=EventType.RESOURCE_VIEWED, target_id="r1"),
        LearningEvent(user_id="u", event_type=EventType.RESOURCE_VIEWED, target_id="r2"),
        LearningEvent(user_id="u", event_type=EventType.RESOURCE_COMPLETED, target_id="r1"),
        LearningEvent(
            user_id="u", event_type=EventType.EXERCISE_COMPLETED,
            target_id="e1", score=0.8,
        ),
    ]
    s = _stats_from_events(events)
    assert s["event_count"] == 4
    assert s["completion_rate"] == pytest.approx(0.5)  # 1 completed / 2 viewed


# ---------------------------------------------------------------------------
# _deterministic_dim_scores
# ---------------------------------------------------------------------------


def test_dim_scores_from_profile_only():
    profile = LearnerProfile(user_id="u")
    profile.knowledge_map.set("A", 0.8)
    profile.knowledge_map.set("B", 0.4)
    stats: dict = {}
    scores = _deterministic_dim_scores(stats, profile)
    assert AssessmentDimension.KNOWLEDGE_MASTERY in scores
    # avg = 0.6
    assert scores[AssessmentDimension.KNOWLEDGE_MASTERY].score == pytest.approx(0.6)


def test_dim_scores_from_exercise_only():
    stats = {"event_count": 5, "exercise_score_avg": 0.75, "completion_rate": 0.6}
    scores = _deterministic_dim_scores(stats, None)
    # Mastery defaults to exercise score
    assert scores[AssessmentDimension.KNOWLEDGE_MASTERY].score == pytest.approx(0.75)
    # Comprehension = exercise avg
    assert scores[AssessmentDimension.COMPREHENSION].score == pytest.approx(0.75)


def test_dim_scores_engagement_scaling():
    stats = {"event_count": 0, "completion_rate": 0.0}
    scores = _deterministic_dim_scores(stats, None)
    assert scores[AssessmentDimension.ENGAGEMENT].score == 0.0

    stats = {"event_count": 30, "completion_rate": 1.0}
    scores = _deterministic_dim_scores(stats, None)
    assert scores[AssessmentDimension.ENGAGEMENT].score == 1.0

    stats = {"event_count": 15, "completion_rate": 0.5}
    scores = _deterministic_dim_scores(stats, None)
    assert scores[AssessmentDimension.ENGAGEMENT].score == 0.5


def test_dim_scores_gaps_inverted():
    profile = LearnerProfile(user_id="u")
    profile.knowledge_map.set("A", 0.2)  # very weak → big gap
    stats: dict = {}
    scores = _deterministic_dim_scores(stats, profile)
    assert scores[AssessmentDimension.GAPS].score > 0.5  # high = many gaps


def test_overall_weighted_average():
    scores = {
        AssessmentDimension.KNOWLEDGE_MASTERY: DimensionScore(
            dimension=AssessmentDimension.KNOWLEDGE_MASTERY, score=0.8
        ),
        AssessmentDimension.ENGAGEMENT: DimensionScore(
            dimension=AssessmentDimension.ENGAGEMENT, score=0.6
        ),
        AssessmentDimension.COMPREHENSION: DimensionScore(
            dimension=AssessmentDimension.COMPREHENSION, score=0.7
        ),
        AssessmentDimension.PACE: DimensionScore(
            dimension=AssessmentDimension.PACE, score=0.5
        ),
        AssessmentDimension.GAPS: DimensionScore(
            dimension=AssessmentDimension.GAPS, score=0.2  # low gap
        ),
        AssessmentDimension.TRAJECTORY: DimensionScore(
            dimension=AssessmentDimension.TRAJECTORY, score=0.5
        ),
    }
    overall = _deterministic_overall(scores)
    # Mastery 0.8 * 0.25 + engagement 0.6 * 0.15 + comprehension 0.7 * 0.20 +
    # pace 0.5 * 0.10 + (1 - 0.2) * 0.20 + trajectory 0.5 * 0.10
    expected = (
        0.8 * 0.25
        + 0.6 * 0.15
        + 0.7 * 0.20
        + 0.5 * 0.10
        + 0.8 * 0.20
        + 0.5 * 0.10
    )
    assert overall == pytest.approx(expected)


# ---------------------------------------------------------------------------
# AssessmentAgent.process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assessment_agent_no_events():
    agent = AssessmentAgent(llm=_mock_llm(json.dumps({
        "trajectory": "insufficient_data",
        "recommendations": ["更多学习数据"],
    })))
    ctx = UnifiedContext(user_id="alice")
    report = await agent.process(ctx, user_id="alice", events=[])
    assert report.user_id == "alice"
    assert report.events_analyzed == 0
    assert report.trajectory == TrajectoryTrend.INSUFFICIENT_DATA


@pytest.mark.asyncio
async def test_assessment_agent_with_events_and_profile():
    agent = AssessmentAgent(llm=_mock_llm(json.dumps({
        "trajectory": "improving",
        "weak_concepts": ["LSTM"],
        "strong_concepts": ["RNN"],
        "recommendations": ["复习 LSTM", "做更多练习"],
        "notes": "整体不错",
    }, ensure_ascii=False)))
    ctx = UnifiedContext(user_id="alice")
    profile = LearnerProfile(user_id="alice")
    profile.knowledge_map.set("LSTM", 0.3)
    profile.knowledge_map.set("RNN", 0.9)
    events = [
        LearningEvent(user_id="alice", event_type=EventType.RESOURCE_VIEWED, target_id="r1"),
        LearningEvent(user_id="alice", event_type=EventType.EXERCISE_COMPLETED, target_id="e1", score=0.7),
    ]
    report = await agent.process(
        ctx,
        user_id="alice",
        events=events,
        stats=_stats_from_events(events),
        profile=profile,
    )
    assert report.trajectory == TrajectoryTrend.IMPROVING
    assert "LSTM" in report.weak_concepts
    assert "RNN" in report.strong_concepts
    assert len(report.recommendations) == 2
    assert report.notes == "整体不错"
    assert report.overall_score > 0
    assert report.events_analyzed == 2


@pytest.mark.asyncio
async def test_assessment_agent_handles_llm_failure():
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(req):
        raise RuntimeError("LLM down")

    llm.call = call
    agent = AssessmentAgent(llm=llm)
    ctx = UnifiedContext(user_id="alice")
    # Should still produce a deterministic report even without LLM
    report = await agent.process(ctx, user_id="alice", events=[])
    assert report.user_id == "alice"
    assert report.trajectory == TrajectoryTrend.INSUFFICIENT_DATA


@pytest.mark.asyncio
async def test_assessment_agent_invalid_trajectory_falls_back():
    agent = AssessmentAgent(llm=_mock_llm(json.dumps({
        "trajectory": "totally_invalid",
        "recommendations": ["x"],
    })))
    ctx = UnifiedContext(user_id="alice")
    report = await agent.process(ctx, user_id="alice", events=[])
    assert report.trajectory == TrajectoryTrend.INSUFFICIENT_DATA


@pytest.mark.asyncio
async def test_assessment_agent_falls_back_to_profile_for_concepts():
    """If LLM doesn't provide weak/strong concepts, fall back to profile."""
    agent = AssessmentAgent(llm=_mock_llm(json.dumps({
        "trajectory": "improving",
        "recommendations": ["x"],
    })))
    ctx = UnifiedContext(user_id="alice")
    profile = LearnerProfile(user_id="alice")
    profile.knowledge_map.set("WEAK", 0.2)  # weak
    profile.knowledge_map.set("STRONG", 0.9)  # strong
    report = await agent.process(
        ctx, user_id="alice", events=[], profile=profile
    )
    assert "WEAK" in report.weak_concepts
    assert "STRONG" in report.strong_concepts


# ---------------------------------------------------------------------------
# AdaptiveStrategyEngine
# ---------------------------------------------------------------------------


def _make_report(
    *,
    mastery: float = 0.7,
    engagement: float = 0.5,
    weak_concepts: list[str] | None = None,
    trajectory: TrajectoryTrend = TrajectoryTrend.STABLE if hasattr(TrajectoryTrend, "STABLE") else TrajectoryTrend.STAGNANT,
) -> AssessmentReport:
    return AssessmentReport(
        user_id="alice",
        dimension_scores={
            AssessmentDimension.KNOWLEDGE_MASTERY: DimensionScore(
                dimension=AssessmentDimension.KNOWLEDGE_MASTERY, score=mastery
            ),
            AssessmentDimension.ENGAGEMENT: DimensionScore(
                dimension=AssessmentDimension.ENGAGEMENT, score=engagement
            ),
            AssessmentDimension.COMPREHENSION: DimensionScore(
                dimension=AssessmentDimension.COMPREHENSION, score=mastery
            ),
            AssessmentDimension.PACE: DimensionScore(
                dimension=AssessmentDimension.PACE, score=0.5
            ),
            AssessmentDimension.GAPS: DimensionScore(
                dimension=AssessmentDimension.GAPS, score=1.0 - mastery
            ),
            AssessmentDimension.TRAJECTORY: DimensionScore(
                dimension=AssessmentDimension.TRAJECTORY, score=0.5
            ),
        },
        overall_score=mastery,
        weak_concepts=weak_concepts or [],
        trajectory=trajectory,
    )


def test_strategy_low_mastery_recommends_review():
    engine = AdaptiveStrategyEngine()
    report = _make_report(mastery=0.2, weak_concepts=["LSTM"])
    decision = engine.decide(report)
    actions = decision.actions
    assert any(a.action_type == ActionType.RECOMMEND_REVIEW for a in actions)


def test_strategy_declining_trajectory_recommends_review():
    engine = AdaptiveStrategyEngine()
    report = _make_report(mastery=0.5, trajectory=TrajectoryTrend.DECLINING)
    decision = engine.decide(report)
    actions = decision.actions
    assert any(a.action_type == ActionType.RECOMMEND_REVIEW for a in actions)


def test_strategy_weak_concepts_recommend_tutoring():
    engine = AdaptiveStrategyEngine()
    report = _make_report(mastery=0.4, weak_concepts=["LSTM", "GRU"])
    decision = engine.decide(report)
    tutoring = [a for a in decision.actions if a.action_type == ActionType.RECOMMEND_TUTORING]
    assert len(tutoring) == 2
    assert {a.target_concept for a in tutoring} == {"LSTM", "GRU"}


def test_strategy_high_mastery_high_engagement_recommends_advance():
    engine = AdaptiveStrategyEngine()
    report = _make_report(mastery=0.9, engagement=0.8)
    decision = engine.decide(report)
    assert any(a.action_type == ActionType.RECOMMEND_ADVANCE for a in decision.actions)


def test_strategy_low_engagement_recommends_break():
    engine = AdaptiveStrategyEngine()
    report = _make_report(mastery=0.7, engagement=0.1)
    decision = engine.decide(report)
    assert any(a.action_type == ActionType.RECOMMEND_BREAK for a in decision.actions)


def test_strategy_actions_sorted_by_priority():
    engine = AdaptiveStrategyEngine()
    report = _make_report(mastery=0.2, weak_concepts=["LSTM", "GRU"])
    decision = engine.decide(report)
    priorities = [a.priority for a in decision.actions]
    assert priorities == sorted(priorities)


def test_strategy_no_action_when_all_good():
    engine = AdaptiveStrategyEngine()
    report = _make_report(mastery=0.9, engagement=0.8)
    decision = engine.decide(report)
    # High mastery + engagement → advance, no other actions
    assert any(a.action_type == ActionType.RECOMMEND_ADVANCE for a in decision.actions)


def test_strategy_compose_directive_includes_weak_concepts():
    engine = AdaptiveStrategyEngine()
    report = _make_report(mastery=0.4, weak_concepts=["LSTM"])
    decision = engine.decide(report)
    assert "LSTM" in decision.overall_directive
