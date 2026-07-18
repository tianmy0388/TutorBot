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
