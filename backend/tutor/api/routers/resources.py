"""Resource HTTP endpoints.

Two layers:

1. Static metadata (Phase 2):
   - ``GET /api/v1/resources/info``   — subsystem manifest
   - ``GET /api/v1/resources/types``  — ResourceType enum

2. Persistence-backed history (Phase 5):
   - ``GET    /api/v1/resources/packages/{user_id}``
   - ``GET    /api/v1/resources/packages/{user_id}/{package_id}``
   - ``GET    /api/v1/resources/packages/{user_id}/{package_id}/resources/{resource_id}``
   - ``DELETE /api/v1/resources/packages/{user_id}/{package_id}``
   - ``GET    /api/v1/resources/packages/{user_id}/stats``

The actual generation still happens through the WebSocket at
``/api/v1/ws`` with ``capability='resource_generation'``; the
persistence layer records completed packages there.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from tutor.core.capability_result import FollowUpTaskSpec
from tutor.services.artifacts import (
    UnsafeArtifactKey,
    resolve_artifact_key,
    to_artifact_key,
)
from tutor.services.config.settings import get_settings
from tutor.services.identity import identity_policy_for
from tutor.services.jobs.follow_up import FollowUpScheduler
from tutor.services.jobs.schema import JobStatus
from tutor.services.manim_render.executor import sanitize_public_diagnostic
from tutor.services.resource_package.schema import (
    Resource,
    ResourceType,
    public_package_dump,
    public_resource_dump,
)
from tutor.services.resource_package.store import get_resource_package_store

router = APIRouter()


def _resource_store(request: Request):
    return (
        getattr(request.app.state, "resource_package_store", None)
        or get_resource_package_store()
    )


# ---------------------------------------------------------------------------
# Static metadata (unchanged from Phase 2)
# ---------------------------------------------------------------------------


@router.get("/resources/info")
async def resources_info() -> dict[str, Any]:
    """Information about the resource generation subsystem."""
    return {
        "name": "resource_generation",
        "version": "0.1.0",
        "supported_types": [t.value for t in ResourceType],
        "entry_point": "WebSocket /api/v1/ws (set capability='resource_generation')",
        "pipeline_stages": [
            "intent_understanding",
            "profile_loading",
            "knowledge_graph_query",
            "resource_planning",
            "content_and_pedagogy",
            "parallel_resource_generation",
            "quality_review",
            "anti_hallucination",
            "package_assembly",
            "path_integration",
            "persistence",
        ],
        "agents": [
            "IntentUnderstandingAgent",
            "ContentExpertAgent",
            "PedagogyAgent",
            "MultimediaAgent",
            "ExerciseGeneratorAgent",
            "ManimVideoAgent",
            "CodeSandboxAgent",
            "QualityReviewerAgent",
            "AntiHallucinationAgent",
        ],
    }


@router.get("/resources/types")
async def resource_types() -> dict[str, Any]:
    """List all supported resource types."""
    return {
        "types": [
            {
                "id": t.value,
                "name": {
                    ResourceType.DOCUMENT: "课程讲解文档",
                    ResourceType.MINDMAP: "知识点思维导图",
                    ResourceType.EXERCISE: "练习题/题库",
                    ResourceType.READING: "拓展阅读材料",
                    ResourceType.VIDEO: "多模态视频/动画",
                    ResourceType.CODE: "代码实操案例",
                    ResourceType.PPT: "PPT 教案",
                }.get(t, t.value),
                "agent": {
                    ResourceType.DOCUMENT: "ContentExpertAgent + PedagogyAgent",
                    ResourceType.MINDMAP: "MultimediaAgent",
                    ResourceType.EXERCISE: "ExerciseGeneratorAgent",
                    ResourceType.READING: "PedagogyAgent (reading mode)",
                    ResourceType.VIDEO: "ManimVideoAgent (two-stage)",
                    ResourceType.CODE: "CodeSandboxAgent",
                    ResourceType.PPT: "(Phase 5.3)",
                }.get(t, "TBD"),
            }
            for t in ResourceType
        ],
    }


# ---------------------------------------------------------------------------
# Persistence-backed package history (Phase 5)
# ---------------------------------------------------------------------------


@router.get("/resources/packages/{user_id}")
async def list_packages(
    user_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    since_hours: int | None = Query(None, ge=1, le=24 * 365),
    topic: str | None = Query(None, max_length=200),
) -> dict[str, Any]:
    """List resource package summaries for a user (newest first).

    Each entry is the lightweight summary shape returned by
    :meth:`ResourcePackage.summary`, plus ``user_id``. Use the package
    detail endpoint to fetch the full payload (including all resources).
    """
    user_id = identity_policy_for(request).resolve(user_id)
    store = _resource_store(request)
    since = datetime.now(UTC) - timedelta(hours=since_hours) if since_hours is not None else None
    items = await store.list(user_id, limit=limit, offset=offset, since=since, topic=topic)
    total = await store.count(user_id)
    return {
        "user_id": user_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@router.get("/resources/packages/{user_id}/stats")
async def user_stats(user_id: str, request: Request) -> dict[str, Any]:
    """Aggregate stats for one user's generated resources."""
    user_id = identity_policy_for(request).resolve(user_id)
    store = _resource_store(request)
    return await store.stats(user_id)


