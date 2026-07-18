"""Bounded, schema-aware projections for public resource job data.

Generic public-event redaction intentionally has a shallow traversal limit.
That is appropriate for untrusted diagnostic structures, but valid resource
schemas can nest exercise options beyond it.  This module validates known
resource shapes first, then applies bounded credential scrubbing without a
schema-depth cutoff.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
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


@dataclass
class _ScrubState:
    """Per-projection bounds and cycle tracking."""

    nodes: int = 0
    active_containers: set[int] = field(default_factory=set)


def project_public_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached public projection of one capability event.

    A resource event has one well-known schema-bearing location.  Validate it
    before scrubbing so valid exercise options are not mistaken for hostile
    deep data; invalid or unknown event structures remain generically bounded.
    """
    detached = copy.deepcopy(dict(event))
    metadata = detached.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("resource"), dict):
        projected_resource = _public_resource(metadata["resource"])
        if projected_resource is not None:
            metadata["resource"] = projected_resource
    projected = _scrub_known_json(detached)
    return projected if isinstance(projected, dict) else {}


def project_public_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached public projection of terminal capability payloads.

    Terminal payloads may contain a resource or package below arbitrary
    capability-owned wrapper keys.  Known shapes are projected at every such
    location; unrelated payload values retain the existing bounded public
    treatment.
    """
    detached = copy.deepcopy(dict(payload))
    projected = _project_payload_shapes(detached, _ScrubState())
    scrubbed = _scrub_known_json(projected)
    return scrubbed if isinstance(scrubbed, dict) else {}


def _project_payload_shapes(value: Any, state: _ScrubState) -> Any:
    """Replace validated resource/package mappings without trusting unknowns."""
    state.nodes += 1
    if state.nodes > _MAX_TOTAL_NODES:
        return TRUNCATED
    if isinstance(value, dict):
        container_id = id(value)
        if container_id in state.active_containers:
            return {TRUNCATED: TRUNCATED}
        state.active_containers.add(container_id)
        try:
            package = _public_package(value)
            if package is not None:
                return package
            resource = _public_resource(value)
            if resource is not None:
                return resource
            result: dict[Any, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= _MAX_CONTAINER_ITEMS:
                    result[TRUNCATED] = TRUNCATED
                    break
                result[key] = _project_payload_shapes(item, state)
            return result
        finally:
            state.active_containers.remove(container_id)
    if isinstance(value, list):
        container_id = id(value)
        if container_id in state.active_containers:
            return [TRUNCATED]
        state.active_containers.add(container_id)
        try:
            result = [
                _project_payload_shapes(item, state)
                for index, item in enumerate(value)
                if index < _MAX_CONTAINER_ITEMS
            ]
            if len(value) > _MAX_CONTAINER_ITEMS:
                result.append(TRUNCATED)
            return result
        finally:
            state.active_containers.remove(container_id)
    if isinstance(value, tuple):
        return _project_payload_shapes(list(value), state)
    return value


def _public_resource(value: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate and safely serialize a recognisable Resource mapping."""
    if not {"type", "title"}.issubset(value):
        return None
    try:
        return public_resource_dump(Resource.model_validate(value))
    except (RecursionError, TypeError, ValidationError, ValueError):
        return None


def _public_package(value: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate and safely serialize a recognisable ResourcePackage mapping."""
    if not {"topic", "resources"}.issubset(value):
        return None
    try:
        return public_package_dump(ResourcePackage.model_validate(value))
    except (RecursionError, TypeError, ValidationError, ValueError):
        return None


def _scrub_known_json(value: Any, state: _ScrubState | None = None) -> Any:
    """Scrub JSON-shaped data with explicit string, container, and node bounds.

    Known schema nesting is deliberately not depth-limited.  Unknown objects
    are never formatted and are represented only by the redaction marker.
    """
    current = state or _ScrubState()
    current.nodes += 1
    if current.nodes > _MAX_TOTAL_NODES:
        return TRUNCATED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, bytes):
        return REDACTED
    if isinstance(value, Mapping):
        return _scrub_mapping(value, current)
    if isinstance(value, Sequence):
        return _scrub_sequence(value, current)
    return REDACTED


def _scrub_mapping(value: Mapping[Any, Any], state: _ScrubState) -> dict[str, Any]:
    container_id = id(value)
    if container_id in state.active_containers:
        return {TRUNCATED: TRUNCATED}
    state.active_containers.add(container_id)
    try:
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_CONTAINER_ITEMS:
                result[TRUNCATED] = TRUNCATED
                break
            if not isinstance(key, str):
                result[REDACTED] = REDACTED
                continue
            safe_key = _bound_key(key)
            if is_sensitive_key(key) or _is_host_path_key(key):
                result[safe_key] = REDACTED
            else:
                result[safe_key] = _scrub_known_json(item, state)
        return result
    finally:
        state.active_containers.remove(container_id)


def _scrub_sequence(value: Sequence[Any], state: _ScrubState) -> list[Any]:
    container_id = id(value)
    if container_id in state.active_containers:
        return [TRUNCATED]
    state.active_containers.add(container_id)
    try:
        result = [
            _scrub_known_json(item, state)
            for index, item in enumerate(value)
            if index < _MAX_CONTAINER_ITEMS
        ]
        if len(value) > _MAX_CONTAINER_ITEMS:
            result.append(TRUNCATED)
        return result
    finally:
        state.active_containers.remove(container_id)


def _scrub_string(value: str) -> str:
    bounded = value[:_MAX_STRING_CHARS]
    if is_credential_shaped(bounded):
        return REDACTED
    return redact_text(bounded, max_length=_MAX_STRING_CHARS) + (
        TRUNCATED if len(value) > _MAX_STRING_CHARS else ""
    )


def _bound_key(value: str) -> str:
    """Bound public mapping keys without invoking user-defined formatting."""
    if len(value) <= _MAX_STRING_CHARS:
        return value
    return value[:_MAX_STRING_CHARS] + TRUNCATED


def _is_host_path_key(key: str) -> bool:
    """Recognise filesystem-location fields while retaining portable keys."""
    normalised = "".join(character for character in key.lower() if character.isalnum())
    return normalised == "path" or normalised.endswith("path")


__all__ = ["project_public_event", "project_public_payload"]
