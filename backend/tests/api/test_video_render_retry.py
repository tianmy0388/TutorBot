from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from tutor.api.routers.resources import router
from tutor.core.capability_result import FollowUpTaskSpec
from tutor.services.jobs.follow_up import FollowUpScheduler
from tutor.services.jobs.schema import Job, JobStatus
from tutor.services.jobs.store import JobStore
from tutor.services.resource_package.schema import (
    Resource,
    ResourcePackage,
    ResourceType,
)
from tutor.services.resource_package.store import ResourcePackageStore


def test_video_retry_creates_one_new_durable_child_then_resets_pending(tmp_path):
    jobs = JobStore(tmp_path / "jobs.db")
    packages = ResourcePackageStore(tmp_path / "packages.db")

    async def seed():
        await jobs.init()
        await packages.init()
        parent = Job(
            job_id="parent",
            user_id="owner",
            session_id="session",
            capability="resource_generation",
            status=JobStatus.SUCCEEDED,
        )
        await jobs.save(parent)
        old_child = (
            await FollowUpScheduler(jobs).enqueue(
                parent.job_id,
                (
                    FollowUpTaskSpec(
                        kind="video_render",
                        dedupe_key="video:package:video",
                        payload={"package_id": "package", "resource_id": "video"},
                    ),
                ),
            )
        )[0]
        old_child.status = JobStatus.FAILED
        await jobs.save(old_child)
        resource = Resource(
            resource_id="video",
            type=ResourceType.VIDEO,
            title="video",
            format_specific={
                "manim_code": "from manim import *",
                "scene_class": "MainScene",
                "render_status": "failed",
            },
        )
        package = ResourcePackage(
            package_id="package",
            topic="topic",
            resources=[resource],
        )
        package.associate_originating_job(parent.job_id)
        await packages.save(package, user_id="owner")

    asyncio.run(seed())
    runner = SimpleNamespace(store=jobs, resume_pending=AsyncMock(return_value=1))
    app = FastAPI()
    app.state.settings = SimpleNamespace(multi_user_enabled=True)
    app.state.resource_package_store = packages
    app.state.learning_runner = runner
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)
    path = "/api/v1/resources/packages/owner/package/resources/video/retry-video"

    first = client.post(path)
    second = client.post(path)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["job_id"] == second.json()["job_id"]
    assert first.json()["job_id"] != "parent"
    assert first.json()["status"] == "pending"

    async def verify():
        children = await jobs.get_children("parent")
        resource = await packages.get_resource("video")
        assert len(children) == 2
        assert children[-1].status == JobStatus.PENDING
        assert resource is not None
        assert resource.format_specific["render_status"] == "pending"
        await jobs.close()
        await packages.close()

    asyncio.run(verify())
    runner.resume_pending.assert_awaited_once()


def test_video_retry_enforces_video_ownership_scope(tmp_path):
    packages = ResourcePackageStore(tmp_path / "packages.db")
    jobs = JobStore(tmp_path / "jobs.db")

    async def seed():
        await packages.init()
        await jobs.init()
        package = ResourcePackage(
            package_id="package",
            topic="topic",
            resources=[
                Resource(
                    resource_id="document",
                    type=ResourceType.DOCUMENT,
                    title="doc",
                )
            ],
        )
        package.associate_originating_job("parent")
        await packages.save(package, user_id="owner")

    asyncio.run(seed())
    app = FastAPI()
    app.state.settings = SimpleNamespace(multi_user_enabled=True)
    app.state.resource_package_store = packages
    app.state.learning_runner = SimpleNamespace(store=jobs, resume_pending=AsyncMock())
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)

    wrong_owner = client.post(
        "/api/v1/resources/packages/attacker/package/resources/document/retry-video"
    )
    wrong_type = client.post(
        "/api/v1/resources/packages/owner/package/resources/document/retry-video"
    )

    assert wrong_owner.status_code == 404
    assert wrong_type.status_code == 422
    asyncio.run(packages.close())
    asyncio.run(jobs.close())


