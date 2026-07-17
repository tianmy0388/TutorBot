"""MainOrchestrator — the unified entry point for all Tutor capabilities.

The orchestrator:

1. Receives a :class:`UnifiedContext` (typically from a WebSocket handler).
2. Routes to the appropriate :class:`BaseCapability` based on intent.
3. Subscribes to the :class:`StreamBus` and forwards events to a consumer
   (the WebSocket sender task).
4. Tracks session/turn state and exposes admin endpoints.

Design inspired by DeepTutor's :class:`ChatOrchestrator`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from loguru import logger

from tutor.core.context import UnifiedContext
from tutor.core.stream import StreamEvent
from tutor.runtime.registry.capability_registry import (
    CapabilityRegistry,
    get_capability_registry,
)
from tutor.services.intent.router import classify


class MainOrchestrator:
    """Singleton orchestrator that routes requests to capabilities."""

    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistry | None = None,
    ) -> None:
        self.capabilities = capability_registry or get_capability_registry()

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_capabilities(self) -> list[str]:
        return self.capabilities.list_capabilities()

    def get_capability_manifests(self) -> list[dict[str, Any]]:
        return self.capabilities.get_manifests()

    def get_tool_schemas(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        # Lazy import to avoid circular dependency
        from tutor.runtime.registry.tool_registry import get_tool_registry

        return get_tool_registry().build_openai_schemas(names)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def route(self, context: UnifiedContext) -> str:
        """Pick a capability for the given context (cheap, no LLM call).

        Explicit hints stay explicit. Otherwise the shared deterministic
        intent router is the only capability selector.
        """
        return classify(
            context.user_message or "",
            explicit_capability=context.capability,
        ).capability

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def handle(self, context: UnifiedContext) -> AsyncIterator[StreamEvent]:
        """Run a capability and yield its events.

        The yielded events are the full trace of one turn (including
        ``DONE``). The caller (e.g. a WebSocket handler) consumes these
        and forwards them to the client.
        """
        capability_name = self.route(context)
        context.capability = capability_name

        cap = self.capabilities.get(capability_name)
        if cap is None:
            logger.error(f"No capability registered for {capability_name!r}")
            bus = context.stream_bus
            await bus.error(
                f"Capability not found: {capability_name}",
                source="orchestrator",
            )
            await bus.done(source="orchestrator")
            async for evt in bus.subscribe_iter():
                yield evt
            return

        bus = context.stream_bus
        await bus.observation(
            f"Routing to capability: {capability_name}",
            source="orchestrator",
            metadata={"capability": capability_name},
        )

        run_task = asyncio.create_task(cap.run(context, bus))

        try:
            async for evt in bus.subscribe_iter():
                yield evt
        finally:
            # Ensure the capability task is awaited/cleaned up.
            if not run_task.done():
                try:
                    await asyncio.wait_for(run_task, timeout=2.0)
                except TimeoutError:
                    logger.warning("Capability did not finish promptly after stream close")
                    run_task.cancel()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"Capability exited with: {exc!r}")


@lru_cache(maxsize=1)
def get_orchestrator() -> MainOrchestrator:
    """Return the singleton :class:`MainOrchestrator`."""
    return MainOrchestrator()


__all__ = ["MainOrchestrator", "get_orchestrator"]
