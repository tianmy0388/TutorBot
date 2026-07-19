"""Persisted mastery-aware learning-path capability."""

from __future__ import annotations

from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.capability_result import CapabilityResult
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus


class PathPlanningCapability(BaseCapability):
    """Plan and persist an actionable course path from learner state."""

    manifest = CapabilityManifest(
        name="path_planning",
        description="根据当前学习状态整理下一步课程路径",
        stages=["understand_goal", "read_progress", "organize_path", "prepare_next"],
        tools_used=["knowledge_graph"],
        cli_aliases=["path", "plan"],
        tags=["path", "planning"],
    )

    def __init__(self, *, profile_store=None, kg_service=None) -> None:
        super().__init__()
        self._profile_store = profile_store
        self._kg_service = kg_service

    async def run(self, context: UnifiedContext, stream: StreamBus) -> CapabilityResult:
        from tutor.services.jobs.follow_up import PathRebuildFollowUpCapability
        from tutor.services.knowledge_graph import get_knowledge_graph_service
        from tutor.services.learner_profile.store import get_profile_store

        store = self._profile_store or get_profile_store()
        service = self._kg_service or get_knowledge_graph_service()
        async with stream.stage("understand_goal", source="path_capability"):
            await stream.observation("正在确认课程目标", source="path_capability")
        async with stream.stage("read_progress", source="path_capability"):
            profile = await store.get(context.user_id)
            await stream.observation("已读取当前学习记录", source="path_capability")
        if profile is None:
            return CapabilityResult(
                assistant_message="尚无学习记录，完成一次练习后即可规划下一步",
                payload={
                    "status": "empty",
                    "code": "LEARNING_PROFILE_NOT_FOUND",
                    "nodes": [],
                    "edges": [],
                },
            )

        course = str(context.metadata.get("course") or service.default_course())
        context.metadata.update(
            {
                "profile_version": profile.version,
                "profile": profile.model_dump(mode="json"),
                "course": course,
                "path_id": str(context.metadata.get("path_id") or ""),
            }
        )
        async with stream.stage("organize_path", source="path_capability"):
            result = await PathRebuildFollowUpCapability(
                profile_store=store,
                kg_service=service,
            ).run(context, stream)
            await stream.observation(
                f"已整理 {len(result.payload.get('nodes', []))} 个学习步骤",
                source="path_capability",
            )
        async with stream.stage("prepare_next", source="path_capability"):
            nodes = result.payload.get("nodes", [])
            next_node = next(
                (node for node in nodes if node.get("status") == "available"),
                nodes[0] if nodes else None,
            )
            await stream.observation(
                f"下一步：{next_node.get('name')}" if next_node else "当前路径已整理完成",
                source="path_capability",
            )
        return result


__all__ = ["PathPlanningCapability"]
