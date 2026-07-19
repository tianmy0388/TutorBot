"""Pure, bounded redaction for ordinary structured application logs.

This boundary is intentionally stricter than public artifact projection:
submitted/generated source and prompt internals are never useful enough in an
ordinary log to justify retaining their contents.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from tutor.core.redaction import redact_text

_REDACTED = "[REDACTED]"
_TRUNCATED = "[TRUNCATED]"
_MAX_DEPTH_MARKER = "[MAX_DEPTH]"
_RECURSIVE_MARKER = "[RECURSIVE]"
_MAX_DEPTH = 8
_MAX_ITEMS = 128
_MAX_STRING_LENGTH = 4_096

_SECRET_KEYS = {
    "apikey",
    "authorization",
    "password",
    "passwd",
    "secret",
    "credential",
    "cookie",
    "privatekey",
    "clientsecret",
    "accesstoken",
    "refreshtoken",
    "sessiontoken",
    "authtoken",
    "token",
}
_PRIVATE_KEYS = {
    "hiddentests",
    "hiddentestcases",
    "privatereasoning",
    "reasoningcontent",
    "chainofthought",
    "cot",
    "prompt",
    "systemprompt",
    "developerprompt",
    "userprompt",
    "privateprompt",
    "rawprompt",
    "promptmessages",
    "messages",
}
_SOURCE_KEYS = {
    "code",
    "sourcecode",
    "submittedcode",
    "submittedsource",
    "submission",
    "submissionsource",
    "usersource",
    "usercode",
    "generatedcode",
    "manimcode",
    "solutioncode",
    "fullsource",
    "originalcode",
    "currentcode",
    "repairedcode",
    "patchedcode",
    "candidatecode",
    "originalsource",
    "currentsource",
    "rawsource",
    "startercode",
    "pythoncode",
    "javascriptcode",
    "typescriptcode",
    "htmlcode",
    "csscode",
    "sqlcode",
}
_SECRET_SUFFIXES = (
    "apikey",
    "accesstoken",
    "refreshtoken",
    "sessiontoken",
    "authtoken",
    "privatekey",
    "clientsecret",
    "token",
)
_STRICT_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<prefix>\b(?:[a-z0-9]+[-_])*(?:api[-_ ]?key|authorization|"
    r"auth[-_ ]?token|access[-_ ]?token|refresh[-_ ]?token|"
    r"session[-_ ]?token|password|passwd|client[-_ ]?secret|"
    r"private[-_ ]?key|secret|token)\s*[:=]\s*)"
    r"(?:(?P<quote>[\"'])(?P<quoted>.*?)(?P=quote)|(?P<bare>[^\s,;]+))"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")


def _normalise_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


def _is_secret_key(normalised: str) -> bool:
    return normalised in _SECRET_KEYS or normalised.endswith(_SECRET_SUFFIXES)


def _is_private_key(normalised: str) -> bool:
    if normalised in {"prompttokens", "completiontokens", "totaltokens"}:
        return False
    return normalised in _PRIVATE_KEYS or normalised.endswith(
        ("hiddentests", "privatereasoning", "chainofthought", "prompt")
    ) or normalised.startswith("prompt") or normalised in {
        "systemmessages",
        "developermessages",
        "chatmessages",
    }


def _is_source_key(normalised: str) -> bool:
    return normalised in _SOURCE_KEYS


def _source_marker(value: object) -> str:
    if isinstance(value, str):
        return f"[REDACTED:{len(value)} chars]"
    if isinstance(value, bytes):
        return f"[REDACTED:{len(value)} bytes]"
    return _REDACTED


def _safe_text(value: str) -> str:
    bounded = value[:_MAX_STRING_LENGTH]
    if len(value) > _MAX_STRING_LENGTH:
        bounded += _TRUNCATED
    bounded = redact_text(bounded, max_length=max(1, len(bounded)))

    def replace_assignment(match: re.Match[str]) -> str:
        quote = match.group("quote") or ""
        return f"{match.group('prefix')}{quote}{_REDACTED}{quote}"

    bounded = _STRICT_ASSIGNMENT_RE.sub(replace_assignment, bounded)
    return _BEARER_RE.sub(f"Bearer {_REDACTED}", bounded)


def _walk(value: object, *, depth: int, active: set[int]) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[NON_FINITE]"
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, bytes):
        return _safe_text(value.decode("utf-8", errors="replace"))

    if isinstance(value, (Mapping, list, tuple)):
        object_id = id(value)
        if object_id in active:
            return _RECURSIVE_MARKER
        if depth >= _MAX_DEPTH:
            return _MAX_DEPTH_MARKER
        active.add(object_id)
        try:
            if isinstance(value, Mapping):
                result: dict[str, object] = {}
                for index, (raw_key, item) in enumerate(value.items()):
                    if index >= _MAX_ITEMS:
                        result[_TRUNCATED] = _TRUNCATED
                        break
                    key = (
                        raw_key
                        if isinstance(raw_key, str)
                        else f"[UNSUPPORTED_KEY:{type(raw_key).__name__}]"
                    )
                    normalised = _normalise_key(key)
                    if _is_secret_key(normalised) or _is_private_key(normalised):
                        result[key] = _REDACTED
                    elif _is_source_key(normalised):
                        result[key] = _source_marker(item)
                    else:
                        result[key] = _walk(item, depth=depth + 1, active=active)
                return result

            sequence = list(value[:_MAX_ITEMS])
            result = [
                _walk(item, depth=depth + 1, active=active)
                for item in sequence
            ]
            if len(value) > _MAX_ITEMS:
                result.append(_TRUNCATED)
            return result
        finally:
            active.remove(object_id)

    type_name = type(value).__name__[:80]
    return f"[UNSUPPORTED:{type_name}]"


def redact_sensitive(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic JSON-friendly logging projection.

    The input is never mutated. Container depth/item counts and free strings
    are bounded, cycles are replaced, and unsupported values are represented
    without invoking their potentially secret-bearing ``repr``/``str``.
    """

    projected = _walk(value, depth=0, active=set())
    if not isinstance(projected, dict):  # defensive: public API accepts mapping
        return {}
    return projected


__all__ = ["redact_sensitive"]
