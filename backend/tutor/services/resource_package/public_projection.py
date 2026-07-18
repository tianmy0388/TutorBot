"""Bounded, schema-aware projections for public resource job data."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from tutor.core.redaction import (
    REDACTED,
    TRUNCATED,
    is_credential_shaped,
    is_sensitive_key,
    redact_text,
)
from tutor.services.resource_package.schema import (
    Resource,
    ResourcePackage,
    public_package_dump,
    public_resource_dump,
)

_MAX_STRING_CHARS = 32_000
_MAX_CONTAINER_ITEMS = 256
_MAX_TOTAL_NODES = 10_000
_MAX_CONTAINER_DEPTH = 128
_WINDOWS_OR_UNC_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"[A-Za-z]:[\\/]"
    r"|\\\\[^\\/\s]+[\\/]"
    r")"
)
_UNIX_PATH_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/-])/(?P<path>[A-Za-z0-9._~+@%:=,-]+(?:/[A-Za-z0-9._~+@%:=,-]+)*/?)"
)
_DIAGNOSTIC_PATH_CONTEXT_RE = re.compile(
    r"(?:\bfile\b|\btraceback\b|\bfailed(?:\s+at)?\b|\bexecute(?:d|ing)?\b|\bcwd\b|\bpath\b)\s*(?:[:=]\s*|at\s+)?$",
    re.IGNORECASE,
)
_HOST_SYSTEM_ROOTS = frozenset(
    {
        "bin",
        "boot",
        "data",
        "dev",
        "etc",
        "home",
        "lib",
        "lib64",
        "mnt",
        "opt",
        "private",
        "proc",
        "root",
        "run",
        "sbin",
        "srv",
        "sys",
        "tmp",
        "usr",
        "var",
        "workspace",
        "workspaces",
    }
)


@dataclass
class _ScrubState:
    """Per-projection bounds and cycle tracking."""

    nodes: int = 0
    active_containers: set[int] = field(default_factory=set)


def project_public_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached, bounded public projection of one capability event.

    ``metadata.resource`` is schema-bearing: a validation failure must not
    fall back to generic traversal because it could retain private exercise
    answers or code tests.  The final scrub creates the detached output with
    an iterative traversal, so hostile depth cannot exhaust Python's stack.
    """
    detached = _shallow_mapping(event)
    metadata = detached.get("metadata")
    if isinstance(metadata, Mapping):
        public_metadata = _shallow_mapping(metadata)
        resource = public_metadata.get("resource")
        if isinstance(resource, Mapping):
            public_metadata["resource"] = _validated_resource(resource)
        detached["metadata"] = public_metadata
    return _scrub_known_json(detached)


