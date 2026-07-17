"""Durable follow-up child job persistence and restart recovery."""

from __future__ import annotations

import asyncio

import pytest
from tutor.core.capability_result import CapabilityResult, FollowUpTaskSpec
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.follow_up import FollowUpScheduler
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import Job, JobStatus, JobSubmit
from tutor.services.jobs.store import JobStore


class _VideoRenderCapability:
    async def run(
        self,
        context: UnifiedContext,
        bus: StreamBus,
    ) -> CapabilityResult:
        return CapabilityResult(
            assistant_message="视频渲染完成",
            payload={"resource_id": context.metadata["resource_id"]},
        )


class _CapabilitiesStub:
    def __init__(self, mapping: dict[str, object] | None = None) -> None:
        self.mapping = mapping or {}

    def get(self, name: str):
        if name in self.mapping:
            return self.mapping[name]
        if name == "video_render":
            return _VideoRenderCapability()
        return None


class _ParentWithFollowUp:
    async def run(
        self,
        context: UnifiedContext,
        bus: StreamBus,
    ) -> CapabilityResult:
        return CapabilityResult(
            assistant_message="资源包已完成",
            payload={"package_id": "pkg-raw"},
            follow_up_tasks=(
                FollowUpTaskSpec(
                    kind="video_render",
                    payload={
                        "package_id": "pkg-raw",
                        "resource_id": "video-raw",
                        "authorization": "Bearer secret-child-token",
                    },
                    dedupe_key="video:pkg-raw:video-raw",
                ),
            ),
        )


class _BlockingVideoRender:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(
        self,
        context: UnifiedContext,
        bus: StreamBus,
    ) -> CapabilityResult:
        self.started.set()
        await self.release.wait()
        return CapabilityResult(
            assistant_message="视频渲染完成",
            payload={"resource_id": context.metadata["resource_id"]},
        )


async def _wait_terminal(store: JobStore, job_id: str) -> Job:
    for _ in range(100):
        job = await store.get(job_id)
        if job is not None and job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.PARTIAL,
        }:
            return job
        await asyncio.sleep(0.02)
    raise AssertionError(f"child job {job_id} did not become terminal")


@pytest.mark.asyncio
async def test_follow_up_is_persisted_and_idempotent_by_parent_and_dedupe_key(
    tmp_path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(
        job_id="parent-1",
        user_id="local-user",
        session_id="session-1",
        capability="resource_generation",
        status=JobStatus.SUCCEEDED,
    )
    await store.save(parent)
    scheduler = FollowUpScheduler(store)
    spec = FollowUpTaskSpec(
        kind="video_render",
        payload={"package_id": "pkg-1", "resource_id": "video-1"},
        dedupe_key="video:pkg-1:video-1",
    )

    first = await scheduler.enqueue(parent.job_id, (spec,))
    second = await scheduler.enqueue(parent.job_id, (spec,))

    assert first[0].job_id == second[0].job_id
    assert first[0].parent_job_id == parent.job_id
    assert first[0].task_kind == "video_render"
    assert first[0].dedupe_key == spec.dedupe_key
    assert first[0].status == JobStatus.PENDING
    assert first[0].user_id == parent.user_id
    assert first[0].session_id == parent.session_id
    assert first[0].metadata == spec.payload
    assert [job.job_id for job in await store.get_children(parent.job_id)] == [
        first[0].job_id
    ]
    await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted_running", [False, True])
async def test_fresh_runner_resumes_a_persisted_queued_child_to_terminal(
    tmp_path,
    interrupted_running,
) -> None:
    db_path = tmp_path / "jobs.db"
    first_store = JobStore(db_path)
    await first_store.init()
    parent = Job(
        job_id="parent-restart",
        user_id="local-user",
        session_id="session-restart",
        capability="resource_generation",
        status=JobStatus.SUCCEEDED,
    )
    await first_store.save(parent)
    child = (
        await FollowUpScheduler(first_store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    payload={"package_id": "pkg-2", "resource_id": "video-2"},
                    dedupe_key="video:pkg-2:video-2",
                ),
            ),
        )
    )[0]
    if interrupted_running:
        await first_store.update_status(
            child.job_id,
            status=JobStatus.RUNNING,
        )
    await first_store.close()

    fresh_store = JobStore(db_path)
    await fresh_store.init()
    fresh_runner = JobRunner(
        job_store=fresh_store,
        capability_registry=_CapabilitiesStub(),  # type: ignore[arg-type]
    )

    resumed = await fresh_runner.resume_pending()
    terminal_child = await _wait_terminal(fresh_store, child.job_id)
    durable_parent = await fresh_store.get(parent.job_id)

    assert resumed == 1
    assert terminal_child.status == JobStatus.SUCCEEDED
    assert terminal_child.result is not None
    assert terminal_child.result["assistant_message"] == "视频渲染完成"
    assert sum(
        event.get("type") == "job_terminal" for event in terminal_child.events
    ) == 1
    assert durable_parent is not None
    assert durable_parent.status == JobStatus.SUCCEEDED
    await fresh_store.close()


