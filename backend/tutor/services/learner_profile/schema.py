"""Six-dimensional learner profile data model (Pydantic v2).

Design notes
------------
- All mastery scores are normalised to ``[0.0, 1.0]`` for easy maths.
- ``KnowledgeMap`` is *sparse*: only concepts we've observed are present.
- Profile updates go through :class:`ProfileDiff` (the agent never rewrites
  the whole profile — it always returns a diff that we apply with
  :func:`apply_diff`).
- Times are timezone-aware UTC.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CognitiveStyle(str, Enum):
    """Felder-Silverman learning style model (subset)."""

    VISUAL = "visual"          # prefers diagrams, charts, videos
    VERBAL = "verbal"          # prefers text, written explanations
    DEDUCTIVE = "deductive"    # prefers general → specific
    INDUCTIVE = "inductive"    # prefers specific → general
    ACTIVE = "active"          # prefers doing / experimenting
    REFLECTIVE = "reflective"  # prefers observing / thinking first


class GoalType(str, Enum):
    """What the student is trying to achieve."""

    EXAM_PREP = "exam_prep"               # 期中/期末/资格考试
    PROJECT_BUILD = "project_build"       # 课程项目 / 毕设
    SKILL_UPGRADE = "skill_upgrade"       # 技能提升 / 转行
    CURIOSITY = "curiosity"               # 兴趣探索
    RESEARCH = "research"                 # 学术研究
    COMPETITION = "competition"           # 比赛 / Kaggle


class Urgency(str, Enum):
    """How soon the student needs results."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class KnowledgeMap(BaseModel):
    """Sparse mastery scores per concept.

    ``scores`` maps concept-id → mastery in [0, 1].
    Mastery is monotonically updated (increases with success, decreases with
    failure) but never goes below 0 or above 1.
    """

    model_config = ConfigDict(extra="forbid")

    scores: dict[str, float] = Field(default_factory=dict)
    last_updated: dict[str, datetime] = Field(default_factory=dict)

    @field_validator("scores")
    @classmethod
    def _validate_scores(cls, v: dict[str, float]) -> dict[str, float]:
        cleaned: dict[str, float] = {}
        for k, val in v.items():
            if not isinstance(k, str) or not k.strip():
                raise ValueError(f"knowledge key must be non-empty string: {k!r}")
            try:
                f = float(val)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"mastery for {k!r} must be numeric, got {val!r}") from exc
            if math.isnan(f) or math.isinf(f):
                raise ValueError(f"mastery for {k!r} must be finite, got {f!r}")
            cleaned[k] = max(0.0, min(1.0, f))
        return cleaned

    def get(self, concept: str, default: float = 0.0) -> float:
        return self.scores.get(concept, default)

    def set(self, concept: str, mastery: float) -> None:
        self.scores[concept] = max(0.0, min(1.0, float(mastery)))
        self.last_updated[concept] = datetime.now(UTC)

    def update(self, concept: str, delta: float) -> float:
        """Apply a delta (positive or negative) to a concept's mastery.

        Returns the new mastery.
        """
        cur = self.scores.get(concept, 0.0)
        new = max(0.0, min(1.0, cur + float(delta)))
        self.scores[concept] = new
        self.last_updated[concept] = datetime.now(UTC)
        return new

    def known_concepts(self) -> list[str]:
        return list(self.scores.keys())

    def average_mastery(self) -> float:
        if not self.scores:
            return 0.0
        return sum(self.scores.values()) / len(self.scores)

    def weak_concepts(self, threshold: float = 0.4) -> list[str]:
        return [c for c, v in self.scores.items() if v < threshold]

    def strong_concepts(self, threshold: float = 0.8) -> list[str]:
        return [c for c, v in self.scores.items() if v >= threshold]


class ErrorPattern(BaseModel):
    """A recurring mistake pattern observed for a concept."""

    model_config = ConfigDict(extra="forbid")

    concept: str
    mistake_type: str = "general"  # free-form: "sign_error", "off_by_one", ...
    frequency: int = 1
    last_observed: datetime = Field(default_factory=lambda: datetime.now(UTC))
    examples: list[str] = Field(default_factory=list)
    notes: str = ""

    def bump(self, example: str | None = None) -> None:
        self.frequency += 1
        self.last_observed = datetime.now(UTC)
        if example:
            self.examples.append(example)
            # Keep only last 5 examples
            self.examples = self.examples[-5:]


class PaceProfile(BaseModel):
    """Student's preferred learning pace."""

    model_config = ConfigDict(extra="forbid")

    avg_session_duration_min: int = 30  # typical single sitting
    preferred_chunk_size_min: int = 15  # per resource / topic block
    review_interval_hours: int = 24     # spaced-repetition cadence
    daily_time_budget_min: int = 60     # how much time per day
    sessions_per_week: int = 5          # typical cadence

    @field_validator(
        "avg_session_duration_min",
        "preferred_chunk_size_min",
        "review_interval_hours",
        "daily_time_budget_min",
    )
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"must be non-negative, got {v}")
        return v

    @field_validator("sessions_per_week")
    @classmethod
    def _sessions_in_range(cls, v: int) -> int:
        if v < 0 or v > 7 * 24:
            raise ValueError(f"sessions_per_week must be in [0, 168], got {v}")
        return v


