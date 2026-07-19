"""Terminal conversation persistence (2026-07-19 learning-experience plan).

When a job with a session reaches a terminal state, the runner appends
two messages to the conversation:

  1. a ``workflow_timeline`` message (stage pairing + progress excerpt)
  2. exactly one assistant message from the contract's
     ``assistant_message`` — tutoring results additionally carry
     ``metadata.kind == "tutor_answer"`` and ``metadata.answer``.

The writes are idempotent and best-effort: a failure must never fail
the job, and jobs without a persisted conversation write nothing.
"""

from __future__ import annotations

import asyncio

import pytest
from tutor.core.capability_result import CapabilityResult
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.conversations import (
    get_conversation_store,
    reset_conversation_store,
)
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import JobStatus, JobSubmit
from tutor.services.jobs.store import get_job_store, reset_job_store

TUTOR_ANSWER = {
    "tldr": "自注意力就是给每个词分配关注点。",
    "intuition": "像读书时划重点。",
    "principle": "Attention(Q,K,V) = softmax(QK^T/√d)V",
    "example": "『它太累了』里的『它』指代动物。",
    "follow_up_suggestion": "追问：多头注意力为什么有效？",
    "related_concepts": ["transformer", "注意力权重"],
    "full_markdown": "# 自注意力\n\n公式 $E=mc^2$",
    "confidence": 0.9,
    "sources": [],
}


class _TutoringCapability:
    async def run(
        self, context: UnifiedContext, bus: StreamBus
    ) -> CapabilityResult:
        async with bus.stage("question_understanding"):
            await bus.progress("正在理解问题", 1, 2)
        async with bus.stage("answer_composition"):
            await bus.progress("正在整理讲解", 2, 2)
        return CapabilityResult(
            assistant_message=TUTOR_ANSWER["tldr"],
            payload={"understanding": {}, "answer": TUTOR_ANSWER},
        )


class _ResourceCapability:
    async def run(
        self, context: UnifiedContext, bus: StreamBus
    ) -> CapabilityResult:
        async with bus.stage("intent_understanding"):
            await bus.progress("正在理解目标", 1, 1)
        return CapabilityResult(
            assistant_message="已生成 1 项资源，1 项失败：video",
            payload={
                "summary": "done",
                "artifacts": [
                    {"resource_type": "mindmap", "status": "succeeded"},
                    {"resource_type": "video", "status": "failed"},
                ],
            },
        )


class _CapabilitiesStub:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, name: str):
        return self._mapping.get(name)


@pytest.fixture
async def fresh_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    reset_job_store()
    reset_conversation_store()
    store = get_job_store()
    await store.init()
    conv_store = get_conversation_store()
    await conv_store.init()
    yield store, conv_store
    await store.close()
    await conv_store.close()
    reset_job_store()
    reset_conversation_store()


def _runner(store, mapping) -> JobRunner:
    return JobRunner(
        job_store=store,
        capability_registry=_CapabilitiesStub(mapping),  # type: ignore[arg-type]
    )


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
    raise AssertionError(f"job {job_id} did not reach terminal in {timeout}s")


@pytest.mark.asyncio
async def test_tutoring_terminal_writes_workflow_and_tutor_answer(
    fresh_env,
) -> None:
    store, conv_store = fresh_env
    await conv_store.get_or_create(session_id="sess-1", user_id="u1")
    runner = _runner(store, {"tutoring": _TutoringCapability()})
    job = await runner.submit(
        JobSubmit(
            user_id="u1",
            session_id="sess-1",
            capability="tutoring",
            message="解释自注意力",
        )
    )
    await _wait_for_terminal(store, job.job_id)

    messages = await conv_store.list_messages("sess-1")
    workflow = [
        m for m in messages if (m.metadata or {}).get("kind") == "workflow_timeline"
    ]
    tutor = [
        m for m in messages if (m.metadata or {}).get("kind") == "tutor_answer"
    ]
    assert len(workflow) == 1
    assert len(tutor) == 1

    wf = workflow[0]
    assert wf.role == "assistant"
    assert wf.job_id == job.job_id
    assert wf.metadata["client_message_id"] == f"workflow:{job.job_id}"
    snapshot = wf.metadata["workflow"]
    assert snapshot["status"] == "succeeded"
    assert [s["name"] for s in snapshot["stages"]] == [
        "question_understanding",
        "answer_composition",
    ]
    assert all(s["status"] == "completed" for s in snapshot["stages"])
    assert wf.metadata["progress_excerpt"] == ["正在理解问题", "正在整理讲解"]

    msg = tutor[0]
    assert msg.role == "assistant"
    assert msg.content == TUTOR_ANSWER["tldr"]
    assert msg.metadata["client_message_id"] == f"terminal:{job.job_id}"
    answer = msg.metadata["answer"]
    for key in (
        "tldr",
        "intuition",
        "principle",
        "example",
        "follow_up_suggestion",
        "related_concepts",
        "full_markdown",
    ):
        assert key in answer
    assert answer["full_markdown"] == TUTOR_ANSWER["full_markdown"]


