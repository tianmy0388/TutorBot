"""Markdown media safety helpers for resource-package persistence."""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

_MAX_INLINE_IMAGE_LENGTH = 8_192


def replace_unowned_markdown_images(markdown: str, artifact_names: set[str]) -> str:
    """Replace unresolved relative Markdown images with visible text.

    Generated packages may only retain a relative source when it names an
    artifact owned by that resource. Canonical API paths and HTTP(S) URLs are
    already resolvable without guessing a browser-relative path.
    """

    result: list[str] = []
    cursor = 0
    while cursor < len(markdown):
        start = markdown.find("![", cursor)
        if start < 0:
            result.append(markdown[cursor:])
            break
        result.append(markdown[cursor:start])
        image = _parse_markdown_image(markdown, start)
        if image is None:
            result.append(markdown[start : start + 2])
            cursor = start + 2
            continue
        end, alt, source = image
        original = markdown[start:end]
        if source is None:
            label = alt.strip() or "图片"
            result.append(f"[{label}：图片未提供]")
            break
        if _is_allowed_image_source(source, artifact_names):
            result.append(original)
        else:
            label = alt.strip() or "图片"
            result.append(f"[{label}：图片未提供]")
        cursor = end
    return "".join(result)


def _parse_markdown_image(markdown: str, start: int) -> tuple[int, str, str | None] | None:
    """Parse the bounded inline-image forms accepted by CommonMark.

    This intentionally handles only inline images (not reference links): an
    escaped alt delimiter, an angle-bracket destination, a non-angle
    destination with escaped whitespace/delimiters, and an optional title.
    Invalid markup is returned unchanged.
    """
    limit = min(len(markdown), start + _MAX_INLINE_IMAGE_LENGTH)
    alt_end = _find_unescaped(markdown, "]", start + 2, limit)
    if alt_end < 0 or alt_end + 1 >= len(markdown) or markdown[alt_end + 1] != "(":
        return None
    end = _find_closing_parenthesis(markdown, alt_end + 2, limit)
    if end < 0:
        if limit == len(markdown):
            return None
        # Do not retain the beginning of an oversized candidate: it could be
        # an unsafe absolute URI. The bounded prefix is replaced visibly.
        return limit, markdown[start + 2 : alt_end], None
    body = markdown[alt_end + 2 : end].strip()
    if not body:
        return None
    source = _image_destination(body)
    if source is None:
        return None
    return end + 1, markdown[start + 2 : alt_end], source


def _find_unescaped(text: str, target: str, start: int, limit: int) -> int:
    escaped = False
    for index in range(start, limit):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == target:
            return index
    return -1


def _find_closing_parenthesis(text: str, start: int, limit: int) -> int:
    escaped = False
    quote = ""
    angle = False
    nesting = 0
    for index in range(start, limit):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "<":
            angle = True
            continue
        if char == ">":
            angle = False
            continue
        if angle:
            continue
        if char == "(":
            nesting += 1
            continue
        if char == ")":
            if nesting:
                nesting -= 1
            else:
                return index
    return -1


def _image_destination(body: str) -> str | None:
    if body.startswith("<"):
        end = _find_unescaped(body, ">", 1, len(body))
        return body[1:end] if end >= 0 else None
    escaped = False
    for index, char in enumerate(body):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char.isspace():
            return body[:index]
    return body


def _is_allowed_image_source(source: str, artifact_names: set[str]) -> bool:
    unescaped = _unescape_markdown_destination(source)
    parsed = urlsplit(unescaped)
    if parsed.scheme in {"http", "https"}:
        return True
    if unescaped.startswith("/api/"):
        return True
    if (
        parsed.scheme
        or unescaped.startswith(("/", "\\"))
        or _is_windows_absolute(unescaped)
    ):
        return False
    basename = PurePosixPath(unquote(parsed.path.replace("\\", "/"))).name
    return bool(basename and basename in artifact_names)


def _unescape_markdown_destination(source: str) -> str:
    return source.replace("\\ ", " ").replace("\\(", "(").replace("\\)", ")")


def _is_windows_absolute(source: str) -> bool:
    return len(source) >= 3 and source[0].isalpha() and source[1] == ":" and source[2] in {"/", "\\"}


__all__ = ["replace_unowned_markdown_images"]
