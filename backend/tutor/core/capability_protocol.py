"""BaseCapability — top-level orchestration unit.

A Capability is what the MainOrchestrator dispatches to. Each Capability
encapsulates a multi-step workflow that may invoke multiple Agents and
Tools in sequence or in parallel.

The Capability protocol is intentionally minimal:

- :class:`CapabilityManifest` — static metadata describing the capability
- :meth:`BaseCapability.run` — the async execution entry point

Design inspired by DeepTutor's ``BaseCapability``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus


@dataclass
class CapabilityManifest:
    """Static metadata describing a Capability.

    The orchestrator uses this for routing (deciding which capability to
    invoke for a given user message) and for UI surfacing.
    """

    name: str
    description: str
    stages: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    cli_aliases: list[str] = field(default_factory=list)
    request_schema: dict[str, Any] = field(default_factory=dict)
    config_defaults: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "stages": list(self.stages),
            "tools_used": list(self.tools_used),
            "cli_aliases": list(self.cli_aliases),
            "request_schema": dict(self.request_schema),
            "config_defaults": dict(self.config_defaults),
            "tags": list(self.tags),
        }


class BaseCapability(ABC):
    """Abstract base for all Capabilities.

    Subclasses must:
    1. Define a class-level :attr:`manifest` (a :class:`CapabilityManifest`)
    2. Implement :meth:`run`

    The Capability should emit all progress through the provided
    ``stream`` so the frontend can render real-time updates.
    """

    manifest: CapabilityManifest  # subclasses must set

    def __init__(self) -> None:
        if not hasattr(self, "manifest") or self.manifest is None:
            raise TypeError(f"{type(self).__name__} must set class attribute 'manifest'")

    @abstractmethod
    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        """Execute the capability.

        Implementations should ``await stream.done()`` on success and may
        emit ``stream.error(...)`` on failure. They must NOT close the
        stream — the orchestrator owns that lifecycle.
        """
        raise NotImplementedError


__all__ = ["BaseCapability", "CapabilityManifest"]
