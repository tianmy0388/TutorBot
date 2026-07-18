from __future__ import annotations

import asyncio
import sys
import threading
import time
from datetime import UTC, datetime

import httpx
import pytest
from tutor.api.main import create_app
from tutor.services.config.settings import Settings
from tutor.services.exercise_attempts.schema import (
    AttemptStatus,
    ExerciseAttempt,
    SubmissionExecutionResult,
)
from tutor.services.exercise_attempts.schema import (
    TestCaseResult as CaseResult,
)
from tutor.services.learning_events.schema import EventType
from tutor.services.resource_package.schema import ResourcePackage, ResourceType, build_resource


def _package(
    *,
    owner: str = "local-user",
    legacy: bool = False,
    invalid_spec: bool = False,
) -> ResourcePackage:
    question = {
        "id": "q-code",
        "type": "code",
        "difficulty": 2,
        "knowledge_point": "addition",
        "question": "Implement add",
        "answer": "legacy reference",
        "explanation": "",
        "estimated_seconds": 60,
    }
    if invalid_spec:
        question["code_spec"] = {
            "language": "python",
            "starter_code": "def add(a, b): pass",
            "tests": [
                {"name": "invalid", "call": "add(1, 2)", "expected_json": float("nan")}
            ],
            "time_limit_seconds": 3,
        }
    elif not legacy:
        question["code_spec"] = {
            "language": "python",
            "starter_code": "def add(a, b):\n    pass",
            "tests": [
                {"name": "positive", "call": "add(1, 2)", "expected_json": 3},
                {"name": "negative", "call": "add(-1, 1)", "expected_json": 0},
            ],
            "time_limit_seconds": 3,
        }
    return ResourcePackage(
        package_id="pkg-code",
        topic="python",
        resources=[
            build_resource(
                type=ResourceType.EXERCISE,
                title="Exercises",
                format_specific={"questions": [question], "total_questions": 1},
                metadata={"package_id": "pkg-code"},
            )
        ],
        metadata={"user_id": owner, "session_id": "sess-code"},
    )


async def _ready_app(
    tmp_path,
    *,
    multi_user: bool = False,
    legacy: bool = False,
    invalid_spec: bool = False,
):
    app = create_app(
        Settings(
            env="test",
            data_dir=tmp_path,
            execution_python=sys.executable,
            multi_user_enabled=multi_user,
        )
    )
    workflow = app.state.learning_workflow
    await workflow.event_store.init()
    await workflow.profile_store.init()
    await workflow.job_store.init()
    await app.state.resource_package_store.init()
    await app.state.exercise_attempt_store.init()
    await app.state.resource_package_store.save(
        _package(legacy=legacy, invalid_spec=invalid_spec), user_id="local-user"
    )
    return app


async def _close_app(app) -> None:
    await app.state.exercise_attempt_store.close()
    await app.state.resource_package_store.close()
    workflow = app.state.learning_workflow
    await workflow.event_store.close()
    await workflow.profile_store.close()
    await workflow.job_store.close()


