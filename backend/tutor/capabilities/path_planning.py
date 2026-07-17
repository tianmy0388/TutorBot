"""PathPlanningCapability — persisted mastery-aware learning paths."""

from __future__ import annotations

from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.capability_result import CapabilityResult
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

    def __init__(self, *, profile_store=None, kg_service=None) -> None:
        super().__init__()
        self._profile_store = profile_store
        self._kg_service = kg_service

    async def run(self, context: UnifiedContext, stream: StreamBus) -> CapabilityResult:
        from tutor.services.jobs.follow_up import PathRebuildFollowUpCapability
        from tutor.services.learner_profile.store import get_profile_store

        store = self._profile_store or get_profile_store()
        profile = await store.get(context.user_id)
        if profile is None:
            return CapabilityResult(
                assistant_message="尚无画像，暂不能规划学习路径",
                payload={
                    "status": "empty",
                    "code": "LEARNING_PROFILE_NOT_FOUND",
                    "nodes": [],
                    "edges": [],
                },
            )
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
        context.metadata.update(
            {
                "profile_version": profile.version,
                "profile": profile.model_dump(mode="json"),
                "course": str(context.metadata.get("course") or ""),
            }
        )
        return await PathRebuildFollowUpCapability(
            profile_store=store,
            kg_service=self._kg_service,
        ).run(context, stream)


__all__ = ["PathPlanningCapability"]
