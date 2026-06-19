"""QualityReviewerAgent — review a Resource for correctness, pedagogy, safety.

Pipeline role (last gate):
    Any resource → QualityReviewer → pass | revise | reject

The reviewer inspects a :class:`Resource` and returns a
:class:`ResourceReview` with one of three verdicts:

- ``PASS``    — resource is good as-is
- ``REVISE``  — resource needs changes (returned ``suggestions`` list)
- ``REJECT``  — resource has fundamental problems

The reviewer also emits a ``quality_score`` (0-1) which the orchestrator
can use to flag low-confidence resources for human review.

For MVP the reviewer is a single LLM call. Phase 3 can extend with
multi-lens review (peer + student) per idea.md.
"""

from __future__ import annotations

import json
from typing import Any

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.resource_package.schema import (
    Resource,
    ResourceReview,
    ReviewVerdict,
)


REVIEW_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "revise", "reject"]},
        "quality_score": {"type": "number", "minimum": 0, "maximum": 1},
        "issues": {"type": "array", "items": {"type": "string"}},
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "comments": {"type": "string"},
    },
    "required": ["verdict", "quality_score"],
}


class QualityReviewerAgent(BaseAgent):
    """Review a Resource and return a verdict + suggestions."""

    module_name = "resource"
    agent_name = "quality_reviewer"
    default_temperature = 0.2  # low — wants consistent evaluation
    default_max_tokens = 1024

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        resource: Resource,
    ) -> ResourceReview:
        """Review ``resource`` and return a :class:`ResourceReview`."""
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)
        user_msg = self.get_user_prompt(prompt_data).format(
            resource_type=resource.type.value,
            title=resource.title,
            content=resource.content[:4000],
            difficulty=resource.difficulty,
            format_specific=json.dumps(
                resource.format_specific, ensure_ascii=False
            )[:1500],
        )
        messages = self.build_messages(system=system, user=user_msg)

        if stream is not None:
            async with stream.stage("quality_review", source=self.agent_name):
                await stream.thinking(
                    f"正在审核「{resource.title}」({resource.type.value})...",
                    source=self.agent_name,
                    stage="quality_review",
                )
                resp = await self.call_llm(
                    messages=messages,
                    stream=stream,
                    source=self.agent_name,
                    stage="quality_review",
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

        verdict_str = str(data.get("verdict") or "pass").lower()
        try:
            verdict = ReviewVerdict(verdict_str)
        except ValueError:
            verdict = ReviewVerdict.PASS
        quality_score = float(data.get("quality_score") or 0.8)
        quality_score = max(0.0, min(1.0, quality_score))
        issues = [str(i) for i in (data.get("issues") or [])]
        suggestions = [str(s) for s in (data.get("suggestions") or [])]
        comments = str(data.get("comments") or "")

        review = ResourceReview(
            resource_id=resource.resource_id,
            verdict=verdict,
            quality_score=quality_score,
            issues=issues,
            suggestions=suggestions,
            reviewer=self.agent_name,
        )
        if stream is not None:
            await stream.observation(
                f"审核完成：verdict={verdict.value}, "
                f"score={quality_score:.2f}, "
                f"{len(issues)} issues, {len(suggestions)} suggestions",
                source=self.agent_name,
                stage="quality_review",
                metadata={"review_id": id(review)},
            )
            if comments:
                await stream.thinking(
                    f"评论：{comments}",
                    source=self.agent_name,
                    stage="quality_review",
                )
        return review


__all__ = ["QualityReviewerAgent", "REVIEW_OUTPUT_SCHEMA"]
