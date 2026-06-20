"""AssessmentAgent — produce a multi-dimensional learning assessment.

Pipeline role:

    LearningEventStore.stats() + LearnerProfile + (optional) prior reports
        → AssessmentAgent (LLM)
        → AssessmentReport

The agent combines deterministic stats (computed in :func:`_deterministic_stats`)
with LLM-based qualitative analysis to produce a complete report.

For MVP the LLM produces narrative + trajectory; the 6 dimension scores
default to deterministic values (0-1) computed from events. Phase 4
can let the LLM override the scores.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.learning_events.schema import (
    AssessmentDimension,
    AssessmentReport,
    DimensionScore,
    EventType,
    LearningEvent,
    TrajectoryTrend,
)
from tutor.services.learner_profile.schema import LearnerProfile


ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "trajectory": {
            "type": "string",
            "enum": [t.value for t in TrajectoryTrend],
        },
        "weak_concepts": {"type": "array", "items": {"type": "string"}},
        "strong_concepts": {"type": "array", "items": {"type": "string"}},
        "recommendations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-5 specific, actionable recommendations",
        },
        "notes": {"type": "string"},
    },
    "required": ["trajectory", "recommendations"],
}


class AssessmentAgent(BaseAgent):
    """Multi-dimensional learning effectiveness assessment."""

    module_name = "assessment"
    agent_name = "assessment"
    default_temperature = 0.3
    default_max_tokens = 1024

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        user_id: str,
        events: list[LearningEvent] | None = None,
        stats: dict[str, Any] | None = None,
        profile: LearnerProfile | None = None,
        window_hours: int = 168,
    ) -> AssessmentReport:
        """Run assessment and return the structured report.

        ``events`` / ``stats`` should come from
        :meth:`LearningEventStore.query` / :meth:`LearningEventStore.stats`.
        """
        if events is None:
            events = []
        if stats is None:
            stats = _stats_from_events(events, window_hours=window_hours)

        # Deterministic scores first
        dim_scores = _deterministic_dim_scores(stats, profile)
        overall = _deterministic_overall(dim_scores)

        # LLM narrative + trajectory
        if stream is not None:
            async with stream.stage("assessment_analysis", source=self.agent_name):
                await stream.thinking(
                    f"分析 {len(events)} 条学习事件，生成多维度评估...",
                    source=self.agent_name,
                    stage="assessment_analysis",
                )
                llm_data = await self._ask_llm(stats, profile, dim_scores)
        else:
            llm_data = await self._ask_llm(stats, profile, dim_scores)

        # Merge LLM output (overrides deterministic where provided)
        trajectory = _parse_trajectory(llm_data.get("trajectory"))
        weak = [str(c) for c in (llm_data.get("weak_concepts") or []) if c]
        strong = [str(c) for c in (llm_data.get("strong_concepts") or []) if c]
        recs = [str(r) for r in (llm_data.get("recommendations") or []) if r][:5]
        notes = str(llm_data.get("notes") or "")

        # If LLM didn't provide weak/strong, fall back to profile
        if not weak and profile is not None:
            weak = profile.weak_concepts()[:3]
        if not strong and profile is not None:
            strong = profile.strong_concepts()[:3]

        report = AssessmentReport(
            user_id=user_id,
            dimension_scores=dim_scores,
            overall_score=overall,
            trajectory=trajectory,
            weak_concepts=weak,
            strong_concepts=strong,
            recommendations=recs,
            notes=notes,
            event_window_hours=window_hours,
            events_analyzed=len(events),
        )

        if stream is not None:
            await stream.observation(
                f"评估完成: overall={overall:.2f}, "
                f"trajectory={trajectory.value}, "
                f"weak={len(weak)}, strong={len(strong)}",
                source=self.agent_name,
                stage="assessment_analysis",
                metadata={"overall_score": overall},
            )
        return report

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _ask_llm(
        self,
        stats: dict[str, Any],
        profile: LearnerProfile | None,
        dim_scores: dict[AssessmentDimension, DimensionScore],
    ) -> dict[str, Any]:
        """Ask the LLM for narrative analysis (trajectory + recs)."""
        prompt_data = self.get_prompt_data("zh")
        system = self.get_system_prompt(prompt_data)
        user_msg = self.get_user_prompt(prompt_data).format(
            stats=json.dumps(stats, ensure_ascii=False, default=str, indent=2),
            profile_summary=(
                profile.to_summary() if profile else "(no profile)"
            ),
            dim_scores=json.dumps(
                {k.value: v.to_dict() for k, v in dim_scores.items()},
                ensure_ascii=False,
                indent=2,
            ),
        )
        messages = self.build_messages(system=system, user=user_msg)

        try:
            resp = await self.call_llm(
                messages=messages,
                stream=None,
                source=self.agent_name,
                temperature=self.default_temperature,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            logger.warning(f"AssessmentAgent LLM failed: {exc!r}")
            return {}

        data = self.parse_json_response(resp.content, fallback={})
        return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Deterministic helpers (no LLM)
# ---------------------------------------------------------------------------


def _stats_from_events(
    events: list[LearningEvent], *, window_hours: int = 168
) -> dict[str, Any]:
    """Recompute a stats dict from a list of events (used in tests)."""
    if not events:
        return {
            "event_count": 0,
            "by_type": {},
            "total_duration_seconds": 0,
            "concepts_touched": [],
            "exercise_score_avg": None,
            "completion_rate": 0.0,
            "window_hours": window_hours,
        }
    by_type: Counter[str] = Counter(e.event_type.value for e in events)
    total_duration = sum(e.duration_seconds for e in events)
    concepts = {e.concept_id for e in events if e.concept_id}
    exercise_scores = [
        e.score for e in events
        if e.event_type in (EventType.EXERCISE_ATTEMPTED, EventType.EXERCISE_COMPLETED)
        and e.score is not None
    ]
    avg_score = (
        sum(exercise_scores) / len(exercise_scores)
        if exercise_scores
        else None
    )
    completed = by_type.get(EventType.RESOURCE_COMPLETED.value, 0)
    viewed = by_type.get(EventType.RESOURCE_VIEWED.value, 0)
    completion_rate = completed / viewed if viewed > 0 else 0.0
    return {
        "event_count": len(events),
        "by_type": dict(by_type),
        "total_duration_seconds": total_duration,
        "concepts_touched": sorted(concepts),
        "exercise_score_avg": avg_score,
        "completion_rate": completion_rate,
        "window_hours": window_hours,
    }


def _deterministic_dim_scores(
    stats: dict[str, Any],
    profile: LearnerProfile | None,
) -> dict[AssessmentDimension, DimensionScore]:
    """Compute 6 dimension scores from stats + profile."""
    scores: dict[AssessmentDimension, DimensionScore] = {}

    # 1. Knowledge mastery: avg profile mastery OR (exercise score * 0.5)
    if profile is not None and profile.knowledge_map.scores:
        mastery = profile.knowledge_map.average_mastery()
        scores[AssessmentDimension.KNOWLEDGE_MASTERY] = DimensionScore(
            dimension=AssessmentDimension.KNOWLEDGE_MASTERY,
            score=mastery,
            evidence=[
                f"已掌握 {len(profile.knowledge_map.known_concepts())} 个概念",
                f"平均掌握度 {mastery:.2f}",
            ],
        )
    elif stats.get("exercise_score_avg") is not None:
        scores[AssessmentDimension.KNOWLEDGE_MASTERY] = DimensionScore(
            dimension=AssessmentDimension.KNOWLEDGE_MASTERY,
            score=stats["exercise_score_avg"],
            evidence=[f"练习平均分 {stats['exercise_score_avg']:.2f}"],
        )
    else:
        scores[AssessmentDimension.KNOWLEDGE_MASTERY] = DimensionScore(
            dimension=AssessmentDimension.KNOWLEDGE_MASTERY,
            score=0.5,
            evidence=["暂无数据"],
        )

    # 2. Engagement: event count scaled
    count = stats.get("event_count", 0)
    engagement = min(1.0, count / 30.0)  # 30 events = full engagement
    scores[AssessmentDimension.ENGAGEMENT] = DimensionScore(
        dimension=AssessmentDimension.ENGAGEMENT,
        score=engagement,
        evidence=[
            f"{count} 条学习事件",
            f"总时长 {stats.get('total_duration_seconds', 0)} 秒",
        ],
    )

    # 3. Comprehension: exercise score avg (0.5 if no data)
    ex_avg = stats.get("exercise_score_avg")
    comprehension = ex_avg if ex_avg is not None else 0.5
    scores[AssessmentDimension.COMPREHENSION] = DimensionScore(
        dimension=AssessmentDimension.COMPREHENSION,
        score=comprehension,
        evidence=(
            [f"练习平均分 {ex_avg:.2f}"] if ex_avg is not None else ["暂无练习数据"]
        ),
    )

    # 4. Pace: completion rate
    pace = stats.get("completion_rate", 0.0)
    scores[AssessmentDimension.PACE] = DimensionScore(
        dimension=AssessmentDimension.PACE,
        score=pace,
        evidence=[f"完成率 {pace:.0%} (完成/查看)"],
    )

    # 5. Gaps: 1 - avg mastery (lower mastery = bigger gap)
    if profile is not None and profile.knowledge_map.scores:
        gap_score = 1.0 - profile.knowledge_map.average_mastery()
    else:
        gap_score = 0.5
    scores[AssessmentDimension.GAPS] = DimensionScore(
        dimension=AssessmentDimension.GAPS,
        score=gap_score,
        evidence=[
            "分数越高=薄弱点越多" if gap_score > 0.5 else "薄弱点较少",
        ],
        notes="inverted: high score = many gaps",
    )

    # 6. Trajectory: needs time-series — fallback to data presence
    trajectory_score = 0.5 if count >= 5 else 0.3
    scores[AssessmentDimension.TRAJECTORY] = DimensionScore(
        dimension=AssessmentDimension.TRAJECTORY,
        score=trajectory_score,
        evidence=[f"基于 {count} 条事件估算"],
    )

    return scores


def _deterministic_overall(
    dim_scores: dict[AssessmentDimension, DimensionScore],
) -> float:
    """Weighted average of dimension scores (GAPS inverted)."""
    weights = {
        AssessmentDimension.KNOWLEDGE_MASTERY: 0.25,
        AssessmentDimension.ENGAGEMENT: 0.15,
        AssessmentDimension.COMPREHENSION: 0.20,
        AssessmentDimension.PACE: 0.10,
        AssessmentDimension.GAPS: 0.20,  # inverted
        AssessmentDimension.TRAJECTORY: 0.10,
    }
    total = 0.0
    for dim, w in weights.items():
        s = dim_scores.get(dim)
        if s is None:
            continue
        v = s.score
        if dim == AssessmentDimension.GAPS:
            v = 1.0 - v  # invert
        total += w * v
    return max(0.0, min(1.0, total))


def _parse_trajectory(value: Any) -> TrajectoryTrend:
    """Parse LLM trajectory output into enum."""
    if not isinstance(value, str):
        return TrajectoryTrend.INSUFFICIENT_DATA
    try:
        return TrajectoryTrend(value.lower())
    except ValueError:
        return TrajectoryTrend.INSUFFICIENT_DATA


__all__ = ["AssessmentAgent", "ASSESSMENT_SCHEMA"]
