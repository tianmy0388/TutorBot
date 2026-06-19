"""BaseTool — atomic capability used by Agents.

Tools are stateless / near-stateless functions (RAG retrieval, web search,
code execution, etc.) that Agents invoke. They follow an OpenAI-style
function-calling schema so any LLM provider can route to them.

Design inspired by DeepTutor's ``BaseTool`` + OpenAI function-calling.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolParameter:
    """JSON-schema-style parameter descriptor."""

    name: str
    type: str  # JSON-schema type: "string", "number", "object", ...
    description: str = ""
    required: bool = False
    enum: list[Any] | None = None
    items: dict[str, Any] | None = None  # for array types
    properties: dict[str, Any] | None = None  # for object types

    def to_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum is not None:
            schema["enum"] = self.enum
        if self.items is not None:
            schema["items"] = self.items
        if self.properties is not None:
            schema["properties"] = self.properties
        return schema


@dataclass
class ToolDefinition:
    """OpenAI function-calling schema for a Tool."""

    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)

    def to_openai_schema(self) -> dict[str, Any]:
        props: dict[str, Any] = {}
        required: list[str] = []
        for p in self.parameters:
            props[p.name] = p.to_schema()
            if p.required:
                required.append(p.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        }


class ToolResult:
    """Outcome of a Tool invocation.

    A simple value-object so Tools don't have to depend on Pydantic.
    """

    def __init__(
        self,
        success: bool,
        data: Any = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.success = success
        self.data = data
        self.error = error
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
        }

    def __repr__(self) -> str:
        if self.success:
            return f"ToolResult(ok, data={type(self.data).__name__})"
        return f"ToolResult(error={self.error!r})"


class BaseTool(ABC):
    """Abstract base for all Tools.

    Subclasses must implement :meth:`get_definition` and :meth:`execute`.
    The tool should be registered with the ToolRegistry before agents
    can invoke it.
    """

    name: str  # subclasses must set
    description: str  # subclasses must set

    def __init__(self) -> None:
        if not getattr(self, "name", None):
            raise TypeError(f"{type(self).__name__} must set class attribute 'name'")

    @abstractmethod
    def get_definition(self) -> ToolDefinition:
        """Return the tool's OpenAI function-calling schema."""
        raise NotImplementedError

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Invoke the tool with the given arguments."""
        raise NotImplementedError


__all__ = ["BaseTool", "ToolDefinition", "ToolParameter", "ToolResult"]
