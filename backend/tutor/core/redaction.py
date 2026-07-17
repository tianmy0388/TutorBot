"""Public-boundary redaction and stable failure contracts.

Exception objects belong in the runner's protected diagnostic artifact, never
in ordinary logs, events, reports, or resource payloads.  This module keeps the
public representation deliberately small and recursively scrubs capability
events before persistence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"
TRUNCATED = "[TRUNCATED]"

_SENSITIVE_KEYS = {
    "apikey",
    "authorization",
    "token",
    "secret",
    "password",
    "privatereasoning",
    "hiddentests",
    "sourcecode",
}

_SECRET_TOKEN_RE = re.compile(r"\bSECRET_TOKEN_[A-Za-z0-9_-]+\b", re.IGNORECASE)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+\-/=]+", re.IGNORECASE)
_URL_CREDENTIAL_RE = re.compile(
    r"(?P<scheme>https?://)[^\s/@:]+:[^\s/@]+@", re.IGNORECASE
)
_NAMED_CREDENTIAL_RE = re.compile(
    r"(?P<prefix>\b(?:api[_-]?key|authorization|access[_-]?token|token|secret|password)\s*[:=]\s*)"
    r"(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_KNOWN_KEY_RE = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b"
)


def _normalise_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_sensitive_key(key: Any) -> bool:
    """Return whether a structured field is a protected public surface."""
    normalised = _normalise_key(key)
    return normalised in _SENSITIVE_KEYS or any(
        normalised.endswith(suffix)
        for suffix in ("apikey", "accesstoken", "password", "secret")
    )


def redact_text(value: str, *, max_length: int = 32_000) -> str:
    """Redact credential-shaped substrings without matching educational prose."""
    text = value[:max_length]
    if len(value) > max_length:
        text += TRUNCATED
    text = _SECRET_TOKEN_RE.sub(REDACTED, text)
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = _URL_CREDENTIAL_RE.sub(r"\g<scheme>[REDACTED]@", text)
    text = _NAMED_CREDENTIAL_RE.sub(
        lambda match: f"{match.group('prefix')}{REDACTED}", text
    )
    return _KNOWN_KEY_RE.sub(REDACTED, text)


def redact_sensitive(
    value: Any,
    *,
    max_depth: int = 8,
    max_items: int = 256,
    _depth: int = 0,
) -> Any:
    """Recursively produce a bounded, JSON-friendly redacted projection.

    Values beyond the traversal limits are replaced, never returned raw.
    """
    if _depth >= max_depth:
        return TRUNCATED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes):
        return REDACTED
    if isinstance(value, Mapping):
        result: dict[Any, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                result[TRUNCATED] = TRUNCATED
                break
            result[key] = (
                REDACTED
                if is_sensitive_key(key)
                else redact_sensitive(
                    item,
                    max_depth=max_depth,
                    max_items=max_items,
                    _depth=_depth + 1,
                )
            )
        return result
    if isinstance(value, Sequence):
        return [
            redact_sensitive(
                item,
                max_depth=max_depth,
                max_items=max_items,
                _depth=_depth + 1,
            )
            for item in value[:max_items]
        ] + ([TRUNCATED] if len(value) > max_items else [])
    # Public capability events are expected to be JSON-shaped. Unknown objects
    # could have hostile or secret-bearing __str__/__repr__ methods.
    return REDACTED


def failure_category(exc: BaseException) -> str:
    """Map an exception to a small type-safe category without its message."""
    class_name = type(exc).__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in class_name:
        return "timeout"
    if (
        isinstance(exc, (ConnectionError, OSError))
        or "connection" in class_name
        or "network" in class_name
    ):
        return "connection"
    return "operation"


def public_failure(code: str, message: str, *, retryable: bool) -> dict[str, Any]:
    """Build the sole ordinary/public failure shape used by nested agents."""
    return {"code": code, "message": message, "retryable": retryable}


__all__ = [
    "REDACTED",
    "failure_category",
    "is_sensitive_key",
    "public_failure",
    "redact_sensitive",
    "redact_text",
]
