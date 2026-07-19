"""Learner profile read endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from tutor.services.learning_events.workflow import get_learning_workflow

router = APIRouter()


@router.get("/profile/{user_id}")
async def get_profile(user_id: str, request: Request) -> dict[str, Any]:
    """Return the complete frontend-facing learner profile."""
    if not user_id.strip():
        raise HTTPException(status_code=400, detail="user_id is required")

    workflow = (
        getattr(request.app.state, "learning_workflow", None)
        or get_learning_workflow()
    )
    await workflow.profile_store.init()
    profile = await workflow.profile_store.get_or_create(user_id)
    summary = profile.to_summary()
    return {
        **summary,
        "knowledge_map": dict(profile.knowledge_map.scores),
        "modality": profile.modality.model_dump(mode="json"),
        "pace": profile.learning_pace.model_dump(mode="json"),
        "motivation": profile.motivation.model_dump(mode="json"),
        "error_patterns": [
            pattern.model_dump(mode="json") for pattern in profile.error_patterns
        ],
        "metadata": dict(profile.metadata),
    }


__all__ = ["router"]
