"""Typed schemas for the resource plan service (Task 4).

The resource plan is the structured output of the intent router when it
decides the user is asking for resource generation. The plan lists a
``recommended`` set of resource types the system will produce, an
``optional`` set the user may add, and an ``estimated_seconds`` budget
for the whole run. The user can confirm a (possibly edited)
``selected_types`` subset before any agent runs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Canonical resource types
# ---------------------------------------------------------------------------

SUPPORTED_RESOURCE_TYPES: frozenset[str] = frozenset(
    {
        "document",
        "mindmap",
        "exercise",
        "reading",
        "video",
        "code",
        "ppt",
    }
)


# ---------------------------------------------------------------------------
# Request / response
# ---------------------------------------------------------------------------


class ResourcePlanRequest(BaseModel):
    """Client payload for ``POST /api/v1/plans``."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    user_id: str = "anonymous"
    language: str = "zh"
    explicit_capability: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SelectedResourceTypes(BaseModel):
    """User-confirmed resource types for one plan."""

    model_config = ConfigDict(extra="forbid")

    types: list[str]

    @field_validator("types")
    @classmethod
    def _validate_types(cls, v: list[str]) -> list[str]:
        bad = [t for t in v if t not in SUPPORTED_RESOURCE_TYPES]
        if bad:
            raise ValueError(
                f"unsupported resource types: {bad}. "
                f"allowed: {sorted(SUPPORTED_RESOURCE_TYPES)}"
            )
        # de-dupe while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for t in v:
            if t not in seen:
                deduped.append(t)
                seen.add(t)
        return deduped


class ResourcePlan(BaseModel):
    """Server-side plan returned by ``POST /api/v1/plans``."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    intent: str  # the capability name (e.g. "resource_generation")
    topic: str
    recommended: list[str]  # the user should confirm this list
    optional: list[str]    # extras the user may add
    estimated_seconds: int
    rationale: str = ""


class ResourcePlanConfirmRequest(BaseModel):
    """Client payload for ``POST /api/v1/plans/{plan_id}/confirm``."""

    model_config = ConfigDict(extra="forbid")

    selected_types: SelectedResourceTypes
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ResourcePlan",
    "ResourcePlanConfirmRequest",
    "ResourcePlanRequest",
    "SelectedResourceTypes",
    "SUPPORTED_RESOURCE_TYPES",
]