def project_public_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached, bounded public projection of terminal payload data."""
    return _scrub_known_json(
        _shallow_mapping(payload),
        transform=_project_payload_mapping,
    )


def _project_payload_mapping(value: dict[Any, Any], key: str | None) -> Any:
    """Fail closed for resource/package-shaped terminal payload mappings."""
    if key in {"package", "resource_package"}:
        return _validated_package(value)
    if key == "resource":
        return _validated_resource(value)
    if {"topic", "resources"}.issubset(value):
        return _validated_package(value)
    if {"type", "title"}.issubset(value):
        return _validated_resource(value)
    return value


def _validated_resource(value: Mapping[str, Any]) -> dict[str, Any] | str:
    """Return a browser-safe Resource dump or redact a malformed resource."""
    try:
        return public_resource_dump(Resource.model_validate(value))
    except (RecursionError, TypeError, ValidationError, ValueError):
        return REDACTED


def _validated_package(value: Mapping[str, Any]) -> dict[str, Any] | str:
    """Return a browser-safe package dump or redact a malformed package."""
    try:
        return public_package_dump(ResourcePackage.model_validate(value))
    except (RecursionError, TypeError, ValidationError, ValueError):
        return REDACTED


def _scrub_known_json(
    value: Any,
    *,
    transform: Callable[[dict[Any, Any], str | None], Any] | None = None,
) -> dict[str, Any]:
    """Scrub JSON-shaped data iteratively with explicit public-size bounds.

    Only builtin dictionaries, lists, and tuples are traversed. Unknown
    objects are redacted without formatting them. The iterative work stack is
    intentionally used instead of ``deepcopy`` or recursion so the total-node
    bound protects deeply nested acyclic input as well as cyclic input.
    """
    root: dict[str, Any] = {}
    state = _ScrubState()
    stack: list[
        tuple[dict[str, Any] | list[Any], str | int, Any, str | None, bool, int]
    ] = [
        (root, "value", value, None, True, 0)
    ]

    while stack:
        parent, slot, raw, key, apply_transform, depth = stack.pop()
        if slot == "__remove_active__":
            state.active_containers.remove(raw)
            continue
        if slot == "__append_truncated__":
            parent.append(TRUNCATED)
            continue
        state.nodes += 1
        if state.nodes > _MAX_TOTAL_NODES:
            _assign(parent, slot, TRUNCATED)
            continue
        if raw is None or isinstance(raw, (bool, int, float)):
            _assign(parent, slot, raw)
            continue
        if isinstance(raw, str):
            _assign(parent, slot, _scrub_string(raw))
            continue
        if isinstance(raw, bytes):
            _assign(parent, slot, REDACTED)
            continue
        if isinstance(raw, dict):
            if depth >= _MAX_CONTAINER_DEPTH:
                _assign(parent, slot, TRUNCATED)
                continue
            working: Any = (
                transform(raw, key)
                if transform is not None and apply_transform
                else raw
            )
            if working is not raw:
                stack.append((parent, slot, working, key, False, depth))
                continue
            container_id = id(raw)
            if container_id in state.active_containers:
                _assign(parent, slot, {TRUNCATED: TRUNCATED})
                continue
            target: dict[str, Any] = {}
            _assign(parent, slot, target)
            state.active_containers.add(container_id)
            stack.append((target, "__remove_active__", container_id, None, False, depth))
            entries: list[tuple[Any, Any]] = []
            for index, item in enumerate(raw.items()):
                if index >= _MAX_CONTAINER_ITEMS:
                    target[TRUNCATED] = TRUNCATED
                    break
                entries.append(item)
            for raw_key, item in reversed(entries):
                if not isinstance(raw_key, str):
                    target[REDACTED] = REDACTED
                    continue
                safe_key = _bound_key(raw_key)
                if is_sensitive_key(raw_key) or _is_host_path_key(raw_key):
                    target[safe_key] = REDACTED
                else:
                    stack.append((target, safe_key, item, raw_key, True, depth + 1))
            continue
        if isinstance(raw, (list, tuple)):
            if depth >= _MAX_CONTAINER_DEPTH:
                _assign(parent, slot, TRUNCATED)
                continue
            container_id = id(raw)
            if container_id in state.active_containers:
                _assign(parent, slot, [TRUNCATED])
                continue
            target_list: list[Any] = []
            _assign(parent, slot, target_list)
            state.active_containers.add(container_id)
            stack.append((target_list, "__remove_active__", container_id, None, False, depth))
            if len(raw) > _MAX_CONTAINER_ITEMS:
                stack.append((target_list, "__append_truncated__", None, None, False, depth))
            item_count = min(len(raw), _MAX_CONTAINER_ITEMS)
            for index in range(item_count - 1, -1, -1):
                stack.append((target_list, index, raw[index], None, True, depth + 1))
            continue
        _assign(parent, slot, REDACTED)

    projected = root.get("value")
    return projected if isinstance(projected, dict) else {}


def _assign(parent: dict[str, Any] | list[Any], slot: str | int, value: Any) -> None:
    if isinstance(parent, dict):
        parent[slot] = value
        return
    index = int(slot)
    while len(parent) <= index:
        parent.append(None)
    parent[index] = value


def _shallow_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only the current mapping level; recursive copying is unsafe here."""
    try:
        return dict(value)
    except (RecursionError, TypeError, ValueError):
        return {}


def _scrub_string(value: str) -> str:
    bounded = value[:_MAX_STRING_CHARS]
    if _is_host_path(bounded) or is_credential_shaped(bounded):
        return REDACTED
    return redact_text(bounded, max_length=_MAX_STRING_CHARS) + (
        TRUNCATED if len(value) > _MAX_STRING_CHARS else ""
    )


def _bound_key(value: str) -> str:
    if len(value) <= _MAX_STRING_CHARS:
        return value
    return value[:_MAX_STRING_CHARS] + TRUNCATED


def _is_host_path_key(key: str) -> bool:
    normalised = "".join(character for character in key.lower() if character.isalnum())
    return normalised == "path" or normalised.endswith("path")


def _is_host_path(value: str) -> bool:
    """Detect host paths without mistaking routes and Markdown links for paths."""
    if _WINDOWS_OR_UNC_PATH_RE.search(value) or "file://" in value:
        return True
    for match in _UNIX_PATH_CANDIDATE_RE.finditer(value):
        if _is_markdown_link_destination(value, match.start()):
            continue
        root = match.group("path").split("/", 1)[0]
        if root in _HOST_SYSTEM_ROOTS:
            return True
        context = value[:match.start()].rstrip(" \t\"'")
        if _DIAGNOSTIC_PATH_CONTEXT_RE.search(context):
            return True
    return False


def _is_markdown_link_destination(value: str, path_start: int) -> bool:
    """Keep a relative Markdown destination public even when it resembles a path."""
    return path_start > 1 and value[path_start - 1] == "(" and "](" in value[:path_start]


__all__ = ["project_public_event", "project_public_payload"]
