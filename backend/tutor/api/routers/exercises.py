"""Owner-scoped Python exercise attempt endpoints."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from tutor.agents.resource.code_sandbox import run_code_submission
from tutor.services.exercise_attempts.publisher import publish_attempt_event
from tutor.services.exercise_attempts.schema import (
    AttemptStatus,
    ExerciseAttempt,
    SubmissionExecutionResult,
)
from tutor.services.exercise_attempts.store import (
    AttemptConflictError,
    AttemptOwnershipError,
    ExerciseAttemptStore,
)
from tutor.services.identity import IdentityRequired, identity_policy_for
from tutor.services.resource_package.schema import CodeSpec, ResourceType

router = APIRouter()
MAX_SOURCE_BYTES = 128 * 1024


class ExerciseAttemptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str | None = Field(default=None, max_length=128)
    session_id: str = Field(min_length=1, max_length=64)
    source_code: str
    client_attempt_id: str | None = Field(default=None, min_length=1, max_length=64)


def _attempt_store(request: Request) -> ExerciseAttemptStore:
    return request.app.state.exercise_attempt_store


def _canonical_user(request: Request, requested: str | None) -> str:
    try:
        return identity_policy_for(request).resolve(requested)
    except IdentityRequired as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "IDENTITY_REQUIRED", "message": "User identity is required"},
        ) from exc


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "EXERCISE_NOT_FOUND", "message": "Exercise was not found"},
    )


async def _owned_code_question(
    request: Request,
    *,
    user_id: str,
    package_id: str,
    question_id: str,
) -> tuple[CodeSpec, str, str]:
    package = await request.app.state.resource_package_store.get_for_user(
        package_id, user_id
    )
    if package is None:
        raise _not_found()
    matches: list[dict[str, Any]] = []
    for resource in package.resources:
        if resource.type != ResourceType.EXERCISE:
            continue
        questions = (resource.format_specific or {}).get("questions")
        if not isinstance(questions, list):
            continue
        matches.extend(
            item
            for item in questions
            if isinstance(item, dict) and str(item.get("id", "")) == question_id
        )
    if len(matches) != 1:
        raise _not_found()
    question = matches[0]
    if question.get("type") != "code":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "QUESTION_NOT_CODE",
                "message": "This question is not an executable code exercise",
            },
        )
    try:
        code_spec = CodeSpec.model_validate(question.get("code_spec"))
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CODE_SPEC_UNAVAILABLE",
                "message": "This legacy code exercise cannot be executed",
            },
        ) from exc
    concept_id = str(question.get("knowledge_point") or question_id)
    return code_spec, concept_id, package.topic


def _validate_source(source: str) -> None:
    if not source.strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "SOURCE_CODE_EMPTY", "message": "Source code is required"},
        )
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "SOURCE_CODE_TOO_LARGE",
                "message": "Source code exceeds the 128 KiB limit",
            },
        )


async def _publish_best_effort(attempt: ExerciseAttempt, request: Request) -> None:
    try:
        await publish_attempt_event(
            attempt,
            attempt_store=_attempt_store(request),
            workflow=request.app.state.learning_workflow,
            runner=getattr(request.app.state, "learning_runner", None),
        )
    except Exception as exc:  # terminal attempt is already durable
        logger.warning(
            "EXERCISE_EVENT_PUBLICATION_DEFERRED exception_type={}",
            type(exc).__name__,
        )


async def _wait_for_claimed_attempt(
    store: ExerciseAttemptStore,
    *,
    attempt_id: str,
    user_id: str,
    timeout_seconds: int,
) -> ExerciseAttempt | None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds + 2
    while asyncio.get_running_loop().time() < deadline:
        terminal = await store.get_for_user(attempt_id, user_id)
        if terminal is not None:
            return terminal
        await asyncio.sleep(0.025)
    return None


@router.post(
    "/exercises/{package_id}/{question_id}/attempts",
    status_code=status.HTTP_201_CREATED,
)
async def submit_exercise_attempt(
    package_id: str,
    question_id: str,
    body: ExerciseAttemptRequest,
    request: Request,
) -> dict[str, Any]:
    user_id = _canonical_user(request, body.user_id)
    _validate_source(body.source_code)
    code_spec, concept_id, course = await _owned_code_question(
        request,
        user_id=user_id,
        package_id=package_id,
        question_id=question_id,
    )
    store = _attempt_store(request)
    attempt_id = uuid.uuid4().hex
    acquired = True
    if body.client_attempt_id:
        try:
            claim = await store.claim_attempt(
                client_attempt_id=body.client_attempt_id,
                user_id=user_id,
                package_id=package_id,
                question_id=question_id,
                source_code=body.source_code,
            )
        except AttemptOwnershipError as exc:
            raise _not_found() from exc
        except AttemptConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": exc.code,
                    "message": "The client attempt id is already used",
                },
            ) from exc
        attempt_id = claim.attempt_id
        acquired = claim.acquired

    if not acquired:
        terminal = await _wait_for_claimed_attempt(
            store,
            attempt_id=attempt_id,
            user_id=user_id,
            timeout_seconds=code_spec.time_limit_seconds,
        )
        if terminal is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "ATTEMPT_IN_PROGRESS",
                    "message": "The matching attempt is still running",
                },
            )
        await _publish_best_effort(terminal, request)
        return terminal.model_dump(mode="json")

    settings = request.app.state.settings
    try:
        execution = await asyncio.to_thread(
            run_code_submission,
            body.source_code,
            code_spec=code_spec,
            interpreter=settings.execution_python,
        )
    except Exception as exc:  # noqa: BLE001 - persist stable redacted terminal
        logger.error(
            "EXERCISE_EXECUTION_FAILED exception_type={}",
            type(exc).__name__,
        )
        execution = SubmissionExecutionResult(
            status=AttemptStatus.ERROR,
            passed_tests=0,
            total_tests=len(code_spec.tests),
            test_results=[],
            stdout="",
            stderr="",
            duration_seconds=0.0,
            error_code="CODE_EXECUTION_ERROR",
        )
    attempt = ExerciseAttempt(
        attempt_id=attempt_id,
        client_attempt_id=body.client_attempt_id,
        user_id=user_id,
        session_id=body.session_id,
        package_id=package_id,
        question_id=question_id,
        concept_id=concept_id,
        course=course,
        source_code=body.source_code,
        status=execution.status,
        passed_tests=execution.passed_tests,
        total_tests=execution.total_tests,
        test_results=execution.test_results,
        stdout=execution.stdout,
        stderr=execution.stderr,
        duration_seconds=execution.duration_seconds,
        error_code=execution.error_code,
    )
    try:
        durable = await store.save_terminal(attempt)
    except AttemptOwnershipError as exc:
        raise _not_found() from exc
    except AttemptConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": "Attempt id conflict"},
        ) from exc
    except Exception as exc:
        logger.error(
            "EXERCISE_ATTEMPT_STORE_UNAVAILABLE exception_type={}",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ATTEMPT_STORE_UNAVAILABLE",
                "message": "Exercise attempt service is unavailable",
            },
        ) from exc
    await _publish_best_effort(durable, request)
    return durable.model_dump(mode="json")


@router.get("/exercises/{package_id}/{question_id}/attempts")
async def list_exercise_attempts(
    package_id: str,
    question_id: str,
    request: Request,
    user_id: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    canonical = _canonical_user(request, user_id)
    await _owned_code_question(
        request,
        user_id=canonical,
        package_id=package_id,
        question_id=question_id,
    )
    store = _attempt_store(request)
    items, total = await asyncio.gather(
        store.list_attempts(
        canonical,
        package_id,
        question_id,
        limit=limit,
        offset=offset,
        ),
        store.count_attempts(canonical, package_id, question_id),
    )
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


__all__ = ["ExerciseAttemptRequest", "router"]
