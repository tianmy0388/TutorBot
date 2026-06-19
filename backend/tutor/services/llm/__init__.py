"""LLM provider abstraction.

A :class:`LLMProvider` exposes a uniform interface (``call`` / ``stream``)
regardless of upstream SDK (OpenAI, Anthropic, Ollama...). The factory
in :mod:`tutor.services.llm.provider_factory` returns the right
implementation based on configuration.
"""

from tutor.services.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMRole,
    LLMToolCall,
)
from tutor.services.llm.provider_factory import (
    get_runtime_provider,
    list_providers,
    register_provider,
)

__all__ = [
    "LLMMessage",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "LLMRole",
    "LLMToolCall",
    "get_runtime_provider",
    "list_providers",
    "register_provider",
]
