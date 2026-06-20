"""Learning event + assessment data models.

Captures student actions + multi-dimensional learning effectiveness
assessment. All dataclasses are JSON-serialisable for persistence.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    """All trackable learning events."""

    RESOURCE_VIEWED = "resource_viewed"        # opened a resource
    RESOURCE_COMPLETED = "resource_completed"  # finished (scrolled to end)
    EXERCISE_ATTEMPTED = "exercise_attempted"  # tried a question
    EXERCISE_COMPLETED = "exercise_completed"  # completed a full exercise
    TUTORING_ASKED = "tutoring_asked"          # asked a Q in tutoring
    TUTORING_FOLLOWED = "tutoring_followed"    # clicked follow-up suggestion
    PATH_ADVANCED = "path_advanced"            # moved to next path node
    RESOURCE_RATED = "resource_rated"          # 1-5 star rating
    PROFILE_UPDATED = "profile_updated"        # learner profile changed


class AssessmentDimension(str, Enum):
    """The 6 dimensions of learning effectiveness."""

    KNOWLEDGE_MASTERY = "knowledge_mastery"  # 知识掌握度
    ENGAGEMENT = "engagement"               # 参与度
    COMPREHENSION = "comprehension"         # 理解深度
    PACE = "pace"                            # 学习节奏
    GAPS = "gaps"                            # 薄弱点
    TRAJECTORY = "trajectory"                # 学习轨迹


class TrajectoryTrend(str, Enum):
    """Direction of learning trajectory."""

    IMPROVING = "improving"
    STAGNANT = "stagnant"
    DECLINING = "declining"
    INSUFFICIENT_DATA = "insufficient_data"


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


@dataclass
class LearningEvent:
    """A single trackable learning event."""

    model_config = {} if not hasattr(dataclass, "model_config") else None

    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    user_id: str = ""
    event_type: EventType = EventType.RESOURCE_VIEWED
    target_id: str = ""  # resource_id, exercise_id, package_id, etc.
    concept_id: str = ""  # optional concept this event relates to
    duration_seconds: int = 0
    score: float | None = None  # for exercises: 0-1
    correct: bool | None = None  # for exercise_attempted
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "user_id": self.user_id,
            "event_type": self.event_type.value,
            "target_id": self.target_id,
            "concept_id": self.concept_id,
            "duration_seconds": self.duration_seconds,
            "score": self.score,
            "correct": self.correct,
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LearningEvent":
        return cls(
            event_id=data.get("event_id", uuid.uuid4().hex),
            user_id=data.get("user_id", ""),
            event_type=EventType(data["event_type"]) if "event_type" in data else EventType.RESOURCE_VIEWED,
            target_id=data.get("target_id", ""),
            concept_id=data.get("concept_id", ""),
            duration_seconds=int(data.get("duration_seconds", 0)),
            score=data.get("score"),
            correct=data.get("correct"),
            metadata=dict(data.get("metadata") or {}),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if "created_at" in data
                else datetime.now(timezone.utc)
            ),
        )


# ---------------------------------------------------------------------------
# Assessment dimensions
# ---------------------------------------------------------------------------


@dataclass
class DimensionScore:
    """Score for one assessment dimension."""

    dimension: AssessmentDimension
    score: float  # 0-1
    evidence: list[str] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self) -> None:
        if math.isnan(self.score) or math.isinf(self.score):
            raise ValueError(f"score must be finite, got {self.score!r}")
        self.score = max(0.0, min(1.0, float(self.score)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension.value,
            "score": round(self.score, 3),
            "evidence": list(self.evidence),
            "notes": self.notes,
        }


@dataclass
class AssessmentReport:
    """Full multi-dimensional assessment for a user at a point in time."""

    user_id: str
    dimension_scores: dict[AssessmentDimension, DimensionScore] = field(default_factory=dict)
    overall_score: float = 0.5
    trajectory: TrajectoryTrend = TrajectoryTrend.INSUFFICIENT_DATA
    weak_concepts: list[str] = field(default_factory=list)
    strong_concepts: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    notes: str = ""
    event_window_hours: int = 168  # default 1 week
    events_analyzed: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "dimension_scores": {
                k.value: v.to_dict() for k, v in self.dimension_scores.items()
            },
            "overall_score": round(self.overall_score, 3),
            "trajectory": self.trajectory.value,
            "weak_concepts": list(self.weak_concepts),
            "strong_concepts": list(self.strong_concepts),
            "recommendations": list(self.recommendations),
            "notes": self.notes,
            "event_window_hours": self.event_window_hours,
            "events_analyzed": self.events_analyzed,
            "created_at": self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Adaptive strategy
# ---------------------------------------------------------------------------


class ActionType(str, Enum):
    """Kinds of adaptive actions we can take."""

    RECOMMEND_REVIEW = "recommend_review"           # push review material
    RECOMMEND_ADVANCE = "recommend_advance"         # push advanced content
    RECOMMEND_PRACTICE = "recommend_practice"       # push more exercises
    RECOMMEND_TUTORING = "recommend_tutoring"       # trigger tutoring session
    RECOMMEND_BREAK = "recommend_break"             # student is overloaded
    ADJUST_PACE = "adjust_pace"                     # change chunk size
    NO_ACTION = "no_action"                          # learner is on track


@dataclass
class RecommendedAction:
    """One recommended adaptive action."""

    action_type: ActionType
    target_concept: str = ""
    target_resource_type: str = ""  # document/exercise/video/...
    rationale: str = ""
    priority: int = 5  # 1=highest, 10=lowest
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "target_concept": self.target_concept,
            "target_resource_type": self.target_resource_type,
            "rationale": self.rationale,
            "priority": self.priority,
            "metadata": dict(self.metadata),
        }


@dataclass
class StrategyDecision:
    """Output of adaptive strategy engine."""

    user_id: str
    actions: list[RecommendedAction] = field(default_factory=list)
    overall_directive: str = ""
    notes: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "actions": [a.to_dict() for a in self.actions],
            "overall_directive": self.overall_directive,
            "notes": self.notes,
            "created_at": self.created_at.isoformat(),
        }


__all__ = [
    "ActionType",
    "AssessmentDimension",
    "AssessmentReport",
    "DimensionScore",
    "EventType",
    "LearningEvent",
    "RecommendedAction",
    "StrategyDecision",
    "TrajectoryTrend",
]
