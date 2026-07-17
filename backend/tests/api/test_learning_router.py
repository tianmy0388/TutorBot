from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from tutor.api.routers.learning import router
from tutor.services.jobs.store import JobStore
from tutor.services.learner_profile.schema import LearnerProfile
from tutor.services.learner_profile.store import ProfileStore
from tutor.services.learning_events.store import LearningEventStore
from tutor.services.learning_events.workflow import LearningWorkflow


@pytest.fixture
async def client(tmp_path):
    events = LearningEventStore(tmp_path / "events.db")
    profiles = ProfileStore(tmp_path / "profiles.db")
    jobs = JobStore(tmp_path / "jobs.db")
    await events.init()
    await profiles.init()
    await jobs.init()
    app = FastAPI()
    app.state.settings = SimpleNamespace(multi_user_enabled=False)
    app.state.learning_workflow = LearningWorkflow(
        event_store=events, profile_store=profiles, job_store=jobs
    )
    app.state.learning_runner = None
    app.include_router(router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        yield api, events, profiles, jobs
    await events.close()
    await profiles.close()
    await jobs.close()


@pytest.mark.asyncio
async def test_post_canonicalizes_local_identity_and_returns_202(client):
    api, events, _, _ = client
    response = await api.post(
        "/api/learning/events",
        json={
            "event_id": "evt-local",
            "user_id": "browser-random",
            "session_id": "sess-loop",
            "event_type": "exercise_scored",
            "concept_id": "attention",
            "score": 0.7,
        },
    )

    assert response.status_code == 202
    assert response.json()["user_id"] == "local-user"
    assert (await events.query("local-user"))[0].event_id == "evt-local"
    assert await events.query("browser-random") == []


@pytest.mark.asyncio
async def test_duplicate_is_accepted_but_conflicting_event_id_is_409(client):
    api, _, _, _ = client
    payload = {
        "event_id": "evt-repeat",
        "user_id": "local-user",
        "event_type": "exercise_scored",
        "concept_id": "attention",
        "score": 0.6,
    }
    first = await api.post("/api/learning/events", json=payload)
    retry = await api.post("/api/learning/events", json=payload)
    conflict = await api.post(
        "/api/learning/events", json={**payload, "score": 0.9}
    )

    assert first.status_code == retry.status_code == 202
    assert retry.json()["inserted"] is False
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "LEARNING_EVENT_CONFLICT"
    assert "database" not in str(conflict.json()).lower()


@pytest.mark.asyncio
async def test_empty_gets_are_404_without_fabricating_profile(client):
    api, _, profiles, _ = client
    profile = await api.get("/api/learning/profile/local-user")
    path = await api.get("/api/learning/path/local-user")

    assert profile.status_code == 404
    assert profile.json()["detail"]["code"] == "LEARNING_PROFILE_NOT_FOUND"
    assert path.status_code == 404
    assert path.json()["detail"]["code"] == "LEARNING_PATH_NOT_FOUND"
    assert await profiles.list_users() == []


@pytest.mark.asyncio
async def test_profile_get_projects_frontend_shape(client):
    api, _, profiles, _ = client
    profile = LearnerProfile(user_id="local-user", version=2, event_watermark=5)
    profile.knowledge_map.set("attention", 0.72)
    await profiles.replace(profile, source="test")

    response = await api.get("/api/learning/profile/ignored-browser-user")

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "local-user"
    assert body["knowledge_map"] == {"attention": 0.72}
    assert body["pace"]["preferred_chunk_size_min"] == 15
    assert body["event_watermark"] == 5


@pytest.mark.asyncio
async def test_request_validation_rejects_bad_scored_evidence(client):
    api, _, _, _ = client
    response = await api.post(
        "/api/learning/events",
        json={
            "event_type": "exercise_scored",
            "concept_id": "",
            "score": 1.2,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_multi_user_mode_requires_identity_and_rejects_cross_user_event_id(client):
    api, _, _, _ = client
    api._transport.app.state.settings = SimpleNamespace(multi_user_enabled=True)
    missing = await api.post(
        "/api/learning/events",
        json={"event_type": "resource_viewed"},
    )
    payload = {
        "event_id": "shared-event-id",
        "user_id": "alice",
        "event_type": "exercise_scored",
        "concept_id": "attention",
        "score": 0.5,
    }
    alice = await api.post("/api/learning/events", json=payload)
    bob = await api.post(
        "/api/learning/events", json={**payload, "user_id": "bob"}
    )

    assert missing.status_code == 400
    assert missing.json()["detail"]["code"] == "IDENTITY_REQUIRED"
    assert alice.status_code == 202
    assert bob.status_code == 409


@pytest.mark.asyncio
async def test_course_changes_are_part_of_event_id_conflict(client):
    api, _, _, _ = client
    payload = {
        "event_id": "course-sensitive-event",
        "event_type": "exercise_scored",
        "concept_id": "attention",
        "score": 0.7,
        "course": "course-a",
    }

    first = await api.post("/api/learning/events", json=payload)
    conflict = await api.post(
        "/api/learning/events", json={**payload, "course": "course-b"}
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "LEARNING_EVENT_CONFLICT"


@pytest.mark.asyncio
async def test_reconcile_all_recovers_course_from_durable_event(
    client,
    monkeypatch,
):
    api, events, profiles, jobs = client
    workflow = api._transport.app.state.learning_workflow

    async def crash_after_event(*args, **kwargs):
        raise RuntimeError("private database crash detail")

    monkeypatch.setattr(workflow, "reconcile_user", crash_after_event)
    for index, course in enumerate(
        ("course-old", "", "course-middle", "", "course-recovered")
    ):
        response = await api.post(
            "/api/learning/events",
            json={
                "event_id": f"course-crash-event-{index}",
                "event_type": "exercise_scored",
                "concept_id": "attention",
                "score": 0.8,
                "course": course,
            },
        )
        assert response.status_code == 202
    persisted = await events.query("local-user")
    assert persisted[0].to_dict()["course"] == "course-recovered"

    repaired = LearningWorkflow(
        event_store=events,
        profile_store=profiles,
        job_store=jobs,
    )
    assert await repaired.reconcile_all() == 1
    root = await jobs.get(repaired.root_job_id("local-user"))
    child = (await jobs.get_children(root.job_id))[0]
    assert child.metadata["course"] == "course-recovered"


@pytest.mark.asyncio
async def test_post_store_failure_is_stable_and_redacted(client, monkeypatch):
    api, events, _, _ = client

    async def fail_append(event):
        raise RuntimeError("sqlite C:/private/learning.db secret-token")

    monkeypatch.setattr(events, "append", fail_append)
    response = await api.post(
        "/api/learning/events",
        json={"event_type": "resource_viewed"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "LEARNING_EVENT_STORE_UNAVAILABLE",
        "message": "Learning event service is unavailable",
    }
    assert "private" not in response.text.lower()
    assert "secret-token" not in response.text


@pytest.mark.asyncio
async def test_profile_and_path_store_failures_are_stable_and_redacted(
    client,
    monkeypatch,
):
    api, _, profiles, _ = client

    async def fail_read(*args, **kwargs):
        raise RuntimeError("sqlite C:/private/profile.db secret-token")

    monkeypatch.setattr(profiles, "get", fail_read)
    profile = await api.get("/api/learning/profile/local-user")
    monkeypatch.setattr(profiles, "get_latest_path", fail_read)
    path = await api.get("/api/learning/path/local-user")

    assert profile.status_code == path.status_code == 503
    assert profile.json()["detail"]["code"] == "LEARNING_PROFILE_UNAVAILABLE"
    assert path.json()["detail"]["code"] == "LEARNING_PATH_UNAVAILABLE"
    assert "private" not in profile.text.lower() + path.text.lower()
    assert "secret-token" not in profile.text + path.text
