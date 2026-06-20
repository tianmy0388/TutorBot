"""Job HTTP endpoints (Phase 5.2).

All paths are scoped to ``/api/v1/jobs``. Job execution is driven by
the WebSocket at ``/api/v1/ws``; the REST layer is for inspection
and management (list, detail, cancel, delete).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from tutor.services.jobs import JobStatus, get_job_runner, get_job_store

router = APIRouter()


@router.get("/jobs/{user_id}")
async def list_jobs(
    user_id: str,
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """List jobs for a user (newest first)."""
    store = get_job_store()
    st: JobStatus | None = None
    if status:
        try:
            st = JobStatus(status)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid status: {status}"
            ) from exc
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
async def job_stats(user_id: str) -> dict[str, Any]:
    store = get_job_store()
    return await store.stats(user_id)


@router.get("/jobs/{user_id}/{job_id}")
async def get_job(user_id: str, job_id: str) -> dict[str, Any]:
    store = get_job_store()
    job = await store.get(job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="job not found")
    return job.to_full_dict()


@router.post("/jobs/{user_id}/{job_id}/cancel")
async def cancel_job(user_id: str, job_id: str) -> dict[str, Any]:
    runner = get_job_runner()
    ok = await runner.cancel(job_id, user_id=user_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="job is not active (already completed/failed/cancelled or not found)",
        )
    return {"cancelled": True, "job_id": job_id}


@router.delete("/jobs/{user_id}/{job_id}")
async def delete_job(user_id: str, job_id: str) -> dict[str, Any]:
    store = get_job_store()
    job = await store.get(job_id)
    if job is None or job.user_id != user_id:
        raise HTTPException(status_code=404, detail="job not found")
    deleted = await store.delete(job_id)
    return {"deleted": deleted, "job_id": job_id}


@router.delete("/jobs/{user_id}")
async def delete_all_jobs(user_id: str) -> dict[str, Any]:
    store = get_job_store()
    count = await store.delete_user(user_id)
    return {"deleted": count, "user_id": user_id}


__all__ = ["router"]