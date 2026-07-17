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

from datetime import UTC

import httpx
import pytest
from httpx import ASGITransport
from tutor.api.main import create_app
from tutor.services.config.settings import reset_settings_cache
from tutor.services.conversations import reset_conversation_store


@pytest.mark.asyncio
async def test_aggregate_recovers_all_session_records_after_owner_migration(
    tmp_path, monkeypatch
) -> None:
    """Ownership is checked once at the conversation boundary.

    Jobs/packages imported from a legacy local identity still belong to the
    conversation by ``session_id`` and must not disappear after a refresh.
    """
    from datetime import datetime, timedelta

    from tutor.core.capability_result import FollowUpTaskSpec
    from tutor.services.jobs import Job, JobStatus, get_job_store, reset_job_store
    from tutor.services.jobs.follow_up import FollowUpScheduler
    from tutor.services.learner_profile import get_profile_store
    from tutor.services.resource_package import (
        Resource,
        ResourcePackage,
        ResourceType,
        get_resource_package_store,
        reset_resource_package_store,
    )

    reset_job_store()
    reset_resource_package_store()
    now = datetime.now(UTC)

    async with _client(tmp_path, monkeypatch) as client:
        created = await client.post(
            "/api/v1/conversations",
            json={"session_id": "session-recovery", "user_id": "owner"},
        )
        assert created.status_code == 201, created.text
        for content in ("first", "second"):
            response = await client.post(
                "/api/v1/conversations/session-recovery/messages?user_id=owner",
                json={"role": "user", "content": content},
            )
            assert response.status_code == 201, response.text

        job_store = get_job_store()
        await job_store.init()
        await job_store.save(
            Job(
                job_id="job-repaired",
                user_id="legacy-browser-owner",
                session_id="session-recovery",
                status=JobStatus.FAILED,
                error="process restarted while job was running",
                created_at=now,
                finished_at=now,
            )
        )
        child = (
            await FollowUpScheduler(job_store).enqueue(
                "job-repaired",
                (
                    FollowUpTaskSpec(
                        kind="video_render",
                        payload={
                            "package_id": "package-second",
                            "resource_id": "missing-resource",
                        },
                        dedupe_key="video:package-second:missing-resource",
                    ),
                ),
            )
        )[0]
        await job_store.set_terminal(
            child.job_id,
            status=JobStatus.FAILED,
            finished_at=now,
            result={
                "job_id": child.job_id,
                "capability": "video_render",
                "status": "failed",
                "assistant_message": "视频渲染失败",
            },
            terminal_event={"type": "job_terminal", "content": "failed"},
        )

        package_store = get_resource_package_store()
        await package_store.init()
        first = ResourcePackage(
            package_id="package-first",
            topic="first",
            created_at=now,
            resources=[Resource(type=ResourceType.DOCUMENT, title="first resource")],
            learning_path_summary={"path_id": "path-1", "current_index": 1},
            metadata={"session_id": "session-recovery"},
        )
        second = ResourcePackage(
            package_id="package-second",
            topic="second",
            created_at=now + timedelta(seconds=1),
            resources=[
                Resource(
                    resource_id="missing-resource",
                    type=ResourceType.CODE,
                    title="missing resource",
                    format_specific={
                        "artifacts": [
                            {
                                "name": "figure.png",
                                "artifact_key": "code_runs/missing/figure.png",
                                "kind": "png",
                            }
                        ]
                    },
                    metadata={
                        "recovery_contract": {
                            "job_id": "job-repaired",
                            "resource_types": ["code"],
                        }
                    },
                )
            ],
            metadata={"session_id": "session-recovery"},
        )
        await package_store.save(first, user_id="legacy-owner-a")
        await package_store.save(second, user_id="legacy-owner-b")
        await get_profile_store().init()

        response = await client.get(
            "/api/v1/conversations/session-recovery/aggregate?user_id=owner"
        )

        assert response.status_code == 200, response.text
        aggregate = response.json()
        assert [m["content"] for m in aggregate["conversation"]["messages"]] == [
            "first",
            "second",
        ]
        assert [j["job_id"] for j in aggregate["jobs"]] == ["job-repaired"]
        assert aggregate["jobs"][0]["background_status"] == "failed"
        assert aggregate["jobs"][0]["children"][0]["job_id"] == child.job_id
        assert aggregate["jobs"][0]["children"][0]["status"] == "failed"
        assert [p["package_id"] for p in aggregate["packages"]] == [
            "package-first",
            "package-second",
        ]
        assert aggregate["packages"][1]["resources"][0]["metadata"]["artifact_missing"] is True
        assert aggregate["profile_summary"]["user_id"] == "owner"
        assert aggregate["path_summary"] == {"path_id": "path-1", "current_index": 1}
        warning_codes = {warning["code"] for warning in aggregate["recovery_warnings"]}
        assert warning_codes == {
            "migrated_ownership",
            "interrupted_job_repaired",
            "missing_artifact",
        }

    reset_job_store()
    reset_resource_package_store()


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
async def test_aggregate_removes_legacy_absolute_paths_from_response(
    tmp_path, monkeypatch
) -> None:
    from sqlalchemy import select
    from tutor.services.jobs import Job, get_job_store, reset_job_store
    from tutor.services.jobs.schema import JobStatus
    from tutor.services.learner_profile import get_profile_store
    from tutor.services.resource_package import (
        Resource,
        ResourcePackage,
        ResourceType,
        get_resource_package_store,
        reset_resource_package_store,
    )
    from tutor.services.resource_package.store import ResourceRow

    reset_resource_package_store()
    reset_job_store()

    async with _client(tmp_path, monkeypatch) as client:
        response = await client.post(
            "/api/v1/conversations",
            json={"session_id": "legacy-path-session", "user_id": "owner"},
        )
        assert response.status_code == 201

        data_dir = tmp_path / "data"
        safe_path = data_dir / "legacy" / "figure.png"
        safe_path.parent.mkdir(parents=True)
        safe_path.write_bytes(b"png")
        outside_path = tmp_path / "private" / "secret.png"

        store = get_resource_package_store()
        await store.init()
        job_store = get_job_store()
        await job_store.init()
        await job_store.save(
            Job(
                job_id="succeeded-parent-job",
                user_id="owner",
                session_id="legacy-path-session",
                capability="resource_generation",
                status=JobStatus.SUCCEEDED,
                message="generate",
                language="zh",
                metadata={"selected_resource_types": ["code"]},
                result={
                    "job_id": "succeeded-parent-job",
                    "capability": "resource_generation",
                    "status": "succeeded",
                    "assistant_message": "done",
                    "artifacts": [{"resource_type": "code", "status": "succeeded"}],
                },
            )
        )
        package = ResourcePackage(
            package_id="legacy-path-package",
            topic="legacy",
            metadata={
                "session_id": "legacy-path-session",
                "job_id": "succeeded-parent-job",
            },
            resources=[
                Resource(
                    resource_id="safe-legacy-resource",
                    type=ResourceType.CODE,
                    title="safe",
                ),
                Resource(
                    resource_id="unsafe-legacy-resource",
                    type=ResourceType.CODE,
                    title="unsafe",
                ),
            ],
        )
        await store.save(package, user_id="owner")
        async with store._with_session() as session:  # noqa: SLF001
            safe_row = (
                await session.execute(
                    select(ResourceRow).where(
                        ResourceRow.resource_id == "safe-legacy-resource"
                    )
                )
            ).scalar_one()
            safe_row.format_specific = {
                "artifacts": [
                    {"name": "figure.png", "path": str(safe_path), "kind": "png"}
                ],
                "mp4_path": str(safe_path),
            }
            unsafe_row = (
                await session.execute(
                    select(ResourceRow).where(
                        ResourceRow.resource_id == "unsafe-legacy-resource"
                    )
                )
            ).scalar_one()
            unsafe_row.format_specific = {
                "artifacts": [
                    {"name": "secret.png", "path": str(outside_path), "kind": "png"}
                ],
                "pptx_path": str(outside_path),
            }
        await get_profile_store().init()

        response = await client.get(
            "/api/v1/conversations/legacy-path-session/aggregate?user_id=owner"
        )

        assert response.status_code == 200, response.text
        body = response.json()
        serialized = response.text
        assert str(safe_path) not in serialized
        assert str(outside_path) not in serialized
        by_id = {
            resource["resource_id"]: resource
            for resource in body["packages"][0]["resources"]
        }
        safe_fs = by_id["safe-legacy-resource"]["format_specific"]
        assert safe_fs["artifacts"][0]["artifact_key"] == "legacy/figure.png"
        assert safe_fs["artifact_key"] == "legacy/figure.png"
        assert "path" not in safe_fs["artifacts"][0]
        assert "mp4_path" not in safe_fs
        unsafe_fs = by_id["unsafe-legacy-resource"]["format_specific"]
        assert "pptx_path" not in unsafe_fs
        assert "path" not in unsafe_fs["artifacts"][0]
        assert any(
            warning["code"] == "missing_artifact"
            and warning["resource_id"] == "unsafe-legacy-resource"
            and warning["artifact_key"] is None
            for warning in body["recovery_warnings"]
        )
        assert safe_fs["artifacts"][0]["artifact_key"] == "legacy/figure.png"
        unsafe_metadata = by_id["unsafe-legacy-resource"]["metadata"]
        assert unsafe_metadata["recovery_contract"] == {
            "job_id": "succeeded-parent-job",
            "resource_types": ["code"],
        }

    reset_resource_package_store()
    reset_job_store()


