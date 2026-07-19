"""Deterministic, LLM-free intent router (Task 4).

The router is the front door of the fast-path vs production-path
distinction:

- An ordinary tutoring/portrait question (``解释 self-attention``) goes
  straight to the :class:`tutoring` capability — no plan, no video, no
  LLM cost on routing.
- A resource generation request (``为 Transformer 制定学习资源``) goes
  to the planning step first. The plan is returned to the user; the
  user confirms; only then is a job created.
- Comparison queries (``对比 RNN 和 LSTM``) use the tutoring
  capability unless the user explicitly asks for resource generation.

Precedence (highest first):

1. ``explicit_capability`` argument (UI or test override)
2. assessment keywords
3. profile keywords
4. path-planning keywords
5. resource-generation keywords (which also extract ``explicit_types``)
6. tutoring default

The router NEVER consults an LLM and NEVER adds ``video`` or ``ppt`` to
the recommended plan unless the user said so in the message.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal, cast

from tutor.services.resource_plan.schema import ResourcePlan
from tutor.services.resource_plan.service import build_default_plan

# ---------------------------------------------------------------------------
# Keyword groups
# ---------------------------------------------------------------------------

#: Phrases that, if present, indicate an assessment request.
ASSESSMENT_KEYWORDS: tuple[str, ...] = (
    "评估", "测评", "测验", "评估一下", "掌握情况", "评估报告",
    "assessment", "evaluate my", "test my",
)

#: Phrases that, if present, indicate a profile request.
PROFILE_KEYWORDS: tuple[str, ...] = (
    "学习画像", "我的画像", "了解我", "更新画像",
    "learner profile", "who am i", "my profile",
)

#: Phrases that, if present, indicate a path-planning request.
PATH_PLANNING_KEYWORDS: tuple[str, ...] = (
    "学习路径", "下一步", "下一节", "先学什么", "学完接着",
    "path", "next step", "study plan", "roadmap",
)

#: Phrases that, if present, indicate resource generation.
RESOURCE_GENERATION_KEYWORDS: tuple[str, ...] = (
    "生成资源", "学习资源", "制定资源", "为我生成", "为我整理",
    "准备一份", "制作", "帮我做", "教我", "给我", "请给我",
    "生成", "生成一份", "出一份",
    "学习一下", "系统学习", "深入学习", "学习一下",
    "generate resources", "build resources", "prepare a",
    "create resources", "study", "learn about", "create a",
    "make me", "give me",
)

#: Comparison / vs queries — these exclude video/PPT by default.
COMPARISON_PATTERNS: tuple[str, ...] = (
    r"对比", r"比较", r"区别", r"差异",
    r"\bvs\.?\b", r"\bversus\b", r"\bcompare\b", r"\bdifference\b",
)

#: Phrases that explicitly request a video/animation.
VIDEO_KEYWORDS: tuple[str, ...] = (
    "视频", "动画", "讲解视频", "录个视频",
    "video", "animation", "animated", "demo video",
)

#: Phrases that explicitly request a PPT.
PPT_KEYWORDS: tuple[str, ...] = (
    "PPT", "ppt", "课件", "幻灯片", "slides", "slide deck",
)

#: Phrases that explicitly request exercise.
EXERCISE_KEYWORDS: tuple[str, ...] = (
    "练习题", "习题", "quiz", "exercise", "practice",
)

#: Phrases that explicitly request reading.
READING_KEYWORDS: tuple[str, ...] = (
    "阅读", "拓展阅读", "参考资料", "参考文献",
    "reading", "further reading", "reference",
)

#: Phrases that explicitly request code.
CODE_KEYWORDS: tuple[str, ...] = (
    "代码", "code", "示例代码", "sample code", "实现一下",
)

# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

CapabilityName = Literal[
    "tutoring",
    "resource_generation",
    "assessment",
    "profile",
    "path_planning",
]


VALID_CAPABILITIES: frozenset[str] = frozenset(
    {
        "tutoring",
        "resource_generation",
        "path_planning",
        "assessment",
        "profile",
    }
)


class InvalidCapabilityError(ValueError):
    """Raised when a caller supplies an unsupported explicit capability."""

    code = "INVALID_CAPABILITY"

    def __init__(self, capability: str) -> None:
        self.capability = capability
        expected = ", ".join(sorted(VALID_CAPABILITIES))
        super().__init__(
            f"{self.code}: unsupported explicit capability {capability!r}; "
            f"expected one of: {expected}"
        )


def validate_explicit_capability(value: str | None) -> CapabilityName | None:
    """Validate a non-empty explicit hint; empty hints mean no override."""

    if value is None or value == "":
        return None
    if value not in VALID_CAPABILITIES:
        raise InvalidCapabilityError(value)
    return cast(CapabilityName, value)


@dataclass(frozen=True)
class IntentDecision:
    """The router's output: a capability name plus an optional plan."""

    capability: CapabilityName
    topic: str
    explicit_types: frozenset[str]
    resource_plan: ResourcePlan | None
    is_comparison: bool
    confidence: float = 0.5
    reason: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_any(haystack: str, needles: Iterable[str]) -> bool:
    h = haystack.lower()
    return any(n.lower() in h for n in needles)