@pytest.mark.asyncio
async def test_resource_terminal_writes_plain_assistant_and_counts(
    fresh_env,
) -> None:
    store, conv_store = fresh_env
    await conv_store.get_or_create(session_id="sess-2", user_id="u1")
    runner = _runner(store, {"resource_generation": _ResourceCapability()})
    job = await runner.submit(
        JobSubmit(
            user_id="u1",
            session_id="sess-2",
            capability="resource_generation",
            message="生成学习资源",
        )
    )
    await _wait_for_terminal(store, job.job_id)

    messages = await conv_store.list_messages("sess-2")
    workflow = [
        m for m in messages if (m.metadata or {}).get("kind") == "workflow_timeline"
    ]
    assistants = [
        m
        for m in messages
        if m.role == "assistant"
        and (m.metadata or {}).get("client_message_id") == f"terminal:{job.job_id}"
    ]
    assert len(workflow) == 1
    assert len(assistants) == 1
    assert assistants[0].metadata.get("kind") != "tutor_answer"
    assert "answer" not in assistants[0].metadata
    assert workflow[0].metadata["workflow"]["status"] == "partial"
    assert workflow[0].metadata["resources"] == {"total": 2, "succeeded": 1}


@pytest.mark.asyncio
async def test_terminal_persistence_is_idempotent(fresh_env) -> None:
    store, conv_store = fresh_env
    await conv_store.get_or_create(session_id="sess-3", user_id="u1")
    runner = _runner(store, {"tutoring": _TutoringCapability()})
    job = await runner.submit(
        JobSubmit(
            user_id="u1",
            session_id="sess-3",
            capability="tutoring",
            message="解释自注意力",
        )
    )
    await _wait_for_terminal(store, job.job_id)
    before = await conv_store.list_messages("sess-3")

    # Simulate a duplicate terminal write (e.g. restart replay or the old
    # browser POST having already written an assistant message).
    stored = await store.get(job.job_id)
    assert stored is not None
    from tutor.services.jobs.contracts import JobResultContract

    contract = JobResultContract.model_validate(stored.result)
    await runner._persist_terminal_messages(
        stored,
        contract=contract,
        capability_result=None,
        finished_at=stored.finished_at,
        started_at=stored.started_at,
        events=list(stored.events or []),
    )
    after = await conv_store.list_messages("sess-3")
    assert len(after) == len(before)


@pytest.mark.asyncio
async def test_job_without_conversation_writes_nothing(fresh_env) -> None:
    store, conv_store = fresh_env
    await conv_store.get_or_create(session_id="sess-other", user_id="u1")
    runner = _runner(store, {"tutoring": _TutoringCapability()})
    # No session_id from the client: submit() mints a random one that has
    # no conversation row (the CLI case).
    job = await runner.submit(
        JobSubmit(
            user_id="u1",
            session_id=None,
            capability="tutoring",
            message="解释自注意力",
        )
    )
    await _wait_for_terminal(store, job.job_id)
    stored = await store.get(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.SUCCEEDED
    assert await conv_store.get(job.session_id) is None
    assert await conv_store.list_messages("sess-other") == []


@pytest.mark.asyncio
async def test_persistence_failure_does_not_fail_job(
    fresh_env, monkeypatch
) -> None:
    store, conv_store = fresh_env
    await conv_store.get_or_create(session_id="sess-5", user_id="u1")

    async def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(conv_store, "append_message", _boom)
    runner = _runner(store, {"tutoring": _TutoringCapability()})
    job = await runner.submit(
        JobSubmit(
            user_id="u1",
            session_id="sess-5",
            capability="tutoring",
            message="解释自注意力",
        )
    )
    await _wait_for_terminal(store, job.job_id)
    stored = await store.get(job.job_id)
    assert stored is not None
    assert stored.status == JobStatus.SUCCEEDED
