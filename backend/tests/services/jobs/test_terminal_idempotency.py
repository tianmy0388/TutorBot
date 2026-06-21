"""Terminal-event idempotency regression test (Stage 3 of the 2026-06-21 plan).

The plan demands:

  - Every event in the runner has job_id/session_id/sequence/timestamp.
  - The terminal event is sent exactly once.
  - If a process restarts mid-job and tries to emit the terminal
    again, the second attempt is dropped.

The tests below cover these three points at the JobRunner boundary.
"""

from __future__ import annotations

import asyncio

import pytest

from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.contracts import JobResultContract
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import JobStatus, JobSubmit
from tutor.services.jobs.store import get_job_store, reset_job_store


class _NoopCapability:
    """A capability that finishes immediately with no events."""

    async def run(self, context: UnifiedContext, bus: StreamBus) -> None:
        return None


class _CapabilitiesStub:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, name: str):
        return self._mapping.get(name)


@pytest.fixture
async def fresh_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    reset_job_store()
    store = get_job_store()
    await store.init()
    cap = _NoopCapability()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub({"tutoring": cap}),  # type: ignore[arg-type]
    )
    yield runner, store
    await store.close()
    reset_job_store()


async def _wait_for_terminal(store, job_id: str, timeout: float = 5.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        stored = await store.get(job_id)
        if stored is not None and stored.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.PARTIAL,
        }:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach terminal state in {timeout}s")


@pytest.mark.asyncio
async def test_terminal_event_has_full_metadata(fresh_runner) -> None:
    runner, store = fresh_runner
    submit = JobSubmit(
        user_id="u1",
        session_id="sess-1",
        capability="tutoring",
        message="解释 self-attention",
        language="zh",
    )
    job = await runner.submit(submit)
    await _wait_for_terminal(store, job.job_id)
    stored = await store.get(job.job_id)
    assert stored is not None
    events = list(stored.events or [])
    assert events, "runner must persist at least one event"
    terminal = next((e for e in events if e.get("type") == "job_terminal"), None)
    assert terminal is not None
    # Every mandatory metadata field must be present.
    for k in ("job_id", "session_id", "seq", "timestamp", "event_id"):
        assert k in terminal, f"terminal event missing {k}"
    assert terminal["session_id"] == "sess-1"
    assert terminal["job_id"] == job.job_id
    assert isinstance(terminal["seq"], int)
    assert terminal["seq"] > 0, "seq must be the actual next sequence, not 0"
    assert isinstance(terminal["timestamp"], (int, float))


@pytest.mark.asyncio
async def test_terminal_persisted_exactly_once(fresh_runner) -> None:
    runner, store = fresh_runner
    submit = JobSubmit(
        user_id="u1",
        session_id="sess-1",
        capability="tutoring",
        message="解释 self-attention",
        language="zh",
    )
    job = await runner.submit(submit)
    await _wait_for_terminal(store, job.job_id)
    stored = await store.get(job.job_id)
    assert stored is not None
    terminal_count = sum(
        1 for e in (stored.events or []) if e.get("type") == "job_terminal"
    )
    assert terminal_count == 1, (
        f"expected exactly 1 terminal event, got {terminal_count}"
    )

    # Now simulate a restart: a second _persist_terminal_once call
    # against the same job must be a no-op.
    contract = JobResultContract(
        job_id=job.job_id,
        capability="tutoring",
        status="succeeded",
        assistant_message="已就绪",
    )
    terminal_evt = runner._terminal_event(stored, contract, seq=999)
    persisted = await runner._persist_terminal_once(job.job_id, terminal_evt)
    assert persisted is False, "second terminal persist must be a no-op"

    stored2 = await store.get(job.job_id)
    terminal_count2 = sum(
        1 for e in (stored2.events or []) if e.get("type") == "job_terminal"
    )
    assert terminal_count2 == 1, "no new terminal event must be appended"
