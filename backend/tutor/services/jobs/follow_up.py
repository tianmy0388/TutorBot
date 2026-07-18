"""Durable, idempotent follow-up child-job scheduling."""

from __future__ import annotations

from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.capability_result import CapabilityResult, FollowUpTaskSpec
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.schema import Job
from tutor.services.jobs.store import JobStore
from tutor.services.resource_package.schema import ArtifactRef


async def _require_current_claim(context: UnifiedContext) -> None:
    validator = context.metadata.get("_claim_validator")
    if callable(validator) and not await validator():
        raise PermissionError("follow-up claim is no longer current")


class FollowUpScheduler:
    """Persist follow-up specs as child jobs before parent terminalization."""

    def __init__(self, store: JobStore) -> None:
        self.store = store

    async def enqueue(
        self,
        parent_job_id: str,
        specs: tuple[FollowUpTaskSpec, ...],
    ) -> list[Job]:
        for spec in specs:
            validate_follow_up_spec(spec)
        children: list[Job] = []
        for spec in specs:
            children.append(
                await self.store.create_child_if_absent(
                    parent_job_id=parent_job_id,
                    task_kind=spec.kind,
                    dedupe_key=spec.dedupe_key,
                    payload=spec.payload,
                )
            )
        return children


class VideoRenderFollowUpCapability(BaseCapability):
    """Execute one persisted pending-video spec on a child stream."""

    manifest = CapabilityManifest(
        name="video_render",
        description="内部持久化 Manim 视频渲染子任务",
        stages=["video_rendering"],
        tags=["internal", "follow_up", "video"],
    )

    def __init__(self, package_store=None, settings=None) -> None:
        super().__init__()
        self._package_store = package_store
        self._settings = settings

    async def run(
        self,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> CapabilityResult:
        from tutor.capabilities.resource_generation import (
            ResourceGenerationCapability,
        )
        from tutor.services.resource_package import get_resource_package_store

        package_id = str(context.metadata.get("package_id") or "")
        resource_id = str(context.metadata.get("resource_id") or "")
        claim_validator = context.metadata.get("_claim_validator")
        claim_guard = context.metadata.get("_claim_guard")

        async def require_current_claim() -> None:
            if callable(claim_validator) and not await claim_validator():
                raise PermissionError("follow-up claim is no longer current")

        await require_current_claim()
        package_store = self._package_store or get_resource_package_store()
        package = await package_store.get_for_user(package_id, context.user_id)
        if package is None:
            raise PermissionError("Video package is unavailable for this user")
        if not await package_store.owns_resource(
            package_id,
            resource_id,
            context.user_id,
        ):
            raise PermissionError("Video resource is unavailable for this user")
        resource = next(
            (item for item in package.resources if item.resource_id == resource_id),
            None,
        )
        if resource is None:
            raise RuntimeError("Video resource is unavailable")

        capability = ResourceGenerationCapability(
            package_store=package_store,
            settings=self._settings,
        )
        await capability._render_one_video(
            resource,
            package,
            context,
            stream,
            persist_package=False,
            emit_resource=False,
        )
        await require_current_claim()

        async def persist_resource() -> None:
            await package_store.update_resource(
                package.package_id,
                resource,
                user_id=context.user_id,
            )

        if not callable(claim_guard) or not await claim_guard(persist_resource):
            raise PermissionError("follow-up claim is no longer current")
        render_status = str(
            (resource.format_specific or {}).get("render_status") or "failed"
        )
        if render_status != "ready":
            raise RuntimeError("Video rendering failed")

        artifacts: tuple[ArtifactRef, ...] = ()
        artifact_key = (resource.format_specific or {}).get("artifact_key")
        if artifact_key:
            artifacts = (
                ArtifactRef(
                    name=str(artifact_key).rsplit("/", 1)[-1],
                    kind="video",
                    artifact_key=str(artifact_key),
                ),
            )
        return CapabilityResult(
            assistant_message="视频渲染完成",
            payload={
                "package_id": package.package_id,
                "resource_id": resource.resource_id,
                "render_status": render_status,
            },
            artifacts=artifacts,
        )


class ProfileUpdateFollowUpCapability(BaseCapability):
    """Aggregate a stable learning-event window without invoking an LLM."""

    manifest = CapabilityManifest(
        name="profile_update",
        description="内部确定性学习画像更新子任务",
        stages=["profile_update"],
        tags=["internal", "follow_up", "profile"],
    )

    def __init__(self, *, event_store=None, profile_store=None, builder=None) -> None:
        super().__init__()
        self._event_store = event_store
        self._profile_store = profile_store
        self._builder = builder

    async def run(self, context: UnifiedContext, stream: StreamBus) -> CapabilityResult:
        from tutor.services.learner_profile.builder import ProfileBuilder
        from tutor.services.learner_profile.schema import empty_profile
        from tutor.services.learner_profile.store import get_profile_store
        from tutor.services.learning_events.store import get_learning_event_store

        event_store = self._event_store or get_learning_event_store()
        profile_store = self._profile_store or get_profile_store()
        builder = self._builder or ProfileBuilder(store=profile_store)
        start = int(context.metadata["from_watermark"])
        through = int(context.metadata["through_sequence"])
        if through <= start:
            raise ValueError("profile event window must advance")
        await _require_current_claim(context)
        events = await event_store.list_since(
            context.user_id, start, through_sequence=through
        )
        window_course = next(
            (event.course for event in reversed(events) if event.course),
            str(context.metadata.get("course") or ""),
        )
        current = await profile_store.get(context.user_id)
        current = current or empty_profile(context.user_id)
        if current.event_watermark < through:
            if current.event_watermark != start:
                raise RuntimeError("profile watermark is stale")
            for _attempt in range(3):
                candidate = builder.aggregate_events(
                    current, events, through_sequence=through
                )
                outcome = None

                async def persist_profile(candidate_to_save=candidate) -> None:
                    nonlocal outcome
                    outcome = await profile_store.save_event_profile(
                        candidate_to_save, expected_watermark=start
                    )

                guard = context.metadata.get("_claim_guard")
                if callable(guard):
                    if not await guard(persist_profile):
                        raise PermissionError("follow-up claim is no longer current")
                else:
                    await persist_profile()
                if outcome is None:
                    raise RuntimeError("profile persistence failed")
                current = outcome.profile
                if current.event_watermark >= through:
                    break
                if current.event_watermark != start:
                    raise RuntimeError("profile watermark is stale")
            else:
                raise RuntimeError("profile changed repeatedly during event aggregation")

        follow_ups: list[FollowUpTaskSpec] = []
        if await profile_store.get_path(context.user_id, current.version) is None:
            follow_ups.append(
                FollowUpTaskSpec(
                    kind="path_rebuild",
                    dedupe_key=f"path_rebuild:{current.version}",
                    payload={
                        "user_id": context.user_id,
                        "profile_version": current.version,
                        "profile": current.model_dump(mode="json"),
                        "course": window_course,
                    },
                )
            )
        next_through = await event_store.profile_trigger_sequence_since(
            context.user_id,
            current.event_watermark,
        )
        if next_through is not None:
            pending_events = await event_store.list_since(
                context.user_id,
                current.event_watermark,
                through_sequence=next_through,
            )
            next_course = next(
                (event.course for event in reversed(pending_events) if event.course),
                window_course,
            )
            follow_ups.append(
                FollowUpTaskSpec(
                    kind="profile_update",
                    dedupe_key=f"profile_update:{current.event_watermark}",
                    payload={
                        "user_id": context.user_id,
                        "from_watermark": current.event_watermark,
                        "through_sequence": next_through,
                        "course": next_course,
                    },
                )
            )
        return CapabilityResult(
            assistant_message="学习者画像已更新",
            payload={
                "profile_version": current.version,
                "event_watermark": current.event_watermark,
                "knowledge_scores": dict(current.knowledge_map.scores),
            },
            follow_up_tasks=tuple(follow_ups),
        )


class PathRebuildFollowUpCapability(BaseCapability):
    """Plan and persist a path for the exact profile snapshot in the child."""

    manifest = CapabilityManifest(
        name="path_rebuild",
        description="内部画像版本绑定学习路径子任务",
        stages=["path_rebuild"],
        tags=["internal", "follow_up", "path"],
    )

    def __init__(self, *, profile_store=None, kg_service=None) -> None:
        super().__init__()
        self._profile_store = profile_store
        self._kg_service = kg_service

    async def run(self, context: UnifiedContext, stream: StreamBus) -> CapabilityResult:
        from tutor.services.knowledge_graph import get_knowledge_graph_service
        from tutor.services.knowledge_graph.planner import KGPathPlanner
        from tutor.services.learner_profile.schema import (
            LearnerProfile,
            PersistedLearningPath,
        )
        from tutor.services.learner_profile.store import get_profile_store

        store = self._profile_store or get_profile_store()
        version = int(context.metadata["profile_version"])
        profile = LearnerProfile.model_validate(context.metadata["profile"])
        if profile.user_id != context.user_id or profile.version != version:
            raise ValueError("path profile snapshot does not match child identity")
        existing = await store.get_path(context.user_id, version)
        if existing is not None:
            return CapabilityResult(
                assistant_message="学习路径已恢复",
                payload=existing.model_dump(mode="json"),
            )

        service = self._kg_service or get_knowledge_graph_service()
        course = str(context.metadata.get("course") or service.default_course())
        nodes: list[dict] = []
        edges: list[dict] = []
        rationale = "knowledge graph has no learnable nodes"
        path_id = f"profile-{version}"
        name = ""
        description = ""
        total_hours = 0.0
        completed_count = 0
        available_count = 0
        locked_count = 0
        if course and service.has_course(course):
            model, graph = service.get_graph(course)
            planned = service.plan_for_learner(course, profile)
            if model.nodes and not any(
                node.node_id in model.node_ids() for node in planned.nodes
            ):
                planned = KGPathPlanner().plan(
                    model,
                    graph,
                    profile,
                    path_id="__automatic_graph_fallback__",
                )
            selected = {node.node_id for node in planned.nodes}
            nodes = [
                {
                    "id": node.node_id,
                    "name": node.name,
                    "category": node.category,
                    "difficulty": node.difficulty,
                    "estimated_hours": node.estimated_hours,
                    "prerequisites": model.prerequisites_of(node.node_id),
                    "status": node.status.value,
                }
                for node in planned.nodes
                if node.node_id in model.node_ids()
            ]
            edges = [
                {"from": edge.from_, "to": edge.to, "type": edge.type.value}
                for edge in model.edges
                if edge.from_ in selected and edge.to in selected
            ]
            rationale = "mastery-aware prerequisite topological order"
            path_id = planned.path_id or path_id
            name = planned.name
            description = planned.description
            total_hours = planned.total_estimated_hours
            completed_count = planned.completed_count
            available_count = planned.available_count
            locked_count = planned.locked_count
        path = PersistedLearningPath(
            user_id=context.user_id,
            profile_version=version,
            course=course,
            path_id=path_id,
            name=name,
            description=description,
            nodes=nodes,
            edges=edges,
            rationale=rationale,
            total_estimated_hours=total_hours,
            completed_count=completed_count,
            available_count=available_count,
            locked_count=locked_count,
        )
        persisted = None

        async def persist_path() -> None:
            nonlocal persisted
            persisted = await store.save_path(path)

        await _require_current_claim(context)
        guard = context.metadata.get("_claim_guard")
        if callable(guard):
            if not await guard(persist_path):
                raise PermissionError("follow-up claim is no longer current")
        else:
            await persist_path()
        if persisted is None:
            raise RuntimeError("path persistence failed")
        return CapabilityResult(
            assistant_message=("学习路径已生成" if persisted.nodes else "暂无可规划知识节点"),
            payload=persisted.model_dump(mode="json"),
        )


_FOLLOW_UP_BUILDERS = {
    "video_render": VideoRenderFollowUpCapability,
    "profile_update": ProfileUpdateFollowUpCapability,
    "path_rebuild": PathRebuildFollowUpCapability,
}


def validate_follow_up_spec(spec: FollowUpTaskSpec) -> None:
    """Reject unsupported or malformed internal work before persistence."""
    if spec.kind not in _FOLLOW_UP_BUILDERS:
        raise ValueError(f"unsupported follow-up kind: {spec.kind}")
    if not isinstance(spec.payload, dict):
        raise ValueError("follow-up payload must be an object")
    if not isinstance(spec.dedupe_key, str) or not spec.dedupe_key.strip():
        raise ValueError("follow-up dedupe_key must be non-empty")
    if len(spec.dedupe_key) > 256:
        raise ValueError("follow-up dedupe_key exceeds 256 characters")
    if spec.kind == "video_render":
        for field in ("package_id", "resource_id"):
            value = spec.payload.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"video_render follow-up requires {field}")
    elif spec.kind == "profile_update":
        for field in ("user_id", "from_watermark", "through_sequence"):
            if field not in spec.payload:
                raise ValueError(f"profile_update follow-up requires {field}")
    elif spec.kind == "path_rebuild":
        for field in ("user_id", "profile_version", "profile"):
            if field not in spec.payload:
                raise ValueError(f"path_rebuild follow-up requires {field}")


def build_follow_up_capability(task_kind: str) -> BaseCapability | None:
    builder = _FOLLOW_UP_BUILDERS.get(task_kind)
    return builder() if builder is not None else None


__all__ = [
    "FollowUpScheduler",
    "VideoRenderFollowUpCapability",
    "ProfileUpdateFollowUpCapability",
    "PathRebuildFollowUpCapability",
    "build_follow_up_capability",
    "validate_follow_up_spec",
]
