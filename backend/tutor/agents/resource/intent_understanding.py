"""IntentUnderstandingAgent — parse the user message into a structured intent.

Pipeline role (first step of :class:`ResourceGenerationCapability`):

    User message → IntentUnderstandingAgent → Intent(topic, scope, types)

The agent extracts:

- ``topic``      — what the student wants to learn
- ``scope``      — "deep_dive" / "overview" / "single_concept" / "comparison"
- ``resource_types`` — which of the 6 resource types to generate
                       (default: all)
- ``prerequisites`` — concepts the student thinks they know
- ``goal``        — free-form goal description
- ``confidence``  — extraction confidence 0-1

Returns an :class:`Intent` dataclass (cheap, in-memory).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.resource_package.schema import ResourceType


@dataclass
class Intent:
    """Structured intent extracted from a user message."""

    topic: str
    scope: str = "single_concept"  # "deep_dive" | "overview" | "single_concept" | "comparison"
    resource_types: list[ResourceType] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    goal: str = ""
    confidence: float = 0.5
    raw_message: str = ""

    def __post_init__(self) -> None:
        if not self.resource_types:
            self.resource_types = list(ResourceType)


INTENT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "scope": {
            "type": "string",
            "enum": ["deep_dive", "overview", "single_concept", "comparison"],
        },
        "resource_types": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [t.value for t in ResourceType],
            },
        },
        "prerequisites": {"type": "array", "items": {"type": "string"}},
        "goal": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["topic"],
}


class IntentUnderstandingAgent(BaseAgent):
    """Parse user message → :class:`Intent`."""

    module_name = "resource"
    agent_name = "intent_understanding"
    default_temperature = 0.2
    default_max_tokens = 1024

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
    ) -> Intent:
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)
        user_msg = self.get_user_prompt(prompt_data).format(
            user_message=context.user_message
        )
        messages = self.build_messages(system=system, user=user_msg)

        if stream is not None:
            async with stream.stage("intent_understanding", source=self.agent_name):
                await stream.thinking(
                    f"解析用户意图：{context.user_message[:80]}...",
                    source=self.agent_name,
                    stage="intent_understanding",
                )
                resp = await self.call_llm(
                    messages=messages,
                    stream=stream,
                    source=self.agent_name,
                    stage="intent_understanding",
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

        topic = str(data.get("topic") or "").strip()
        if not topic:
            # Fallback: use the whole user message as the topic
            topic = context.user_message.strip()[:80]

        scope = str(data.get("scope") or "single_concept")
        if scope not in {"deep_dive", "overview", "single_concept", "comparison"}:
            scope = "single_concept"

        types: list[ResourceType] = []
        for t in (data.get("resource_types") or []):
            try:
                types.append(ResourceType(t))
            except ValueError:
                continue
        if not types:
            types = [
                ResourceType.DOCUMENT,
                ResourceType.MINDMAP,
                ResourceType.EXERCISE,
                ResourceType.READING,
                ResourceType.VIDEO,
                ResourceType.CODE,
            ]

        prereqs = [str(p) for p in (data.get("prerequisites") or [])]
        goal = str(data.get("goal") or "")
        confidence = float(data.get("confidence") or 0.5)

        intent = Intent(
            topic=topic,
            scope=scope,
            resource_types=types,
            prerequisites=prereqs,
            goal=goal,
            confidence=max(0.0, min(1.0, confidence)),
            raw_message=context.user_message,
        )

        if stream is not None:
            await stream.observation(
                f"意图已解析: topic='{topic}', scope={scope}, types={len(types)}",
                source=self.agent_name,
                stage="intent_understanding",
                metadata={"confidence": intent.confidence},
            )
        else:
            logger.debug(f"Intent parsed: {intent}")

        return intent


# ---------------------------------------------------------------------------
# Fallback parser (no LLM) — keyword-based
# ---------------------------------------------------------------------------


def parse_intent_keyword(message: str) -> Intent:
    """Cheap keyword-based fallback when no LLM is available.

    Strategy:
    - Topic = first noun-phrase-ish token (everything before first "，" or "。")
    - Scope = "single_concept" unless "系统"/"深入"/"全面"/"overview"/"deep" is present
    - Resource types: default all 6
    """
    msg = message.strip()
    topic = msg.split("，")[0].split("。")[0].split(",")[0].strip()
    if not topic:
        topic = msg[:80] or "未指定主题"

    scope = "single_concept"
    lower = msg.lower()
    if any(k in msg for k in ["系统", "深入", "全面", "详细"]):
        scope = "deep_dive"
    if any(k in msg for k in ["概览", "总览", "overview", "summary"]):
        scope = "overview"
    if "对比" in msg or "比较" in msg or "comparison" in lower:
        scope = "comparison"

    return Intent(
        topic=topic,
        scope=scope,
        resource_types=list(ResourceType),
        goal=msg,
        confidence=0.3,
        raw_message=msg,
    )


__all__ = ["Intent", "INTENT_SCHEMA", "IntentUnderstandingAgent", "parse_intent_keyword"]
