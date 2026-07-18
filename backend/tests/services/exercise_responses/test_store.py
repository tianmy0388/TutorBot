from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from tutor.services.exercise_responses.schema import (
    ExerciseDraft,
    ExerciseGradingStatus,
    ExerciseSubmission,
)
from tutor.services.exercise_responses.store import (
    ExerciseResponseConflictError,
    ExerciseResponseStore,
)

USER = "local-user"
PACKAGE = "pkg-general"
RESOURCE = "resource-general"
QUESTION = "q-choice"


def _draft(
    *,
    user_id: str = USER,
    answer_json="B",
    updated_at: datetime | None = None,
) -> ExerciseDraft:
    return ExerciseDraft(
        user_id=user_id,
        package_id=PACKAGE,
        resource_id=RESOURCE,
        question_id=QUESTION,
        question_type="single_choice",
        answer_json=answer_json,
        updated_at=updated_at or datetime.now(UTC),
    )


def _submission(
    submission_id: str = "server-1",
    *,
    user_id: str = USER,
    client_submission_id: str | None = "client-1",
    answer_json="B",
    correct: bool = True,
    score: float = 1.0,
    created_at: datetime | None = None,
) -> ExerciseSubmission:
    return ExerciseSubmission(
        submission_id=submission_id,
        client_submission_id=client_submission_id,
        user_id=user_id,
        session_id="sess-general",
        package_id=PACKAGE,
        resource_id=RESOURCE,
        question_id=QUESTION,
        question_type="single_choice",
        answer_json=answer_json,
        correct=correct,
        score=score,
        concept_id="conditionals",
        course="python",
        created_at=created_at or datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_draft_upsert_restores_without_creating_submission(tmp_path) -> None:
    store = ExerciseResponseStore(tmp_path / "responses.db")
    await store.init()
    try:
        await store.upsert_draft(_draft(answer_json="A"))
        replacement = _draft(answer_json="B")
        await store.upsert_draft(replacement)

        state = await store.get_state(USER, PACKAGE, RESOURCE, QUESTION)
        assert state.draft is not None
        assert state.draft.answer_json == "B"
        assert state.submissions == []
        assert await store.list_unpublished_page(after_row_id=0) == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_state_is_owner_scoped_and_submission_clears_only_matching_draft(
    tmp_path,
) -> None:
    store = ExerciseResponseStore(tmp_path / "responses.db")
    await store.init()
    try:
        await store.upsert_draft(_draft())
        await store.upsert_draft(_draft(user_id="other-user", answer_json="A"))
        saved = await store.save_submission(_submission())

        owner_state = await store.get_state(USER, PACKAGE, RESOURCE, QUESTION)
        other_state = await store.get_state(
            "other-user", PACKAGE, RESOURCE, QUESTION
        )
        assert owner_state.draft is None
        assert [item.submission_id for item in owner_state.submissions] == [
            saved.submission_id
        ]
        assert other_state.draft is not None
        assert other_state.draft.answer_json == "A"
        assert other_state.submissions == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_client_submission_id_is_owner_scoped_idempotent_and_conflicting(
    tmp_path,
) -> None:
    store = ExerciseResponseStore(tmp_path / "responses.db")
    await store.init()
    try:
        first = await store.save_submission(_submission())
        replay = await store.save_submission(_submission(submission_id="server-2"))
        assert replay.submission_id == first.submission_id

        with pytest.raises(ExerciseResponseConflictError) as conflict:
            await store.save_submission(
                _submission(submission_id="server-3", answer_json="A", correct=False, score=0)
            )
        assert conflict.value.code == "SUBMISSION_ID_CONFLICT"

        other = await store.save_submission(
            _submission(submission_id="other-server", user_id="other-user")
        )
        assert other.submission_id != first.submission_id
        other_state = await store.get_state(
            "other-user", PACKAGE, RESOURCE, QUESTION
        )
        assert [item.submission_id for item in other_state.submissions] == [
            "other-server"
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_submission_history_and_crash_repair_cursor_are_stable(tmp_path) -> None:
    store = ExerciseResponseStore(tmp_path / "responses.db")
    await store.init()
    try:
        now = datetime.now(UTC)
        old = await store.save_submission(
            _submission(
                "old",
                client_submission_id=None,
                created_at=now - timedelta(seconds=1),
            )
        )
        new = await store.save_submission(
            _submission("new", client_submission_id=None, created_at=now)
        )

        state = await store.get_state(USER, PACKAGE, RESOURCE, QUESTION)
        assert [item.submission_id for item in state.submissions] == ["new", "old"]
        watermark = await store.get_repair_high_watermark()
        first_page = await store.list_unpublished_page(
            after_row_id=0, through_row_id=watermark, limit=1
        )
        second_page = await store.list_unpublished_page(
            after_row_id=first_page[-1].row_id,
            through_row_id=watermark,
            limit=1,
        )
        assert first_page[0].submission.submission_id == old.submission_id
        assert second_page[0].submission.submission_id == new.submission_id

        assert await store.mark_event_published("new", USER) is True
        assert await store.mark_event_published("old", "other-user") is False
        persisted = await store.get_submission_for_user("new", USER)
        assert persisted is not None and persisted.event_published is True
        remaining = await store.list_unpublished_page(
            after_row_id=0, through_row_id=watermark
        )
        assert [record.submission.submission_id for record in remaining] == ["old"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_init_migrates_an_existing_empty_database(tmp_path) -> None:
    db_path = tmp_path / "responses.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")

    store = ExerciseResponseStore(db_path)
    await store.init()
    await store.close()

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        draft_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(exercise_drafts)")
        }
        submission_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(exercise_submissions)")
        }
    assert {"exercise_drafts", "exercise_submissions"} <= tables
    assert {"user_id", "package_id", "resource_id", "question_id", "answer_json"} <= draft_columns
    assert {
        "event_published",
        "grading_status",
        "linked_code_attempt_id",
        "answer_json",
    } <= submission_columns


