"""Runtime layer: orchestrator + registries.

- :mod:`tutor.runtime.orchestrator`           — :class:`MainOrchestrator`
- :mod:`tutor.runtime.registry.capability_registry` — :class:`CapabilityRegistry`
- :mod:`tutor.runtime.registry.tool_registry`       — :class:`ToolRegistry`
"""

from tutor.runtime.orchestrator import MainOrchestrator, get_orchestrator
from tutor.runtime.registry.capability_registry import (
    BUILTIN_CAPABILITY_CLASSES,
    CapabilityRegistry,
    get_capability_registry,
)
from tutor.runtime.registry.tool_registry import (
    BUILTIN_TOOL_CLASSES,
    ToolRegistry,
    get_tool_registry,
)

__all__ = [
    "BUILTIN_CAPABILITY_CLASSES",
    "BUILTIN_TOOL_CLASSES",
    "CapabilityRegistry",
    "MainOrchestrator",
    "ToolRegistry",
    "get_capability_registry",
    "get_orchestrator",
    "get_tool_registry",
]
