"""Regression test: ``call_llm_with_retry`` must retry transient
network errors, not just truncation.

2db13ad8 trace showed a single ``openai.APITimeoutError`` (DeepSeek
took >60s on the read) escaping the retry wrapper and turning the
video generation into an immediate ``None`` resource. Before this
fix, ``call_llm_with_retry`` only retried on ``finish_reason="length"``
truncation; any network blip failed-fast and the caller (``_safe``)
saw the exception.

After the fix, transient errors are retried with exponential backoff,
sharing the ``max_attempts`` budget with truncation retries. Non-
transient errors (parse, bad request) still fail-fast.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from tutor.agents.base_agent import BaseAgent


class _StubLLM:
    """Returns a sequence of (result_or_exception) tuples, one per call."""

    # ``call_llm`` reads ``resolved_llm.model`` for trace logging — fake it.
    model = "fake-model"

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls = 0

    async def call(self, req):  # type: ignore[no-untyped-def]
        idx = self.calls
        self.calls += 1
        if idx >= len(self._script):
            raise AssertionError(
                f"stub script exhausted at call #{idx + 1} "
                f"(script had {len(self._script)} entries)"
            )
        action = self._script[idx]
        if isinstance(action, BaseException):
            raise action
        return action


class _Recorder(BaseAgent):
    """Minimal concrete subclass so we can exercise ``call_llm_with_retry``."""

    module_name = "test"
    agent_name = "test_recorder"

    def __init__(self, llm_response_or_exc: Any) -> None:
        super().__init__()
        self._stub = _StubLLM(llm_response_or_exc if isinstance(llm_response_or_exc, list) else [llm_response_or_exc])

    async def process(self, context, stream=None):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    @property
    def resolved_llm(self):  # type: ignore[override]
        return self._stub


def _make_resp(content: str, finish_reason: str = "stop"):  # type: ignore[no-untyped-def]
    from tutor.services.llm.base import LLMResponse

    return LLMResponse(content=content, model="m", finish_reason=finish_reason, usage={})


# ---------------------------------------------------------------------------
# Transient retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retries_on_api_timeout_and_eventually_succeeds() -> None:
    """2db13ad8 reproduction: APITimeoutError then success."""
    from openai import APITimeoutError

    fake_req = object()  # the APITimeoutError just needs *a* request
    timeout_exc = APITimeoutError(request=fake_req)  # type: ignore[arg-type]

    # Fake APITimeoutError raised twice, then a real response.
    agent = _Recorder([timeout_exc, timeout_exc, _make_resp('{"ok": true}')])

    resp, data, attempts = await agent.call_llm_with_retry(
        messages=[],
        max_attempts=3,
    )

    assert resp.finish_reason == "stop"
    assert data == {"ok": True}
    assert attempts == 3
    assert agent._stub.calls == 3


@pytest.mark.asyncio
async def test_retries_on_asyncio_timeout_error() -> None:
    """AsyncioTimeoutError (e.g. asyncio.wait_for) is also transient."""
    agent = _Recorder(
        [asyncio.TimeoutError(), _make_resp('{"ok": true}')]
    )
    resp, data, attempts = await agent.call_llm_with_retry(
        messages=[],
        max_attempts=3,
    )
    assert attempts == 2
    assert resp.finish_reason == "stop"


@pytest.mark.asyncio
async def test_retries_on_connection_error() -> None:
    """Plain ``ConnectionError`` (httpx wraps these) must retry."""
    agent = _Recorder(
        [ConnectionError("refused"), _make_resp('{"ok": true}')]
    )
    resp, _, attempts = await agent.call_llm_with_retry(
        messages=[],
        max_attempts=3,
    )
    assert attempts == 2


@pytest.mark.asyncio
async def test_retries_on_api_connection_error() -> None:
    """openai.APIConnectionError (httpx ReadError etc.) must retry."""
    from openai import APIConnectionError

    fake_req = object()
    agent = _Recorder(
        [
            APIConnectionError(request=fake_req),  # type: ignore[arg-type]
            _make_resp('{"ok": true}'),
        ]
    )
    resp, _, attempts = await agent.call_llm_with_retry(
        messages=[],
        max_attempts=3,
    )
    assert attempts == 2


# ---------------------------------------------------------------------------
# Exhaustion: must re-raise, not return silently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raises_after_max_transient_attempts() -> None:
    """If every attempt is a transient error, the LAST exception must
    propagate to the caller. Silent fallback would mask real outages.
    """
    from openai import APITimeoutError

    fake_req = object()
    agent = _Recorder(
        [
            APITimeoutError(request=fake_req),  # type: ignore[arg-type]
            APITimeoutError(request=fake_req),  # type: ignore[arg-type]
            APITimeoutError(request=fake_req),  # type: ignore[arg-type]
        ]
    )
    with pytest.raises(APITimeoutError):
        await agent.call_llm_with_retry(messages=[], max_attempts=3)
    assert agent._stub.calls == 3


# ---------------------------------------------------------------------------
# Non-transient errors must fail-fast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_transient_error_does_not_retry() -> None:
    """A ``ValueError`` from the provider is not transient — retrying
    the same prompt won't help and would waste budget.
    """
    agent = _Recorder([ValueError("bad input"), _make_resp('{"ok": true}')])
    with pytest.raises(ValueError):
        await agent.call_llm_with_retry(messages=[], max_attempts=3)
    assert agent._stub.calls == 1  # no retry


# ---------------------------------------------------------------------------
# Truncation retry path still works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncation_retry_still_works() -> None:
    """Sanity: the original ``finish_reason="length"`` retry path
    must still fire after the network retry additions.
    """
    agent = _Recorder(
        [
            _make_resp('{"a": ', finish_reason="length"),
            _make_resp('{"a": 1}', finish_reason="stop"),
        ]
    )
    resp, data, attempts = await agent.call_llm_with_retry(
        messages=[],
        max_tokens=1024,
        max_attempts=3,
    )
    assert resp.finish_reason == "stop"
    assert data == {"a": 1}
    assert attempts == 2


@pytest.mark.asyncio
async def test_truncation_and_network_share_max_attempts_budget() -> None:
    """If truncation AND network errors co-occur, we don't get 6
    total calls. ``max_attempts`` is the total budget.
    """
    from openai import APITimeoutError

    fake_req = object()
    agent = _Recorder(
        [
            _make_resp('{"a": ', finish_reason="length"),  # truncation attempt 1
            APITimeoutError(request=fake_req),  # type: ignore[arg-type]
            # Should NOT happen — budget exhausted
            _make_resp('{"a": 1}', finish_reason="stop"),
        ]
    )
    with pytest.raises(APITimeoutError):
        await agent.call_llm_with_retry(messages=[], max_attempts=2)
    assert agent._stub.calls == 2


@pytest.mark.asyncio
async def test_retries_on_httpx_read_timeout_when_openai_missing() -> None:
    """fdb26152 reproduction 2: in some installations (Anaconda's
    patched openai-compat, older openai versions), ``APITimeoutError``
    isn't importable from ``openai``. The retry wrapper MUST still
    catch the underlying httpx ``ReadTimeout`` that propagates up
    the stack so we don't fail-fast on flaky networks.
    """
    import httpx

    agent = _Recorder(
        [
            httpx.ReadTimeout("simulated 60s read timeout"),
            _make_resp('{"ok": true}'),
        ]
    )
    resp, data, attempts = await agent.call_llm_with_retry(
        messages=[],
        max_attempts=3,
    )
    assert attempts == 2
    assert resp.finish_reason == "stop"
    assert data == {"ok": True}


@pytest.mark.asyncio
async def test_retries_on_httpx_connect_error() -> None:
    """httpx.ConnectError covers DNS / refused-connection blips."""
    import httpx

    agent = _Recorder(
        [
            httpx.ConnectError("dns failure"),
            _make_resp('{"ok": true}'),
        ]
    )
    resp, _, attempts = await agent.call_llm_with_retry(
        messages=[],
        max_attempts=3,
    )
    assert attempts == 2


@pytest.mark.asyncio
async def test_retries_on_plain_oserror_when_no_provider_classes() -> None:
    """Belt-and-suspenders fallback: a generic ``OSError`` / socket
    error from older openai versions or proxies must still retry.
    Without this, one socket hiccup turns into a hard failure.
    """
    agent = _Recorder(
        [
            OSError("simulated socket reset"),
            _make_resp('{"ok": true}'),
        ]
    )
    resp, _, attempts = await agent.call_llm_with_retry(
        messages=[],
        max_attempts=3,
    )
    assert attempts == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))