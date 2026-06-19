"""TutoringAgent — generate the main answer to a student's question.

Pipeline role:

    QuestionUnderstanding + Profile + RAG context
        → TutoringAgent
        → TutoringAnswer (structured Markdown)

The agent produces a 4-layer answer:

1. **TL;DR** — one-sentence direct answer
2. **Intuition** — informal explanation / analogy
3. **Principle** — formal definition / mechanism
4. **Example** — concrete worked example

Output: :class:`TutoringAnswer` with each layer accessible separately
so the frontend can render them progressively (stream-by-stream).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.agents.tutor.question_understanding import QuestionUnderstanding
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.llm.base import LLMMessage, LLMRequest


@dataclass
class TutoringAnswer:
    """Structured answer to a student's question."""

    tldr: str = ""
    intuition: str = ""
    principle: str = ""
    example: str = ""
    follow_up_suggestion: str = ""
    related_concepts: list[str] = field(default_factory=list)
    full_markdown: str = ""
    confidence: float = 0.7
    sources: list[str] = field(default_factory=list)  # RAG source paths

    def to_dict(self) -> dict[str, Any]:
        return {
            "tldr": self.tldr,
            "intuition": self.intuition,
            "principle": self.principle,
            "example": self.example,
            "follow_up_suggestion": self.follow_up_suggestion,
            "related_concepts": list(self.related_concepts),
            "full_markdown": self.full_markdown,
            "confidence": round(self.confidence, 3),
            "sources": list(self.sources),
        }

    def render_markdown(self) -> str:
        """Render a self-contained Markdown document."""
        parts: list[str] = []
        if self.tldr:
            parts.append(f"## 一句话回答\n\n{self.tldr}\n")
        if self.intuition:
            parts.append(f"## 直觉理解\n\n{self.intuition}\n")
        if self.principle:
            parts.append(f"## 原理详解\n\n{self.principle}\n")
        if self.example:
            parts.append(f"## 例子\n\n{self.example}\n")
        if self.follow_up_suggestion:
            parts.append(f"## 进一步学习\n\n{self.follow_up_suggestion}\n")
        if self.related_concepts:
            parts.append(
                "\n**相关概念**："
                + "、".join(f"`{c}`" for c in self.related_concepts)
            )
        return "\n".join(parts).strip()


TUTORING_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tldr": {"type": "string", "description": "一句话直接回答"},
        "intuition": {"type": "string", "description": "直觉/类比解释"},
        "principle": {"type": "string", "description": "原理/形式化说明"},
        "example": {"type": "string", "description": "具体例子（代码或场景）"},
        "follow_up_suggestion": {"type": "string"},
        "related_concepts": {
            "type": "array",
            "items": {"type": "string"},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["tldr", "principle"],
}


class TutoringAgent(BaseAgent):
    """Generate the main 4-layer answer."""

    module_name = "tutor"
    agent_name = "tutoring"
    default_temperature = 0.4
    default_max_tokens = 4096

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        understanding: QuestionUnderstanding,
        rag_context: str = "",
        profile: dict[str, Any] | None = None,
    ) -> TutoringAnswer:
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)
        user_msg = self.get_user_prompt(prompt_data).format(
            question=context.user_message,
            question_type=understanding.question_type.value,
            difficulty=understanding.difficulty,
            concepts=", ".join(understanding.concepts) or "(none specified)",
            student_intent=understanding.student_intent or "(not specified)",
            profile=json.dumps(profile or {}, ensure_ascii=False, indent=2),
            rag_context=rag_context or "(no RAG context retrieved)",
        )
        messages = self.build_messages(system=system, user=user_msg)

        if stream is not None:
            async with stream.stage("answer_generation", source=self.agent_name):
                await stream.thinking(
                    f"正在生成多层级解答 ({understanding.question_type.value})...",
                    source=self.agent_name,
                    stage="answer_generation",
                )
                resp = await self.call_llm(
                    messages=messages,
                    stream=stream,
                    source=self.agent_name,
                    stage="answer_generation",
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

        tldr = str(data.get("tldr") or "").strip()
        intuition = str(data.get("intuition") or "").strip()
        principle = str(data.get("principle") or "").strip()
        example = str(data.get("example") or "").strip()
        follow_up = str(data.get("follow_up_suggestion") or "").strip()
        related = [str(c) for c in (data.get("related_concepts") or []) if c]
        try:
            confidence = float(data.get("confidence") or 0.7)
        except (TypeError, ValueError):
            confidence = 0.7
        confidence = max(0.0, min(1.0, confidence))

        # Build markdown
        parts: list[str] = []
        if tldr:
            parts.append(f"## 一句话回答\n\n{tldr}")
        if intuition:
            parts.append(f"## 直觉理解\n\n{intuition}")
        if principle:
            parts.append(f"## 原理详解\n\n{principle}")
        if example:
            parts.append(f"## 例子\n\n{example}")
        if follow_up:
            parts.append(f"## 进一步学习\n\n{follow_up}")
        full_md = "\n\n".join(parts)

        answer = TutoringAnswer(
            tldr=tldr,
            intuition=intuition,
            principle=principle,
            example=example,
            follow_up_suggestion=follow_up,
            related_concepts=related,
            full_markdown=full_md,
            confidence=confidence,
            sources=_extract_source_paths(rag_context),
        )
        if stream is not None:
            await stream.observation(
                f"解答已生成 (confidence={confidence:.2f}, "
                f"layers={sum(bool(x) for x in [tldr, intuition, principle, example])})",
                source=self.agent_name,
                stage="answer_generation",
                metadata={"answer_chars": len(full_md)},
            )
        return answer


def _extract_source_paths(rag_context: str) -> list[str]:
    """Pull source file paths from a RAG context blob."""
    import re

    if not rag_context:
        return []
    return list(set(re.findall(r"\[([^\]]+\.md)\]", rag_context)))


__all__ = ["TutoringAgent", "TutoringAnswer", "TUTORING_OUTPUT_SCHEMA"]
