from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from tutor.api.main import create_app
from tutor.services.config.settings import reset_settings_cache
from tutor.services.learner_profile.store import _close_profile_store_sync
from tutor.services.learning_events.store import reset_learning_event_store
from tutor.services.resource_package.store import reset_resource_package_store


def _client(tmp_path, monkeypatch) -> httpx.AsyncClient:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TUTOR_EMBED_API_KEY", "")
    monkeypatch.setenv("TUTOR_EMBED_PROVIDER", "openai")
    reset_settings_cache()
    reset_resource_package_store()
    reset_learning_event_store()
    _close_profile_store_sync()
    app = create_app()
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


@pytest.mark.asyncio
async def test_demo_scenario_list_and_load_persists_evidence(tmp_path, monkeypatch) -> None:
    async with _client(tmp_path, monkeypatch) as client:
        listing = await client.get("/api/v1/demo/scenarios")
        assert listing.status_code == 200, listing.text
        items = listing.json()["items"]
        assert any(item["id"] == "ai_intro_competition" for item in items)

        loaded = await client.post(
            "/api/v1/demo/scenarios/ai_intro_competition/load",
            json={
                "user_id": "u-demo",
                "session_id": "s-demo",
                "persist": True,
            },
        )
        assert loaded.status_code == 200, loaded.text
        body = loaded.json()
        assert body["user_id"] == "u-demo"
        assert body["session_id"] == "s-demo"
        assert body["profile"]["user_id"] == "u-demo"
        assert body["package"]["metadata"]["demo_scenario_id"] == "ai_intro_competition"
        assert len(body["agent_trace"]) >= 5
        assert len(body["learning_loop"]) >= 6
        assert body["teacher_panel"]["risk_level"] == "medium"
        assert body["package"]["resources"][0]["citations"]
        assert body["package"]["resources"][0]["review"]["verdict"] == "pass"
        assert body["package"]["resources"][0]["safety"]["verdict"] == "safe"
        assert any("Embedding API key" in msg for msg in body["runtime_warnings"])

        package_id = body["package"]["package_id"]
        persisted = await client.get(f"/api/v1/resources/packages/u-demo/{package_id}")
        assert persisted.status_code == 200, persisted.text
        persisted_body = persisted.json()
        resource = persisted_body["resources"][0]
        assert resource["citations"][0]["title"]
        assert resource["review"]["quality_score"] >= 0.8
        assert resource["safety"]["risk_level"] == "low"


@pytest.mark.asyncio
async def test_demo_scenario_missing_returns_404(tmp_path, monkeypatch) -> None:
    async with _client(tmp_path, monkeypatch) as client:
        response = await client.post("/api/v1/demo/scenarios/missing/load", json={})
        assert response.status_code == 404
