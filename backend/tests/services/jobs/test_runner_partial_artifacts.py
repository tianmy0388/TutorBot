"""Regression test: JobRunner surfaces partial resources on FAILED.

187b2955 trace analysis: a 601.6s timeout left the user with zero
visible resources even though pedagogy + parallel agents had already
streamed multiple ``RESOURCE`` events before the cut-off. The contract
should carry those events forward as ``partial_artifacts`` so the
frontend can render them after the timeout.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import JobStatus, JobSubmit
from tutor.services.jobs.store import (
    get_job_store,
    reset_job_store,
)


class _StreamingCapability:
    """Capability that emits a few RESOURCE events, then raises."""

    def __init__(self) -> None:
        self._counter = 0

    async def run(self, context: UnifiedContext, bus: StreamBus) -> None:
        # Simulate 3 finished resources + a final-stage crash
        for i, rtype in enumerate(["document", "mindmap", "exercise"]):
            self._counter += 1
            await bus.resource(
                {
                    "resource_id": f"r{i}",
                    "type": rtype,
                    "title": f"测试资源 {i}",
                    "content": f"body {i}",
                },
                source="capability",
                stage="parallel_resource_generation",
            )
        raise RuntimeError("video render blew up")


class _CapabilitiesStub:
    def __init__(self, mapping: dict[str, object]) -> None:
        self._mapping = mapping

    def get(self, name: str):
        return self._mapping.get(name)


@pytest.mark.asyncio
async def test_runner_collects_resource_events_into_partial_artifacts(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when the capability raises mid-pipeline, every ``RESOURCE``
    event it emitted BEFORE the crash must show up in the contract's
    ``partial_artifacts`` list.
    """
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache
    reset_settings_cache()
    reset_job_store()

    store = get_job_store()
    await store.init()
    cap = _StreamingCapability()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub({"tutoring": cap}),  # type: ignore[arg-type]
    )

    job = await runner.submit(
        JobSubmit(user_id="u1", message="hi", capability="tutoring")
    )

    for _ in range(60):
        stored = await store.get(job.job_id)
        if stored is not None and stored.status in (
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.PARTIAL,
        ):
            break
        await asyncio.sleep(0.05)

    stored = await store.get(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.FAILED

    from tutor.services.jobs.contracts import JobResultContract

    contract = JobResultContract.model_validate(stored.result)
    assert contract.partial_artifacts, (
        f"expected partial_artifacts to be populated, got "
        f"{contract.partial_artifacts!r}"
    )
    # Three resources streamed before the crash — all must surface.
    assert len(contract.partial_artifacts) == 3
    types_seen = {a.resource_type for a in contract.partial_artifacts}
    assert types_seen == {"document", "mindmap", "exercise"}
    # All must report succeeded (we didn't filter them out).
    for a in contract.partial_artifacts:
        assert a.status == "succeeded"
        assert a.resource_id is not None
        assert a.title is not None

    await store.close()
    reset_job_store()


@pytest.mark.asyncio
async def test_runner_partial_artifacts_empty_when_no_resource_events(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward-compat: a capability that emits no RESOURCE events
    must still produce a valid contract with an empty
    ``partial_artifacts`` list (no ValidationError)."""
    from tutor.services.jobs.contracts import JobResultContract

    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache
    reset_settings_cache()
    reset_job_store()

    class _Silent:
        async def run(self, context: UnifiedContext, bus: StreamBus) -> None:
            raise RuntimeError("crashed before any resource event")

    store = get_job_store()
    await store.init()
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub({"tutoring": _Silent()}),  # type: ignore[arg-type]
    )
    job = await runner.submit(
        JobSubmit(user_id="u1", message="hi", capability="tutoring")
    )
    for _ in range(60):
        stored = await store.get(job.job_id)
        if stored is not None and stored.status in (
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.PARTIAL,
        ):
            break
        await asyncio.sleep(0.05)
    stored = await store.get(job.job_id)
    assert stored is not None
    contract = JobResultContract.model_validate(stored.result)
    assert contract.partial_artifacts == []
    await store.close()
    reset_job_store()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))