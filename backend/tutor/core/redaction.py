"""Public-boundary redaction and stable failure contracts.

Exception objects belong in the runner's protected diagnostic artifact, never
in ordinary logs, events, reports, or resource payloads.  This module keeps the
public representation deliberately small and recursively scrubs capability
events before persistence.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from math import log2
from typing import Any

REDACTED = "[REDACTED]"
TRUNCATED = "[TRUNCATED]"

_STRICT_SENSITIVE_KEYS = {
    "apikey",
    "authorization",
    "secret",
    "password",
    "passphrase",
    "privatekey",
    "clientsecret",
    "refreshtoken",
    "sessiontoken",
    "authtoken",
    "accesstoken",
    "secretkey",
    "accesskey",
    "awsaccesskeyid",
    "awssecretaccesskey",
    "cookie",
    "cookies",
    "setcookie",
    "cookieheader",
    "sessioncookie",
    "authcookie",
    "privatereasoning",
    "hiddentests",
}
_CONTENT_KEYS = {"sourcecode", "code", "manimcode", "content"}
_AMBIGUOUS_CREDENTIAL_KEYS = {"token"}

_SECRET_TOKEN_RE = re.compile(r"\bSECRET_TOKEN_[A-Za-z0-9_-]+\b", re.IGNORECASE)
_AUTH_RE = re.compile(
    r"\b(?P<scheme>Bearer|Basic)\s+(?P<credential>[^\s,;]+)", re.IGNORECASE
)
_URL_CREDENTIAL_RE = re.compile(
    r"(?P<scheme>https?://)[^\s/@:]+:[^\s/@]+@", re.IGNORECASE
)
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)
_ASSIGNMENT_RE = re.compile(
    r"(?P<prefix>\b(?:api[_-]?key|authorization|auth[_-]?token|access[_-]?token|"
    r"refresh[_-]?token|session[_-]?token|token|client[_-]?secret|secret[_-]?key|"
    r"private[_-]?key|password)\s*[:=]\s*)"
    r"(?:(?P<quote>[\"'])(?P<quoted>.*?)(?P=quote)|(?P<bare>[^\s,;#]+))",
    re.IGNORECASE,
)
_KNOWN_KEY_RE = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|api[_-]?key[_-][A-Za-z0-9_-]{12,}|"
    r"AIza[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{20,})\b",
    re.IGNORECASE,
)
_OPAQUE_CANDIDATE_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9_~+/=-]{32,}(?![A-Za-z0-9_])")


def _normalise_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_sensitive_key(key: Any) -> bool:
    """Return whether a structured field is unambiguously confidential."""
    normalised = _normalise_key(key)
    return normalised in _STRICT_SENSITIVE_KEYS or any(
        normalised.endswith(suffix)
        for suffix in (
            "apikey",
            "privatekey",
            "accesstoken",
            "refreshtoken",
            "sessiontoken",
            "authtoken",
            "password",
            "clientsecret",
            "secretkey",
            "accesskey",
            "accesskeyid",
            "authorization",
        )
    )


def _entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * log2(count / length) for count in counts.values())


def is_credential_shaped(value: Any) -> bool:
    """Detect high-confidence credential values without relying on field names."""
    if not isinstance(value, str):
        return False
    candidate = value.strip().strip("\"'")
    if not candidate:
        return False
    if _SECRET_TOKEN_RE.search(candidate):
        return True
    if _PEM_RE.search(candidate) or _JWT_RE.fullmatch(candidate):
        return True
    if _AUTH_RE.fullmatch(candidate) or _KNOWN_KEY_RE.fullmatch(candidate):
        return True
    if not re.fullmatch(r"[A-Za-z0-9_~+/=-]{32,}", candidate):
        return False
    categories = sum(
        bool(re.search(pattern, candidate))
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[_~+/=-]")
    )
    entropy = _entropy(candidate)
    if len(candidate) >= 40 and re.fullmatch(r"[A-Fa-f0-9]+", candidate):
        return entropy >= 3.0
    return (categories >= 3 and entropy >= 3.5) or (
        categories >= 2 and entropy >= 4.0
    )


def _redact_assignment(match: re.Match[str]) -> str:
    value = match.group("quoted") if match.group("quote") else match.group("bare")
    if not is_credential_shaped(value):
        return match.group(0)
    quote = match.group("quote") or ""
    return f"{match.group('prefix')}{quote}{REDACTED}{quote}"


def redact_text(value: str, *, max_length: int = 32_000) -> str:
    """Scrub credentials while preserving prose and executable source code."""
    text = value[:max_length]
    if len(value) > max_length:
        text += TRUNCATED
    text = _PEM_RE.sub(REDACTED, text)
    text = _SECRET_TOKEN_RE.sub(REDACTED, text)
    text = _AUTH_RE.sub(lambda match: f"{match.group('scheme')} {REDACTED}", text)
    text = _URL_CREDENTIAL_RE.sub(r"\g<scheme>[REDACTED]@", text)
    text = _JWT_RE.sub(REDACTED, text)
    text = _KNOWN_KEY_RE.sub(REDACTED, text)
    text = _ASSIGNMENT_RE.sub(_redact_assignment, text)
    return _OPAQUE_CANDIDATE_RE.sub(
        lambda match: REDACTED if is_credential_shaped(match.group(0)) else match.group(0),
        text,
    )


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
            normalised_key = _normalise_key(key)
            if is_sensitive_key(key):
                result[key] = REDACTED
            elif (
                normalised_key in _CONTENT_KEYS
                or normalised_key in _AMBIGUOUS_CREDENTIAL_KEYS
            ) and isinstance(item, str):
                result[key] = redact_text(item)
            else:
                result[key] = redact_sensitive(
                    item,
                    max_depth=max_depth,
                    max_items=max_items,
                    _depth=_depth + 1,
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
    "is_credential_shaped",
    "is_sensitive_key",
    "public_failure",
    "redact_sensitive",
    "redact_text",
]
