"""Tests for :mod:`tutor.services.manim_render.code_retry`."""

from __future__ import annotations

from typing import Awaitable, Callable
from unittest.mock import MagicMock

import pytest

from tutor.services.llm.base import LLMResponse
from tutor.services.manim_render.code_retry import CodeRetry


def _mock_llm(responses: list[str]):
    """Mock LLM returning successive responses."""
    queue = list(responses)
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(req):
        content = queue.pop(0) if queue else "{}"
        return LLMResponse(content=content, model="mock", finish_reason="stop")

    llm.call = call
    return llm


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------


def test_apply_patches_replaces_first_match():
    cr = CodeRetry(llm=_mock_llm([]), max_attempts=1)
    code = "x = 1\ny = 2\nx = x + 1\n"
    patches = [
        {"search": "x = 1", "replace": "x = 100", "explanation": "init"},
    ]
    out = cr._apply_patches(code, patches)
    # Only the FIRST occurrence is replaced
    assert out == "x = 100\ny = 2\nx = x + 1\n"


def test_apply_patches_skips_unmatched_search():
    cr = CodeRetry(llm=_mock_llm([]), max_attempts=1)
    code = "x = 1\n"
    patches = [
        {"search": "DOES NOT EXIST", "replace": "x = 99"},
    ]
    out = cr._apply_patches(code, patches)
    # Unchanged because search not found
    assert out == "x = 1\n"


def test_apply_multiple_patches():
    cr = CodeRetry(llm=_mock_llm([]), max_attempts=1)
    code = "a = 1\nb = 2\n"
    patches = [
        {"search": "a = 1", "replace": "a = 10"},
        {"search": "b = 2", "replace": "b = 20"},
    ]
    out = cr._apply_patches(code, patches)
    assert out == "a = 10\nb = 20\n"


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def test_parse_json_direct():
    cr = CodeRetry(llm=_mock_llm([]), max_attempts=1)
    out = cr._parse_json_safe('{"patches": []}')
    assert out == {"patches": []}


def test_parse_json_with_fences():
    cr = CodeRetry(llm=_mock_llm([]), max_attempts=1)
    out = cr._parse_json_safe('```json\n{"patches": []}\n```')
    assert out == {"patches": []}


def test_parse_json_with_prose_around():
    cr = CodeRetry(llm=_mock_llm([]), max_attempts=1)
    out = cr._parse_json_safe(
        'Here is the fix:\n{"patches": [{"search": "x", "replace": "y"}]}\nDone.'
    )
    assert out == {"patches": [{"search": "x", "replace": "y"}]}


def test_parse_json_invalid_returns_none():
    cr = CodeRetry(llm=_mock_llm([]), max_attempts=1)
    assert cr._parse_json_safe("not json at all") is None


# ---------------------------------------------------------------------------
# Full retry loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_succeeds_on_first_try():
    """render_fn returns success immediately — no patches needed."""
    cr = CodeRetry(llm=_mock_llm([]), max_attempts=3)

    async def render_fn(code: str) -> tuple[bool, str]:
        return True, ""

    result = await cr.fix_until_renderable(
        original_code="x = 1", render_fn=render_fn
    )
    assert result.success is True
    assert result.attempts_used == 1
    assert result.code == "x = 1"


@pytest.mark.asyncio
async def test_retry_succeeds_after_one_patch():
    """First render fails → LLM suggests patch → second render succeeds."""
    patches_json = (
        '{"patches": [{"search": "x = 1", "replace": "x = 2", '
        '"explanation": "fix bug"}], "explanation": "ok"}'
    )
    cr = CodeRetry(llm=_mock_llm([patches_json]), max_attempts=3)

    attempts = [0]

    async def render_fn(code: str) -> tuple[bool, str]:
        attempts[0] += 1
        if attempts[0] == 1:
            return False, "some error"
        return True, ""

    result = await cr.fix_until_renderable(
        original_code="x = 1", render_fn=render_fn
    )
    assert result.success is True
    assert result.attempts_used == 2
    assert "x = 2" in result.code


@pytest.mark.asyncio
async def test_retry_gives_up_after_max_attempts():
    """All attempts fail → success=False, attempts_used=max."""
    cr = CodeRetry(llm=_mock_llm([]), max_attempts=3)

    async def render_fn(code: str) -> tuple[bool, str]:
        return False, "always fails"

    result = await cr.fix_until_renderable(
        original_code="x = 1", render_fn=render_fn
    )
    assert result.success is False
    assert result.attempts_used == 3
    assert "always fails" in result.final_error


@pytest.mark.asyncio
async def test_retry_continues_after_empty_patches():
    """If LLM returns no patches, retry loop continues until max_attempts."""
    cr = CodeRetry(llm=_mock_llm(["{}"]), max_attempts=3)

    async def render_fn(code: str) -> tuple[bool, str]:
        return False, "fail"

    result = await cr.fix_until_renderable(
        original_code="x = 1", render_fn=render_fn
    )
    # Loops to max_attempts even without patches
    assert result.success is False
    assert result.attempts_used == 3


@pytest.mark.asyncio
async def test_retry_continues_after_no_op_patches():
    """If patches' search strings don't match, loop continues."""
    no_op_patches = json_dumps_no_op()
    cr = CodeRetry(llm=_mock_llm([no_op_patches]), max_attempts=3)

    async def render_fn(code: str) -> tuple[bool, str]:
        return False, "fail"

    result = await cr.fix_until_renderable(
        original_code="x = 1", render_fn=render_fn
    )
    # Patches were no-op but we keep trying
    assert result.success is False
    assert result.attempts_used == 3


def json_dumps_no_op():
    import json
    return json.dumps(
        {
            "patches": [
                {
                    "search": "DOES NOT EXIST IN CODE",
                    "replace": "x = 99",
                    "explanation": "noop",
                }
            ]
        }
    )


@pytest.mark.asyncio
async def test_retry_handles_llm_failure_gracefully():
    """If LLM call raises, retry still continues through max_attempts."""
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(req):
        raise RuntimeError("LLM down")

    llm.call = call
    cr = CodeRetry(llm=llm, max_attempts=3)

    async def render_fn(code: str) -> tuple[bool, str]:
        return False, "render fail"

    result = await cr.fix_until_renderable(
        original_code="x = 1", render_fn=render_fn
    )
    # LLM failures don't kill the loop — we go through max_attempts renders
    assert result.success is False
    assert result.attempts_used == 3
