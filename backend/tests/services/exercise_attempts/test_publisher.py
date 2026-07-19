from __future__ import annotations

from types import SimpleNamespace

import pytest
from tutor.services.exercise_attempts.publisher import repair_unpublished_attempt_events
from tutor.services.exercise_attempts.schema import AttemptStatus, ExerciseAttempt


def _attempt(index: int) -> ExerciseAttempt:
    return ExerciseAttempt(
        attempt_id=f"attempt-{index}",
        user_id="local-user",
        session_id="sess-code",
        package_id="pkg-code",
        question_id=f"q-{index}",
        concept_id="addition",
        course="python",
        source_code="def add(a, b): return a + b",
        status=AttemptStatus.PASSED,
        passed_tests=1,
        total_tests=1,
    )


@pytest.mark.asyncio
async def test_startup_repair_pages_beyond_1000_and_failure_does_not_starve_tail() -> None:
    attempts = [_attempt(index) for index in range(1002)]

    class FakeAttemptStore:
        def __init__(self) -> None:
            self.page_cursors: list[int] = []
            self.marked: list[str] = []

        async def list_unpublished(self, *, limit: int = 1000):
            return attempts[:limit]

        async def list_unpublished_page(
            self, *, after_row_id: int, limit: int = 1000
        ):
            self.page_cursors.append(after_row_id)
            return [
                SimpleNamespace(row_id=index + 1, attempt=attempt)
                for index, attempt in enumerate(attempts)
                if index + 1 > after_row_id
            ][:limit]

        async def mark_event_published(self, attempt_id: str, user_id: str) -> bool:
            self.marked.append(attempt_id)
            return True

    class FakeEventStore:
        def __init__(self) -> None:
            self.appended: list[str] = []

        async def append(self, event) -> None:
            self.appended.append(event.event_id)
            if event.event_id == "exercise-attempt:attempt-0":
                raise RuntimeError("persistent first-row failure")

    store = FakeAttemptStore()
    event_store = FakeEventStore()
    repaired = await repair_unpublished_attempt_events(
        attempt_store=store,  # type: ignore[arg-type]
        workflow=SimpleNamespace(event_store=event_store),  # type: ignore[arg-type]
    )

    assert repaired == 1001
    assert "exercise-attempt:attempt-1001" in event_store.appended
    assert "attempt-1001" in store.marked
    assert store.page_cursors[:2] == [0, 1000]
