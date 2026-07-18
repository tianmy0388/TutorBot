from __future__ import annotations

import asyncio
import json

import pytest

from tutor.capabilities.path_planning import PathPlanningCapability
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus, StreamEventType


@pytest.mark.asyncio
async def test_path_capability_emits_real_planned_path() -> None:
    capability = PathPlanningCapability()
    context = UnifiedContext(
        user_id="path-student",
        user_message="接下来学什么",
        metadata={"course": "ai_introduction"},
    )
    stream = StreamBus(session_id=context.session_id, turn_id=context.turn_id)
    events = []

    async def collect() -> None:
        async for event in stream.subscribe_iter():
            events.append(event)

    collector = asyncio.create_task(collect())
    await asyncio.sleep(0)

    await capability.run(context, stream)
    await collector
    result_event = next(event for event in events if event.type == StreamEventType.RESULT)
    payload = json.loads(result_event.content)

    assert payload["course"] == "ai_introduction"
    assert payload["path_id"]
    assert payload["nodes"]
    assert all("node_id" in node for node in payload["nodes"])
    assert not any("占位" in event.content for event in events)
