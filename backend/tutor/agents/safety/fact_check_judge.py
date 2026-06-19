"""FactCheckJudge — judge whether a claim is supported by evidence (LLM)."""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.fact_check.verifier import ClaimVerdict
from tutor.services.llm.base import LLMMessage, LLMRequest


@dataclass
class JudgeVerdict:
    """LLM's verdict on one claim."""

    verdict: ClaimVerdict
    confidence: float
    reasoning: str


JUDGE_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["supported", "refuted", "unverified"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "confidence"],
}


class FactCheckJudge(BaseAgent):
    """Judge whether a claim is supported by evidence."""

    module_name = "safety"
    agent_name = "fact_check_judge"
    default_temperature = 0.1
    default_max_tokens = 512

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        claim: str,
        evidence: str,
    ) -> JudgeVerdict:
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)
        user_msg = self.get_user_prompt(prompt_data).format(
            claim=claim,
            evidence=evidence[:4000],
        )
        messages = self.build_messages(system=system, user=user_msg)

        try:
            resp = await self.call_llm(
                messages=messages,
                stream=stream,
                source=self.agent_name,
                temperature=self.default_temperature,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            logger.warning(f"FactCheckJudge LLM failed: {exc!r}")
            return JudgeVerdict(
                verdict=ClaimVerdict.UNVERIFIED,
                confidence=0.3,
                reasoning=f"judge failed: {exc}",
            )

        data = self.parse_json_response(resp.content, fallback={})
        if not isinstance(data, dict):
            data = {}
        verdict_str = str(data.get("verdict") or "unverified").lower()
        try:
            verdict = ClaimVerdict(verdict_str)
        except ValueError:
            verdict = ClaimVerdict.UNVERIFIED
        try:
            confidence = float(data.get("confidence") or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        return JudgeVerdict(
            verdict=verdict,
            confidence=confidence,
            reasoning=str(data.get("reasoning") or ""),
        )


__all__ = ["FactCheckJudge", "JudgeVerdict", "JUDGE_OUTPUT_SCHEMA"]