@pytest.mark.asyncio
async def test_parent_terminalizes_independently_after_persisting_raw_follow_up(
    tmp_path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    child_capability = _BlockingVideoRender()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(
            {
                "resource_generation": _ParentWithFollowUp(),
                "video_render": child_capability,
            }
        ),  # type: ignore[arg-type]
    )

    parent = await runner.submit(
        # The scheduler must derive owner/session from this durable parent.
        JobSubmit(
            user_id="local-user",
            session_id="session-parent",
            capability="resource_generation",
        )
    )
    await asyncio.wait_for(child_capability.started.wait(), timeout=3)
    stored_parent = await store.get(parent.job_id)
    children = await store.get_children(parent.job_id)

    assert stored_parent is not None
    assert stored_parent.status == JobStatus.SUCCEEDED
    assert len(children) == 1
    assert children[0].status == JobStatus.RUNNING
    assert children[0].metadata["authorization"] == "Bearer secret-child-token"
    assert "secret-child-token" not in str(stored_parent.result)

    child_capability.release.set()
    await _wait_terminal(store, children[0].job_id)
    await store.close()


@pytest.mark.asyncio
async def test_job_projection_includes_children_and_failed_background_status(
    tmp_path,
) -> None:
    from tutor.core.redaction import redact_sensitive

    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(
        job_id="parent-api",
        user_id="local-user",
        session_id="session-api",
        status=JobStatus.SUCCEEDED,
    )
    await store.save(parent)
    child = (
        await FollowUpScheduler(store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    payload={
                        "package_id": "pkg-api",
                        "resource_id": "video-api",
                        "authorization": "Bearer do-not-publish",
                    },
                    dedupe_key="video:pkg-api:video-api",
                ),
            ),
        )
    )[0]
    assert await store.set_terminal(
        child.job_id,
        status=JobStatus.FAILED,
        finished_at=None,
        result={
            "job_id": child.job_id,
            "capability": "video_render",
            "status": "failed",
            "assistant_message": "视频渲染失败",
        },
        terminal_event={
            "type": "job_terminal",
            "content": "视频渲染失败",
            "metadata": {},
        },
    )

    projection = await store.get_with_children(parent.job_id)
    child_projection = await store.get_with_children(child.job_id)
    listed = await store.list(parent.user_id)

    assert projection is not None
    assert projection["background_status"] == "failed"
    assert projection["children"][0]["status"] == "failed"
    assert projection["children"][0]["task_kind"] == "video_render"
    assert projection["children"][0]["metadata"] == redact_sensitive(
        child.metadata
    )
    assert "do-not-publish" not in str(projection)
    assert child_projection is not None
    assert "do-not-publish" not in str(child_projection)
    assert [item["job_id"] for item in listed] == [parent.job_id]
    assert await store.count(parent.user_id) == len(listed)
    await store.close()


@pytest.mark.asyncio
async def test_startup_resume_does_not_reap_a_resumable_child_as_orphaned(
    tmp_path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(
        job_id="parent-startup",
        user_id="local-user",
        session_id="session-startup",
        status=JobStatus.SUCCEEDED,
    )
    await store.save(parent)
    child = (
        await FollowUpScheduler(store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    payload={"package_id": "pkg-3", "resource_id": "video-3"},
                    dedupe_key="video:pkg-3:video-3",
                ),
            ),
        )
    )[0]
    child_capability = _BlockingVideoRender()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(
            {"video_render": child_capability}
        ),  # type: ignore[arg-type]
    )

    resumed = await runner.resume_active_jobs()
    await asyncio.wait_for(child_capability.started.wait(), timeout=3)
    running = await store.get(child.job_id)

    assert resumed == 1
    assert running is not None
    assert running.status == JobStatus.RUNNING
    assert running.terminal_event_id is None

    child_capability.release.set()
    terminal = await _wait_terminal(store, child.job_id)
    assert terminal.status == JobStatus.SUCCEEDED
    await store.close()
