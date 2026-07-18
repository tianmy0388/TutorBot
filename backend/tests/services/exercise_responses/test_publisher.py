from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from tutor.services.exercise_attempts.schema import AttemptStatus, ExerciseAttempt
from tutor.services.exercise_attempts.store import ExerciseAttemptStore
from tutor.services.exercise_responses.publisher import (
    publish_submission_event,
    repair_unpublished_submission_events,
)
from tutor.services.exercise_responses.schema import ExerciseSubmission
from tutor.services.exercise_responses.store import ExerciseResponseStore
from tutor.services.learning_events.schema import EventType
from tutor.services.learning_events.store import LearningEventStore


def _submission(index: int) -> ExerciseSubmission:
    return ExerciseSubmission(
        submission_id=f"submission-{index}",
        user_id="local-user",
        session_id="sess-general",
        package_id="pkg-general",
        resource_id="resource-general",
        question_id=f"q-{index}",
        question_type="single_choice",
        answer_json="B",
        correct=True,
        score=1.0,
        concept_id="selection",
        course="python",
    )


@pytest.mark.asyncio
async def test_startup_repair_pages_past_failure_without_starving_tail() -> None:
    submissions = [_submission(index) for index in range(1002)]

    class FakeResponseStore:
        def __init__(self) -> None:
            self.page_cursors: list[int] = []
            self.marked: list[str] = []

        async def get_repair_high_watermark(self) -> int:
            return len(submissions)

        async def list_unpublished_page(
            self,
            *,
            after_row_id: int,
            through_row_id: int,
            limit: int = 1000,
        ):
            self.page_cursors.append(after_row_id)
            return [
                SimpleNamespace(row_id=index + 1, submission=submission)
                for index, submission in enumerate(submissions)
                if after_row_id < index + 1 <= through_row_id
            ][:limit]

        async def mark_event_published(
            self, submission_id: str, user_id: str
        ) -> bool:
            self.marked.append(submission_id)
            return True

    class FakeEventStore:
        def __init__(self) -> None:
            self.appended: list[str] = []

        async def append(self, event) -> None:
            self.appended.append(event.event_id)
            if event.event_id == "exercise-submission:submission-0":
                raise RuntimeError("persistent first-row failure")

    store = FakeResponseStore()
    event_store = FakeEventStore()
    repaired = await repair_unpublished_submission_events(
        response_store=store,  # type: ignore[arg-type]
        workflow=SimpleNamespace(event_store=event_store),  # type: ignore[arg-type]
    )

    assert repaired == 1001
    assert "exercise-submission:submission-1001" in event_store.appended
    assert "submission-1001" in store.marked
    assert store.page_cursors[:2] == [0, 1000]


@pytest.mark.asyncio
async def test_startup_repair_stops_at_initial_high_watermark() -> None:
    submissions = [_submission(0), _submission(1)]

    class TailInsertingStore:
        def __init__(self) -> None:
            self.inserted_tail = False
            self.marked: list[str] = []

        async def get_repair_high_watermark(self) -> int:
            return 2

        async def list_unpublished_page(
            self,
            *,
            after_row_id: int,
            through_row_id: int,
            limit: int = 1000,
        ):
            if not self.inserted_tail:
                submissions.append(_submission(2))
                self.inserted_tail = True
            return [
                SimpleNamespace(row_id=index + 1, submission=submission)
                for index, submission in enumerate(submissions)
                if after_row_id < index + 1 <= through_row_id
            ][:limit]

        async def mark_event_published(
            self, submission_id: str, user_id: str
        ) -> bool:
            self.marked.append(submission_id)
            return True

    class EventStore:
        async def append(self, event) -> None:
            return None

    store = TailInsertingStore()
    repaired = await repair_unpublished_submission_events(
        response_store=store,  # type: ignore[arg-type]
        workflow=SimpleNamespace(event_store=EventStore()),  # type: ignore[arg-type]
    )

    assert repaired == 2
    assert store.marked == ["submission-0", "submission-1"]
    assert "submission-2" not in store.marked


