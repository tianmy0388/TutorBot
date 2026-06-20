"""AssessmentCapability — multi-dimensional learning effectiveness assessment.

Pipeline (5 stages):

    1. event_collection   — pull events from LearningEventStore
    2. event_aggregation  — compute statistics + per-concept deltas
    3. assessment         — AssessmentAgent → AssessmentReport
    4. adaptive_strategy   — AdaptiveStrategyEngine → StrategyDecision
    5. persist_and_emit   — save assessment + emit RESULT event

The capability can also be triggered manually (user asks "评估一下我") or
automatically after every N resource completions (Phase 5 background job).
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from tutor.agents.assessment.adaptive_strategy import AdaptiveStrategyEngine
from tutor.agents.assessment.assessment_agent import AssessmentAgent
from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.learning_events.schema import (
    EventType,
    LearningEvent,
)
from tutor.services.learning_events.store import (
    LearningEventStore,
    get_learning_event_store,
)
from tutor.services.learner_profile.builder import (
    ProfileBuilder,
    get_profile_builder,
)


class AssessmentCapability(BaseCapability):
    """Multi-dimensional learning effectiveness assessment + adaptive strategy."""

    manifest = CapabilityManifest(
        name="assessment",
        description="多维度学习效果评估 + 自适应推送策略（动态调整学习计划）",
        stages=[
            "event_collection",
            "event_aggregation",
            "assessment",
            "adaptive_strategy",
            "persist_and_emit",
        ],
        tools_used=[],
        cli_aliases=["assess", "evaluate", "progress"],
        tags=["assessment", "analytics", "adaptive"],
    )

    def __init__(
        self,
        *,
        builder: ProfileBuilder | None = None,
        event_store: LearningEventStore | None = None,
        assessment_agent: AssessmentAgent | None = None,
        strategy_engine: AdaptiveStrategyEngine | None = None,
        window_hours: int = 168,
    ) -> None:
        super().__init__()
        self.builder = builder
        self._owns_builder = builder is None
        self.event_store = event_store or get_learning_event_store()
        self.assessment_agent = assessment_agent or AssessmentAgent()
        self.strategy_engine = strategy_engine or AdaptiveStrategyEngine()
        self.window_hours = window_hours

    @property
    def _builder(self) -> ProfileBuilder:
        if self.builder is None:
            self.builder = get_profile_builder()
        return self.builder

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        # ------------------------------------------------------------------
        # Stage 1: event collection
        # ------------------------------------------------------------------
        events: list[LearningEvent] = []
        async with stream.stage("event_collection", source="assessment_capability"):
            try:
                # Initialise store if needed
                try:
                    await self.event_store.init()
                except Exception:
                    pass
                events = await self.event_store.query(
                    context.user_id, limit=500
                )
                await stream.observation(
                    f"已收集 {len(events)} 条学习事件",
                    source="assessment_capability",
                    stage="event_collection",
                    metadata={"event_count": len(events)},
                )
            except Exception as exc:
                logger.exception(f"Event collection failed: {exc!r}")
                await stream.error(
                    f"事件采集失败: {exc}", source="assessment_capability"
                )

        # ------------------------------------------------------------------
        # Stage 2: event aggregation
        # ------------------------------------------------------------------
        stats: dict[str, Any] = {}
        async with stream.stage("event_aggregation", source="assessment_capability"):
            try:
                stats = await self.event_store.stats(
                    context.user_id, window_hours=self.window_hours
                )
                await stream.observation(
                    f"聚合统计完成: events={stats.get('event_count', 0)}, "
                    f"completion_rate={stats.get('completion_rate', 0):.0%}",
                    source="assessment_capability",
                    stage="event_aggregation",
                    metadata=stats,
                )
            except Exception as exc:
                logger.warning(f"Event aggregation failed: {exc!r}")
                await stream.error(
                    f"事件聚合失败: {exc}", source="assessment_capability"
                )

        # ------------------------------------------------------------------
        # Stage 3: assessment
        # ------------------------------------------------------------------
        profile = None
        from tutor.services.learner_profile.schema import LearnerProfile

        try:
            profile = await self._builder.get(context.user_id)
            context.metadata["learner_profile"] = profile
        except Exception as exc:
            logger.warning(f"Profile load failed: {exc!r}")

        report = None
        async with stream.stage("assessment", source="assessment_capability"):
            try:
                report = await self.assessment_agent.process(
                    context,
                    stream=stream,
                    user_id=context.user_id,
                    events=events,
                    stats=stats,
                    profile=profile,
                    window_hours=self.window_hours,
                )
            except Exception as exc:
                logger.exception(f"Assessment failed: {exc!r}")
                await stream.error(
                    f"评估失败: {exc}", source="assessment_capability"
                )
                report = None

        # ------------------------------------------------------------------
        # Stage 4: adaptive strategy
        # ------------------------------------------------------------------
        strategy = None
        async with stream.stage("adaptive_strategy", source="assessment_capability"):
            if report is not None:
                try:
                    strategy = self.strategy_engine.decide(
                        report,
                        concepts_touched=stats.get("concepts_touched"),
                    )
                    await stream.observation(
                        f"自适应策略生成 {len(strategy.actions)} 条行动: "
                        f"主行动={strategy.actions[0].action_type.value if strategy.actions else 'none'}",
                        source="assessment_capability",
                        stage="adaptive_strategy",
                        metadata={
                            "primary_action": (
                                strategy.actions[0].action_type.value
                                if strategy.actions else None
                            ),
                        },
                    )
                except Exception as exc:
                    logger.exception(f"Strategy failed: {exc!r}")
                    await stream.error(
                        f"策略生成失败: {exc}", source="assessment_capability"
                    )

        # ------------------------------------------------------------------
        # Stage 5: persist + emit
        # ------------------------------------------------------------------
        async with stream.stage("persist_and_emit", source="assessment_capability"):
            try:
                # Record this assessment as a learning event for the audit trail
                await self.event_store.record(
                    LearningEvent(
                        user_id=context.user_id,
                        event_type=EventType.PROFILE_UPDATED,
                        target_id="assessment",
                        metadata={
                            "report": report.to_dict() if report else None,
                            "strategy": strategy.to_dict() if strategy else None,
                        },
                    )
                )
            except Exception as exc:
                logger.warning(f"Persist assessment failed: {exc!r}")

            await stream.result(
                {
                    "report": report.to_dict() if report else {},
                    "strategy": strategy.to_dict() if strategy else {},
                    "stats_summary": {
                        "events_analyzed": len(events),
                        "window_hours": self.window_hours,
                        "event_types": list(stats.get("by_type", {}).keys()),
                    },
                    "next_step": "apply_strategy",
                },
                source="assessment_capability",
            )
            await stream.done(source="assessment_capability")


__all__ = ["AssessmentCapability"]
