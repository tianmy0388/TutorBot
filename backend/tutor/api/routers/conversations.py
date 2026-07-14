"""HTTP endpoints for the conversations history (stage 4 of the 2026-06-21 plan).

Surface:

  POST   /conversations                              — create / get
  GET    /conversations?user_id=&limit=&offset=      — list (newest first)
  GET    /conversations/{session_id}                 — detail + messages
  GET    /conversations/{session_id}/aggregate       — detail + jobs + packages
                                                        (single atomic call
                                                        the front-end uses to
                                                        switch conversations)
  PATCH  /conversations/{session_id}                 — rename
  DELETE /conversations/{session_id}                 — delete + cascade
  POST   /conversations/{session_id}/messages        — append one message
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from tutor.services.conversations import (
    AppendMessageRequest,
    Conversation,
    ConversationDetail,
    ConversationListResponse,
    CreateConversationRequest,
    Message,
    UpdateConversationRequest,
    get_conversation_store,
)

router = APIRouter()


@router.post("/conversations", status_code=201)
async def create_or_get_conversation(
    req: CreateConversationRequest,
) -> dict[str, Any]:
    store = get_conversation_store()
    session_id = req.session_id or f"sess_{uuid.uuid4().hex[:12]}"
    conv = await store.get_or_create(
        session_id=session_id, user_id=req.user_id, title=req.title
    )
    return conv.model_dump(mode="json")


@router.get("/conversations")
async def list_conversations(
    user_id: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    store = get_conversation_store()
    items, total = await store.list_for_user(user_id, limit=limit, offset=offset)
    return ConversationListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    ).model_dump(mode="json")


@router.get("/conversations/{session_id}")
async def get_conversation(
    session_id: str, user_id: str = Query(..., min_length=1, max_length=64)
) -> dict[str, Any]:
    store = get_conversation_store()
    detail = await store.get_conversation_with_messages(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if detail.user_id != user_id:
        raise HTTPException(status_code=403, detail="not your conversation")
    return detail.model_dump(mode="json")


@router.get("/conversations/{session_id}/aggregate")
async def get_conversation_aggregate(
    session_id: str,
    user_id: str = Query(..., min_length=1, max_length=64),
    jobs_limit: int = Query(50, ge=1, le=200),
    packages_limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Aggregate snapshot for one conversation (2026-06-21 plan).

    Returns a single payload containing:

      * the conversation header + message history
      * jobs filtered by ``session_id`` (newest first, capped by ``jobs_limit``)
      * resource package summaries filtered by ``session_id`` (newest first,
        capped by ``packages_limit``)

    The front-end uses this when the user clicks a history row so it
    can replace ``jobsById`` / ``latestPackage`` / chat messages in one
    atomic store update — no flicker, no cross-session bleed, and
    background jobs running in other sessions are NOT cancelled.
    """
    conv_store = get_conversation_store()
    detail = await conv_store.get_conversation_with_messages(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if detail.user_id != user_id:
        raise HTTPException(status_code=403, detail="not your conversation")

    # Lazy imports so this endpoint doesn't pull in the heavy resource /
    # jobs stores on first request to a fresh process.
    from tutor.services.jobs import get_job_store
    from tutor.services.resource_package import get_resource_package_store

    job_store = get_job_store()
    pkg_store = get_resource_package_store()

    jobs = await job_store.list(
        user_id, limit=jobs_limit, session_id=session_id
    )
    packages = await pkg_store.list(
        user_id, limit=packages_limit, session_id=session_id
    )
    return {
        "conversation": detail.model_dump(mode="json"),
        "jobs": jobs,
        "packages": packages,
    }


@router.patch("/conversations/{session_id}")
async def update_conversation(
    session_id: str,
    req: UpdateConversationRequest,
    user_id: str = Query(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    store = get_conversation_store()
    existing = await store.get(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if existing.user_id != user_id:
        raise HTTPException(status_code=403, detail="not your conversation")
    updated = await store.update(session_id, title=req.title)
    if updated is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return updated.model_dump(mode="json")


@router.delete("/conversations/{session_id}")
async def delete_conversation(
    session_id: str, user_id: str = Query(..., min_length=1, max_length=64)
) -> dict[str, Any]:
    store = get_conversation_store()
    existing = await store.get(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if existing.user_id != user_id:
        raise HTTPException(status_code=403, detail="not your conversation")
    await store.delete(session_id)
    return {"deleted": True, "session_id": session_id}


@router.post(
    "/conversations/{session_id}/messages",
    status_code=201,
)
async def append_message(
    session_id: str,
    req: AppendMessageRequest,
    user_id: str = Query(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    store = get_conversation_store()
    existing = await store.get(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    if existing.user_id != user_id:
        raise HTTPException(status_code=403, detail="not your conversation")
    msg = Message(
        role=req.role,
        content=req.content,
        job_id=req.job_id,
        capability=req.capability,
        metadata=req.metadata,
    )
    persisted = await store.append_message(session_id, msg)
    if persisted is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return persisted.model_dump(mode="json")


__all__ = ["router"]
