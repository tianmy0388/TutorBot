from __future__ import annotations

from types import SimpleNamespace

import pytest
from loguru import logger
from tutor.tools.mcp_web_search_tool import MCPWebSearchTool


class _Registry:
    def __init__(self, *, result=None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error

    async def call_tool(self, _server, _tool, _arguments):
        if self._error is not None:
            raise self._error
        return self._result


def _tool(registry: _Registry) -> MCPWebSearchTool:
    tool = MCPWebSearchTool(registry=registry)  # type: ignore[arg-type]
    tool._resolved = True
    tool._server_name = "MiniMax"
    tool._tool_name = "web_search"
    tool._max_results = 5
    return tool


@pytest.mark.asyncio
async def test_minimax_exception_is_safe_in_logs_and_tool_result() -> None:
    secret = "SECRET_MINIMAX_QUERY_OR_KEY"
    tool = _tool(_Registry(error=RuntimeError(f"query/key={secret}")))
    records = []
    sink_id = logger.add(records.append, format="{message}", level="ERROR")
    try:
        result = await tool.execute(query="private learner query")
    finally:
        logger.remove(sink_id)

    captured = "\n".join(str(record) for record in records)
    assert result.success is False
    assert result.error == "MCP web search unavailable"
    assert "MCP_WEB_SEARCH_FAILED" in captured
    assert "MiniMax" in captured
    assert "RuntimeError" in captured
    assert secret not in captured
    assert secret not in (result.error or "")


@pytest.mark.asyncio
async def test_minimax_provider_error_never_returns_raw_payload() -> None:
    secret = "SECRET_MINIMAX_PROVIDER_PAYLOAD"
    tool = _tool(
        _Registry(
            result=SimpleNamespace(
                is_error=True,
                text=f"provider prompt={secret}",
                raw={"api_key": secret},
            )
        )
    )

    result = await tool.execute(query="private learner query")

    assert result.success is False
    assert result.error == "MCP web search unavailable"
    assert secret not in (result.error or "")