@pytest.mark.asyncio
async def test_attempt_uses_package_tests_persists_and_emits_scored_event(tmp_path) -> None:
    app = await _ready_app(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/exercises/pkg-code/q-code/attempts",
                json={
                    "user_id": "untrusted-browser-id",
                    "session_id": "sess-code",
                    "source_code": "def add(a, b): return a + b",
                    "client_attempt_id": "client-1",
                },
            )
            assert response.status_code == 201
            body = response.json()
            assert body["user_id"] == "local-user"
            assert body["status"] == "passed"
            assert body["passed_tests"] == 2
            assert body["source_code"] == "def add(a, b): return a + b"
            assert "expected_json" not in response.text
            assert "call" not in response.text

            history = await client.get(
                "/api/v1/exercises/pkg-code/q-code/attempts",
                params={"user_id": "local-user", "limit": 10, "offset": 0},
            )
            assert history.status_code == 200
            assert history.json()["items"][0]["attempt_id"] == body["attempt_id"]
            assert history.json()["total"] == 1

        events = await app.state.learning_workflow.event_store.query(
            "local-user", event_types=[EventType.EXERCISE_SCORED]
        )
        assert len(events) == 1
        assert events[0].event_id == f"exercise-attempt:{body['attempt_id']}"
        assert events[0].score == 1.0
        assert events[0].concept_id == "addition"
        assert events[0].session_id == "sess-code"
        assert events[0].target_id == "q-code"
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_attempt_request_is_strict_idempotent_and_client_cannot_supply_tests(tmp_path) -> None:
    app = await _ready_app(tmp_path)
    payload = {
        "user_id": "local-user",
        "session_id": "sess-code",
        "source_code": "def add(a, b): return a + b",
        "client_attempt_id": "client-repeat",
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            first = await client.post(
                "/api/v1/exercises/pkg-code/q-code/attempts", json=payload
            )
            second = await client.post(
                "/api/v1/exercises/pkg-code/q-code/attempts", json=payload
            )
            assert first.status_code == second.status_code == 201
            assert first.json()["attempt_id"] == second.json()["attempt_id"]

            controlled = await client.post(
                "/api/v1/exercises/pkg-code/q-code/attempts",
                json={**payload, "client_attempt_id": "new", "tests": []},
            )
            assert controlled.status_code == 422

            conflict = await client.post(
                "/api/v1/exercises/pkg-code/q-code/attempts",
                json={**payload, "source_code": "def add(a, b): return 0"},
            )
            assert conflict.status_code == 409
            assert conflict.json()["detail"]["code"] == "ATTEMPT_ID_CONFLICT"
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_attempt_validates_source_and_uses_non_enumerating_ownership_errors(tmp_path) -> None:
    app = await _ready_app(tmp_path, multi_user=True)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for package_id, question_id, user_id in (
                ("missing", "q-code", "local-user"),
                ("pkg-code", "missing", "local-user"),
                ("pkg-code", "q-code", "another-user"),
            ):
                response = await client.post(
                    f"/api/v1/exercises/{package_id}/{question_id}/attempts",
                    json={
                        "user_id": user_id,
                        "session_id": "sess",
                        "source_code": "def add(a, b): return a + b",
                    },
                )
                assert response.status_code == 404
                assert response.json()["detail"]["code"] == "EXERCISE_NOT_FOUND"

            empty = await client.post(
                "/api/v1/exercises/pkg-code/q-code/attempts",
                json={"user_id": "local-user", "session_id": "sess", "source_code": "  "},
            )
            assert empty.status_code == 422
            assert empty.json()["detail"]["code"] == "SOURCE_CODE_EMPTY"
            oversized = await client.post(
                "/api/v1/exercises/pkg-code/q-code/attempts",
                json={
                    "user_id": "local-user",
                    "session_id": "sess",
                    "source_code": "x" * (128 * 1024 + 1),
                },
            )
            assert oversized.status_code == 413
            assert oversized.json()["detail"]["code"] == "SOURCE_CODE_TOO_LARGE"
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_legacy_code_spec_is_typed_unavailable_instead_of_breaking_package(tmp_path) -> None:
    app = await _ready_app(tmp_path, legacy=True)
    try:
        package = await app.state.resource_package_store.get_for_user(
            "pkg-code", "local-user"
        )
        assert package is not None
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/exercises/pkg-code/q-code/attempts",
                json={
                    "user_id": "local-user",
                    "session_id": "sess",
                    "source_code": "def add(a, b): return a + b",
                },
            )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "CODE_SPEC_UNAVAILABLE"
    finally:
        await _close_app(app)

    invalid_app = await _ready_app(tmp_path / "invalid", invalid_spec=True)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=invalid_app), base_url="http://test"
        ) as client:
            invalid = await client.post(
                "/api/v1/exercises/pkg-code/q-code/attempts",
                json={
                    "user_id": "local-user",
                    "session_id": "sess-code",
                    "source_code": "def add(a, b): return a + b",
                },
            )
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "CODE_SPEC_UNAVAILABLE"
    finally:
        await _close_app(invalid_app)


