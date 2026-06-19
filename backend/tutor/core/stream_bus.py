"""Async fan-out event bus for streaming a single conversation turn.

The :class:`StreamBus` is the central spine of streaming. Producers
(orchestrator, agents, tools) ``emit()`` events; consumers (typically one
WebSocket sender task per active turn) ``subscribe()`` and receive all
events until ``close()`` is called.

Why a fan-out bus instead of a single queue?
    A single conversation may have multiple consumers (UI trace panel,
    logging middleware, analytics, etc.). Fan-out means each consumer
    gets its own queue and can be added/removed without affecting
    producers or other consumers.

Design inspired by DeepTutor's :class:`deeptutor.core.stream_bus.StreamBus`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger

from tutor.core.stream import StreamEvent, StreamEventType


class StreamBus:
    """Async fan-out event bus.

    Example
    -------
    >>> async def run():
    ...     bus = StreamBus()
    ...     consumer_task = asyncio.create_task(_consume(bus))
    ...     async with bus.stage("analysis", "planner"):
    ...         await bus.thinking("let me think...", "planner", "analysis")
    ...         await bus.content("hello ", "planner", "analysis")
    ...         await bus.content("world", "planner", "analysis")
    ...     await bus.done()
    ...     await consumer_task
    """

    def __init__(
        self,
        *,
        session_id: str = "",
        turn_id: str = "",
        max_queue_size: int = 1000,
    ) -> None:
        self._session_id = session_id
        self._turn_id = turn_id
        self._max_queue_size = max_queue_size
        self._subscribers: list[asyncio.Queue[StreamEvent | None]] = []
        self._closed = False
        self._seq = 0
        self._lock = asyncio.Lock()
        self._stage_stack: list[tuple[str, str, dict[str, Any]]] = []

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[StreamEvent | None]:
        """Register a new consumer queue.

        Returns a queue that yields :class:`StreamEvent` instances until the
        bus is closed, at which point it receives a single ``None`` sentinel.
        """
        q: asyncio.Queue[StreamEvent | None] = asyncio.Queue(maxsize=self._max_queue_size)
        self._subscribers.append(q)
        return q

    async def subscribe_iter(self) -> AsyncIterator[StreamEvent]:
        """Async iterator wrapper around :meth:`subscribe`."""
        q = self.subscribe()
        try:
            while True:
                evt = await q.get()
                if evt is None:
                    return
                yield evt
        finally:
            # Remove queue from subscribers when consumer disconnects
            with contextlib.suppress(ValueError):
                self._subscribers.remove(q)

    # ------------------------------------------------------------------
    # Emit
    # ------------------------------------------------------------------

    async def emit(self, event: StreamEvent) -> None:
        """Emit an event to all subscribers.

        If a subscriber's queue is full, the event is dropped for that
        subscriber (with a warning). We never block producers.
        """
        if self._closed:
            logger.warning("StreamBus.emit called after close(); dropping event")
            return

        # Stamp session/turn ids and sequence number
        if not event.session_id:
            event.session_id = self._session_id
        if not event.turn_id:
            event.turn_id = self._turn_id
        async with self._lock:
            self._seq += 1
            event.seq = self._seq

        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    f"Subscriber queue full (size={q.maxsize}); dropping event {event.type}"
                )

    async def close(self) -> None:
        """Close the bus. All subscribers will receive a ``None`` sentinel."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)

    # ------------------------------------------------------------------
    # Convenience emit helpers
    # ------------------------------------------------------------------

    async def _make(
        self,
        event_type: StreamEventType,
        *,
        content: str = "",
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> StreamEvent:
        evt = StreamEvent(
            type=event_type,
            source=source,
            stage=stage,
            content=content,
            metadata=metadata or {},
        )
        await self.emit(evt)
        return evt

    async def content(
        self,
        text: str,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._make(StreamEventType.CONTENT, content=text, source=source, stage=stage, metadata=metadata)

    async def content_final(
        self,
        text: str,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._make(
            StreamEventType.CONTENT_FINAL,
            content=text,
            source=source,
            stage=stage,
            metadata=metadata,
        )

    async def thinking(
        self,
        text: str,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._make(StreamEventType.THINKING, content=text, source=source, stage=stage, metadata=metadata)

    async def observation(
        self,
        text: str,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._make(
            StreamEventType.OBSERVATION,
            content=text,
            source=source,
            stage=stage,
            metadata=metadata,
        )

    async def tool_call(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        md = dict(metadata or {})
        md["tool_name"] = tool_name
        md["args"] = args or {}
        await self._make(
            StreamEventType.TOOL_CALL,
            content=tool_name,
            source=source,
            stage=stage,
            metadata=md,
        )

    async def tool_result(
        self,
        tool_name: str,
        result: Any,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        md = dict(metadata or {})
        md["tool_name"] = tool_name
        md["result"] = result
        await self._make(
            StreamEventType.TOOL_RESULT,
            content=str(result)[:200] if result is not None else "",
            source=source,
            stage=stage,
            metadata=md,
        )

    async def progress(
        self,
        message: str,
        current: int,
        total: int,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        md = dict(metadata or {})
        md["current"] = current
        md["total"] = total
        await self._make(
            StreamEventType.PROGRESS,
            content=message,
            source=source,
            stage=stage,
            metadata=md,
        )

    async def sources(
        self,
        sources: list[dict[str, Any]],
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        md = dict(metadata or {})
        md["sources"] = sources
        await self._make(
            StreamEventType.SOURCES,
            source=source,
            stage=stage,
            metadata=md,
        )

    async def result(
        self,
        data: Any,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit a final structured result for the turn."""
        import json

        md = dict(metadata or {})
        try:
            payload = json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            payload = str(data)
        await self._make(
            StreamEventType.RESULT,
            content=payload,
            source=source,
            stage=stage,
            metadata=md,
        )

    async def error(
        self,
        message: str,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._make(
            StreamEventType.ERROR,
            content=message,
            source=source,
            stage=stage,
            metadata=metadata,
        )

    async def cancelled(
        self,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._make(
            StreamEventType.CANCELLED,
            source=source,
            stage=stage,
            metadata=metadata,
        )

    async def done(
        self,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._make(
            StreamEventType.DONE,
            source=source,
            stage=stage,
            metadata=metadata,
        )
        await self.close()

    # ------------------------------------------------------------------
    # Stage context manager
    # ------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def stage(
        self,
        name: str,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[None]:
        """Context manager that emits STAGE_START / STAGE_END around a block.

        Nested stages are supported via a stack — the inner stage's END
        is emitted before the outer stage's END.

        Example
        -------
        >>> async with bus.stage("analysis", "planner"):
        ...     # ... do analysis work ...
        ...     pass
        """
        await self._make(
            StreamEventType.STAGE_START,
            content=name,
            source=source,
            stage=name,
            metadata=metadata,
        )
        self._stage_stack.append((name, source, metadata or {}))
        try:
            yield
            status = "completed"
        except BaseException as exc:
            status = f"failed: {type(exc).__name__}: {exc}"
            raise
        finally:
            popped_name, popped_source, popped_md = self._stage_stack.pop()
            end_md = dict(popped_md)
            end_md["status"] = locals().get("status", "completed")
            await self._make(
                StreamEventType.STAGE_END,
                content=popped_name,
                source=popped_source,
                stage=popped_name,
                metadata=end_md,
            )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


__all__ = ["StreamBus"]
