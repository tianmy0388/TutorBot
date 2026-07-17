"""Stage 4 — Conversation persistence regression tests.

Pins the new ``/conversations`` endpoints:

  - create / list / detail / append / rename / delete
  - user isolation (403 across users)
  - message cascade on delete
  - auto-title from first user message
  - history survives an app restart (the persistence layer is
    SQLite, not in-memory)
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport
from tutor.api.main import create_app
from tutor.services.config.settings import reset_settings_cache
from tutor.services.conversations import reset_conversation_store


def _client(tmp_path, monkeypatch, *, multi_user_enabled: bool = True) -> httpx.AsyncClient:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TUTOR_MULTI_USER_ENABLED", str(multi_user_enabled).lower())
    reset_settings_cache()
    reset_conversation_store()
    app = create_app()
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


@pytest.mark.asyncio
async def test_local_mode_stale_browser_identity_reads_canonical_conversation(tmp_path, monkeypatch) -> None:
    async with _client(tmp_path, monkeypatch, multi_user_enabled=False) as client:
        created = await client.post(
            "/api/v1/conversations",
            json={"session_id": "sess_ebb5a8f5dfdb", "user_id": "u_old_owner"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["user_id"] == "local-user"

        response = await client.get("/api/v1/conversations/sess_ebb5a8f5dfdb?user_id=u_stale_browser")

        assert response.status_code == 200, response.text
        assert response.json()["user_id"] == "local-user"


@pytest.mark.asyncio
async def test_create_list_detail_append(tmp_path, monkeypatch) -> None:
    async with _client(tmp_path, monkeypatch) as client:
        # Create a session
        r = await client.post(
            "/api/v1/conversations",
            json={"user_id": "u1"},
        )
        assert r.status_code == 201, r.text
        conv = r.json()
        sid = conv["session_id"]
        assert conv["user_id"] == "u1"
        assert conv["message_count"] == 0

        # Append a user message
        r = await client.post(
            f"/api/v1/conversations/{sid}/messages?user_id=u1",
            json={"role": "user", "content": "解释 self-attention"},
        )
        assert r.status_code == 201, r.text
        msg = r.json()
        assert msg["role"] == "user"
        assert msg["content"] == "解释 self-attention"

        # Append an assistant message
        r = await client.post(
            f"/api/v1/conversations/{sid}/messages?user_id=u1",
            json={
                "role": "assistant",
                "content": "self-attention 计算 QKV。",
                "job_id": "job_abc",
            },
        )
        assert r.status_code == 201, r.text

        # List shows the session with auto-title
        r = await client.get(
            "/api/v1/conversations?user_id=u1",
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["session_id"] == sid
        assert body["items"][0]["title"] == "解释 self-attention"
        assert body["items"][0]["message_count"] == 2

        # Detail returns messages in order
        r = await client.get(
            f"/api/v1/conversations/{sid}?user_id=u1",
        )
        assert r.status_code == 200, r.text
        detail = r.json()
        assert len(detail["messages"]) == 2
        assert detail["messages"][0]["role"] == "user"
        assert detail["messages"][1]["role"] == "assistant"
        assert detail["messages"][1]["job_id"] == "job_abc"

        reset_conversation_store()


@pytest.mark.asyncio
async def test_user_isolation(tmp_path, monkeypatch) -> None:
    async with _client(tmp_path, monkeypatch) as client:
        r = await client.post(
            "/api/v1/conversations",
            json={"user_id": "alice"},
        )
        assert r.status_code == 201, r.text
        sid = r.json()["session_id"]

        # Bob cannot read Alice's session
        r = await client.get(
            f"/api/v1/conversations/{sid}?user_id=bob",
        )
        assert r.status_code == 403, r.text

        # Bob cannot append to Alice's session
        r = await client.post(
            f"/api/v1/conversations/{sid}/messages?user_id=bob",
            json={"role": "user", "content": "hi"},
        )
        assert r.status_code == 403, r.text

        # Bob cannot delete Alice's session
        r = await client.delete(
            f"/api/v1/conversations/{sid}?user_id=bob",
        )
        assert r.status_code == 403, r.text

        reset_conversation_store()


@pytest.mark.asyncio
async def test_rename_and_delete_cascades(tmp_path, monkeypatch) -> None:
    async with _client(tmp_path, monkeypatch) as client:
        r = await client.post(
            "/api/v1/conversations",
            json={"user_id": "u1", "title": "Old"},
        )
        sid = r.json()["session_id"]

        # Rename
        r = await client.patch(
            f"/api/v1/conversations/{sid}?user_id=u1",
            json={"title": "New Title"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "New Title"

        # Add a message
        r = await client.post(
            f"/api/v1/conversations/{sid}/messages?user_id=u1",
            json={"role": "user", "content": "hi"},
        )
        assert r.status_code == 201, r.text

        # Delete
        r = await client.delete(
            f"/api/v1/conversations/{sid}?user_id=u1",
        )
        assert r.status_code == 200, r.text

        # Detail is now 404
        r = await client.get(
            f"/api/v1/conversations/{sid}?user_id=u1",
        )
        assert r.status_code == 404, r.text

        reset_conversation_store()


@pytest.mark.asyncio
async def test_history_survives_app_restart(tmp_path, monkeypatch) -> None:
    """The plan's acceptance criterion: refreshing the browser (and
    restarting the backend) keeps the history visible.

    We simulate the restart by dropping the in-process store and
    re-creating the FastAPI app — both reads and writes must hit
    the on-disk SQLite file."""
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    reset_settings_cache()
    reset_conversation_store()

    # First "session": write a conversation.
    app1 = create_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app1), base_url="http://test") as client:
        r = await client.post(
            "/api/v1/conversations",
            json={"user_id": "u1"},
        )
        assert r.status_code == 201, r.text
        sid = r.json()["session_id"]
        r = await client.post(
            f"/api/v1/conversations/{sid}/messages?user_id=u1",
            json={"role": "user", "content": "Transformer attention"},
        )
        assert r.status_code == 201, r.text

    # Drop the singleton and rebuild a fresh app — this is the
    # equivalent of restarting the backend process.
    reset_conversation_store()
    app2 = create_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as client:
        r = await client.get(
            f"/api/v1/conversations/{sid}?user_id=u1",
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["session_id"] == sid
        assert body["message_count"] == 1
        assert body["messages"][0]["content"] == "Transformer attention"
        assert body["title"] == "Transformer attention"

    reset_conversation_store()
