"""PPT render failures are explicit, filterable, and secret-free."""

from __future__ import annotations

import json
from typing import Any

import pytest
from loguru import logger
from tutor.agents.resource.ppt_generator import PPTGeneratorAgent
from tutor.capabilities.resource_generation import ResourceGenerationCapability
from tutor.core.stream_bus import StreamBus

SECRET = "SECRET_TOKEN_PPT_RENDERER_a8c2"


class _FailingPPTService:
    def build(self, **kwargs: Any) -> Any:
        raise RuntimeError(f"renderer response {SECRET} password=ppt-pass-123")


@pytest.mark.asyncio
async def test_ppt_failure_resource_and_event_are_structured_and_redacted() -> None:
    agent = PPTGeneratorAgent(ppt_service=_FailingPPTService())
    bus = StreamBus()
    queue = bus.subscribe()
    logs: list[str] = []
    sink_id = logger.add(lambda message: logs.append(str(message)), format="{message}")
    try:
        resource = await agent.process(
            topic="Calculus",
            source_content="Normal educational content about tokenization.",
            stream=bus,
        )
    finally:
        logger.remove(sink_id)

    events: list[dict[str, Any]] = []
    while not queue.empty():
        event = queue.get_nowait()
        if event is not None:
            events.append(event.to_dict())
    public = json.dumps(
        {"logs": logs, "events": events, "resource": resource.model_dump(mode="json")},
        ensure_ascii=False,
    )
    assert SECRET not in public
    assert "ppt-pass-123" not in public
    assert resource.format_specific["failure"] == {
        "code": "PPT_RENDER_FAILED",
        "message": "PPT rendering failed",
        "retryable": True,
    }
    assert ResourceGenerationCapability._is_generation_failed(resource) is True
    assert "PPT_RENDER_FAILED" in public
