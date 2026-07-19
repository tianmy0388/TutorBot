"""Owner-scoped exercise drafts, submissions, and Python attempts."""

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
    submission_pipeline_budget_seconds,
)
from tutor.services.exercise_attempts.store import (
    AttemptConflictError,
    AttemptOwnershipError,
    ExerciseAttemptStore,
)
from tutor.services.exercise_responses.publisher import publish_submission_event
from tutor.services.exercise_responses.schema import (
    ExerciseDraft,
    ExerciseGradingStatus,
    ExerciseQuestionType,
    ExerciseSubmission,
    exercise_submission_request_identity,
)
from tutor.services.exercise_responses.store import (
    ExerciseResponseConflictError,
    ExerciseResponseStore,
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


class ExerciseDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str | None = Field(default=None, max_length=128)
    answer_json: Any


class ExerciseSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str | None = Field(default=None, max_length=128)
    session_id: str = Field(default="", max_length=64)
    answer_json: Any = None
    client_submission_id: str | None = Field(
        default=None, min_length=1, max_length=64
    )
    linked_code_attempt_id: str | None = Field(
        default=None, min_length=1, max_length=64
    )


def _attempt_store(request: Request) -> ExerciseAttemptStore:
    return request.app.state.exercise_attempt_store


def _response_store(request: Request) -> ExerciseResponseStore:
    return request.app.state.exercise_response_store


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


async def _owned_question(
    request: Request,
    *,
    user_id: str,
    package_id: str,
    resource_id: str | None,
    question_id: str,
) -> tuple[dict[str, Any], str, str]:
    package = await request.app.state.resource_package_store.get_for_user(
        package_id, user_id
    )
    if package is None:
        raise _not_found()
    matches: list[dict[str, Any]] = []
    for resource in package.resources:
        if resource.type != ResourceType.EXERCISE:
            continue
        if resource_id is not None and resource.resource_id != resource_id:
            continue
        questions = (resource.format_specific or {}).get("questions")
        if not isinstance(questions, list):
            continue
        matches.extend(
            {"question": item, "resource_id": resource.resource_id}
            for item in questions
            if isinstance(item, dict) and str(item.get("id", "")) == question_id
        )
    if len(matches) != 1:
        raise _not_found()
    match = matches[0]
    return match["question"], str(match["resource_id"]), package.topic


async def _owned_code_question(
    request: Request,
    *,
    user_id: str,
    package_id: str,
    question_id: str,
) -> tuple[CodeSpec, str, str]:
    question, _, course = await _owned_question(
        request,
        user_id=user_id,
        package_id=package_id,
        resource_id=None,
        question_id=question_id,
    )
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
    return code_spec, concept_id, course


def _malformed_answer() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": "MALFORMED_ANSWER",
            "message": "The submitted answer does not match the question type",
        },
    )


def _submission_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": ExerciseResponseConflictError.code,
            "message": "The client submission id is already used",
        },
    )


def _normalized_text(value: Any) -> str:
    if not isinstance(value, str):
        raise _malformed_answer()
    return " ".join(value.split()).casefold()


def _normalized_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _normalized_text(value)
    if normalized in {"true", "t", "yes", "y", "1", "对", "正确", "是"}:
        return True
    if normalized in {"false", "f", "no", "n", "0", "错", "错误", "否"}:
        return False
    raise _malformed_answer()


def _normalized_blank_text(value: Any) -> str:
    """Normalize one fill-blank element; unfilled slots are simply wrong."""
    if value is None:
        return ""
    return _normalized_text(value)


def _fill_blank_variants(entry: Any) -> set[str]:
    variants = entry if isinstance(entry, list) else [entry]
    return {_normalized_text(variant) for variant in variants}


def _score_fill_blank(submitted: Any, canonical: Any) -> bool:
    if isinstance(submitted, list):
        if len(submitted) == 1:
            # Single-blank arrays from the UI unwrap to the string path.
            submitted = "" if submitted[0] is None else submitted[0]
        elif not submitted:
            raise _malformed_answer()
        else:
            # Multi-blank: positional scoring against an equal-length
            # canonical list; each entry is one accepted string or a list
            # of accepted variant strings.
            if not isinstance(canonical, list) or len(canonical) != len(submitted):
                raise _malformed_answer()
            return all(
                _normalized_blank_text(item) in _fill_blank_variants(entry)
                for item, entry in zip(submitted, canonical, strict=True)
            )
    submitted_text = _normalized_text(submitted)
    accepted = canonical if isinstance(canonical, list) else [canonical]
    if not accepted:
        raise _malformed_answer()
    return submitted_text in {_normalized_text(item) for item in accepted}


