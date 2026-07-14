"""OpenAI-compatible provider.

This provider works with any service that speaks the OpenAI Chat
Completions API:

- OpenAI
- DeepSeek
- Azure OpenAI (with custom base_url + api_version)
- Ollama (with ``http://localhost:11434/v1``)
- vLLM / LM Studio / NVIDIA NIM / OpenRouter / etc.
- Any other "OpenAI-compatible" endpoint
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from tutor.services.llm.base import (
    LLMChunk,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMToolCall,
)


class OpenAICompatProvider(LLMProvider):
    """Provider for OpenAI and OpenAI-compatible APIs."""

    name = "openai_compat"

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
        super().__init__(
            model=model,
            api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
            base_url=base_url,
            default_temperature=default_temperature,
            default_max_tokens=default_max_tokens,
            timeout=timeout,
            **kwargs,
        )
        # OpenAI SDK supports a per-request timeout via ``timeout=`` to ``create``.
        client_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.api_key:
            client_kwargs["api_key"] = self.api_key
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        self._client = AsyncOpenAI(**client_kwargs)

    # ------------------------------------------------------------------
    # Call
    # ------------------------------------------------------------------

    async def call(self, request: LLMRequest) -> LLMResponse:
        req = self._finalise_request(request)
        params = self._build_params(req)
        try:
            resp: ChatCompletion = await self._client.chat.completions.create(**params)
        except Exception as exc:
            logger.error(f"OpenAICompatProvider.call failed: {exc!r}")
            raise
        return self._parse_response(resp)

    # ------------------------------------------------------------------
    # Stream
    # ------------------------------------------------------------------

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        req = self._finalise_request(request)
        params = self._build_params(req)
        params["stream"] = True
        params["stream_options"] = {"include_usage": True}

        try:
            response = await self._client.chat.completions.create(**params)
        except Exception as exc:
            logger.error(f"OpenAICompatProvider.stream failed: {exc!r}")
            raise

        async for raw in response:
            chunk = self._parse_chunk(raw)
            if chunk is not None:
                yield chunk

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_params(self, req: LLMRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": req.model,
            "messages": [m.to_dict() for m in req.messages],
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        if req.tools:
            params["tools"] = req.tools
        if req.tool_choice:
            params["tool_choice"] = req.tool_choice
        if req.stop:
            params["stop"] = req.stop
        # 2026-06-21 fix: ``req.extra`` carries provider-specific
        # options that ``BaseAgent.call_llm`` builds —
        # ``response_format={"type": "json_object"}`` for
        # JSON-mode agents (ManimVideoAgent, CodeSandboxAgent,
        # ExerciseGenerator, etc.) and other future settings.
        # Before this fix, ``req.extra`` was silently ignored,
        # so every agent that asked for JSON mode got plaintext
        # instead, causing ``parse_json_response`` to return
        # ``{}`` and triggering the fallback placeholder code.
        if req.extra:
            params.update(req.extra)
        return params

    def _parse_response(self, resp: ChatCompletion) -> LLMResponse:
        choice = resp.choices[0] if resp.choices else None
        content = ""
        tool_calls: list[LLMToolCall] = []
        finish_reason = ""
        if choice is not None:
            content = choice.message.content or ""
            finish_reason = choice.finish_reason or ""
            for tc in (choice.message.tool_calls or []):
                try:
                    import json

                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                tool_calls.append(
                    LLMToolCall(id=tc.id, name=tc.function.name, arguments=args)
                )
        usage: dict[str, int] = {}
        if getattr(resp, "usage", None):
            usage = {
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(resp.usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(resp.usage, "total_tokens", 0) or 0,
            }
        return LLMResponse(
            content=content,
            model=resp.model,
            finish_reason=finish_reason,
            tool_calls=tool_calls,
            usage=usage,
            raw=resp,
        )

    def _parse_chunk(self, chunk: ChatCompletionChunk) -> LLMChunk | None:
        if not chunk.choices and not getattr(chunk, "usage", None):
            return None
        delta = ""
        tool_calls: list[LLMToolCall] = []
        finish_reason = ""
        if chunk.choices:
            ch = chunk.choices[0]
            delta = (ch.delta.content or "") if ch.delta else ""
            finish_reason = ch.finish_reason or ""
            if ch.delta and ch.delta.tool_calls:
                for tc in ch.delta.tool_calls:
                    try:
                        import json

                        args_raw = tc.function.arguments if tc.function else ""
                        args = json.loads(args_raw) if args_raw else {}
                    except Exception:
                        args = {}
                    tool_calls.append(
                        LLMToolCall(
                            id=tc.id or "",
                            name=(tc.function.name if tc.function else "") or "",
                            arguments=args,
                        )
                    )
        usage: dict[str, int] | None = None
        if getattr(chunk, "usage", None):
            usage = {
                "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(chunk.usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(chunk.usage, "total_tokens", 0) or 0,
            }
        return LLMChunk(
            delta=delta,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )


__all__ = ["OpenAICompatProvider"]
