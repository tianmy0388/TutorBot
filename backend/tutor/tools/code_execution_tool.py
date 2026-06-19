"""CodeExecutionTool — sandboxed Python execution (placeholder)."""

from __future__ import annotations

from typing import Any

from tutor.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter, ToolResult


class CodeExecutionTool(BaseTool):
    """Execute Python code in a sandbox and return the result."""

    name = "code_execution"
    description = "在沙箱中执行 Python 代码并返回输出"

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="code",
                    type="string",
                    description="Python 源代码",
                    required=True,
                ),
                ToolParameter(
                    name="timeout",
                    type="number",
                    description="超时（秒）",
                    required=False,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(
            success=True,
            data={"stdout": "", "stderr": "", "message": "CodeExecutionTool 占位实现"},
        )


__all__ = ["CodeExecutionTool"]