@pytest.mark.asyncio
async def test_aggregate_uses_exact_package_job_and_omits_ambiguous_legacy_retry(
    tmp_path, monkeypatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from tutor.services.jobs import Job, JobStatus, get_job_store, reset_job_store
    from tutor.services.learner_profile import get_profile_store
    from tutor.services.resource_package import (
        Resource,
        ResourcePackage,
        ResourceType,
        get_resource_package_store,
        reset_resource_package_store,
    )

    reset_job_store()
    reset_resource_package_store()
    async with _client(tmp_path, monkeypatch) as client:
        created = await client.post(
            "/api/v1/conversations",
            json={"session_id": "multi-job-session", "user_id": "owner"},
        )
        assert created.status_code == 201
        now = datetime.now(UTC)
        job_store = get_job_store()
        await job_store.init()
        for index, job_id in enumerate(("job-first", "job-second")):
            await job_store.save(
                Job(
                    job_id=job_id,
                    user_id="owner",
                    session_id="multi-job-session",
                    capability="resource_generation",
                    status=JobStatus.SUCCEEDED,
                    created_at=now + timedelta(seconds=index),
                )
            )

        package_store = get_resource_package_store()
        await package_store.init()
        packages = (
            ResourcePackage(
                package_id="package-first",
                topic="first",
                resources=[
                    Resource(
                        resource_id="resource-first",
                        type=ResourceType.CODE,
                        title="first",
                        format_specific={"artifact_key": "missing/first.py"},
                    )
                ],
                metadata={"session_id": "multi-job-session", "job_id": "job-first"},
            ),
            ResourcePackage(
                package_id="package-second",
                topic="second",
                resources=[
                    Resource(
                        resource_id="resource-second",
                        type=ResourceType.VIDEO,
                        title="second",
                        format_specific={"artifact_key": "missing/second.mp4"},
                    )
                ],
                metadata={"session_id": "multi-job-session", "job_id": "job-second"},
            ),
            ResourcePackage(
                package_id="package-legacy",
                topic="legacy",
                resources=[
                    Resource(
                        resource_id="resource-legacy",
                        type=ResourceType.PPT,
                        title="legacy",
                        format_specific={"artifact_key": "missing/legacy.pptx"},
                    )
                ],
                metadata={"session_id": "multi-job-session"},
            ),
        )
        for package in packages:
            await package_store.save(package, user_id="owner")
        await get_profile_store().init()

        response = await client.get(
            "/api/v1/conversations/multi-job-session/aggregate?user_id=owner"
        )

        assert response.status_code == 200, response.text
        resources = {
            resource["resource_id"]: resource
            for package in response.json()["packages"]
            for resource in package["resources"]
        }
        assert resources["resource-first"]["metadata"]["recovery_contract"] == {
            "job_id": "job-first",
            "resource_types": ["code"],
        }
        assert resources["resource-second"]["metadata"]["recovery_contract"] == {
            "job_id": "job-second",
            "resource_types": ["video"],
        }
        assert "recovery_contract" not in resources["resource-legacy"]["metadata"]
        assert any(
            warning["code"] == "recovery_association_missing"
            and warning["package_id"] == "package-legacy"
            for warning in response.json()["recovery_warnings"]
        )

    reset_job_store()
    reset_resource_package_store()


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
