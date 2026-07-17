"""Job HTTP endpoints (Phase 5.2 + Task 5 retry).

All paths are scoped to ``/api/v1/jobs``. Job execution is driven by
the WebSocket at ``/api/v1/ws``; the REST layer is for inspection,
management (list, detail, cancel, delete), and partial retry.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from tutor.services.identity import identity_policy_for
from tutor.services.jobs import (
    JobStatus,
    JobSubmit,
    get_job_runner,
    get_job_store,
)
from tutor.services.jobs.contracts import (
    JobResultContract,
)
from tutor.services.resource_plan.schema import SUPPORTED_RESOURCE_TYPES

router = APIRouter()


class RetryRequest(BaseModel):
    """Body of ``POST /jobs/{user_id}/{job_id}/retry``."""

    model_config = ConfigDict(extra="forbid")
    resource_types: list[str] = Field(default_factory=list)


__all__ = ["router"]


@router.get("/jobs/{user_id}")
async def list_jobs(
    user_id: str,
    request: Request,
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List jobs for a user (newest first)."""
    user_id = identity_policy_for(request).resolve(user_id)
    store = get_job_store()
    st: JobStatus | None = None
    if status:
        try:
            st = JobStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid status: {status}") from exc
    items = await store.list(user_id, status=st, limit=limit, offset=offset)
    total = await store.count(user_id, status=st)
    return {
        "user_id": user_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@router.get("/jobs/{user_id}/stats")
async def job_stats(user_id: str, request: Request) -> dict[str, Any]:
    user_id = identity_policy_for(request).resolve(user_id)
    store = get_job_store()
    return await store.stats(user_id)


@router.get("/jobs/{user_id}/{job_id}")
async def get_job(user_id: str, job_id: str, request: Request) -> dict[str, Any]:
    user_id = identity_policy_for(request).resolve(user_id)
    store = get_job_store()
    job = await store.get(job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="job not found")
    return job.to_full_dict()


@router.post("/jobs/{user_id}/{job_id}/cancel")
async def cancel_job(user_id: str, job_id: str, request: Request) -> dict[str, Any]:
    user_id = identity_policy_for(request).resolve(user_id)
    runner = get_job_runner()
    ok = await runner.cancel(job_id, user_id=user_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="job is not active (already completed/failed/cancelled or not found)",
        )
    return {"cancelled": True, "job_id": job_id}


@router.delete("/jobs/{user_id}/{job_id}")
async def delete_job(user_id: str, job_id: str, request: Request) -> dict[str, Any]:
    user_id = identity_policy_for(request).resolve(user_id)
    store = get_job_store()
    job = await store.get(job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="job not found")
    deleted = await store.delete(job_id)
    return {"deleted": deleted, "job_id": job_id}


@router.delete("/jobs/{user_id}")
async def delete_all_jobs(user_id: str, request: Request) -> dict[str, Any]:
    user_id = identity_policy_for(request).resolve(user_id)
    store = get_job_store()
    count = await store.delete_user(user_id)
    return {"deleted": count, "user_id": user_id}


@router.post("/jobs/{user_id}/{job_id}/retry")
async def retry_job(user_id: str, job_id: str, req: RetryRequest, request: Request) -> dict[str, Any]:
    """Submit a child job that retries only the failed resource types.

    The endpoint validates that every requested type actually failed in
    the parent job (you cannot retry something that already succeeded).
    The child job inherits the parent's plan_id / topic and carries a
    ``parent_job_id`` plus a ``preserved_artifacts`` list of types that
    already succeeded — a downstream re-package step uses that list to
    reassemble the full package.
    """
    policy = identity_policy_for(request)
    user_id = policy.resolve(user_id)
    store = get_job_store()
    parent = await store.get(job_id)
    if parent is None or (
        policy.multi_user_enabled and parent.user_id != user_id
    ):
        raise HTTPException(status_code=404, detail="job not found")
    if parent.status not in (JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED):
        raise HTTPException(
            status_code=409,
            detail=(
                f"job is {parent.status.value!r}; only succeeded/partial/failed "
                "jobs can be retried"
            ),
        )

    # Validate types are supported and actually failed in the parent.
    bad_type = [t for t in req.resource_types if t not in SUPPORTED_RESOURCE_TYPES]
    if bad_type:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported resource types: {bad_type}",
        )
    if not req.resource_types:
        raise HTTPException(
            status_code=422,
            detail="resource_types must be a non-empty list",
        )

    # Look at the parent's contract to see which types failed.
    parent_result = JobResultContract.model_validate(parent.result) if parent.result else None
    parent_failed: set[str] = set()
    parent_succeeded: set[str] = set()
    if parent_result is not None:
        for art in parent_result.artifacts or []:
            if art.status == "failed":
                parent_failed.add(art.resource_type)
            elif art.status == "succeeded":
                parent_succeeded.add(art.resource_type)

    retryable_types = set(parent_failed)
    if parent.status == JobStatus.SUCCEEDED:
        retryable_types.update(parent_succeeded)
        retryable_types.update(
            str(value)
            for value in (parent.metadata or {}).get("selected_resource_types", [])
        )
    not_retryable = [t for t in req.resource_types if t not in retryable_types]
    if not_retryable:
        raise HTTPException(
            status_code=422,
            detail=(
                "cannot retry types absent from the parent recovery contract: "
                f"{not_retryable}"
            ),
        )

    # Build the child job metadata.
    preserved_artifacts = parent_succeeded.difference(req.resource_types)
    child_meta: dict[str, Any] = {
        **dict(parent.metadata or {}),
        "selected_resource_types": list(req.resource_types),
        "parent_job_id": parent.job_id,
        "plan_id": (parent.metadata or {}).get("plan_id", ""),
        "topic": (parent.metadata or {}).get("topic", ""),
        "preserved_artifacts": sorted(preserved_artifacts),
    }

    runner = get_job_runner()
    child = await runner.submit(
        JobSubmit(
            user_id=user_id,
            message=parent.message,
            capability=parent.capability,
            language=parent.language,
            session_id=parent.session_id,
            metadata=child_meta,
        )
    )
    return {
        "job_id": child.job_id,
        "parent_job_id": parent.job_id,
        "selected_types": list(req.resource_types),
        "preserved_artifacts": sorted(preserved_artifacts),
        "topic": (parent.metadata or {}).get("topic", ""),
        "status": child.status.value,
    }


__all__ = ["router"]