def test_video_retry_completion_race_cannot_overwrite_terminal_resource(
    tmp_path,
    monkeypatch,
):
    jobs = JobStore(tmp_path / "jobs.db")
    packages = ResourcePackageStore(tmp_path / "packages.db")

    async def seed():
        await jobs.init()
        await packages.init()
        parent = Job(
            job_id="race-parent",
            user_id="owner",
            session_id="session",
            capability="resource_generation",
            status=JobStatus.SUCCEEDED,
        )
        await jobs.save(parent)
        resource = Resource(
            resource_id="race-video",
            type=ResourceType.VIDEO,
            title="video",
            format_specific={
                "manim_code": "from manim import *",
                "scene_class": "MainScene",
                "render_status": "failed",
            },
        )
        package = ResourcePackage(
            package_id="race-package",
            topic="topic",
            resources=[resource],
        )
        package.associate_originating_job(parent.job_id)
        await packages.save(package, user_id="owner")

    asyncio.run(seed())
    original_enqueue = FollowUpScheduler.enqueue

    async def enqueue_then_complete(self, parent_job_id, specs):
        children = await original_enqueue(self, parent_job_id, specs)
        child = children[0]
        terminal_resource = await packages.get_resource("race-video")
        assert terminal_resource is not None
        terminal_resource.format_specific.update(
            {
                "render_status": "ready",
                "render_job_id": child.job_id,
                "video_url": "/static/manim/MainScene.mp4",
                "artifact_key": "manim_videos/MainScene.mp4",
            }
        )
        await packages.update_resource(
            "race-package",
            terminal_resource,
            user_id="owner",
        )
        assert await jobs.set_terminal(
            child.job_id,
            status=JobStatus.SUCCEEDED,
            finished_at=None,
            result={"resource_id": "race-video"},
            terminal_event={"type": "job_terminal", "event_id": "race-terminal"},
            error=(
                'provider-token=private-value at '
                '"C:\\Program Files\\Tutor Bot\\scene.py"'
            ),
        )
        return children

    monkeypatch.setattr(FollowUpScheduler, "enqueue", enqueue_then_complete)
    runner = SimpleNamespace(store=jobs, resume_pending=AsyncMock(return_value=0))
    app = FastAPI()
    app.state.settings = SimpleNamespace(multi_user_enabled=True)
    app.state.resource_package_store = packages
    app.state.learning_runner = runner
    app.include_router(router, prefix="/api/v1")

    response = TestClient(app).post(
        "/api/v1/resources/packages/owner/race-package/resources/"
        "race-video/retry-video"
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "succeeded"
    assert "private-value" not in response.text
    assert "Program Files" not in response.text

    async def verify():
        resource = await packages.get_resource("race-video")
        assert resource is not None
        assert resource.format_specific["render_status"] == "ready"
        assert resource.format_specific["video_url"] == "/static/manim/MainScene.mp4"
        assert resource.format_specific["artifact_key"] == "manim_videos/MainScene.mp4"
        await jobs.close()
        await packages.close()

    asyncio.run(verify())
    runner.resume_pending.assert_not_awaited()


def test_video_retry_rejects_ready_resource_without_creating_child(tmp_path):
    jobs = JobStore(tmp_path / "jobs.db")
    packages = ResourcePackageStore(tmp_path / "packages.db")

    async def seed():
        await jobs.init()
        await packages.init()
        parent = Job(
            job_id="ready-parent",
            user_id="owner",
            session_id="session",
            capability="resource_generation",
            status=JobStatus.SUCCEEDED,
        )
        await jobs.save(parent)
        resource = Resource(
            resource_id="ready-video",
            type=ResourceType.VIDEO,
            title="video",
            format_specific={
                "manim_code": "from manim import *",
                "scene_class": "MainScene",
                "render_status": "ready",
                "video_url": "/static/manim/MainScene.mp4",
                "artifact_key": "manim_videos/MainScene.mp4",
            },
        )
        package = ResourcePackage(
            package_id="ready-package",
            topic="topic",
            resources=[resource],
        )
        package.associate_originating_job(parent.job_id)
        await packages.save(package, user_id="owner")

    asyncio.run(seed())
    runner = SimpleNamespace(store=jobs, resume_pending=AsyncMock(return_value=0))
    app = FastAPI()
    app.state.settings = SimpleNamespace(multi_user_enabled=True)
    app.state.resource_package_store = packages
    app.state.learning_runner = runner
    app.include_router(router, prefix="/api/v1")

    response = TestClient(app).post(
        "/api/v1/resources/packages/owner/ready-package/resources/"
        "ready-video/retry-video"
    )

    assert response.status_code == 409

    async def verify():
        assert await jobs.get_children("ready-parent") == []
        resource = await packages.get_resource("ready-video")
        assert resource is not None
        assert resource.format_specific["render_status"] == "ready"
        assert resource.format_specific["video_url"] == "/static/manim/MainScene.mp4"
        await jobs.close()
        await packages.close()

    asyncio.run(verify())
    runner.resume_pending.assert_not_awaited()
