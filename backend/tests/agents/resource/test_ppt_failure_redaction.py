"""PPT render failures are explicit, filterable, and secret-free."""

from __future__ import annotations

import json
from pathlib import Path
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


class _SuccessfulPPTService:
    def __init__(self, path: Path) -> None:
        self.path = path

    def build(self, **kwargs: Any) -> Path:
        return self.path


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


@pytest.mark.asyncio
async def test_ppt_success_observation_never_emits_absolute_host_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    absolute_path = (tmp_path / "private-host-root" / "deck.pptx").resolve()
    absolute_path.parent.mkdir(parents=True)
    absolute_path.write_bytes(b"pptx-placeholder")
    monkeypatch.setattr(
        "tutor.agents.resource.ppt_generator._peek_pptx",
        lambda path: (["Intro"], 1),
    )
    agent = PPTGeneratorAgent(ppt_service=_SuccessfulPPTService(absolute_path))
    bus = StreamBus()
    queue = bus.subscribe()

    await agent.process(
        topic="Calculus",
        source_content="Limits and derivatives",
        stream=bus,
    )

    events: list[dict[str, Any]] = []
    while not queue.empty():
        event = queue.get_nowait()
        if event is not None:
            events.append(event.to_dict())
    strings: list[str] = []

    def _collect_strings(value: Any) -> None:
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, dict):
            for nested in value.values():
                _collect_strings(nested)
        elif isinstance(value, list):
            for nested in value:
                _collect_strings(nested)

    _collect_strings(events)
    assert not [value for value in strings if Path(value).is_absolute()]
    assert all("pptx_path" not in event.get("metadata", {}) for event in events)