@pytest.mark.asyncio
async def test_failed_event_append_keeps_attempt_and_replay_repairs_gap(tmp_path, monkeypatch) -> None:
    app = await _ready_app(tmp_path)
    store = app.state.learning_workflow.event_store
    original_append = store.append
    calls = 0

    async def flaky_append(event):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("private database path")
        return await original_append(event)

    monkeypatch.setattr(store, "append", flaky_append)
    payload = {
        "user_id": "local-user",
        "session_id": "sess-code",
        "source_code": "def add(a, b): return a + b",
        "client_attempt_id": "repair-me",
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            first = await client.post(
                "/api/v1/exercises/pkg-code/q-code/attempts", json=payload
            )
            assert first.status_code == 201
            attempt_id = first.json()["attempt_id"]
            assert not (
                await app.state.exercise_attempt_store.get_for_user(
                    attempt_id, "local-user"
                )
            ).event_published

            replay = await client.post(
                "/api/v1/exercises/pkg-code/q-code/attempts", json=payload
            )
            assert replay.status_code == 201
            assert replay.json()["attempt_id"] == attempt_id

        repaired = await app.state.exercise_attempt_store.get_for_user(
            attempt_id, "local-user"
        )
        assert repaired.event_published
        events = await store.query("local-user", event_types=[EventType.EXERCISE_SCORED])
        assert [event.event_id for event in events] == [f"exercise-attempt:{attempt_id}"]
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_startup_repairs_unpublished_attempt_and_closes_owned_store(tmp_path) -> None:
    app = create_app(
        Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    )
    await app.state.exercise_attempt_store.init()
    attempt = ExerciseAttempt(
        attempt_id="crash-gap",
        user_id="local-user",
        session_id="sess-crash",
        package_id="pkg-crash",
        question_id="q-crash",
        concept_id="loops",
        course="python",
        source_code="def solve(): return 1",
        status=AttemptStatus.PASSED,
        passed_tests=1,
        total_tests=1,
        test_results=[CaseResult(name="one", passed=True, actual_json=1)],
        duration_seconds=0.1,
        created_at=datetime.now(UTC),
    )
    await app.state.exercise_attempt_store.save_terminal(attempt)
    await app.state.exercise_attempt_store.close()

    async with app.router.lifespan_context(app):
        persisted = await app.state.exercise_attempt_store.get_for_user(
            "crash-gap", "local-user"
        )
        assert persisted.event_published
        events = await app.state.learning_workflow.event_store.query(
            "local-user", event_types=[EventType.EXERCISE_SCORED]
        )
        assert events[0].event_id == "exercise-attempt:crash-gap"

    assert app.state.exercise_attempt_store._engine is None


@pytest.mark.asyncio
async def test_concurrent_same_client_id_executes_once_and_waiter_reads_terminal(
    tmp_path, monkeypatch
) -> None:
    app = await _ready_app(tmp_path)
    from tutor.api.routers import exercises as exercises_router

    calls = 0

    def slow_submission(*args, **kwargs):
        nonlocal calls
        calls += 1
        time.sleep(0.15)
        return SubmissionExecutionResult(
            status=AttemptStatus.PASSED,
            passed_tests=2,
            total_tests=2,
            test_results=[
                CaseResult(name="positive", passed=True, actual_json=3),
                CaseResult(name="negative", passed=True, actual_json=0),
            ],
            duration_seconds=0.15,
        )

    monkeypatch.setattr(exercises_router, "run_code_submission", slow_submission)
    payload = {
        "user_id": "local-user",
        "session_id": "sess-code",
        "source_code": "def add(a, b): return a + b",
        "client_attempt_id": "concurrent",
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            first, second = await asyncio.gather(
                client.post("/api/v1/exercises/pkg-code/q-code/attempts", json=payload),
                client.post("/api/v1/exercises/pkg-code/q-code/attempts", json=payload),
            )
        assert first.status_code == second.status_code == 201
        assert first.json()["attempt_id"] == second.json()["attempt_id"]
        assert calls == 1
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_concurrent_waiter_covers_policy_and_execution_budget(
    tmp_path, monkeypatch
) -> None:
    app = await _ready_app(tmp_path)
    from tutor.api.routers import exercises as exercises_router

    started = threading.Event()
    release = threading.Event()
    calls = 0

    def pipeline_length_submission(*args, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=10)
        return SubmissionExecutionResult(
            status=AttemptStatus.PASSED,
            passed_tests=2,
            total_tests=2,
            test_results=[
                CaseResult(name="positive", passed=True, actual_json=3),
                CaseResult(name="negative", passed=True, actual_json=0),
            ],
            duration_seconds=5.25,
        )

    monkeypatch.setattr(
        exercises_router, "run_code_submission", pipeline_length_submission
    )
    payload = {
        "user_id": "local-user",
        "session_id": "sess-code",
        "source_code": "def add(a, b): return a + b",
        "client_attempt_id": "pipeline-budget",
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            first_task = asyncio.create_task(
                client.post("/api/v1/exercises/pkg-code/q-code/attempts", json=payload)
            )
            assert await asyncio.to_thread(started.wait, 2)
            second_task = asyncio.create_task(
                client.post("/api/v1/exercises/pkg-code/q-code/attempts", json=payload)
            )
            await asyncio.sleep(5.25)
            release.set()
            first, second = await asyncio.gather(first_task, second_task)
        assert first.status_code == second.status_code == 201
        assert first.json()["attempt_id"] == second.json()["attempt_id"]
        assert calls == 1
    finally:
        release.set()
        await _close_app(app)


@pytest.mark.asyncio
async def test_unexpected_executor_failure_is_redacted_and_persisted(tmp_path, monkeypatch) -> None:
    app = await _ready_app(tmp_path)
    from tutor.api.routers import exercises as exercises_router

    def explode(*args, **kwargs):
        raise RuntimeError("C:/private/wrapper.py provider-secret")

    monkeypatch.setattr(exercises_router, "run_code_submission", explode)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/exercises/pkg-code/q-code/attempts",
                json={
                    "user_id": "local-user",
                    "session_id": "sess-code",
                    "source_code": "def add(a, b): return a + b",
                },
            )
        assert response.status_code == 201
        assert response.json()["status"] == "error"
        assert response.json()["error_code"] == "CODE_EXECUTION_ERROR"
        assert "private" not in response.text
        attempts = await app.state.exercise_attempt_store.list_attempts(
            "local-user", "pkg-code", "q-code", limit=10, offset=0
        )
        assert attempts[0].status == AttemptStatus.ERROR
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_resource_detail_public_projection_hides_server_code_spec(tmp_path) -> None:
    app = await _ready_app(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/v1/resources/packages/local-user/pkg-code"
            )
        assert response.status_code == 200
        question = response.json()["resources"][0]["format_specific"]["questions"][0]
        assert "answer" not in question
        assert question["code_spec"]["test_count"] == 2
        assert "tests" not in question["code_spec"]
        assert "expected_json" not in response.text
        assert "add(1, 2)" not in response.text
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_non_code_and_duplicate_question_ids_have_typed_safe_errors(tmp_path) -> None:
    app = await _ready_app(tmp_path)
    non_code = ResourcePackage(
        package_id="pkg-non-code",
        topic="python",
        resources=[
            build_resource(
                type=ResourceType.EXERCISE,
                title="Mixed",
                format_specific={
                    "questions": [
                        {
                            "id": "q-choice",
                            "type": "true_false",
                            "question": "Python exists",
                            "answer": True,
                        }
                    ]
                },
            )
        ],
        metadata={"user_id": "local-user"},
    )
    duplicate = ResourcePackage(
        package_id="pkg-duplicate",
        topic="python",
        resources=[
            build_resource(
                type=ResourceType.EXERCISE,
                title=f"Duplicate {index}",
                format_specific={
                    "questions": [
                        {
                            "id": "same",
                            "type": "code",
                            "question": "x",
                            "code_spec": {
                                "starter_code": "def x(): pass",
                                "tests": [
                                    {"name": "x", "call": "x()", "expected_json": 1}
                                ],
                            },
                        }
                    ]
                },
            )
            for index in range(2)
        ],
        metadata={"user_id": "local-user"},
    )
    await app.state.resource_package_store.save(non_code, user_id="local-user")
    await app.state.resource_package_store.save(duplicate, user_id="local-user")
    payload = {
        "user_id": "local-user",
        "session_id": "sess",
        "source_code": "def x(): return 1",
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            non_code_response = await client.post(
                "/api/v1/exercises/pkg-non-code/q-choice/attempts", json=payload
            )
            duplicate_response = await client.post(
                "/api/v1/exercises/pkg-duplicate/same/attempts", json=payload
            )
        assert non_code_response.status_code == 422
        assert non_code_response.json()["detail"]["code"] == "QUESTION_NOT_CODE"
        assert duplicate_response.status_code == 404
        assert duplicate_response.json()["detail"]["code"] == "EXERCISE_NOT_FOUND"
    finally:
        await _close_app(app)
