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

from fastapi import APIRouter, HTTPException, Query, Request

from tutor.services.conversations import (
    AppendMessageRequest,
    ConversationAggregate,
    ConversationListResponse,
    CreateConversationRequest,
    Message,
    RecoveryWarning,
    UpdateConversationRequest,
    get_conversation_store,
)
from tutor.services.identity import identity_policy_for

router = APIRouter()


@router.post("/conversations", status_code=201)
async def create_or_get_conversation(
    req: CreateConversationRequest,
    request: Request,
) -> dict[str, Any]:
    user_id = identity_policy_for(request).resolve(req.user_id)
    store = get_conversation_store()
    session_id = req.session_id or f"sess_{uuid.uuid4().hex[:12]}"
    conv = await store.get_or_create(session_id=session_id, user_id=user_id, title=req.title)
    return conv.model_dump(mode="json")


@router.get("/conversations")
async def list_conversations(
    request: Request,
    user_id: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    user_id = identity_policy_for(request).resolve(user_id)
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
    session_id: str,
    request: Request,
    user_id: str = Query(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    user_id = identity_policy_for(request).resolve(user_id)
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
    request: Request,
    user_id: str = Query(..., min_length=1, max_length=64),
    jobs_limit: int = Query(50, ge=1, le=200),
    packages_limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Aggregate snapshot for one conversation (2026-06-21 plan).

    Returns a single payload containing:

      * the conversation header + message history
      * jobs filtered by ``session_id`` (newest capped window, returned
        chronologically)
      * resource packages filtered by ``session_id`` (newest capped window,
        returned chronologically)

    The front-end uses this when the user clicks a history row so it
    can replace ``jobsById`` / ``latestPackage`` / chat messages in one
    atomic store update — no flicker, no cross-session bleed, and
    background jobs running in other sessions are NOT cancelled.
    """
    user_id = identity_policy_for(request).resolve(user_id)
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

    # The conversation is the authorization boundary. Rows imported from an
    # older local browser identity are joined by session_id without applying a
    # second owner filter that would make them disappear after migration.
    jobs = await job_store.list_for_session(session_id, limit=jobs_limit)
    packages = await pkg_store.list_for_session(session_id, limit=packages_limit)

    warnings: list[RecoveryWarning] = []
    mismatched_owner = any(job.get("user_id") != detail.user_id for job in jobs) or any(
        (package.metadata or {}).get("user_id") != detail.user_id
        for package in packages
    )
    if mismatched_owner:
        warnings.append(
            RecoveryWarning(
                code="migrated_ownership",
                message="Recovered session records created under an earlier local identity.",
            )
        )

    for job in jobs:
        error = str(job.get("error") or "")
        if job.get("status") == "failed" and (
            "process restarted" in error or "timed out" in error
        ):
            warnings.append(
                RecoveryWarning(
                    code="interrupted_job_repaired",
                    message="An interrupted job was repaired to a terminal state.",
                    job_id=str(job.get("job_id") or "") or None,
                )
            )

    packages, missing_warnings = _mark_missing_artifacts(packages, jobs)
    warnings.extend(missing_warnings)

    from tutor.services.learner_profile import get_profile_store

    profile = await get_profile_store().get_or_create(detail.user_id)
    path_summary: dict[str, Any] = {}
    for package in reversed(packages):
        if package.learning_path_summary:
            path_summary = dict(package.learning_path_summary)
            break

    aggregate = ConversationAggregate(
        conversation=detail,
        jobs=jobs,
        packages=packages,
        profile_summary=profile.to_summary(),
        path_summary=path_summary,
        recovery_warnings=warnings,
    )
    return aggregate.model_dump(mode="json")


def _mark_missing_artifacts(packages, jobs):
    """Annotate missing resources without failing the aggregate request."""
    from tutor.services.artifacts import (
        UnsafeArtifactKey,
        resolve_artifact_key,
    )
    from tutor.services.config.settings import get_settings
    from tutor.services.resource_package.store import portable_format_specific

    data_dir = get_settings().data_dir
    eligible_jobs = [
        job
        for job in jobs
        if job.get("capability") == "resource_generation"
        and job.get("status") in {"succeeded", "failed", "partial"}
    ]

    def recovery_parent_for(package):
        job_id = package.originating_job_id
        if job_id:
            parent = next(
                (job for job in eligible_jobs if job.get("job_id") == job_id),
                None,
            )
            return parent, parent is None
        if len(eligible_jobs) == 1:
            return eligible_jobs[0], False
        return None, True

    recovered = []
    warnings: list[RecoveryWarning] = []
    association_warned: set[str] = set()
    for package in packages:
        resources = []
        for resource in package.resources:
            missing: list[str] = []
            fs = portable_format_specific(resource.format_specific, data_dir)
            references: list[str] = []
            unresolved_count = int(bool(fs.get("artifact_unresolved")))
            artifacts = fs.get("artifacts")
            if isinstance(artifacts, list):
                for entry in artifacts:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("unresolved"):
                        unresolved_count += 1
                    key = entry.get("artifact_key")
                    if key:
                        references.append(str(key))
            root_ref = fs.get("artifact_key")
            if root_ref:
                references.append(str(root_ref))

            for key in dict.fromkeys(references):
                try:
                    artifact_path = resolve_artifact_key(key, data_dir)
                except UnsafeArtifactKey:
                    artifact_path = None
                if artifact_path is None or not artifact_path.is_file():
                    missing.append(key)
                    warnings.append(
                        RecoveryWarning(
                            code="missing_artifact",
                            message="A generated resource file is missing and can be regenerated.",
                            package_id=package.package_id,
                            resource_id=resource.resource_id,
                            artifact_key=key or None,
                        )
                    )

            for _ in range(unresolved_count):
                warnings.append(
                    RecoveryWarning(
                        code="missing_artifact",
                        message="A generated resource file is missing and can be regenerated.",
                        package_id=package.package_id,
                        resource_id=resource.resource_id,
                        artifact_key=None,
                    )
                )

            if missing or unresolved_count:
                metadata = dict(resource.metadata or {})
                metadata["artifact_missing"] = True
                metadata["missing_artifact_keys"] = missing
                if not metadata.get("recovery_contract"):
                    retry_parent, association_missing = recovery_parent_for(package)
                    if retry_parent:
                        metadata["recovery_contract"] = {
                            "job_id": retry_parent.get("job_id"),
                            "resource_types": [resource.type.value],
                        }
                    elif (
                        association_missing
                        and package.package_id not in association_warned
                    ):
                        warnings.append(
                            RecoveryWarning(
                                code="recovery_association_missing",
                                message=(
                                    "The missing artifact cannot be associated with "
                                    "one generation job safely."
                                ),
                                package_id=package.package_id,
                                resource_id=resource.resource_id,
                            )
                        )
                        association_warned.add(package.package_id)
                resource = resource.model_copy(
                    update={"format_specific": fs, "metadata": metadata}
                )
            else:
                resource = resource.model_copy(update={"format_specific": fs})
            resources.append(resource)
        recovered.append(package.model_copy(update={"resources": resources}))
    return recovered, warnings


@router.patch("/conversations/{session_id}")
async def update_conversation(
    session_id: str,
    req: UpdateConversationRequest,
    request: Request,
    user_id: str = Query(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    user_id = identity_policy_for(request).resolve(user_id)
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
    session_id: str,
    request: Request,
    user_id: str = Query(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    user_id = identity_policy_for(request).resolve(user_id)
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
    request: Request,
    user_id: str = Query(..., min_length=1, max_length=64),
) -> dict[str, Any]:
    user_id = identity_policy_for(request).resolve(user_id)
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
