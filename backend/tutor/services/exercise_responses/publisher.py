"""Crash-repairable general-submission to learning-event publication."""

from __future__ import annotations

from loguru import logger

from tutor.services.exercise_attempts.publisher import publish_attempt_event
from tutor.services.exercise_attempts.store import ExerciseAttemptStore
from tutor.services.exercise_responses.schema import (
    ExerciseGradingStatus,
    ExerciseSubmission,
)
from tutor.services.exercise_responses.store import ExerciseResponseStore
from tutor.services.learning_events.schema import EventType, LearningEvent
from tutor.services.learning_events.workflow import LearningWorkflow

_REPAIR_PAGE_SIZE = 1000


async def publish_submission_event(
    submission: ExerciseSubmission,
    *,
    response_store: ExerciseResponseStore,
    workflow: LearningWorkflow,
    attempt_store: ExerciseAttemptStore | None = None,
    runner=None,
    reconcile: bool = True,
) -> bool:
    """Publish scored evidence, or reuse the linked code-attempt evidence."""
    if submission.grading_status == ExerciseGradingStatus.MANUAL_REVIEW:
        return await response_store.mark_event_published(
            submission.submission_id, submission.user_id
        )
    if submission.linked_code_attempt_id:
        if attempt_store is None:
            return False
        attempt = await attempt_store.get_for_user(
            submission.linked_code_attempt_id, submission.user_id
        )
        if attempt is None:
            return False
        if not attempt.event_published:
            await publish_attempt_event(
                attempt,
                attempt_store=attempt_store,
                workflow=workflow,
                runner=runner,
                reconcile=reconcile,
            )
            attempt = await attempt_store.get_for_user(
                submission.linked_code_attempt_id, submission.user_id
            )
            if attempt is None or not attempt.event_published:
                return False
        return await response_store.mark_event_published(
            submission.submission_id, submission.user_id
        )

    event = LearningEvent(
        event_id=f"exercise-submission:{submission.submission_id}",
        user_id=submission.user_id,
        session_id=submission.session_id,
        course=submission.course,
        event_type=EventType.EXERCISE_SCORED,
        target_id=submission.question_id,
        concept_id=submission.concept_id or submission.question_id,
        score=submission.score,
        correct=submission.correct,
        metadata={
            "submission_id": submission.submission_id,
            "package_id": submission.package_id,
            "resource_id": submission.resource_id,
            "question_type": submission.question_type.value,
        },
        created_at=submission.created_at,
    )
    await workflow.event_store.append(event)
    marked = await response_store.mark_event_published(
        submission.submission_id, submission.user_id
    )
    if not marked:
        return False
    if reconcile:
        await workflow.reconcile_user(
            submission.user_id,
            session_id=submission.session_id,
            course=submission.course,
        )
        if runner is not None:
            await runner.resume_pending()
    return True


async def repair_unpublished_submission_events(
    *,
    response_store: ExerciseResponseStore,
    workflow: LearningWorkflow,
    attempt_store: ExerciseAttemptStore | None = None,
) -> int:
    """Best-effort stable-cursor startup repair of publication gaps."""
    repaired = 0
    cursor = 0
    watermark = await response_store.get_repair_high_watermark()
    if watermark <= 0:
        return 0
    while True:
        page = await response_store.list_unpublished_page(
            after_row_id=cursor,
            through_row_id=watermark,
            limit=_REPAIR_PAGE_SIZE,
        )
        if not page:
            break
        for record in page:
            cursor = record.row_id
            try:
                if await publish_submission_event(
                    record.submission,
                    response_store=response_store,
                    workflow=workflow,
                    attempt_store=attempt_store,
                    reconcile=False,
                ):
                    repaired += 1
            except Exception as exc:  # noqa: BLE001 - next startup/POST retries
                logger.warning(
                    "EXERCISE_SUBMISSION_EVENT_REPAIR_DEFERRED exception_type={}",
                    type(exc).__name__,
                )
    return repaired


__all__ = [
    "publish_submission_event",
    "repair_unpublished_submission_events",
]
