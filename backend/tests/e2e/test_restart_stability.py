"""Stage 6 — end-to-end restart-stability test.

Pins the plan's restart-survival acceptance criterion across the
THREE persistence layers (jobs, knowledge bases, conversations):

  - A running job's terminal event is replayable after a restart.
  - A knowledge base + its documents survive a process restart.
  - A conversation and its messages survive a process restart.

The "restart" is simulated by dropping the in-process singletons
and creating a fresh FastAPI app — both reads and writes must hit
the on-disk SQLite files.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from httpx import ASGITransport
from tutor.api.main import create_app
from tutor.core.capability_result import CapabilityResult, FollowUpTaskSpec
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.config.settings import reset_settings_cache
from tutor.services.conversations import reset_conversation_store
from tutor.services.jobs import (
    JobStatus,
    get_job_store,
    reset_job_runner,
    reset_job_store,
)
from tutor.services.jobs.contracts import JobResultContract
from tutor.services.jobs.follow_up import FollowUpScheduler
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import Job
from tutor.services.jobs.store import JobStore
from tutor.services.knowledge_base import (
    KnowledgeBaseService,
    seed_default_libraries,
)
from tutor.services.knowledge_base.store import (
    reset_kb_store,
)


def _app_client() -> httpx.AsyncClient:
    app = create_app()
    return httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )


class _RestartedVideoCapability:
    async def run(
        self, context: UnifiedContext, bus: StreamBus
    ) -> CapabilityResult:
        return CapabilityResult(
            assistant_message="恢复后视频完成",
            payload={"resource_id": context.metadata["resource_id"]},
        )


class _RestartCapabilities:
    def get(self, name: str):
        return _RestartedVideoCapability() if name == "video_render" else None


@pytest.mark.asyncio
async def test_queued_follow_up_child_resumes_after_fresh_runner(tmp_path) -> None:
    db_path = tmp_path / "restart-child.db"
    first_store = JobStore(db_path)
    await first_store.init()
    parent = Job(
        job_id="restart-parent",
        user_id="local-user",
        session_id="restart-session",
        status=JobStatus.SUCCEEDED,
    )
    await first_store.save(parent)
    child = (
        await FollowUpScheduler(first_store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    payload={"package_id": "pkg-r", "resource_id": "video-r"},
                    dedupe_key="video:pkg-r:video-r",
                ),
            ),
        )
    )[0]
    await first_store.close()

    fresh_store = JobStore(db_path)
    await fresh_store.init()
    runner = JobRunner(
        job_store=fresh_store,
        capability_registry=_RestartCapabilities(),  # type: ignore[arg-type]
    )
    assert await runner.resume_pending() == 1
    for _ in range(100):
        durable = await fresh_store.get(child.job_id)
        if durable is not None and durable.status == JobStatus.SUCCEEDED:
            break
        import asyncio

        await asyncio.sleep(0.02)
    else:
        raise AssertionError("durable child was not resumed")
    assert durable.result["assistant_message"] == "恢复后视频完成"
    assert (await fresh_store.get(parent.job_id)).status == JobStatus.SUCCEEDED
    await fresh_store.close()


@pytest.mark.asyncio
async def test_jobs_kb_and_conversations_survive_restart(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    reset_settings_cache()
    reset_job_store()
    reset_job_runner()
    reset_kb_store()
    reset_conversation_store()

    # ---- Phase 1: write everything -------------------------------
    seed_default_libraries(KnowledgeBaseService())
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    contract = JobResultContract(
        job_id=job_id,
        capability="tutoring",
        status="succeeded",
        assistant_message="重启后这条消息仍然在",
    )
    job = Job(
        job_id=job_id,
        user_id="local-user",
        session_id="sess_restart",
        capability="tutoring",
        status=JobStatus.SUCCEEDED,
        message="解释 self-attention",
        language="zh",
        metadata={},
        created_at=now,
        finished_at=now,
        result=contract.model_dump(mode="json"),
    )

    async with _app_client() as client:
        # Persist a job
        store = get_job_store()
        await store.init()
        await store.save(job)

        # Persist a knowledge-base document via the real upload
        # endpoint so the on-disk state matches production.
        text = tmp_path / "doc.txt"
        text.write_text("Transformer attention.\n", encoding="utf-8")
        with text.open("rb") as f:
            r = await client.post(
                "/api/v1/knowledge-bases/ai_introduction/documents",
                files={"file": ("doc.txt", f, "text/plain")},
            )
        assert r.status_code == 202, r.text
        # Drain the queue so the doc lands in 'ready' before restart.
        import asyncio
        await asyncio.sleep(2.0)

        # Persist a conversation + a message
        r = await client.post(
            "/api/v1/conversations",
            json={"user_id": "u1", "session_id": "sess_restart"},
        )
        assert r.status_code == 201, r.text
        r = await client.post(
            "/api/v1/conversations/sess_restart/messages?user_id=u1",
            json={"role": "user", "content": "重启前写的消息"},
        )
        assert r.status_code == 201, r.text
        await store.close()

    # ---- Phase 2: drop all in-process state, rebuild the app ----
    reset_settings_cache()
    reset_job_store()
    reset_job_runner()
    reset_kb_store()
    reset_conversation_store()

    async with _app_client() as client:
        # Job still there, with the contract intact
        r = await client.get(f"/api/v1/jobs/u1/{job_id}")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "succeeded"
        assert r.json()["result"]["assistant_message"] == "重启后这条消息仍然在"

        # Knowledge base + at least one document still there
        r = await client.get("/api/v1/knowledge-bases/ai_introduction")
        assert r.status_code == 200, r.text
        detail = r.json()
        assert detail["id"] == "ai_introduction"
        assert len(detail["documents"]) >= 1
        doc_statuses = {d["status"] for d in detail["documents"]}
        # Either ready (in-process pipeline completed) or uploaded
        # (queued for the next event loop). Both prove survival.
        assert doc_statuses & {"ready", "uploaded", "extracting", "chunking", "embedding"}

        # Conversation still there with its message
        r = await client.get(
            "/api/v1/conversations/sess_restart?user_id=u1"
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["message_count"] == 1
        assert body["messages"][0]["content"] == "重启前写的消息"