@router.get("/resources/packages/{user_id}/{package_id}")
async def get_package(user_id: str, package_id: str, request: Request) -> dict[str, Any]:
    """Return one full :class:`ResourcePackage` (header + all resources)."""
    user_id = identity_policy_for(request).resolve(user_id)
    store = _resource_store(request)
    pkg = await store.get(package_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail="package not found")
    if (pkg.metadata or {}).get("user_id", "anonymous") != user_id:
        # Don't leak across users
        raise HTTPException(status_code=404, detail="package not found")
    return public_package_dump(pkg)


@router.get("/resources/packages/{user_id}/{package_id}/resources/{resource_id}")
async def get_resource(user_id: str, package_id: str, resource_id: str, request: Request) -> dict[str, Any]:
    """Return one resource inside a package (lighter than the full package)."""
    user_id = identity_policy_for(request).resolve(user_id)
    store = _resource_store(request)
    res: Resource | None = await store.get_resource(resource_id)
    if res is None:
        raise HTTPException(status_code=404, detail="resource not found")
    pkg = await store.get(package_id)
    if pkg is None or (pkg.metadata or {}).get("user_id", "anonymous") != user_id:
        raise HTTPException(status_code=404, detail="resource not found")
    return public_resource_dump(res)


@router.delete("/resources/packages/{user_id}/{package_id}")
async def delete_package(user_id: str, package_id: str, request: Request) -> dict[str, Any]:
    """Delete one package (and its child resources)."""
    user_id = identity_policy_for(request).resolve(user_id)
    store = _resource_store(request)
    pkg = await store.get(package_id)
    if pkg is None or (pkg.metadata or {}).get("user_id", "anonymous") != user_id:
        raise HTTPException(status_code=404, detail="package not found")
    deleted = await store.delete(package_id)
    return {"deleted": deleted, "package_id": package_id}


@router.delete("/resources/packages/{user_id}")
async def delete_all_packages(user_id: str, request: Request) -> dict[str, Any]:
    """Delete **all** packages for a user (use with care)."""
    user_id = identity_policy_for(request).resolve(user_id)
    store = _resource_store(request)
    count = await store.delete_user(user_id)
    return {"deleted": count, "user_id": user_id}


