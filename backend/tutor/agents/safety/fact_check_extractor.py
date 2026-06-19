"""FactCheckExtractor — pull 3-8 key factual claims from text (LLM)."""

from __future__ import annotations

import json

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.llm.base import LLMMessage, LLMRequest


EXTRACTOR_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": ["definition", "fact", "number", "comparison", "process", "other"],
                    },
                },
                "required": ["text"],
            },
        }
    },
    "required": ["claims"],
}


class FactCheckExtractor(BaseAgent):
    """Extract 3-8 verifiable factual claims from content."""

    module_name = "safety"
    agent_name = "fact_check_extractor"
    default_temperature = 0.2
    default_max_tokens = 1024

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        content: str,
        topic: str = "",
    ) -> list[str]:
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)
        user_msg = self.get_user_prompt(prompt_data).format(
            content=content[:6000],
            topic=topic or "(unspecified)",
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
            logger.warning(f"FactCheckExtractor LLM failed: {exc!r}")
            return []

        data = self.parse_json_response(resp.content, fallback={})
        if not isinstance(data, dict):
            return []
        raw = data.get("claims") or []
        out: list[str] = []
        for c in raw:
            if isinstance(c, str) and c.strip():
                out.append(c.strip())
            elif isinstance(c, dict) and isinstance(c.get("text"), str):
                out.append(c["text"].strip())
        return out[:8]


__all__ = ["FactCheckExtractor", "EXTRACTOR_OUTPUT_SCHEMA"]
