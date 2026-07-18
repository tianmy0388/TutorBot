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
from contextvars import ContextVar
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
        self._stage_stack: ContextVar[
            tuple[tuple[str, str, dict[str, Any]], ...]
        ] = ContextVar(
            f"stream_bus_stage_stack_{id(self)}",
            default=(),
        )

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[StreamEvent | None]:
        """Register a new consumer queue.

        Returns a queue that yields :class:`StreamEvent` instances until the
        bus is closed, at which point it receives a single ``None`` sentinel.
        """
        q: asyncio.Queue[StreamEvent | None] = asyncio.Queue(maxsize=self._max_queue_size)
        # ``subscribe`` is intentionally synchronous.  On one event loop it
        # cannot interleave with the lock-protected body of ``close``; a late
        # subscriber therefore either joins before the close snapshot or gets
        # its own sentinel immediately after closure.
        if self._closed:
            q.put_nowait(None)
        else:
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
        async with self._lock:
            if self._closed:
                logger.warning("StreamBus.emit called after close(); dropping event")
                return

            # Stamp and enqueue while holding the same lock used by close.
            # This makes enqueue-before-sentinel the only possible ordering.
            if not event.session_id:
                event.session_id = self._session_id
            if not event.turn_id:
                event.turn_id = self._turn_id
            self._seq += 1
            event.seq = self._seq
            for q in tuple(self._subscribers):
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
            subscribers = tuple(self._subscribers)
        if subscribers:
            # A full queue must drain before its sentinel is delivered.  The
            # gather remains alive if the caller is cancelled so closure
            # cannot become permanently half-delivered.
            await asyncio.shield(
                asyncio.gather(*(q.put(None) for q in subscribers))
            )

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

    async def resource(
        self,
        resource: Any,
        *,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Emit an incremental ``RESOURCE`` event for a single finished resource.

        **2026-07-08 fix (187b2955 trace):** previously the only path for
        a resource to reach the frontend was the final ``RESULT`` event,
        which fires AFTER ``video_rendering`` for the whole package. If
        any later step (review, safety, render) failed or the 600s
        timeout fired, the user saw an empty right pane even though
        several resources were already usable. This helper emits one
        ``RESOURCE`` event per finished :class:`Resource` so the
        frontend can render cards incrementally.

        Accepts both :class:`Resource` objects (preferred — pulls
        ``resource_id``/``type``/``title`` via attribute access) and
        plain ``dict`` payloads (the typical wire shape after Pydantic
        ``model_dump(mode="json")``).
        """
        md = dict(metadata or {})
        # Carry the resource in metadata (not content) so the trace
        # panel doesn't double-render it as a TEXT chunk.
        #
        # **2026-07-08 fix (fdb26152 test):** the previous
        # ``json.loads(json.dumps(resource, default=str))`` rendered
        # every non-trivial Pydantic field as a ``repr()`` string
        # (``default=str`` runs only on non-serialisable objects, but
        # a Pydantic v2 ``Resource`` is a non-serialisable type from
        # the stdlib json's point of view). The frontend then saw
        # ``md["resource"]`` as a string, not a dict, and broke. We
        # now prefer ``model_dump(mode="json")`` for Pydantic objects
        # and fall back to a plain dict for already-dumped payloads.
        md["resource"] = _serialise_resource(resource)

        # Pull id / type / title — works for both objects and dicts.
        if isinstance(resource, dict):
            if "resource_id" in resource and "resource_id" not in md:
                md["resource_id"] = str(resource["resource_id"])
            if "type" in resource and "resource_type" not in md:
                md["resource_type"] = str(resource["type"])
            if "title" in resource and "title" not in md:
                md["title"] = str(resource["title"])
        else:
            if hasattr(resource, "type") and "resource_type" not in md:
                md["resource_type"] = str(getattr(resource.type, "value", resource.type))
            if hasattr(resource, "resource_id") and "resource_id" not in md:
                md["resource_id"] = str(resource.resource_id)
            if hasattr(resource, "title") and "title" not in md:
                md["title"] = str(resource.title)

        await self._make(
            StreamEventType.RESOURCE,
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
        entry = (name, source, dict(metadata or {}))
        token = self._stage_stack.set((*self._stage_stack.get(), entry))
        try:
            yield
            status = "completed"
        except BaseException:
            # Nested capability/tool failures can contain provider payloads,
            # credentials or user data. Detailed uncaught tracebacks belong
            # only in the JobRunner's protected error artifact.
            status = "failed"
            raise
        finally:
            self._stage_stack.reset(token)
            end_name, end_source, stage_md = entry
            end_md = dict(stage_md)
            end_md["status"] = locals().get("status", "completed")
            await self._make(
                StreamEventType.STAGE_END,
                content=end_name,
                source=end_source,
                stage=end_name,
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


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _serialise_resource(resource: Any) -> Any:
    """Best-effort serialisation of a ``Resource`` for stream metadata.

    **2026-07-08 fix (fdb26152):** a plain
    ``json.dumps(resource, default=str)`` rendered every Pydantic
    object as a ``repr()`` string (Pydantic v2 models aren't
    serialisable by stdlib ``json`` without help). The frontend then
    saw ``md["resource"]`` as a string and broke. We now:

    1. Use ``model_dump(mode="json")`` for Pydantic v2 BaseModel
       objects (the typical ``Resource`` case).
    2. Pass dicts through verbatim.
    3. Fall back to ``str(resource)`` if nothing else works.
    """
    # Pydantic v2: prefer ``model_dump(mode="json")``.
    if hasattr(resource, "model_dump") and callable(resource.model_dump):
        try:
            from tutor.services.resource_package.schema import (
                Resource,
                public_resource_dump,
            )

            if isinstance(resource, Resource):
                return public_resource_dump(resource)
            return resource.model_dump(mode="json")
        except Exception:  # noqa: BLE001
            pass
    # Already a dict (e.g. ``package.model_dump(...)`` result).
    if isinstance(resource, dict):
        return resource
    # Last resort: stringify, but mark it so the consumer knows.
    try:
        import json as _json
        return _json.loads(_json.dumps(resource, default=str))
    except Exception:  # noqa: BLE001
        return str(resource)
