"""QuestionUnderstandingAgent — analyze a student's question.

Pipeline role (first step of :class:`TutoringCapability`):

    Question → QuestionUnderstandingAgent → QuestionUnderstanding
    → used by TutoringAgent + MultiModalEnrichmentAgent

The agent extracts:

- ``question_type`` — what kind of help is being asked
- ``concepts`` — concept IDs mentioned in the question
- ``difficulty`` — inferred complexity (1-5)
- ``student_intent`` — short free-form description of what they want
- ``confidence`` — extraction confidence 0-1
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.llm.base import LLMMessage, LLMRequest


class QuestionType(str, Enum):
    """High-level taxonomy of student questions."""

    CONCEPT = "concept"            # what is X?
    METHOD = "method"              # how do I do X?
    DEBUG = "debug"                # why doesn't my code work?
    COMPARISON = "comparison"      # what's the difference between X and Y?
    PRACTICE = "practice"          # give me an exercise on X
    META = "meta"                  # how should I learn X?
    OTHER = "other"


@dataclass
class QuestionUnderstanding:
    """Structured understanding of a student's question."""

    question_type: QuestionType = QuestionType.OTHER
    concepts: list[str] = field(default_factory=list)
    difficulty: int = 2
    student_intent: str = ""
    follow_up_questions: list[str] = field(default_factory=list)
    confidence: float = 0.5
    raw_question: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_type": self.question_type.value,
            "concepts": list(self.concepts),
            "difficulty": self.difficulty,
            "student_intent": self.student_intent,
            "follow_up_questions": list(self.follow_up_questions),
            "confidence": round(self.confidence, 3),
            "raw_question": self.raw_question,
        }


UNDERSTANDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question_type": {
            "type": "string",
            "enum": [t.value for t in QuestionType],
        },
        "concepts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concept IDs mentioned in the question",
        },
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
        "student_intent": {"type": "string"},
        "follow_up_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["question_type", "difficulty"],
}


class QuestionUnderstandingAgent(BaseAgent):
    """Understand a student's question."""

    module_name = "tutor"
    agent_name = "question_understanding"
    default_temperature = 0.2
    default_max_tokens = 1024

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
    ) -> QuestionUnderstanding:
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)

        # Provide profile context if available
        profile = context.metadata.get("learner_profile")
        profile_summary = ""
        if profile is not None and hasattr(profile, "to_summary"):
            profile_summary = profile.to_summary()
        else:
            profile_summary = json.dumps(profile or {}, ensure_ascii=False)

        user_msg = self.get_user_prompt(prompt_data).format(
            question=context.user_message,
            profile=profile_summary,
            history_length=len(context.history or []),
        )
        messages = self.build_messages(system=system, user=user_msg)

        if stream is not None:
            async with stream.stage("question_understanding", source=self.agent_name):
                await stream.thinking(
                    f"正在理解学生问题：{context.user_message[:80]}...",
                    source=self.agent_name,
                    stage="question_understanding",
                )
                resp = await self.call_llm(
                    messages=messages,
                    stream=stream,
                    source=self.agent_name,
                    stage="question_understanding",
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

        # Parse with safe defaults
        qt_str = str(data.get("question_type") or "other").lower()
        try:
            qt = QuestionType(qt_str)
        except ValueError:
            qt = QuestionType.OTHER
        difficulty = int(data.get("difficulty") or 2)
        difficulty = max(1, min(5, difficulty))
        concepts = [str(c) for c in (data.get("concepts") or []) if c]
        follow_ups = [
            str(q)
            for q in (data.get("follow_up_questions") or [])
            if q
        ]
        try:
            confidence = float(data.get("confidence") or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        understanding = QuestionUnderstanding(
            question_type=qt,
            concepts=concepts,
            difficulty=difficulty,
            student_intent=str(data.get("student_intent") or ""),
            follow_up_questions=follow_ups[:3],
            confidence=confidence,
            raw_question=context.user_message,
        )

        if stream is not None:
            await stream.observation(
                f"问题理解完成: type={qt.value}, concepts={concepts[:3]}, "
                f"difficulty={difficulty}",
                source=self.agent_name,
                stage="question_understanding",
                metadata={"understanding": understanding.to_dict()},
            )
        return understanding


__all__ = [
    "QuestionType",
    "QuestionUnderstanding",
    "QuestionUnderstandingAgent",
    "UNDERSTANDING_SCHEMA",
]
