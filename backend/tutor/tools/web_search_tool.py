"""WebSearchTool — web search tool (placeholder).

Full integration (DuckDuckGo / SearXNG) lands in Phase 2.
"""

from __future__ import annotations

from typing import Any

from tutor.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter, ToolResult


class WebSearchTool(BaseTool):
    """Search the web for current information."""

    name = "web_search"
    description = "在 Web 上搜索信息（默认使用 DuckDuckGo）"

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
        query = kwargs.get("query", "")
        return ToolResult(
            success=True,
            data={"query": query, "results": [], "message": "WebSearchTool 占位实现"},
        )


__all__ = ["WebSearchTool"]
