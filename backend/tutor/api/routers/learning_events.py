"""Learning event HTTP endpoints.

These endpoints turn the assessment subsystem into a real learning loop:
frontend viewers can record resource views, completions, ratings, and
exercise outcomes; AssessmentCapability then consumes the same event log.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from tutor.services.learning_events.schema import EventType, LearningEvent
from tutor.services.learning_events.store import get_learning_event_store
from tutor.services.learning_events.workflow import LearningWorkflow, get_learning_workflow
from tutor.services.learner_profile.builder import ProfileBuilder
from tutor.services.learner_profile.schema import empty_profile

router = APIRouter()


class LearningEventRequest(BaseModel):
    user_id: str = Field(default="anonymous", min_length=1, max_length=128)
    event_type: EventType
    target_id: str = Field(default="", max_length=256)
    concept_id: str = Field(default="", max_length=256)
    duration_seconds: int = Field(default=0, ge=0, le=24 * 60 * 60)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    correct: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


@router.post("/learning-events")
async def record_learning_event(
    req: LearningEventRequest,
    request: Request,
) -> dict[str, Any]:
    """Append one learning event and return the persisted payload."""
    workflow: LearningWorkflow = (
        getattr(request.app.state, "learning_workflow", None)
        or get_learning_workflow()
    )
    store = workflow.event_store
    await store.init()
    await workflow.profile_store.init()
    await workflow.job_store.init()
    try:
        score = req.score
        if (
            req.event_type == EventType.EXERCISE_ATTEMPTED
            and score is None
            and req.correct is not None
        ):
            score = 1.0 if req.correct else 0.0
        metadata = dict(req.metadata or {})
        course = str(
            metadata.get("course")
            or metadata.get("knowledge_graph_id")
            or ""
        )
        event = LearningEvent(
            user_id=req.user_id,
            session_id=str(metadata.get("session_id") or ""),
            course=course,
            event_type=req.event_type,
            target_id=req.target_id,
            concept_id=req.concept_id,
            duration_seconds=req.duration_seconds,
            score=score,
            correct=req.correct,
            metadata=metadata,
            created_at=req.created_at or datetime.now(timezone.utc),
        )
        appended = await store.append(event)
        profile_update: dict[str, Any] = {}
        if req.event_type == EventType.EXERCISE_ATTEMPTED:
            current = await workflow.profile_store.get(req.user_id)
            current = current or empty_profile(req.user_id)
            events = await store.list_since(
                req.user_id,
                current.event_watermark,
                through_sequence=appended.event.sequence,
            )
            candidate = ProfileBuilder(
                store=workflow.profile_store,
            ).aggregate_events(
                current,
                events,
                through_sequence=appended.event.sequence,
            )
            outcome = await workflow.profile_store.save_event_profile(
                candidate,
                expected_watermark=current.event_watermark,
            )
            profile_update = {
                "profile_version": outcome.profile.version,
                "mastery": outcome.profile.knowledge_map.get(req.concept_id),
            }
        children = await workflow.reconcile_user(
            req.user_id,
            session_id=event.session_id,
            course=course,
        )
        runner = getattr(request.app.state, "learning_runner", "default")
        if runner == "default":
            from tutor.services.jobs import get_job_runner

            runner = get_job_runner()
        if runner is not None and children:
            await runner.resume_pending()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "legacy learning event failed exception_type={}",
            type(exc).__name__,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**appended.event.to_dict(), **profile_update}


@router.get("/learning-events/{user_id}")
async def list_learning_events(
    user_id: str,
    limit: int = Query(50, ge=1, le=500),
    event_type: EventType | None = None,
) -> dict[str, Any]:
    """List recent learning events for one user."""
    store = get_learning_event_store()
    await store.init()
    events = await store.query(
        user_id,
        event_types=[event_type] if event_type else None,
        limit=limit,
    )
    return {
        "user_id": user_id,
        "items": [e.to_dict() for e in events],
        "total": len(events),
    }


@router.get("/learning-events/{user_id}/stats")
async def learning_event_stats(
    user_id: str,
    window_hours: int = Query(168, ge=1, le=24 * 365),
) -> dict[str, Any]:
    """Return aggregate learning statistics for assessment/debug panels."""
    store = get_learning_event_store()
    await store.init()
    stats = await store.stats(user_id, window_hours=window_hours)
    return {"user_id": user_id, **stats}


__all__ = ["router"]
