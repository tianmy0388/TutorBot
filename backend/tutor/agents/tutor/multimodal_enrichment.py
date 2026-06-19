"""MultiModalEnrichmentAgent — suggest visual aids and practice problems.

Given a :class:`TutoringAnswer`, suggest:

- **Diagram** — a Mermaid block the frontend can render
- **Code example** — a runnable snippet
- **Exercise** — a quick practice problem
- **Reference link** — a pointer to existing package resources

Output: list of :class:`EnrichmentSuggestion` items.

The agent re-uses the :class:`TutoringAgent` / pedagogy / multimedia /
code_sandbox agents when possible so the suggestions are consistent
with the rest of the system. For MVP we just emit structured
suggestions; the frontend (or a follow-up job) renders / generates them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.agents.tutor.question_understanding import QuestionUnderstanding
from tutor.agents.tutor.tutoring import TutoringAnswer
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.llm.base import LLMMessage, LLMRequest


class EnrichmentType(str, Enum):
    """Kinds of enrichment we can suggest."""

    DIAGRAM = "diagram"           # Mermaid / SVG / MathTex
    CODE_EXAMPLE = "code_example"  # Runnable code
    EXERCISE = "exercise"          # Practice problem
    REFERENCE = "reference"        # Link to existing package resources
    VIDEO = "video"                # Manim video request


@dataclass
class EnrichmentSuggestion:
    """A single enrichment suggestion."""

    type: EnrichmentType
    title: str
    content: str  # Mermaid DSL, code snippet, exercise JSON, or link description
    rationale: str = ""
    confidence: float = 0.7
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "title": self.title,
            "content": self.content,
            "rationale": self.rationale,
            "confidence": round(self.confidence, 3),
            "metadata": dict(self.metadata),
        }


ENRICHMENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [t.value for t in EnrichmentType],
                    },
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "rationale": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["type", "title", "content"],
            },
        }
    },
    "required": ["suggestions"],
}


class MultiModalEnrichmentAgent(BaseAgent):
    """Suggest visual aids and exercises for a tutoring answer."""

    module_name = "tutor"
    agent_name = "multimodal_enrichment"
    default_temperature = 0.5
    default_max_tokens = 2048

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        understanding: QuestionUnderstanding,
        answer: TutoringAnswer,
    ) -> list[EnrichmentSuggestion]:
        """Return a list of suggestions (1-3 items)."""
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)
        user_msg = self.get_user_prompt(prompt_data).format(
            question=context.user_message,
            question_type=understanding.question_type.value,
            difficulty=understanding.difficulty,
            concepts=", ".join(understanding.concepts) or "(none)",
            answer_md=answer.full_markdown[:3000],
            answer_principle=(answer.principle or answer.tldr)[:1000],
        )
        messages = self.build_messages(system=system, user=user_msg)

        if stream is not None:
            async with stream.stage(
                "multimodal_enrichment", source=self.agent_name
            ):
                await stream.thinking(
                    "正在推荐多模态补充材料...",
                    source=self.agent_name,
                    stage="multimodal_enrichment",
                )
                resp = await self.call_llm(
                    messages=messages,
                    stream=stream,
                    source=self.agent_name,
                    stage="multimodal_enrichment",
                    temperature=self.default_temperature,
                    response_format={"type": "json_object"},
                )
        else:
            resp = await self.call_llm(
                messages=messages,
                stream=None,
                source=self.agent_name,
                temperature=self.default_temperature,
                response_format={"type": "json_object"},
            )

        data = self.parse_json_response(resp.content, fallback={})
        if not isinstance(data, dict):
            data = {}

        raw = data.get("suggestions") or []
        out: list[EnrichmentSuggestion] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                etype = EnrichmentType(item.get("type") or "diagram")
            except ValueError:
                etype = EnrichmentType.DIAGRAM
            try:
                conf = float(item.get("confidence") or 0.7)
            except (TypeError, ValueError):
                conf = 0.7
            conf = max(0.0, min(1.0, conf))
            out.append(
                EnrichmentSuggestion(
                    type=etype,
                    title=str(item.get("title") or ""),
                    content=str(item.get("content") or ""),
                    rationale=str(item.get("rationale") or ""),
                    confidence=conf,
                )
            )
        # Cap at 3
        out = out[:3]
        if stream is not None:
            await stream.observation(
                f"已推荐 {len(out)} 个多模态补充",
                source=self.agent_name,
                stage="multimodal_enrichment",
                metadata={
                    "types": [s.type.value for s in out],
                },
            )
        if not out:
            logger.debug("MultiModalEnrichment produced no suggestions")
        return out


__all__ = [
    "EnrichmentSuggestion",
    "EnrichmentType",
    "ENRICHMENT_OUTPUT_SCHEMA",
    "MultiModalEnrichmentAgent",
]
