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

import json
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.services.llm.base import LLMMessage, LLMProvider, LLMRequest


@dataclass
class RetryResult:
    """Outcome of one fix attempt."""

    success: bool
    code: str
    attempts_used: int
    final_error: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "attempts_used": self.attempts_used,
            "final_error": self.final_error,
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
        code = original_code
        history: list[dict[str, Any]] = []
        attempts_used = 0

        for attempt in range(1, self.max_attempts + 1):
            attempts_used = attempt
            success, error = await render_fn(code)
            if success:
                return RetryResult(
                    success=True,
                    code=code,
                    attempts_used=attempt,
                    history=history,
                )

            history.append(
                {"attempt": attempt, "ok": False, "error": error[:500]}
            )
            # If we have more attempts left, try to get patches
            if attempt < self.max_attempts:
                patches = await self._ask_llm(code, error, attempt)
                if not patches:
                    history.append(
                        {"attempt": attempt, "patch": "no patches returned"}
                    )
                else:
                    new_code = self._apply_patches(code, patches)
                    if new_code == code:
                        history.append(
                            {
                                "attempt": attempt,
                                "patch": "patches did not match code",
                            }
                        )
                    else:
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
            final_error=(
                history[-1].get("error", "") if history else "no attempts"
            ),
            history=history,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _ask_llm(
        self,
        code: str,
        error: str,
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
                    f"## Error from render\n"
                    f"```\n{error[:2000]}\n```\n\n"
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
            logger.warning(f"CodeRetry LLM call failed: {exc!r}")
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
        """Apply patches in order. Skip patches whose 'search' isn't found."""
        out = code
        for p in patches:
            search = p["search"]
            replace = p["replace"]
            if search in out:
                # Replace only the FIRST occurrence to avoid over-matching
                out = out.replace(search, replace, 1)
            else:
                logger.debug(f"Patch search not found, skipping: {search[:80]}")
        return out

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


__all__ = ["CodeRetry", "RetryResult", "RETRY_OUTPUT_SCHEMA"]