def _score_answer(question_type: ExerciseQuestionType, submitted: Any, canonical: Any) -> tuple[bool, float]:
    if question_type == ExerciseQuestionType.SINGLE_CHOICE:
        correct = _normalized_text(submitted) == _normalized_text(canonical)
    elif question_type == ExerciseQuestionType.MULTIPLE_CHOICE:
        if not isinstance(submitted, list) or not isinstance(canonical, list):
            raise _malformed_answer()
        submitted_items = [_normalized_text(item) for item in submitted]
        if len(set(submitted_items)) != len(submitted_items):
            raise _malformed_answer()
        submitted_set = set(submitted_items)
        canonical_set = {_normalized_text(item) for item in canonical}
        if not submitted_set or not canonical_set:
            raise _malformed_answer()
        correct = submitted_set == canonical_set
    elif question_type == ExerciseQuestionType.TRUE_FALSE:
        correct = _normalized_boolean(submitted) == _normalized_boolean(canonical)
    elif question_type == ExerciseQuestionType.FILL_BLANK:
        correct = _score_fill_blank(submitted, canonical)
    elif question_type == ExerciseQuestionType.SHORT_ANSWER:
        submitted_text = _normalized_text(submitted)
        accepted = canonical if isinstance(canonical, list) else [canonical]
        if not accepted:
            raise _malformed_answer()
        correct = submitted_text in {_normalized_text(item) for item in accepted}
    else:
        raise _malformed_answer()
    return correct, 1.0 if correct else 0.0


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


async def _publish_submission_best_effort(
    submission: ExerciseSubmission, request: Request
) -> None:
    if submission.event_published:
        return
    try:
        await publish_submission_event(
            submission,
            response_store=_response_store(request),
            attempt_store=_attempt_store(request),
            workflow=request.app.state.learning_workflow,
            runner=getattr(request.app.state, "learning_runner", None),
        )
    except Exception as exc:  # terminal submission is already durable
        logger.warning(
            "EXERCISE_SUBMISSION_EVENT_PUBLICATION_DEFERRED exception_type={}",
            type(exc).__name__,
        )


@router.get(
    "/exercises/{package_id}/resources/{resource_id}/responses"
)
async def get_exercise_response_state(
    package_id: str,
    resource_id: str,
    request: Request,
    question_id: str = Query(min_length=1, max_length=64),
    user_id: str | None = Query(default=None, max_length=128),
) -> dict[str, Any]:
    canonical_user = _canonical_user(request, user_id)
    await _owned_question(
        request,
        user_id=canonical_user,
        package_id=package_id,
        resource_id=resource_id,
        question_id=question_id,
    )
    state = await _response_store(request).get_state(
        canonical_user, package_id, resource_id, question_id
    )
    return state.model_dump(mode="json")


@router.put(
    "/exercises/{package_id}/resources/{resource_id}/questions/{question_id}/draft"
)
async def put_exercise_draft(
    package_id: str,
    resource_id: str,
    question_id: str,
    body: ExerciseDraftRequest,
    request: Request,
) -> dict[str, Any]:
    user_id = _canonical_user(request, body.user_id)
    question, owned_resource_id, _ = await _owned_question(
        request,
        user_id=user_id,
        package_id=package_id,
        resource_id=resource_id,
        question_id=question_id,
    )
    try:
        question_type = ExerciseQuestionType(str(question.get("type", "")))
    except ValueError as exc:
        raise _not_found() from exc
    draft = ExerciseDraft(
        user_id=user_id,
        package_id=package_id,
        resource_id=owned_resource_id,
        question_id=question_id,
        question_type=question_type,
        answer_json=body.answer_json,
    )
    durable = await _response_store(request).upsert_draft(draft)
    return durable.model_dump(mode="json")


