"""Cross-module event bus for system-level events.

Unlike :class:`StreamBus` (which is per-turn), the :class:`EventBus`
is a singleton that handles cross-cutting concerns:

- Profile updates trigger downstream cache invalidation
- Resource generation completion may schedule background indexing
- Capability completion may update the learner's progress dashboard

Design inspired by DeepTutor's ``events.event_bus.EventBus``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

from loguru import logger


class EventType(str, Enum):
    """System-level event types."""

    # Profile lifecycle
    PROFILE_CREATED = "PROFILE_CREATED"
    PROFILE_UPDATED = "PROFILE_UPDATED"

    # Resource generation
    RESOURCE_PACKAGE_CREATED = "RESOURCE_PACKAGE_CREATED"
    RESOURCE_PACKAGE_FAILED = "RESOURCE_PACKAGE_FAILED"

    # Path planning
    PATH_PLAN_CREATED = "PATH_PLAN_CREATED"

    # Tutoring
    TUTOR_QUESTION_ANSWERED = "TUTOR_QUESTION_ANSWERED"

    # Assessment
    ASSESSMENT_COMPLETED = "ASSESSMENT_COMPLETED"

    # Knowledge base
    KB_INITIALIZED = "KB_INITIALIZED"
    KB_UPDATED = "KB_UPDATED"

    # System
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_ENDED = "SESSION_ENDED"
    SYSTEM_ERROR = "SYSTEM_ERROR"


EventHandler = Callable[["SystemEvent"], Awaitable[None]]


class SystemEvent:
    """A system-level event payload."""

    def __init__(
        self,
        event_type: EventType,
        data: dict[str, Any] | None = None,
        source: str = "",
    ) -> None:
        self.event_type = event_type
        self.data = data or {}
        self.source = source

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "data": self.data,
            "source": self.source,
        }

    def __repr__(self) -> str:
        return f"SystemEvent({self.event_type.value}, source={self.source!r})"


class EventBus:
    """Singleton async pub/sub bus for system events.

    Example
    -------
    >>> bus = EventBus()
    >>> async def on_update(event: SystemEvent) -> None:
    ...     print("Profile updated:", event.data)
    >>> bus.subscribe(EventType.PROFILE_UPDATED, on_update)
    >>> await bus.publish(SystemEvent(EventType.PROFILE_UPDATED, {"user_id": "u1"}))
    """

    _instance: "EventBus | None" = None

    def __new__(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._handlers = {}  # type: ignore[attr-defined]
            cls._instance._lock = asyncio.Lock()  # type: ignore[attr-defined]
        return cls._instance

    def __init__(self) -> None:
        # __new__ initialised the dict; nothing to do here.
        pass

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                pass

    async def publish(self, event: SystemEvent) -> None:
        handlers = list(self._handlers.get(event.event_type, []))
        if not handlers:
            return
        results = await asyncio.gather(
            *(self._safe_invoke(h, event) for h in handlers),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, BaseException):
                logger.error(f"Event handler raised: {r!r}")

    async def _safe_invoke(self, handler: EventHandler, event: SystemEvent) -> None:
        try:
            await handler(event)
        except Exception as exc:
            logger.exception(f"Handler {handler} failed: {exc}")

    def reset(self) -> None:
        """Clear all handlers. Intended for tests."""
        self._handlers.clear()


__all__ = ["EventBus", "EventType", "EventHandler", "SystemEvent"]
