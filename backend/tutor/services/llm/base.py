"""Abstract base + data classes for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

LLMRole = Literal["system", "user", "assistant", "tool"]


@dataclass
class LLMMessage:
    """A single message in a chat conversation."""

    role: LLMRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list["LLMToolCall"] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            out["name"] = self.name
        if self.tool_call_id:
            out["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            out["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        return out


@dataclass
class LLMToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class LLMRequest:
    """A request to an LLM provider."""

    messages: list[LLMMessage]
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: str | dict[str, Any] | None = None
    stop: list[str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": [m.to_dict() for m in self.messages],
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "tools": list(self.tools),
            "tool_choice": self.tool_choice,
            "stop": list(self.stop) if self.stop else None,
            "extra": dict(self.extra),
        }


@dataclass
class LLMResponse:
    """A response from a non-streaming LLM call."""

    content: str
    model: str = ""
    finish_reason: str = ""
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    raw: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "model": self.model,
            "finish_reason": self.finish_reason,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "usage": self.usage,
        }


@dataclass
class LLMChunk:
    """A single chunk from a streaming LLM response."""

    delta: str = ""
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, int] | None = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    Subclasses implement the actual SDK calls (OpenAI, Anthropic, etc.).
    The factory in :mod:`tutor.services.llm.provider_factory` returns
    the right implementation given a :class:`tutor.services.config.settings.Settings`.
    """

    name: str = "abstract"

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        base_url: str = "",
        default_temperature: float = 0.7,
        default_max_tokens: int = 4096,
        timeout: int = 60,
        **kwargs: Any,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens
        self.timeout = timeout
        self._extra = kwargs

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    @abstractmethod
    async def call(self, request: LLMRequest) -> LLMResponse:
        """Issue a non-streaming chat completion request."""
        raise NotImplementedError

    @abstractmethod
    def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        """Yield streaming chunks from a chat completion request."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _finalise_request(self, request: LLMRequest) -> LLMRequest:
        """Apply defaults if caller didn't set them."""
        if not request.model:
            request.model = self.model
        if request.temperature is None or request.temperature == 0:
            request.temperature = self.default_temperature
        if not request.max_tokens:
            request.max_tokens = self.default_max_tokens
        return request


__all__ = [
    "LLMChunk",
    "LLMMessage",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMRole",
    "LLMToolCall",
]
