from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from tutor.services.exercise_attempts import schema as attempt_schema
from tutor.services.exercise_attempts.schema import (
    AttemptStatus,
    ExerciseAttempt,
)
from tutor.services.exercise_attempts.schema import (
    TestCaseResult as CaseResult,
)
from tutor.services.exercise_attempts.store import (
    AttemptConflictError,
    AttemptOwnershipError,
    ExerciseAttemptStore,
)


def _attempt(
    attempt_id: str,
    *,
    user_id: str = "local-user",
    client_attempt_id: str | None = None,
    source_code: str = "def add(a, b): return a + b",
    created_at: datetime | None = None,
) -> ExerciseAttempt:
    return ExerciseAttempt(
        attempt_id=attempt_id,
        client_attempt_id=client_attempt_id,
        user_id=user_id,
        session_id="sess-code",
        package_id="pkg-code",
        question_id="q-code",
        concept_id="addition",
        course="python",
        source_code=source_code,
        status=AttemptStatus.PASSED,
        passed_tests=1,
        total_tests=1,
        test_results=[CaseResult(name="adds", passed=True, actual_json=3)],
        stdout="ok",
        stderr="",
        duration_seconds=0.1,
        created_at=created_at or datetime.now(UTC),
    )


def test_default_claim_lease_exceeds_maximum_submission_pipeline(tmp_path) -> None:
    store = ExerciseAttemptStore(tmp_path / "attempts.db")
    maximum_pipeline = attempt_schema.submission_pipeline_budget_seconds(10)
    assert store._claim_lease_seconds > maximum_pipeline


@pytest.mark.asyncio
async def test_store_orders_owner_scoped_attempts_and_marks_publication(tmp_path) -> None:
    store = ExerciseAttemptStore(tmp_path / "attempts.db")
    await store.init()
    try:
        now = datetime.now(UTC)
        await store.save_terminal(_attempt("a-old", created_at=now - timedelta(seconds=1)))
        await store.save_terminal(_attempt("a-new", created_at=now))
        await store.save_terminal(_attempt("other", user_id="someone-else", created_at=now))

        listed = await store.list_attempts(
            "local-user", "pkg-code", "q-code", limit=10, offset=0
        )
        assert [item.attempt_id for item in listed] == ["a-new", "a-old"]
        assert await store.count_attempts("local-user", "pkg-code", "q-code") == 2
        assert await store.count_attempts("someone-else", "pkg-code", "q-code") == 1
        first_page = await store.list_unpublished_page(after_row_id=0, limit=2)
        assert [item.attempt.attempt_id for item in first_page] == ["a-old", "a-new"]
        second_page = await store.list_unpublished_page(
            after_row_id=first_page[-1].row_id,
            limit=2,
        )
        assert [item.attempt.attempt_id for item in second_page] == ["other"]
        assert [item.attempt_id for item in await store.list_unpublished()] == [
            "a-old",
            "a-new",
            "other",
        ]

        assert await store.mark_event_published("a-new", "local-user") is True
        assert (await store.get_for_user("a-new", "local-user")).event_published is True
        assert await store.mark_event_published("a-new", "someone-else") is False
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_client_attempt_id_is_idempotent_and_conflicts_are_stable(tmp_path) -> None:
    store = ExerciseAttemptStore(tmp_path / "attempts.db")
    await store.init()
    try:
        original = _attempt("server-1", client_attempt_id="client-1")
        saved = await store.save_terminal(original)
        replay = await store.save_terminal(
            _attempt("server-2", client_attempt_id="client-1")
        )
        assert replay.attempt_id == saved.attempt_id

        with pytest.raises(AttemptConflictError):
            await store.save_terminal(
                _attempt(
                    "server-3",
                    client_attempt_id="client-1",
                    source_code="def add(a, b): return 0",
                )
            )
        with pytest.raises(AttemptOwnershipError):
            await store.save_terminal(
                _attempt(
                    "server-4",
                    user_id="someone-else",
                    client_attempt_id="client-1",
                )
            )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_additively_migrates_event_publication_column(tmp_path) -> None:
    db_path = tmp_path / "attempts.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE exercise_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id VARCHAR(64) NOT NULL UNIQUE,
                client_attempt_id VARCHAR(64) UNIQUE,
                user_id VARCHAR(128) NOT NULL,
                session_id VARCHAR(64) NOT NULL DEFAULT '',
                package_id VARCHAR(64) NOT NULL,
                question_id VARCHAR(64) NOT NULL,
                concept_id VARCHAR(128) NOT NULL DEFAULT '',
                course VARCHAR(128) NOT NULL DEFAULT '',
                source_code TEXT NOT NULL,
                status VARCHAR(32) NOT NULL,
                passed_tests INTEGER NOT NULL,
                total_tests INTEGER NOT NULL,
                test_results JSON NOT NULL,
                stdout TEXT NOT NULL DEFAULT '',
                stderr TEXT NOT NULL DEFAULT '',
                duration_seconds FLOAT NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        )

    store = ExerciseAttemptStore(db_path)
    await store.init()
    await store.close()
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(exercise_attempts)")}
    assert "event_published" in columns
    assert "error_code" in columns


@pytest.mark.asyncio
async def test_durable_claim_allows_only_one_executor_for_client_attempt_id(tmp_path) -> None:
    store = ExerciseAttemptStore(tmp_path / "attempts.db")
    await store.init()
    try:
        first = await store.claim_attempt(
            client_attempt_id="one-executor",
            user_id="local-user",
            package_id="pkg-code",
            question_id="q-code",
            source_code="def add(a, b): return a + b",
        )
        second = await store.claim_attempt(
            client_attempt_id="one-executor",
            user_id="local-user",
            package_id="pkg-code",
            question_id="q-code",
            source_code="def add(a, b): return a + b",
        )
        assert first.acquired is True
        assert second.acquired is False
        assert second.attempt_id == first.attempt_id
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_stale_and_startup_orphan_claims_are_recoverable(tmp_path) -> None:
    store = ExerciseAttemptStore(tmp_path / "attempts.db", claim_lease_seconds=0)
    await store.init()
    try:
        first = await store.claim_attempt(
            client_attempt_id="stale",
            user_id="local-user",
            package_id="pkg-code",
            question_id="q-code",
            source_code="pass",
        )
        takeover = await store.claim_attempt(
            client_attempt_id="stale",
            user_id="local-user",
            package_id="pkg-code",
            question_id="q-code",
            source_code="pass",
        )
        assert takeover.acquired is True
        assert takeover.attempt_id == first.attempt_id

        assert await store.reap_orphaned_claims() == 1
        restarted = await store.claim_attempt(
            client_attempt_id="stale",
            user_id="local-user",
            package_id="pkg-code",
            question_id="q-code",
            source_code="pass",
        )
        assert restarted.acquired is True
        assert restarted.attempt_id != first.attempt_id
    finally:
        await store.close()