@router.post(
    "/exercises/{package_id}/resources/{resource_id}/questions/{question_id}/submit"
)
async def submit_exercise_response(
    package_id: str,
    resource_id: str,
    question_id: str,
    body: ExerciseSubmitRequest,
    request: Request,
) -> dict[str, Any]:
    user_id = _canonical_user(request, body.user_id)
    response_store = _response_store(request)
    if body.client_submission_id:
        existing = await response_store.get_by_client_id(
            body.client_submission_id, user_id
        )
        if existing is not None:
            try:
                retry_identity = exercise_submission_request_identity(
                    session_id=body.session_id,
                    package_id=package_id,
                    resource_id=resource_id,
                    question_id=question_id,
                    question_type=existing.question_type,
                    answer_json=body.answer_json,
                    linked_code_attempt_id=body.linked_code_attempt_id,
                )
            except ValueError as exc:
                raise _submission_conflict() from exc
            if retry_identity != existing.client_request_identity():
                raise _submission_conflict()
            await _publish_submission_best_effort(existing, request)
            return existing.model_dump(mode="json")

    question, owned_resource_id, course = await _owned_question(
        request,
        user_id=user_id,
        package_id=package_id,
        resource_id=resource_id,
        question_id=question_id,
    )
    try:
        question_type = ExerciseQuestionType(str(question.get("type", "")))
    except ValueError as exc:
        raise _not_found() from exc

    linked_attempt_id: str | None = None
    answer_json = body.answer_json
    session_id = body.session_id
    grading_status = ExerciseGradingStatus.AUTO_GRADED
    if question_type == ExerciseQuestionType.CODE:
        if not body.linked_code_attempt_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "CODE_ATTEMPT_REQUIRED",
                    "message": "Submit code through the execution endpoint first",
                },
            )
        attempt = await _attempt_store(request).get_for_user(
            body.linked_code_attempt_id, user_id
        )
        if (
            attempt is None
            or attempt.package_id != package_id
            or attempt.question_id != question_id
        ):
            raise _not_found()
        linked_attempt_id = attempt.attempt_id
        answer_json = None
        correct = attempt.status == AttemptStatus.PASSED
        score = (
            attempt.passed_tests / attempt.total_tests
            if attempt.total_tests
            else 0.0
        )
    else:
        if body.linked_code_attempt_id is not None:
            raise _malformed_answer()
        if question_type == ExerciseQuestionType.SHORT_ANSWER:
            _normalized_text(body.answer_json)
            accepted = question.get("accepted_answers")
            accepted_answers = (
                accepted
                if isinstance(accepted, list)
                and accepted
                and all(
                    isinstance(item, str) and _normalized_text(item)
                    for item in accepted
                )
                else None
            )
            if accepted_answers is None:
                grading_status = ExerciseGradingStatus.MANUAL_REVIEW
                correct = None
                score = None
            else:
                correct, score = _score_answer(
                    question_type, body.answer_json, accepted_answers
                )
        else:
            canonical = question.get("answer")
            if canonical is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "ANSWER_UNAVAILABLE",
                        "message": "This exercise cannot be scored",
                    },
                )
            correct, score = _score_answer(
                question_type, body.answer_json, canonical
            )

    explanation = question.get("explanation")
    if not isinstance(explanation, str) or not explanation:
        explanation = None
    submission = ExerciseSubmission(
        client_submission_id=body.client_submission_id,
        user_id=user_id,
        session_id=session_id,
        package_id=package_id,
        resource_id=owned_resource_id,
        question_id=question_id,
        question_type=question_type,
        answer_json=answer_json,
        answer=question.get("answer"),
        explanation=explanation,
        grading_status=grading_status,
        correct=correct,
        score=score,
        concept_id=str(question.get("knowledge_point") or question_id),
        course=course,
        linked_code_attempt_id=linked_attempt_id,
    )
    try:
        durable = await response_store.save_submission(submission)
    except ExerciseResponseConflictError as exc:
        raise _submission_conflict() from exc
    except Exception as exc:
        logger.error(
            "EXERCISE_RESPONSE_STORE_UNAVAILABLE exception_type={}",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RESPONSE_STORE_UNAVAILABLE",
                "message": "Exercise response service is unavailable",
            },
        ) from exc
    await _publish_submission_best_effort(durable, request)
    return durable.model_dump(mode="json")


async def _wait_for_claimed_attempt(
    store: ExerciseAttemptStore,
    *,
    attempt_id: str,
    user_id: str,
    timeout_seconds: int,
) -> ExerciseAttempt | None:
    deadline = (
        asyncio.get_running_loop().time()
        + submission_pipeline_budget_seconds(timeout_seconds)
    )
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


__all__ = [
    "ExerciseAttemptRequest",
    "ExerciseDraftRequest",
    "ExerciseSubmitRequest",
    "router",
]