@pytest.mark.asyncio
async def test_real_store_repairs_append_then_mark_crash_exactly_once(
    tmp_path, monkeypatch
) -> None:
    response_store = ExerciseResponseStore(tmp_path / "responses.db")
    event_store = LearningEventStore(tmp_path / "events.db")
    await response_store.init()
    await event_store.init()
    durable = await response_store.save_submission(_submission(10))
    original_mark = response_store.mark_event_published
    calls = 0

    async def fail_first_mark(submission_id: str, user_id: str) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("crash after append")
        return await original_mark(submission_id, user_id)

    monkeypatch.setattr(response_store, "mark_event_published", fail_first_mark)
    workflow = SimpleNamespace(event_store=event_store)
    try:
        with pytest.raises(RuntimeError, match="crash after append"):
            await publish_submission_event(
                durable,
                response_store=response_store,
                workflow=workflow,  # type: ignore[arg-type]
                reconcile=False,
            )
        persisted = await response_store.get_submission_for_user(
            durable.submission_id, durable.user_id
        )
        assert persisted is not None and persisted.event_published is False

        repaired = await repair_unpublished_submission_events(
            response_store=response_store,
            workflow=workflow,  # type: ignore[arg-type]
        )
        assert repaired == 1
        events = await event_store.query(
            durable.user_id, event_types=[EventType.EXERCISE_SCORED]
        )
        assert [event.event_id for event in events] == [
            f"exercise-submission:{durable.submission_id}"
        ]
    finally:
        await response_store.close()
        await event_store.close()


@pytest.mark.asyncio
async def test_real_unpublished_linked_attempt_repairs_without_general_duplicate(
    tmp_path,
) -> None:
    response_store = ExerciseResponseStore(tmp_path / "responses.db")
    attempt_store = ExerciseAttemptStore(tmp_path / "attempts.db")
    event_store = LearningEventStore(tmp_path / "events.db")
    await asyncio.gather(
        response_store.init(), attempt_store.init(), event_store.init()
    )
    attempt = ExerciseAttempt(
        attempt_id="linked-unpublished",
        user_id="local-user",
        session_id="sess-code",
        package_id="pkg-code",
        question_id="q-code",
        concept_id="addition",
        course="python",
        source_code="def add(a, b): return a + b",
        status=AttemptStatus.PASSED,
        passed_tests=1,
        total_tests=1,
    )
    await attempt_store.save_terminal(attempt)
    linked = ExerciseSubmission(
        submission_id="linked-response",
        user_id="local-user",
        session_id="sess-code",
        package_id="pkg-code",
        resource_id="resource-code",
        question_id="q-code",
        question_type="code",
        answer_json=None,
        correct=True,
        score=1.0,
        concept_id="addition",
        course="python",
        linked_code_attempt_id=attempt.attempt_id,
    )
    await response_store.save_submission(linked)
    try:
        repaired = await repair_unpublished_submission_events(
            response_store=response_store,
            attempt_store=attempt_store,
            workflow=SimpleNamespace(event_store=event_store),  # type: ignore[arg-type]
        )
        assert repaired == 1
        events = await event_store.query(
            "local-user", event_types=[EventType.EXERCISE_SCORED]
        )
        assert [event.event_id for event in events] == [
            "exercise-attempt:linked-unpublished"
        ]
        persisted_attempt = await attempt_store.get_for_user(
            attempt.attempt_id, attempt.user_id
        )
        persisted_link = await response_store.get_submission_for_user(
            linked.submission_id, linked.user_id
        )
        assert persisted_attempt is not None and persisted_attempt.event_published
        assert persisted_link is not None and persisted_link.event_published
    finally:
        await response_store.close()
        await attempt_store.close()
        await event_store.close()
