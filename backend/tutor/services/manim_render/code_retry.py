"""CodeRetry — LLM-based automatic fix loop for failed Manim code.

Inspired by ManimCat's ``code-retry``. On render failure:

1. Extract the error message from ``stderr``
2. Ask the LLM to produce a SEARCH/REPLACE patch (JSON)
3. Apply the patch to the code
4. Re-attempt (up to ``max_attempts``)

The LLM is given:
- The original code
- The previous attempt's code (if any)
- The error message
- A simple "fix this Python code" instruction

Output schema:
```json
{
  "patches": [
    {"search": "old snippet", "replace": "new snippet", "explanation": "..."},
    ...
  ],
  "explanation": "overall why this should fix it"
}
```

If the LLM returns invalid patches (search not found), the retry is
skipped and the loop continues with the next attempt.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tokenize
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from tutor.services.llm.base import LLMMessage, LLMProvider, LLMRequest
from tutor.services.logging import redact_sensitive
from tutor.services.manim_render.executor import (
    RenderFailure,
    safe_failure_summary,
    tail_lines,
)


@dataclass
class RetryResult:
    """Outcome of one fix attempt."""

    success: bool
    code: str
    attempts_used: int
    final_error: str = ""
    error_code: str = ""
    failure: RenderFailure | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "attempts_used": self.attempts_used,
            "final_error": self.final_error,
            "error_code": self.error_code,
            "failure": self.failure.to_dict() if self.failure else None,
            "history": list(self.history),
            "code_chars": len(self.code),
        }


# JSON schema for the LLM output
RETRY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "patches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "search": {"type": "string"},
                    "replace": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["search", "replace"],
            },
        },
        "explanation": {"type": "string"},
    },
    "required": ["patches"],
}


RETRY_SYSTEM_PROMPT = """You are a code-fixing assistant for the Manim animation library.

You receive:
- The current Python source code
- An error message from a failed render

Your job: emit SEARCH/REPLACE patches that fix the code.

Rules:
- Output JSON with shape {"patches": [...], "explanation": "..."}
- Each patch: {"search": "exact substring from code", "replace": "fixed substring", "explanation": "why"}
- The 'search' string MUST appear verbatim in the code (whitespace and all)
- Keep patches minimal — fix ONLY the error, don't rewrite unrelated code
- If you cannot identify a fix, return empty patches list
- Never invent APIs that don't exist in Manim Community Edition v0.20
- For ImportError: change import statement; don't add fake modules
- For AttributeError: check Manim docs for correct attribute name
- For TypeError: check function signature

