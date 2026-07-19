"""Durable learning-event, profile, and learning-path endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tutor.services.identity import IdentityRequired, identity_policy_for
from tutor.services.learning_events.schema import EventType, LearningEvent
from tutor.services.learning_events.store import EventConflictError
from tutor.services.learning_events.workflow import (
    LearningWorkflow,
    get_learning_workflow,
)

router = APIRouter()


class LearningEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=64)
    user_id: str | None = Field(default=None, max_length=128)
    session_id: str = Field(default="", max_length=64)
    event_type: EventType
    target_id: str = Field(default="", max_length=128)
    concept_id: str = Field(default="", max_length=128)
    duration_seconds: int = Field(default=0, ge=0)
    score: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    correct: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    course: str = Field(default="", max_length=128)

    @model_validator(mode="after")
    def validate_evidence(self) -> LearningEventRequest:
        if self.event_type == EventType.EXERCISE_SCORED:
            if self.score is None:
                raise ValueError("score is required for exercise_scored")
            if not self.concept_id.strip():
                raise ValueError("concept_id is required for exercise_scored")
        return self


def _workflow_for(request: Request) -> LearningWorkflow:
    return getattr(request.app.state, "learning_workflow", None) or get_learning_workflow()


def _resolve_user(request: Request, requested: str | None) -> str:
    try:
        return identity_policy_for(request).resolve(requested)
    except IdentityRequired as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "IDENTITY_REQUIRED", "message": "User identity is required"},
        ) from exc


def _store_unavailable(code: str, message: str, exc: Exception) -> HTTPException:
    logger.error(
        "learning persistence unavailable code={code} kind={kind}",
        code=code,
        kind=type(exc).__name__,
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": code, "message": message},
    )


@router.post("/learning/events", status_code=status.HTTP_202_ACCEPTED)
async def append_learning_event(
    body: LearningEventRequest,
    request: Request,
) -> dict[str, Any]:
    user_id = _resolve_user(request, body.user_id)
    workflow = _workflow_for(request)
    event = LearningEvent(
        event_id=body.event_id,
        user_id=user_id,
        session_id=body.session_id,
        course=body.course,
        event_type=body.event_type,
        target_id=body.target_id,
        concept_id=body.concept_id,
        duration_seconds=body.duration_seconds,
        score=body.score,
        correct=body.correct,
        metadata=body.metadata,
    )
    try:
        appended = await workflow.event_store.append(event)
    except EventConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": exc.code,
                "message": "The event id is already used by different evidence",
            },
        ) from exc
    except Exception as exc:
        raise _store_unavailable(
            "LEARNING_EVENT_STORE_UNAVAILABLE",
            "Learning event service is unavailable",
            exc,
        ) from exc

    children = []
    try:
        children = await workflow.reconcile_user(
            user_id,
            session_id=body.session_id,
            course=body.course,
        )
        runner = getattr(request.app.state, "learning_runner", "default")
        if runner == "default":
            from tutor.services.jobs import get_job_runner

            runner = get_job_runner()
        if runner is not None:
            await runner.resume_pending()
    except Exception as exc:  # event is durable; reconciliation retries safely
        logger.error(
            "LEARNING_EVENT_RECONCILIATION_DEFERRED exception_type={}",
            type(exc).__name__,
        )

    return {
        "event_id": appended.event.event_id,
        "sequence": appended.event.sequence,
        "user_id": user_id,
        "inserted": appended.inserted,
        "profile_job_id": children[0].job_id if children else None,
    }


def _profile_projection(profile) -> dict[str, Any]:
    return {
        **profile.to_summary(),
        "knowledge_map": dict(profile.knowledge_map.scores),
        "modality": profile.modality.model_dump(mode="json"),
        "pace": profile.learning_pace.model_dump(mode="json"),
        "motivation": profile.motivation.model_dump(mode="json"),
        "error_patterns": [item.model_dump(mode="json") for item in profile.error_patterns],
        "metadata": dict(profile.metadata),
        "event_watermark": profile.event_watermark,
    }


@router.get("/learning/profile/{user_id}")
async def get_learning_profile(user_id: str, request: Request) -> dict[str, Any]:
    canonical = _resolve_user(request, user_id)
    try:
        profile = await _workflow_for(request).profile_store.get(canonical)
    except Exception as exc:
        raise _store_unavailable(
            "LEARNING_PROFILE_UNAVAILABLE",
            "Learner profile service is unavailable",
            exc,
        ) from exc
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "LEARNING_PROFILE_NOT_FOUND", "message": "No learner profile yet"},
        )
    return _profile_projection(profile)


@router.get("/learning/path/{user_id}")
async def get_learning_path(
    user_id: str,
    request: Request,
    profile_version: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    canonical = _resolve_user(request, user_id)
    store = _workflow_for(request).profile_store
    try:
        path = (
            await store.get_path(canonical, profile_version)
            if profile_version is not None
            else await store.get_latest_path(canonical)
        )
    except Exception as exc:
        raise _store_unavailable(
            "LEARNING_PATH_UNAVAILABLE",
            "Learning path service is unavailable",
            exc,
        ) from exc
    if path is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "LEARNING_PATH_NOT_FOUND", "message": "No learning path yet"},
        )
    return path.model_dump(mode="json")


__all__ = ["LearningEventRequest", "router"]
