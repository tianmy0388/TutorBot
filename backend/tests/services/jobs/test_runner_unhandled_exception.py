"""Regression test: capability that raises an unhandled exception.

Pre-fix, the runner's watchdog only catches ``asyncio.TimeoutError``
inside ``_watch_and_close``. Other exceptions raised by ``cap.run()``
escape the watchdog task and are silently dropped by asyncio ("Task
exception was never retrieved"). The bus is then closed by the
watchdog's ``finally`` block, the subscribe_iter loop ends, and the
contract is built with ``error_msg=None`` + ``final_result=None`` →
**MISSING_RESULT** — the user sees "能力未返回结构化结果" with no hint
of what actually went wrong.

The fix: surface the capability's actual exception in the contract
(``error_code=CAPABILITY_FAILED`` with the exception type + message
in ``diagnostic``) so the operator can see the real cause in the
logs / error message.
"""

from __future__ import annotations

import asyncio
import sys

import pytest
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import JobStatus, JobSubmit
from tutor.services.jobs.store import (
    get_job_store,
    reset_job_store,
)


class _CrashingCapability:
    """Mimics ``cap.run()`` raising an unhandled exception mid-pipeline."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def run(self, context: UnifiedContext, bus: StreamBus) -> None:
        # Emit a few events first so the trace panel has data, then crash.
        await bus.thinking("starting…", source="crash")
        await bus.content("partial output", source="crash")
        raise self._exc


class _CapabilitiesStub:
    def __init__(self, mapping: dict[str, object]) -> None:
        self._mapping = mapping

    def get(self, name: str):
        return self._mapping.get(name)


@pytest.mark.asyncio
async def test_runner_surfaces_capability_exception_as_capability_error(
    tmp_path, monkeypatch
) -> None:
    """Unhandled capability errors are stable publicly and detailed privately."""
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache
    reset_settings_cache()
    reset_job_store()

    store = get_job_store()
    await store.init()
    cap = _CrashingCapability(RuntimeError("quality_review LLM call exploded"))
    runner = JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub({"tutoring": cap}),  # type: ignore[arg-type]
    )

    job = await runner.submit(
        JobSubmit(user_id="u1", message="hi", capability="tutoring")
    )

    # Wait for the job to terminalize.
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
    assert stored.status == JobStatus.FAILED, (
        f"expected FAILED, got {stored.status.value}"
    )
    assert stored.result is not None
    from tutor.services.jobs.contracts import JobResultContract

    contract = JobResultContract.model_validate(stored.result)
    assert contract.error is not None
    # The stable lifecycle code is CAPABILITY_FAILED, not MISSING_RESULT.
    assert contract.error.code == "CAPABILITY_FAILED", (
        f"expected CAPABILITY_FAILED, got {contract.error.code!r} "
        f"(message={contract.error.message!r})"
    )
    # Public diagnostics point to the protected artifact; raw provider
    # messages and tracebacks never enter the persisted public contract.
    assert stored.error_log_ref is not None
    assert contract.error.diagnostic == stored.error_log_ref.artifact_key
    error_log = tmp_path / "data" / stored.error_log_ref.artifact_key
    log_text = error_log.read_text(encoding="utf-8")
    assert "quality_review" in log_text
    assert "RuntimeError" in log_text
    assert stored.error_log_ref.artifact_key

    await store.close()
    reset_job_store()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))