DO NOT modify scene class name or construct() method signature."""


class CodeRetry:
    """Apply LLM-generated SEARCH/REPLACE patches to fix broken Manim code."""

    def __init__(
        self,
        *,
        llm: LLMProvider | None = None,
        max_attempts: int = 4,
    ) -> None:
        self._own_agent = llm is None
        self.llm = llm  # may be None → lazy via BaseAgent
        self.max_attempts = max(1, max_attempts)

    async def fix_until_renderable(
        self,
        *,
        original_code: str,
        render_fn,  # Callable[[str], Awaitable[Tuple[bool, str]]]
    ) -> RetryResult:
        """Loop: render → if fail, ask LLM to patch → repeat up to ``max_attempts``.

        ``render_fn`` returns ``(success, error_message)``. It must NOT raise
        for normal failure cases (caller's responsibility).

        Notes
        -----
        - The loop runs EXACTLY ``max_attempts`` render attempts.
        - If render succeeds on attempt N, return early with ``attempts_used=N``.
        - Between attempts, the LLM is asked for patches. If it returns nothing
          or patches don't change the code, we continue with the same code.
        - ``attempts_used`` reflects the number of *render* attempts (not the
          number of LLM calls).
        """
        code = normalize_generated_source(original_code)
        history: list[dict[str, Any]] = []
        attempts_used = 0
        rendered_hashes: set[str] = set()
        last_failure: RenderFailure | None = None

        for attempt in range(1, self.max_attempts + 1):
            attempts_used = attempt
            source_hash = _source_hash(code)
            rendered_hashes.add(source_hash)
            success, raw_failure = await render_fn(code)
            if success:
                return RetryResult(
                    success=True,
                    code=(original_code if attempt == 1 else code),
                    attempts_used=attempt,
                    history=history,
                )

            last_failure = _coerce_failure(raw_failure)

            history.append(
                {
                    "attempt": attempt,
                    "ok": False,
                    "error_code": last_failure.error_code,
                    "summary": last_failure.summary,
                    "traceback_tail": list(last_failure.traceback_tail),
                    "log_artifact_key": last_failure.log_artifact_key,
                }
            )
            # If we have more attempts left, try to get patches
            if attempt < self.max_attempts:
                patches = await self._ask_llm(code, last_failure, attempt)
                if not patches:
                    history.append(
                        {"attempt": attempt, "patch": "no patches returned"}
                    )
                    new_code = code
                else:
                    new_code = normalize_generated_source(
                        self._apply_patches(code, patches)
                    )
                    if _source_hash(new_code) in rendered_hashes:
                        history.append(
                            {
                                "attempt": attempt,
                                "patch": "patches did not match code",
                            }
                        )
                if _source_hash(new_code) in rendered_hashes:
                    unchanged = RenderFailure(
                        error_code="unchanged_retry",
                        summary=safe_failure_summary(
                            f"Retry produced unchanged source. Prior failure: "
                            f"{last_failure.summary}",
                            fallback="Retry produced unchanged Manim source",
                        ),
                        traceback_tail=last_failure.traceback_tail,
                        log_artifact_key=last_failure.log_artifact_key,
                    )
                    return RetryResult(
                        success=False,
                        code=code,
                        attempts_used=attempts_used,
                        final_error=unchanged.summary,
                        error_code=unchanged.error_code,
                        failure=unchanged,
                        history=history,
                    )
                history.append(
                    {
                        "attempt": attempt,
                        "patch": "applied",
                        "patch_count": len(patches),
                    }
                )
                code = new_code

        # Failed after max_attempts
        return RetryResult(
            success=False,
            code=code,
            attempts_used=attempts_used,
            final_error=(last_failure.summary if last_failure else "no attempts"),
            error_code=(last_failure.error_code if last_failure else "render_failed"),
            failure=last_failure,
            history=history,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _ask_llm(
        self,
        code: str,
        failure: RenderFailure,
        attempt: int,
    ) -> list[dict[str, str]]:
        """Call the LLM to produce SEARCH/REPLACE patches."""
        messages = [
            LLMMessage(
                role="system",
                content=RETRY_SYSTEM_PROMPT,
            ),
            LLMMessage(
                role="user",
                content=(
                    f"## Manim code (attempt {attempt})\n"
                    f"```python\n{code[:6000]}\n```\n\n"
                    f"## Stable render failure\n"
                    f"error_code: {failure.error_code}\n"
                    f"traceback_tail (last {len(failure.traceback_tail)} lines):\n"
                    f"```\n{chr(10).join(failure.traceback_tail)}\n```\n\n"
                    "Correction requirement: change the source in a way that "
                    "directly addresses this error before another render.\n\n"
                    f"Please return SEARCH/REPLACE patches as JSON."
                ),
            ),
        ]
        request = LLMRequest(
            messages=messages,
            temperature=0.2,
            max_tokens=2048,
        )

        try:
            llm = self._get_llm()
            resp = await llm.call(request)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "MANIM_RETRY_LLM_FAILED details={details}",
                details=redact_sensitive(
                    {
                        "error_code": "MANIM_RETRY_LLM_FAILED",
                        "exception_type": type(exc).__name__,
                    }
                ),
            )
            return []

        data = self._parse_json_safe(resp.content)
        if not isinstance(data, dict):
            return []
        patches = data.get("patches")
        if not isinstance(patches, list):
            return []
        out: list[dict[str, str]] = []
        for p in patches:
            if not isinstance(p, dict):
                continue
            search = p.get("search")
            replace = p.get("replace")
            if isinstance(search, str) and isinstance(replace, str):
                out.append(
                    {
                        "search": search,
                        "replace": replace,
                        "explanation": str(p.get("explanation") or ""),
                    }
                )
        return out

    def _get_llm(self) -> LLMProvider:
        """Resolve the LLM lazily."""
        if self.llm is None:
            # Lazy default — uses settings
            from tutor.services.llm.provider_factory import get_runtime_provider

            self.llm = get_runtime_provider()
        return self.llm

    @staticmethod
    def _apply_patches(
        code: str, patches: list[dict[str, str]]
    ) -> str:
        """Apply only exact, unique searches aligned to token boundaries."""
        out = code
        for p in patches:
            search = p["search"]
            replace = p["replace"]
            start = out.find(search)
            if (
                search
                and start >= 0
                and out.find(search, start + 1) < 0
                and CodeRetry._has_token_boundaries(out, start, search)
            ):
                out = out.replace(search, replace, 1)
            else:
                logger.debug(
                    "MANIM_PATCH_SEARCH_MISSED details={details}",
                    details=redact_sensitive(
                        {
                            "error_code": "MANIM_PATCH_SEARCH_MISSED",
                            "source_code": search,
                        }
                    ),
                )
        return out

    @staticmethod
    def _has_token_boundaries(code: str, start: int, search: str) -> bool:
        """Return whether a substring does not cut through a Python token."""
        end = start + len(search)
        line_offsets = [0]
        for line in code.splitlines(keepends=True):
            line_offsets.append(line_offsets[-1] + len(line))

        def absolute(position: tuple[int, int]) -> int:
            row, column = position
            line_index = min(max(row - 1, 0), len(line_offsets) - 1)
            return min(line_offsets[line_index] + column, len(code))

        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
        except (IndentationError, tokenize.TokenError):
            tokens = []
        if tokens:
            token_starts = {absolute(token.start) for token in tokens}
            token_ends = {absolute(token.end) for token in tokens}
            if start not in token_starts or end not in token_ends:
                return False
            for token in tokens:
                if token.type not in {tokenize.STRING, tokenize.COMMENT}:
                    continue
                protected_start = absolute(token.start)
                protected_end = absolute(token.end)
                if start < protected_end and end > protected_start:
                    return False
            return True

        # Tokenization can fail on the malformed source that a retry is meant
        # to repair. Retain the conservative lexical fallback for that case.
        before = code[start - 1] if start else ""
        after = code[end] if end < len(code) else ""
        first = search[0]
        last = search[-1]
        if first.isalnum() or first == "_":
            if before.isalnum() or before == "_":
                return False
            if first.isdigit() and before == ".":
                return False
        if last.isalnum() or last == "_":
            if after.isalnum() or after == "_":
                return False
            if last.isdigit() and after == ".":
                return False
        return True

    @staticmethod
    def _parse_json_safe(content: str) -> Any:
        """Tolerant JSON parse."""
        if not content:
            return None
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            pass
        # Try to find { ... } block
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except (ValueError, TypeError):
                pass
        return None


def normalize_generated_source(code: str) -> str:
    """Canonicalize generated source for rendering and retry hashing."""
    text = (code or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    fenced = re.fullmatch(
        r"```(?:python|py)?\s*\n(?P<body>.*)\n```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        text = fenced.group("body")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def _source_hash(code: str) -> str:
    return hashlib.sha256(normalize_generated_source(code).encode("utf-8")).hexdigest()


def _coerce_failure(value: Any) -> RenderFailure:
    if isinstance(value, RenderFailure):
        return value
    if isinstance(value, dict):
        return RenderFailure(
            error_code=str(value.get("error_code") or "render_failed"),
            summary=safe_failure_summary(
                str(value.get("summary") or ""),
                fallback="Manim rendering failed",
            ),
            traceback_tail=tuple(
                str(line) for line in (value.get("traceback_tail") or ())
            )[-120:],
            log_artifact_key=str(value.get("log_artifact_key") or ""),
        )
    text = str(value or "render failed")
    return RenderFailure(
        error_code="render_failed",
        summary=safe_failure_summary(text, fallback="Manim rendering failed"),
        traceback_tail=tail_lines(text),
    )


__all__ = [
    "CodeRetry",
    "RetryResult",
    "RETRY_OUTPUT_SCHEMA",
    "normalize_generated_source",
]