@router.post(
    "/resources/packages/{user_id}/{package_id}/resources/{resource_id}/retry-video"
)
async def retry_video_render(
    user_id: str,
    package_id: str,
    resource_id: str,
    request: Request,
) -> dict[str, Any]:
    """Create or reuse one active durable retry child for a failed video."""
    policy = identity_policy_for(request)
    user_id = policy.resolve(user_id)
    package_store = _resource_store(request)
    package = await package_store.get(package_id)
    if package is None or (
        policy.multi_user_enabled
        and (package.metadata or {}).get("user_id", "anonymous") != user_id
    ):
        raise HTTPException(status_code=404, detail="resource not found")
    resource = next(
        (item for item in package.resources if item.resource_id == resource_id),
        None,
    )
    if resource is None:
        raise HTTPException(status_code=404, detail="resource not found")
    if resource.type != ResourceType.VIDEO:
        raise HTTPException(status_code=422, detail="resource is not a video")

    runner = getattr(request.app.state, "learning_runner", None)
    if runner is None or not hasattr(runner, "store"):
        raise HTTPException(status_code=503, detail="job runner is unavailable")
    job_store = runner.store
    parent_job_id = package.originating_job_id
    parent = await job_store.get(parent_job_id) if parent_job_id else None
    if parent is None or (
        policy.multi_user_enabled and parent.user_id != user_id
    ):
        raise HTTPException(status_code=404, detail="originating job not found")
    if parent.session_id != str((package.metadata or {}).get("session_id") or parent.session_id):
        raise HTTPException(status_code=409, detail="package conversation does not match job")
    if parent.status not in {JobStatus.SUCCEEDED, JobStatus.PARTIAL}:
        raise HTTPException(status_code=409, detail="originating job cannot authorize retry")

    matching = [
        child
        for child in await job_store.get_children(parent.job_id)
        if child.task_kind == "video_repair_render"
        and str((child.metadata or {}).get("package_id") or "") == package_id
        and str((child.metadata or {}).get("resource_id") or "") == resource_id
    ]
    failed_revision = int(
        (resource.format_specific or {}).get("source_revision") or 0
    )
    child = next(
        (
            candidate
            for candidate in reversed(matching)
            if candidate.status in {JobStatus.PENDING, JobStatus.RUNNING}
            and int((candidate.metadata or {}).get("failed_revision") or 0)
            == failed_revision
            and str((candidate.metadata or {}).get("user_id") or "")
            == parent.user_id
        ),
        None,
    )
    render_status = str((resource.format_specific or {}).get("render_status") or "")
    if render_status == "ready":
        raise HTTPException(status_code=409, detail="ready video cannot be retried")
    if child is None and render_status != "failed":
        raise HTTPException(status_code=409, detail="video is not retryable")
    if child is None:
        attempt = len(matching) + 1
        child = (
            await FollowUpScheduler(job_store).enqueue(
                parent.job_id,
                (
                    FollowUpTaskSpec(
                        kind="video_repair_render",
                        dedupe_key=(
                            f"video-repair:{package_id}:{resource_id}:"
                            f"{failed_revision}:{attempt}"
                        ),
                        payload={
                            "package_id": package_id,
                            "resource_id": resource_id,
                            "user_id": parent.user_id,
                            "failed_revision": failed_revision,
                        },
                    ),
                ),
            )
        )[0]

    # Durable child creation is the commit point. The original render failure,
    # source and log manifest remain visible while intelligent repair runs.
    reset_applied = False
    expected_repair_job_id = (resource.format_specific or {}).get("repair_job_id")

    async def reset_resource() -> None:
        nonlocal reset_applied
        def bind_child(payload: dict[str, Any]) -> None:
            payload["repair_status"] = "pending"
            payload["repair_job_id"] = child.job_id
            payload.setdefault("source_revision", failed_revision)
            payload.setdefault("repair_history", [])

        updated = await package_store.mutate_video_repair_if_current(
            package_id=package_id,
            resource_id=resource_id,
            user_id=parent.user_id,
            expected_source_revision=failed_revision,
            expected_repair_job_id=(
                str(expected_repair_job_id) if expected_repair_job_id else None
            ),
            mutation=bind_child,
        )
        reset_applied = updated is not None

    if str(expected_repair_job_id or "") != child.job_id:
        await job_store.run_if_child_active(child.job_id, operation=reset_resource)
    current_child = await job_store.get(child.job_id) or child
    if reset_applied and current_child.status in {
        JobStatus.PENDING,
        JobStatus.RUNNING,
    }:
        await runner.resume_pending()
    current_resource = await package_store.get_resource(resource_id) or resource
    return {
        "job_id": current_child.job_id,
        "parent_job_id": parent.job_id,
        "package_id": package_id,
        "resource_id": resource_id,
        "status": current_child.status.value,
        "child": {
            "job_id": current_child.job_id,
            "capability": current_child.capability,
            "status": current_child.status.value,
            "parent_job_id": current_child.parent_job_id,
            "task_kind": current_child.task_kind,
            "dedupe_key": current_child.dedupe_key,
            "metadata": {
                "package_id": package_id,
                "resource_id": resource_id,
                "failed_revision": int(
                    (current_child.metadata or {}).get("failed_revision") or 0
                ),
            },
            "error": (
                sanitize_public_diagnostic(str(current_child.error))
                if current_child.error
                else None
            ),
        },
        "resource": public_resource_dump(current_resource),
    }


# ---------------------------------------------------------------------------
# File downloads (Phase 5.3 — PPT)
# ---------------------------------------------------------------------------


