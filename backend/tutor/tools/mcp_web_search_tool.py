"""MCPWebSearchTool — delegate ``web_search`` to a configured MCP server.

This is the real implementation of the ``web_search`` tool for providers
that go through MCP. The :class:`tutor.tools.web_search_tool.WebSearchTool`
remains as a low-level DuckDuckGo / SearXNG / Bing dispatcher; this tool
is registered alongside it and gets picked up when
``TUTOR_WEB_SEARCH_PROVIDER=mcp``.

The exact ``arguments`` shape sent to the MCP server depends on which
server is configured. The MiniMax coding-plan MCP accepts at minimum:

.. code-block:: json

    {"query": "<search query>", "count": 5}

We forward those plus a couple of common aliases so other MCP servers
work without per-server glue code.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from tutor.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter, ToolResult
from tutor.services.config.settings import get_settings
from tutor.services.mcp import MCPRegistry, get_mcp_registry


class MCPWebSearchTool(BaseTool):
    """Web search backed by an MCP server (e.g. MiniMax coding-plan MCP)."""

    name = "web_search"
    description = "通过配置的 MCP server 进行 Web 搜索（默认走 MiniMax 编码套餐 MCP）"

    def __init__(self, registry: MCPRegistry | None = None) -> None:
        super().__init__()
        # Allow dependency injection (used by tests) — default to the
        # process-wide singleton so the registry's lazy-loaded config is
        # the source of truth.
        self._registry = registry
        self._server_name: str = ""
        self._tool_name: str = ""
        self._max_results: int = 5
        # Resolved lazily on first ``execute`` so that settings can be
        # edited between construction and first use.
        self._resolved = False

    def _resolve(self) -> tuple[str, str]:
        if self._resolved:
            return self._server_name, self._tool_name
        settings = get_settings()
        self._max_results = settings.web_search_max_results
        self._server_name = settings.web_search_mcp_server
        self._tool_name = settings.web_search_mcp_tool
        self._resolved = True
        return self._server_name, self._tool_name

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="搜索关键词",
                    required=True,
                ),
                ToolParameter(
                    name="max_results",
                    type="number",
                    description="最多返回结果数",
                    required=False,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return ToolResult(
                success=False,
                error="Missing required argument: 'query'",
            )

        max_results = int(kwargs.get("max_results") or self._max_results or 5)
        max_results = max(1, min(max_results, 20))  # clamp 1..20

        server_name, tool_name = self._resolve()
        registry = self._registry or get_mcp_registry()

        # Build the argument payload. The MiniMax MCP accepts ``query`` and
        # ``count``; we also forward common aliases so the same tool works
        # against other MCP servers without per-server glue.
        arguments: dict[str, Any] = {
            "query": query,
            "count": max_results,
            "max_results": max_results,
            "top_k": max_results,
            "limit": max_results,
        }

        try:
            result = await registry.call_tool(server_name, tool_name, arguments)
        except Exception as exc:
            logger.error(
                f"MCPWebSearchTool: call {server_name}.{tool_name} failed: {exc!r}"
            )
            return ToolResult(
                success=False,
                error=f"MCP web search failed: {exc}",
            )

        if result.is_error:
            return ToolResult(
                success=False,
                error=f"MCP web search returned an error: {result.text or result.raw}",
            )

        results = self._parse_content(result.text, result.raw)
        return ToolResult(
            success=True,
            data={
                "query": query,
                "server": server_name,
                "tool": tool_name,
                "results": results,
                "raw_text": result.text,
            },
            metadata={"max_results": max_results},
        )

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_content(
        self, text: str, raw: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Best-effort parsing of MCP ``web_search`` results.

        Different MCP servers return different shapes:

        - MiniMax coding-plan: each ``text`` item is a JSON object with
          ``title`` / ``link`` / ``snippet`` / ``content`` keys. Often the
          top-level text is itself a JSON array of such objects.
        - Other servers: the text may be a JSON array of strings, or a
          newline-separated list, or just free-form text.

        We try JSON first (structurally richest), then fall back to a
        line-based parser that picks up ``http`` URLs as results.
        """
        if text:
            results = self._try_parse_json_results(text)
            if results:
                return results[: self._max_results]
            results = self._parse_text_lines(text)
            if results:
                return results[: self._max_results]
        # Last resort: dump the raw payload so the caller can still see it.
        if isinstance(raw, dict) and raw.get("content"):
            return [{"raw": item} for item in raw["content"]]
        return []

    @staticmethod
    def _try_parse_json_results(text: str) -> list[dict[str, Any]]:
        text = text.strip()
        if not text:
            return []
        # Tolerate a leading ```json fence.
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].lstrip()
            text = text.rstrip("`").strip()
        # The server may emit a JSON object wrapping the list (e.g. {"results": [...]})
        # or the list directly. Try both.
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return []
        candidates: list[Any]
        if isinstance(obj, list):
            candidates = obj
        elif isinstance(obj, dict):
            for key in ("results", "data", "items", "hits", "web_results"):
                if isinstance(obj.get(key), list):
                    candidates = obj[key]
                    break
            else:
                candidates = [obj]
        else:
            return []

        out: list[dict[str, Any]] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            # Normalise common field names to a stable schema.
            title = (
                item.get("title")
                or item.get("name")
                or item.get("heading")
                or ""
            )
            link = (
                item.get("link")
                or item.get("url")
                or item.get("href")
                or item.get("source")
                or ""
            )
            snippet = (
                item.get("snippet")
                or item.get("description")
                or item.get("content")
                or item.get("summary")
                or item.get("text")
                or ""
            )
            if not (title or link or snippet):
                continue
            out.append(
                {
                    "title": title,
                    "link": link,
                    "snippet": snippet if isinstance(snippet, str) else str(snippet),
                    "source": item.get("source", ""),
                }
            )
        return out

    @staticmethod
    def _parse_text_lines(text: str) -> list[dict[str, Any]]:
        """Fallback: scan plain text for URL lines and treat the rest as a snippet."""
        import re

        url_re = re.compile(r"https?://\S+")
        results: list[dict[str, Any]] = []
        current_snippet: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                if current_snippet:
                    current_snippet = []
                continue
            match = url_re.search(line)
            if match:
                # Flush any preceding snippet onto the *previous* result.
                if results and current_snippet:
                    results[-1]["snippet"] = (
                        results[-1].get("snippet", "") + "\n" + "\n".join(current_snippet)
                    ).strip()
                    current_snippet = []
                url = match.group(0).rstrip(").,;\"'>")
                title = line[: match.start()].strip(" -•|·\t") or url
                results.append({"title": title, "link": url, "snippet": ""})
            else:
                current_snippet.append(line)
        if results and current_snippet:
            results[-1]["snippet"] = (
                results[-1].get("snippet", "") + "\n" + "\n".join(current_snippet)
            ).strip()
        return results


__all__ = ["MCPWebSearchTool"]
