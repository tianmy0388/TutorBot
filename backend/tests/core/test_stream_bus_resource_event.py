"""Regression test: StreamBus.resource() emits a ``RESOURCE`` event.

187b2955 trace analysis (Phase 1) showed the frontend only updated
``latestPackage`` from the final ``RESULT`` event — any earlier-stage
failure left the user with an empty right pane. We added a
``StreamEventType.RESOURCE`` event + ``StreamBus.resource()`` helper so
the capability can stream single-resource readiness incrementally.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from tutor.core.stream import StreamEventType
from tutor.core.stream_bus import StreamBus


@pytest.mark.asyncio
async def test_stream_bus_emits_resource_event_with_metadata() -> None:
    bus = StreamBus()
    # subscribe_iter yields from a queue; we use the lower-level
    # ``subscribe()`` + ``get`` so we can stop after one event without
    # having to ``bus.close()`` (which would also end any other tests
    # sharing the loop).
    q = bus.subscribe()

    # Bare-minimum payload that the capability will pass in
    fake_resource = {
        "resource_id": "abc123",
        "type": "document",
        "title": "测试资源",
        "content": "# 测试\n\nbody",
    }
    await bus.resource(
        fake_resource,
        source="resource_capability",
        stage="content_and_pedagogy",
    )

    evt = await asyncio.wait_for(q.get(), timeout=2.0)
    assert evt.type == StreamEventType.RESOURCE
    # StreamBus.resource() puts the payload in metadata, not content,
    # so the trace panel doesn't double-render it as text.
    assert evt.metadata.get("resource") == fake_resource
    assert evt.metadata.get("resource_id") == "abc123"
    assert evt.metadata.get("resource_type") == "document"
    assert evt.metadata.get("title") == "测试资源"
    assert evt.source == "resource_capability"
    assert evt.stage == "content_and_pedagogy"


@pytest.mark.asyncio
async def test_stream_bus_resource_with_non_dict_payload() -> None:
    """Non-JSON-serialisable payloads must still emit (graceful degrade)."""
    bus = StreamBus()
    q = bus.subscribe()

    # Object with a weird repr — must not crash the bus.
    class _Weird:
        def __repr__(self) -> str:
            return "<weird>"

    await bus.resource(_Weird(), source="capability")
    evt = await asyncio.wait_for(q.get(), timeout=2.0)
    assert evt.type == StreamEventType.RESOURCE
    # Fallback to ``str(...)`` when JSON round-trip fails.
    assert "weird" in str(evt.metadata.get("resource", ""))


def test_resource_event_type_exists_in_enum() -> None:
    """Frontend dispatches on the string value, so it MUST be ``"resource"``."""
    assert StreamEventType.RESOURCE.value == "resource"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))