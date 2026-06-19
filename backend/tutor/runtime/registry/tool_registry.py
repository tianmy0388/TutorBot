"""ToolRegistry — registry of :class:`BaseTool` instances.

Design inspired by DeepTutor's :class:`ToolRegistry`.
"""

from __future__ import annotations

import importlib
import threading
from functools import lru_cache
from typing import Any

from loguru import logger

from tutor.core.tool_protocol import BaseTool


BUILTIN_TOOL_CLASSES: dict[str, str] = {
    "rag": "tutor.tools.rag_tool:RAGTool",
    "web_search": "tutor.tools.web_search_tool:WebSearchTool",
    "code_execution": "tutor.tools.code_execution_tool:CodeExecutionTool",
    "paper_search": "tutor.tools.paper_search_tool:PaperSearchTool",
}


class ToolRegistry:
    """Thread-safe registry of tool instances."""

    def __init__(self) -> None:
        self._registry: dict[str, BaseTool] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: BaseTool) -> None:
        name = tool.name
        with self._lock:
            if name in self._registry:
                logger.warning(f"Overwriting tool {name!r}")
            self._registry[name] = tool
            logger.debug(f"Registered tool: {name}")

    def unregister(self, name: str) -> None:
        with self._lock:
            self._registry.pop(name, None)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def get(self, name: str) -> BaseTool | None:
        return self._registry.get(name)

    def list_tools(self) -> list[str]:
        return sorted(self._registry.keys())

    def get_enabled(self, names: list[str] | None = None) -> list[BaseTool]:
        if not names:
            return list(self._registry.values())
        return [self._registry[n] for n in names if n in self._registry]

    def build_openai_schemas(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        return [t.get_definition().to_openai_schema() for t in self.get_enabled(names)]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(self, name: str, **kwargs: Any):
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"Tool {name!r} not found in registry")
        return await tool.execute(**kwargs)

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def load_builtins(self) -> None:
        """Instantiate and register every builtin tool.

        Missing classes are logged and skipped — this lets the system run
        even if a specific tool's external dependency is not installed.
        """
        for name, path in BUILTIN_TOOL_CLASSES.items():
            if name in self._registry:
                continue
            try:
                module_name, _, class_name = path.partition(":")
                module = importlib.import_module(module_name)
                cls = getattr(module, class_name)
                self.register(cls())
            except ModuleNotFoundError as exc:
                logger.warning(f"Tool {name!r}: module not found ({exc})")
            except (AttributeError, TypeError) as exc:
                logger.warning(f"Tool {name!r}: failed to load ({exc})")
            except Exception as exc:
                logger.error(f"Tool {name!r}: unexpected error ({exc!r})")

    def reset(self) -> None:
        with self._lock:
            self._registry.clear()


@lru_cache(maxsize=1)
def get_tool_registry() -> ToolRegistry:
    """Return the singleton :class:`ToolRegistry`."""
    reg = ToolRegistry()
    reg.load_builtins()
    return reg


__all__ = [
    "BUILTIN_TOOL_CLASSES",
    "ToolRegistry",
    "get_tool_registry",
]