def _is_comparison(msg: str) -> bool:
    return any(re.search(p, msg, re.IGNORECASE) for p in COMPARISON_PATTERNS)


def _extract_topic(msg: str) -> str:
    """Best-effort topic extraction.

    For deterministic tests we prefer a regex strip. We deliberately do
    not call any LLM here — the router must be free.
    """
    cleaned = msg.strip()
    # Strip common lead-ins.
    patterns: tuple[str, ...] = (
        r"^(请)?(帮我|为我|给我)(生成|制作|规划|学习|整理|讲|解释|对比|比较)?(一下)?(关于)?",
        r"^(解释|讲解|说明|对比|比较|学习|系统学习|深入学习|我要|我想)",
        r"^(generate|create|build|study|learn|explain|compare|prepare)\s+",
    )
    for p in patterns:
        cleaned = re.sub(p, "", cleaned, flags=re.IGNORECASE).strip()
    # Strip trailing stopwords.
    for tail in ("是什么", "的", "一下", "吧", "呢", "?"):
        if cleaned.endswith(tail):
            cleaned = cleaned[: -len(tail)].strip()
    return cleaned or msg[:40]


def _detect_explicit_types(msg: str) -> frozenset[str]:
    types: set[str] = set()
    if _has_any(msg, VIDEO_KEYWORDS):
        types.add("video")
    if _has_any(msg, PPT_KEYWORDS):
        types.add("ppt")
    if _has_any(msg, EXERCISE_KEYWORDS):
        types.add("exercise")
    if _has_any(msg, READING_KEYWORDS):
        types.add("reading")
    if _has_any(msg, CODE_KEYWORDS):
        types.add("code")
    return frozenset(types)


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def classify(
    message: str,
    *,
    explicit_capability: str | None = None,
    explicit_types: Iterable[str] | None = None,
    plan_id_factory: Callable[[], str] | None = None,
) -> IntentDecision:
    """Classify a user message into a capability + optional plan.

    Pure / deterministic: same message in, same decision out.
    """
    validated_explicit = validate_explicit_capability(explicit_capability)
    msg = (message or "").strip()
    is_comparison = _is_comparison(msg)
    detected_explicit = set(_detect_explicit_types(msg))
    if explicit_types is not None:
        detected_explicit.update(explicit_types)

    # 0. If the user explicitly asked for a resource type (video/ppt/exercise
    # /reading/code) without otherwise matching a capability, default to
    # resource_generation. This makes "给我一些练习题" / "生成一段动画"
    # produce a real plan instead of a tutoring answer.
    wants_resource = bool(detected_explicit)

    # 1. Explicit capability override
    if validated_explicit is not None:
        if validated_explicit == "resource_generation":
            plan = build_default_plan(
                topic=_extract_topic(msg),
                explicit_types=detected_explicit,
                comparison=is_comparison,
            )
            plan = plan.model_copy(
                update={"plan_id": (plan_id_factory or _new_plan_id)()}
            )
            return IntentDecision(
                capability="resource_generation",
                topic=_extract_topic(msg),
                explicit_types=frozenset(detected_explicit),
                resource_plan=plan,
                is_comparison=is_comparison,
                confidence=1.0,
                reason="explicit capability override",
            )
        return IntentDecision(
            capability=validated_explicit,
            topic=_extract_topic(msg),
            explicit_types=frozenset(detected_explicit),
            resource_plan=None,
            is_comparison=is_comparison,
            confidence=1.0,
            reason="explicit capability override",
        )

    if not msg:
        return IntentDecision(
            capability="tutoring",
            topic="",
            explicit_types=frozenset(),
            resource_plan=None,
            is_comparison=False,
            confidence=0.5,
            reason="empty message → tutoring default",
        )

    # 2. Assessment
    if _has_any(msg, ASSESSMENT_KEYWORDS):
        return IntentDecision(
            capability="assessment",
            topic=_extract_topic(msg),
            explicit_types=frozenset(detected_explicit),
            resource_plan=None,
            is_comparison=is_comparison,
            confidence=0.95,
            reason="assessment keyword",
        )

    # 3. Profile
    if _has_any(msg, PROFILE_KEYWORDS):
        return IntentDecision(
            capability="profile",
            topic=_extract_topic(msg),
            explicit_types=frozenset(detected_explicit),
            resource_plan=None,
            is_comparison=is_comparison,
            confidence=0.95,
            reason="profile keyword",
        )

    # 4. Path planning
    if _has_any(msg, PATH_PLANNING_KEYWORDS):
        return IntentDecision(
            capability="path_planning",
            topic=_extract_topic(msg),
            explicit_types=frozenset(detected_explicit),
            resource_plan=None,
            is_comparison=is_comparison,
            confidence=0.95,
            reason="path planning keyword",
        )

    # 5. Resource generation: explicit keyword OR user-requested resource type.
    if _has_any(msg, RESOURCE_GENERATION_KEYWORDS) or wants_resource:
        plan = build_default_plan(
            topic=_extract_topic(msg),
            explicit_types=detected_explicit,
            comparison=is_comparison,
        )
        plan = plan.model_copy(
            update={"plan_id": (plan_id_factory or _new_plan_id)()}
        )
        return IntentDecision(
            capability="resource_generation",
            topic=_extract_topic(msg),
            explicit_types=frozenset(detected_explicit),
            resource_plan=plan,
            is_comparison=is_comparison,
            confidence=0.9,
            reason="resource generation keyword"
            if _has_any(msg, RESOURCE_GENERATION_KEYWORDS)
            else "explicit resource type request",
        )

    # 6. Tutoring default
    return IntentDecision(
        capability="tutoring",
        topic=_extract_topic(msg),
        explicit_types=frozenset(detected_explicit),
        resource_plan=None,
        is_comparison=is_comparison,
        confidence=0.6,
        reason="tutoring default (no keyword match)",
    )


def _new_plan_id() -> str:
    return f"plan_{uuid.uuid4().hex[:12]}"


__all__ = [
    "ASSESSMENT_KEYWORDS",
    "COMPARISON_PATTERNS",
    "CapabilityName",
    "InvalidCapabilityError",
    "IntentDecision",
    "PPT_KEYWORDS",
    "PATH_PLANNING_KEYWORDS",
    "PROFILE_KEYWORDS",
    "RESOURCE_GENERATION_KEYWORDS",
    "VALID_CAPABILITIES",
    "VIDEO_KEYWORDS",
    "classify",
    "validate_explicit_capability",
]
