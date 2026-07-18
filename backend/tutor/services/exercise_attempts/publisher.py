"""Crash-repairable attempt → learning-event publication."""

from __future__ import annotations

from loguru import logger

from tutor.services.exercise_attempts.schema import AttemptStatus, ExerciseAttempt
from tutor.services.exercise_attempts.store import ExerciseAttemptStore
from tutor.services.learning_events.schema import EventType, LearningEvent
from tutor.services.learning_events.workflow import LearningWorkflow


async def publish_attempt_event(
    attempt: ExerciseAttempt,
    *,
    attempt_store: ExerciseAttemptStore,
    workflow: LearningWorkflow,
    runner=None,
    reconcile: bool = True,
) -> bool:
    """Append the deterministic scored event, then mark the attempt row."""
    score = attempt.passed_tests / attempt.total_tests if attempt.total_tests else 0.0
    event = LearningEvent(
        event_id=f"exercise-attempt:{attempt.attempt_id}",
        user_id=attempt.user_id,
        session_id=attempt.session_id,
        course=attempt.course,
        event_type=EventType.EXERCISE_SCORED,
        target_id=attempt.question_id,
        concept_id=attempt.concept_id or attempt.question_id,
        duration_seconds=max(0, int(round(attempt.duration_seconds))),
        score=score,
        correct=attempt.status == AttemptStatus.PASSED,
        metadata={
            "attempt_id": attempt.attempt_id,
            "status": attempt.status.value,
            "passed_tests": attempt.passed_tests,
            "total_tests": attempt.total_tests,
        },
        created_at=attempt.created_at,
    )
    await workflow.event_store.append(event)
    marked = await attempt_store.mark_event_published(
        attempt.attempt_id, attempt.user_id
    )
    if not marked:
        return False
    if reconcile:
        await workflow.reconcile_user(
            attempt.user_id,
            session_id=attempt.session_id,
            course=attempt.course,
        )
        if runner is not None:
            await runner.resume_pending()
    return True


async def repair_unpublished_attempt_events(
    *,
    attempt_store: ExerciseAttemptStore,
    workflow: LearningWorkflow,
) -> int:
    """Best-effort startup repair; gaps remain replayable after failures."""
    repaired = 0
    for attempt in await attempt_store.list_unpublished():
        try:
            if await publish_attempt_event(
                attempt,
                attempt_store=attempt_store,
                workflow=workflow,
                reconcile=False,
            ):
                repaired += 1
        except Exception as exc:  # noqa: BLE001 - next startup/POST retries
            logger.warning(
                "EXERCISE_EVENT_REPAIR_DEFERRED exception_type={}",
                type(exc).__name__,
            )
    return repaired


__all__ = ["publish_attempt_event", "repair_unpublished_attempt_events"]
