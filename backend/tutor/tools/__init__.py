"""Tool implementations (Level-1 atomic capabilities).

- RAGTool — knowledge base retrieval
- WebSearchTool — web search (DuckDuckGo / SearXNG / Bing)
- MCPWebSearchTool — web search backed by an MCP server (e.g. MiniMax)
- CodeExecutionTool — sandboxed Python execution
- PaperSearchTool — arXiv search
"""

from tutor.tools.rag_tool import RAGTool
from tutor.tools.web_search_tool import WebSearchTool
from tutor.tools.mcp_web_search_tool import MCPWebSearchTool
from tutor.tools.code_execution_tool import CodeExecutionTool
from tutor.tools.paper_search_tool import PaperSearchTool

__all__ = [
    "RAGTool",
    "WebSearchTool",
    "MCPWebSearchTool",
    "CodeExecutionTool",
    "PaperSearchTool",
]
