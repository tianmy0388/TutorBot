"""Learning event HTTP endpoints.

These endpoints turn the assessment subsystem into a real learning loop:
frontend viewers can record resource views, completions, ratings, and
exercise outcomes; AssessmentCapability then consumes the same event log.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from tutor.services.learning_events.schema import EventType, LearningEvent
from tutor.services.learning_events.store import get_learning_event_store

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
async def record_learning_event(req: LearningEventRequest) -> dict[str, Any]:
    """Append one learning event and return the persisted payload."""
    store = get_learning_event_store()
    await store.init()
    try:
        event = LearningEvent(
            user_id=req.user_id,
            event_type=req.event_type,
            target_id=req.target_id,
            concept_id=req.concept_id,
            duration_seconds=req.duration_seconds,
            score=req.score,
            correct=req.correct,
            metadata=dict(req.metadata or {}),
            created_at=req.created_at or datetime.now(timezone.utc),
        )
        saved = await store.record(event)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return saved.to_dict()


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
