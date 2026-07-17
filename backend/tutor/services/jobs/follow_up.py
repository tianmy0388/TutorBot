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

    def __init__(self, package_store=None) -> None:
        super().__init__()
        self._package_store = package_store

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

        capability = ResourceGenerationCapability(package_store=package_store)
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


_FOLLOW_UP_BUILDERS = {
    "video_render": VideoRenderFollowUpCapability,
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


def build_follow_up_capability(task_kind: str) -> BaseCapability | None:
    builder = _FOLLOW_UP_BUILDERS.get(task_kind)
    return builder() if builder is not None else None


__all__ = [
    "FollowUpScheduler",
    "VideoRenderFollowUpCapability",
    "build_follow_up_capability",
    "validate_follow_up_spec",
]
