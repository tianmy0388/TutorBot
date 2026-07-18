from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from tutor.core.capability_result import CapabilityResult
from tutor.services.jobs import Job, JobStatus, JobSubmit
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.store import JobStore


class _Capabilities:
    def get(self, name: str):
        return object() if name == "tutoring" else None


class _CapturingCapability:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.snapshot: bool | None = None

    async def run(self, context, stream):
        self.snapshot = context.web_search_enabled
        self.started.set()
        await self.release.wait()
        return CapabilityResult(assistant_message="done")


@pytest.mark.asyncio
async def test_submit_snapshots_server_setting_and_ignores_metadata_override(
    tmp_path, monkeypatch
) -> None:
    state = {"enabled": False}

    async def lookup(session_id: str):
        assert session_id == "conversation-1"
        return SimpleNamespace(user_id="owner", web_search_enabled=state["enabled"])

    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    runner = JobRunner(
        job_store=store,
        capability_registry=_Capabilities(),  # type: ignore[arg-type]
        conversation_lookup=lookup,
    )
    monkeypatch.setattr(runner, "_schedule", lambda _job: None)

    first = await runner.submit(
        JobSubmit(
            user_id="owner",
            session_id="conversation-1",
            capability="tutoring",
            metadata={
                "web_search_enabled": True,
                "web_search_requested": True,
                "display": "kept",
            },
        )
    )
    state["enabled"] = True
    second = await runner.submit(
        JobSubmit(
            user_id="owner",
            session_id="conversation-1",
            capability="tutoring",
        )
    )

    assert first.web_search_enabled is False
    assert first.metadata == {
        "web_search_enabled": False,
        "web_search_requested": False,
        "display": "kept",
    }
    assert second.web_search_enabled is True
    assert (await store.get(first.job_id)).web_search_enabled is False  # type: ignore[union-attr]
    assert first.to_summary()["web_search_enabled"] is False
    assert first.to_full_dict()["web_search_requested"] is False

    no_session = await runner.submit(
        JobSubmit(user_id="owner", capability="tutoring")
    )
    assert no_session.web_search_enabled is False
    await store.close()


@pytest.mark.asyncio
async def test_job_migration_restart_and_follow_up_inherit_snapshot(tmp_path) -> None:
    db_path = tmp_path / "legacy-jobs.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id VARCHAR(64) NOT NULL UNIQUE,
                user_id VARCHAR(128) NOT NULL,
                session_id VARCHAR(64) NOT NULL DEFAULT '',
                capability VARCHAR(64) NOT NULL DEFAULT 'resource_generation',
                parent_job_id VARCHAR(64), task_kind VARCHAR(64),
                dedupe_key VARCHAR(256), claim_owner VARCHAR(64),
                claim_expires_at DATETIME,
                claim_generation INTEGER NOT NULL DEFAULT 0,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                message VARCHAR NOT NULL DEFAULT '',
                language VARCHAR(8) NOT NULL DEFAULT 'zh',
                metadata_json JSON NOT NULL DEFAULT '{}',
                error VARCHAR, error_log_ref JSON,
                terminal_event_id VARCHAR(64),
                created_at DATETIME NOT NULL,
                started_at DATETIME, finished_at DATETIME,
                result JSON, event_count INTEGER NOT NULL DEFAULT 0,
                last_seq INTEGER NOT NULL DEFAULT 0,
                events JSON NOT NULL DEFAULT '[]'
            );
            INSERT INTO jobs (
                job_id, user_id, session_id, capability, status, created_at
            ) VALUES (
                'legacy-job', 'owner', 'conversation-1', 'tutoring',
                'pending', CURRENT_TIMESTAMP
            );
            """
        )

    store = JobStore(db_path)
    await store.init()
    legacy = await store.get("legacy-job")
    assert legacy is not None
    assert legacy.web_search_enabled is False

    parent = Job(
        job_id="enabled-parent",
        user_id="owner",
        session_id="conversation-1",
        capability="resource_generation",
        web_search_enabled=True,
        status=JobStatus.SUCCEEDED,
    )
    await store.save(parent)
    child = await store.create_child_if_absent(
        parent_job_id=parent.job_id,
        task_kind="video_render",
        dedupe_key="video:1",
        payload={"resource_id": "resource-1"},
    )
    assert child.web_search_enabled is True
    assert child.to_summary()["web_search_requested"] is True
    await store.close()

    reopened = JobStore(db_path)
    await reopened.init()
    restored = await reopened.get(parent.job_id)
    restored_child = await reopened.get(child.job_id)
    assert restored is not None and restored.web_search_enabled is True
    assert restored_child is not None and restored_child.web_search_enabled is True
    await reopened.close()


@pytest.mark.asyncio
async def test_running_context_uses_immutable_persisted_snapshot(tmp_path) -> None:
    state = {"enabled": True}

    async def lookup(_session_id: str):
        return SimpleNamespace(user_id="owner", web_search_enabled=state["enabled"])

    capability = _CapturingCapability()
    store = JobStore(tmp_path / "running-jobs.db")
    await store.init()
    runner = JobRunner(
        job_store=store,
        capability_registry=SimpleNamespace(get=lambda _name: capability),
        conversation_lookup=lookup,
    )
    job = await runner.submit(
        JobSubmit(
            user_id="owner",
            session_id="conversation-1",
            capability="tutoring",
        )
    )
    await asyncio.wait_for(capability.started.wait(), timeout=2)
    state["enabled"] = False
    capability.release.set()
    await asyncio.wait_for(runner._tasks[job.job_id], timeout=2)  # noqa: SLF001

    assert capability.snapshot is True
    persisted = await store.get(job.job_id)
    assert persisted is not None and persisted.web_search_enabled is True
    await store.close()
