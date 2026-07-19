from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import httpx
import pytest
from httpx import ASGITransport
from tutor.api.main import create_app
from tutor.services.config.settings import reset_settings_cache
from tutor.services.conversations import (
    ConversationStore,
    get_conversation_store,
    reset_conversation_store,
)


def _client(tmp_path, monkeypatch) -> httpx.AsyncClient:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TUTOR_MULTI_USER_ENABLED", "true")
    reset_settings_cache()
    reset_conversation_store()
    return httpx.AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
    )


@pytest.mark.asyncio
async def test_settings_patch_persists_across_all_projections_and_restart(
    tmp_path, monkeypatch
) -> None:
    async with _client(tmp_path, monkeypatch) as client:
        created = await client.post(
            "/api/v1/conversations",
            json={"session_id": "search-session", "user_id": "owner"},
        )
        assert created.status_code == 201
        assert created.json()["web_search_enabled"] is False

        enabled = await client.patch(
            "/api/v1/conversations/search-session/settings?user_id=owner",
            json={"web_search_enabled": True},
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["web_search_enabled"] is True

        detail = await client.get(
            "/api/v1/conversations/search-session?user_id=owner"
        )
        listing = await client.get("/api/v1/conversations?user_id=owner")
        from tutor.services.jobs import get_job_store
        from tutor.services.learner_profile import get_profile_store
        from tutor.services.resource_package import get_resource_package_store

        await get_job_store().init()
        await get_resource_package_store().init()
        await get_profile_store().init()
        aggregate = await client.get(
            "/api/v1/conversations/search-session/aggregate?user_id=owner"
        )
        assert detail.json()["web_search_enabled"] is True
        assert listing.json()["items"][0]["web_search_enabled"] is True
        assert aggregate.json()["conversation"]["web_search_enabled"] is True

        missing = await client.patch(
            "/api/v1/conversations/missing-session/settings?user_id=owner",
            json={"web_search_enabled": True},
        )
        assert missing.status_code == 404

        forbidden = await client.patch(
            "/api/v1/conversations/search-session/settings?user_id=intruder",
            json={"web_search_enabled": False},
        )
        assert forbidden.status_code == 403
        extra = await client.patch(
            "/api/v1/conversations/search-session/settings?user_id=owner",
            json={"web_search_enabled": False, "title": "smuggled"},
        )
        assert extra.status_code == 422

        disabled = await client.patch(
            "/api/v1/conversations/search-session/settings?user_id=owner",
            json={"web_search_enabled": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["web_search_enabled"] is False

        reenabled = await client.patch(
            "/api/v1/conversations/search-session/settings?user_id=owner",
            json={"web_search_enabled": True},
        )
        assert reenabled.status_code == 200
        assert reenabled.json()["web_search_enabled"] is True

    store = get_conversation_store()
    await store.close()
    reset_conversation_store()
    reopened = get_conversation_store()
    restored = await reopened.get("search-session")
    assert restored is not None
    assert restored.web_search_enabled is True
    await reopened.close()


@pytest.mark.asyncio
async def test_create_atomically_persists_initial_web_search_setting(
    tmp_path, monkeypatch
) -> None:
    async with _client(tmp_path, monkeypatch) as client:
        defaulted = await client.post(
            "/api/v1/conversations",
            json={"session_id": "default-off", "user_id": "owner"},
        )
        opted_in = await client.post(
            "/api/v1/conversations",
            json={
                "session_id": "atomic-opt-in",
                "user_id": "owner",
                "web_search_enabled": True,
            },
        )

        assert defaulted.status_code == 201, defaulted.text
        assert defaulted.json()["web_search_enabled"] is False
        assert opted_in.status_code == 201, opted_in.text
        assert opted_in.json()["web_search_enabled"] is True

        # POST remains idempotent: a stale client cannot overwrite an
        # already materialized conversation through the create contract.
        existing = await client.post(
            "/api/v1/conversations",
            json={
                "session_id": "default-off",
                "user_id": "owner",
                "web_search_enabled": True,
            },
        )
        assert existing.status_code == 201
        assert existing.json()["web_search_enabled"] is False

    store = get_conversation_store()
    await store.close()
    reset_conversation_store()
    reopened = get_conversation_store()
    restored = await reopened.get("atomic-opt-in")
    assert restored is not None
    assert restored.web_search_enabled is True
    await reopened.close()


@pytest.mark.asyncio
async def test_legacy_sqlite_conversation_table_is_migrated_in_place(tmp_path) -> None:
    db_path = tmp_path / "legacy-conversations.db"
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE conversations (
                session_id VARCHAR(64) PRIMARY KEY,
                user_id VARCHAR(64) NOT NULL,
                title VARCHAR(200) NOT NULL DEFAULT '',
                message_count INTEGER NOT NULL DEFAULT 0,
                last_message_preview VARCHAR(280) NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE messages (
                id VARCHAR(64) PRIMARY KEY,
                session_id VARCHAR(64) NOT NULL,
                role VARCHAR(16) NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                job_id VARCHAR(64),
                capability VARCHAR(64),
                created_at DATETIME NOT NULL,
                msg_metadata JSON NOT NULL DEFAULT '{}'
            );
            """
        )
        connection.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("legacy", "owner", "old", 1, "hello", now, now),
        )
        connection.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("message-1", "legacy", "user", "hello", None, None, now, "{}"),
        )

    store = ConversationStore(db_path=str(db_path))
    await store.init()
    detail = await store.get_conversation_with_messages("legacy")
    assert detail is not None
    assert detail.web_search_enabled is False
    assert [message.content for message in detail.messages] == ["hello"]
    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(conversations)")
        }
    assert "web_search_enabled" in columns
    await store.close()
