from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from tutor.core.tool_protocol import ToolResult
from tutor.services.search import SearchExecutor


class _Registry:
    def __init__(self, tool=None) -> None:
        self.tool = tool
        self.get_calls = 0

    def get(self, name: str):
        assert name == "web_search"
        self.get_calls += 1
        return self.tool


class _Tool:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    async def execute(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def _settings(*, enabled: bool = True):
    return SimpleNamespace(
        web_search_enabled=enabled,
        web_search_provider="fake-provider",
        web_search_max_results=2,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conversation_enabled", "runtime_enabled"),
    [(False, True), (True, False), (False, False)],
)
async def test_disabled_gate_never_resolves_or_executes_tool(
    conversation_enabled: bool, runtime_enabled: bool
) -> None:
    tool = _Tool(ToolResult(success=True, data={"results": []}))
    registry = _Registry(tool)
    executor = SearchExecutor(
        registry=registry,
        settings_getter=lambda: _settings(enabled=runtime_enabled),
    )

    outcome = await executor.execute("query", conversation_enabled=conversation_enabled)

    assert outcome.search_used is False
    assert outcome.unavailable is False
    assert registry.get_calls == 0
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_enabled_search_normalizes_safe_bounded_sources() -> None:
    tool = _Tool(
        ToolResult(
            success=True,
            data={
                "provider": "provider-result",
                "results": [
                    {
                        "title": "<b>Safe\x00 title</b>",
                        "url": "https://example.com/a",
                        "snippet": "<script>bad()</script>Useful excerpt",
                    },
                    {
                        "title": "Second",
                        "link": "http://example.org/b",
                        "description": "B",
                    },
                    {
                        "title": "Dropped by limit",
                        "url": "https://example.net/c",
                    },
                    {"title": "Unsafe", "url": "javascript:alert(1)"},
                ],
            },
        )
    )
    registry = _Registry(tool)
    executor = SearchExecutor(
        registry=registry,
        settings_getter=lambda: _settings(),
    )

    outcome = await executor.execute("current facts", conversation_enabled=True)

    assert outcome.search_used is True
    assert outcome.unavailable is False
    assert registry.get_calls == 1
    assert tool.calls == 1
    assert [source.url for source in outcome.sources] == [
        "https://example.com/a",
        "http://example.org/b",
    ]
    first = outcome.sources[0].to_dict()
    assert first["title"] == "Safe title"
    assert first["excerpt"] == "bad()Useful excerpt"
    assert first["provider"] == "provider-result"
    assert first["retrieved_at"].endswith("+00:00")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool",
    [
        _Tool(ToolResult(success=True, data={"results": [], "message": "placeholder"})),
        _Tool(ToolResult(success=False, error="private api key abc")),
        _Tool(ToolResult(success=True, data={"results": "bad"})),
        _Tool(error=RuntimeError("private provider failure")),
        None,
    ],
)
async def test_provider_failures_and_placeholder_are_typed_unavailability(tool) -> None:
    executor = SearchExecutor(
        registry=_Registry(tool),
        settings_getter=lambda: _settings(),
        timeout_seconds=0.1,
    )

    outcome = await executor.execute("query", conversation_enabled=True)

    assert outcome.search_used is False
    assert outcome.sources == ()
    assert outcome.unavailable is True
    assert outcome.degradation_code == "WEB_SEARCH_UNAVAILABLE"
    assert "private" not in repr(outcome)


@pytest.mark.asyncio
async def test_provider_timeout_is_typed_unavailability() -> None:
    class _SlowTool:
        async def execute(self, **kwargs):
            await asyncio.sleep(1)

    outcome = await SearchExecutor(
        registry=_Registry(_SlowTool()),
        settings_getter=lambda: _settings(),
        timeout_seconds=0.01,
    ).execute("query", conversation_enabled=True)

    assert outcome.degradation_code == "WEB_SEARCH_UNAVAILABLE"
