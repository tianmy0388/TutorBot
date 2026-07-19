from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from tutor.api.main import create_app
from tutor.services.config.settings import reset_settings_cache
from tutor.services.courses import get_course_service, reset_course_store, seed_default_courses
from tutor.services.knowledge_base import KnowledgeBaseService, seed_default_libraries
from tutor.services.knowledge_base.store import reset_kb_store
from tutor.services.learner_profile.store import _close_profile_store_sync
from tutor.services.learning_events.store import reset_learning_event_store


def _client(tmp_path, monkeypatch) -> httpx.AsyncClient:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TUTOR_EMBED_PROVIDER", "local")
    monkeypatch.setenv("TUTOR_EMBED_MODEL", "local-hash-v1")
    monkeypatch.setenv("TUTOR_EMBED_DIMENSIONS", "384")
    reset_settings_cache()
    reset_learning_event_store()
    reset_kb_store()
    reset_course_store()
    _close_profile_store_sync()
    seed_default_libraries(KnowledgeBaseService())
    seed_default_courses(get_course_service())
    return httpx.AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
    )


@pytest.mark.asyncio
async def test_learning_event_record_and_stats(tmp_path, monkeypatch) -> None:
    async with _client(tmp_path, monkeypatch) as client:
        created = await client.post(
            "/api/v1/learning-events",
            json={
                "user_id": "u-teacher",
                "event_type": "exercise_attempted",
                "target_id": "cn_tcp_exercise",
                "concept_id": "transport_tcp",
                "score": 0.5,
                "correct": False,
                "metadata": {
                    "course": "computer_network",
                    "resource_type": "exercise",
                },
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["event_type"] == "exercise_attempted"
        assert body["score"] == 0.5

        stats = await client.get("/api/v1/learning-events/u-teacher/stats")
        assert stats.status_code == 200, stats.text
        assert stats.json()["event_count"] == 1
        assert stats.json()["exercise_score_avg"] == 0.5


@pytest.mark.asyncio
async def test_teacher_course_analytics_accepts_graph_id_and_course_id(
    tmp_path,
    monkeypatch,
) -> None:
    async with _client(tmp_path, monkeypatch) as client:
        events = [
            {
                "user_id": "alice",
                "event_type": "resource_viewed",
                "target_id": "cn_tcp_doc",
                "concept_id": "transport_tcp",
                "duration_seconds": 120,
                "metadata": {
                    "course": "computer_network",
                    "resource_type": "document",
                },
            },
            {
                "user_id": "alice",
                "event_type": "resource_completed",
                "target_id": "cn_tcp_doc",
                "concept_id": "transport_tcp",
                "duration_seconds": 100,
                "metadata": {
                    "course": "computer_network",
                    "resource_type": "document",
                },
            },
            {
                "user_id": "bob",
                "event_type": "exercise_attempted",
                "target_id": "cn_tcp_exercise",
                "concept_id": "transport_tcp",
                "score": 0.4,
                "correct": False,
                "metadata": {
                    "course": "computer_network",
                    "resource_type": "exercise",
                },
            },
        ]
        for event in events:
            response = await client.post("/api/v1/learning-events", json=event)
            assert response.status_code == 200, response.text

        by_graph = await client.get(
            "/api/v1/teacher/courses/computer_network/analytics"
        )
        assert by_graph.status_code == 200, by_graph.text
        payload = by_graph.json()
        assert payload["course"]["knowledge_graph_id"] == "computer_network"
        assert payload["active_users"] == 2
        assert payload["event_count"] == 3
        assert payload["weak_concepts"][0]["concept"] == "transport_tcp"
        assert payload["recommendations"]

        by_course = await client.get(
            "/api/v1/teacher/courses/course_computer_network/analytics"
        )
        assert by_course.status_code == 200, by_course.text
        assert by_course.json()["active_users"] == 2
