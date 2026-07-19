from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text
from tutor.agents.resource.exercise_generator import ExerciseGeneratorAgent
from tutor.api.main import create_app
from tutor.core.context import UnifiedContext
from tutor.services.config.settings import Settings
from tutor.services.exercise_attempts.schema import (
    AttemptStatus,
    ExerciseAttempt,
    SubmissionExecutionResult,
)
from tutor.services.exercise_attempts.schema import (
    TestCaseResult as CaseResult,
)
from tutor.services.exercise_responses.schema import ExerciseSubmission
from tutor.services.learning_events.schema import EventType
from tutor.services.llm.base import LLMResponse
from tutor.services.resource_package.schema import ResourcePackage, ResourceType, build_resource


def _static_exercise_llm(payload: dict):
    class StaticExerciseLLM:
        model = "static-exercise"
        default_temperature = 0.0
        default_max_tokens = 4096

        async def call(self, request):
            return LLMResponse(
                content=json.dumps(payload, ensure_ascii=False),
                model=self.model,
                finish_reason="stop",
            )

    return StaticExerciseLLM()


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
    package = ResourcePackage(
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
    package.resources[0].resource_id = "resource-code"
    return package


def _general_package(*, owner: str = "local-user") -> ResourcePackage:
    questions = [
        {
            "id": "q-single",
            "type": "single_choice",
            "knowledge_point": "selection",
            "question": "Choose B",
            "options": [
                {"label": "A", "text": "A"},
                {"label": "B", "text": "B"},
            ],
            "answer": "B",
            "explanation": "B is the correct option.",
        },
        {
            "id": "q-multiple",
            "type": "multiple_choice",
            "knowledge_point": "collections",
            "question": "Choose A and C",
            "options": [
                {"label": "A", "text": "A"},
                {"label": "B", "text": "B"},
                {"label": "C", "text": "C"},
            ],
            "answer": ["A", "C"],
        },
        {
            "id": "q-boolean",
            "type": "true_false",
            "knowledge_point": "booleans",
            "question": "True is truthy",
            "answer": True,
        },
        {
            "id": "q-fill",
            "type": "fill_blank",
            "knowledge_point": "strings",
            "question": "Greeting",
            "answer": "Hello World",
        },
        {
            "id": "q-fill-multi",
            "type": "fill_blank",
            "knowledge_point": "geography",
            "question": "The capital of France is ___ and of Germany is ___.",
            "answer": [["paris", "parisian"], "berlin"],
            "explanation": "Paris and Berlin are the capitals.",
        },
        {
            "id": "q-short",
            "type": "short_answer",
            "knowledge_point": "algorithms",
            "question": "Expand DP",
            "answer": "(开放式回答)",
            "accepted_answers": ["dynamic programming", "dynamic-programming"],
        },
        {
            "id": "q-short-open",
            "type": "short_answer",
            "knowledge_point": "writing",
            "question": "Rewrite this idea in your own words",
            "answer": "(开放式回答)",
        },
        {
            "id": "q-short-legacy",
            "type": "short_answer",
            "knowledge_point": "reasoning",
            "question": "Explain an unrelated example",
            "answer": "legacy model answer",
        },
    ]
    resource = build_resource(
        type=ResourceType.EXERCISE,
        title="General exercises",
        format_specific={"questions": questions, "total_questions": len(questions)},
        metadata={"package_id": "pkg-general"},
    )
    resource.resource_id = "resource-general"
    return ResourcePackage(
        package_id="pkg-general",
        topic="computer-science",
        resources=[resource],
        metadata={"user_id": owner, "session_id": "sess-general"},
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
    await app.state.exercise_response_store.init()
    await app.state.resource_package_store.save(
        _package(legacy=legacy, invalid_spec=invalid_spec), user_id="local-user"
    )
    await app.state.resource_package_store.save(
        _general_package(), user_id="local-user"
    )
    return app


async def _close_app(app) -> None:
    await app.state.exercise_response_store.close()
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
        stored_package = await app.state.resource_package_store.get("pkg-code")
        assert stored_package is not None
        async with app.state.resource_package_store._engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE resources SET resource_metadata = :metadata "
                    "WHERE resource_id = :resource_id"
                ),
                {
                    "metadata": '{"package_id":"pkg-code"}',
                    "resource_id": stored_package.resources[0].resource_id,
                },
            )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/v1/resources/packages/local-user/pkg-code"
            )
        assert response.status_code == 200
        assert response.json()["resources"][0]["metadata"]["package_persisted"] is True
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


