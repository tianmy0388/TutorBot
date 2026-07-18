from __future__ import annotations

import asyncio
import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from tutor.core.capability_result import FollowUpTaskSpec
from tutor.services.jobs.follow_up import (
    FollowUpScheduler,
    VideoRenderFollowUpCapability,
)
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import Job, JobStatus
from tutor.services.jobs.store import JobStore
from tutor.services.manim_render.executor import ManimExecutor, RenderFailure
from tutor.services.manim_render.service import ManimRenderService, RenderedVideo
from tutor.services.resource_package.schema import (
    Resource,
    ResourcePackage,
    ResourceType,
)
from tutor.services.resource_package.store import ResourcePackageStore

requires_manim = pytest.mark.skipif(
    shutil.which("manim") is None and importlib.util.find_spec("manim") is None,
    reason="manim not installed",
)


class _Capabilities:
    def get(self, name: str):
        return None


class _FakeManimService:
    def __init__(self, result: RenderedVideo) -> None:
        self.result = result
        self.calls = 0

    async def render(self, **kwargs):
        self.calls += 1
        return self.result


async def _wait_terminal(store: JobStore, job_id: str) -> Job:
    for _ in range(12_000):
        job = await store.get(job_id)
        if job is not None and job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.PARTIAL,
        }:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(f"job did not terminalize: {job_id}")


def _runner(jobs: JobStore, packages: ResourcePackageStore) -> JobRunner:
    return JobRunner(
        job_store=jobs,
        capability_registry=_Capabilities(),  # type: ignore[arg-type]
        follow_up_builder=lambda kind: VideoRenderFollowUpCapability(
            package_store=packages
        ),
    )


async def _fixture(tmp_path: Path):
    jobs = JobStore(tmp_path / "jobs.db")
    packages = ResourcePackageStore(tmp_path / "packages.db")
    await jobs.init()
    await packages.init()
    parent = Job(
        job_id="parent-video",
        user_id="local-user",
        session_id="session-video",
        capability="resource_generation",
        status=JobStatus.SUCCEEDED,
    )
    await jobs.save(parent)
    resource = Resource(
        resource_id="video-1",
        type=ResourceType.VIDEO,
        title="Video",
        format_specific={
            "manim_code": "from manim import *\nclass MainScene(Scene):\n    pass\n",
            "scene_class": "MainScene",
            "render_status": "pending",
        },
    )
    package = ResourcePackage(
        package_id="package-video",
        topic="topic",
        resources=[resource],
    )
    package.associate_originating_job(parent.job_id)
    package.metadata["session_id"] = parent.session_id
    await packages.save(package, user_id=parent.user_id)
    child = (
        await FollowUpScheduler(jobs).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_render",
                    dedupe_key="video:package-video:video-1",
                    payload={
                        "package_id": package.package_id,
                        "resource_id": resource.resource_id,
                    },
                ),
            ),
        )
    )[0]
    return jobs, packages, child


@pytest.mark.asyncio
async def test_failed_video_child_persists_structured_failure_and_terminal_event(
    tmp_path,
    monkeypatch,
):
    jobs, packages, child = await _fixture(tmp_path)
    failure = RenderFailure(
        error_code="missing_external_asset",
        summary="Manim source references unavailable external assets",
        traceback_tail=("line 119", "FileNotFoundError: person.svg"),
        log_artifact_key="manim_logs/child/attempt-01.log",
    )
    fake = _FakeManimService(
        RenderedVideo(
            success=False,
            code="",
            attempts=0,
            error=failure.summary,
            failure=failure,
        )
    )
    monkeypatch.setattr(
        "tutor.services.manim_render.service.get_manim_render_service",
        lambda: fake,
    )
    runner = JobRunner(
        job_store=jobs,
        capability_registry=_Capabilities(),  # type: ignore[arg-type]
        follow_up_builder=lambda kind: VideoRenderFollowUpCapability(
            package_store=packages
        ),
    )

    assert await runner.resume_pending() == 1
    terminal = await _wait_terminal(jobs, child.job_id)
    persisted = await packages.get_resource("video-1")

    assert terminal.status == JobStatus.FAILED
    assert sum(event.get("type") == "job_terminal" for event in terminal.events) == 1
    assert persisted is not None
    assert persisted.format_specific["render_status"] == "failed"
    assert persisted.format_specific["render_failure"] == failure.to_dict()
    assert persisted.format_specific["render_error_code"] == "missing_external_asset"
    assert persisted.format_specific["render_error"] == failure.summary
    assert fake.calls == 1
    await runner.shutdown()
    await jobs.close()
    await packages.close()


