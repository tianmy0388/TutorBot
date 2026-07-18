from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from tutor.api.main import create_app


@pytest.mark.asyncio
async def test_profile_endpoint_returns_frontend_detail_shape() -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/profile/student-profile")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_id"] == "student-profile"
    assert isinstance(body["knowledge_map"], dict)
    assert set(body["modality"]) >= {"text", "video", "exercise"}
    assert "pace" in body
    assert "motivation" in body
    assert "error_patterns" in body
    assert "metadata" in body


@pytest.mark.asyncio
async def test_attempt_updates_mastery_but_completion_does_not_score_twice() -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
    ) as client:
        attempted = await client.post(
            "/api/v1/learning-events",
            json={
                "user_id": "student-practice",
                "event_type": "exercise_attempted",
                "target_id": "exercise-1",
                "concept_id": "chain_rule",
                "correct": True,
                "metadata": {"difficulty": 3},
            },
        )
        after_attempt = await client.get("/api/v1/profile/student-practice")
        completed = await client.post(
            "/api/v1/learning-events",
            json={
                "user_id": "student-practice",
                "event_type": "exercise_completed",
                "target_id": "exercise-1",
                "concept_id": "chain_rule",
                "correct": True,
                "metadata": {"difficulty": 3},
            },
        )
        after_completion = await client.get("/api/v1/profile/student-practice")

    assert attempted.status_code == 200, attempted.text
    assert completed.status_code == 200, completed.text
    attempted_mastery = after_attempt.json()["knowledge_map"]["chain_rule"]
    assert attempted_mastery > 0
    assert after_completion.json()["knowledge_map"]["chain_rule"] == attempted_mastery
    assert "profile_version" in attempted.json()
    assert "profile_version" not in completed.json()


@pytest.mark.asyncio
async def test_removed_demo_endpoints_return_404() -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
    ) as client:
        listing = await client.get("/api/v1/demo/scenarios")
        loading = await client.post("/api/v1/demo/scenarios/example/load", json={})

    assert listing.status_code == 404
    assert loading.status_code == 404
