"""Intent classification service (Task 4)."""

from tutor.services.intent.router import (
    ASSESSMENT_KEYWORDS,
    COMPARISON_PATTERNS,
    IntentDecision,
    PATH_PLANNING_KEYWORDS,
    PROFILE_KEYWORDS,
    RESOURCE_GENERATION_KEYWORDS,
    VALID_CAPABILITIES,
    classify,
)

__all__ = [
    "ASSESSMENT_KEYWORDS",
    "COMPARISON_PATTERNS",
    "IntentDecision",
    "PATH_PLANNING_KEYWORDS",
    "PROFILE_KEYWORDS",
    "RESOURCE_GENERATION_KEYWORDS",
    "VALID_CAPABILITIES",
    "classify",
]
