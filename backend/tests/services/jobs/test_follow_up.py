"""Durable follow-up child job persistence and restart recovery."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

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


class _CountingBlockingCapability:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return CapabilityResult(assistant_message="child complete")


class _CancellationAwareCapability:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class _ParentWithUnsupportedFollowUp:
    async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
        return CapabilityResult(
            assistant_message="invalid child",
            follow_up_tasks=(
                FollowUpTaskSpec(
                    kind="profile_update",
                    payload={"profile_id": "p1"},
                    dedupe_key="profile:p1",
                ),
            ),
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
async def test_init_migrates_legacy_cross_parent_learning_child_duplicates(
    tmp_path,
) -> None:
    db_path = tmp_path / "legacy-learning-children.db"
    store = JobStore(db_path)
    await store.init()
    parents = [
        Job(
            job_id=f"legacy-parent-{index}",
            user_id="local-user",
            status=JobStatus.SUCCEEDED,
        )
        for index in (1, 2)
    ]
    for parent in parents:
        await store.save(parent)
    canonical = (
        await FollowUpScheduler(store).enqueue(
            parents[0].job_id,
            (
                FollowUpTaskSpec(
                    kind="path_rebuild",
                    dedupe_key="path_rebuild:2",
                    payload={
                        "user_id": "local-user",
                        "profile_version": 2,
                        "profile": {"user_id": "local-user", "version": 2},
                    },
                ),
            ),
        )
    )[0]
    canonical_grandchild = (
        await FollowUpScheduler(store).enqueue(
            canonical.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    dedupe_key="video:shared",
                    payload={"package_id": "pkg", "resource_id": "video"},
                ),
            ),
        )
    )[0]
    await store.close()

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("DROP INDEX uq_learning_follow_up_dedupe")
        row = dict(
            connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (canonical.job_id,)
            ).fetchone()
        )
        row.pop("id")
        row["job_id"] = "legacy-duplicate-child"
        row["parent_job_id"] = parents[1].job_id
        row["status"] = "completed"
        row["result"] = '{"legacy_result":"preserved"}'
        columns = list(row)
        connection.execute(
            f"INSERT INTO jobs ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            tuple(row[column] for column in columns),
        )
        grandchild = dict(
            connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (canonical_grandchild.job_id,),
            ).fetchone()
        )
        grandchild.pop("id")
        grandchild["job_id"] = "legacy-duplicate-grandchild"
        grandchild["parent_job_id"] = "legacy-duplicate-child"
        grandchild["status"] = JobStatus.SUCCEEDED.value
        grandchild_columns = list(grandchild)
        connection.execute(
            f"INSERT INTO jobs ({', '.join(grandchild_columns)}) "
            f"VALUES ({', '.join('?' for _ in grandchild_columns)})",
            tuple(grandchild[column] for column in grandchild_columns),
        )

    reopened = JobStore(db_path)
    await reopened.init()

    children = [
        *await reopened.get_children(parents[0].job_id),
        *await reopened.get_children(parents[1].job_id),
    ]
    assert [child.job_id for child in children] == ["legacy-duplicate-child"]
    migrated = children[0]
    assert migrated.status == JobStatus.SUCCEEDED
    assert migrated.result == {"legacy_result": "preserved"}
    grandchildren = await reopened.get_children(migrated.job_id)
    assert [child.job_id for child in grandchildren] == [
        "legacy-duplicate-grandchild"
    ]
    assert grandchildren[0].status == JobStatus.SUCCEEDED
    assert await reopened.get(canonical.job_id) is None
    assert await reopened.get(canonical_grandchild.job_id) is None
    returned = (
        await FollowUpScheduler(reopened).enqueue(
            parents[1].job_id,
            (
                FollowUpTaskSpec(
                    kind="path_rebuild",
                    dedupe_key="path_rebuild:2",
                    payload={
                        "user_id": "local-user",
                        "profile_version": 2,
                        "profile": {"user_id": "local-user", "version": 2},
                    },
                ),
            ),
        )
    )[0]
    assert returned.job_id == migrated.job_id
    await reopened.close()

    with sqlite3.connect(db_path) as connection:
        orphan_count = connection.execute(
            "SELECT COUNT(*) FROM jobs AS child "
            "LEFT JOIN jobs AS parent ON parent.job_id = child.parent_job_id "
            "WHERE child.parent_job_id IS NOT NULL AND parent.job_id IS NULL"
        ).fetchone()[0]
    assert orphan_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted_running", [False, True])
@pytest.mark.parametrize("parent_status", [JobStatus.SUCCEEDED, JobStatus.PARTIAL])
async def test_fresh_runner_resumes_a_persisted_queued_child_to_terminal(
    tmp_path,
    interrupted_running,
    parent_status,
    monkeypatch,
) -> None:
    db_path = tmp_path / "jobs.db"
    first_store = JobStore(db_path)
    await first_store.init()
    parent = Job(
        job_id="parent-restart",
        user_id="local-user",
        session_id="session-restart",
        capability="resource_generation",
        status=parent_status,
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
    from tutor.services.jobs import follow_up as follow_up_module

    monkeypatch.setattr(
        follow_up_module,
        "build_follow_up_capability",
        lambda kind: _VideoRenderCapability(),
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
    assert durable_parent.status == parent_status
    await fresh_store.close()


@pytest.mark.asyncio
async def test_parent_terminalizes_independently_after_persisting_raw_follow_up(
    tmp_path,
    monkeypatch,
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
    from tutor.services.jobs import follow_up as follow_up_module

    monkeypatch.setattr(
        follow_up_module,
        "build_follow_up_capability",
        lambda kind: child_capability,
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
async def test_cancel_winning_parent_terminal_cas_never_dispatches_enqueued_child(
    tmp_path,
    monkeypatch,
) -> None:
    """The enqueue→terminal crash window must not leak child side effects."""
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(
        job_id="parent-cancel-race",
        user_id="local-user",
        session_id="session-race",
        capability="resource_generation",
        status=JobStatus.RUNNING,
    )
    await store.save(parent)
    child_capability = _BlockingVideoRender()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(
            {"video_render": child_capability}
        ),  # type: ignore[arg-type]
    )
    entered_terminal = asyncio.Event()
    release_terminal = asyncio.Event()
    original_write_terminal = runner._write_terminal

    async def blocked_write_terminal(*args, **kwargs):
        entered_terminal.set()
        await release_terminal.wait()
        return await original_write_terminal(*args, **kwargs)

    monkeypatch.setattr(runner, "_write_terminal", blocked_write_terminal)
    result = CapabilityResult(
        assistant_message="资源包已完成",
        follow_up_tasks=(
            FollowUpTaskSpec(
                kind="video_render",
                payload={"package_id": "pkg-race", "resource_id": "video-race"},
                dedupe_key="video:pkg-race:video-race",
            ),
        ),
    )
    finishing = asyncio.create_task(
        runner._finish_job(
            parent,
            result=result,
            failure=None,
            failure_traceback=None,
            partial_resources=[],
        )
    )
    await asyncio.wait_for(entered_terminal.wait(), timeout=2)
    assert await store.set_terminal(
        parent.job_id,
        status=JobStatus.CANCELLED,
        finished_at=None,
        result={
            "job_id": parent.job_id,
            "capability": parent.capability,
            "status": "cancelled",
            "assistant_message": "任务已取消",
        },
        terminal_event={"type": "job_terminal", "content": "任务已取消"},
    )
    release_terminal.set()
    await finishing
    children = await store.get_children(parent.job_id)
    assert len(children) == 1
    child = await _wait_terminal(store, children[0].job_id)
    assert child.status == JobStatus.CANCELLED
    assert not child_capability.started.is_set()
    await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_parent_status"),
    [
        ({}, JobStatus.SUCCEEDED),
        (
            {
                "artifacts": [
                    {"resource_type": "document", "status": "succeeded"},
                    {"resource_type": "video", "status": "failed"},
                ]
            },
            JobStatus.PARTIAL,
        ),
    ],
)
async def test_successful_parent_cas_loser_never_settles_claimed_child(
    tmp_path,
    monkeypatch,
    payload,
    expected_parent_status,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(
        job_id="parent-success-race",
        user_id="local-user",
        status=JobStatus.RUNNING,
    )
    await store.save(parent)
    child_capability = _CountingBlockingCapability()
    from tutor.services.jobs import follow_up as follow_up_module

    monkeypatch.setattr(
        follow_up_module,
        "build_follow_up_capability",
        lambda kind: child_capability,
    )
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(),  # type: ignore[arg-type]
    )
    result = CapabilityResult(
        assistant_message="parent complete",
        payload=payload,
        follow_up_tasks=(
            FollowUpTaskSpec(
                kind="video_render",
                payload={"package_id": "pkg", "resource_id": "video"},
                dedupe_key="video:pkg:video",
            ),
        ),
    )

    await runner._finish_job(
        parent,
        result=result,
        failure=None,
        failure_traceback=None,
        partial_resources=[],
    )
    await asyncio.wait_for(child_capability.started.wait(), timeout=2)
    await runner._finish_job(
        parent,
        result=result,
        failure=None,
        failure_traceback=None,
        partial_resources=[],
    )

    children = await store.get_children(parent.job_id)
    assert len(children) == 1
    durable_child = await store.get(children[0].job_id)
    assert durable_child is not None and durable_child.status == JobStatus.RUNNING
    assert durable_child.terminal_event_id is None
    assert (await store.get(parent.job_id)).status == expected_parent_status
    child_capability.release.set()
    assert (await _wait_terminal(store, children[0].job_id)).status == JobStatus.SUCCEEDED
    assert child_capability.calls == 1
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
    monkeypatch,
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
    from tutor.services.jobs import follow_up as follow_up_module

    monkeypatch.setattr(
        follow_up_module,
        "build_follow_up_capability",
        lambda kind: child_capability,
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parent_status", "expected_child_status"),
    [
        (JobStatus.FAILED, JobStatus.FAILED),
        (JobStatus.CANCELLED, JobStatus.CANCELLED),
    ],
)
async def test_resume_settles_child_without_side_effect_when_parent_is_terminally_ineligible(
    tmp_path,
    parent_status,
    expected_child_status,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(job_id="ineligible-parent", status=JobStatus.SUCCEEDED)
    await store.save(parent)
    child = (
        await FollowUpScheduler(store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    payload={"package_id": "pkg", "resource_id": "video"},
                    dedupe_key="video:pkg:video",
                ),
            ),
        )
    )[0]
    parent.status = parent_status
    await store.save(parent)
    capability = _CountingBlockingCapability()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(
            {"video_render": capability}
        ),  # type: ignore[arg-type]
    )

    assert await runner.resume_pending() == 0
    terminal = await _wait_terminal(store, child.job_id)
    assert terminal.status == expected_child_status
    assert capability.calls == 0
    await store.close()


@pytest.mark.asyncio
async def test_resume_fails_orphan_child_without_executing_it(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(job_id="missing-parent", status=JobStatus.SUCCEEDED)
    await store.save(parent)
    child = (
        await FollowUpScheduler(store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    payload={"package_id": "pkg", "resource_id": "video"},
                    dedupe_key="video:pkg:video",
                ),
            ),
        )
    )[0]
    assert await store.delete(parent.job_id)
    capability = _CountingBlockingCapability()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(
            {"video_render": capability}
        ),  # type: ignore[arg-type]
    )

    assert await runner.resume_pending() == 0
    terminal = await _wait_terminal(store, child.job_id)
    assert terminal.status == JobStatus.FAILED
    assert capability.calls == 0
    await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_status", [JobStatus.PENDING, JobStatus.RUNNING])
async def test_resume_defers_child_while_parent_is_nonterminal(
    tmp_path,
    parent_status,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(job_id="active-parent", status=JobStatus.SUCCEEDED)
    await store.save(parent)
    child = (
        await FollowUpScheduler(store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    payload={"package_id": "pkg", "resource_id": "video"},
                    dedupe_key="video:pkg:video",
                ),
            ),
        )
    )[0]
    parent.status = parent_status
    await store.save(parent)
    capability = _CountingBlockingCapability()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(
            {"video_render": capability}
        ),  # type: ignore[arg-type]
    )

    assert await runner.resume_pending() == 0
    durable_child = await store.get(child.job_id)
    assert durable_child is not None and durable_child.status == JobStatus.PENDING
    assert capability.calls == 0
    await store.close()


@pytest.mark.asyncio
async def test_startup_reaps_parent_before_settling_its_deferred_child(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(job_id="crashed-parent", status=JobStatus.RUNNING)
    await store.save(parent)
    child = (
        await FollowUpScheduler(store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    payload={"package_id": "pkg", "resource_id": "video"},
                    dedupe_key="video:pkg:video",
                ),
            ),
        )
    )[0]
    capability = _CountingBlockingCapability()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(
            {"video_render": capability}
        ),  # type: ignore[arg-type]
    )

    assert await runner.resume_active_jobs() == 1
    durable_parent = await _wait_terminal(store, parent.job_id)
    durable_child = await _wait_terminal(store, child.job_id)
    assert durable_parent.status == JobStatus.FAILED
    assert durable_child.status == JobStatus.FAILED
    assert capability.calls == 0
    await store.close()


@pytest.mark.asyncio
async def test_two_runners_atomically_claim_one_child_for_single_execution(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "jobs.db"
    store_a = JobStore(db_path)
    store_b = JobStore(db_path)
    await store_a.init()
    await store_b.init()
    parent = Job(job_id="claim-parent", status=JobStatus.SUCCEEDED)
    await store_a.save(parent)
    child = (
        await FollowUpScheduler(store_a).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    payload={"package_id": "pkg", "resource_id": "video"},
                    dedupe_key="video:pkg:video",
                ),
            ),
        )
    )[0]
    capability = _CountingBlockingCapability()
    from tutor.services.jobs import follow_up as follow_up_module

    monkeypatch.setattr(
        follow_up_module,
        "build_follow_up_capability",
        lambda kind: capability,
    )
    runner_a = JobRunner(
        job_store=store_a,
        capability_registry=_CapabilitiesStub(
            {"video_render": capability}
        ),  # type: ignore[arg-type]
    )
    runner_b = JobRunner(
        job_store=store_b,
        capability_registry=_CapabilitiesStub(
            {"video_render": capability}
        ),  # type: ignore[arg-type]
    )

    resumed = await asyncio.gather(
        runner_a.resume_pending(),
        runner_b.resume_pending(),
    )
    await asyncio.wait_for(capability.started.wait(), timeout=2)
    assert sum(resumed) == 1
    assert capability.calls == 1
    assert await runner_b.resume_pending() == 0
    capability.release.set()
    assert (await _wait_terminal(store_a, child.job_id)).status == JobStatus.SUCCEEDED
    await store_a.close()
    await store_b.close()


@pytest.mark.asyncio
async def test_claim_generation_fences_stale_owner_terminal_write(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(job_id="fence-parent", status=JobStatus.SUCCEEDED)
    await store.save(parent)
    child = (
        await FollowUpScheduler(store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    payload={"package_id": "pkg", "resource_id": "video"},
                    dedupe_key="video:pkg:video",
                ),
            ),
        )
    )[0]
    first_at = datetime.now(UTC)
    old = await store.claim_child(
        child.job_id,
        owner="old-owner",
        lease_seconds=1,
        now=first_at,
    )
    new = await store.claim_child(
        child.job_id,
        owner="new-owner",
        lease_seconds=60,
        now=first_at + timedelta(seconds=2),
    )
    assert old is not None and new is not None
    assert new.claim_generation == old.claim_generation + 1
    assert not await store.claim_is_current(
        child.job_id,
        owner="old-owner",
        generation=old.claim_generation,
    )
    assert await store.claim_is_current(
        child.job_id,
        owner="new-owner",
        generation=new.claim_generation,
    )
    assert not await store.set_terminal(
        child.job_id,
        status=JobStatus.SUCCEEDED,
        finished_at=None,
        result={"status": "succeeded"},
        terminal_event={"type": "job_terminal", "content": "stale"},
        expected_claim_owner="old-owner",
        expected_claim_generation=old.claim_generation,
    )
    assert await store.set_terminal(
        child.job_id,
        status=JobStatus.SUCCEEDED,
        finished_at=None,
        result={"status": "succeeded"},
        terminal_event={"type": "job_terminal", "content": "current"},
        expected_claim_owner="new-owner",
        expected_claim_generation=new.claim_generation,
    )


@pytest.mark.asyncio
async def test_active_child_cas_false_deletes_before_other_store_can_claim(
    tmp_path,
) -> None:
    first = JobStore(tmp_path / "jobs.db")
    second = JobStore(tmp_path / "jobs.db")
    await first.init()
    await second.init()
    parent = Job(job_id="atomic-bind-parent", status=JobStatus.SUCCEEDED)
    await first.save(parent)
    child = (
        await FollowUpScheduler(first).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_repair_render",
                    payload={
                        "package_id": "pkg",
                        "resource_id": "video",
                        "user_id": "owner",
                        "failed_revision": 0,
                    },
                    dedupe_key="repair:atomic-false",
                ),
            ),
        )
    )[0]
    operation_entered = asyncio.Event()
    release_operation = asyncio.Event()

    async def stale_resource_cas() -> bool:
        operation_entered.set()
        await release_operation.wait()
        return False

    guarded = asyncio.create_task(
        first.run_if_child_active_or_delete(
            child.job_id,
            operation=stale_resource_cas,
        )
    )
    await asyncio.wait_for(operation_entered.wait(), timeout=2)
    claim = asyncio.create_task(
        second.claim_child(
            child.job_id,
            owner="other-runner",
            lease_seconds=60,
        )
    )
    await asyncio.sleep(0.05)
    assert not claim.done()
    release_operation.set()

    assert await guarded is False
    assert await claim is None
    assert await first.get(child.job_id) is None
    await second.close()
    await first.close()


@pytest.mark.asyncio
async def test_active_child_cas_true_keeps_child_claimable(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(job_id="atomic-keep-parent", status=JobStatus.SUCCEEDED)
    await store.save(parent)
    child = (
        await FollowUpScheduler(store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_repair_render",
                    payload={
                        "package_id": "pkg",
                        "resource_id": "video",
                        "user_id": "owner",
                        "failed_revision": 0,
                    },
                    dedupe_key="repair:atomic-true",
                ),
            ),
        )
    )[0]

    async def current_resource_cas() -> bool:
        return True

    assert await store.run_if_child_active_or_delete(
        child.job_id,
        operation=current_resource_cas,
    )
    assert await store.claim_child(
        child.job_id,
        owner="runner",
        lease_seconds=60,
    ) is not None
    await store.close()


@pytest.mark.asyncio
async def test_active_child_bind_does_not_run_for_ineligible_parent(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(job_id="atomic-ineligible-parent", status=JobStatus.SUCCEEDED)
    await store.save(parent)
    child = (
        await FollowUpScheduler(store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_repair_render",
                    payload={
                        "package_id": "pkg",
                        "resource_id": "video",
                        "user_id": "owner",
                        "failed_revision": 0,
                    },
                    dedupe_key="repair:atomic-ineligible",
                ),
            ),
        )
    )[0]
    await store.update_status(parent.job_id, status=JobStatus.CANCELLED)
    operation_called = False

    async def resource_cas() -> bool:
        nonlocal operation_called
        operation_called = True
        return True

    assert not await store.run_if_child_active_or_delete(
        child.job_id,
        operation=resource_cas,
    )
    assert operation_called is False
    await store.close()


@pytest.mark.asyncio
async def test_failed_heartbeat_cancels_owner_execution_without_terminal_write(
    tmp_path,
    monkeypatch,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(job_id="heartbeat-parent", status=JobStatus.SUCCEEDED)
    await store.save(parent)
    child = (
        await FollowUpScheduler(store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    payload={"package_id": "pkg", "resource_id": "video"},
                    dedupe_key="video:pkg:video",
                ),
            ),
        )
    )[0]
    capability = _CancellationAwareCapability()
    from tutor.services.jobs import follow_up as follow_up_module

    monkeypatch.setattr(
        follow_up_module,
        "build_follow_up_capability",
        lambda kind: capability,
    )

    async def reject_renewal(*args, **kwargs):
        return False

    monkeypatch.setattr(store, "renew_child_claim", reject_renewal)
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(),  # type: ignore[arg-type]
    )
    runner.CHILD_HEARTBEAT_SECONDS = 0.01
    assert await runner.resume_pending() == 1
    await asyncio.wait_for(capability.started.wait(), timeout=2)
    try:
        await asyncio.wait_for(capability.cancelled.wait(), timeout=1)
    finally:
        task = runner._tasks.get(child.job_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    durable = await store.get(child.job_id)
    assert durable is not None and durable.status == JobStatus.RUNNING
    assert durable.terminal_event_id is None
    await store.close()


@pytest.mark.asyncio
async def test_runner_shutdown_cancels_and_gathers_jobs_and_claim_retry_monitors(
    tmp_path,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(),  # type: ignore[arg-type]
    )
    job_cancelled = asyncio.Event()
    retry_cancelled = asyncio.Event()

    async def block(cancelled: asyncio.Event) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    job_task = asyncio.create_task(block(job_cancelled))
    retry_task = asyncio.create_task(block(retry_cancelled))
    runner._tasks["active"] = job_task
    runner._claim_retry_tasks["retry"] = retry_task
    await asyncio.sleep(0)

    await runner.shutdown()

    assert job_cancelled.is_set() and retry_cancelled.is_set()
    assert job_task.done() and retry_task.done()
    assert runner._tasks == {}
    assert runner._claim_retry_tasks == {}
    await store.close()


@pytest.mark.asyncio
async def test_shutdown_job_runner_clears_singleton_after_gather(
    tmp_path,
    monkeypatch,
) -> None:
    from tutor.services.jobs import runner as runner_module

    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(),  # type: ignore[arg-type]
    )
    task = asyncio.create_task(asyncio.Event().wait())
    runner._claim_retry_tasks["retry"] = task
    monkeypatch.setattr(runner_module, "_runner", runner)

    await runner_module.shutdown_job_runner()

    assert task.done()
    assert runner_module._runner is None
    await store.close()


@pytest.mark.asyncio
async def test_sync_reset_requests_shutdown_and_cancels_outstanding_tasks(
    tmp_path,
    monkeypatch,
) -> None:
    from tutor.services.jobs import runner as runner_module

    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(),  # type: ignore[arg-type]
    )
    cancelled = asyncio.Event()

    async def block() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(block())
    runner._claim_retry_tasks["retry"] = task
    monkeypatch.setattr(runner_module, "_runner", runner)
    await asyncio.sleep(0)

    runner_module.reset_job_runner()
    await asyncio.sleep(0)

    assert cancelled.is_set()
    assert task.done()
    assert runner._shutting_down
    assert runner_module._runner is None
    await store.close()


@pytest.mark.asyncio
async def test_expired_claim_recovers_and_active_old_claim_retries_after_lease_expiry(
    tmp_path,
    monkeypatch,
) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(job_id="lease-parent", status=JobStatus.SUCCEEDED)
    await store.save(parent)
    children = await FollowUpScheduler(store).enqueue(
        parent.job_id,
        (
            FollowUpTaskSpec(
                kind="video_render",
                payload={"package_id": "pkg", "resource_id": "expired"},
                dedupe_key="video:pkg:expired",
            ),
            FollowUpTaskSpec(
                kind="video_render",
                payload={"package_id": "pkg", "resource_id": "active"},
                dedupe_key="video:pkg:active",
            ),
        ),
    )
    expired, active = children
    expired.status = JobStatus.RUNNING
    expired.claim_owner = "dead-runner"
    expired.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    active.status = JobStatus.RUNNING
    active.claim_owner = "live-runner"
    active.claim_expires_at = datetime.now(UTC) + timedelta(milliseconds=500)
    await store.save(expired)
    await store.save(active)
    capability = _CountingBlockingCapability()
    capability.release.set()
    from tutor.services.jobs import follow_up as follow_up_module

    monkeypatch.setattr(
        follow_up_module,
        "build_follow_up_capability",
        lambda kind: capability,
    )
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(
            {"video_render": capability}
        ),  # type: ignore[arg-type]
    )
    runner.CHILD_CLAIM_RETRY_SECONDS = 0.02

    assert await runner.resume_pending() == 1
    assert (await _wait_terminal(store, expired.job_id)).status == JobStatus.SUCCEEDED
    durable_active = await store.get(active.job_id)
    assert durable_active is not None and durable_active.status == JobStatus.RUNNING
    assert durable_active.claim_owner == "live-runner"
    assert capability.calls == 1
    assert (await _wait_terminal(store, active.job_id)).status == JobStatus.SUCCEEDED
    assert capability.calls == 2
    await store.close()


@pytest.mark.asyncio
async def test_unsupported_follow_up_fails_parent_without_creating_child(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(
            {"resource_generation": _ParentWithUnsupportedFollowUp()}
        ),  # type: ignore[arg-type]
    )

    parent = await runner.submit(
        JobSubmit(capability="resource_generation", user_id="local-user")
    )
    terminal = await _wait_terminal(store, parent.job_id)
    assert terminal.status == JobStatus.FAILED
    assert await store.get_children(parent.job_id) == []
    await store.close()


@pytest.mark.asyncio
async def test_scheduler_validates_all_specs_before_persisting_any_child(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(job_id="validate-parent", status=JobStatus.SUCCEEDED)
    await store.save(parent)
    specs = (
        FollowUpTaskSpec(
            kind="video_render",
            payload={"package_id": "pkg", "resource_id": "valid"},
            dedupe_key="video:pkg:valid",
        ),
        FollowUpTaskSpec(
            kind="video_render",
            payload={"package_id": "pkg"},
            dedupe_key="",
        ),
    )

    with pytest.raises(ValueError, match="follow-up"):
        await FollowUpScheduler(store).enqueue(parent.job_id, specs)
    assert await store.get_children(parent.job_id) == []
    await store.close()


@pytest.mark.asyncio
async def test_follow_up_registry_wins_over_same_named_ordinary_capability(
    tmp_path,
    monkeypatch,
) -> None:
    from tutor.services.jobs import follow_up as follow_up_module

    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(job_id="registry-parent", status=JobStatus.SUCCEEDED)
    await store.save(parent)
    child = (
        await FollowUpScheduler(store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    payload={"package_id": "pkg", "resource_id": "video"},
                    dedupe_key="video:pkg:video",
                ),
            ),
        )
    )[0]
    follow_up_capability = _CountingBlockingCapability()
    follow_up_capability.release.set()
    ordinary_capability = _CountingBlockingCapability()
    ordinary_capability.release.set()
    monkeypatch.setattr(
        follow_up_module,
        "build_follow_up_capability",
        lambda kind: follow_up_capability,
    )
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(
            {"video_render": ordinary_capability}
        ),  # type: ignore[arg-type]
    )

    assert await runner.resume_pending() == 1
    assert (await _wait_terminal(store, child.job_id)).status == JobStatus.SUCCEEDED
    assert follow_up_capability.calls == 1
    assert ordinary_capability.calls == 0
    await store.close()


@pytest.mark.asyncio
async def test_job_stats_are_parent_only_like_list_and_count(tmp_path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    await store.init()
    parent = Job(job_id="stats-parent", user_id="u", status=JobStatus.SUCCEEDED)
    await store.save(parent)
    await FollowUpScheduler(store).enqueue(
        parent.job_id,
        (
            FollowUpTaskSpec(
                kind="video_render",
                payload={"package_id": "pkg", "resource_id": "video"},
                dedupe_key="video:pkg:video",
            ),
        ),
    )

    stats = await store.stats("u")
    assert stats["job_count"] == 1
    assert stats["active_count"] == 0
    assert stats["by_capability"] == {"resource_generation": 1}
    await store.close()