@router.get("/resources/packages/{user_id}/{package_id}/resources/{resource_id}/download")
async def download_resource_file(
    user_id: str, package_id: str, resource_id: str, request: Request
) -> FileResponse:
    """Download an on-disk artifact for a resource (e.g. the .pptx file)."""
    policy = identity_policy_for(request)
    user_id = policy.resolve(user_id)
    store = _resource_store(request)
    res = await store.get_resource(resource_id)
    if res is None:
        raise HTTPException(status_code=404, detail="resource not found")
    pkg = await store.get(package_id)
    from sqlalchemy import select

    from tutor.services.resource_package.store import ResourceRow

    store._ensure_engine()  # noqa: SLF001
    async with store._with_session() as session:  # noqa: SLF001
        row = (
            await session.execute(
                select(ResourceRow).where(ResourceRow.resource_id == resource_id)
            )
        ).scalar_one_or_none()
    if (
        pkg is None
        or row is None
        or row.package_id != package_id
        or (
            policy.multi_user_enabled
            and (
                (pkg.metadata or {}).get("user_id", "anonymous") != user_id
                or row.user_id != user_id
            )
        )
    ):
        raise HTTPException(status_code=404, detail="resource not found")

    # Currently only PPT resources have on-disk artifacts; route by type.
    if res.type == ResourceType.PPT:
        fs = res.format_specific or {}
        raw = fs.get("artifact_key") or fs.get("pptx_path")
        if not raw:
            raise HTTPException(status_code=404, detail="artifact_key not set on this resource")
        p = _resolve_stored_artifact(
            str(raw), is_key=bool(fs.get("artifact_key"))
        )
        if not p.is_file():
            raise HTTPException(status_code=404, detail="pptx file is missing")
        # Filename uses the resource title for nicer downloads
        safe_title = _safe_filename(res.title) + ".pptx"
        return FileResponse(
            path=str(p),
            media_type=("application/vnd.openxmlformats-officedocument.presentationml.presentation"),
            filename=safe_title,
        )

    raise HTTPException(
        status_code=415,
        detail=f"download not supported for resource type={res.type.value}",
    )


# ---------------------------------------------------------------------------
# Artifact streaming (585f367d fix)
#
# Pre-fix, the code sandbox saved matplotlib figures to
# ``data_dir/code_runs/<run_id>/figure_*.png`` and exposed their paths
# via ``format_specific.artifacts[]``, but the right-pane ``CodeViewer``
# never read that field — and there was no HTTP route to turn the
# absolute filesystem path into a URL the browser could render. The
# user's XOR backprop snippet drew a loss curve, but the right pane
# showed nothing.
#
# This endpoint serves one artifact file at a time. We deliberately
# validate the requested ``artifact_name`` against the manifest stored
# on the resource (no path traversal), and we restrict the served
# directory to the configured ``data_dir`` so a malicious resource
# cannot exfiltrate arbitrary files.
# ---------------------------------------------------------------------------


_ARTIFACT_MEDIA_TYPES: dict[str, str] = {
    "png": "image/png",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "log": "text/plain; charset=utf-8",
    "render_log": "text/plain; charset=utf-8",
}


@router.get(
    "/resources/packages/{user_id}/{package_id}/resources/{resource_id}/artifacts/{artifact_name:path}"
)
async def get_resource_artifact(
    user_id: str,
    package_id: str,
    resource_id: str,
    artifact_name: str,
    request: Request,
) -> FileResponse:
    """Serve one on-disk artifact (PNG / SVG / PDF) for a resource.

    The artifact must be declared in ``format_specific.artifacts[]``;
    the request fails with 404 otherwise. The resolved filesystem
    path is constrained to ``settings.data_dir`` so a tampered
    manifest cannot serve arbitrary files.
    """
    policy = identity_policy_for(request)
    user_id = policy.resolve(user_id)
    return await _serve_resource_artifact(
        user_id=user_id,
        package_id=package_id,
        resource_id=resource_id,
        artifact_name=artifact_name,
        allow_historical_owner=not policy.multi_user_enabled,
    )


@router.get("/resources/{user_id}/resources/{resource_id}/artifacts/{artifact_name:path}")
async def get_resource_artifact_by_id_only(
    user_id: str,
    resource_id: str,
    artifact_name: str,
    request: Request,
) -> FileResponse:
    """Same as the package-scoped variant, but skips the
    ``package_id`` lookup. Used when the frontend only has a
    placeholder ``package_id`` (e.g. ``"partial-${job_id}"`` or
    ``"pending-${job_id}"``) — typical after a job_timeout where
    the capability never reached the persistence stage.

    Resource id is globally unique, so we resolve it directly and
    only check that it belongs to ``user_id``.
    """
    policy = identity_policy_for(request)
    user_id = policy.resolve(user_id)
    return await _serve_resource_artifact(
        user_id=user_id,
        package_id=None,
        resource_id=resource_id,
        artifact_name=artifact_name,
        allow_historical_owner=not policy.multi_user_enabled,
    )


