from __future__ import annotations

from types import SimpleNamespace

import pytest
from tutor.services.exercise_responses.publisher import (
    repair_unpublished_submission_events,
)
from tutor.services.exercise_responses.schema import ExerciseSubmission


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

        async def list_unpublished_page(
            self, *, after_row_id: int, limit: int = 1000
        ):
            self.page_cursors.append(after_row_id)
            return [
                SimpleNamespace(row_id=index + 1, submission=submission)
                for index, submission in enumerate(submissions)
                if index + 1 > after_row_id
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
