"""PaperSearchToolWrapper — arXiv search tool (placeholder)."""

from __future__ import annotations

from typing import Any

from tutor.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter, ToolResult


class PaperSearchTool(BaseTool):
    """Search arXiv for academic papers."""

    name = "paper_search"
    description = "在 arXiv 上搜索学术论文"

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="论文检索词",
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
        return ToolResult(
            success=True,
            data={"papers": [], "message": "PaperSearchTool 占位实现"},
        )


__all__ = ["PaperSearchTool"]
