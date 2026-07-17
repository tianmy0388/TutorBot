"""Race, cancellation, security, and durable-result regressions for JobRunner."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from tutor.core.capability_result import CapabilityResult, FollowUpTaskSpec
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.contracts import JobResultContract
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import Job, JobStatus, JobSubmit
from tutor.services.jobs.store import JobStore


class _Capabilities:
    def __init__(self, capability: object) -> None:
        self.capability = capability

    def get(self, name: str) -> object | None:
        return self.capability


async def _wait_status(
    store: JobStore,
    job_id: str,
    statuses: set[JobStatus],
    *,
    timeout: float = 3,
) -> Job:
    async with asyncio.timeout(timeout):
        while True:
            job = await store.get(job_id)
            if job is not None and job.status in statuses:
                return job
            await asyncio.sleep(0.01)


@pytest.fixture
async def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JobStore:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    value = JobStore(db_path=tmp_path / "jobs.db")
    await value.init()
    yield value
    await value.close()


@pytest.mark.asyncio
async def test_terminal_compatibility_events_are_not_visible_before_commit(
    tmp_path: Path,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class PausingStore(JobStore):
        async def set_terminal(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            entered.set()
            await release.wait()
            return await super().set_terminal(*args, **kwargs)

    class Success:
        async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
            return CapabilityResult(assistant_message="ok", payload={"ok": True})

    pausing_store = PausingStore(db_path=tmp_path / "atomic.db")
    await pausing_store.init()
    runner = JobRunner(
        job_store=pausing_store,
        capability_registry=_Capabilities(Success()),  # type: ignore[arg-type]
    )
    job = await runner.submit(JobSubmit(capability="tutoring"))
    await asyncio.wait_for(entered.wait(), timeout=2)
    before = await pausing_store.get(job.job_id)
    assert before is not None
    assert before.status == JobStatus.RUNNING
    assert not [
        event
        for event in before.events
        if event.get("type") in {"result", "error", "done", "cancelled", "job_terminal"}
    ]
    release.set()
    await _wait_status(pausing_store, job.job_id, {JobStatus.SUCCEEDED})
    await pausing_store.close()


@pytest.mark.asyncio
async def test_saturated_capability_stream_drains_and_terminalizes(
    store: JobStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_init = StreamBus.__init__

    def tiny_queue_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["max_queue_size"] = 3
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(StreamBus, "__init__", tiny_queue_init)

    class Saturating:
        async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
            for index in range(5):
                await bus.progress(f"p-{index}", index, 5, source="saturating")
            return CapabilityResult(assistant_message="drained")

    runner = JobRunner(
        job_store=store,
        capability_registry=_Capabilities(Saturating()),  # type: ignore[arg-type]
    )
    job = await runner.submit(JobSubmit(capability="tutoring"))
    stored = await _wait_status(store, job.job_id, {JobStatus.SUCCEEDED}, timeout=5)
    assert any(event.get("type") == "progress" for event in stored.events)
    assert sum(event.get("type") == "job_terminal" for event in stored.events) == 1


@pytest.mark.asyncio
async def test_allowed_resource_events_are_recursively_redacted(store: JobStore) -> None:
    secret = "SECRET_TOKEN_RUNNER_RESOURCE_d43a"
    code_secret = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"

    class EmitsSensitiveResource:
        async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
            await bus.resource(
                {
                    "resource_id": "resource-1",
                    "type": "document",
                    "title": "Tokenization lesson",
                    "content": "A normal educational token is a unit of text.",
                    "format_specific": {
                        "failure": {
                            "code": "DOCUMENT_GENERATION_FAILED",
                            "message": "Document generation failed",
                            "retryable": True,
                        }
                    },
                    "metadata": {
                        "api_key": secret,
                        "nested": {
                            "authorization": "Bearer bearer-runner-secret",
                            "private_reasoning": "hidden chain",
                            "hidden_tests": "private grader",
                            "note": f"provider returned token={secret}",
                        },
                    },
                },
                source="sensitive-capability",
                metadata={
                    "password": "runner-password",
                    "source_code": (
                        "token = tokenizer.next_token()\n"
                        f"api_key = \"{code_secret}\"\n"
                        "print(token)"
                    ),
                },
            )
            return CapabilityResult(assistant_message="ok")

    runner = JobRunner(
        job_store=store,
        capability_registry=_Capabilities(EmitsSensitiveResource()),  # type: ignore[arg-type]
    )
    job = await runner.submit(JobSubmit(capability="tutoring"))
    stored = await _wait_status(store, job.job_id, {JobStatus.SUCCEEDED})

    public = json.dumps(stored.to_full_dict(), ensure_ascii=False)
    assert secret not in public
    assert "bearer-runner-secret" not in public
    assert "runner-password" not in public
    assert "hidden chain" not in public
    assert "private grader" not in public
    assert code_secret not in public
    assert "token = tokenizer.next_token()" in public
    assert "print(token)" in public
    assert "[REDACTED]" in public
    assert "DOCUMENT_GENERATION_FAILED" in public
    assert "A normal educational token is a unit of text." in public
    contract = JobResultContract.model_validate(stored.result)
    partial = next(
        artifact
        for artifact in contract.partial_artifacts
        if artifact.resource_id == "resource-1"
    )
    assert partial.status == "failed"
    assert partial.error is not None
    assert partial.error.code == "DOCUMENT_GENERATION_FAILED"


@pytest.mark.asyncio
async def test_cancel_commits_terminal_before_return_and_replays_it(store: JobStore) -> None:
    started = asyncio.Event()

    class Blocking:
        async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    runner = JobRunner(
        job_store=store,
        capability_registry=_Capabilities(Blocking()),  # type: ignore[arg-type]
    )
    job = await runner.submit(JobSubmit(capability="tutoring", session_id="cancel-session"))
    await asyncio.wait_for(started.wait(), timeout=2)

    assert await runner.cancel(job.job_id)
    stored = await store.get(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.CANCELLED
    terminals = [event for event in stored.events if event.get("type") == "job_terminal"]
    assert len(terminals) == 1

    replay = [event async for event in runner.subscribe(job.job_id)]
    assert [event["event_id"] for event in replay].count(terminals[0]["event_id"]) == 1


@pytest.mark.asyncio
async def test_cancel_returns_false_when_success_commits_after_active_read(
    tmp_path: Path,
) -> None:
    class WinningSuccessStore(JobStore):
        intercepted = False

        async def get(self, job_id: str):  # type: ignore[no-untyped-def]
            snapshot = await super().get(job_id)
            task = asyncio.current_task()
            if (
                task is not None
                and task.get_name() == "cancel-race"
                and not self.intercepted
                and snapshot is not None
            ):
                self.intercepted = True
                contract = JobResultContract(
                    job_id=snapshot.job_id,
                    capability=snapshot.capability,
                    status="succeeded",
                    assistant_message="success won",
                )
                assert await self.set_terminal(
                    snapshot.job_id,
                    status=JobStatus.SUCCEEDED,
                    finished_at=contract.finished_at,
                    result=contract.model_dump(mode="json"),
                    terminal_events=[
                        {"type": "result", "content": "success won"},
                        {"type": "done", "content": ""},
                        {
                            "type": "job_terminal",
                            "content": "success won",
                            "metadata": {
                                "contract": contract.model_dump(mode="json")
                            },
                        },
                    ],
                )
            return snapshot

    race_store = WinningSuccessStore(db_path=tmp_path / "cancel-race.db")
    await race_store.init()
    job = Job(user_id="u1", status=JobStatus.RUNNING)
    await race_store.save(job)
    runner = JobRunner(
        job_store=race_store,
        capability_registry=_Capabilities(object()),  # type: ignore[arg-type]
    )

    cancelled = await asyncio.create_task(
        runner.cancel(job.job_id),
        name="cancel-race",
    )
    stored = await race_store.get(job.job_id)

    assert cancelled is False
    assert stored is not None
    assert stored.status == JobStatus.SUCCEEDED
    assert sum(event.get("type") == "job_terminal" for event in stored.events) == 1
    await race_store.close()


@pytest.mark.asyncio
async def test_terminalization_between_subscribe_read_and_register_is_replayed(
    store: JobStore,
) -> None:
    release_capability = asyncio.Event()

    class Blocking:
        async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
            await release_capability.wait()
            return CapabilityResult(assistant_message="finished in gap")

    runner = JobRunner(
        job_store=store,
        capability_registry=_Capabilities(Blocking()),  # type: ignore[arg-type]
    )
    job = await runner.submit(JobSubmit(capability="tutoring"))
    await _wait_status(store, job.job_id, {JobStatus.RUNNING})

    original_get = store.get
    first_snapshot = asyncio.Event()
    release_snapshot = asyncio.Event()
    intercepted = False

    async def delayed_get(job_id: str):  # type: ignore[no-untyped-def]
        nonlocal intercepted
        snapshot = await original_get(job_id)
        task = asyncio.current_task()
        if task is not None and task.get_name() == "subscribe-race" and not intercepted:
            intercepted = True
            first_snapshot.set()
            await release_snapshot.wait()
        return snapshot

    store.get = delayed_get  # type: ignore[method-assign]
    stream = runner.subscribe(job.job_id)
    next_event = asyncio.create_task(anext(stream), name="subscribe-race")
    await asyncio.wait_for(first_snapshot.wait(), timeout=2)
    release_capability.set()
    await _wait_status(store, job.job_id, {JobStatus.SUCCEEDED})
    release_snapshot.set()

    event = await asyncio.wait_for(next_event, timeout=2)
    assert event["type"] == "result" or event["type"] == "job_terminal"
    remaining = [event async for event in stream]
    all_events = [event, *remaining]
    assert sum(item.get("type") == "job_terminal" for item in all_events) == 1


@pytest.mark.asyncio
async def test_secret_traceback_only_exists_in_protected_artifact(
    store: JobStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "sk-secret-token-123"

    class SecretFailure:
        async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
            raise RuntimeError(f"provider rejected {secret}")

    runner = JobRunner(
        job_store=store,
        capability_registry=_Capabilities(SecretFailure()),  # type: ignore[arg-type]
    )
    job = await runner.submit(JobSubmit(capability="tutoring"))
    stored = await _wait_status(store, job.job_id, {JobStatus.FAILED})

    public_blob = json.dumps(stored.to_full_dict(), ensure_ascii=False, default=str)
    captured = capsys.readouterr()
    assert secret not in public_blob
    assert secret not in captured.out
    assert secret not in captured.err
    assert stored.error_log_ref is not None
    artifact = tmp_path / "data" / stored.error_log_ref.artifact_key
    assert secret in artifact.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_follow_up_specs_survive_result_reload(store: JobStore) -> None:
    spec = FollowUpTaskSpec(
        kind="video_render",
        payload={"package_id": "pkg-1", "resource_id": "video-1"},
        dedupe_key="video:pkg-1:video-1",
    )

    class WithFollowUp:
        async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
            return CapabilityResult(assistant_message="queued", follow_up_tasks=(spec,))

    runner = JobRunner(
        job_store=store,
        capability_registry=_Capabilities(WithFollowUp()),  # type: ignore[arg-type]
    )
    job = await runner.submit(JobSubmit(capability="tutoring"))
    stored = await _wait_status(store, job.job_id, {JobStatus.SUCCEEDED})
    contract = JobResultContract.model_validate(stored.result)
    assert len(contract.follow_up_tasks) == 1
    assert contract.follow_up_tasks[0].kind == "video_render"
    assert contract.follow_up_tasks[0].payload == spec.payload
    assert contract.follow_up_tasks[0].dedupe_key == spec.dedupe_key


@pytest.mark.asyncio
async def test_runner_terminal_public_projection_redacts_internal_payloads(
    store: JobStore,
) -> None:
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkFkYSJ9."
        "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    )
    provider_key = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    pem_body = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="
    pem = f"-----BEGIN PRIVATE KEY-----\n{pem_body}\n-----END PRIVATE KEY-----"
    lesson_code = "token = tokenizer.next_token()\nprint(token)\n"
    generated_code = (
        lesson_code
        + f'api_key = "{provider_key}"\n'
        + f'session = "{jwt}"\n'
    )
    follow_up_payload = {
        "resource_id": "video-1",
        "refresh_token": "follow-up-refresh-secret",
        "source_code": generated_code,
    }
    spec = FollowUpTaskSpec(
        kind="video_render",
        payload=follow_up_payload,
        dedupe_key="video:pkg-1:video-1",
    )
    internal_result = CapabilityResult(
        assistant_message="queued",
        payload={
            "hidden_tests": "hidden-test-secret",
            "private_reasoning": "private-reasoning-secret",
            "refresh_token": "result-refresh-secret",
            "provider_note": f"JWT={jwt}; key={provider_key}\n{pem}",
            "source_code": generated_code,
        },
        follow_up_tasks=(spec,),
    )

    class WithPrivateTerminalPayload:
        async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
            return internal_result

    runner = JobRunner(
        job_store=store,
        capability_registry=_Capabilities(WithPrivateTerminalPayload()),  # type: ignore[arg-type]
    )
    job = await runner.submit(JobSubmit(capability="tutoring"))
    stored = await _wait_status(store, job.job_id, {JobStatus.SUCCEEDED})

    public_blob = json.dumps(stored.to_full_dict(), ensure_ascii=False, default=str)
    for secret in (
        "hidden-test-secret",
        "private-reasoning-secret",
        "result-refresh-secret",
        "follow-up-refresh-secret",
        jwt,
        provider_key,
        pem_body,
    ):
        assert secret not in public_blob
    assert "token = tokenizer.next_token()" in public_blob
    assert "print(token)" in public_blob

    result_event = next(event for event in stored.events if event["type"] == "result")
    result_payload = json.loads(result_event["content"])
    assert lesson_code in result_payload["source_code"]
    assert result_event["metadata"]["follow_up_tasks"][0]["payload"][
        "resource_id"
    ] == "video-1"
    contract = JobResultContract.model_validate(stored.result)
    assert contract.follow_up_tasks[0].payload["resource_id"] == "video-1"

    # Public projections must never mutate the internal hand-off that a
    # durable follow-up scheduler consumes.
    assert internal_result.payload["refresh_token"] == "result-refresh-secret"
    assert spec.payload["refresh_token"] == "follow-up-refresh-secret"
    assert provider_key in spec.payload["source_code"]


@pytest.mark.asyncio
async def test_capability_events_are_normalized_to_allowed_types(store: JobStore) -> None:
    class Noisy:
        async def run(self, context: UnifiedContext, bus: StreamBus) -> CapabilityResult:
            await bus.thinking("thinking detail", source="noisy")
            await bus.observation("observation detail", source="noisy")
            await bus.content("content detail", source="noisy")
            await bus.tool_call("search", {"q": "x"}, source="noisy")
            await bus.sources([{"title": "source"}], source="noisy")
            return CapabilityResult(assistant_message="normalized")

    runner = JobRunner(
        job_store=store,
        capability_registry=_Capabilities(Noisy()),  # type: ignore[arg-type]
    )
    job = await runner.submit(JobSubmit(capability="tutoring"))
    stored = await _wait_status(store, job.job_id, {JobStatus.SUCCEEDED})

    capability_events = [event for event in stored.events if event.get("source") == "noisy"]
    allowed = {"progress", "stage_start", "stage_end", "resource", "sources"}
    assert capability_events
    assert {event["type"] for event in capability_events} <= allowed
    observations = [
        event
        for event in capability_events
        if event.get("metadata", {}).get("original_event_type") == "observation"
    ]
    assert observations[0]["metadata"]["message"] == "observation detail"


@pytest.mark.asyncio
async def test_reaper_commits_error_and_terminal_bundle(store: JobStore) -> None:
    job = Job(user_id="u1", status=JobStatus.RUNNING, started_at=None)
    await store.save(job)
    runner = JobRunner(job_store=store, capability_registry=_Capabilities(object()))  # type: ignore[arg-type]

    assert await runner.resume_active_jobs() == 1
    stored = await store.get(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.FAILED
    assert [event["type"] for event in stored.events[-2:]] == ["error", "job_terminal"]
