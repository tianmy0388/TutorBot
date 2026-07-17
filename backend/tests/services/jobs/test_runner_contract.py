"""Runner-level tests for the typed result contract.

These tests pin the contract behaviour at the JobRunner boundary:
the persisted ``result`` must conform to :class:`JobResultContract`
and a ``job_terminal`` event must be broadcast with the same contract.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from tutor.core.capability_result import CapabilityResult
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.contracts import JobResultContract
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import JobStatus, JobSubmit
from tutor.services.jobs.store import get_job_store, reset_job_store


class _FakeCapability:
    """A minimal capability that returns one structured result."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.context_job_id: str | None = None

    async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:  # noqa: D401
        self.context_job_id = context.job_id
        return CapabilityResult(
            assistant_message=self._payload.get("assistant_message"),
            payload=self._payload,
        )


class _FailingCapability:
    async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
        await bus.progress("started", 1, 2, source="fake")
        raise RuntimeError("boom after progress")


class _SilentCapability:
    """Emits no events at all — runner must report MISSING_RESULT."""

    async def run(self, context: UnifiedContext, bus: StreamBus) -> None:
        return None


class _LegacyTerminalCapability:
    """Simulates pre-contract code attempting to own terminal events."""

    async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
        await bus.progress("kept", 1, 1, source="legacy")
        await bus.result({"wrong": True}, source="legacy")
        await bus.done(source="legacy")
        return CapabilityResult(assistant_message="canonical", payload={"right": True})


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
    assert cap.context_job_id == job.job_id

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
    assert contract.error.code == "CAPABILITY_FAILED"
    assert "boom" in contract.error.message
    assert stored.error_log_ref is not None
    assert stored.error_log_ref.artifact_key
    log_path = Path(tmp_path / "data") / stored.error_log_ref.artifact_key
    assert log_path.is_file()
    log_text = log_path.read_text(encoding="utf-8")
    assert "Traceback" in log_text
    assert "RuntimeError: boom after progress" in log_text
    assert any(event.get("type") == "progress" for event in stored.events)
    assert sum(event.get("type") == "job_terminal" for event in stored.events) == 1

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

    result_events = [e for e in (stored.events or []) if e.get("type") == "result"]
    done_events = [e for e in (stored.events or []) if e.get("type") == "done"]
    assert len(result_events) == 1
    assert len(done_events) == 1
    assert result_events[0]["source"] == "job_runner"
    assert json.loads(result_events[0]["content"])["assistant_message"] == "完成"

    await store.close()
    reset_job_store()


@pytest.mark.asyncio
async def test_runner_filters_capability_terminal_events_without_losing_progress(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    reset_job_store()
    store = get_job_store()
    await store.init()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub({"tutoring": _LegacyTerminalCapability()}),  # type: ignore[arg-type]
    )

    job = await runner.submit(JobSubmit(user_id="u1", message="hi", capability="tutoring"))
    for _ in range(50):
        stored = await store.get(job.job_id)
        if stored is not None and stored.status == JobStatus.SUCCEEDED:
            break
        await asyncio.sleep(0.05)

    stored = await store.get(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.SUCCEEDED
    assert [e["source"] for e in stored.events if e.get("type") == "progress"] == ["legacy"]
    for terminal_type in ("result", "done", "job_terminal"):
        terminal_events = [e for e in stored.events if e.get("type") == terminal_type]
        assert len(terminal_events) == 1
        assert terminal_events[0]["source"] == "job_runner"

    await store.close()
    reset_job_store()