async def _serve_resource_artifact(
    *,
    user_id: str,
    package_id: str | None,
    resource_id: str,
    artifact_name: str,
    allow_historical_owner: bool = False,
) -> FileResponse:
    """Shared implementation for the package-scoped and
    package-less artifact endpoints."""
    store = get_resource_package_store()
    res = await store.get_resource(resource_id)
    if res is None:
        raise HTTPException(status_code=404, detail="resource not found")

    from sqlalchemy import select

    from tutor.services.resource_package.store import ResourceRow

    store._ensure_engine()  # noqa: SLF001
    async with store._with_session() as session:  # noqa: SLF001
        row = (
            await session.execute(
                select(ResourceRow).where(ResourceRow.resource_id == resource_id)
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="resource not found")

    if package_id is not None:
        pkg = await store.get(package_id)
        if (
            pkg is None
            or row.package_id != package_id
            or (
                not allow_historical_owner
                and (
                    (pkg.metadata or {}).get("user_id", "anonymous") != user_id
                    or row.user_id != user_id
                )
            )
        ):
            raise HTTPException(status_code=404, detail="resource not found")
    else:
        # Without package_id, cross-validate that this resource
        # actually belongs to user_id. The ResourceRow carries
        # user_id directly.
        if not allow_historical_owner and row.user_id != user_id:
            raise HTTPException(status_code=404, detail="resource not found")

    artifacts = (res.format_specific or {}).get("artifacts") or []
    if not isinstance(artifacts, list):
        raise HTTPException(status_code=404, detail="no artifacts on this resource")

    match = None
    for entry in artifacts:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name == artifact_name:
            match = entry
            break
    if match is None:
        raise HTTPException(status_code=404, detail=f"artifact '{artifact_name}' not declared")
    if match.get("unresolved"):
        raise HTTPException(
            status_code=403,
            detail="artifact path is outside the data directory",
        )

    artifact_key = match.get("artifact_key")
    path_str = match.get("path")
    url_str = match.get("url")
    if isinstance(url_str, str) and url_str.startswith(("http://", "https://")):
        raise HTTPException(status_code=404, detail="external artifact is not stored locally")
    raw = artifact_key or path_str or url_str
    if not raw:
        raise HTTPException(status_code=404, detail="artifact has no artifact_key")

    p = _resolve_stored_artifact(str(raw), is_key=bool(artifact_key))

    if not p.is_file():
        raise HTTPException(status_code=404, detail="artifact file is missing")

    kind = str(match.get("kind") or p.suffix.lstrip(".")).lower()
    media_type = _ARTIFACT_MEDIA_TYPES.get(kind, "application/octet-stream")
    return FileResponse(path=str(p), media_type=media_type, filename=p.name)


def _resolve_stored_artifact(raw: str, *, is_key: bool) -> Path:
    """Resolve canonical keys plus safely-contained legacy path values."""
    data_dir = get_settings().data_dir
    try:
        if is_key:
            resolved = resolve_artifact_key(raw, data_dir)
        else:
            legacy = Path(raw)
            key = (
                to_artifact_key(legacy, data_dir)
                if legacy.is_absolute()
                else raw.replace("\\", "/")
            )
            resolved = resolve_artifact_key(key, data_dir)
    except UnsafeArtifactKey:
        raise HTTPException(
            status_code=403,
            detail="artifact path is outside the data directory",
        ) from None
    relative = resolved.resolve().relative_to(data_dir.resolve())
    if relative.parts and relative.parts[0].casefold() == "operator_logs":
        raise HTTPException(
            status_code=403,
            detail="operator artifacts are not publicly accessible",
        )
    return resolved


def _safe_filename(name: str) -> str:
    """Reduce a title to a filesystem-safe basename (no extension)."""
    if not name:
        return "resource"
    out: list[str] = []
    for ch in name:
        if ch.isalnum() or ch in ("-", "_", " "):
            out.append(ch)
    cleaned = ("".join(out)).strip().replace(" ", "_")
    return cleaned[:80] or "resource"


__all__ = ["router"]
