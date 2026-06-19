"""FeatureExtractorAgent — extract structured features from student messages.

Uses the LLM to parse free-form Chinese / English dialogue into the
6-dimension feature schema consumed by :class:`ProfileBuilder.ingest_signal`.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.learner_profile.builder import DialogueSignal
from tutor.services.learner_profile.schema import LearnerProfile


# Strict output schema (for prompt injection & validation)
FEATURE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "major": {"type": "string", "description": "专业 (例如 计算机科学)"},
        "level": {
            "type": "string",
            "enum": ["high_school", "undergraduate", "graduate", "phd", "professional"],
        },
        "knowledge": {
            "type": "object",
            "description": "{concept: mastery_0_to_1}",
            "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "cognitive_style": {
            "type": "string",
            "enum": [
                "visual",
                "verbal",
                "deductive",
                "inductive",
                "active",
                "reflective",
            ],
        },
        "motivation": {
            "type": "object",
            "properties": {
                "goal_type": {
                    "type": "string",
                    "enum": [
                        "exam_prep",
                        "project_build",
                        "skill_upgrade",
                        "curiosity",
                        "research",
                        "competition",
                    ],
                },
                "urgency": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "critical"],
                },
                "self_efficacy": {"type": "number", "minimum": 0, "maximum": 1},
                "goal_description": {"type": "string"},
            },
        },
        "learning_pace": {
            "type": "object",
            "properties": {
                "avg_session_duration_min": {"type": "integer"},
                "preferred_chunk_size_min": {"type": "integer"},
                "daily_time_budget_min": {"type": "integer"},
                "sessions_per_week": {"type": "integer"},
            },
        },
        "modality": {
            "type": "object",
            "description": "Modality preference scores in [0,1]",
            "properties": {
                "text": {"type": "number"},
                "video": {"type": "number"},
                "interactive": {"type": "number"},
                "diagram": {"type": "number"},
                "code": {"type": "number"},
                "audio": {"type": "number"},
                "exercise": {"type": "number"},
            },
        },
        "metadata": {
            "type": "object",
            "description": "Any other free-form facts (course, school, ...)",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Agent's confidence in the extraction (0-1)",
        },
    },
}


class FeatureExtractorAgent(BaseAgent):
    """Extract 6-dim features from natural-language student input."""

    module_name = "profile"
    agent_name = "feature_extractor"
    default_temperature = 0.3  # low temp → more deterministic extraction
    default_max_tokens = 2048

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
    ) -> DialogueSignal:
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)

        history_text = ""
        if context.history:
            history_text = "\n\n".join(
                f"{turn.get('role', 'user')}: {turn.get('content', '')}"
                for turn in context.history[-6:]
            )

        user_msg = (
            self.get_user_prompt(prompt_data).format(
                user_message=context.user_message,
                history=history_text or "(无历史对话)",
            )
        )

        messages = self.build_messages(system=system, user=user_msg)

        if stream is not None:
            async with stream.stage("feature_extraction", source=self.agent_name):
                await stream.thinking(
                    "正在从对话中抽取学习特征...",
                    source=self.agent_name,
                    stage="feature_extraction",
                )
                resp = await self.call_llm(
                    messages=messages,
                    stream=stream,
                    source=self.agent_name,
                    stage="feature_extraction",
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

        features = self.parse_json_response(resp.content, fallback={})
        if not isinstance(features, dict):
            logger.warning(f"Feature extractor got non-dict response: {type(features)}")
            features = {}

        # Inject major/level into metadata for downstream use
        meta = features.setdefault("metadata", {})
        if isinstance(meta, dict):
            if "major" in features:
                meta["major"] = features["major"]
            if "level" in features:
                meta["level"] = features["level"]

        confidence = float(features.get("confidence") or 0.5)
        confidence = max(0.0, min(1.0, confidence))

        signal = DialogueSignal(
            raw_text=context.user_message,
            extracted_features=features,
            confidence=confidence,
        )
        if stream is not None:
            await stream.observation(
                f"抽取完成 (confidence={confidence:.2f}): "
                f"knowledge={len(features.get('knowledge') or {})}, "
                f"style={features.get('cognitive_style', '?')}",
                source=self.agent_name,
                stage="feature_extraction",
                metadata={"features": features},
            )
        return signal


__all__ = ["FeatureExtractorAgent", "FEATURE_SCHEMA"]
