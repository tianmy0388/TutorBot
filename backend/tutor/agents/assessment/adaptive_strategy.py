"""AdaptiveStrategyEngine — rule-based decision engine.

Given an :class:`AssessmentReport`, produce a :class:`StrategyDecision`
with ranked :class:`RecommendedAction` items.

Rules (applied in order):

1. **Mastery very low (<0.3)**  → recommend_review (priority 1-3)
2. **Mastery high (>0.85) + active** → recommend_advance (priority 5)
3. **Weak concepts present** → recommend_tutoring for each (priority 2-4)
4. **Low engagement (<0.2)**  → recommend_break OR adjust_pace
5. **High engagement + low mastery** → recommend_practice (priority 3)
6. **Trajectory declining** → recommend_review (priority 2)
7. **All good** → no_action
"""

from __future__ import annotations

from typing import Any

from tutor.services.learning_events.schema import (
    ActionType,
    AssessmentDimension,
    AssessmentReport,
    RecommendedAction,
    StrategyDecision,
    TrajectoryTrend,
)


class AdaptiveStrategyEngine:
    """Pure-rule decision engine — no LLM call required."""

    # Tunable thresholds
    MASTERY_LOW = 0.3
    MASTERY_HIGH = 0.85
    ENGAGEMENT_LOW = 0.2
    ENGAGEMENT_HIGH = 0.7

    def decide(
        self,
        report: AssessmentReport,
        *,
        concepts_touched: list[str] | None = None,
    ) -> StrategyDecision:
        actions: list[RecommendedAction] = []
        mastery = report.dimension_scores.get(
            AssessmentDimension.KNOWLEDGE_MASTERY,
            None,
        )
        mastery_val = mastery.score if mastery else 0.5

        engagement = report.dimension_scores.get(
            AssessmentDimension.ENGAGEMENT, None
        )
        engagement_val = engagement.score if engagement else 0.5

        # Rule 1: very low mastery → review
        if mastery_val < self.MASTERY_LOW:
            target = (
                report.weak_concepts[0]
                if report.weak_concepts
                else "(general review)"
            )
            actions.append(
                RecommendedAction(
                    action_type=ActionType.RECOMMEND_REVIEW,
                    target_concept=target,
                    target_resource_type="document",
                    rationale=(
                        f"掌握度 {mastery_val:.0%} 偏低，建议先复习基础"
                    ),
                    priority=1,
                )
            )

        # Rule 6: declining trajectory → also review
        if report.trajectory == TrajectoryTrend.DECLINING:
            target = (
                report.weak_concepts[0]
                if report.weak_concepts
                else "(general)"
            )
            actions.append(
                RecommendedAction(
                    action_type=ActionType.RECOMMEND_REVIEW,
                    target_concept=target,
                    target_resource_type="video",
                    rationale="学习轨迹下降，建议回顾最近学习内容",
                    priority=2,
                )
            )

        # Rule 3: weak concepts → tutoring (limit to top 3)
        for i, concept in enumerate(report.weak_concepts[:3]):
            actions.append(
                RecommendedAction(
                    action_type=ActionType.RECOMMEND_TUTORING,
                    target_concept=concept,
                    target_resource_type="tutoring",
                    rationale=f"薄弱点 {concept}：建议提问深入理解",
                    priority=2 + i,
                    metadata={"concept": concept},
                )
            )

        # Rule 5: high engagement + low mastery → more practice
        if (
            engagement_val >= self.ENGAGEMENT_HIGH
            and mastery_val < 0.6
            and not actions  # don't duplicate if we already have review
        ):
            actions.append(
                RecommendedAction(
                    action_type=ActionType.RECOMMEND_PRACTICE,
                    target_concept=(
                        report.weak_concepts[0]
                        if report.weak_concepts
                        else "(general)"
                    ),
                    target_resource_type="exercise",
                    rationale="学习积极但掌握度不够，建议多做练习",
                    priority=3,
                )
            )

        # Rule 2: high mastery + active → advance
        if (
            mastery_val >= self.MASTERY_HIGH
            and engagement_val >= self.ENGAGEMENT_HIGH
        ):
            actions.append(
                RecommendedAction(
                    action_type=ActionType.RECOMMEND_ADVANCE,
                    target_concept="(next topic)",
                    target_resource_type="video",
                    rationale="已掌握当前内容，建议进入进阶主题",
                    priority=5,
                )
            )

        # Rule 4: low engagement → break or pace adjust
        if engagement_val < self.ENGAGEMENT_LOW:
            actions.append(
                RecommendedAction(
                    action_type=ActionType.RECOMMEND_BREAK,
                    target_concept="",
                    target_resource_type="",
                    rationale="近期学习参与度较低，建议休息或重新规划",
                    priority=4,
                )
            )

        # Rule 7: all good
        if not actions:
            actions.append(
                RecommendedAction(
                    action_type=ActionType.NO_ACTION,
                    target_concept="",
                    target_resource_type="",
                    rationale="学习状态良好，继续当前路径",
                    priority=5,
                )
            )

        # Sort by priority (lower = more urgent)
        actions.sort(key=lambda a: (a.priority, a.action_type.value))

        # Compose directive
        directive = self._compose_directive(actions, report)

        return StrategyDecision(
            user_id=report.user_id,
            actions=actions,
            overall_directive=directive,
            notes=f"基于 {report.events_analyzed} 条事件 + 6 维度评估生成",
        )

    def _compose_directive(
        self,
        actions: list[RecommendedAction],
        report: AssessmentReport,
    ) -> str:
        """Human-readable summary of the strategy."""
        if not actions:
            return "保持当前学习路径"
        primary = actions[0]
        bits = [
            f"主要行动：{primary.action_type.value} ({primary.rationale})",
        ]
        if report.weak_concepts:
            bits.append(
                f"重点关注：{', '.join(report.weak_concepts[:3])}"
            )
        if report.strong_concepts:
            bits.append(
                f"已掌握：{', '.join(report.strong_concepts[:3])}"
            )
        return " | ".join(bits)


__all__ = ["AdaptiveStrategyEngine"]
