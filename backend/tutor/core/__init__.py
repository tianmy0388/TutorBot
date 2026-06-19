"""Core abstractions for the Tutor multi-agent system.

This package contains the foundational protocols and infrastructure that
other layers build on:

- :mod:`tutor.core.context`        — Request context (UnifiedContext)
- :mod:`tutor.core.stream`         — StreamEvent and event type enum
- :mod:`tutor.core.stream_bus`     — Async fan-out event bus for streaming
- :mod:`tutor.core.capability_protocol` — BaseCapability abstraction
- :mod:`tutor.core.tool_protocol`       — BaseTool abstraction
- :mod:`tutor.core.event_bus`      — Cross-module pub/sub event bus

The design is inspired by DeepTutor's core layer.
"""

from tutor.core.capability_protocol import (
    BaseCapability,
    CapabilityManifest,
)
from tutor.core.context import UnifiedContext
from tutor.core.event_bus import EventBus, EventType
from tutor.core.stream import StreamEvent, StreamEventType
from tutor.core.stream_bus import StreamBus
from tutor.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter

__all__ = [
    "BaseCapability",
    "BaseTool",
    "CapabilityManifest",
    "EventBus",
    "EventType",
    "StreamBus",
    "StreamEvent",
    "StreamEventType",
    "ToolDefinition",
    "ToolParameter",
    "UnifiedContext",
]