@pytest.mark.asyncio
async def test_general_draft_restores_without_event_and_submit_scores_server_side(
    tmp_path,
) -> None:
    app = await _ready_app(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            draft = await client.put(
                "/api/v1/exercises/pkg-general/resources/resource-general/questions/q-single/draft",
                json={"user_id": "ignored", "answer_json": " B "},
            )
            assert draft.status_code == 200
            assert draft.json()["answer_json"] == " B "
            assert "correct" not in draft.text
            assert "canonical" not in draft.text
            assert await app.state.learning_workflow.event_store.query(
                "local-user", event_types=[EventType.EXERCISE_SCORED]
            ) == []

            restored = await client.get(
                "/api/v1/exercises/pkg-general/resources/resource-general/responses",
                params={"user_id": "ignored", "question_id": "q-single"},
            )
            assert restored.status_code == 200
            assert restored.json()["draft"]["answer_json"] == " B "
            assert restored.json()["submissions"] == []

            submitted = await client.post(
                "/api/v1/exercises/pkg-general/resources/resource-general/questions/q-single/submit",
                json={
                    "user_id": "ignored",
                    "session_id": "sess-general",
                    "answer_json": " b ",
                    "client_submission_id": "submit-single",
                },
            )
            assert submitted.status_code == 200
            body = submitted.json()
            assert body["correct"] is True
            assert body["score"] == 1.0
            assert body["user_id"] == "local-user"
            # Submissions are owner-private: they carry the canonical answer
            # and explanation so post-submit feedback survives a refresh.
            assert body["answer"] == "B"
            assert body["explanation"] == "B is the correct option."

            final_state = await client.get(
                "/api/v1/exercises/pkg-general/resources/resource-general/responses",
                params={"question_id": "q-single"},
            )
            assert final_state.status_code == 200
            assert final_state.json()["draft"] is None
            restored = final_state.json()["submissions"][0]
            assert restored["submission_id"] == body["submission_id"]
            assert restored["answer"] == "B"
            assert restored["explanation"] == "B is the correct option."

        events = await app.state.learning_workflow.event_store.query(
            "local-user", event_types=[EventType.EXERCISE_SCORED]
        )
        assert len(events) == 1
        assert events[0].event_id == f"exercise-response:{body['submission_id']}"
        assert events[0].score == 1.0
        assert events[0].concept_id == "selection"
        assert events[0].target_id == "q-single"
    finally:
        await _close_app(app)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question_id", "answer_json"),
    [
        ("q-single", " b "),
        ("q-multiple", [" c ", "A"]),
        ("q-boolean", " YES "),
        ("q-fill", "  hello   world "),
        ("q-short", " Dynamic   Programming "),
    ],
)
async def test_general_answer_types_are_normalized(
    tmp_path, question_id, answer_json
) -> None:
    app = await _ready_app(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/v1/exercises/pkg-general/resources/resource-general/questions/{question_id}/submit",
                json={
                    "session_id": "sess-general",
                    "answer_json": answer_json,
                    "client_submission_id": f"normalize-{question_id}",
                },
            )
        assert response.status_code == 200
        assert response.json()["correct"] is True
        assert response.json()["score"] == 1.0
    finally:
        await _close_app(app)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer_json", "expected"),
    [
        ([" Hello World "], True),
        (["hello world"], True),
        (["goodbye"], False),
        ([None], False),
    ],
)
async def test_fill_blank_single_accepts_ui_array_submissions(
    tmp_path, answer_json, expected
) -> None:
    """Single-blank arrays from the UI unwrap to the accepted-variants path."""
    app = await _ready_app(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/exercises/pkg-general/resources/resource-general/questions/q-fill/submit",
                json={
                    "session_id": "sess-general",
                    "answer_json": answer_json,
                    "client_submission_id": f"fill-single-{json.dumps(answer_json)}",
                },
            )
        assert response.status_code == 200
        assert response.json()["correct"] is expected
        assert response.json()["score"] == (1.0 if expected else 0.0)
    finally:
        await _close_app(app)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer_json", "expected"),
    [
        (["Paris", "Berlin"], True),
        (["parisian", " BERLIN "], True),
        (["paris", None], False),
        (["lyon", "berlin"], False),
    ],
)
async def test_fill_blank_multi_arrays_score_positionally(
    tmp_path, answer_json, expected
) -> None:
    """Multi-blank arrays score per position against per-slot variants."""
    app = await _ready_app(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/exercises/pkg-general/resources/resource-general/questions/q-fill-multi/submit",
                json={
                    "session_id": "sess-general",
                    "answer_json": answer_json,
                    "client_submission_id": f"fill-multi-{json.dumps(answer_json)}",
                },
            )
        assert response.status_code == 200
        assert response.json()["correct"] is expected
        assert response.json()["score"] == (1.0 if expected else 0.0)
    finally:
        await _close_app(app)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer_json",
    [
        ["paris", 42],
        ["paris", ["berlin"]],
        ["paris", {"text": "berlin"}],
        ["paris", "berlin", "extra"],
    ],
)
async def test_fill_blank_malformed_array_submissions_have_typed_errors(
    tmp_path, answer_json
) -> None:
    app = await _ready_app(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/exercises/pkg-general/resources/resource-general/questions/q-fill-multi/submit",
                json={"session_id": "sess", "answer_json": answer_json},
            )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "MALFORMED_ANSWER"
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_fill_blank_array_retry_is_idempotent_and_conflicts_on_change(
    tmp_path,
) -> None:
    app = await _ready_app(tmp_path)
    payload = {
        "session_id": "sess-general",
        "answer_json": [" Paris ", "Berlin"],
        "client_submission_id": "fill-multi-retry",
    }
    url = "/api/v1/exercises/pkg-general/resources/resource-general/questions/q-fill-multi/submit"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            first = await client.post(url, json=payload)
            retry = await client.post(url, json=payload)
            conflict = await client.post(
                url, json={**payload, "answer_json": ["paris", "bonn"]}
            )
        assert first.status_code == retry.status_code == 200
        assert first.json()["submission_id"] == retry.json()["submission_id"]
        assert first.json()["correct"] is True
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "SUBMISSION_ID_CONFLICT"
        events = await app.state.learning_workflow.event_store.query(
            "local-user", event_types=[EventType.EXERCISE_SCORED]
        )
        assert len(events) == 1
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_submission_carries_feedback_and_public_resource_stays_stripped(
    tmp_path,
) -> None:
    app = await _ready_app(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            submitted = await client.post(
                "/api/v1/exercises/pkg-general/resources/resource-general/questions/q-fill-multi/submit",
                json={
                    "session_id": "sess-general",
                    "answer_json": ["Paris", "Berlin"],
                    "client_submission_id": "feedback-fill-multi",
                },
            )
            assert submitted.status_code == 200
            body = submitted.json()
            assert body["answer"] == [["paris", "parisian"], "berlin"]
            assert body["explanation"] == "Paris and Berlin are the capitals."

            state = await client.get(
                "/api/v1/exercises/pkg-general/resources/resource-general/responses",
                params={"question_id": "q-fill-multi"},
            )
            assert state.status_code == 200
            restored = state.json()["submissions"][0]
            assert restored["answer"] == body["answer"]
            assert restored["explanation"] == body["explanation"]

            public = await client.get(
                "/api/v1/resources/packages/local-user/pkg-general"
            )
            assert public.status_code == 200
            questions = {
                item["id"]: item
                for item in public.json()["resources"][0]["format_specific"][
                    "questions"
                ]
            }
            assert "answer" not in questions["q-fill-multi"]
            assert "accepted_answers" not in questions["q-fill-multi"]
            assert "explanation" not in questions["q-fill-multi"]
            assert "parisian" not in public.text
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_general_wrong_retry_conflict_and_strict_request(tmp_path) -> None:
    app = await _ready_app(tmp_path)
    payload = {
        "session_id": "sess-general",
        "answer_json": "A",
        "client_submission_id": "retry-general",
    }
    url = "/api/v1/exercises/pkg-general/resources/resource-general/questions/q-single/submit"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            first = await client.post(url, json=payload)
            retry = await client.post(url, json=payload)
            conflict = await client.post(url, json={**payload, "answer_json": "B"})
            controlled = await client.post(
                url,
                json={
                    **payload,
                    "client_submission_id": "controlled",
                    "score": 1,
                    "correct": True,
                    "canonical_answer": "A",
                },
            )
        assert first.status_code == retry.status_code == 200
        assert first.json()["submission_id"] == retry.json()["submission_id"]
        assert first.json()["correct"] is False
        assert first.json()["score"] == 0.0
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "SUBMISSION_ID_CONFLICT"
        assert controlled.status_code == 422
        events = await app.state.learning_workflow.event_store.query(
            "local-user", event_types=[EventType.EXERCISE_SCORED]
        )
        assert len(events) == 1
    finally:
        await _close_app(app)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question_id", "answer_json"),
    [
        ("q-single", ["B"]),
        ("q-multiple", "A,C"),
        ("q-multiple", ["A", " a ", "C"]),
        ("q-boolean", "maybe"),
        ("q-fill", ["hello", 42]),
        ("q-short", {"text": "dynamic programming"}),
    ],
)
async def test_general_malformed_answers_have_typed_errors(
    tmp_path, question_id, answer_json
) -> None:
    app = await _ready_app(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/v1/exercises/pkg-general/resources/resource-general/questions/{question_id}/submit",
                json={"session_id": "sess", "answer_json": answer_json},
            )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "MALFORMED_ANSWER"
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_general_owner_isolation_and_resource_matching(tmp_path) -> None:
    app = await _ready_app(tmp_path, multi_user=True)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in (
                "/api/v1/exercises/pkg-general/resources/resource-general/questions/q-single/draft",
                "/api/v1/exercises/pkg-general/resources/missing/questions/q-single/draft",
                "/api/v1/exercises/missing/resources/resource-general/questions/q-single/draft",
            ):
                response = await client.put(
                    path,
                    json={"user_id": "other-user", "answer_json": "B"},
                )
                assert response.status_code == 404
                assert response.json()["detail"]["code"] == "EXERCISE_NOT_FOUND"
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_general_code_submission_links_existing_attempt_without_second_event(
    tmp_path,
) -> None:
    app = await _ready_app(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            attempt = await client.post(
                "/api/v1/exercises/pkg-code/q-code/attempts",
                json={
                    "session_id": "sess-code",
                    "source_code": "def add(a, b): return a + b",
                    "client_attempt_id": "code-link-attempt",
                },
            )
            assert attempt.status_code == 201
            linked = await client.post(
                "/api/v1/exercises/pkg-code/resources/resource-code/questions/q-code/submit",
                json={
                    "session_id": "sess-code",
                    "client_submission_id": "code-link-submission",
                    "linked_code_attempt_id": attempt.json()["attempt_id"],
                },
            )
        assert linked.status_code == 200
        assert linked.json()["linked_code_attempt_id"] == attempt.json()["attempt_id"]
        assert linked.json()["correct"] is True
        events = await app.state.learning_workflow.event_store.query(
            "local-user", event_types=[EventType.EXERCISE_SCORED]
        )
        assert len(events) == 1
        assert events[0].event_id.startswith("exercise-attempt:")
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_general_short_answer_without_explicit_accepted_answers_is_manual(
    tmp_path,
) -> None:
    app = await _ready_app(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            rewrite = await client.post(
                "/api/v1/exercises/pkg-general/resources/resource-general/questions/q-short-open/submit",
                json={
                    "session_id": "sess-general",
                    "answer_json": "A thoughtful rewrite in the learner's own words",
                    "client_submission_id": "manual-rewrite",
                },
            )
            unrelated = await client.post(
                "/api/v1/exercises/pkg-general/resources/resource-general/questions/q-short-legacy/submit",
                json={
                    "session_id": "sess-general",
                    "answer_json": "A valid but unrelated example",
                    "client_submission_id": "manual-unrelated",
                },
            )
        for response in (rewrite, unrelated):
            assert response.status_code == 200
            assert response.json()["grading_status"] == "manual_review"
            assert response.json()["correct"] is None
            assert response.json()["score"] is None
        assert await app.state.learning_workflow.event_store.query(
            "local-user", event_types=[EventType.EXERCISE_SCORED]
        ) == []
        assert await app.state.exercise_response_store.list_unpublished_page(
            after_row_id=0
        ) == []
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_idempotent_retry_survives_changed_then_deleted_canonical_resource(
    tmp_path, monkeypatch
) -> None:
    app = await _ready_app(tmp_path)
    event_store = app.state.learning_workflow.event_store
    original_append = event_store.append
    failures_remaining = 2

    async def fail_initial_publications(event):
        nonlocal failures_remaining
        if failures_remaining:
            failures_remaining -= 1
            raise RuntimeError("defer publication")
        return await original_append(event)

    monkeypatch.setattr(event_store, "append", fail_initial_publications)
    single_url = "/api/v1/exercises/pkg-general/resources/resource-general/questions/q-single/submit"
    fill_url = "/api/v1/exercises/pkg-general/resources/resource-general/questions/q-fill/submit"
    single_payload = {
        "session_id": "sess-general",
        "answer_json": "B",
        "client_submission_id": "resource-changed-retry",
    }
    fill_payload = {
        "session_id": "sess-general",
        "answer_json": "hello world",
        "client_submission_id": "resource-deleted-retry",
    }
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            first_single = await client.post(single_url, json=single_payload)
            first_fill = await client.post(fill_url, json=fill_payload)
            assert first_single.status_code == first_fill.status_code == 200

            changed = _general_package()
            changed_questions = changed.resources[0].format_specific["questions"]
            changed_questions[0]["answer"] = "A"
            changed_questions[0]["knowledge_point"] = "changed-concept"
            changed.topic = "changed-course"
            await app.state.resource_package_store.save(changed, user_id="local-user")

            retried_single = await client.post(single_url, json=single_payload)
            assert retried_single.status_code == 200
            assert retried_single.json()["submission_id"] == first_single.json()["submission_id"]
            assert retried_single.json()["correct"] is True

            assert await app.state.resource_package_store.delete("pkg-general")
            retried_fill = await client.post(fill_url, json=fill_payload)
            conflicting = await client.post(
                fill_url, json={**fill_payload, "answer_json": "different"}
            )
            assert retried_fill.status_code == 200
            assert retried_fill.json()["submission_id"] == first_fill.json()["submission_id"]
            assert conflicting.status_code == 409
            assert conflicting.json()["detail"]["code"] == "SUBMISSION_ID_CONFLICT"

        events = await event_store.query(
            "local-user", event_types=[EventType.EXERCISE_SCORED]
        )
        assert len(events) == 2
        by_target = {event.target_id: event for event in events}
        assert by_target["q-single"].concept_id == "selection"
        assert by_target["q-single"].course == "computer-science"
        assert by_target["q-fill"].score == 1.0
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_general_resource_api_hides_all_ordinary_canonical_answers(
    tmp_path,
) -> None:
    app = await _ready_app(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/v1/resources/packages/local-user/pkg-general"
            )
        assert response.status_code == 200
        questions = response.json()["resources"][0]["format_specific"]["questions"]
        assert {question["type"] for question in questions} >= {
            "single_choice",
            "multiple_choice",
            "true_false",
            "fill_blank",
            "short_answer",
        }
        assert all("answer" not in question for question in questions)
        assert all("accepted_answers" not in question for question in questions)
        assert "dynamic programming" not in response.text.casefold()
        assert "legacy model answer" not in response.text.casefold()
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_generated_short_answers_grade_closed_keep_open_manual_and_hide_secrets(
    tmp_path,
) -> None:
    generated = await ExerciseGeneratorAgent(
        llm=_static_exercise_llm(
            {
                "questions": [
                    {
                        "id": "generated-closed",
                        "tier": "advanced",
                        "type": "short_answer",
                        "difficulty": 3,
                        "knowledge_point": "dynamic-programming",
                        "question": "What does DP stand for?",
                        "answer": "SECRET_ANSWER_DYNAMIC_PROGRAMMING",
                        "accepted_answers": ["dynamic programming", "DP"],
                        "explanation": "SECRET_EXPLANATION_CLOSED",
                    },
                    {
                        "id": "generated-open",
                        "tier": "challenge",
                        "type": "short_answer",
                        "difficulty": 4,
                        "knowledge_point": "reflection",
                        "question": "请用自己的话解释动态规划的价值",
                        "answer": "(开放式回答)",
                        "accepted_answers": [],
                        "explanation": "SECRET_EXPLANATION_OPEN",
                    },
                    {
                        "id": "generated-choice",
                        "tier": "basic",
                        "type": "single_choice",
                        "difficulty": 1,
                        "question": "VISIBLE_GENERATED_PROMPT",
                        "options": [
                            {"label": "A", "text": "VISIBLE_GENERATED_OPTION"},
                            {"label": "B", "text": "other"},
                        ],
                        "answer": "A",
                        "explanation": "SECRET_EXPLANATION_CHOICE",
                    },
                ]
            }
        )
    ).process(UnifiedContext(), topic="generated-short-answers")
    generated.resource_id = "resource-generated-short"
    package = ResourcePackage(
        package_id="pkg-generated-short",
        topic="algorithms",
        resources=[generated],
        metadata={"user_id": "local-user"},
    )
    app = await _ready_app(tmp_path)
    await app.state.resource_package_store.save(package, user_id="local-user")
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            public = await client.get(
                "/api/v1/resources/packages/local-user/pkg-generated-short"
            )
            assert public.status_code == 200
            assert "SECRET_ANSWER" not in public.text
            assert "SECRET_EXPLANATION" not in public.text
            public_resource = public.json()["resources"][0]
            assert public_resource["content"] == ""
            public_questions = public_resource["format_specific"]["questions"]
            assert all("answer" not in item for item in public_questions)
            assert all("accepted_answers" not in item for item in public_questions)
            assert all("explanation" not in item for item in public_questions)
            choice = next(
                item for item in public_questions if item["id"] == "generated-choice"
            )
            assert choice["question"] == "VISIBLE_GENERATED_PROMPT"
            assert choice["options"][0]["text"] == "VISIBLE_GENERATED_OPTION"

            closed = await client.post(
                "/api/v1/exercises/pkg-generated-short/resources/resource-generated-short/questions/generated-closed/submit",
                json={
                    "session_id": "sess-generated",
                    "answer_json": " dp ",
                    "client_submission_id": "generated-closed-submit",
                },
            )
            opened = await client.post(
                "/api/v1/exercises/pkg-generated-short/resources/resource-generated-short/questions/generated-open/submit",
                json={
                    "session_id": "sess-generated",
                    "answer_json": "A learner-authored explanation",
                    "client_submission_id": "generated-open-submit",
                },
            )
        assert closed.status_code == opened.status_code == 200
        assert closed.json()["grading_status"] == "auto_graded"
        assert closed.json()["correct"] is True
        assert closed.json()["score"] == 1.0
        assert opened.json()["grading_status"] == "manual_review"
        assert opened.json()["correct"] is None
        assert opened.json()["score"] is None
        events = await app.state.learning_workflow.event_store.query(
            "local-user", event_types=[EventType.EXERCISE_SCORED]
        )
        assert [(event.target_id, event.score) for event in events] == [
            ("generated-closed", 1.0)
        ]
    finally:
        await _close_app(app)


@pytest.mark.asyncio
async def test_startup_repairs_general_submission_and_closes_response_store(
    tmp_path,
) -> None:
    app = create_app(Settings(env="test", data_dir=tmp_path))
    await app.state.exercise_response_store.init()
    saved = await app.state.exercise_response_store.save_submission(
        ExerciseSubmission(
            submission_id="general-crash-gap",
            user_id="local-user",
            session_id="sess-crash",
            package_id="pkg-general",
            resource_id="resource-general",
            question_id="q-single",
            question_type="single_choice",
            answer_json="B",
            correct=True,
            score=1.0,
            concept_id="selection",
            course="computer-science",
        )
    )
    assert saved.event_published is False
    await app.state.exercise_response_store.close()

    async with app.router.lifespan_context(app):
        persisted = await app.state.exercise_response_store.get_submission_for_user(
            "general-crash-gap", "local-user"
        )
        assert persisted is not None and persisted.event_published is True
        events = await app.state.learning_workflow.event_store.query(
            "local-user", event_types=[EventType.EXERCISE_SCORED]
        )
        assert [event.event_id for event in events] == [
            "exercise-response:general-crash-gap"
        ]

    assert app.state.exercise_response_store._engine is None
