"""Tutor agent cluster (智能辅导).

- :class:`QuestionUnderstandingAgent` — classify question + extract concepts
- :class:`TutoringAgent`             — 4-layer answer generator
- :class:`MultiModalEnrichmentAgent` — diagram / code / exercise suggestions
"""

from tutor.agents.tutor.multimodal_enrichment import (
    ENRICHMENT_OUTPUT_SCHEMA,
    EnrichmentSuggestion,
    EnrichmentType,
    MultiModalEnrichmentAgent,
)
from tutor.agents.tutor.question_understanding import (
    QuestionType,
    QuestionUnderstanding,
    QuestionUnderstandingAgent,
    UNDERSTANDING_SCHEMA,
)
from tutor.agents.tutor.tutoring import (
    TUTORING_OUTPUT_SCHEMA,
    TutoringAgent,
    TutoringAnswer,
)

__all__ = [
    "ENRICHMENT_OUTPUT_SCHEMA",
    "EnrichmentSuggestion",
    "EnrichmentType",
    "MultiModalEnrichmentAgent",
    "QuestionType",
    "QuestionUnderstanding",
    "QuestionUnderstandingAgent",
    "TUTORING_OUTPUT_SCHEMA",
    "TutoringAgent",
    "TutoringAnswer",
    "UNDERSTANDING_SCHEMA",
]
