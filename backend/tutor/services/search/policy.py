"""Authorization policy for web-search execution."""

from __future__ import annotations


class SearchPolicy:
    """Combine the immutable conversation snapshot with the runtime gate."""

    @staticmethod
    def allowed(conversation_enabled: bool, runtime_enabled: bool) -> bool:
        return bool(conversation_enabled and runtime_enabled)


__all__ = ["SearchPolicy"]
