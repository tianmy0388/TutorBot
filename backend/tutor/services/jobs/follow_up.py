"""Durable, idempotent follow-up child-job scheduling."""

from __future__ import annotations

from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.capability_result import CapabilityResult, FollowUpTaskSpec
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.schema import Job
from tutor.services.jobs.store import JobStore
from tutor.services.resource_package.schema import ArtifactRef


class FollowUpScheduler:
    """Persist follow-up specs as child jobs before parent terminalization."""

    def __init__(self, store: JobStore) -> None:
        self.store = store

    async def enqueue(
        self,
        parent_job_id: str,
        specs: tuple[FollowUpTaskSpec, ...],
    ) -> list[Job]:
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
        package_store = get_resource_package_store()
        package = await package_store.get(package_id)
        if package is None:
            raise RuntimeError("Video package is unavailable")
        resource = next(
            (item for item in package.resources if item.resource_id == resource_id),
            None,
        )
        if resource is None:
            raise RuntimeError("Video resource is unavailable")

        capability = ResourceGenerationCapability(package_store=package_store)
        await capability._render_one_video(resource, package, context, stream)
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


def build_follow_up_capability(task_kind: str) -> BaseCapability | None:
    if task_kind == "video_render":
        return VideoRenderFollowUpCapability()
    return None


__all__ = [
    "FollowUpScheduler",
    "VideoRenderFollowUpCapability",
    "build_follow_up_capability",
]
