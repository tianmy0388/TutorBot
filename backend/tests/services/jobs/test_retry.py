"""Tests for the targeted job-retry endpoint (Task 5).

The retry endpoint takes a parent job_id and a list of resource types
that previously FAILED. It submits a child job with the same plan but
restricted to those types, preserving the parent's succeeded artifacts
in the child's metadata so a downstream re-package step can reassemble
the full package.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from httpx import ASGITransport
from tutor.api.main import create_app
from tutor.services.config.settings import get_settings, reset_settings_cache
from tutor.services.jobs import (
    Job,
    get_job_store,
    reset_job_runner,
    reset_job_store,
    shutdown_job_runner,
)
from tutor.services.jobs.contracts import JobResultContract
from tutor.services.jobs.schema import JobStatus as SchemaJobStatus


def _client(*, multi_user_enabled: bool = True) -> httpx.AsyncClient:
    reset_settings_cache()
    reset_job_store()
    reset_job_runner()
    settings = get_settings()
    settings.multi_user_enabled = multi_user_enabled
    app = create_app(settings)
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


async def _seed_parent_job(
    store,
    *,
    user_id: str,
    capability: str,
    metadata: dict,
    contract_status: str,
    artifacts: list[dict],
    assistant_message: str = "部分完成",
    row_status: SchemaJobStatus = SchemaJobStatus.PARTIAL,
    session_id: str | None = None,
    web_search_enabled: bool = False,
) -> str:
    """Insert a job row directly, bypassing the runner's background task.

    Going through ``runner.submit`` schedules an async task that races
    the test's status writes. Direct insertion keeps the test fully
    deterministic.
    """
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    contract = JobResultContract(
        job_id=job_id,
        capability=capability,
        status=contract_status,  # type: ignore[arg-type]
        assistant_message=assistant_message,
        artifacts=artifacts,
    )
    job = Job(
        job_id=job_id,
        user_id=user_id,
        session_id=session_id or f"ses_{uuid.uuid4().hex[:8]}",
        capability=capability,
        status=row_status,
        message="hi",
        language="zh",
        metadata=metadata,
        created_at=now,
        started_at=now,
        finished_at=now,
        result=contract.model_dump(mode="json"),
        web_search_enabled=web_search_enabled,
    )
    await store.save(job)
    return job_id


@pytest.mark.asyncio
async def test_rest_retry_inherits_parent_web_search_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    async with _client() as client:
        store = get_job_store()
        await store.init()
        parent_id = await _seed_parent_job(
            store,
            user_id="u1",
            capability="resource_generation",
            metadata={"selected_resource_types": ["video"]},
            contract_status="partial",
            artifacts=[{"resource_type": "video", "status": "failed"}],
            session_id="missing-conversation-row",
            web_search_enabled=True,
        )

        response = await client.post(
            f"/api/v1/jobs/u1/{parent_id}/retry",
            json={"resource_types": ["video"]},
        )

        assert response.status_code == 200, response.text
        child = await store.get(response.json()["job_id"])
        assert child is not None
        assert child.web_search_enabled is True


@pytest.mark.asyncio
async def test_retry_endpoint_validates_resource_types(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    async with _client() as client:
        store = get_job_store()
        await store.init()

        parent_id = await _seed_parent_job(
            store,
            user_id="u1",
            capability="resource_generation",
            metadata={
                "plan_id": "plan_x",
                "selected_resource_types": ["document", "video"],
                "topic": "Transformer",
            },
            contract_status="partial",
            artifacts=[
                {"resource_type": "document", "status": "succeeded"},
                {
                    "resource_type": "video",
                    "status": "failed",
                    "error": {
                        "code": "MANIM_RENDER_FAILED",
                        "message": "渲染失败",
                        "retryable": True,
                    },
                },
            ],
            assistant_message="已生成 1 项资源，1 项失败：video",
        )

        response = await client.post(
            f"/api/v1/jobs/u1/{parent_id}/retry",
            json={"resource_types": ["video"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert "job_id" in body
        assert body["parent_job_id"] == parent_id
        assert body["selected_types"] == ["video"]
        assert body["preserved_artifacts"] == ["document"]

        response_bad = await client.post(
            f"/api/v1/jobs/u1/{parent_id}/retry",
            json={"resource_types": ["document"]},
        )
        assert response_bad.status_code == 422

        response_unknown = await client.post(
            f"/api/v1/jobs/u1/{parent_id}/retry",
            json={"resource_types": ["unknown_type"]},
        )
        assert response_unknown.status_code == 422

        await store.close()
        reset_job_store()
        reset_job_runner()


@pytest.mark.asyncio
async def test_retry_endpoint_unknown_parent_404(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    async with _client() as client:
        store = get_job_store()
        await store.init()
        try:
            response = await client.post(
                "/api/v1/jobs/u1/missing-job/retry",
                json={"resource_types": ["video"]},
            )
            assert response.status_code == 404
        finally:
            await store.close()
            reset_job_store()
            reset_job_runner()


@pytest.mark.asyncio
async def test_retry_preserves_parent_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    async with _client() as client:
        store = get_job_store()
        await store.init()

        parent_id = await _seed_parent_job(
            store,
            user_id="u1",
            capability="resource_generation",
            metadata={
                "plan_id": "plan_y",
                "selected_resource_types": ["document", "video", "exercise"],
                "topic": "RNN",
            },
            contract_status="partial",
            artifacts=[
                {"resource_type": "document", "status": "succeeded"},
                {"resource_type": "exercise", "status": "succeeded"},
                {
                    "resource_type": "video",
                    "status": "failed",
                    "error": {
                        "code": "MANIM_RENDER_FAILED",
                        "message": "渲染失败",
                        "retryable": True,
                    },
                },
            ],
        )

        response = await client.post(
            f"/api/v1/jobs/u1/{parent_id}/retry",
            json={"resource_types": ["video"]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["preserved_artifacts"] == ["document", "exercise"]
        assert body["selected_types"] == ["video"]

        await store.close()
        reset_job_store()
        reset_job_runner()


@pytest.mark.asyncio
async def test_local_mode_retries_missing_artifact_from_succeeded_historical_job(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    store = None
    try:
        async with _client(multi_user_enabled=False) as client:
            store = get_job_store()
            await store.init()
            parent_id = await _seed_parent_job(
                store,
                user_id="historical-owner",
                capability="resource_generation",
                metadata={
                    "plan_id": "plan-success",
                    "selected_resource_types": ["code"],
                    "topic": "Recovery",
                },
                contract_status="succeeded",
                artifacts=[{"resource_type": "code", "status": "succeeded"}],
                row_status=SchemaJobStatus.SUCCEEDED,
                session_id="recovery-session",
            )

            response = await client.post(
                f"/api/v1/jobs/stale-browser/{parent_id}/retry",
                json={"resource_types": ["code"]},
            )

            assert response.status_code == 200, response.text
            assert response.json()["preserved_artifacts"] == []
            child = await store.get(response.json()["job_id"])
            assert child is not None
            assert child.user_id == "local-user"
            assert child.session_id == "recovery-session"
    finally:
        await shutdown_job_runner()
        if store is not None:
            await store.close()
        reset_job_store()


@pytest.mark.asyncio
async def test_local_mode_retries_failed_historical_job_but_multi_user_denies(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    async with _client(multi_user_enabled=False) as client:
        store = get_job_store()
        await store.init()
        parent_id = await _seed_parent_job(
            store,
            user_id="historical-owner",
            capability="resource_generation",
            metadata={"selected_resource_types": ["video"], "topic": "Recovery"},
            contract_status="failed",
            artifacts=[{"resource_type": "video", "status": "failed"}],
            row_status=SchemaJobStatus.FAILED,
        )
        response = await client.post(
            f"/api/v1/jobs/stale-browser/{parent_id}/retry",
            json={"resource_types": ["video"]},
        )
        assert response.status_code == 200, response.text

    async with _client(multi_user_enabled=True) as client:
        store = get_job_store()
        await store.init()
        parent_id = await _seed_parent_job(
            store,
            user_id="owner-a",
            capability="resource_generation",
            metadata={"selected_resource_types": ["video"], "topic": "Recovery"},
            contract_status="failed",
            artifacts=[{"resource_type": "video", "status": "failed"}],
            row_status=SchemaJobStatus.FAILED,
        )
        response = await client.post(
            f"/api/v1/jobs/owner-b/{parent_id}/retry",
            json={"resource_types": ["video"]},
        )
        assert response.status_code == 404
