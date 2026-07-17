from __future__ import annotations

import asyncio

import pytest
from tutor.core.stream import StreamEventType
from tutor.core.stream_bus import StreamBus


def _drain(queue):  # type: ignore[no-untyped-def]
    events = []
    while not queue.empty():
        event = queue.get_nowait()
        if event is not None:
            events.append(event)
    return events


@pytest.mark.asyncio
async def test_nested_stages_in_one_task_remain_lifo() -> None:
    bus = StreamBus()
    queue = bus.subscribe()

    async with bus.stage("outer", source="one"), bus.stage("inner", source="one"):
        pass

    assert [
        (event.type, event.stage)
        for event in _drain(queue)
    ] == [
        (StreamEventType.STAGE_START, "outer"),
        (StreamEventType.STAGE_START, "inner"),
        (StreamEventType.STAGE_END, "inner"),
        (StreamEventType.STAGE_END, "outer"),
    ]


@pytest.mark.asyncio
async def test_interleaved_tasks_end_their_own_stage() -> None:
    bus = StreamBus()
    queue = bus.subscribe()
    entered_a = asyncio.Event()
    entered_b = asyncio.Event()
    release_a = asyncio.Event()
    release_b = asyncio.Event()

    async def branch_a() -> None:
        async with bus.stage("branch-a", source="agent-a", metadata={"branch": "a"}):
            entered_a.set()
            await release_a.wait()

    async def branch_b() -> None:
        await entered_a.wait()
        async with bus.stage("branch-b", source="agent-b", metadata={"branch": "b"}):
            entered_b.set()
            await release_b.wait()

    task_a = asyncio.create_task(branch_a())
    task_b = asyncio.create_task(branch_b())
    await entered_b.wait()
    release_a.set()
    await task_a
    release_b.set()
    await task_b

    ends = [
        event
        for event in _drain(queue)
        if event.type == StreamEventType.STAGE_END
    ]
    assert [event.stage for event in ends] == ["branch-a", "branch-b"]
    assert [event.source for event in ends] == ["agent-a", "agent-b"]
    assert [event.metadata["branch"] for event in ends] == ["a", "b"]


@pytest.mark.asyncio
async def test_cancelled_stage_cleans_task_local_ancestry() -> None:
    bus = StreamBus()
    queue = bus.subscribe()
    entered = asyncio.Event()

    async def blocked() -> None:
        async with bus.stage("cancelled", source="worker"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(blocked())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with bus.stage("after-cancel", source="worker"):
        pass

    ends = [
        event
        for event in _drain(queue)
        if event.type == StreamEventType.STAGE_END
    ]
    assert [(event.stage, event.metadata["status"]) for event in ends] == [
        ("cancelled", "failed"),
        ("after-cancel", "completed"),
    ]


@pytest.mark.asyncio
async def test_exception_cleanup_is_isolated_between_bus_instances() -> None:
    first = StreamBus()
    second = StreamBus()
    first_queue = first.subscribe()
    second_queue = second.subscribe()

    with pytest.raises(RuntimeError):
        async with first.stage("failed", source="first"):
            raise RuntimeError("private provider detail")
    async with second.stage("healthy", source="second"):
        pass

    first_end = _drain(first_queue)[-1]
    second_end = _drain(second_queue)[-1]
    assert (first_end.stage, first_end.source, first_end.metadata["status"]) == (
        "failed",
        "first",
        "failed",
    )
    assert (second_end.stage, second_end.source, second_end.metadata["status"]) == (
        "healthy",
        "second",
        "completed",
    )
