"""AntiHallucinationAgent — comprehensive safety / factuality gate.

Three-pronged verification:

1. **Fact-check**  — extract claims, retrieve KB evidence, judge support
   (via :class:`FactCheckService`)
2. **Consistency** — LLM checks for internal contradictions in the content
3. **Safety** — keyword blacklist + LLM safety classifier
   (via :class:`ContentSafetyAgent`)

Output: :class:`AntiHallucinationReport` with overall verdict and per-claim
breakdown. Attached to Resource.metadata["safety"] by the capability.

Pipeline role: runs **after** QualityReviewer. If the report says
``overall_verdict = refuted`` or ``safety_blocked = True``, the capability
will mark the resource accordingly and the frontend can display a
"verified" or "unverified" badge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.agents.safety.content_safety import ContentSafetyAgent, SafetyReport
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.fact_check.verifier import (
    ClaimVerdict,
    FactCheckResult,
    FactCheckService,
    get_fact_check_service,
)
from tutor.services.llm.base import LLMMessage, LLMRequest


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------


class OverallVerdict(str, Enum):
    """Top-level safety / factuality verdict."""

    SAFE = "safe"               # everything checks out
    CAUTION = "caution"         # minor concerns, usable
    UNSAFE = "unsafe"           # blocked (refuted claim or safety issue)
    UNVERIFIED = "unverified"   # couldn't determine (e.g. no KB)


@dataclass
class AntiHallucinationReport:
    """Combined output of fact-check + consistency + safety."""

    overall_verdict: OverallVerdict = OverallVerdict.UNVERIFIED
    overall_confidence: float = 0.5
    fact_check: FactCheckResult | None = None
    safety: SafetyReport | None = None
    consistency_issues: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_verdict": self.overall_verdict.value,
            "overall_confidence": round(self.overall_confidence, 3),
            "fact_check": self.fact_check.to_dict() if self.fact_check else None,
            "safety": self.safety.to_dict() if self.safety else None,
            "consistency_issues": list(self.consistency_issues),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


CONSISTENCY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of internal contradictions / inconsistencies",
        },
        "is_consistent": {"type": "boolean"},
        "explanation": {"type": "string"},
    },
    "required": ["issues", "is_consistent"],
}


class AntiHallucinationAgent(BaseAgent):
    """Run fact-check + consistency + safety on a resource."""

    module_name = "safety"
    agent_name = "anti_hallucination"
    default_temperature = 0.2
    default_max_tokens = 2048

    def __init__(
        self,
        *,
        fact_check: FactCheckService | None = None,
        content_safety: ContentSafetyAgent | None = None,
        llm_provider=None,
    ) -> None:
        super().__init__(llm=llm_provider)
        self.fact_check = fact_check or get_fact_check_service()
        self.content_safety = content_safety or ContentSafetyAgent(llm=llm_provider)

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        resource_content: str,
        topic: str = "",
        source_documents: list[str] | None = None,
    ) -> AntiHallucinationReport:
        """Run the three checks and aggregate into a report."""
        report = AntiHallucinationReport()

        # ------------------------------------------------------------------
        # Stage 1: Fact-check
        # ------------------------------------------------------------------
        try:
            report.fact_check = await self.fact_check.check(
                content=resource_content,
                topic=topic,
                source_documents=source_documents,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"FactCheck failed: {exc!r}")

        # ------------------------------------------------------------------
        # Stage 2: Content safety
        # ------------------------------------------------------------------
        try:
            report.safety = await self.content_safety.process(
                context, stream=stream, content=resource_content
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"ContentSafety failed: {exc!r}")
            report.safety = SafetyReport(is_safe=True, notes=f"safety check failed: {exc}")

        # ------------------------------------------------------------------
        # Stage 3: Consistency (LLM call)
        # ------------------------------------------------------------------
        try:
            report.consistency_issues = await self._check_consistency(resource_content)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Consistency check failed: {exc!r}")
            report.consistency_issues = []

        # ------------------------------------------------------------------
        # Aggregate
        # ------------------------------------------------------------------
        # Safety is the highest priority
        if report.safety and not report.safety.is_safe:
            report.overall_verdict = OverallVerdict.UNSAFE
            report.overall_confidence = 0.0
            report.notes = "blocked by content safety"
            return report

        # Fact-check is next
        if report.fact_check:
            fc = report.fact_check
            if fc.overall_verdict == ClaimVerdict.REFUTED:
                report.overall_verdict = OverallVerdict.UNSAFE
                report.notes = f"refuted claims: {fc.notes}"
                report.overall_confidence = 0.3
                return report
            report.overall_confidence = fc.overall_confidence

        # Consistency issues reduce confidence but don't block
        if report.consistency_issues:
            # Lower confidence by 0.1 per issue
            report.overall_confidence = max(
                0.1, report.overall_confidence - 0.1 * len(report.consistency_issues)
            )

        # Decide final verdict
        if report.overall_confidence >= 0.8 and not report.consistency_issues:
            report.overall_verdict = OverallVerdict.SAFE
        elif report.overall_confidence >= 0.5:
            report.overall_verdict = OverallVerdict.CAUTION
        else:
            report.overall_verdict = OverallVerdict.UNVERIFIED

        if not report.notes:
            report.notes = (
                f"fact_check: {report.fact_check.notes if report.fact_check else 'n/a'}, "
                f"safety: {'safe' if report.safety and report.safety.is_safe else 'unsafe'}, "
                f"consistency_issues: {len(report.consistency_issues)}"
            )
        return report

    # ------------------------------------------------------------------
    # Consistency (LLM call)
    # ------------------------------------------------------------------

    async def _check_consistency(self, content: str) -> list[str]:
        """Ask the LLM to find internal contradictions in ``content``."""
        if len(content) < 200:
            return []
        messages = [
            LLMMessage(
                role="system",
                content=(
                    "You are a logic auditor. Find INTERNAL contradictions in the "
                    "given content. Examples:\n"
                    "- 'The list has 3 items' but later says 'the list has 4 items'\n"
                    "- 'RNN has 3 gates' but later says 'RNN has no gates'\n"
                    "Output JSON: {\"issues\": [\"...\"], \"is_consistent\": bool, \"explanation\": \"...\"}"
                ),
            ),
            LLMMessage(
                role="user",
                content=(
                    f"Content to audit:\n```\n{content[:6000]}\n```\n\n"
                    f"Return JSON."
                ),
            ),
        ]
        request = LLMRequest(
            messages=messages,
            temperature=0.1,
            max_tokens=1024,
            extra={"response_format": {"type": "json_object"}},
        )
        try:
            resp = await self.resolved_llm.call(request)
        except Exception as exc:
            logger.warning(f"Consistency LLM call failed: {exc!r}")
            return []

        data = self.parse_json_response(resp.content, fallback={})
        if not isinstance(data, dict):
            return []
        if data.get("is_consistent") is True:
            return []
        issues = data.get("issues") or []
        return [str(i) for i in issues if isinstance(i, str)]


__all__ = [
    "AntiHallucinationAgent",
    "AntiHallucinationReport",
    "OverallVerdict",
]
