"""Fact-checking service.

Lightweight claim → evidence verification, designed to plug into
:class:`tutor.agents.safety.anti_hallucination.AntiHallucinationAgent`.

Pipeline:
1. Extract key claims from content (LLM call)
2. For each claim, retrieve evidence from KB (keyword search for MVP;
   will swap to vector search in Phase 5)
3. Verify each claim against its evidence (LLM call)

The keyword-based retriever is intentionally simple — for MVP we just
score each KB file by the count of overlapping non-stopword tokens.
This is good enough to demonstrate the pipeline; the LLM does the
"is this evidence consistent with the claim" judgment.
"""

from tutor.services.fact_check.verifier import (
    ClaimVerdict,
    FactCheckResult,
    FactCheckService,
    FactEvidence,
    get_fact_check_service,
    reset_fact_check_service,
)

__all__ = [
    "ClaimVerdict",
    "FactCheckResult",
    "FactCheckService",
    "FactEvidence",
    "get_fact_check_service",
    "reset_fact_check_service",
]
