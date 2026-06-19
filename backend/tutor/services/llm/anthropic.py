"""Anthropic Claude provider."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger

try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]

from tutor.services.llm.base import (
    LLMChunk,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMToolCall,
)


class AnthropicProvider(LLMProvider):
    """Provider for Anthropic Claude models."""

    name = "anthropic"

    def __init__(
        self,
        *,
        model: str = "claude-3-5-sonnet-20241022",
        api_key: str = "",
        base_url: str = "",
        default_temperature: float = 0.7,
        default_max_tokens: int = 4096,
        timeout: int = 60,
        **kwargs: Any,
    ) -> None:
        if anthropic is None:  # pragma: no cover
            raise ImportError(
                "anthropic package not installed. `pip install anthropic>=0.30.0`"
            )
        super().__init__(
            model=model,
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            base_url=base_url,
            default_temperature=default_temperature,
            default_max_tokens=default_max_tokens,
            timeout=timeout,
            **kwargs,
        )
        client_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.api_key:
            client_kwargs["api_key"] = self.api_key
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        self._client = anthropic.AsyncAnthropic(**client_kwargs)

    # ------------------------------------------------------------------
    # Call
    # ------------------------------------------------------------------

    async def call(self, request: LLMRequest) -> LLMResponse:
        req = self._finalise_request(request)
        system_prompt, messages = self._split_system(req.messages)
        params: dict[str, Any] = {
            "model": req.model,
            "messages": messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        if system_prompt:
            params["system"] = system_prompt
        if req.stop:
            params["stop_sequences"] = req.stop

        try:
            resp = await self._client.messages.create(**params)
        except Exception as exc:
            logger.error(f"AnthropicProvider.call failed: {exc!r}")
            raise

        content_text = ""
        tool_calls: list[LLMToolCall] = []
        for block in resp.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    LLMToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input or {},
                    )
                )
        usage = {
            "input_tokens": getattr(resp.usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(resp.usage, "output_tokens", 0) or 0,
            "total_tokens": (getattr(resp.usage, "input_tokens", 0) or 0)
            + (getattr(resp.usage, "output_tokens", 0) or 0),
        }
        return LLMResponse(
            content=content_text,
            model=resp.model,
            finish_reason=resp.stop_reason or "",
            tool_calls=tool_calls,
            usage=usage,
            raw=resp,
        )

    # ------------------------------------------------------------------
    # Stream
    # ------------------------------------------------------------------

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        req = self._finalise_request(request)
        system_prompt, messages = self._split_system(req.messages)
        params: dict[str, Any] = {
            "model": req.model,
            "messages": messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        if system_prompt:
            params["system"] = system_prompt

        try:
            async with self._client.messages.stream(**params) as stream:
                async for text in stream.text_stream:
                    yield LLMChunk(delta=text)
        except Exception as exc:
            logger.error(f"AnthropicProvider.stream failed: {exc!r}")
            raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_system(messages: list[LLMMessage]) -> tuple[str, list[dict[str, Any]]]:
        """Anthropic puts the system message in a top-level field."""
        system = ""
        rest: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system += m.content + "\n"
                continue
            rest.append({"role": m.role, "content": m.content})
        return system.strip(), rest


__all__ = ["AnthropicProvider"]
