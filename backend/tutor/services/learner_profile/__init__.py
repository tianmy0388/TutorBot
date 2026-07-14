"""Learner profile service.

Six-dimensional learner profile per idea.md:

1. knowledge_map        — mastery scores per concept (0-1)
2. cognitive_style      — visual / verbal / deductive / inductive / active / reflective
3. error_patterns       — recurring mistakes per concept
4. learning_pace        — avg_session_duration / preferred_chunk_size / review_interval
5. motivation_profile   — goal_type / urgency / self_efficacy
6. modality_preferences — text / video / interactive / diagram / code

Modules
-------
- :mod:`tutor.services.learner_profile.schema`   — Pydantic models
- :mod:`tutor.services.learner_profile.store`    — SQLite persistence
- :mod:`tutor.services.learner_profile.builder`  — High-level orchestration
"""

from tutor.services.learner_profile.schema import (
    CognitiveStyle,
    ErrorPattern,
    GoalType,
    KnowledgeMap,
    LearnerProfile,
    LearningPath,
    ModalityPreferences,
    MotivationProfile,
    PaceProfile,
    ProfileDiff,
    Urgency,
)
from tutor.services.learner_profile.store import (
    ProfileEvent,
    ProfileEventType,
    ProfileStore,
    _close_profile_store_sync,
    get_profile_store,
    reset_profile_store,
)
from tutor.services.learner_profile.builder import (
    ProfileBuilder,
    get_profile_builder,
    reset_profile_builder,
)

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
    "ProfileBuilder",
    "ProfileDiff",
    "ProfileEvent",
    "ProfileEventType",
    "ProfileStore",
    "Urgency",
    "_close_profile_store_sync",
    "get_profile_builder",
    "get_profile_store",
    "reset_profile_builder",
    "reset_profile_store",
]
