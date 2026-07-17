"""Security regressions for failures caught inside nested agents."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from loguru import logger
from tutor.agents.base_agent import BaseAgent
from tutor.agents.safety.content_safety import ContentSafetyAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus

SECRET = "SECRET_TOKEN_NESTED_AGENT_9f0e"


class _FailingLLM:
    model = "fake-model"

    def __init__(self, exc_type: type[Exception] = RuntimeError) -> None:
        self.exc_type = exc_type

    async def call(self, request: Any) -> Any:
        raise self.exc_type(
            f"provider body {SECRET} authorization=Bearer bearer-credential-123"
        )

    async def stream(self, request: Any):  # type: ignore[no-untyped-def]
        if False:  # pragma: no cover - makes this an async generator
            yield None
        raise self.exc_type(
            f"https://api-user:api-password@provider.invalid/v1 {SECRET}"
        )


class _Agent(BaseAgent):
    module_name = "test"
    agent_name = "failure_redaction_test"

    async def process(self, context, stream=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def _log_sink() -> tuple[list[str], int]:
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), format="{message}")
    return messages, sink_id


def _drain(queue: asyncio.Queue[Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    while not queue.empty():
        event = queue.get_nowait()
        if event is not None:
            events.append(event.to_dict())
    return events


@pytest.mark.asyncio
async def test_base_agent_call_failure_emits_only_stable_public_failure() -> None:
    agent = _Agent(llm=_FailingLLM())  # type: ignore[arg-type]
    bus = StreamBus()
    queue = bus.subscribe()
    logs, sink_id = _log_sink()
    try:
        with pytest.raises(RuntimeError, match=SECRET):
            await agent.call_llm(messages=[], stream=bus)
    finally:
        logger.remove(sink_id)

    public = json.dumps({"logs": logs, "events": _drain(queue)}, ensure_ascii=False)
    assert SECRET not in public
    assert "bearer-credential-123" not in public
    assert "AGENT_LLM_CALL_FAILED" in public


@pytest.mark.asyncio
async def test_base_agent_retry_metadata_never_contains_provider_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _Agent(llm=_FailingLLM(ConnectionError))  # type: ignore[arg-type]
    bus = StreamBus()
    queue = bus.subscribe()
    logs, sink_id = _log_sink()

    async def _no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    try:
        with pytest.raises(ConnectionError, match=SECRET):
            await agent.call_llm_with_retry(
                messages=[], stream=bus, max_attempts=2
            )
    finally:
        logger.remove(sink_id)

    public = json.dumps({"logs": logs, "events": _drain(queue)}, ensure_ascii=False)
    assert SECRET not in public
    assert "bearer-credential-123" not in public
    assert "AGENT_LLM_RETRY" in public
    assert "AGENT_LLM_RETRIES_EXHAUSTED" in public


@pytest.mark.asyncio
async def test_base_agent_stream_failure_emits_only_stable_public_failure() -> None:
    agent = _Agent(llm=_FailingLLM())  # type: ignore[arg-type]
    bus = StreamBus()
    queue = bus.subscribe()
    logs, sink_id = _log_sink()
    try:
        with pytest.raises(RuntimeError, match=SECRET):
            await agent.stream_llm(messages=[], stream=bus, chunk_size=1)
    finally:
        logger.remove(sink_id)

    public = json.dumps({"logs": logs, "events": _drain(queue)}, ensure_ascii=False)
    assert SECRET not in public
    assert "api-password" not in public
    assert "AGENT_LLM_STREAM_FAILED" in public


@pytest.mark.asyncio
async def test_content_safety_degradation_is_conservative_and_redacted() -> None:
    agent = ContentSafetyAgent(llm=_FailingLLM())  # type: ignore[arg-type]
    logs, sink_id = _log_sink()
    try:
        report = await agent.process(UnifiedContext(), content="ordinary lesson text")
    finally:
        logger.remove(sink_id)

    public = json.dumps({"logs": logs, "report": report.to_dict()}, ensure_ascii=False)
    assert report.is_safe is True  # existing failed-open policy is preserved
    assert SECRET not in public
    assert "bearer-credential-123" not in public
    assert "CONTENT_SAFETY_CHECK_FAILED" in public
