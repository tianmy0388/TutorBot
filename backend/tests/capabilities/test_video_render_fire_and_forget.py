"""Video rendering is durable follow-up work, never fire-and-forget work."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from tutor.capabilities.resource_generation import ResourceGenerationCapability
from tutor.core.capability_result import FollowUpTaskSpec
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.follow_up import FollowUpScheduler
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import Job, JobStatus
from tutor.services.jobs.store import JobStore
from tutor.services.resource_package.schema import (
    Resource,
    ResourcePackage,
    ResourceType,
    VideoResource,
)


def test_resource_capability_has_no_background_render_entrypoint() -> None:
    assert not hasattr(ResourceGenerationCapability, "_start_pending_video_renders")


class _FakeRenderService:
    """Stand-in for ``ManimRenderService`` — controllable success/failure."""

    def __init__(
        self,
        *,
        success: bool = True,
        delay: float = 0.0,
        video_path: str | Path = "/tmp/v.mp4",
    ) -> None:
        self.success = success
        self.delay = delay
        self.video_path = video_path
        self.calls: list[tuple[str, str]] = []

    async def render(self, *, code: str, scene_class: str, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((code, scene_class))
        if self.delay > 0:
            await asyncio.sleep(self.delay)

        class _R:
            pass

        r = _R()
        r.success = self.success
        r.public_url = "https://cdn.example.com/v.mp4" if self.success else None
        r.video_path = self.video_path if self.success else None
        r.duration_seconds = 30.0 if self.success else None
        r.error = None if self.success else "manim exit 1"
        r.attempts = 1
        return r


class _PerSceneRenderService:
    async def render(self, *, code: str, scene_class: str, **kwargs):  # type: ignore[no-untyped-def]
        return await _FakeRenderService(success=scene_class == "ReadyScene").render(
            code=code,
            scene_class=scene_class,
        )


class _LeaseRaceRenderService:
    """Let a stale first renderer finish after the replacement owner."""

    def __init__(self) -> None:
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.calls = 0

    async def render(self, *, code: str, scene_class: str, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        call = self.calls
        if call == 1:
            self.first_started.set()
            await self.release_first.wait()

        class _R:
            pass

        result = _R()
        result.success = True
        result.public_url = f"https://cdn.example.com/owner-{call}.mp4"
        result.video_path = None
        result.duration_seconds = 30.0
        result.error = None
        result.attempts = 1
        return result


def _video_resource(render_status: str = "pending", *, code: str = "class M(Scene): pass") -> Resource:
    return Resource(
        type=ResourceType.VIDEO,
        title="反向传播",
        content="video body",
        topic="反向传播",
        format_specific=VideoResource(
            manim_code=code,
            scene_class="M",
            render_status=render_status,  # type: ignore[arg-type]
        ).model_dump(),
    )


def _cap() -> Any:
    """Build a real ResourceGenerationCapability. We pass mocks for
    every Agent so no LLM / DB is touched. Pydantic v2 BaseCapability
    is frozen-ish, so we have to go through ``__init__``.
    """
    from tutor.capabilities.resource_generation import ResourceGenerationCapability

    # Cheap stubs: never invoked by the render path.
    class _Stub:
        async def process(self, *a, **kw):
            raise NotImplementedError

    cap = ResourceGenerationCapability(
        intent_agent=_Stub(),  # type: ignore[arg-type]
        content_expert=_Stub(),  # type: ignore[arg-type]
        pedagogy=_Stub(),  # type: ignore[arg-type]
        multimedia=_Stub(),  # type: ignore[arg-type]
        exercise_generator=_Stub(),  # type: ignore[arg-type]
        manim_video=_Stub(),  # type: ignore[arg-type]
        code_sandbox=_Stub(),  # type: ignore[arg-type]
        quality_reviewer=_Stub(),  # type: ignore[arg-type]
        anti_hallucination=_Stub(),  # type: ignore[arg-type]
        ppt_generator=_Stub(),  # type: ignore[arg-type]
    )
    return cap


def test_pending_video_follow_ups_are_deterministic() -> None:
    pending = _video_resource()
    ready = _video_resource(render_status="ready")
    package = ResourcePackage(topic="t", resources=[pending, ready])

    first = ResourceGenerationCapability._video_follow_up_specs(package, "u1")
    second = ResourceGenerationCapability._video_follow_up_specs(package, "u1")

    assert first == second
    assert len(first) == 1
    assert first[0].payload == {
        "package_id": package.package_id,
        "resource_id": pending.resource_id,
        "user_id": "u1",
    }
    assert first[0].dedupe_key == f"video:{package.package_id}:{pending.resource_id}"


class _EmptyCapabilities:
    def get(self, name: str):
        return None


async def _wait_child(store: JobStore, job_id: str) -> Job:
    for _ in range(100):
        child = await store.get(job_id)
        if child is not None and child.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.PARTIAL,
        }:
            return child
        await asyncio.sleep(0.02)
    raise AssertionError("video child did not become terminal")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("render_success", "expected_job_status", "expected_render_status"),
    [
        (False, JobStatus.FAILED, "failed"),
        (True, JobStatus.SUCCEEDED, "ready"),
    ],
)
async def test_durable_video_child_updates_package_and_terminal_job(
    tmp_path,
    monkeypatch,
    render_success,
    expected_job_status,
    expected_render_status,
) -> None:
    from tutor.services import manim_render as mr_module
    from tutor.services.resource_package import store as package_store_module
    from tutor.services.resource_package.store import ResourcePackageStore

    package_store = ResourcePackageStore(tmp_path / "packages.db")
    await package_store.init()
    monkeypatch.setattr(package_store_module, "_store", package_store)
    package = ResourcePackage(topic="t", resources=[_video_resource()])
    await package_store.save(package, user_id="local-user")

    job_store = JobStore(tmp_path / "jobs.db")
    await job_store.init()
    parent = Job(
        job_id="video-parent",
        user_id="local-user",
        session_id="video-session",
        status=JobStatus.SUCCEEDED,
    )
    await job_store.save(parent)
    spec = ResourceGenerationCapability._video_follow_up_specs(
        package, parent.user_id
    )[0]
    child = (await FollowUpScheduler(job_store).enqueue(parent.job_id, (spec,)))[0]
    module_patch = monkeypatch_for_module(
        mr_module, _FakeRenderService(success=render_success)
    )
    runner = JobRunner(
        job_store=job_store,
        capability_registry=_EmptyCapabilities(),  # type: ignore[arg-type]
    )

    assert await runner.resume_pending() == 1
    terminal = await _wait_child(job_store, child.job_id)
    reloaded = await package_store.get(package.package_id)
    durable_parent = await job_store.get(parent.job_id)
    module_patch.undo()

    assert terminal.status == expected_job_status
    assert (terminal.error_log_ref is not None) is (not render_success)
    assert reloaded is not None
    assert (
        reloaded.resources[0].format_specific["render_status"]
        == expected_render_status
    )
    if not render_success:
        assert (
            reloaded.resources[0].format_specific["render_error_code"]
            == "internal_error"
        )
    assert durable_parent is not None
    assert durable_parent.status == JobStatus.SUCCEEDED
    await job_store.close()
    await package_store.close()


@pytest.mark.asyncio
async def test_stale_video_owner_cannot_overwrite_new_owner_package_or_terminal(
    tmp_path,
    monkeypatch,
) -> None:
    from tutor.services import manim_render as mr_module
    from tutor.services.resource_package import store as package_store_module
    from tutor.services.resource_package.store import ResourcePackageStore

    package_store = ResourcePackageStore(tmp_path / "packages.db")
    await package_store.init()
    monkeypatch.setattr(package_store_module, "_store", package_store)
    package = ResourcePackage(topic="t", resources=[_video_resource()])
    await package_store.save(package, user_id="local-user")
    first_store = JobStore(tmp_path / "jobs.db")
    await first_store.init()
    second_store = JobStore(tmp_path / "jobs.db")
    await second_store.init()
    parent = Job(
        job_id="lease-race-parent",
        user_id="local-user",
        status=JobStatus.SUCCEEDED,
    )
    await first_store.save(parent)
    spec = ResourceGenerationCapability._video_follow_up_specs(package, parent.user_id)[0]
    child = (await FollowUpScheduler(first_store).enqueue(parent.job_id, (spec,)))[0]
    renderer = _LeaseRaceRenderService()
    module_patch = monkeypatch_for_module(mr_module, renderer)
    old_runner = JobRunner(
        job_store=first_store,
        capability_registry=_EmptyCapabilities(),  # type: ignore[arg-type]
    )
    old_runner.CHILD_LEASE_SECONDS = 0.05
    old_runner.CHILD_HEARTBEAT_SECONDS = 10
    new_runner = JobRunner(
        job_store=second_store,
        capability_registry=_EmptyCapabilities(),  # type: ignore[arg-type]
    )
    new_runner.CHILD_LEASE_SECONDS = 2
    new_runner.CHILD_HEARTBEAT_SECONDS = 0.2

    assert await old_runner.resume_pending() == 1
    await asyncio.wait_for(renderer.first_started.wait(), timeout=2)
    old_task = old_runner._tasks[child.job_id]
    await asyncio.sleep(0.1)
    assert await new_runner.resume_pending() == 1
    new_terminal = await _wait_child(second_store, child.job_id)
    assert new_terminal.status == JobStatus.SUCCEEDED
    before_stale_finish = await package_store.get(package.package_id)
    assert before_stale_finish is not None
    assert (
        before_stale_finish.resources[0].format_specific["video_url"]
        == "https://cdn.example.com/owner-2.mp4"
    )

    renderer.release_first.set()
    await asyncio.wait_for(asyncio.shield(old_task), timeout=2)
    durable = await second_store.get(child.job_id)
    reloaded = await package_store.get(package.package_id)
    module_patch.undo()

    assert durable is not None and durable.status == JobStatus.SUCCEEDED
    assert sum(event.get("type") == "job_terminal" for event in durable.events) == 1
    assert not any(event.get("type") == "resource" for event in durable.events)
    assert reloaded is not None
    assert (
        reloaded.resources[0].format_specific["video_url"]
        == "https://cdn.example.com/owner-2.mp4"
    )
    await second_store.close()
    await first_store.close()
    await package_store.close()


@pytest.mark.asyncio
async def test_claim_guard_holds_generation_stable_through_resource_commit(
    tmp_path,
) -> None:
    from tutor.services.resource_package.store import ResourcePackageStore

    package_store = ResourcePackageStore(tmp_path / "packages.db")
    await package_store.init()
    package = ResourcePackage(topic="t", resources=[_video_resource()])
    await package_store.save(package, user_id="local-user")
    first_store = JobStore(tmp_path / "jobs.db")
    await first_store.init()
    second_store = JobStore(tmp_path / "jobs.db")
    await second_store.init()
    parent = Job(
        job_id="guard-parent",
        user_id="local-user",
        status=JobStatus.SUCCEEDED,
    )
    await first_store.save(parent)
    spec = ResourceGenerationCapability._video_follow_up_specs(package, parent.user_id)[0]
    child = (await FollowUpScheduler(first_store).enqueue(parent.job_id, (spec,)))[0]
    old = await first_store.claim_child(
        child.job_id,
        owner="old-owner",
        lease_seconds=60,
    )
    assert old is not None
    snapshot = await package_store.get(package.package_id)
    assert snapshot is not None
    stale_resource = snapshot.resources[0]
    stale_resource.format_specific["render_status"] = "ready"
    stale_resource.format_specific["video_url"] = "https://cdn.example.com/old.mp4"
    operation_entered = asyncio.Event()
    release_operation = asyncio.Event()

    async def persist_stale_resource() -> None:
        operation_entered.set()
        await release_operation.wait()
        await package_store.update_resource(
            package.package_id,
            stale_resource,
            user_id="local-user",
        )

    guarded_write = asyncio.create_task(
        first_store.run_if_current_claim(
            child.job_id,
            owner="old-owner",
            generation=old.claim_generation,
            operation=persist_stale_resource,
        )
    )
    await asyncio.wait_for(operation_entered.wait(), timeout=2)
    assert old.claim_expires_at is not None
    replacement_claim = asyncio.create_task(
        second_store.claim_child(
            child.job_id,
            owner="new-owner",
            lease_seconds=2,
            now=old.claim_expires_at + timedelta(seconds=1),
        )
    )
    await asyncio.sleep(0.05)
    assert not replacement_claim.done()

    release_operation.set()
    assert await asyncio.wait_for(guarded_write, timeout=2)
    new = await asyncio.wait_for(replacement_claim, timeout=2)
    assert new is not None
    assert new.claim_generation == old.claim_generation + 1
    reloaded = await package_store.get(package.package_id)
    assert reloaded is not None
    assert (
        reloaded.resources[0].format_specific["video_url"]
        == "https://cdn.example.com/old.mp4"
    )

    await second_store.close()
    await first_store.close()
    await package_store.close()


@pytest.mark.asyncio
async def test_resource_level_updates_do_not_overwrite_sibling_video_results(
    tmp_path,
) -> None:
    from tutor.services.resource_package.store import ResourcePackageStore

    store = ResourcePackageStore(tmp_path / "packages.db")
    await store.init()
    second_store = ResourcePackageStore(tmp_path / "packages.db")
    await second_store.init()
    package = ResourcePackage(
        topic="t",
        resources=[_video_resource(), _video_resource()],
    )
    await store.save(package, user_id="local-user")
    first_snapshot = await store.get(package.package_id)
    second_snapshot = await store.get(package.package_id)
    assert first_snapshot is not None and second_snapshot is not None
    first = first_snapshot.resources[0]
    second = second_snapshot.resources[1]
    first.format_specific["render_status"] = "ready"
    first.format_specific["video_url"] = "https://cdn.example.com/first.mp4"
    second.format_specific["render_status"] = "failed"
    second.format_specific["render_error_code"] = "VIDEO_RENDER_FAILED"

    await asyncio.gather(
        store.update_resource(package.package_id, first, user_id="local-user"),
        second_store.update_resource(
            package.package_id,
            second,
            user_id="local-user",
        ),
    )

    reloaded = await store.get(package.package_id)
    assert reloaded is not None
    by_id = {item.resource_id: item for item in reloaded.resources}
    assert by_id[first.resource_id].format_specific["render_status"] == "ready"
    assert by_id[second.resource_id].format_specific["render_status"] == "failed"
    await second_store.close()
    await store.close()


@pytest.mark.asyncio
async def test_video_child_fails_when_atomic_resource_persistence_fails(
    tmp_path,
    monkeypatch,
) -> None:
    from tutor.services import manim_render as mr_module
    from tutor.services.resource_package import store as package_store_module
    from tutor.services.resource_package.store import ResourcePackageStore

    package_store = ResourcePackageStore(tmp_path / "packages.db")
    await package_store.init()
    monkeypatch.setattr(package_store_module, "_store", package_store)
    package = ResourcePackage(topic="t", resources=[_video_resource()])
    await package_store.save(package, user_id="local-user")
    job_store = JobStore(tmp_path / "jobs.db")
    await job_store.init()
    parent = Job(job_id="persist-parent", user_id="local-user", status=JobStatus.SUCCEEDED)
    await job_store.save(parent)
    spec = ResourceGenerationCapability._video_follow_up_specs(package, parent.user_id)[0]
    child = (await FollowUpScheduler(job_store).enqueue(parent.job_id, (spec,)))[0]
    module_patch = monkeypatch_for_module(mr_module, _FakeRenderService(success=True))

    async def fail_update(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(package_store, "update_resource", fail_update, raising=False)
    runner = JobRunner(
        job_store=job_store,
        capability_registry=_EmptyCapabilities(),  # type: ignore[arg-type]
    )
    assert await runner.resume_pending() == 1
    terminal = await _wait_child(job_store, child.job_id)
    module_patch.undo()

    assert terminal.status == JobStatus.FAILED
    assert terminal.error_log_ref is not None
    reloaded = await package_store.get(package.package_id)
    assert reloaded is not None
    assert reloaded.resources[0].format_specific["render_status"] == "pending"
    await job_store.close()
    await package_store.close()


@pytest.mark.asyncio
async def test_cross_user_video_payload_cannot_render_or_mutate_package(
    tmp_path,
    monkeypatch,
) -> None:
    from tutor.services import manim_render as mr_module
    from tutor.services.resource_package import store as package_store_module
    from tutor.services.resource_package.store import ResourcePackageStore

    package_store = ResourcePackageStore(tmp_path / "packages.db")
    await package_store.init()
    monkeypatch.setattr(package_store_module, "_store", package_store)
    package = ResourcePackage(topic="private", resources=[_video_resource()])
    await package_store.save(package, user_id="owner-b")
    job_store = JobStore(tmp_path / "jobs.db")
    await job_store.init()
    parent = Job(job_id="owner-parent", user_id="owner-a", status=JobStatus.SUCCEEDED)
    await job_store.save(parent)
    spec = FollowUpTaskSpec(
        kind="video_render",
        payload={"package_id": package.package_id, "resource_id": package.resources[0].resource_id},
        dedupe_key=f"video:{package.package_id}:{package.resources[0].resource_id}",
    )
    child = (await FollowUpScheduler(job_store).enqueue(parent.job_id, (spec,)))[0]
    fake = _FakeRenderService(success=True)
    module_patch = monkeypatch_for_module(mr_module, fake)
    runner = JobRunner(
        job_store=job_store,
        capability_registry=_EmptyCapabilities(),  # type: ignore[arg-type]
    )

    assert await runner.resume_pending() == 1
    terminal = await _wait_child(job_store, child.job_id)
    module_patch.undo()
    reloaded = await package_store.get(package.package_id)

    assert terminal.status == JobStatus.FAILED
    assert fake.calls == []
    assert reloaded is not None
    assert reloaded.resources[0].format_specific["render_status"] == "pending"
    await job_store.close()
    await package_store.close()


@pytest.mark.asyncio
async def test_atomic_resource_update_rejects_wrong_owner(tmp_path) -> None:
    from tutor.services.resource_package.store import ResourcePackageStore

    store = ResourcePackageStore(tmp_path / "packages.db")
    await store.init()
    package = ResourcePackage(topic="private", resources=[_video_resource()])
    await store.save(package, user_id="owner-b")
    snapshot = await store.get(package.package_id)
    assert snapshot is not None
    resource = snapshot.resources[0]
    resource.format_specific["render_status"] = "ready"

    with pytest.raises((KeyError, PermissionError)):
        await store.update_resource(
            package.package_id,
            resource,
            user_id="owner-a",
        )
    reloaded = await store.get(package.package_id)
    assert reloaded is not None
    assert reloaded.resources[0].format_specific["render_status"] == "pending"
    await store.close()


@pytest.mark.asyncio
async def test_two_video_children_update_different_resources_in_one_package_concurrently(
    tmp_path,
    monkeypatch,
) -> None:
    from tutor.services import manim_render as mr_module
    from tutor.services.resource_package import store as package_store_module
    from tutor.services.resource_package.store import ResourcePackageStore

    package_store = ResourcePackageStore(tmp_path / "packages.db")
    await package_store.init()
    monkeypatch.setattr(package_store_module, "_store", package_store)
    ready_resource = _video_resource()
    failed_resource = _video_resource()
    ready_resource.format_specific["scene_class"] = "ReadyScene"
    failed_resource.format_specific["scene_class"] = "FailScene"
    package = ResourcePackage(
        topic="t",
        resources=[ready_resource, failed_resource],
    )
    await package_store.save(package, user_id="local-user")
    job_store = JobStore(tmp_path / "jobs.db")
    await job_store.init()
    parent = Job(job_id="concurrent-parent", user_id="local-user", status=JobStatus.SUCCEEDED)
    await job_store.save(parent)
    specs = ResourceGenerationCapability._video_follow_up_specs(
        package,
        parent.user_id,
    )
    children = await FollowUpScheduler(job_store).enqueue(parent.job_id, specs)
    module_patch = monkeypatch_for_module(mr_module, _PerSceneRenderService())
    runner = JobRunner(
        job_store=job_store,
        capability_registry=_EmptyCapabilities(),  # type: ignore[arg-type]
    )

    assert await runner.resume_pending() == 2
    terminal = await asyncio.gather(
        *(_wait_child(job_store, child.job_id) for child in children)
    )
    module_patch.undo()
    assert {job.status for job in terminal} == {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
    }
    reloaded = await package_store.get(package.package_id)
    assert reloaded is not None
    statuses = {
        item.resource_id: item.format_specific["render_status"]
        for item in reloaded.resources
    }
    assert statuses[ready_resource.resource_id] == "ready"
    assert statuses[failed_resource.resource_id] == "failed"
    await job_store.close()
    await package_store.close()


@pytest.mark.asyncio
async def test_render_success_emits_resource_event_with_ready_status() -> None:
    """When the render succeeds, ``_render_one_video`` must emit a
    fresh ``RESOURCE`` event with ``render_status="ready"`` and the
    new ``video_url``, so the frontend card swaps the placeholder.
    """
    cap = _cap()
    fake = _FakeRenderService(success=True)
    from tutor.services import manim_render as mr_module
    monkey = monkeypatch_for_module(mr_module, fake)

    bus = StreamBus()
    q = bus.subscribe()
    res = _video_resource()
    pkg = ResourcePackage(topic="t", resources=[res])
    ctx = UnifiedContext(language="zh")

    await cap._render_one_video(res, pkg, ctx, bus)
    monkey.undo()

    # Drain events until we see the RESOURCE one — the helper emits
    # an observation first, then RESOURCE.
    async def _wait_resource() -> Any:
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            evt = await asyncio.wait_for(q.get(), timeout=2.0)
            if evt.type.value == "resource":
                return evt
        raise AssertionError("no RESOURCE event received within 2s")

    evt = await _wait_resource()
    md = evt.metadata
    assert md["resource_id"] == res.resource_id
    assert md["resource_type"] == "video"
    # The full resource dict is now updated to "ready" + has video_url
    payload = md["resource"]
    assert isinstance(payload, dict), (
        f"resource payload must be a dict, got {type(payload).__name__}: "
        f"{payload!r:.200}"
    )
    assert payload["format_specific"]["render_status"] == "ready"
    assert payload["format_specific"]["video_url"] == "https://cdn.example.com/v.mp4"


@pytest.mark.asyncio
async def test_render_success_streams_portable_artifact_key(
    tmp_path, monkeypatch
) -> None:
    from tutor.services import manim_render as mr_module
    from tutor.services.config.settings import get_settings

    data_dir = tmp_path / "data"
    video_path = data_dir / "manim_videos" / "v.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"video")
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)

    cap = _cap()
    fake = _FakeRenderService(success=True, video_path=video_path)
    module_patch = monkeypatch_for_module(mr_module, fake)
    bus = StreamBus()
    queue = bus.subscribe()
    resource = _video_resource()
    package = ResourcePackage(topic="t", resources=[resource])

    await cap._render_one_video(
        resource, package, UnifiedContext(language="zh"), bus
    )
    module_patch.undo()

    while True:
        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        if event.type.value == "resource":
            break
    streamed = event.metadata["resource"]["format_specific"]
    assert resource.format_specific["artifact_key"] == "manim_videos/v.mp4"
    assert streamed["artifact_key"] == "manim_videos/v.mp4"
    assert "mp4_path" not in resource.format_specific
    assert "mp4_path" not in streamed


@pytest.mark.asyncio
async def test_render_failure_emits_resource_event_with_failed_status() -> None:
    """A render failure must emit a redacted ``RESOURCE`` so the right
    pane shows '渲染失败' instead of a forever-pending placeholder."""
    cap = _cap()
    fake = _FakeRenderService(success=False)
    from tutor.services import manim_render as mr_module
    monkey = monkeypatch_for_module(mr_module, fake)

    bus = StreamBus()
    q = bus.subscribe()
    res = _video_resource()
    pkg = ResourcePackage(topic="t", resources=[res])
    ctx = UnifiedContext(language="zh")

    await cap._render_one_video(res, pkg, ctx, bus)
    monkey.undo()

    async def _wait_resource() -> Any:
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            evt = await asyncio.wait_for(q.get(), timeout=2.0)
            if evt.type.value == "resource":
                return evt
        raise AssertionError("no RESOURCE event received within 2s")

    evt = await _wait_resource()
    md = evt.metadata
    payload = md["resource"]
    assert payload["format_specific"]["render_status"] == "failed"
    assert payload["format_specific"]["render_error_code"] == "internal_error"
    assert (
        payload["format_specific"]["render_error"]
        == "Video rendering failed internally"
    )
    assert "manim exit 1" not in str(payload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MonkeyPatch:
    """Minimal ``monkeypatch.setattr`` replacement so the tests don't
    need to take ``monkeypatch`` as a fixture (avoids pytest fixture
    ordering quirks)."""

    _MISSING = object()

    def __init__(self) -> None:
        self._undo_stack: list[Any] = []

    def setattr(self, target: Any, name: str, value: Any) -> None:
        old = getattr(target, name, self._MISSING)
        setattr(target, name, value)
        self._undo_stack.append((target, name, old))

    def undo(self) -> None:
        while self._undo_stack:
            target, name, old = self._undo_stack.pop()
            if old is self._MISSING:
                with contextlib.suppress(AttributeError):
                    delattr(target, name)
            else:
                setattr(target, name, old)


def monkeypatch_for_module(mod: Any, fake: Any) -> _MonkeyPatch:
    """Replace ``get_manim_render_service`` in the manim_render
    sub-module with a callable that returns ``fake``.

    The capability lazy-imports from
    ``tutor.services.manim_render.service``, so we must monkeypatch
    THAT submodule, not the package ``__init__``.
    """
    m = _MonkeyPatch()
    # Patch both the submodule the capability imports from and the
    # package-level alias, just in case.
    from tutor.services.manim_render import service as service_mod
    m.setattr(service_mod, "get_manim_render_service", lambda: fake)
    m.setattr(mod, "get_manim_render_service", lambda: fake)
    return m


class _ScriptedRepairAgent:
    def __init__(self, candidates: list[str]) -> None:
        self.candidates = list(candidates)
        self.calls = []

    async def regenerate(self, context, failed_code, failure, runtime):  # type: ignore[no-untyped-def]
        self.calls.append((failed_code, failure, runtime))
        if not self.candidates:
            raise RuntimeError("provider-token=private-value")
        return self.candidates.pop(0)


REPAIR_GOOD_CODE = """from manim import *

