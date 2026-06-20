"""Stdio transport for the Model Context Protocol.

Spawns a child process (e.g. ``uvx minimax-coding-plan-mcp``) and
exchanges newline-delimited JSON-RPC 2.0 messages on its stdin/stdout.

Only the subset of MCP we actually use is implemented:

- ``initialize`` / ``notifications/initialized`` handshake
- ``tools/list``
- ``tools/call``

If you need server-initiated requests (sampling, elicitation, roots)
or progress notifications, extend :meth:`StdioMCPClient._dispatch`.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from tutor.services.mcp.config import MCPServerSpec


class MCPError(RuntimeError):
    """Raised when an MCP server returns an error or the protocol is violated."""


@dataclass
class MCPTool:
    """A single tool advertised by an MCP server."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MCPTool":
        schema = raw.get("inputSchema") or raw.get("input_schema") or {}
        return cls(
            name=raw.get("name", ""),
            description=raw.get("description", "") or "",
            input_schema=schema,
        )


@dataclass
class MCPToolResult:
    """Result of a ``tools/call`` invocation."""

    content: list[dict[str, Any]] = field(default_factory=list)
    is_error: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Concatenate all ``text`` content items — the common case for web_search."""
        parts: list[str] = []
        for item in self.content:
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)


class StdioMCPClient:
    """A single stdio MCP server session.

    Use one instance per server per process; the underlying subprocess
    is created lazily on :meth:`start` and torn down on :meth:`stop`.
    """

    # The MCP protocol version this client announces. Kept as a constant
    # so server logs can compare against expected.
    PROTOCOL_VERSION = "2024-11-05"
    CLIENT_NAME = "tutor"
    CLIENT_VERSION = "0.1.0"

    def __init__(self, spec: MCPServerSpec, *, request_timeout: float = 60.0) -> None:
        self._spec = spec
        self._request_timeout = request_timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._read_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._started = False
        self._server_info: dict[str, Any] = {}
        self._server_capabilities: dict[str, Any] = {}
        self._tools: list[MCPTool] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._spec.name

    @property
    def started(self) -> bool:
        return self._started

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info)

    @property
    def tools(self) -> list[MCPTool]:
        return list(self._tools)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn the subprocess, handshake, and cache the tool list."""
        if self._started:
            return

        command_path = self._resolve_command(self._spec.command)
        if not command_path:
            raise MCPError(
                f"Command not found on PATH: {self._spec.command!r}. "
                f"Install it or adjust the MCP config."
            )

        merged_env = {**os.environ, **self._spec.env}
        # Strip empties — some servers treat "" env vars as explicit overrides.
        for k, v in list(merged_env.items()):
            if v == "":
                merged_env.pop(k, None)

        logger.info(
            f"MCP[{self.name}]: starting {command_path} {self._spec.args!r}"
        )
        try:
            self._proc = await asyncio.create_subprocess_exec(
                command_path,
                *self._spec.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=merged_env,
                cwd=self._spec.cwd,
            )
        except FileNotFoundError as exc:
            raise MCPError(
                f"Failed to spawn MCP server {self.name!r}: {exc}"
            ) from exc
        except PermissionError as exc:
            raise MCPError(
                f"Permission denied spawning MCP server {self.name!r}: {exc}"
            ) from exc

        self._read_task = asyncio.create_task(
            self._read_loop(), name=f"mcp-{self.name}-read"
        )
        self._stderr_task = asyncio.create_task(
            self._stderr_loop(), name=f"mcp-{self.name}-stderr"
        )

        try:
            init_result = await self._request(
                "initialize",
                {
                    "protocolVersion": self.PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": self.CLIENT_NAME,
                        "version": self.CLIENT_VERSION,
                    },
                },
            )
        except Exception:
            await self._kill()
            raise

        self._server_info = init_result.get("serverInfo", {}) or {}
        self._server_capabilities = init_result.get("capabilities", {}) or {}

        # Per the spec, the client must follow ``initialize`` with an
        # ``initialized`` *notification* (no ``id`` field).
        await self._notify("notifications/initialized", {})

        # Cache the tool list eagerly — most clients do this once at
        # startup and only refresh on a ``tools/list_changed`` notification.
        try:
            tools_result = await self._request("tools/list", {})
        except MCPError:
            # Some servers may not support ``tools`` capability — that's
            # acceptable; they can still be used for resources / prompts.
            tools_result = {"tools": []}

        self._tools = [MCPTool.from_dict(t) for t in (tools_result.get("tools") or [])]
        self._started = True
        logger.info(
            f"MCP[{self.name}]: ready — {len(self._tools)} tool(s): "
            f"{[t.name for t in self._tools]}"
        )

    async def stop(self) -> None:
        """Politely terminate the subprocess and cancel background tasks."""
        if not self._proc:
            return
        await self._kill()
        self._started = False
        self._tools = []

    async def _kill(self) -> None:
        proc = self._proc
        self._proc = None
        for task in (self._read_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
        for task in (self._read_task, self._stderr_task):
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._read_task = None
        self._stderr_task = None
        # Fail any in-flight requests so callers don't hang.
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(MCPError(f"MCP server {self.name!r} is shutting down"))
        self._pending.clear()
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await proc.wait()
                except Exception:
                    pass
            except ProcessLookupError:
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_tools(self, *, refresh: bool = False) -> list[MCPTool]:
        """Return cached tools; pass ``refresh=True`` to re-query the server."""
        if not self._started:
            raise MCPError(f"MCP server {self.name!r} is not started")
        if refresh:
            result = await self._request("tools/list", {})
            self._tools = [MCPTool.from_dict(t) for t in (result.get("tools") or [])]
        return list(self._tools)

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> MCPToolResult:
        """Invoke ``name`` on the server with the given arguments."""
        if not self._started:
            raise MCPError(f"MCP server {self.name!r} is not started")
        result = await self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        content = result.get("content") or []
        return MCPToolResult(
            content=content if isinstance(content, list) else [],
            is_error=bool(result.get("isError", False)),
            raw=result,
        )

    # ------------------------------------------------------------------
    # JSON-RPC plumbing
    # ------------------------------------------------------------------

    async def _request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self._proc or not self._proc.stdin:
            raise MCPError(f"MCP server {self.name!r} is not running")
        req_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = fut

        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        line = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError) as exc:
            self._pending.pop(req_id, None)
            raise MCPError(
                f"MCP server {self.name!r} closed stdin before sending response: {exc}"
            ) from exc

        try:
            return await asyncio.wait_for(fut, timeout=self._request_timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise MCPError(
                f"MCP server {self.name!r}: timeout waiting for {method!r}"
            ) from exc

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self._proc or not self._proc.stdin:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        line = (json.dumps(msg) + "\n").encode("utf-8")
        try:
            self._proc.stdin.write(line)
            await self._proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError) as exc:
            raise MCPError(
                f"MCP server {self.name!r} closed stdin while sending {method!r}: {exc}"
            ) from exc

    async def _read_loop(self) -> None:
        """Drain stdout, dispatching each JSON-RPC frame."""
        assert self._proc and self._proc.stdout
        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    # EOF — server closed its stdout.
                    logger.warning(f"MCP[{self.name}]: server closed stdout (EOF)")
                    self._fail_all_pending(
                        MCPError(f"MCP server {self.name!r} closed stdout unexpectedly")
                    )
                    return
                try:
                    text = raw.decode("utf-8").rstrip("\r\n")
                except UnicodeDecodeError:
                    logger.warning(f"MCP[{self.name}]: non-utf8 line, skipping")
                    continue
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    logger.warning(
                        f"MCP[{self.name}]: ignoring malformed JSON line: {text[:200]!r}"
                    )
                    continue
                await self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"MCP[{self.name}]: read loop crashed: {exc!r}")
            self._fail_all_pending(exc)

    async def _stderr_loop(self) -> None:
        """Forward the server's stderr to our logger so users can debug it."""
        assert self._proc and self._proc.stderr
        try:
            while True:
                raw = await self._proc.stderr.readline()
                if not raw:
                    return
                try:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    continue
                if line:
                    logger.debug(f"MCP[{self.name}] stderr: {line}")
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        """Route a JSON-RPC message to the right handler."""
        if not isinstance(msg, dict):
            return
        # Response (with id and result/error).
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut is None or fut.done():
                return
            if "error" in msg and msg["error"] is not None:
                err = msg["error"]
                if isinstance(err, dict):
                    msg_text = err.get("message") or json.dumps(err)
                else:
                    msg_text = str(err)
                fut.set_exception(MCPError(f"MCP[{self.name}] {msg_text}"))
            else:
                fut.set_result(msg["result"] or {})
            return

        # Notification (method, no id) — e.g. tools/list_changed.
        if "method" in msg and "id" not in msg:
            method = msg["method"]
            if method == "notifications/tools/list_changed":
                try:
                    refreshed = await self._request("tools/list", {})
                    self._tools = [
                        MCPTool.from_dict(t) for t in (refreshed.get("tools") or [])
                    ]
                    logger.info(
                        f"MCP[{self.name}]: tool list refreshed — "
                        f"{[t.name for t in self._tools]}"
                    )
                except Exception as exc:
                    logger.warning(
                        f"MCP[{self.name}]: failed to refresh tool list: {exc!r}"
                    )
            return

        # Server-initiated request — we don't currently handle any.
        if "method" in msg and "id" in msg:
            logger.debug(
                f"MCP[{self.name}]: ignoring server request {msg.get('method')!r}"
            )

    def _fail_all_pending(self, exc: BaseException) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_command(command: str) -> str | None:
        """Resolve a command the way shells do: bare names go through PATH,
        absolute/relative paths are returned verbatim if executable."""
        if os.sep in command or (os.altsep and os.altsep in command):
            return command if os.access(command, os.X_OK) else None
        return shutil.which(command)


__all__ = ["MCPError", "MCPTool", "MCPToolResult", "StdioMCPClient"]
