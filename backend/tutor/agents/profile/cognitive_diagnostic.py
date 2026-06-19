"""CognitiveDiagnosticAgent — generate targeted probing questions.

Strategy: based on the current profile + the user's last message, pick
1-3 concepts whose mastery is uncertain and generate a short diagnostic
question for each. The goal is to *refine* the profile, not to teach.

This agent is a "generator" — it does not store anything; the orchestrator
collects the answers and feeds them back to the FeatureExtractorAgent.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.learner_profile.builder import ProfileBuilder, get_profile_builder


class CognitiveDiagnosticAgent(BaseAgent):
    """Generate diagnostic probing questions."""

    module_name = "profile"
    agent_name = "cognitive_diagnostic"
    default_temperature = 0.5
    default_max_tokens = 1024

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
    ) -> list[dict[str, Any]]:
        """Return 1-3 diagnostic question dicts.

        Each dict has keys: ``concept``, ``question``, ``why`` (rationale),
        ``difficulty`` (1-5).
        """
        profile = context.metadata.get("learner_profile")
        if profile is None:
            builder: ProfileBuilder = get_profile_builder()
            profile = await builder.get(context.user_id)

        weak = profile.weak_concepts(threshold=0.5)
        target_concepts = weak[:3] if weak else list(profile.knowledge_map.scores.keys())[:3]

        if not target_concepts:
            # Nothing to diagnose — return one opener
            return [
                {
                    "concept": "general",
                    "question": "你目前的学习目标是？（比如：考试/项目/兴趣探索）",
                    "why": "用于确定目标类型与紧迫度",
                    "difficulty": 1,
                }
            ]

        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)
        user_msg = self.get_user_prompt(prompt_data).format(
            user_message=context.user_message,
            concepts=", ".join(target_concepts),
            profile_summary=json.dumps(profile.to_summary(), ensure_ascii=False),
        )
        messages = self.build_messages(system=system, user=user_msg)

        if stream is not None:
            async with stream.stage("cognitive_diagnosis", source=self.agent_name):
                await stream.thinking(
                    f"为目标概念 {target_concepts} 生成诊断问题...",
                    source=self.agent_name,
                    stage="cognitive_diagnosis",
                )
                resp = await self.call_llm(
                    messages=messages,
                    stream=stream,
                    source=self.agent_name,
                    stage="cognitive_diagnosis",
                    response_format={"type": "json_object"},
                )
        else:
            resp = await self.call_llm(
                messages=messages,
                stream=None,
                source=self.agent_name,
                response_format={"type": "json_object"},
            )

        questions = self._parse_questions(resp.content)
        if not questions:
            # Fallback: simple template questions
            questions = [
                {
                    "concept": c,
                    "question": f"你能用自己的话解释一下「{c}」吗？",
                    "why": "了解你的理解深度",
                    "difficulty": 2,
                }
                for c in target_concepts[:3]
            ]
        return questions

    def _parse_questions(self, content: str) -> list[dict[str, Any]]:
        data = self.parse_json_response(content, fallback={})
        if isinstance(data, dict):
            qs = data.get("questions")
            if isinstance(qs, list):
                # Normalize entries
                out: list[dict[str, Any]] = []
                for q in qs:
                    if not isinstance(q, dict):
                        continue
                    if "question" not in q:
                        continue
                    out.append(
                        {
                            "concept": str(q.get("concept") or "general"),
                            "question": str(q["question"]),
                            "why": str(q.get("why") or ""),
                            "difficulty": int(q.get("difficulty") or 2),
                        }
                    )
                return out
        return []


__all__ = ["CognitiveDiagnosticAgent"]
