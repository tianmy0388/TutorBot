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
import sqlite3

import pytest
from tutor.core.capability_result import CapabilityResult
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.contracts import JobResultContract
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import Job, JobStatus, JobSubmit
from tutor.services.jobs.store import JobStore, get_job_store, reset_job_store
from tutor.services.resource_package.schema import ArtifactRef


class _NoopCapability:
    """A capability that finishes immediately with a typed result."""

    async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
        return CapabilityResult(assistant_message="已就绪")


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

    # Now simulate a restart: a second atomic terminal write against the
    # same job must be a no-op.
    contract = JobResultContract(
        job_id=job.job_id,
        capability="tutoring",
        status="succeeded",
        assistant_message="已就绪",
    )
    terminal_evt = runner._terminal_event(stored, contract, seq=999)
    persisted = await store.set_terminal(
        job.job_id,
        status=JobStatus.SUCCEEDED,
        finished_at=stored.finished_at,
        result=contract.model_dump(mode="json"),
        terminal_event=terminal_evt,
    )
    assert persisted is False, "second terminal persist must be a no-op"

    stored2 = await store.get(job.job_id)
    terminal_count2 = sum(
        1 for e in (stored2.events or []) if e.get("type") == "job_terminal"
    )
    assert terminal_count2 == 1, "no new terminal event must be appended"


@pytest.mark.asyncio
async def test_store_set_terminal_is_idempotent_across_retries(fresh_runner) -> None:
    _, store = fresh_runner
    submit = JobSubmit(
        user_id="u1",
        session_id="sess-atomic",
        capability="tutoring",
        message="atomic terminal",
    )
    job = await JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub({"tutoring": _NoopCapability()}),  # type: ignore[arg-type]
    ).submit(submit)
    await _wait_for_terminal(store, job.job_id)
    stored = await store.get(job.job_id)
    assert stored is not None

    duplicate = {
        "type": "job_terminal",
        "source": "job_runner",
        "stage": "terminal",
        "content": "duplicate",
        "job_id": job.job_id,
        "session_id": job.session_id,
        "turn_id": "",
        "seq": stored.last_seq + 1,
        "timestamp": 1.0,
        "event_id": "duplicate-terminal",
        "metadata": {"contract": stored.result},
    }
    changed = await store.set_terminal(
        job.job_id,
        status=JobStatus.FAILED,
        finished_at=stored.finished_at,
        result={"should": "not replace"},
        error="duplicate",
        error_log_ref=ArtifactRef(
            name="error.log",
            kind="text",
            artifact_key=f"job_logs/{job.job_id}/error.log",
        ),
        terminal_event=duplicate,
    )

    after = await store.get(job.job_id)
    assert changed is False
    assert after is not None
    assert after.status == JobStatus.SUCCEEDED
    assert after.result == stored.result
    assert sum(e.get("type") == "job_terminal" for e in after.events) == 1


@pytest.mark.asyncio
async def test_store_migrates_error_log_ref_for_existing_database(tmp_path) -> None:
    db_path = tmp_path / "legacy-jobs.db"
    original = JobStore(db_path=db_path)
    await original.init()
    await original.close()

    with sqlite3.connect(db_path) as connection:
        connection.execute("ALTER TABLE jobs DROP COLUMN error_log_ref")

    reopened = JobStore(db_path=db_path)
    await reopened.init()
    job = Job(user_id="legacy")
    await reopened.save(job)
    stored = await reopened.get(job.job_id)

    assert stored is not None
    assert stored.error_log_ref is None
    await reopened.close()


@pytest.mark.asyncio
async def test_terminal_marker_survives_replay_buffer_eviction(tmp_path) -> None:
    store = JobStore(db_path=tmp_path / "jobs.db")
    await store.init()
    job = Job(user_id="u1", status=JobStatus.RUNNING)
    await store.save(job)
    first_contract = JobResultContract(
        job_id=job.job_id,
        capability=job.capability,
        status="succeeded",
        assistant_message="first",
    )
    first_event = {
        "type": "job_terminal",
        "source": "job_runner",
        "content": "first",
        "metadata": {"contract": first_contract.model_dump(mode="json")},
    }
    assert await store.set_terminal(
        job.job_id,
        status=JobStatus.SUCCEEDED,
        finished_at=first_contract.finished_at,
        result=first_contract.model_dump(mode="json"),
        terminal_event=first_event,
    )

    for seq in range(store.MAX_EVENTS_PER_JOB + 5):
        await store.append_event(
            job.job_id,
            {"type": "progress", "seq": seq, "event_id": f"late-{seq}"},
            seq,
        )

    replacement = JobResultContract(
        job_id=job.job_id,
        capability=job.capability,
        status="failed",
        assistant_message="replacement",
    )
    changed = await store.set_terminal(
        job.job_id,
        status=JobStatus.FAILED,
        finished_at=replacement.finished_at,
        result=replacement.model_dump(mode="json"),
        terminal_event={
            "type": "job_terminal",
            "source": "job_runner",
            "content": "replacement",
            "metadata": {"contract": replacement.model_dump(mode="json")},
        },
    )

    stored = await store.get(job.job_id)
    assert changed is False
    assert stored is not None
    assert stored.status == JobStatus.SUCCEEDED
    assert stored.result == first_contract.model_dump(mode="json")
    await store.close()


@pytest.mark.asyncio
async def test_concurrent_terminal_calls_preserve_first_outcome(tmp_path) -> None:
    store = JobStore(db_path=tmp_path / "jobs.db")
    await store.init()
    job = Job(user_id="u1", status=JobStatus.RUNNING)
    await store.save(job)

    async def finish(label: str, status: JobStatus) -> bool:
        contract = JobResultContract(
            job_id=job.job_id,
            capability=job.capability,
            status=status.value,
            assistant_message=label,
        )
        return await store.set_terminal(
            job.job_id,
            status=status,
            finished_at=contract.finished_at,
            result=contract.model_dump(mode="json"),
            terminal_event={
                "type": "job_terminal",
                "source": "job_runner",
                "content": label,
                "metadata": {"contract": contract.model_dump(mode="json")},
            },
        )

    applied = await asyncio.gather(
        finish("success", JobStatus.SUCCEEDED),
        finish("failure", JobStatus.FAILED),
    )
    stored = await store.get(job.job_id)
    assert sum(bool(item) for item in applied) == 1
    assert stored is not None
    terminals = [e for e in stored.events if e.get("type") == "job_terminal"]
    assert len(terminals) == 1
    assert stored.result["assistant_message"] == terminals[0]["content"]
    await store.close()
