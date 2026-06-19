"""PathPlanningCapability — learning path planning & resource push.

Placeholder for Phase 3.
"""

from __future__ import annotations

from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus


class PathPlanningCapability(BaseCapability):
    """Personalized learning path planning and resource push."""

    manifest = CapabilityManifest(
        name="path_planning",
        description="基于知识图谱与画像的个性化学习路径规划与资源推送",
        stages=["locate", "prune", "topo_sort", "match", "push"],
        tools_used=["rag"],
        cli_aliases=["path", "plan"],
        tags=["path", "planning"],
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        async with stream.stage("locate", source="path_capability"):
            await stream.observation(
                "在知识图谱中定位学生当前位置...",
                source="path_capability",
            )
        async with stream.stage("prune", source="path_capability"):
            await stream.observation(
                "已掌握节点标记为可跳过...",
                source="path_capability",
            )
        async with stream.stage("topo_sort", source="path_capability"):
            await stream.observation(
                "拓扑排序 + 学习依赖 → 推荐顺序...",
                source="path_capability",
            )
        async with stream.stage("match", source="path_capability"):
            await stream.observation(
                "为每个节点匹配已有/可生成的资源...",
                source="path_capability",
            )
        async with stream.stage("push", source="path_capability"):
            await stream.observation(
                "按顺序 + 学习节奏推送资源...",
                source="path_capability",
            )
        await stream.observation(
            "(占位) PathPlanningCapability 完整实现将在 Phase 3",
            source="path_capability",
        )
        await stream.done(source="path_capability")


__all__ = ["PathPlanningCapability"]