class MainScene(Scene):
    def construct(self):
        dot = Dot()
        self.play(Create(dot), run_time=0.5)
"""


@pytest.mark.asyncio
async def test_video_repair_regenerates_twice_after_validation_then_renders_once(
    tmp_path,
) -> None:
    from tutor.services.jobs.follow_up import VideoRepairFollowUpCapability
    from tutor.services.resource_package.store import ResourcePackageStore

    store = ResourcePackageStore(tmp_path / "packages.db")
    await store.init()
    original = REPAIR_GOOD_CODE.replace("0.5", "0")
    resource = Resource(
        resource_id="repair-video",
        type=ResourceType.VIDEO,
        title="repair",
        format_specific={
            "manim_code": original,
            "scene_class": "MainScene",
            "render_status": "failed",
            "render_error_code": "process_exit",
            "render_error": "original failure",
            "source_revision": 4,
            "repair_status": "pending",
            "repair_job_id": "repair-child",
        },
    )
    package = ResourcePackage(package_id="repair-package", topic="t", resources=[resource])
    await store.save(package, user_id="owner")
    invalid = REPAIR_GOOD_CODE.replace("run_time=0.5", "run_time=0")
    agent = _ScriptedRepairAgent([invalid, REPAIR_GOOD_CODE])
    renderer = _FakeRenderService(success=True)

    async def claim_guard(operation):
        await operation()
        return True

    capability = VideoRepairFollowUpCapability(
        package_store=store,
        repair_agent=agent,
        render_service=renderer,
        runtime_namespace={
            "Scene": object(),
            "Dot": object(),
            "Create": object(),
        },
        runtime_versions={"python": "3.11", "manim": "0.20"},
    )
    result = await capability.run(
        UnifiedContext(
            job_id="repair-child",
            user_id="owner",
            metadata={
                "package_id": package.package_id,
                "resource_id": resource.resource_id,
                "failed_revision": 4,
                "_claim_validator": lambda: _async_true(),
                "_claim_guard": claim_guard,
            },
        ),
        StreamBus(),
    )

    reloaded = await store.get_resource(resource.resource_id)
    assert result.payload["source_revision"] == 5
    assert len(agent.calls) == 2
    assert agent.calls[1][1].error_code == "candidate_validation_failed"
    assert len(renderer.calls) == 1
    assert reloaded is not None
    assert reloaded.format_specific["manim_code"] == REPAIR_GOOD_CODE
    assert reloaded.format_specific["render_status"] == "ready"
    assert reloaded.format_specific["repair_status"] == "ready"
    assert reloaded.format_specific["source_revision"] == 5
    await store.close()


@pytest.mark.asyncio
async def test_video_repair_failure_preserves_original_source_and_error_with_history(
    tmp_path,
) -> None:
    from tutor.services.jobs.follow_up import VideoRepairFollowUpCapability
    from tutor.services.resource_package.store import ResourcePackageStore

    store = ResourcePackageStore(tmp_path / "packages.db")
    await store.init()
    resource = Resource(
        resource_id="failed-repair-video",
        type=ResourceType.VIDEO,
        title="repair",
        format_specific={
            "manim_code": "ORIGINAL SOURCE",
            "scene_class": "MainScene",
            "render_status": "failed",
            "render_error_code": "process_exit",
            "render_error": "ORIGINAL ERROR",
            "source_revision": 1,
            "repair_status": "pending",
            "repair_job_id": "failed-child",
            "repair_history": [
                {
                    "job_id": "old-child",
                    "failed_revision": 0,
                    "status": "failed",
                    "summary": "provider-token=private-value " + ("x" * 1000),
                    "log_artifact_key": "C:\\private\\raw.log",
                }
            ],
        },
    )
    package = ResourcePackage(package_id="failed-package", topic="t", resources=[resource])
    await store.save(package, user_id="owner")

    async def claim_guard(operation):
        await operation()
        return True

    capability = VideoRepairFollowUpCapability(
        package_store=store,
        repair_agent=_ScriptedRepairAgent([]),
        render_service=_FakeRenderService(success=True),
        runtime_namespace={"Scene": object()},
        runtime_versions={"python": "3.11"},
    )
    with pytest.raises(RuntimeError, match="Video repair failed"):
        await capability.run(
            UnifiedContext(
                job_id="failed-child",
                user_id="owner",
                metadata={
                    "package_id": package.package_id,
                    "resource_id": resource.resource_id,
                    "failed_revision": 1,
                    "_claim_validator": lambda: _async_true(),
                    "_claim_guard": claim_guard,
                },
            ),
            StreamBus(),
        )

    reloaded = await store.get_resource(resource.resource_id)
    assert reloaded is not None
    payload = reloaded.format_specific
    assert payload["manim_code"] == "ORIGINAL SOURCE"
    assert payload["render_error"] == "ORIGINAL ERROR"
    assert payload["render_status"] == "failed"
    assert payload["repair_status"] == "failed"
    assert len(payload["repair_history"]) == 2
    assert payload["repair_history"][-1]["log_artifact_key"]
    assert len(payload["repair_history"][0]["summary"]) <= 200
    assert "C:\\private" not in str(payload["repair_history"])
    assert "private-value" not in str(payload["repair_history"])
    await store.close()


@pytest.mark.asyncio
async def test_next_manual_repair_uses_latest_failed_candidate_and_diagnostic(
    tmp_path,
) -> None:
    from tutor.services.jobs.follow_up import VideoRepairFollowUpCapability
    from tutor.services.resource_package.store import ResourcePackageStore

    store = ResourcePackageStore(tmp_path / "packages.db")
    await store.init()
    original = "ORIGINAL LAST-KNOWN FAILED SOURCE"
    resource = Resource(
        resource_id="candidate-handoff-video",
        type=ResourceType.VIDEO,
        title="repair",
        format_specific={
            "manim_code": original,
            "scene_class": "MainScene",
            "render_status": "failed",
            "render_error_code": "process_exit",
            "render_error": "ORIGINAL FAILURE",
            "source_revision": 7,
            "repair_status": "running",
            "repair_job_id": "candidate-child-1",
        },
    )
    package = ResourcePackage(
        package_id="candidate-handoff-package",
        topic="t",
        resources=[resource],
    )
    await store.save(package, user_id="owner")
    invalid_one = REPAIR_GOOD_CODE.replace("run_time=0.5", "run_time=0")
    invalid_two = invalid_one.replace(
        "dot = Dot()",
        "dot = Dot()\n        second_candidate_marker = 2",
    )
    first_agent = _ScriptedRepairAgent([invalid_one, invalid_two])

    async def claim_guard(operation):
        await operation()
        return True

    first_capability = VideoRepairFollowUpCapability(
        package_store=store,
        repair_agent=first_agent,
        render_service=_FakeRenderService(success=True),
        runtime_namespace={
            "Scene": object(),
            "Dot": object(),
            "Create": object(),
        },
        runtime_versions={"python": "3.11", "manim": "0.20"},
    )
    with pytest.raises(RuntimeError, match="Video repair failed"):
        await first_capability.run(
            UnifiedContext(
                job_id="candidate-child-1",
                user_id="owner",
                metadata={
                    "package_id": package.package_id,
                    "resource_id": resource.resource_id,
                    "failed_revision": 7,
                    "_claim_validator": lambda: _async_true(),
                    "_claim_guard": claim_guard,
                },
            ),
            StreamBus(),
        )

    after_first = await store.get_resource(resource.resource_id)
    assert after_first is not None
    assert after_first.format_specific["manim_code"] == original
    assert after_first.format_specific["source_revision"] == 7
    assert after_first.format_specific["repair_candidate_code"] == invalid_two
    candidate_failure = after_first.format_specific["repair_candidate_failure"]
    assert candidate_failure["error_code"] == "candidate_validation_failed"
    assert "NON_POSITIVE_RUN_TIME" in "\n".join(
        candidate_failure["traceback_tail"]
    )

    second_agent = _ScriptedRepairAgent([REPAIR_GOOD_CODE])
    second_renderer = _FakeRenderService(success=True)
    second_capability = VideoRepairFollowUpCapability(
        package_store=store,
        repair_agent=second_agent,
        render_service=second_renderer,
        runtime_namespace={
            "Scene": object(),
            "Dot": object(),
            "Create": object(),
        },
        runtime_versions={"python": "3.11", "manim": "0.20"},
    )
    await second_capability.run(
        UnifiedContext(
            job_id="candidate-child-2",
            user_id="owner",
            metadata={
                "package_id": package.package_id,
                "resource_id": resource.resource_id,
                "failed_revision": 7,
                "expected_repair_job_id": "candidate-child-1",
                "_claim_validator": lambda: _async_true(),
                "_claim_guard": claim_guard,
            },
        ),
        StreamBus(),
    )

    assert second_agent.calls[0][0] == invalid_two
    assert second_agent.calls[0][1].error_code == "candidate_validation_failed"
    assert "NON_POSITIVE_RUN_TIME" in "\n".join(
        second_agent.calls[0][1].traceback_tail
    )
    completed = await store.get_resource(resource.resource_id)
    assert completed is not None
    assert completed.format_specific["manim_code"] == REPAIR_GOOD_CODE
    assert completed.format_specific["source_revision"] == 8
    assert "repair_candidate_code" not in completed.format_specific
    assert "repair_candidate_failure" not in completed.format_specific
    await store.close()


@pytest.mark.asyncio
async def test_renderer_exception_persists_candidate_for_next_manual_repair(
    tmp_path,
) -> None:
    from tutor.services.jobs.follow_up import VideoRepairFollowUpCapability
    from tutor.services.resource_package.schema import public_resource_dump
    from tutor.services.resource_package.store import ResourcePackageStore

    class RaisingRenderer:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def render(self, *, code, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(code)
            raise RuntimeError(
                "provider-token=SECRET_RENDERER C:\\private\\renderer.py"
            )

    store = ResourcePackageStore(tmp_path / "packages.db")
    await store.init()
    resource = Resource(
        resource_id="renderer-exception-video",
        type=ResourceType.VIDEO,
        title="repair",
        format_specific={
            "manim_code": "ORIGINAL SOURCE",
            "scene_class": "MainScene",
            "render_status": "failed",
            "render_error": "ORIGINAL FAILURE",
            "source_revision": 5,
            "repair_status": "running",
            "repair_job_id": "renderer-child-1",
        },
    )
    package = ResourcePackage(
        package_id="renderer-exception-package",
        topic="t",
        resources=[resource],
    )
    await store.save(package, user_id="owner")

    async def claim_guard(operation):
        await operation()
        return True

    renderer = RaisingRenderer()
    first_agent = _ScriptedRepairAgent([REPAIR_GOOD_CODE])
    first_capability = VideoRepairFollowUpCapability(
        package_store=store,
        repair_agent=first_agent,
        render_service=renderer,
        runtime_namespace={
            "Scene": object(),
            "Dot": object(),
            "Create": object(),
        },
        runtime_versions={"python": "3.11", "manim": "0.20"},
    )
    with pytest.raises(RuntimeError, match="Video repair failed"):
        await first_capability.run(
            UnifiedContext(
                job_id="renderer-child-1",
                user_id="owner",
                metadata={
                    "package_id": package.package_id,
                    "resource_id": resource.resource_id,
                    "failed_revision": 5,
                    "_claim_validator": lambda: _async_true(),
                    "_claim_guard": claim_guard,
                },
            ),
            StreamBus(),
        )

    failed = await store.get_resource(resource.resource_id)
    assert failed is not None
    assert renderer.calls == [REPAIR_GOOD_CODE]
    assert failed.format_specific["manim_code"] == "ORIGINAL SOURCE"
    assert failed.format_specific["source_revision"] == 5
    assert failed.format_specific["repair_candidate_code"] == REPAIR_GOOD_CODE
    failure = failed.format_specific["repair_candidate_failure"]
    assert failure["error_code"] == "repair_render_failed"
    assert "SECRET_RENDERER" not in str(failure)
    assert "C:\\private" not in str(failure)
    public = public_resource_dump(failed)
    assert "SECRET_RENDERER" not in str(public)
    assert "C:\\private" not in str(public)

    second_agent = _ScriptedRepairAgent([REPAIR_GOOD_CODE])
    second_capability = VideoRepairFollowUpCapability(
        package_store=store,
        repair_agent=second_agent,
        render_service=_FakeRenderService(success=True),
        runtime_namespace={
            "Scene": object(),
            "Dot": object(),
            "Create": object(),
        },
        runtime_versions={"python": "3.11", "manim": "0.20"},
    )
    await second_capability.run(
        UnifiedContext(
            job_id="renderer-child-2",
            user_id="owner",
            metadata={
                "package_id": package.package_id,
                "resource_id": resource.resource_id,
                "failed_revision": 5,
                "expected_repair_job_id": "renderer-child-1",
                "_claim_validator": lambda: _async_true(),
                "_claim_guard": claim_guard,
            },
        ),
        StreamBus(),
    )

    assert second_agent.calls[0][0] == REPAIR_GOOD_CODE
    assert second_agent.calls[0][1].error_code == "repair_render_failed"
    await store.close()


@pytest.mark.asyncio
async def test_generation_failure_before_candidate_retains_previous_candidate_state(
    tmp_path,
) -> None:
    from tutor.services.jobs.follow_up import VideoRepairFollowUpCapability
    from tutor.services.resource_package.store import ResourcePackageStore

    store = ResourcePackageStore(tmp_path / "packages.db")
    await store.init()
    previous_failure = {
        "error_code": "candidate_validation_failed",
        "summary": "previous candidate invalid",
        "traceback_tail": ["NON_POSITIVE_RUN_TIME"],
    }
    resource = Resource(
        resource_id="candidate-retention-video",
        type=ResourceType.VIDEO,
        title="repair",
        format_specific={
            "manim_code": "ORIGINAL SOURCE",
            "render_status": "failed",
            "render_error": "ORIGINAL FAILURE",
            "source_revision": 1,
            "repair_status": "running",
            "repair_job_id": "candidate-retention-child",
            "repair_candidate_code": "PREVIOUS CANDIDATE",
            "repair_candidate_failure": previous_failure,
        },
    )
    package = ResourcePackage(
        package_id="candidate-retention-package",
        topic="t",
        resources=[resource],
    )
    await store.save(package, user_id="owner")

    async def claim_guard(operation):
        await operation()
        return True

    capability = VideoRepairFollowUpCapability(
        package_store=store,
        repair_agent=_ScriptedRepairAgent([]),
        render_service=_FakeRenderService(success=True),
        runtime_namespace={"Scene": object()},
        runtime_versions={"python": "3.11"},
    )
    with pytest.raises(RuntimeError, match="Video repair failed"):
        await capability.run(
            UnifiedContext(
                job_id="candidate-retention-child",
                user_id="owner",
                metadata={
                    "package_id": package.package_id,
                    "resource_id": resource.resource_id,
                    "failed_revision": 1,
                    "_claim_validator": lambda: _async_true(),
                    "_claim_guard": claim_guard,
                },
            ),
            StreamBus(),
        )

    reloaded = await store.get_resource(resource.resource_id)
    assert reloaded is not None
    assert reloaded.format_specific["repair_candidate_code"] == "PREVIOUS CANDIDATE"
    assert reloaded.format_specific["repair_candidate_failure"] == previous_failure
    await store.close()


def test_repair_history_append_is_idempotent_per_job_outcome() -> None:
    from tutor.services.jobs.follow_up import _append_repair_history
    from tutor.services.manim_render.executor import RenderFailure

    payload: dict[str, Any] = {}
    first = RenderFailure(
        error_code="first",
        summary="first failure",
    )
    replacement = RenderFailure(
        error_code="replacement",
        summary="replacement failure",
    )

    _append_repair_history(
        payload,
        job_id="same-child",
        failed_revision=4,
        status="failed",
        failure=first,
    )
    _append_repair_history(
        payload,
        job_id="same-child",
        failed_revision=4,
        status="failed",
        failure=replacement,
    )

    assert len(payload["repair_history"]) == 1
    assert payload["repair_history"][0]["error_code"] == "replacement"


async def _async_true() -> bool:
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize("prebound", [False, True])
async def test_pending_video_repair_child_resumes_after_runner_refresh(
    tmp_path,
    monkeypatch,
    prebound,
) -> None:
    from tutor.agents.resource.manim_repair import ManimRepairAgent
    from tutor.services import manim_render as mr_module
    from tutor.services.jobs.follow_up import VideoRepairFollowUpCapability
    from tutor.services.resource_package import store as package_store_module
    from tutor.services.resource_package.store import ResourcePackageStore

    package_store = ResourcePackageStore(tmp_path / "packages.db")
    await package_store.init()
    monkeypatch.setattr(package_store_module, "_store", package_store)
    resource = Resource(
        resource_id="resume-repair-video",
        type=ResourceType.VIDEO,
        title="repair",
        format_specific={
            "manim_code": REPAIR_GOOD_CODE.replace("0.5", "0"),
            "scene_class": "MainScene",
            "render_status": "failed",
            "render_error": "original",
            "source_revision": 2,
        },
    )
    package = ResourcePackage(package_id="resume-repair-package", topic="t", resources=[resource])
    await package_store.save(package, user_id="owner")
    job_store = JobStore(tmp_path / "jobs.db")
    await job_store.init()
    parent = Job(job_id="resume-repair-parent", user_id="owner", status=JobStatus.SUCCEEDED)
    await job_store.save(parent)
    spec = FollowUpTaskSpec(
        kind="video_repair_render",
        dedupe_key="resume-repair:2:1",
        payload={
            "package_id": package.package_id,
            "resource_id": resource.resource_id,
            "user_id": "owner",
            "failed_revision": 2,
            "expected_repair_job_id": None,
        },
    )
    child = (await FollowUpScheduler(job_store).enqueue(parent.job_id, (spec,)))[0]
    if prebound:
        resource.format_specific.update(
            {"repair_status": "running", "repair_job_id": child.job_id}
        )
        await package_store.update_resource(
            package.package_id,
            resource,
            user_id="owner",
        )

    async def regenerate(self, context, failed_code, failure, runtime):  # type: ignore[no-untyped-def]
        return REPAIR_GOOD_CODE

    monkeypatch.setattr(ManimRepairAgent, "regenerate", regenerate)
    monkeypatch.setattr(
        VideoRepairFollowUpCapability,
        "_runtime",
        lambda self: (
            {"python": "3.11", "manim": "0.20"},
            {"Scene": object(), "Dot": object(), "Create": object()},
        ),
    )
    module_patch = monkeypatch_for_module(mr_module, _FakeRenderService(success=True))
    runner = JobRunner(job_store=job_store, capability_registry=_EmptyCapabilities())  # type: ignore[arg-type]

    assert await runner.resume_pending() == 1
    terminal = await _wait_child(job_store, child.job_id)
    module_patch.undo()
    reloaded = await package_store.get_resource(resource.resource_id)
    assert terminal.status == JobStatus.SUCCEEDED
    assert reloaded is not None
    assert reloaded.format_specific["source_revision"] == 3
    assert reloaded.format_specific["repair_status"] == "ready"
    await job_store.close()
    await package_store.close()


@pytest.mark.asyncio
async def test_video_repair_resume_after_success_commit_terminalizes_without_work(
    tmp_path,
    monkeypatch,
) -> None:
    from tutor.agents.resource.manim_repair import ManimRepairAgent
    from tutor.services import manim_render as mr_module
    from tutor.services.resource_package import store as package_store_module
    from tutor.services.resource_package.store import ResourcePackageStore

    package_store = ResourcePackageStore(tmp_path / "packages.db")
    await package_store.init()
    monkeypatch.setattr(package_store_module, "_store", package_store)
    resource = Resource(
        resource_id="committed-ready-video",
        type=ResourceType.VIDEO,
        title="repair",
        format_specific={
            "manim_code": REPAIR_GOOD_CODE,
            "scene_class": "MainScene",
            "render_status": "failed",
            "render_error": "original",
            "source_revision": 2,
        },
    )
    package = ResourcePackage(
        package_id="committed-ready-package",
        topic="t",
        resources=[resource],
    )
    await package_store.save(package, user_id="owner")
    job_store = JobStore(tmp_path / "jobs.db")
    await job_store.init()
    parent = Job(
        job_id="committed-ready-parent",
        user_id="owner",
        status=JobStatus.SUCCEEDED,
    )
    await job_store.save(parent)
    child = (
        await FollowUpScheduler(job_store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_repair_render",
                    dedupe_key="committed-ready:2:1",
                    payload={
                        "package_id": package.package_id,
                        "resource_id": resource.resource_id,
                        "user_id": "owner",
                        "failed_revision": 2,
                        "expected_repair_job_id": None,
                    },
                ),
            ),
        )
    )[0]
    resource.format_specific.update(
        {
            "render_status": "ready",
            "repair_status": "ready",
            "repair_job_id": child.job_id,
            "source_revision": 3,
            "video_url": "https://cdn.example.com/repaired.mp4",
            "artifact_key": "manim_videos/repaired.mp4",
            "repair_history": [
                {
                    "job_id": child.job_id,
                    "failed_revision": 2,
                    "status": "ready",
                }
            ],
        }
    )
    await package_store.update_resource(
        package.package_id,
        resource,
        user_id="owner",
    )
    agent_calls: list[str] = []

    async def regenerate(self, context, failed_code, failure, runtime):  # type: ignore[no-untyped-def]
        agent_calls.append(failed_code)
        raise AssertionError("repair agent must not run after success commit")

    monkeypatch.setattr(ManimRepairAgent, "regenerate", regenerate)
    renderer = _FakeRenderService(success=True)
    module_patch = monkeypatch_for_module(mr_module, renderer)
    runner = JobRunner(
        job_store=job_store,
        capability_registry=_EmptyCapabilities(),  # type: ignore[arg-type]
    )

    assert await runner.resume_pending() == 1
    terminal = await _wait_child(job_store, child.job_id)
    module_patch.undo()
    assert terminal.status == JobStatus.SUCCEEDED
    assert agent_calls == []
    assert renderer.calls == []
    await job_store.close()
    await package_store.close()


@pytest.mark.asyncio
async def test_video_repair_resume_after_failure_commit_terminalizes_without_work(
    tmp_path,
    monkeypatch,
) -> None:
    from tutor.agents.resource.manim_repair import ManimRepairAgent
    from tutor.services import manim_render as mr_module
    from tutor.services.resource_package import store as package_store_module
    from tutor.services.resource_package.store import ResourcePackageStore

    package_store = ResourcePackageStore(tmp_path / "packages.db")
    await package_store.init()
    monkeypatch.setattr(package_store_module, "_store", package_store)
    resource = Resource(
        resource_id="committed-failed-video",
        type=ResourceType.VIDEO,
        title="repair",
        format_specific={
            "manim_code": "ORIGINAL SOURCE",
            "render_status": "failed",
            "render_error": "ORIGINAL FAILURE",
            "source_revision": 2,
        },
    )
    package = ResourcePackage(
        package_id="committed-failed-package",
        topic="t",
        resources=[resource],
    )
    await package_store.save(package, user_id="owner")
    job_store = JobStore(tmp_path / "jobs.db")
    await job_store.init()
    parent = Job(
        job_id="committed-failed-parent",
        user_id="owner",
        status=JobStatus.SUCCEEDED,
    )
    await job_store.save(parent)
    child = (
        await FollowUpScheduler(job_store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_repair_render",
                    dedupe_key="committed-failed:2:1",
                    payload={
                        "package_id": package.package_id,
                        "resource_id": resource.resource_id,
                        "user_id": "owner",
                        "failed_revision": 2,
                        "expected_repair_job_id": None,
                    },
                ),
            ),
        )
    )[0]
    failure_record = {
        "job_id": child.job_id,
        "failed_revision": 2,
        "status": "failed",
        "error_code": "candidate_validation_failed",
        "summary": "candidate remained invalid",
    }
    resource.format_specific.update(
        {
            "repair_status": "failed",
            "repair_job_id": child.job_id,
            "repair_history": [failure_record],
        }
    )
    await package_store.update_resource(
        package.package_id,
        resource,
        user_id="owner",
    )
    agent_calls: list[str] = []

    async def regenerate(self, context, failed_code, failure, runtime):  # type: ignore[no-untyped-def]
        agent_calls.append(failed_code)
        raise AssertionError("repair agent must not run after failure commit")

    monkeypatch.setattr(ManimRepairAgent, "regenerate", regenerate)
    renderer = _FakeRenderService(success=True)
    module_patch = monkeypatch_for_module(mr_module, renderer)
    runner = JobRunner(
        job_store=job_store,
        capability_registry=_EmptyCapabilities(),  # type: ignore[arg-type]
    )

    assert await runner.resume_pending() == 1
    terminal = await _wait_child(job_store, child.job_id)
    module_patch.undo()
    reloaded = await package_store.get_resource(resource.resource_id)
    assert terminal.status == JobStatus.FAILED
    assert agent_calls == []
    assert renderer.calls == []
    assert reloaded is not None
    assert reloaded.format_specific["repair_history"] == [failure_record]
    await job_store.close()
    await package_store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current_revision", "repair_job_id"),
    [(3, None), (2, "newer-repair-child")],
)
async def test_stale_or_conflicting_video_repair_child_fails_without_overwrite(
    tmp_path,
    monkeypatch,
    current_revision,
    repair_job_id,
) -> None:
    from tutor.services.resource_package import store as package_store_module
    from tutor.services.resource_package.store import ResourcePackageStore

    package_store = ResourcePackageStore(tmp_path / "packages.db")
    await package_store.init()
    monkeypatch.setattr(package_store_module, "_store", package_store)
    format_specific = {
        "manim_code": "CURRENT SOURCE",
        "render_status": "failed",
        "render_error": "CURRENT FAILURE",
        "source_revision": current_revision,
    }
    if repair_job_id is not None:
        format_specific["repair_job_id"] = repair_job_id
        format_specific["repair_status"] = "running"
    resource = Resource(
        resource_id="stale-repair-video",
        type=ResourceType.VIDEO,
        title="repair",
        format_specific=format_specific,
    )
    package = ResourcePackage(
        package_id="stale-repair-package",
        topic="t",
        resources=[resource],
    )
    await package_store.save(package, user_id="owner")
    job_store = JobStore(tmp_path / "jobs.db")
    await job_store.init()
    parent = Job(
        job_id="stale-repair-parent",
        user_id="owner",
        status=JobStatus.SUCCEEDED,
    )
    await job_store.save(parent)
    child = (
        await FollowUpScheduler(job_store).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_repair_render",
                    dedupe_key="stale-repair:2:1",
                    payload={
                        "package_id": package.package_id,
                        "resource_id": resource.resource_id,
                        "user_id": "owner",
                        "failed_revision": 2,
                        "expected_repair_job_id": None,
                    },
                ),
            ),
        )
    )[0]
    runner = JobRunner(
        job_store=job_store,
        capability_registry=_EmptyCapabilities(),  # type: ignore[arg-type]
    )

    assert await runner.resume_pending() == 1
    terminal = await _wait_child(job_store, child.job_id)
    reloaded = await package_store.get_resource(resource.resource_id)
    assert terminal.status == JobStatus.FAILED
    assert reloaded is not None
    assert reloaded.format_specific == format_specific
    await job_store.close()
    await package_store.close()


@pytest.mark.asyncio
async def test_video_repair_child_cannot_cross_owner_boundary(tmp_path) -> None:
    from tutor.services.jobs.follow_up import VideoRepairFollowUpCapability
    from tutor.services.resource_package.store import ResourcePackageStore

    store = ResourcePackageStore(tmp_path / "packages.db")
    await store.init()
    resource = Resource(
        resource_id="private-repair-video",
        type=ResourceType.VIDEO,
        title="private",
        format_specific={
            "manim_code": "PRIVATE SOURCE",
            "render_status": "failed",
            "render_error": "PRIVATE ERROR",
            "source_revision": 0,
            "repair_status": "pending",
            "repair_job_id": "attacker-child",
        },
    )
    package = ResourcePackage(package_id="private-repair-package", topic="t", resources=[resource])
    await store.save(package, user_id="owner-b")
    capability = VideoRepairFollowUpCapability(
        package_store=store,
        repair_agent=_ScriptedRepairAgent([REPAIR_GOOD_CODE]),
        render_service=_FakeRenderService(success=True),
        runtime_namespace={
            "Scene": object(),
            "Dot": object(),
            "Create": object(),
        },
        runtime_versions={"python": "3.11"},
    )

    with pytest.raises(PermissionError):
        await capability.run(
            UnifiedContext(
                job_id="attacker-child",
                user_id="owner-a",
                metadata={
                    "package_id": package.package_id,
                    "resource_id": resource.resource_id,
                    "failed_revision": 0,
                },
            ),
            StreamBus(),
        )

    reloaded = await store.get_resource(resource.resource_id)
    assert reloaded is not None
    assert reloaded.format_specific["manim_code"] == "PRIVATE SOURCE"
    assert reloaded.format_specific["render_error"] == "PRIVATE ERROR"
    await store.close()


@pytest.mark.asyncio
async def test_video_repair_empty_publish_preserves_original_failure(tmp_path) -> None:
    from tutor.services.jobs.follow_up import VideoRepairFollowUpCapability
    from tutor.services.resource_package.store import ResourcePackageStore

    store = ResourcePackageStore(tmp_path / "packages.db")
    await store.init()
    resource = Resource(
        resource_id="empty-publish-video",
        type=ResourceType.VIDEO,
        title="repair",
        format_specific={
            "manim_code": "ORIGINAL SOURCE",
            "scene_class": "MainScene",
            "render_status": "failed",
            "render_error": "ORIGINAL ERROR",
            "source_revision": 1,
            "repair_status": "pending",
            "repair_job_id": "empty-publish-child",
        },
    )
    package = ResourcePackage(package_id="empty-publish-package", topic="t", resources=[resource])
    await store.save(package, user_id="owner")

    async def claim_guard(operation):
        await operation()
        return True

    capability = VideoRepairFollowUpCapability(
        package_store=store,
        repair_agent=_ScriptedRepairAgent([REPAIR_GOOD_CODE]),
        render_service=_FakeRenderService(success=True, video_path=None),
        runtime_namespace={
            "Scene": object(),
            "Dot": object(),
            "Create": object(),
        },
        runtime_versions={"python": "3.11"},
    )
    capability._render_service.render = _empty_publish_render  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="Video repair failed"):
        await capability.run(
            UnifiedContext(
                job_id="empty-publish-child",
                user_id="owner",
                metadata={
                    "package_id": package.package_id,
                    "resource_id": resource.resource_id,
                    "failed_revision": 1,
                    "_claim_validator": lambda: _async_true(),
                    "_claim_guard": claim_guard,
                },
            ),
            StreamBus(),
        )

    reloaded = await store.get_resource(resource.resource_id)
    assert reloaded is not None
    assert reloaded.format_specific["manim_code"] == "ORIGINAL SOURCE"
    assert reloaded.format_specific["render_error"] == "ORIGINAL ERROR"
    assert reloaded.format_specific["render_status"] == "failed"
    assert reloaded.format_specific["repair_status"] == "failed"
    assert reloaded.format_specific["repair_history"][-1]["status"] == "failed"
    assert reloaded.format_specific["repair_candidate_code"] == REPAIR_GOOD_CODE
    assert (
        reloaded.format_specific["repair_candidate_failure"]["error_code"]
        == "repair_publish_failed"
    )
    await store.close()


async def _empty_publish_render(**kwargs):  # type: ignore[no-untyped-def]
    class Result:
        success = True
        public_url = ""
        video_path = None
        duration_seconds = 0
        error = ""
        failure = None

    return Result()
