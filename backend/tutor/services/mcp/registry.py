"""Global registry of MCP server sessions.

Servers are loaded from a single config file (default ``./.mcp.json``)
and started lazily on first use. A process-wide singleton makes them
shareable across requests without re-spawning subprocesses.
"""

from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

from tutor.services.config.settings import Settings, get_settings
from tutor.services.mcp.config import (
    MCPConfigError,
    MCPServerSpec,
    load_mcp_config,
)
from tutor.services.mcp.stdio_client import MCPError, StdioMCPClient


class MCPRegistry:
    """Process-wide registry of :class:`StdioMCPClient` instances."""

    def __init__(self) -> None:
        self._configs: dict[str, MCPServerSpec] = {}
        self._sessions: dict[str, StdioMCPClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._config_path: Path | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self, path: str | Path | None = None) -> None:
        """Reload the MCP config from ``path`` (or the default location).

        When ``path`` is None, the registry looks for ``.mcp.json`` in:

        1. ``$TUTOR_MCP_CONFIG_PATH`` (if set via env)
        2. ``./.mcp.json`` (current working directory)
        3. ``../.mcp.json`` (parent — common when running from ``backend/``)
        4. Walks up to 4 parents looking for ``.mcp.json``

        Safe to call multiple times — the on-disk file is the source of truth
        each time. Existing live sessions are NOT torn down (call
        :meth:`stop_all` first if you need a clean slate).
        """
        if path is None:
            path = self._find_default_config()
        path = Path(path) if path else None
        if path is None or not path.exists():
            logger.debug(
                f"MCPRegistry: no .mcp.json found (searched cwd + parents); "
                f"registry will be empty"
            )
            self._configs = {}
            self._config_path = path
            return
        try:
            configs = load_mcp_config(path)
        except MCPConfigError as exc:
            logger.error(f"MCPRegistry: failed to load {path}: {exc}")
            self._configs = {}
            self._config_path = path
            return
        self._configs = configs
        self._config_path = path
        logger.info(
            f"MCPRegistry: loaded {len(configs)} server(s) from {path}: "
            f"{list(configs.keys())}"
        )

    @staticmethod
    def _find_default_config() -> Path | None:
        """Locate the MCP config file.

        Resolution order:

        1. ``$TUTOR_MCP_CONFIG_PATH`` (must be non-empty AND point to a file)
        2. ``./.mcp.json``
        3. Walk up to 4 parents looking for ``.mcp.json``

        Returns the first path that exists, or ``None`` if nothing is found.
        """
        env_path = os.environ.get("TUTOR_MCP_CONFIG_PATH", "").strip()
        if env_path:
            p = Path(env_path)
            if p.is_file():
                return p
        # Look in cwd and walk up to 4 parents.
        cwd = Path.cwd()
        for ancestor in [cwd, *cwd.parents][:5]:
            candidate = ancestor / ".mcp.json"
            if candidate.is_file():
                return candidate
        return None

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    def list_servers(self) -> list[str]:
        return sorted(self._configs.keys())

    def get_spec(self, name: str) -> MCPServerSpec | None:
        return self._configs.get(name)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def get_session(self, name: str) -> StdioMCPClient:
        """Return a started session for ``name``, starting it if needed."""
        if name not in self._configs:
            raise MCPError(
                f"MCP server {name!r} is not configured. "
                f"Known: {self.list_servers()}"
            )

        session = self._sessions.get(name)
        if session and session.started:
            return session

        # Coalesce concurrent first-starts for the same server.
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            # Re-check inside the lock — another coroutine may have started it.
            session = self._sessions.get(name)
            if session and session.started:
                return session
            spec = self._configs[name]
            session = StdioMCPClient(spec)
            try:
                await session.start()
            except Exception:
                # Don't keep failed sessions around — let the caller decide
                # whether to retry by re-calling get_session.
                self._sessions.pop(name, None)
                raise
            self._sessions[name] = session
            return session

    async def stop(self, name: str) -> None:
        session = self._sessions.pop(name, None)
        if session:
            await session.stop()

    async def stop_all(self) -> None:
        for name in list(self._sessions.keys()):
            await self.stop(name)
        self._locks.clear()

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Shortcut: get the server's session and call ``tool`` on it."""
        session = await self.get_session(server)
        return await session.call_tool(tool, arguments or {})

    async def list_tools(self, server: str, *, refresh: bool = False) -> list[Any]:
        session = await self.get_session(server)
        return await session.list_tools(refresh=refresh)


@lru_cache(maxsize=1)
def get_mcp_registry() -> MCPRegistry:
    """Process-wide :class:`MCPRegistry` (cached)."""
    reg = MCPRegistry()
    # ``settings.mcp_config_path`` wins if set (via .env); otherwise
    # ``configure(None)`` falls back to ``$TUTOR_MCP_CONFIG_PATH`` /
    # ``./.mcp.json`` / parent dirs.
    try:
        from tutor.services.config.settings import get_settings

        explicit = get_settings().mcp_config_path
    except Exception:  # pragma: no cover — settings not yet loadable
        explicit = None
    reg.configure(explicit)
    return reg


def reset_mcp_registry() -> None:
    """Clear the cached registry. Used by tests and after editing the config."""
    get_mcp_registry.cache_clear()


__all__ = ["MCPRegistry", "get_mcp_registry", "reset_mcp_registry"]
