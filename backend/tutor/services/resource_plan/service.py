"""Resource plan service (Task 4).

The service is deterministic and LLM-free: given a topic and a
:class:`LearnerProfile`, it produces a default set of resource types
the user is most likely to need. Profile modality scores bias the
defaults; user-explicit selections override everything.

Critical product rules (enforced here, asserted in tests):

- The default plan ALWAYS contains document, mindmap, exercise.
- ``video`` and ``ppt`` are NEVER added by the profile or default
  rules — they require explicit user request (otherwise we get surprise
  long-running Manim renders and brittle PPT generation).
- Comparison queries (e.g. "对比 RNN 和 LSTM") exclude video.
- The seven supported types are the source of truth.
"""

from __future__ import annotations

from typing import Iterable

from tutor.services.learner_profile.schema import LearnerProfile
from tutor.services.resource_plan.schema import (
    ResourcePlan,
    SUPPORTED_RESOURCE_TYPES,
)

#: Resource types that always run by default (no user / profile action needed).
DEFAULT_REQUIRED_TYPES: tuple[str, ...] = ("document", "mindmap", "exercise")

#: Resource types gated behind an explicit user request. Profile modality
#: never promotes them into ``recommended``.
EXPLICIT_ONLY_TYPES: frozenset[str] = frozenset({"video", "ppt"})

#: Resource types that are easy to add when the profile signals interest.
PROFILE_SENSITIVE_TYPES: dict[str, str] = {
    # resource_type -> corresponding ModalityPreferences field
    "reading": "text",
    "code": "code",
    "exercise": "exercise",
    "document": "text",
    "mindmap": "diagram",
}

#: Modality score above which a profile-sensitive type is added by default.
PROFILE_ADD_THRESHOLD = 0.7

#: Rough per-resource-type estimate in seconds (used for the budget card).
_TYPE_ESTIMATED_SECONDS: dict[str, int] = {
    "document": 15,
    "mindmap": 10,
    "exercise": 20,
    "reading": 8,
    "video": 90,
    "code": 15,
    "ppt": 60,
}


def build_default_plan(
    *,
    topic: str,
    explicit_types: Iterable[str] = (),
    profile: LearnerProfile | None = None,
    comparison: bool = False,
) -> ResourcePlan:
    """Build the default :class:`ResourcePlan` for a topic.

    Parameters
    ----------
    topic
        The extracted learning topic (e.g. "Transformer").
    explicit_types
        Resource types the user explicitly asked for in their message
        (e.g. from the keyword router).
    profile
        Optional learner profile. If present, modality scores bias the
        plan toward profile-sensitive types.
    comparison
        If True, exclude video/PPT (used for "对比 / vs" queries).
    """
    explicit = {t for t in explicit_types if t in SUPPORTED_RESOURCE_TYPES}
    recommended: list[str] = list(DEFAULT_REQUIRED_TYPES)

    if profile is not None:
        for rtype, mod_field in PROFILE_SENSITIVE_TYPES.items():
            if rtype in EXPLICIT_ONLY_TYPES:
                continue
            score = float(getattr(profile.modality, mod_field, 0.0))
            if score >= PROFILE_ADD_THRESHOLD and rtype not in recommended:
                recommended.append(rtype)

    # User-explicit selections always win. Comparison query gate.
    for t in explicit:
        if comparison and t in EXPLICIT_ONLY_TYPES:
            continue
        if t not in recommended:
            recommended.append(t)

    # All non-recommended, non-explicit types are ``optional``.
    optional = sorted(
        SUPPORTED_RESOURCE_TYPES - set(recommended),
    )

    estimated = sum(_TYPE_ESTIMATED_SECONDS.get(t, 10) for t in recommended)

    rationale_parts: list[str] = []
    if explicit & EXPLICIT_ONLY_TYPES:
        names = "、".join(sorted(explicit & EXPLICIT_ONLY_TYPES))
        rationale_parts.append(f"已根据你的请求加入：{names}")
    if profile is not None and any(
        rtype in recommended and rtype not in DEFAULT_REQUIRED_TYPES
        for rtype in PROFILE_SENSITIVE_TYPES
    ):
        rationale_parts.append("已根据学习偏好调整默认清单")
    if comparison:
        rationale_parts.append("对比类问题不推荐视频/PPT 制作")
    if not rationale_parts:
        rationale_parts.append("默认核心三类：文档、思维导图、练习")

    return ResourcePlan(
        plan_id="",  # filled in by the router when persisted
        intent="resource_generation",
        topic=topic,
        recommended=recommended,
        optional=optional,
        estimated_seconds=estimated,
        rationale="；".join(rationale_parts),
    )


def recommend_for_profile(
    *,
    topic: str,
    profile: LearnerProfile,
    explicit_types: Iterable[str] = (),
) -> ResourcePlan:
    """Convenience wrapper used by the API router."""
    return build_default_plan(
        topic=topic,
        explicit_types=explicit_types,
        profile=profile,
    )


__all__ = [
    "build_default_plan",
    "recommend_for_profile",
    "DEFAULT_REQUIRED_TYPES",
    "EXPLICIT_ONLY_TYPES",
]
