"""Conversation delete cascade (2026-07-19 learning-experience plan).

DELETE /conversations/{session_id} removes the conversation row, its
messages (pre-existing behavior), the session's resource packages, and
the session's job rows. The response reports the actual counts. Disk
artifacts are intentionally kept (out of scope).
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport
from tutor.api.main import create_app
from tutor.services.config.settings import reset_settings_cache
from tutor.services.conversations import reset_conversation_store
from tutor.services.jobs import Job, get_job_store, reset_job_store
from tutor.services.resource_package import (
    Resource,
    ResourcePackage,
    ResourceType,
    get_resource_package_store,
    reset_resource_package_store,
)


def _client(tmp_path, monkeypatch) -> httpx.AsyncClient:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    reset_settings_cache()
    reset_conversation_store()
    reset_job_store()
    reset_resource_package_store()
    app = create_app()
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


async def _seed_session_records(session_id: str, user_id: str) -> None:
    job_store = get_job_store()
    await job_store.init()
    await job_store.save(
        Job(user_id=user_id, session_id=session_id, capability="tutoring")
    )
    await job_store.save(
        Job(
            user_id=user_id,
            session_id=session_id,
            capability="video_render",
            parent_job_id="parent-1",
        )
    )
    pkg_store = get_resource_package_store()
    await pkg_store.init()
    await pkg_store.save(
        ResourcePackage(
            package_id="pkg-cascade",
            topic="cascade",
            resources=[Resource(type=ResourceType.DOCUMENT, title="doc")],
            metadata={"session_id": session_id},
        ),
        user_id=user_id,
    )


@pytest.mark.asyncio
async def test_delete_cascades_packages_and_jobs(tmp_path, monkeypatch) -> None:
    async with _client(tmp_path, monkeypatch) as client:
        created = await client.post(
            "/api/v1/conversations",
            json={"session_id": "sess-cascade", "user_id": "u1"},
        )
        assert created.status_code == 201, created.text
        appended = await client.post(
            "/api/v1/conversations/sess-cascade/messages?user_id=u1",
            json={"role": "user", "content": "hi"},
        )
        assert appended.status_code == 201, appended.text
        await _seed_session_records("sess-cascade", "u1")

        response = await client.delete(
            "/api/v1/conversations/sess-cascade?user_id=u1"
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body == {
            "deleted": True,
            "session_id": "sess-cascade",
            "packages_deleted": 1,
            "jobs_deleted": 2,
        }

        detail = await client.get("/api/v1/conversations/sess-cascade?user_id=u1")
        assert detail.status_code == 404
        assert await get_job_store().list_for_session("sess-cascade") == []
        assert (
            await get_resource_package_store().list_for_session("sess-cascade")
            == []
        )


@pytest.mark.asyncio
async def test_delete_empty_session_reports_zero_counts(
    tmp_path, monkeypatch
) -> None:
    async with _client(tmp_path, monkeypatch) as client:
        created = await client.post(
            "/api/v1/conversations",
            json={"session_id": "sess-empty", "user_id": "u1"},
        )
        assert created.status_code == 201, created.text

        response = await client.delete("/api/v1/conversations/sess-empty?user_id=u1")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "deleted": True,
            "session_id": "sess-empty",
            "packages_deleted": 0,
            "jobs_deleted": 0,
        }
