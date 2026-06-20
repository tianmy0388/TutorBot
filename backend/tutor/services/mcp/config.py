"""Load and validate ``.mcp.json`` configuration.

The on-disk format mirrors the Claude Code / Cursor convention so users
can copy their MCP config between tools without modification:

.. code-block:: json

    {
      "mcpServers": {
        "MiniMax": {
          "command": "uvx",
          "args": ["minimax-coding-plan-mcp"],
          "env": {
            "MINIMAX_API_KEY": "${MINIMAX_API_KEY}",
            "MINIMAX_API_HOST": "https://api.minimaxi.com"
          }
        }
      }
    }

Environment-variable references of the form ``${VAR}`` (and bare ``$VAR``)
are substituted from ``os.environ`` at load time.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class MCPConfigError(ValueError):
    """Raised when the MCP config file is malformed or required fields are missing."""


@dataclass
class MCPServerSpec:
    """One server entry from ``.mcp.json``."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"command": self.command, "args": list(self.args)}
        if self.env:
            out["env"] = dict(self.env)
        if self.cwd:
            out["cwd"] = self.cwd
        return out


_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` / ``$VAR`` references inside ``value``."""
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            var = m.group(1) or m.group(2)
            return os.environ.get(var, "")
        return _ENV_REF_RE.sub(repl, value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def load_mcp_config(path: str | Path) -> dict[str, MCPServerSpec]:
    """Load and parse an MCP config file.

    Returns a mapping ``server_name -> MCPServerSpec``. Raises
    :class:`MCPConfigError` on any structural problem.
    """
    path = Path(path)
    if not path.exists():
        raise MCPConfigError(f"MCP config file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MCPConfigError(f"Invalid JSON in {path}: {exc}") from exc

    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        raise MCPConfigError(
            f"Missing or invalid 'mcpServers' object in {path}. "
            f"Expected {{\"mcpServers\": {{\"<name>\": {{...}}}}}}"
        )

    out: dict[str, MCPServerSpec] = {}
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            raise MCPConfigError(
                f"Server {name!r}: expected an object, got {type(spec).__name__}"
            )
        command = spec.get("command")
        if not command or not isinstance(command, str):
            raise MCPConfigError(
                f"Server {name!r}: missing or invalid 'command' (must be a non-empty string)"
            )
        args_raw = spec.get("args") or []
        if not isinstance(args_raw, list):
            raise MCPConfigError(
                f"Server {name!r}: 'args' must be a list of strings"
            )
        env_raw = spec.get("env") or {}
        if not isinstance(env_raw, dict):
            raise MCPConfigError(
                f"Server {name!r}: 'env' must be an object of string -> string"
            )
        cwd = spec.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise MCPConfigError(f"Server {name!r}: 'cwd' must be a string")

        out[name] = MCPServerSpec(
            name=name,
            command=command,
            args=[_expand_env(a) for a in args_raw],
            env={k: _expand_env(v) for k, v in env_raw.items()},
            cwd=cwd,
        )
    return out


__all__ = ["MCPConfigError", "MCPServerSpec", "load_mcp_config"]
