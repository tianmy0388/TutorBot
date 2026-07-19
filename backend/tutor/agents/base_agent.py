"""BaseAgent — abstract base class for all Agents in TutorBot.

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

import asyncio
import json
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loguru import logger

from tutor.core.context import UnifiedContext
from tutor.core.redaction import failure_category, public_failure
from tutor.core.stream_bus import StreamBus
from tutor.services.llm.base import (
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
            category = failure_category(exc)
            logger.error(
                "AGENT_LLM_CALL_FAILED agent={} category={}",
                self.agent_name,
                category,
            )
            if stream is not None:
                await stream.error(
                    "LLM request failed",
                    source=source or self.agent_name,
                    stage=stage,
                    metadata=public_failure(
                        "AGENT_LLM_CALL_FAILED",
                        "LLM request failed",
                        retryable=category in {"timeout", "connection"},
                    ),
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

    async def call_llm_with_retry(
        self,
        *,
        messages: list[LLMMessage],
        stream: StreamBus | None = None,
        source: str | None = None,
        stage: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_attempts: int = 3,
        response_format: dict[str, Any] | None = None,
    ) -> tuple[LLMResponse, Any, int]:
        """L2 retry wrapper — handles ``finish_reason="length"`` AND
        transient network errors.

        Truncation handling:
            On the first attempt we use ``max_tokens`` (or
            ``self.default_max_tokens`` if None). Each subsequent attempt
            doubles the budget. Between attempts we append a user-message
            that quotes the tail of the previous truncated output so the
            LLM can resume from a known-good anchor instead of regenerating
            from scratch.

        Transient network handling (**2026-07-08 fix**):
            Wraps ``self.call_llm(...)`` in a try/except that retries
            ``openai.APITimeoutError``, ``openai.APIConnectionError``,
            ``httpx.ReadTimeout``, ``httpx.ConnectError``, and any
            ``OSError``/``asyncio.TimeoutError`` subclass. 2db13ad8
            trace showed a single ``APITimeoutError`` (DeepSeek took
            >60s on a read) escaping the retry wrapper and turning the
            video into an immediate ``None`` resource. DeepSeek's API
            is fine with re-issuing the same prompt; we'd rather retry
            2 more times than drop the resource entirely.

            Truncation and network errors share ``max_attempts`` — we
            don't want a flaky network to push the budget to 6 total
            calls.

        Returns ``(resp, parsed_data, attempts_used)``:

        * ``resp`` is the *last* ``LLMResponse`` we got on success, or
          ``None`` if every attempt raised.
        * ``parsed_data`` is ``parse_json_response(content)`` with the
          agent's standard fallback (``{}``) on parse failure.
        * ``attempts_used`` is 1-based.

        **Scope:** this wrapper only retries on **truncation** +
        **transient network errors**. Parse failures
        (``finish_reason="stop"`` but invalid JSON) fall through
        immediately — L1 parse-failure retry is a separate layer per
        the design doc.
        """
        if max_tokens is None:
            max_tokens = self.default_max_tokens

        # Lazy import: keeps the dependency surface narrow for callers
        # that don't touch the retry path. We try several names because
        # ``openai`` exception class names have shifted across
        # versions (``openai.error.Timeout`` → ``openai.APITimeoutError``
        # → ``openai.exceptions.APITimeoutError``), and some
        # installations don't expose them at all (e.g. proxied / patched
        # openai-compat clients that re-raise httpx errors directly).
        api_timeout_error = None  # type: ignore
        api_connection_error = None  # type: ignore
        for _mod_path, _names in (
            ("openai", ("APITimeoutError", "APIConnectionError")),
            ("openai.exceptions", ("APITimeoutError", "APIConnectionError")),
            ("openai.error", ("Timeout", "APIConnectionError")),
        ):
            try:
                _mod = __import__(_mod_path, fromlist=_names)
                for _n in _names:
                    if api_timeout_error is None and hasattr(_mod, "APITimeoutError"):
                        api_timeout_error = _mod.APITimeoutError
                    if api_connection_error is None and hasattr(_mod, "APIConnectionError"):
                        api_connection_error = _mod.APIConnectionError
                    if api_timeout_error is None and _n == "Timeout" and hasattr(_mod, "Timeout"):
                        api_timeout_error = _mod.Timeout
            except ImportError:
                continue

        # Also try httpx directly — some openai-compat clients re-raise
        # the underlying httpx error without wrapping it.
        try:
            import httpx as _httpx
            read_timeout = _httpx.ReadTimeout
            connect_error = _httpx.ConnectError
            connect_timeout = _httpx.ConnectTimeout
        except ImportError:  # pragma: no cover
            read_timeout = connect_error = connect_timeout = None  # type: ignore

        transient_errors: tuple[type[BaseException], ...] = (
            asyncio.TimeoutError,
            ConnectionError,
            TimeoutError,
            OSError,  # socket.timeout, ConnectionResetError — older openai
        )
        if api_timeout_error is not None:
            transient_errors = transient_errors + (api_timeout_error,)
        if api_connection_error is not None:
            transient_errors = transient_errors + (api_connection_error,)
        if read_timeout is not None:
            transient_errors = transient_errors + (read_timeout,)
        if connect_error is not None:
            transient_errors = transient_errors + (connect_error,)
        if connect_timeout is not None:
            transient_errors = transient_errors + (connect_timeout,)

        # Local mutable copy so we can append feedback between attempts
        # without mutating the caller's list.
        msgs: list[LLMMessage] = list(messages)

        last_resp: LLMResponse | None = None
        for attempt in range(max_attempts):
            mt = max_tokens * (2 ** attempt)
            try:
                resp = await self.call_llm(
                    messages=msgs,
                    stream=stream,
                    source=source,
                    stage=stage,
                    temperature=temperature,
                    max_tokens=mt,
                    response_format=response_format,
                )
            except transient_errors as exc:
                category = failure_category(exc)
                if attempt < max_attempts - 1:
                    # Small backoff so we don't hammer the provider if
                    # the issue is rate-limit-driven. 1s, 2s for the
                    # next attempts — exponential.
                    backoff = 2 ** attempt
                    logger.warning(
                        "AGENT_LLM_RETRY agent={} attempt={}/{} category={} backoff_seconds={}",
                        self.agent_name,
                        attempt + 1,
                        max_attempts,
                        category,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                # Out of retries. Re-raise so the caller's ``_safe``
                # wrapper sees the real exception (it logs + emits a
                # stream.error + returns None).
                logger.error(
                    "AGENT_LLM_RETRIES_EXHAUSTED agent={} attempts={} category={}",
                    self.agent_name,
                    max_attempts,
                    category,
                )
                raise
            except Exception:
                # Non-transient (parse error, bad request, etc.). Let
                # the caller handle — no point retrying the same input.
                raise

            last_resp = resp

            # Non-truncating: caller is happy. Parse and return.
            if resp.finish_reason != "length":
                data = self.parse_json_response(resp.content, fallback={})
                return resp, data, attempt + 1

            # Truncated. If we have retries left, give the LLM the tail
            # of the previous output as an anchor.
            if attempt < max_attempts - 1:
                tail = (resp.content or "")[-500:]
                feedback = LLMMessage(
                    role="user",
                    content=(
                        f"你上一次的输出被 max_tokens 限制截断了 "
                        f"(finish_reason=length)。\n\n"
                        f"截断处附近的最后 500 字符：\n```\n{tail}\n```\n\n"
                        f"请基于以上内容继续生成，确保所有字段都闭合、"
                        f"JSON 完整。如果可能，请在更紧凑的篇幅内重写。"
                    ),
                )
                msgs.append(feedback)
                logger.warning(
                    f"{self.agent_name} attempt {attempt + 1}/{max_attempts}: "
                    f"hit length cap at max_tokens={mt}; retrying with "
                    f"max_tokens={mt * 2}"
                )

        # All attempts truncated. Return the last response so the caller
        # can still salvage what it can (and the L2 truncation signal
        # is visible in ``resp.finish_reason``).
        logger.error(
            f"{self.agent_name}: all {max_attempts} attempts hit length cap; "
            f"final max_tokens={max_tokens * (2 ** (max_attempts - 1))}"
        )
        data = self.parse_json_response(
            (last_resp.content if last_resp else ""), fallback={}
        )
        return last_resp, data, max_attempts

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
            category = failure_category(exc)
            logger.error(
                "AGENT_LLM_STREAM_FAILED agent={} category={}",
                self.agent_name,
                category,
            )
            await stream.error(
                "LLM stream failed",
                source=source or self.agent_name,
                stage=stage,
                metadata=public_failure(
                    "AGENT_LLM_STREAM_FAILED",
                    "LLM stream failed",
                    retryable=category in {"timeout", "connection"},
                ),
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
            raise ValueError("AGENT_LLM_RESPONSE_INVALID_JSON")
        return fallback

    # ------------------------------------------------------------------
    # Trace
    # ------------------------------------------------------------------

    def _trace(self, payload: dict[str, Any]) -> None:
        if self.trace_callback is not None:
            try:
                self.trace_callback(payload)
            except Exception:
                logger.warning("AGENT_TRACE_CALLBACK_FAILED agent={}", self.agent_name)

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
