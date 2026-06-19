"""BaseAgent — abstract base class for all Agents in Tutor.

Responsibilities:
- Hold a reference to the LLM provider (constructed from settings)
- Load prompts via :class:`PromptManager` with multi-language fallback
- Expose ``call_llm`` and ``stream_llm`` with consistent semantics
- Track token usage and emit trace callbacks
- Provide a :meth:`process` hook for subclasses

Subclasses typically follow this pattern::

    class ContentExpertAgent(BaseAgent):
        module_name = "resource"
        agent_name = "content_expert"

        async def process(self, context, stream):
            prompt_data = self.get_prompt_data(context.language)
            system = self.get_system_prompt(prompt_data)
            user_msg = self.build_user_message(context)
            response = await self.call_llm(
                messages=[system, user_msg],
                stream=stream,
                source="content_expert",
                stage="content_generation",
            )
            return response.content

Design inspired by DeepTutor's :class:`BaseAgent`.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from tutor.core.context import UnifiedContext
from tutor.core.stream import StreamEvent, StreamEventType
from tutor.core.stream_bus import StreamBus
from tutor.services.llm.base import (
    LLMChunk,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
)
from tutor.services.llm.provider_factory import get_runtime_provider
from tutor.services.prompt.manager import PromptManager, get_prompt_manager


TraceCallback = Callable[[dict[str, Any]], None]


@dataclass
class TokenUsage:
    """Running token usage counters for one agent."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0

    def add(self, usage: dict[str, int]) -> None:
        if not usage:
            return
        self.prompt_tokens += usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
        self.completion_tokens += usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
        self.total_tokens += usage.get("total_tokens", 0) or (self.prompt_tokens + self.completion_tokens)
        self.calls += 1


