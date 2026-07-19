from __future__ import annotations

import asyncio

import pytest
from tutor.services.resource_package.schema import Resource, ResourcePackage, ResourceType
from tutor.services.resource_package.store import ResourcePackageStore


@pytest.mark.asyncio
async def test_two_store_writers_allow_only_one_matching_repair_cas(tmp_path) -> None:
    db_path = tmp_path / "packages.db"
    first = ResourcePackageStore(db_path)
    second = ResourcePackageStore(db_path)
    await first.init()
    await second.init()
    resource = Resource(
        resource_id="cas-video",
        type=ResourceType.VIDEO,
        title="video",
        format_specific={
            "manim_code": "original",
            "render_status": "failed",
            "source_revision": 7,
            "repair_job_id": "repair-child",
            "repair_status": "running",
        },
    )
    package = ResourcePackage(package_id="cas-package", topic="t", resources=[resource])
    await first.save(package, user_id="owner")

    async def write(store: ResourcePackageStore, marker: str):
        def mutation(payload: dict[str, object]) -> None:
            payload["source_revision"] = 8
            payload["repair_status"] = "ready"
            payload["writer"] = marker

        return await store.mutate_video_repair_if_current(
            package_id=package.package_id,
            resource_id=resource.resource_id,
            user_id="owner",
            expected_source_revision=7,
            expected_repair_job_id="repair-child",
            mutation=mutation,
        )

    results = await asyncio.gather(write(first, "first"), write(second, "second"))

    assert sum(result is not None for result in results) == 1
    reloaded = await first.get_resource(resource.resource_id)
    assert reloaded is not None
    assert reloaded.format_specific["source_revision"] == 8
    assert reloaded.format_specific["writer"] in {"first", "second"}
    assert reloaded.format_specific["repair_status"] == "ready"
    await first.close()
    await second.close()


@pytest.mark.asyncio
async def test_repair_cas_rejects_stale_revision_or_job_without_mutation(tmp_path) -> None:
    store = ResourcePackageStore(tmp_path / "packages.db")
    await store.init()
    resource = Resource(
        resource_id="stale-video",
        type=ResourceType.VIDEO,
        title="video",
        format_specific={
            "manim_code": "current",
            "render_status": "failed",
            "source_revision": 3,
            "repair_job_id": "current-child",
            "repair_status": "running",
        },
    )
    package = ResourcePackage(package_id="stale-package", topic="t", resources=[resource])
    await store.save(package, user_id="owner")

    def mutation(payload: dict[str, object]) -> None:
        payload["manim_code"] = "STALE OVERWRITE"

    wrong_revision = await store.mutate_video_repair_if_current(
        package_id=package.package_id,
        resource_id=resource.resource_id,
        user_id="owner",
        expected_source_revision=2,
        expected_repair_job_id="current-child",
        mutation=mutation,
    )
    wrong_job = await store.mutate_video_repair_if_current(
        package_id=package.package_id,
        resource_id=resource.resource_id,
        user_id="owner",
        expected_source_revision=3,
        expected_repair_job_id="stale-child",
        mutation=mutation,
    )

    assert wrong_revision is None
    assert wrong_job is None
    reloaded = await store.get_resource(resource.resource_id)
    assert reloaded is not None
    assert reloaded.format_specific["manim_code"] == "current"
    await store.close()
