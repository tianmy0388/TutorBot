"""Runner-level tests for the typed result contract.

These tests pin the contract behaviour at the JobRunner boundary:
the persisted ``result`` must conform to :class:`JobResultContract`
and a ``job_terminal`` event must be broadcast with the same contract.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.contracts import JobResultContract
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import Job, JobStatus, JobSubmit
from tutor.services.jobs.store import JobStore, get_job_store, reset_job_store


class _FakeCapability:
    """A minimal capability that emits one result event and exits."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def run(self, context: UnifiedContext, bus: StreamBus) -> None:  # noqa: D401
        await bus.result(self._payload, source="fake")
        await bus.done()


class _FailingCapability:
    async def run(self, context: UnifiedContext, bus: StreamBus) -> None:
        await bus.error("boom", source="fake")
        await bus.done()


class _SilentCapability:
    """Emits no events at all — runner must report MISSING_RESULT."""

    async def run(self, context: UnifiedContext, bus: StreamBus) -> None:
        return None


class _CapabilitiesStub:
    def __init__(self, mapping: dict[str, object]) -> None:
        self._mapping = mapping

    def get(self, name: str):
        return self._mapping.get(name)


@pytest.mark.asyncio
async def test_runner_persists_succeeded_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache
    reset_settings_cache()
    reset_job_store()

    store = get_job_store()
    await store.init()
    cap = _FakeCapability({"assistant_message": "你好，世界"})
    runner = JobRunner(job_store=store, capability_registry=_CapabilitiesStub({"tutoring": cap}))  # type: ignore[arg-type]

    job = await runner.submit(JobSubmit(user_id="u1", message="hi", capability="tutoring"))
    # Drain the bus so the task can complete
    await asyncio.sleep(0.1)
    # Wait for the task to finish
    for _ in range(50):
        stored = await store.get(job.job_id)
        if stored is not None and stored.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.PARTIAL):
            break
        await asyncio.sleep(0.05)
    stored = await store.get(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.SUCCEEDED
    assert stored.result is not None
    contract = JobResultContract.model_validate(stored.result)
    assert contract.assistant_message == "你好，世界"
    assert contract.status.value == "succeeded"
    assert contract.job_id == job.job_id
    assert contract.capability == "tutoring"

    await store.close()
    reset_job_store()


@pytest.mark.asyncio
async def test_runner_marks_missing_result_as_failed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache
    reset_settings_cache()
    reset_job_store()

    store = get_job_store()
    await store.init()
    cap = _SilentCapability()
    runner = JobRunner(job_store=store, capability_registry=_CapabilitiesStub({"tutoring": cap}))  # type: ignore[arg-type]

    job = await runner.submit(JobSubmit(user_id="u1", message="hi", capability="tutoring"))
    for _ in range(50):
        stored = await store.get(job.job_id)
        if stored is not None and stored.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.PARTIAL):
            break
        await asyncio.sleep(0.05)
    stored = await store.get(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.FAILED
    assert stored.result is not None
    contract = JobResultContract.model_validate(stored.result)
    assert contract.error is not None
    assert contract.error.code == "MISSING_RESULT"
    assert contract.assistant_message  # non-empty
    assert stored.finished_at is not None
    assert contract.finished_at is not None

    await store.close()
    reset_job_store()


@pytest.mark.asyncio
async def test_runner_marks_error_event_as_failed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache
    reset_settings_cache()
    reset_job_store()

    store = get_job_store()
    await store.init()
    cap = _FailingCapability()
    runner = JobRunner(job_store=store, capability_registry=_CapabilitiesStub({"tutoring": cap}))  # type: ignore[arg-type]

    job = await runner.submit(JobSubmit(user_id="u1", message="hi", capability="tutoring"))
    for _ in range(50):
        stored = await store.get(job.job_id)
        if stored is not None and stored.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.PARTIAL):
            break
        await asyncio.sleep(0.05)
    stored = await store.get(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.FAILED
    contract = JobResultContract.model_validate(stored.result or {})
    assert contract.error is not None
    assert contract.error.code == "CAPABILITY_ERROR"
    assert "boom" in contract.error.message

    await store.close()
    reset_job_store()


@pytest.mark.asyncio
async def test_runner_emits_job_terminal_event(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache
    reset_settings_cache()
    reset_job_store()

    store = get_job_store()
    await store.init()
    cap = _FakeCapability({"assistant_message": "完成"})
    runner = JobRunner(job_store=store, capability_registry=_CapabilitiesStub({"tutoring": cap}))  # type: ignore[arg-type]

    job = await runner.submit(JobSubmit(user_id="u1", message="hi", capability="tutoring"))

    # Wait for the job to terminalize, then read the persisted replay
    # buffer (the job_terminal event is appended before broadcast).
    for _ in range(50):
        stored = await store.get(job.job_id)
        if stored is not None and stored.status in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.PARTIAL):
            break
        await asyncio.sleep(0.05)

    stored = await store.get(job.job_id)
    assert stored is not None
    terminal_events = [e for e in (stored.events or []) if e.get("type") == "job_terminal"]
    assert terminal_events, "expected at least one job_terminal event"
    last = terminal_events[-1]
    contract = JobResultContract.model_validate(last["metadata"]["contract"])
    assert contract.job_id == job.job_id
    assert contract.assistant_message == "完成"
    assert contract.status.value == "succeeded"

    await store.close()
    reset_job_store()
