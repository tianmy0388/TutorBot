"""LearnerProfileCapability — dialogue-driven profile construction.

Uses the 3-agent cluster from :mod:`tutor.agents.profile`:

1. FeatureExtractorAgent — extracts structured features from user messages.
2. CognitiveDiagnosticAgent — asks follow-up probing questions.
3. ProfileUpdaterAgent — incrementally updates the learner profile.

This is a placeholder implementation that emits a stage trace; the full
implementation lands in Phase 2.
"""

from __future__ import annotations

from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus


class LearnerProfileCapability(BaseCapability):
    """Dialogue-driven learner profile construction."""

    manifest = CapabilityManifest(
        name="profile",
        description="通过对话构建并更新学习者画像（≥6 维度）",
        stages=["feature_extraction", "cognitive_diagnosis", "profile_update"],
        tools_used=[],
        cli_aliases=["profile", "learner"],
        tags=["profile", "personalization"],
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        async with stream.stage("feature_extraction", source="profile_capability"):
            await stream.observation(
                "分析用户输入并提取结构化特征...",
                source="profile_capability",
            )
            await stream.thinking(
                f"用户消息: {context.user_message[:200]}",
                source="profile_capability",
            )
            await stream.observation(
                "(占位) FeatureExtractorAgent 将在 Phase 2 实现",
                source="profile_capability",
            )

        async with stream.stage("cognitive_diagnosis", source="profile_capability"):
            await stream.observation(
                "通过对话探测知识掌握情况...",
                source="profile_capability",
            )
            await stream.observation(
                "(占位) CognitiveDiagnosticAgent 将在 Phase 2 实现",
                source="profile_capability",
            )

        async with stream.stage("profile_update", source="profile_capability"):
            await stream.observation(
                "增量更新 6 维画像...",
                source="profile_capability",
            )
            await stream.observation(
                "(占位) ProfileUpdaterAgent 将在 Phase 2 实现",
                source="profile_capability",
            )

        await stream.result(
            {
                "status": "placeholder",
                "capability": "profile",
                "message": "ProfileCapability 占位实现 — 完整功能在 Phase 2 实现",
            },
            source="profile_capability",
        )
        await stream.done(source="profile_capability")


__all__ = ["LearnerProfileCapability"]
