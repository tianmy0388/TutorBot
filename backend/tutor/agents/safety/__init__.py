"""Safety / anti-hallucination agent cluster.

- :class:`ContentSafetyAgent`     — keyword + LLM safety check
- :class:`AntiHallucinationAgent` — fact-check + consistency + safety
- :class:`FactCheckService`       — claim extraction + KB retrieval + judgment
"""

from tutor.agents.safety.anti_hallucination import (
    AntiHallucinationAgent,
    AntiHallucinationReport,
    OverallVerdict,
)
from tutor.agents.safety.content_safety import (
    KEYWORD_BLACKLIST,
    ContentSafetyAgent,
    SafetyReport,
)
from tutor.services.fact_check.verifier import (
    ClaimCheck,
    ClaimVerdict,
    FactCheckResult,
    FactCheckService,
)

__all__ = [
    "AntiHallucinationAgent",
    "AntiHallucinationReport",
    "ClaimCheck",
    "ClaimVerdict",
    "ContentSafetyAgent",
    "FactCheckResult",
    "FactCheckService",
    "KEYWORD_BLACKLIST",
    "OverallVerdict",
    "SafetyReport",
]
