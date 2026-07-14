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

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from tutor.services.resource_package.schema import Resource, ResourceType
from tutor.services.resource_package.store import get_resource_package_store

router = APIRouter()


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
    store = get_resource_package_store()
    since = (
        datetime.now(timezone.utc) - timedelta(hours=since_hours)
        if since_hours is not None
        else None
    )
    items = await store.list(
        user_id, limit=limit, offset=offset, since=since, topic=topic
    )
    total = await store.count(user_id)
    return {
        "user_id": user_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@router.get("/resources/packages/{user_id}/stats")
async def user_stats(user_id: str) -> dict[str, Any]:
    """Aggregate stats for one user's generated resources."""
    store = get_resource_package_store()
    return await store.stats(user_id)


@router.get("/resources/packages/{user_id}/{package_id}")
async def get_package(user_id: str, package_id: str) -> dict[str, Any]:
    """Return one full :class:`ResourcePackage` (header + all resources)."""
    store = get_resource_package_store()
    pkg = await store.get(package_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail="package not found")
    if (pkg.metadata or {}).get("user_id", "anonymous") != user_id:
        # Don't leak across users
        raise HTTPException(status_code=404, detail="package not found")
    return pkg.model_dump(mode="json")


@router.get(
    "/resources/packages/{user_id}/{package_id}/resources/{resource_id}"
)
async def get_resource(
    user_id: str, package_id: str, resource_id: str
) -> dict[str, Any]:
    """Return one resource inside a package (lighter than the full package)."""
    store = get_resource_package_store()
    res: Resource | None = await store.get_resource(resource_id)
    if res is None:
        raise HTTPException(status_code=404, detail="resource not found")
    pkg = await store.get(package_id)
    if pkg is None or (pkg.metadata or {}).get("user_id", "anonymous") != user_id:
        raise HTTPException(status_code=404, detail="resource not found")
    return res.model_dump(mode="json")


@router.delete("/resources/packages/{user_id}/{package_id}")
async def delete_package(user_id: str, package_id: str) -> dict[str, Any]:
    """Delete one package (and its child resources)."""
    store = get_resource_package_store()
    pkg = await store.get(package_id)
    if pkg is None or (pkg.metadata or {}).get("user_id", "anonymous") != user_id:
        raise HTTPException(status_code=404, detail="package not found")
    deleted = await store.delete(package_id)
    return {"deleted": deleted, "package_id": package_id}


@router.delete("/resources/packages/{user_id}")
async def delete_all_packages(user_id: str) -> dict[str, Any]:
    """Delete **all** packages for a user (use with care)."""
    store = get_resource_package_store()
    count = await store.delete_user(user_id)
    return {"deleted": count, "user_id": user_id}


# ---------------------------------------------------------------------------
# File downloads (Phase 5.3 — PPT)
# ---------------------------------------------------------------------------


@router.get(
    "/resources/packages/{user_id}/{package_id}/resources/{resource_id}/download"
)
async def download_resource_file(
    user_id: str, package_id: str, resource_id: str
) -> FileResponse:
    """Download an on-disk artifact for a resource (e.g. the .pptx file)."""
    store = get_resource_package_store()
    res = await store.get_resource(resource_id)
    if res is None:
        raise HTTPException(status_code=404, detail="resource not found")
    pkg = await store.get(package_id)
    if pkg is None or (pkg.metadata or {}).get("user_id", "anonymous") != user_id:
        raise HTTPException(status_code=404, detail="resource not found")

    # Currently only PPT resources have on-disk artifacts; route by type.
    if res.type == ResourceType.PPT:
        pptx_path = (res.format_specific or {}).get("pptx_path")
        if not pptx_path:
            raise HTTPException(
                status_code=404, detail="pptx_path not set on this resource"
            )
        p = Path(pptx_path)
        if not p.exists():
            raise HTTPException(status_code=410, detail="pptx file is gone")
        # Filename uses the resource title for nicer downloads
        safe_title = _safe_filename(res.title) + ".pptx"
        return FileResponse(
            path=str(p),
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            ),
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
}


@router.get(
    "/resources/packages/{user_id}/{package_id}/resources/{resource_id}/artifacts/{artifact_name:path}"
)
async def get_resource_artifact(
    user_id: str,
    package_id: str,
    resource_id: str,
    artifact_name: str,
) -> FileResponse:
    """Serve one on-disk artifact (PNG / SVG / PDF) for a resource.

    The artifact must be declared in ``format_specific.artifacts[]``;
    the request fails with 404 otherwise. The resolved filesystem
    path is constrained to ``settings.data_dir`` so a tampered
    manifest cannot serve arbitrary files.
    """
    return await _serve_resource_artifact(
        user_id=user_id,
        package_id=package_id,
        resource_id=resource_id,
        artifact_name=artifact_name,
    )


@router.get(
    "/resources/{user_id}/resources/{resource_id}/artifacts/{artifact_name:path}"
)
async def get_resource_artifact_by_id_only(
    user_id: str,
    resource_id: str,
    artifact_name: str,
) -> FileResponse:
    """Same as the package-scoped variant, but skips the
    ``package_id`` lookup. Used when the frontend only has a
    placeholder ``package_id`` (e.g. ``"partial-${job_id}"`` or
    ``"pending-${job_id}"``) — typical after a job_timeout where
    the capability never reached the persistence stage.

    Resource id is globally unique, so we resolve it directly and
    only check that it belongs to ``user_id``.
    """
    return await _serve_resource_artifact(
        user_id=user_id,
        package_id=None,
        resource_id=resource_id,
        artifact_name=artifact_name,
    )


async def _serve_resource_artifact(
    *,
    user_id: str,
    package_id: str | None,
    resource_id: str,
    artifact_name: str,
) -> FileResponse:
    """Shared implementation for the package-scoped and
    package-less artifact endpoints."""
    from tutor.services.config.settings import get_settings

    store = get_resource_package_store()
    res = await store.get_resource(resource_id)
    if res is None:
        raise HTTPException(status_code=404, detail="resource not found")

    if package_id is not None:
        pkg = await store.get(package_id)
        if pkg is None or (pkg.metadata or {}).get("user_id", "anonymous") != user_id:
            raise HTTPException(status_code=404, detail="resource not found")
    else:
        # Without package_id, cross-validate that this resource
        # actually belongs to user_id. The ResourceRow carries
        # user_id directly.
        from sqlalchemy import select
        from tutor.services.resource_package.store import ResourceRow

        engine = store._ensure_engine()  # noqa: SLF001
        async with store._with_session() as session:  # noqa: SLF001
            row = (
                await session.execute(
                    select(ResourceRow).where(
                        ResourceRow.resource_id == resource_id
                    )
                )
            ).scalar_one_or_none()
            if row is None or row.user_id != user_id:
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
        raise HTTPException(
            status_code=404, detail=f"artifact '{artifact_name}' not declared"
        )

    path_str = match.get("path")
    if not path_str:
        raise HTTPException(status_code=404, detail="artifact has no path")

    p = Path(str(path_str)).resolve()
    # Security: keep the resolved path inside data_dir.
    try:
        data_root = get_settings().data_dir.resolve()
        p.relative_to(data_root)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="artifact path is outside the data directory",
        )

    if not p.exists():
        raise HTTPException(status_code=410, detail="artifact file is gone")

    kind = str(match.get("kind") or p.suffix.lstrip(".")).lower()
    media_type = _ARTIFACT_MEDIA_TYPES.get(kind, "application/octet-stream")
    return FileResponse(path=str(p), media_type=media_type, filename=p.name)


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