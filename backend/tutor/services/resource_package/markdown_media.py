"""Markdown media safety helpers for resource-package persistence."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit


_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[([^\]]*)\]\(([^\s)]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\)"
)


def replace_unowned_markdown_images(markdown: str, artifact_names: set[str]) -> str:
    """Replace unresolved relative Markdown images with visible text.

    Generated packages may only retain a relative source when it names an
    artifact owned by that resource. Canonical API paths and HTTP(S) URLs are
    already resolvable without guessing a browser-relative path.
    """

    def replace(match: re.Match[str]) -> str:
        alt, source = match.groups()
        parsed = urlsplit(source)
        if parsed.scheme in {"http", "https"} or source.startswith("/api/"):
            return match.group(0)
        basename = PurePosixPath(unquote(parsed.path)).name
        if basename and basename in artifact_names:
            return match.group(0)
        label = alt.strip() or "图片"
        return f"[{label}：图片未提供]"

    return _MARKDOWN_IMAGE_RE.sub(replace, markdown)


__all__ = ["replace_unowned_markdown_images"]
