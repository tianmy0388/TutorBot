"""Regression test for the Windows-GBK stdout crash.

The original code_sandbox ran the user's snippet via subprocess.run
with ``text=True`` and NO explicit ``encoding=``. On Chinese Windows
the parent uses GBK to decode the pipe — but the subprocess is told
``PYTHONIOENCODING=utf-8``, so any non-GBK byte in the snippet's
output crashed the reader thread and left ``proc.stdout`` as ``None``
(or empty). The caller then did ``stdout[:2000]`` and crashed with
``'NoneType' object is not subscriptable``.

This test simulates a code path that prints Chinese bytes — which
is what an LLM-generated snippet for "反向传播" would produce — and
asserts the CodeSandboxAgent returns a resource (with a string
``stdout``) rather than crashing.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest
from tutor.agents.resource.code_sandbox import CodeSandboxAgent, _bounded_utf8
from tutor.core.context import UnifiedContext
from tutor.services.llm.base import LLMResponse


def _mock_llm(*responses: str):
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048
    queue = list(responses)

    async def call(req):
        c = queue.pop(0) if queue else "{}"
        return LLMResponse(content=c, model="mock", finish_reason="stop")

    llm.call = call
    return llm


@pytest.mark.asyncio
async def test_code_sandbox_handles_non_ascii_subprocess_output():
    """Snippet prints Chinese — would have crashed the GBK reader."""
    snippet = (
        "import numpy as np\n"
        "print('反向传播 sigmoid 输出:', 1 / (1 + np.exp(-1)))\n"
    )
    llm = _mock_llm(json.dumps({
        "title": "反向传播 sigmoid",
        "language": "python",
        "code": snippet,
        "explanation": "示例",
        "difficulty": 3,
    }, ensure_ascii=False))
    agent = CodeSandboxAgent(llm=llm)
    ctx = UnifiedContext(user_message="什么是反向传播？")
    resource = await agent.process(ctx, topic="反向传播")
    assert resource.type.value == "code"
    # The stdout slot must be a string (never None) — even if the
    # subprocess decoder raised mid-stream.
    stdout = resource.format_specific.get("stdout")
    assert isinstance(stdout, str), f"stdout should be str, got {type(stdout).__name__}: {stdout!r}"
    # And the Chinese text should have made it through.
    assert "反向传播" in stdout or "sigmoid" in stdout


def test_submission_output_replaces_invalid_utf8_bytes_deterministically():
    assert _bounded_utf8("中文输出".encode() + b"\xff") == "中文输出�"


if __name__ == "__main__":
    # Allow ``python tests/.../test_xxx.py`` direct invocation.
    import pytest
    sys.exit(pytest.main([__file__, "-xvs"]))
