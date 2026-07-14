"""Regression test: PedagogyAgent token caps must stay tight.

187b2955 trace analysis (Phase 1) showed the old defaults caused a single
pedagogy LLM call to consume 221s (out of a 600s budget):

    default_max_tokens = 4096
    default_max_attempts = 3
    → final attempt max_tokens = 4096 * 2^2 = 16384

Pedagogy's JSON output schema is bounded (sections, key_points, examples,
thinking_prompts, etc.); no single section needs >2k tokens. We cap:

    default_max_tokens = 2048
    default_max_attempts = 2
    → final attempt max_tokens = 2048 * 2 = 4096

This test pins both attributes so a future "harmless tweak" can't bring
back the 600s regression silently.
"""

from __future__ import annotations

import sys

import pytest

from tutor.agents.resource.pedagogy import PedagogyAgent


def test_pedagogy_default_max_tokens_is_2048() -> None:
    """Max tokens must be small enough to fit in ~10s of generation."""
    assert PedagogyAgent.default_max_tokens == 2048, (
        f"pedagogy max_tokens regression: expected 2048, got "
        f"{PedagogyAgent.default_max_tokens}"
    )


def test_pedagogy_default_max_attempts_is_2() -> None:
    """Retry must stop after 2 attempts to bound worst-case latency.

    With max_attempts=3 and max_tokens doubling, the final attempt
    could exceed 16k tokens, which translates to 100s+ on most
    models. We accept at most a single retry (2 attempts total).
    """
    assert PedagogyAgent.default_max_attempts == 2, (
        f"pedagogy max_attempts regression: expected 2, got "
        f"{PedagogyAgent.default_max_attempts}"
    )


@pytest.mark.asyncio
async def test_pedagogy_process_passes_max_attempts_to_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``process`` must call ``call_llm_with_retry`` with the bounded
    max_attempts (not the base 3). We intercept the retry helper and
    assert the kwarg.
    """
    captured: dict[str, int] = {}

    async def fake_retry(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured["max_attempts"] = int(kwargs.get("max_attempts", -1))
        # Return a minimal successful response so ``process`` doesn't crash
        class _Resp:
            content = '{"title": "t", "sections": []}'
            finish_reason = "stop"
            usage = {}

        return _Resp(), {"title": "t", "sections": []}, 1

    agent = PedagogyAgent()
    monkeypatch.setattr(agent, "call_llm_with_retry", fake_retry)

    from tutor.core.context import UnifiedContext
    from tutor.services.resource_package.schema import (
        Resource,
        ResourceType,
    )

    src = Resource(
        type=ResourceType.DOCUMENT,
        title="测试",
        content="# 测试\n\n内容",
        topic="测试",
    )

    await agent.process(
        UnifiedContext(language="zh"),
        stream=None,
        source_resource=src,
        profile={},
    )
    assert captured.get("max_attempts") == 2, (
        f"expected pedagogy to bound retry to 2, got "
        f"{captured.get('max_attempts')}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))