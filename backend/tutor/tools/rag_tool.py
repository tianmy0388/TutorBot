"""RAGTool — knowledge base retrieval tool (placeholder).

Full LlamaIndex integration lands in Phase 2.
"""

from __future__ import annotations

from typing import Any

from tutor.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter, ToolResult


class RAGTool(BaseTool):
    """Retrieve relevant passages from the active knowledge base."""

    name = "rag"
    description = "从知识库中检索与查询相关的文档片段"

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="检索查询字符串",
                    required=True,
                ),
                ToolParameter(
                    name="kb_name",
                    type="string",
                    description="知识库名称（默认: ai_introduction）",
                    required=False,
                ),
                ToolParameter(
                    name="top_k",
                    type="number",
                    description="返回结果数量",
                    required=False,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query", "")
        return ToolResult(
            success=True,
            data={
                "query": query,
                "chunks": [],
                "message": "RAGTool 占位实现 — LlamaIndex 集成在 Phase 2",
            },
        )


__all__ = ["RAGTool"]
