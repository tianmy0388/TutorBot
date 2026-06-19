"""AssessmentCapability — learning effect assessment. Optional bonus.

Placeholder for Phase 3.
"""

from __future__ import annotations

from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus


class AssessmentCapability(BaseCapability):
    """多维度学习效果评估 + 推送策略动态调整。"""

    manifest = CapabilityManifest(
        name="assessment",
        description="多维度学习效果评估与动态调整推送策略",
        stages=["collect_signals", "multi_dim_eval", "adjust_plan"],
        tools_used=[],
        cli_aliases=["assess", "evaluate"],
        tags=["assessment"],
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        async with stream.stage("collect_signals", source="assessment_capability"):
            await stream.observation("收集学习行为/练习/反馈信号...", source="assessment_capability")
        async with stream.stage("multi_dim_eval", source="assessment_capability"):
            await stream.observation("多维度评估...", source="assessment_capability")
        async with stream.stage("adjust_plan", source="assessment_capability"):
            await stream.observation("动态调整推送策略与学习计划...", source="assessment_capability")
        await stream.observation("(占位) AssessmentCapability 完整实现将在 Phase 3", source="assessment_capability")
        await stream.done(source="assessment_capability")


__all__ = ["AssessmentCapability"]