@pytest.mark.asyncio
async def test_init_adds_grading_status_to_pre_review_submission_table(tmp_path) -> None:
    db_path = tmp_path / "responses.db"
    original = ExerciseResponseStore(db_path)
    await original.init()
    await original.save_submission(_submission("legacy-row"))
    await original.close()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "ALTER TABLE exercise_submissions DROP COLUMN grading_status"
        )

    migrated = ExerciseResponseStore(db_path)
    await migrated.init()
    try:
        persisted = await migrated.get_submission_for_user("legacy-row", USER)
        assert persisted is not None
        assert persisted.grading_status == ExerciseGradingStatus.AUTO_GRADED
        assert persisted.correct is True
        assert persisted.score == 1.0
    finally:
        await migrated.close()


@pytest.mark.asyncio
async def test_manual_submission_round_trips_nullable_grading(tmp_path) -> None:
    store = ExerciseResponseStore(tmp_path / "responses.db")
    await store.init()
    try:
        manual = ExerciseSubmission(
            submission_id="manual",
            client_submission_id="manual-client",
            user_id=USER,
            session_id="sess-general",
            package_id=PACKAGE,
            resource_id=RESOURCE,
            question_id="q-short",
            question_type="short_answer",
            answer_json="A learner-authored explanation",
            grading_status=ExerciseGradingStatus.MANUAL_REVIEW,
            correct=None,
            score=None,
            concept_id="writing",
            course="python",
        )
        saved = await store.save_submission(manual)
        persisted = await store.get_submission_for_user(saved.submission_id, USER)
        assert persisted is not None
        assert persisted.grading_status == ExerciseGradingStatus.MANUAL_REVIEW
        assert persisted.correct is None
        assert persisted.score is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_two_store_instances_race_one_owner_client_key_to_one_row(tmp_path) -> None:
    db_path = tmp_path / "responses.db"
    first_store = ExerciseResponseStore(db_path)
    second_store = ExerciseResponseStore(db_path)
    await first_store.init()
    await second_store.init()
    try:
        first, second = await asyncio.gather(
            first_store.save_submission(_submission("racer-one")),
            second_store.save_submission(_submission("racer-two")),
        )
        assert first.submission_id == second.submission_id
        state = await first_store.get_state(USER, PACKAGE, RESOURCE, QUESTION)
        assert [item.submission_id for item in state.submissions] == [
            first.submission_id
        ]
    finally:
        await first_store.close()
        await second_store.close()
