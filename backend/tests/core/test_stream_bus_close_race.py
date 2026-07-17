"""StreamBus close/emit ordering regressions."""

from __future__ import annotations

import asyncio

import pytest
from tutor.core.stream import StreamEvent, StreamEventType
from tutor.core.stream_bus import StreamBus


@pytest.mark.asyncio
async def test_close_delivers_sentinel_after_full_queue_drains() -> None:
    bus = StreamBus(max_queue_size=1)
    queue = bus.subscribe()
    await bus.emit(StreamEvent(type=StreamEventType.PROGRESS, content="buffered"))

    close_task = asyncio.create_task(bus.close())
    await asyncio.sleep(0)
    assert not close_task.done(), "close must wait rather than drop the sentinel"

    buffered = await asyncio.wait_for(queue.get(), timeout=1)
    assert buffered is not None
    assert buffered.content == "buffered"
    await asyncio.wait_for(close_task, timeout=1)
    assert await asyncio.wait_for(queue.get(), timeout=1) is None


@pytest.mark.asyncio
async def test_emit_cannot_enqueue_after_close_sentinel() -> None:
    bus = StreamBus(max_queue_size=2)
    queue = bus.subscribe()

    await bus.close()
    await bus.emit(StreamEvent(type=StreamEventType.PROGRESS, content="late"))

    assert await asyncio.wait_for(queue.get(), timeout=1) is None
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.05)