@pytest.mark.asyncio
async def test_successful_video_child_persists_ready_nonempty_mp4(
    tmp_path,
    monkeypatch,
):
    from tutor.services.config.settings import get_settings

    jobs, packages, child = await _fixture(tmp_path)
    data_dir = tmp_path / "data"
    video = data_dir / "manim_videos" / "MainScene.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"non-empty-mp4")
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)
    fake = _FakeManimService(
        RenderedVideo(
            success=True,
            code="",
            video_path=video,
            public_url="/static/manim/MainScene.mp4",
            attempts=1,
        )
    )
    monkeypatch.setattr(
        "tutor.services.manim_render.service.get_manim_render_service",
        lambda: fake,
    )
    runner = JobRunner(
        job_store=jobs,
        capability_registry=_Capabilities(),  # type: ignore[arg-type]
        follow_up_builder=lambda kind: VideoRenderFollowUpCapability(
            package_store=packages
        ),
    )

    assert await runner.resume_pending() == 1
    terminal = await _wait_terminal(jobs, child.job_id)
    persisted = await packages.get_resource("video-1")

    assert terminal.status == JobStatus.SUCCEEDED
    assert persisted is not None
    assert persisted.format_specific["render_status"] == "ready"
    assert persisted.format_specific["artifact_key"] == "manim_videos/MainScene.mp4"
    assert video.stat().st_size > 0
    await runner.shutdown()
    await jobs.close()
    await packages.close()


@pytest.mark.asyncio
async def test_missing_svg_preflight_terminalizes_child_without_manim_launch(
    tmp_path,
    monkeypatch,
):
    from tutor.services.config.settings import get_settings

    jobs, packages, child = await _fixture(tmp_path)
    resource = await packages.get_resource("video-1")
    assert resource is not None
    resource.format_specific["manim_code"] = '''from manim import *
class MainScene(Scene):
    def construct(self):
        self.add(SVGMobject("person_silhouette.svg"))
'''
    await packages.update_resource(
        "package-video",
        resource,
        user_id="local-user",
    )
    data_dir = tmp_path / "data"
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)
    executor = MagicMock(spec=ManimExecutor)
    executor.temp_dir = tmp_path / "render-workdir"
    executor.is_available.return_value = True
    service = ManimRenderService(
        executor=executor,
        code_retry=MagicMock(),
        public_dir=data_dir / "manim_videos",
    )
    monkeypatch.setattr(
        "tutor.services.manim_render.service.get_manim_render_service",
        lambda: service,
    )
    runner = _runner(jobs, packages)

    assert await runner.resume_pending() == 1
    terminal = await _wait_terminal(jobs, child.job_id)
    persisted = await packages.get_resource("video-1")

    assert terminal.status == JobStatus.FAILED
    assert persisted is not None
    assert persisted.format_specific["render_status"] == "failed"
    assert (
        persisted.format_specific["render_failure"]["error_code"]
        == "missing_external_asset"
    )
    executor.render.assert_not_called()
    await runner.shutdown()
    await jobs.close()
    await packages.close()


@requires_manim
@pytest.mark.asyncio
async def test_real_manim_child_persists_ready_nonempty_mp4(
    tmp_path,
    monkeypatch,
):
    from tutor.services.config.settings import get_settings

    jobs, packages, child = await _fixture(tmp_path)
    resource = await packages.get_resource("video-1")
    assert resource is not None
    resource.format_specific["manim_code"] = '''from manim import *
class MainScene(Scene):
    def construct(self):
        dot = Dot()
        self.play(FadeIn(dot), run_time=0.1)
'''
    await packages.update_resource(
        "package-video",
        resource,
        user_id="local-user",
    )
    data_dir = tmp_path / "data"
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)
    service = ManimRenderService(
        executor=ManimExecutor(
            quality="l",
            output_dir=tmp_path / "manim-output",
            temp_dir=tmp_path / "manim-temp",
            timeout_seconds=120,
        ),
        public_dir=data_dir / "manim_videos",
    )
    monkeypatch.setattr(
        "tutor.services.manim_render.service.get_manim_render_service",
        lambda: service,
    )
    runner = _runner(jobs, packages)

    assert await runner.resume_pending() == 1
    terminal = await _wait_terminal(jobs, child.job_id)
    persisted = await packages.get_resource("video-1")

    assert terminal.status == JobStatus.SUCCEEDED
    assert persisted is not None
    assert persisted.format_specific["render_status"] == "ready"
    video = data_dir / persisted.format_specific["artifact_key"]
    assert video.is_file()
    assert video.stat().st_size > 0
    await runner.shutdown()
    await jobs.close()
    await packages.close()