class MotivationProfile(BaseModel):
    """Student's motivation and goals."""

    model_config = ConfigDict(extra="forbid")

    goal_type: GoalType = GoalType.CURIOSITY
    goal_description: str = ""
    urgency: Urgency = Urgency.MEDIUM
    self_efficacy: float = 0.5  # 0-1, how confident the student feels
    target_completion_date: datetime | None = None
    stakes: str = ""  # what's at risk if they fail

    @field_validator("self_efficacy")
    @classmethod
    def _efficacy_in_range(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"self_efficacy must be finite, got {v!r}")
        return max(0.0, min(1.0, float(v)))


class ModalityPreferences(BaseModel):
    """How much the student prefers each learning modality (0-1)."""

    model_config = ConfigDict(extra="forbid")

    text: float = 0.6
    video: float = 0.7
    interactive: float = 0.6
    diagram: float = 0.7
    code: float = 0.5
    audio: float = 0.3
    exercise: float = 0.7

    @field_validator("*")
    @classmethod
    def _in_unit(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"modality score must be finite, got {v!r}")
        return max(0.0, min(1.0, float(v)))

    def dominant(self) -> str:
        return max(self.model_dump(), key=lambda k: getattr(self, k))

    def to_dict(self) -> dict[str, float]:
        return self.model_dump()


# ---------------------------------------------------------------------------
# Root model
# ---------------------------------------------------------------------------


class LearnerProfile(BaseModel):
    """The complete learner profile (≥6 dimensions per idea.md).

    Fields
    ------
    user_id : str
        Stable per student (default = "anonymous" for single-user MVP).
    knowledge_map : KnowledgeMap
        Sparse mastery scores per concept.
    cognitive_style : CognitiveStyle
        Single best-guess style (we keep one for simplicity; sub-scores can
        be added later in ``metadata``).
    error_patterns : list[ErrorPattern]
        Recent recurring mistakes, ordered by frequency desc.
    learning_pace : PaceProfile
        Pace parameters.
    motivation : MotivationProfile
        Motivation & goals.
    modality : ModalityPreferences
        Modality preference scores.
    version : int
        Monotonic version, bumped on every persisted update.
    created_at, updated_at : datetime
        Audit timestamps.
    metadata : dict
        Free-form per-student metadata (course context, custom tags, ...).
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str = "anonymous"
    knowledge_map: KnowledgeMap = Field(default_factory=KnowledgeMap)
    cognitive_style: CognitiveStyle = CognitiveStyle.VISUAL
    error_patterns: list[ErrorPattern] = Field(default_factory=list)
    learning_pace: PaceProfile = Field(default_factory=PaceProfile)
    motivation: MotivationProfile = Field(default_factory=MotivationProfile)
    modality: ModalityPreferences = Field(default_factory=ModalityPreferences)
    event_watermark: int = Field(default=0, ge=0)
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def age_days(self) -> float:
        delta = datetime.now(UTC) - self.created_at
        return delta.total_seconds() / 86400.0

    def weak_concepts(self, threshold: float = 0.4) -> list[str]:
        return self.knowledge_map.weak_concepts(threshold)

    def strong_concepts(self, threshold: float = 0.8) -> list[str]:
        return self.knowledge_map.strong_concepts(threshold)

    def to_summary(self) -> dict[str, Any]:
        """Compact summary for logging / dashboard."""
        return {
            "user_id": self.user_id,
            "version": self.version,
            "cognitive_style": self.cognitive_style.value,
            "knowledge_count": len(self.knowledge_map.scores),
            "avg_mastery": round(self.knowledge_map.average_mastery(), 3),
            "weak_concepts": self.weak_concepts(),
            "strong_concepts": self.strong_concepts(),
            "error_pattern_count": len(self.error_patterns),
            "goal": self.motivation.goal_type.value,
            "urgency": self.motivation.urgency.value,
            "self_efficacy": round(self.motivation.self_efficacy, 3),
            "modality_dominant": self.modality.dominant(),
            "major": str(self.metadata.get("major") or ""),
            "level": str(self.metadata.get("level") or ""),
            "session_duration_min": self.learning_pace.avg_session_duration_min,
            "event_watermark": self.event_watermark,
            "updated_at": self.updated_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Diff (for incremental updates)
# ---------------------------------------------------------------------------


class LearningPath(BaseModel):
    """A student's planned learning path through a knowledge graph.

    Stored alongside the profile for resource-push decisions.
    """

    model_config = ConfigDict(extra="forbid")

    path_id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = ""
    sequence: list[str] = Field(default_factory=list)  # concept_ids in order
    current_index: int = 0
    completed: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PersistedLearningPath(BaseModel):
    """Durable path bound to the exact profile version used to plan it."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    profile_version: int = Field(ge=1)
    course: str = ""
    path_id: str = ""
    name: str = ""
    description: str = ""
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    rationale: str = ""
    total_estimated_hours: float = 0.0
    completed_count: int = 0
    available_count: int = 0
    locked_count: int = 0
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProfileDiff(BaseModel):
    """A partial update to a :class:`LearnerProfile`.

    Agents emit :class:`ProfileDiff` instances; the store applies them
    via :func:`apply_diff` so we never lose concurrent updates.
    """

    model_config = ConfigDict(extra="forbid")

    # Knowledge mastery deltas (concept → delta in [-1, 1])
    knowledge_delta: dict[str, float] = Field(default_factory=dict)
    # Absolute knowledge overrides (concept → new mastery)
    knowledge_set: dict[str, float] = Field(default_factory=dict)

    cognitive_style: CognitiveStyle | None = None
    error_pattern: ErrorPattern | None = None
    learning_pace: PaceProfile | None = None
    motivation: MotivationProfile | None = None
    modality: ModalityPreferences | None = None

    metadata_merge: dict[str, Any] = Field(default_factory=dict)

    @field_validator("knowledge_delta")
    @classmethod
    def _delta_in_range(cls, v: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for k, val in v.items():
            try:
                f = float(val)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"delta for {k!r} must be numeric, got {val!r}") from exc
            if math.isnan(f) or math.isinf(f):
                raise ValueError(f"delta for {k!r} must be finite, got {f!r}")
            out[k] = max(-1.0, min(1.0, f))
        return out

    @field_validator("knowledge_set")
    @classmethod
    def _set_in_range(cls, v: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for k, val in v.items():
            try:
                f = float(val)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"mastery for {k!r} must be numeric, got {val!r}") from exc
            if math.isnan(f) or math.isinf(f):
                raise ValueError(f"mastery for {k!r} must be finite, got {f!r}")
            out[k] = max(0.0, min(1.0, f))
        return out

    def is_empty(self) -> bool:
        return not (
            self.knowledge_delta
            or self.knowledge_set
            or self.cognitive_style
            or self.error_pattern
            or self.learning_pace
            or self.motivation
            or self.modality
            or self.metadata_merge
        )


