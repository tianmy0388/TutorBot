"""ExerciseGeneratorAgent — generate tiered exercises (basic/advanced/challenge).

Pipeline role:
    Pedagogy output → ExerciseGenerator → ExerciseResource

The agent receives the (pedagogy-improved) document content and asks the
LLM to design:

- 3+ *basic* questions (recall, single-concept)
- 2+ *advanced* questions (application, multi-step)
- 1+ *challenge* questions (synthesis, open-ended or code)

Question types supported: single_choice, multiple_choice, true_false,
fill_blank, short_answer, code. Difficulty tagged per question.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.resource_package.schema import (
    ExerciseOption,
    ExerciseQuestion,
    ExerciseResource,
    Resource,
    ResourceType,
    build_resource,
)

EXERCISE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "tier": {"type": "string", "enum": ["basic", "advanced", "challenge"]},
                    "type": {
                        "type": "string",
                        "enum": [
                            "single_choice",
                            "multiple_choice",
                            "true_false",
                            "fill_blank",
                            "short_answer",
                            "code",
                        ],
                    },
                    "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
                    "knowledge_point": {"type": "string"},
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "text": {"type": "string"},
                            },
                            "required": ["label", "text"],
                        },
                    },
                    "answer": {},
                    "accepted_answers": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "explanation": {"type": "string"},
                    "estimated_seconds": {"type": "integer"},
                    "code_spec": {
                        "type": ["object", "null"],
                        "properties": {
                            "language": {"const": "python"},
                            "starter_code": {"type": "string"},
                            "tests": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 50,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "call": {"type": "string"},
                                        "expected_json": {
                                            "type": [
                                                "null",
                                                "boolean",
                                                "number",
                                                "string",
                                                "array",
                                                "object",
                                            ]
                                        },
                                    },
                                    "required": ["name", "call", "expected_json"],
                                    "additionalProperties": False,
                                },
                            },
                            "time_limit_seconds": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 10,
                            },
                        },
                        "required": ["language", "starter_code", "tests"],
                        "additionalProperties": False,
                    },
                },
                "required": ["id", "type", "question", "answer"],
            },
        }
    },
    "required": ["questions"],
}


class ExerciseGeneratorAgent(BaseAgent):
    """Generate tiered exercise sets."""

    module_name = "resource"
    agent_name = "exercise_generator"
    default_temperature = 0.6
    default_max_tokens = 4096

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        topic: str,
        source_content: str = "",
        profile: dict[str, Any] | None = None,
        n_basic: int = 3,
        n_advanced: int = 2,
        n_challenge: int = 1,
    ) -> Resource:
        """Generate an exercise set for ``topic``.

        Returns a :class:`Resource` of ``type=exercise``.
        """
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)
        user_msg = self.get_user_prompt(prompt_data).format(
            topic=topic,
            source_content=(source_content or "")[:6000],
            n_basic=n_basic,
            n_advanced=n_advanced,
            n_challenge=n_challenge,
            profile=json.dumps(profile or {}, ensure_ascii=False, indent=2),
        )
        messages = self.build_messages(system=system, user=user_msg)

        if stream is not None:
            async with stream.stage("exercise_generation", source=self.agent_name):
                await stream.thinking(
                    f"为「{topic}」生成 {n_basic}+{n_advanced}+{n_challenge} 道分层习题...",
                    source=self.agent_name,
                    stage="exercise_generation",
                )
                resp = await self.call_llm(
                    messages=messages,
                    stream=stream,
                    source=self.agent_name,
                    stage="exercise_generation",
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

        raw_questions = data.get("questions") or []
        questions: list[ExerciseQuestion] = []
        for q in raw_questions:
            if not isinstance(q, dict):
                continue
            try:
                options = [
                    ExerciseOption(label=str(o.get("label", "")), text=str(o.get("text", "")))
                    for o in (q.get("options") or [])
                    if isinstance(o, dict)
                ]
                question_type = str(q.get("type", "single_choice"))
                question_text = str(q.get("question") or "")
                answer = q.get("answer")
                open_short_answer = question_type == "short_answer" and (
                    "用自己的话" in question_text
                    or str(answer).strip() == "(开放式回答)"
                )
                accepted_answers = (
                    [
                        item.strip()
                        for item in (q.get("accepted_answers") or [])
                        if isinstance(item, str) and item.strip()
                    ]
                    if question_type == "short_answer" and not open_short_answer
                    else []
                )
                eq = ExerciseQuestion(
                    id=str(q.get("id") or f"q-{len(questions) + 1}"),
                    type=question_type,
                    difficulty=int(q.get("difficulty") or 3),
                    knowledge_point=str(q.get("knowledge_point") or ""),
                    question=question_text,
                    options=options,
                    answer=answer,
                    accepted_answers=accepted_answers,
                    explanation=str(q.get("explanation") or ""),
                    estimated_seconds=int(q.get("estimated_seconds") or 60),
                    code_spec=q.get("code_spec"),
                )
                if eq.type == "code" and eq.code_spec is None:
                    raise ValueError("generated code question requires code_spec")
                questions.append(eq)
            except Exception:
                logger.warning("EXERCISE_ITEM_INVALID skipped=true")
                continue

        if not questions:
            # Fallback: single trivial question so the resource is non-empty
            questions = [
                ExerciseQuestion(
                    id="q-fallback",
                    type="short_answer",
                    difficulty=2,
                    question=f"请用自己的话总结「{topic}」的核心概念。",
                    answer="(开放式回答)",
                    accepted_answers=[],
                    explanation="这是开放性问题，没有标准答案。",
                    estimated_seconds=120,
                )
            ]

        # Difficulty breakdown
        tier_count: Counter[str] = Counter()
        for q in questions:
            # Try to find a "tier" hint — fall back to difficulty
            tier_count[str(q.difficulty)] += 1

        breakdown: dict[str, int] = {}
        for q in raw_questions:
            if not isinstance(q, dict):
                continue
            tier = str(q.get("tier", "")).strip()
            if tier:
                breakdown[tier] = breakdown.get(tier, 0) + 1

        payload = ExerciseResource(
            questions=questions,
            total_questions=len(questions),
            difficulty_breakdown=breakdown,
        )

        total_minutes = max(1, sum(q.estimated_seconds for q in questions) // 60)

        return build_resource(
            type=ResourceType.EXERCISE,
            title=f"{topic} — 练习题",
            content="\n\n".join(
                f"### Q{i + 1}. {q.question}\n\n"
                + (
                    "\n".join(f"- **{o.label}** {o.text}" for o in q.options)
                    if q.options
                    else ""
                )
                + f"\n\n**答案**：{q.answer}\n\n**解析**：{q.explanation}"
                for i, q in enumerate(questions)
            ),
            format_specific=payload.model_dump(),
            difficulty=3,
            estimated_minutes=total_minutes,
            prerequisites=[],
            generated_by=[self.agent_name],
            confidence_score=0.7,
            topic=topic,
            tags=["exercise", "quiz"],
        )


__all__ = ["ExerciseGeneratorAgent", "EXERCISE_OUTPUT_SCHEMA"]
