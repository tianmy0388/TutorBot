"""Path planning capability backed by the existing knowledge graph service."""

from __future__ import annotations

from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.knowledge_graph.service import get_knowledge_graph_service
from tutor.services.learner_profile.builder import get_profile_builder


class PathPlanningCapability(BaseCapability):
    """Plan an actionable course path from persisted learner state."""

    manifest = CapabilityManifest(
        name="path_planning",
        description="根据当前学习状态整理下一步课程路径",
        stages=["understand_goal", "read_progress", "organize_path", "prepare_next"],
        tools_used=["knowledge_graph"],
        cli_aliases=["path", "plan"],
        tags=["path", "planning"],
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        service = get_knowledge_graph_service()
        course = str((context.metadata or {}).get("course") or service.default_course())
        path_id = str((context.metadata or {}).get("path_id") or "")

        async with stream.stage("understand_goal", source="path_capability"):
            await stream.observation("正在确认课程目标", source="path_capability")

        async with stream.stage("read_progress", source="path_capability"):
            builder = get_profile_builder()
            await builder.initialize()
            profile = await builder.get(context.user_id)
            await stream.observation("已读取当前学习记录", source="path_capability")

        async with stream.stage("organize_path", source="path_capability"):
            plan = service.plan_for_learner(course, profile, path_id=path_id)
            await stream.observation(
                f"已整理 {len(plan.nodes)} 个学习步骤",
                source="path_capability",
            )

        async with stream.stage("prepare_next", source="path_capability"):
            next_node = plan.first_available()
            await stream.observation(
                f"下一步：{next_node.name}" if next_node else "当前路径已整理完成",
                source="path_capability",
            )

        await stream.result(plan.model_dump(mode="json"), source="path_capability")
        await stream.done(source="path_capability")


__all__ = ["PathPlanningCapability"]
