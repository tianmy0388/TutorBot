"""Regression tests for ``BaseAgent.call_llm_with_retry`` (L2 retry).

The retry wrapper handles the ``finish_reason="length"`` case: when the
LLM hits ``max_tokens`` mid-output, retry with a doubled budget. This
fixes the recurring pattern observed in 2026-07-07 testing:

  * ``CodeSandboxAgent`` had its JSON ``code`` field truncated mid-string
    (line ~36 of a 50-line snippet) by the 2048-token budget.
  * ``PedagogyAgent`` had its 7-section JSON truncated by the 4096-token
    budget — the user's trace showed pedagogy_design content stream
    cut off at ``{ "title": "...", "summary": "从直观比喻到数``.
  * ``ManimVideoAgent`` had ``_codegen_max_tokens`` raised to 8192 in
    2026-06-22 but the storyboard could still exceed that for long
    multi-scene animations.

Each test below uses a mock LLM that returns scripted responses based
on attempt number, then asserts:
  * ``attempts_used`` matches expectation
  * ``parsed_data`` is the dict from the final response
  * The LLM was called with the expected ``max_tokens`` schedule
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from tutor.agents.base_agent import BaseAgent
from tutor.core.stream_bus import StreamBus
from tutor.services.llm.base import LLMResponse


# ---------------------------------------------------------------------------
# Scripted mock LLM
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedTurn:
    content: str
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)


class ScriptedLLM:
    """Return a different response per call.

    Each call pops the next entry from ``script``. If the script is
    exhausted, returns an empty ``{}`` with ``finish_reason="stop"``.
    Records every call's ``max_tokens`` for later assertion.
    """

    def __init__(self, script: list[_ScriptedTurn]):
        self._script = list(script)
        self.calls: list[int] = []  # max_tokens per call
        self.model = "scripted-mock"
        self.default_temperature = 0.5
        self.default_max_tokens = 1024

    async def call(self, req):
        self.calls.append(req.max_tokens)
        if self._script:
            turn = self._script.pop(0)
        else:
            turn = _ScriptedTurn(content="{}")
        return LLMResponse(
            content=turn.content,
            model="scripted-mock",
            finish_reason=turn.finish_reason,
            usage=turn.usage,
        )


class _TrivialAgent(BaseAgent):
    """Minimal concrete subclass so we can test BaseAgent directly."""

    module_name = "_test"
    agent_name = "_trivial"
    default_temperature = 0.5
    default_max_tokens = 1024

    async def process(self, context, stream=None):  # noqa: D401, ARG002
        return None


def _make_agent(llm):
    return _TrivialAgent(llm=llm)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_returns_first_response_when_no_truncation():
    """If the LLM does NOT truncate, the wrapper returns immediately
    with ``attempts_used == 1``."""
    complete_json = json.dumps({"code": "print('hi')", "title": "x"})
    llm = ScriptedLLM([_ScriptedTurn(complete_json)])
    agent = _make_agent(llm)
    resp, data, attempts = await agent.call_llm_with_retry(
        messages=[], max_tokens=1024, response_format={"type": "json_object"}
    )
    assert attempts == 1, f"expected 1 attempt, got {attempts}"
    assert data == {"code": "print('hi')", "title": "x"}
    # Only one LLM call.
    assert llm.calls == [1024]


@pytest.mark.asyncio
async def test_retry_doubles_max_tokens_on_truncation():
    """When the LLM returns ``finish_reason="length"``, the wrapper
    retries with ``max_tokens * 2`` then ``* 4``."""
    truncated = '{ "code": "import math\\ndef s'
    complete = json.dumps({"code": "import math\ndef sigmoid():\n    pass"})

    llm = ScriptedLLM(
        [
            _ScriptedTurn(truncated, finish_reason="length"),
            _ScriptedTurn(complete),
        ]
    )
    agent = _make_agent(llm)
    resp, data, attempts = await agent.call_llm_with_retry(
        messages=[], max_tokens=1024, response_format={"type": "json_object"}
    )
    assert attempts == 2, f"expected 2 attempts, got {attempts}"
    assert data == {"code": "import math\ndef sigmoid():\n    pass"}
    # First call: 1024. Second call: 2048 (doubled).
    assert llm.calls == [1024, 2048], f"unexpected max_tokens schedule: {llm.calls}"


@pytest.mark.asyncio
async def test_retry_keeps_doubling_until_success():
    """Three-attempt scenario: truncate, truncate, success."""
    truncated = '{ "x": "'
    complete = json.dumps({"x": "ok"})

    llm = ScriptedLLM(
        [
            _ScriptedTurn(truncated, finish_reason="length"),
            _ScriptedTurn(truncated, finish_reason="length"),
            _ScriptedTurn(complete),
        ]
    )
    agent = _make_agent(llm)
    resp, data, attempts = await agent.call_llm_with_retry(
        messages=[], max_tokens=1024, response_format={"type": "json_object"}
    )
    assert attempts == 3
    assert data == {"x": "ok"}
    # Schedule: 1024 → 2048 → 4096
    assert llm.calls == [1024, 2048, 4096]


@pytest.mark.asyncio
async def test_retry_gives_up_after_max_attempts():
    """If every attempt truncates, return the last response with
    ``attempts == max_attempts`` and parsed data being whatever
    the last truncated payload can yield (fallback ``{}`` if
    unparseable)."""
    truncated = '{ "x": "'

    llm = ScriptedLLM(
        [
            _ScriptedTurn(truncated, finish_reason="length"),
            _ScriptedTurn(truncated, finish_reason="length"),
            _ScriptedTurn(truncated, finish_reason="length"),
        ]
    )
    agent = _make_agent(llm)
    resp, data, attempts = await agent.call_llm_with_retry(
        messages=[],
        max_tokens=1024,
        max_attempts=3,
        response_format={"type": "json_object"},
    )
    assert attempts == 3
    # The last response was truncated JSON — fallback {}.
    assert data == {}, f"expected fallback dict, got {data!r}"
    # All three attempts fired with the doubling schedule.
    assert llm.calls == [1024, 2048, 4096]


@pytest.mark.asyncio
async def test_retry_respects_default_max_tokens_when_omitted():
    """If ``max_tokens=None``, the wrapper falls back to the agent's
    ``default_max_tokens`` and doubles from there."""
    complete = json.dumps({"x": 1})
    llm = ScriptedLLM([_ScriptedTurn(complete)])
    agent = _make_agent(llm)
    # agent.default_max_tokens == 1024 in _TrivialAgent
    resp, data, attempts = await agent.call_llm_with_retry(
        messages=[], response_format={"type": "json_object"}
    )
    assert attempts == 1
    assert llm.calls == [1024]


@pytest.mark.asyncio
async def test_retry_handles_unparseable_json_without_retry():
    """If the LLM does NOT truncate but returns unparseable JSON, the
    wrapper falls back to ``{}`` and does NOT retry. (L1 — parse-failure
    retry — is a separate concern, deferred per the design doc.)"""
    garbage = "not json at all >>>"
    llm = ScriptedLLM([_ScriptedTurn(garbage, finish_reason="stop")])
    agent = _make_agent(llm)
    resp, data, attempts = await agent.call_llm_with_retry(
        messages=[], max_tokens=1024, response_format={"type": "json_object"}
    )
    assert attempts == 1, "should not retry on parse failure (L1 deferred)"
    assert data == {}, f"expected fallback dict, got {data!r}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))