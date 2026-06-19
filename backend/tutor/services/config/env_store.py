"""``EnvStore`` — ordered reader/writer for ``.env``.

Design inspired by DeepTutor's :class:`EnvStore`. We preserve the order
of keys, treat tri-state booleans carefully, and round-trip comments.

Note: For most runtime reads, prefer :class:`tutor.services.config.settings.Settings`
which uses pydantic-settings. ``EnvStore`` is for tooling that needs to
inspect or update the ``.env`` file (e.g. CLI ``tutor config set``).
"""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path
from typing import Iterable


_TRUTHY = {"1", "true", "yes", "on", "y", "t"}
_FALSY = {"0", "false", "no", "off", "n", "f", ""}


def _parse_bool(value: str | bool | None) -> bool | None:
    """Parse a tri-state boolean from string. Returns ``None`` for unset."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in _TRUTHY:
        return True
    if s in _FALSY:
        return False
    return None


class EnvStore:
    """Lightweight wrapper around the project ``.env`` file."""

    def __init__(self, path: str | Path = ".env") -> None:
        self.path = Path(path)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load(self) -> "OrderedDict[str, str]":
        """Load all key=value pairs from ``.env`` preserving order."""
        env: OrderedDict[str, str] = OrderedDict()
        if not self.path.exists():
            return env
        with self.path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                env[key] = value
        return env

    def get(self, key: str, default: str = "") -> str:
        """Get a value (process env wins over .env)."""
        proc = os.environ.get(key)
        if proc is not None:
            return proc
        return self.load().get(key, default)

    def get_bool(self, key: str, default: bool | None = None) -> bool | None:
        """Tri-state boolean lookup."""
        v = self.get(key, "")
        parsed = _parse_bool(v)
        if parsed is None:
            return default
        return parsed

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write(self, values: dict[str, str]) -> None:
        """Update ``.env`` with the supplied values, preserving existing keys.

        - If a key exists, its value is updated.
        - If a key does not exist, it is appended at the end.
        - Existing comments and blank lines are preserved.
        """
        env = self.load()
        for k, v in values.items():
            env[k] = v
        self._write_ordered(env.keys(), lambda k: env[k])

    def _write_ordered(self, keys: Iterable[str], lookup) -> None:
        lines: list[str] = []
        if self.path.exists():
            seen: set[str] = set()
            with self.path.open("r", encoding="utf-8") as fh:
                for raw in fh:
                    stripped = raw.strip()
                    if not stripped or stripped.startswith("#"):
                        lines.append(raw.rstrip("\n"))
                        continue
                    key = stripped.split("=", 1)[0].strip()
                    if key in lookup and key not in seen:
                        lines.append(f"{key}={lookup(key)}")
                        seen.add(key)
                    elif key not in lookup:
                        lines.append(raw.rstrip("\n"))
        for k in keys:
            if k not in lines and k not in locals().get("seen", set()):
                lines.append(f"{k}={lookup(k)}")
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")


__all__ = ["EnvStore"]
