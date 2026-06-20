"""MCP (Model Context Protocol) client infrastructure.

Implements a minimal stdio JSON-RPC 2.0 client that:

- Spawns an MCP server subprocess (e.g. ``uvx minimax-coding-plan-mcp``).
- Performs the ``initialize`` / ``notifications/initialized`` handshake.
- Discovers the server's tools via ``tools/list``.
- Invokes tools via ``tools/call`` and returns the structured result.

Spec reference: https://modelcontextprotocol.io/specification/2024-11-05

Currently only the stdio transport is implemented; HTTP/SSE can be added
later behind the same :class:`MCPClient` ABC if needed.
"""

from tutor.services.mcp.config import MCPConfigError, MCPServerSpec, load_mcp_config
from tutor.services.mcp.registry import MCPRegistry, get_mcp_registry
from tutor.services.mcp.stdio_client import MCPError, MCPTool, MCPToolResult, StdioMCPClient

__all__ = [
    "MCPConfigError",
    "MCPError",
    "MCPRegistry",
    "MCPServerSpec",
    "MCPTool",
    "MCPToolResult",
    "StdioMCPClient",
    "get_mcp_registry",
    "load_mcp_config",
]
