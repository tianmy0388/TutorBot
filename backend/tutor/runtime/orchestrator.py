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
import threading
from collections.abc import AsyncIterator, Callable
from functools import lru_cache
from typing import Any

from loguru import logger

from tutor.core.context import UnifiedContext
from tutor.core.stream import StreamEvent
from tutor.core.stream_bus import StreamBus
from tutor.runtime.registry.capability_registry import (
    CapabilityRegistry,
    get_capability_registry,
)


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

        Strategy:
        1. If ``context.capability`` is explicit, honour it.
        2. Else, simple keyword-based heuristic (placeholder until the
           router LLM is implemented).
        """
        if context.capability:
            return context.capability

        msg = (context.user_message or "").lower()
        # 简单关键词路由 — 后续会用 Router Agent 替换
        if any(kw in msg for kw in ["学习画像", "我的画像", "了解我", "learner profile", "who am i"]):
            return "profile"
        if any(
            kw in msg
            for kw in [
                "系统学习",
                "学习资源",
                "学习一下",
                "讲解",
                "解释",
                "学习路径",
                "生成资源",
                "learn",
                "study",
                "explain",
            ]
        ):
            return "resource_generation"
        if any(kw in msg for kw in ["计划", "路径", "下一步", "path", "plan", "next"]):
            return "path_planning"
        if any(kw in msg for kw in ["评估", "测试结果", "测验", "assessment", "evaluate"]):
            return "assessment"
        # 默认：资源生成（Tutor 的核心用例）
        return "resource_generation"

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
                except asyncio.TimeoutError:
                    logger.warning("Capability did not finish promptly after stream close")
                    run_task.cancel()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"Capability exited with: {exc!r}")


@lru_cache(maxsize=1)
def get_orchestrator() -> MainOrchestrator:
    """Return the singleton :class:`MainOrchestrator`."""
    return MainOrchestrator()


__all__ = ["MainOrchestrator", "get_orchestrator"]
