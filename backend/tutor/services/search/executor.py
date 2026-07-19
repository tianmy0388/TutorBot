"""Bounded, safe web-search execution shared by permitted capabilities."""

from __future__ import annotations

import asyncio
import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from tutor.services.config.settings import get_settings
from tutor.services.search.policy import SearchPolicy

_TAG_RE = re.compile(r"<[^>]*>")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _safe_text(value: Any, *, limit: int) -> str:
    text = html.unescape(str(value or ""))
    text = _TAG_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    return " ".join(text.split())[:limit]


def _safe_url(value: Any) -> str | None:
    url = _CONTROL_RE.sub("", str(value or "").strip())[:2048]
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


@dataclass(frozen=True, slots=True)
class SearchSource:
    title: str
    url: str
    excerpt: str
    provider: str
    retrieved_at: datetime

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "excerpt": self.excerpt,
            "provider": self.provider,
            "retrieved_at": self.retrieved_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    search_used: bool = False
    sources: tuple[SearchSource, ...] = ()
    unavailable: bool = False
    degradation_code: str | None = None


class SearchExecutor:
    """Apply both gates before resolving and invoking the configured tool."""

    def __init__(
        self,
        *,
        registry: Any | None = None,
        settings_getter: Callable[[], Any] = get_settings,
        timeout_seconds: float = 8.0,
    ) -> None:
        self._registry = registry
        self._settings_getter = settings_getter
        self._timeout_seconds = max(float(timeout_seconds), 0.001)

    async def execute(
        self,
        query: str,
        *,
        conversation_enabled: bool,
    ) -> SearchOutcome:
        settings = self._settings_getter()
        if not SearchPolicy.allowed(
            conversation_enabled=conversation_enabled,
            runtime_enabled=bool(settings.web_search_enabled),
        ):
            return SearchOutcome()

        registry = self._registry
        if registry is None:
            from tutor.runtime.registry.tool_registry import get_tool_registry

            registry = get_tool_registry()
        tool = registry.get("web_search")
        if tool is None:
            return self._unavailable()

        max_results = max(1, min(int(settings.web_search_max_results or 5), 10))
        try:
            result = await asyncio.wait_for(
                tool.execute(query=query, max_results=max_results),
                timeout=self._timeout_seconds,
            )
        except Exception:  # noqa: BLE001 - provider details never leave this unit
            return self._unavailable()

        if not bool(getattr(result, "success", False)):
            return self._unavailable()
        data = getattr(result, "data", None)
        if not isinstance(data, dict):
            return self._unavailable()
        raw_results = data.get("results")
        if not isinstance(raw_results, list) or not raw_results:
            return self._unavailable()

        provider = _safe_text(
            data.get("provider")
            or data.get("server")
            or settings.web_search_provider,
            limit=80,
        )
        retrieved_at = datetime.now(UTC)
        sources: list[SearchSource] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = _safe_url(item.get("url") or item.get("link") or item.get("href"))
            if url is None:
                continue
            title = _safe_text(
                item.get("title") or item.get("name") or urlsplit(url).netloc,
                limit=200,
            )
            excerpt = _safe_text(
                item.get("excerpt")
                or item.get("snippet")
                or item.get("description")
                or item.get("summary")
                or item.get("content"),
                limit=1000,
            )
            item_provider = _safe_text(item.get("provider") or provider, limit=80)
            sources.append(
                SearchSource(
                    title=title,
                    url=url,
                    excerpt=excerpt,
                    provider=item_provider,
                    retrieved_at=retrieved_at,
                )
            )
            if len(sources) >= max_results:
                break
        if not sources:
            return self._unavailable()
        return SearchOutcome(search_used=True, sources=tuple(sources))

    @staticmethod
    def _unavailable() -> SearchOutcome:
        return SearchOutcome(
            unavailable=True,
            degradation_code="WEB_SEARCH_UNAVAILABLE",
        )


__all__ = ["SearchExecutor", "SearchOutcome", "SearchSource"]
