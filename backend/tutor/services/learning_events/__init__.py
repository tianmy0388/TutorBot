"""Learning events service.

Tracks per-user learning activities + multi-dimensional assessment.

Modules
-------
- :mod:`tutor.services.learning_events.schema`  — data models
- :mod:`tutor.services.learning_events.store`    — SQLite persistence
"""

from tutor.services.learning_events.schema import (
    AssessmentDimension,
    AssessmentReport,
    DimensionScore,
    EventType,
    LearningEvent,
    LearningEventType,
    RecommendedAction,
    StrategyDecision,
    TrajectoryTrend,
)
from tutor.services.learning_events.store import (
    AppendResult,
    EventConflictError,
    LearningEventStore,
    get_learning_event_store,
    reset_learning_event_store,
)

__all__ = [
    "AssessmentDimension",
    "AssessmentReport",
    "DimensionScore",
    "EventType",
    "LearningEvent",
    "LearningEventType",
    "AppendResult",
    "EventConflictError",
    "LearningEventStore",
    "RecommendedAction",
    "StrategyDecision",
    "TrajectoryTrend",
    "get_learning_event_store",
    "reset_learning_event_store",
]