class BaseAgent(ABC):
    """Abstract base for all Tutor agents."""

    # Subclasses override these
    module_name: str = ""  # e.g. "profile"
    agent_name: str = ""   # e.g. "feature_extractor"
    default_temperature: float = 0.7
    default_max_tokens: int = 4096

    def __init__(
        self,
        *,
        llm: LLMProvider | None = None,
        prompt_manager: PromptManager | None = None,
        trace_callback: TraceCallback | None = None,
    ) -> None:
        if not self.agent_name:
            raise TypeError(f"{type(self).__name__} must set class attribute 'agent_name'")
        self.llm = llm  # may be None → built lazily on first call
        self.prompts = prompt_manager or get_prompt_manager()
        self.trace_callback = trace_callback
        self.usage = TokenUsage()

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    def _build_default_llm(self) -> LLMProvider:
        return get_runtime_provider()

    @property
    def resolved_llm(self) -> LLMProvider:
        """Return the LLM, building it lazily on first access."""
        if self.llm is None:
            self.llm = self._build_default_llm()
        return self.llm

    # ------------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------------

    def get_prompt_data(self, language: str = "zh") -> dict[str, Any]:
        """Load this agent's prompt YAML (with language fallback)."""
        return self.prompts.load_prompts(
            self.module_name, self.agent_name, language=language
        )

    def get_system_prompt(
        self,
        prompt_data: dict[str, Any] | None = None,
        *,
        section: str = "system",
        field: str = "content",
        fallback: str = "",
    ) -> str:
        """Read a system prompt field.

        Defaults to ``system.content`` but supports custom section/field
        so agents with multi-stage prompts (e.g. Manim designer/coder) can
        pick different sections.
        """
        prompt_data = prompt_data or self.get_prompt_data()
        return self.prompts.get_prompt(prompt_data, section, field, fallback)

    def get_user_prompt(
        self,
        prompt_data: dict[str, Any] | None = None,
        *,
        section: str = "user",
        field: str = "default",
        fallback: str = "",
    ) -> str:
        """Read a user-prompt template (e.g. ``user.default``)."""
        prompt_data = prompt_data or self.get_prompt_data()
        return self.prompts.get_prompt(prompt_data, section, field, fallback)

    # ------------------------------------------------------------------
    # LLM call helpers
    # ------------------------------------------------------------------

    def build_messages(
        self,
        *,
        system: str | None = None,
        user: str | None = None,
        messages: list[LLMMessage] | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> list[LLMMessage]:
        """Construct a chat message list with consistent ordering."""
        out: list[LLMMessage] = []
        if system:
            out.append(LLMMessage(role="system", content=system))
        if messages:
            out.extend(messages)
        if history:
            for turn in history:
                role = turn.get("role", "user")
                content = turn.get("content", "")
                if role in {"system", "user", "assistant", "tool"}:
                    out.append(LLMMessage(role=role, content=content))
        if user:
            out.append(LLMMessage(role="user", content=user))
        return out

    async def call_llm(
        self,
        *,
        messages: list[LLMMessage],
        stream: StreamBus | None = None,
        source: str | None = None,
        stage: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Non-streaming LLM call with optional stream-bus narration."""
        req = LLMRequest(
            messages=messages,
            temperature=temperature if temperature is not None else self.default_temperature,
            max_tokens=max_tokens or self.default_max_tokens,
            extra={"response_format": response_format} if response_format else {},
        )

        t0 = time.time()
        self._trace(
            {
                "event": "llm_call_start",
                "agent": self.agent_name,
                "model": req.model or self.resolved_llm.model,
                "messages_count": len(messages),
            }
        )

        if stream is not None:
            await stream.thinking(
                f"Calling {req.model or self.resolved_llm.model}...",
                source=source or self.agent_name,
                stage=stage,
            )

        try:
            resp = await self.resolved_llm.call(req)
        except Exception as exc:
            logger.exception(f"LLM call failed for {self.agent_name}: {exc!r}")
            if stream is not None:
                await stream.error(
                    f"LLM call failed: {exc}", source=source or self.agent_name, stage=stage
                )
            raise

        elapsed_ms = int((time.time() - t0) * 1000)
        self.usage.add(resp.usage)

        if stream is not None and resp.content:
            await stream.content(
                resp.content,
                source=source or self.agent_name,
                stage=stage,
                metadata={"model": resp.model, "elapsed_ms": elapsed_ms},
            )

        self._trace(
            {
                "event": "llm_call_end",
                "agent": self.agent_name,
                "model": resp.model,
                "usage": resp.usage,
                "elapsed_ms": elapsed_ms,
                "finish_reason": resp.finish_reason,
            }
        )

        return resp

    async def stream_llm(
        self,
        *,
        messages: list[LLMMessage],
        stream: StreamBus,
        source: str | None = None,
        stage: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        chunk_size: int | None = None,
    ) -> LLMResponse:
        """Streaming LLM call. Returns the final aggregated :class:`LLMResponse`.

        Chunks are emitted to the ``stream`` bus as ``CONTENT`` events; small
        chunks are coalesced for smoother rendering on the frontend.
        """
        from tutor.services.config.settings import get_settings

        settings = get_settings()
        chunk_size = chunk_size or settings.stream_chunk_size

        req = LLMRequest(
            messages=messages,
            temperature=temperature if temperature is not None else self.default_temperature,
            max_tokens=max_tokens or self.default_max_tokens,
        )

        t0 = time.time()
        self._trace(
            {
                "event": "llm_stream_start",
                "agent": self.agent_name,
                "model": req.model or self.resolved_llm.model,
            }
        )

        buffer: list[str] = []
        buf_len = 0
        final_usage: dict[str, int] = {}
        model_name = req.model or self.resolved_llm.model

        try:
            async for chunk in self.resolved_llm.stream(req):
                if chunk.delta:
                    buffer.append(chunk.delta)
                    buf_len += len(chunk.delta)
                    if buf_len >= chunk_size:
                        await stream.content(
                            "".join(buffer),
                            source=source or self.agent_name,
                            stage=stage,
                        )
                        buffer.clear()
                        buf_len = 0
                if chunk.usage:
                    final_usage = chunk.usage
        except Exception as exc:
            logger.exception(f"LLM stream failed for {self.agent_name}: {exc!r}")
            await stream.error(
                f"LLM stream failed: {exc}", source=source or self.agent_name, stage=stage
            )
            raise

        # Flush remaining buffer
        if buffer:
            await stream.content(
                "".join(buffer),
                source=source or self.agent_name,
                stage=stage,
            )

        full_content = "".join(buffer)
        elapsed_ms = int((time.time() - t0) * 1000)
        self.usage.add(final_usage)

        resp = LLMResponse(
            content=full_content,
            model=model_name,
            finish_reason="stop",
            usage=final_usage,
        )

        await stream.content_final(
            full_content,
            source=source or self.agent_name,
            stage=stage,
            metadata={"elapsed_ms": elapsed_ms, "usage": final_usage},
        )

        self._trace(
            {
                "event": "llm_stream_end",
                "agent": self.agent_name,
                "model": model_name,
                "usage": final_usage,
                "elapsed_ms": elapsed_ms,
                "content_length": len(full_content),
            }
        )

        return resp

    # ------------------------------------------------------------------
    # JSON parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_json_response(
        content: str,
        *,
        fallback: Any = None,
        strict: bool = False,
    ) -> Any:
        """Try to parse an LLM response as JSON.

        Handles common cases:
        - Pure JSON
        - JSON inside ```json ... ``` code blocks
        - JSON embedded in prose (best-effort first/last brace match)
        """
        if not content:
            return fallback

        text = content.strip()

        # Strip leading/trailing ```json fences
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
            if text.endswith("```"):
                text = text[:-3].strip()

        # Direct parse
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            pass

        # Try to extract the largest {...} or [...] substring
        for opener, closer in [("{", "}"), ("[", "]")]:
            start = text.find(opener)
            end = text.rfind(closer)
            if start != -1 and end != -1 and end > start:
                candidate = text[start : end + 1]
                try:
                    return json.loads(candidate)
                except (ValueError, TypeError):
                    continue

        if strict:
            raise ValueError(f"Could not parse JSON from LLM response: {content[:200]!r}")
        return fallback

    # ------------------------------------------------------------------
    # Trace
    # ------------------------------------------------------------------

    def _trace(self, payload: dict[str, Any]) -> None:
        if self.trace_callback is not None:
            try:
                self.trace_callback(payload)
            except Exception as exc:
                logger.warning(f"trace_callback raised: {exc!r}")

    # ------------------------------------------------------------------
    # Subclass hook
    # ------------------------------------------------------------------

    @abstractmethod
    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
    ) -> Any:
        """Implement the agent's domain logic.

        Subclasses should return a structured result (or ``None``) and emit
        any user-facing content via ``stream``.
        """
        raise NotImplementedError


__all__ = ["BaseAgent", "TokenUsage", "TraceCallback"]