# ---------------------------------------------------------------------------
# Diff application
# ---------------------------------------------------------------------------


def apply_diff(profile: LearnerProfile, diff: ProfileDiff) -> LearnerProfile:
    """Apply a :class:`ProfileDiff` to a profile in-place and return it.

    Notes
    -----
    - Empty diffs are a no-op (no version bump, no timestamp change).
    - ``knowledge_delta`` is added to existing mastery.
    - ``knowledge_set`` overrides (after delta).
    - ``error_pattern`` is upserted by ``(concept, mistake_type)``:
      same combo → ``bump()``; new → appended.
    - ``metadata_merge`` does a shallow merge (later writes win).
    - All other fields, if provided, replace the corresponding sub-object.
    - Version is bumped by 1.
    - ``updated_at`` is set to ``now``.
    """
    if diff.is_empty():
        return profile

    # Knowledge: deltas first, then overrides
    for concept, delta in diff.knowledge_delta.items():
        profile.knowledge_map.update(concept, delta)
    for concept, value in diff.knowledge_set.items():
        profile.knowledge_map.set(concept, value)

    if diff.cognitive_style is not None:
        profile.cognitive_style = diff.cognitive_style

    if diff.error_pattern is not None:
        ep = diff.error_pattern
        existing = next(
            (
                p
                for p in profile.error_patterns
                if p.concept == ep.concept and p.mistake_type == ep.mistake_type
            ),
            None,
        )
        if existing is not None:
            existing.bump(example=ep.examples[0] if ep.examples else None)
        else:
            profile.error_patterns.append(ep)

    if diff.learning_pace is not None:
        profile.learning_pace = diff.learning_pace
    if diff.motivation is not None:
        profile.motivation = diff.motivation
    if diff.modality is not None:
        profile.modality = diff.modality

    if diff.metadata_merge:
        profile.metadata.update(diff.metadata_merge)

    profile.version += 1
    profile.updated_at = datetime.now(UTC)
    return profile


def empty_profile(user_id: str = "anonymous") -> LearnerProfile:
    """Build a blank profile — used as a starting point for new students."""
    return LearnerProfile(user_id=user_id)


__all__ = [
    "CognitiveStyle",
    "ErrorPattern",
    "GoalType",
    "KnowledgeMap",
    "LearnerProfile",
    "LearningPath",
    "ModalityPreferences",
    "MotivationProfile",
    "PaceProfile",
    "PersistedLearningPath",
    "ProfileDiff",
    "Urgency",
    "apply_diff",
    "empty_profile",
]
