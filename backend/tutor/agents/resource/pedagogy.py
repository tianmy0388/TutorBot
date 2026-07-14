"""PedagogyAgent — restructure raw content for teaching.

Pipeline role:
    ContentExpert's resource → PedagogyAgent → improved version (same type)

The agent receives the raw document (Markdown + structured sections) and
applies teaching principles:

- Reorder for cognitive load (simple → complex)
- Add concrete examples (especially when content is abstract)
- Highlight key concepts (callouts, bold)
- Insert comprehension checks ("思考一下", "尝试：")
- Adjust vocabulary to the learner's level

Output is a new :class:`Resource` with the same ``type`` (DOCUMENT or
READING) but improved ``content`` + ``format_specific.sections``.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.resource_package.schema import (
    DocumentResource,
    Resource,
    ResourceType,
    build_resource,
)


PEDAGOGY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "key_points": {"type": "array", "items": {"type": "string"}},
                    "examples": {"type": "array", "items": {"type": "string"}},
                    "thinking_prompts": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "content"],
            },
        },
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
        "estimated_minutes": {"type": "integer", "minimum": 1},
        "prerequisites": {"type": "array", "items": {"type": "string"}},
        "teaching_notes": {"type": "string"},
    },
    "required": ["title", "sections"],
}


class PedagogyAgent(BaseAgent):
    """Restructure content for effective teaching."""

    module_name = "resource"
    agent_name = "pedagogy"
    default_temperature = 0.5
    # **2026-07-08 fix (187b2955 trace):** the previous default of 4096 +
    # ``call_llm_with_retry``'s exponential doubling (3 attempts × 2^2 =
    # 16 384 max tokens on the final attempt) caused single LLM calls to
    # stretch to 221s. Combined with four sequential pedagogy invocations
    # (content → pedagogy → 2nd pedagogy → reading-compilation), this
    # agent alone consumed ~60% of the 600s job budget. We now:
    #   * use a tighter 2048 token ceiling (output schema is bounded — no
    #     section needs >2k tokens), and
    #   * cap ``call_llm_with_retry`` at 2 attempts instead of the base 3,
    #     so the second attempt can only double to 4096 — well under any
    #     reasonable model latency budget.
    default_max_tokens = 2048
    default_max_attempts = 2

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        source_resource: Resource,
        profile: dict[str, Any] | None = None,
    ) -> Resource:
        """Return an improved version of ``source_resource`` (same type)."""
        if source_resource.type not in (ResourceType.DOCUMENT, ResourceType.READING):
            logger.warning(
                f"PedagogyAgent called with non-document resource type {source_resource.type}; "
                f"passing through unchanged."
            )
            return source_resource

        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)

        # Pass the raw markdown to the LLM
        raw_content = source_resource.content
        user_msg = self.get_user_prompt(prompt_data).format(
            topic=source_resource.topic or source_resource.title,
            profile=json.dumps(profile or {}, ensure_ascii=False, indent=2),
            raw_content=raw_content[:8000],  # truncate to fit context
            difficulty=source_resource.difficulty,
        )
        messages = self.build_messages(system=system, user=user_msg)

        if stream is not None:
            async with stream.stage("pedagogy_design", source=self.agent_name):
                await stream.thinking(
                    f"为「{source_resource.title}」重构教学结构...",
                    source=self.agent_name,
                    stage="pedagogy_design",
                )
                resp, data, _attempts = await self.call_llm_with_retry(
                    messages=messages,
                    stream=stream,
                    source=self.agent_name,
                    stage="pedagogy_design",
                    temperature=self.default_temperature,
                    max_attempts=self.default_max_attempts,
                    response_format={"type": "json_object"},
                )
        else:
            resp, data, _attempts = await self.call_llm_with_retry(
                messages=messages,
                stream=None,
                source=self.agent_name,
                temperature=self.default_temperature,
                max_attempts=self.default_max_attempts,
                response_format={"type": "json_object"},
            )

        if not isinstance(data, dict) or not data.get("sections"):
            # LLM failed — return source unchanged
            logger.warning(f"PedagogyAgent got empty response; passing through.")
            return source_resource

        sections = data.get("sections") or []
        norm_sections: list[dict[str, Any]] = []
        for s in sections:
            if not isinstance(s, dict):
                continue
            norm_sections.append(
                {
                    "title": str(s.get("title", "")),
                    "content": str(s.get("content", "")),
                    "key_points": [str(p) for p in (s.get("key_points") or [])],
                    "examples": [str(e) for e in (s.get("examples") or [])],
                    "thinking_prompts": [
                        str(p) for p in (s.get("thinking_prompts") or [])
                    ],
                }
            )

        markdown = _sections_to_markdown(
            title=str(data.get("title") or source_resource.title),
            summary=str(data.get("summary") or ""),
            sections=norm_sections,
            teaching_notes=str(data.get("teaching_notes") or ""),
        )

        difficulty = int(data.get("difficulty") or source_resource.difficulty)
        estimated_minutes = int(data.get("estimated_minutes") or source_resource.estimated_minutes)
        prerequisites = [str(p) for p in (data.get("prerequisites") or source_resource.prerequisites)]

        doc_payload = DocumentResource(
            sections=norm_sections,
            has_math=any("$" in s.get("content", "") for s in norm_sections),
            has_diagrams=any("```" in s.get("content", "") for s in norm_sections),
        )

        # Preserve original metadata + add teaching notes
        new_meta = dict(source_resource.metadata)
        new_meta["teaching_notes"] = str(data.get("teaching_notes") or "")
        new_meta["pedagogy_applied"] = True
        new_meta["original_resource_id"] = source_resource.resource_id

        # Combine generated_by
        generated_by = list(source_resource.generated_by) + [self.agent_name]

        return build_resource(
            type=source_resource.type,
            title=str(data.get("title") or source_resource.title),
            content=markdown,
            format_specific=doc_payload.model_dump(),
            difficulty=max(1, min(5, difficulty)),
            estimated_minutes=max(1, estimated_minutes),
            prerequisites=prerequisites,
            generated_by=generated_by,
            confidence_score=min(0.95, source_resource.confidence_score + 0.05),
            topic=source_resource.topic,
            tags=source_resource.tags,
            metadata=new_meta,
        )


def _sections_to_markdown(
    *,
    title: str,
    summary: str,
    sections: list[dict[str, Any]],
    teaching_notes: str = "",
) -> str:
    """Render structured sections to Markdown (with examples + prompts)."""
    out: list[str] = []
    out.append(f"# {title}\n")
    if summary:
        out.append(f"> {summary}\n")
    if teaching_notes:
        out.append(f"\n> 💡 **教学说明**：{teaching_notes}\n")
    for s in sections:
        st = s.get("title", "")
        sc = s.get("content", "")
        if st:
            out.append(f"\n## {st}\n")
        if sc:
            out.append(f"{sc}\n")
        key_points = s.get("key_points") or []
        if key_points:
            out.append("\n**🎯 关键点：**\n")
            for p in key_points:
                out.append(f"- {p}")
            out.append("")
        examples = s.get("examples") or []
        if examples:
            out.append("\n**📖 示例：**\n")
            for e in examples:
                out.append(f"- {e}")
            out.append("")
        prompts = s.get("thinking_prompts") or []
        if prompts:
            out.append("\n**🤔 思考一下：**\n")
            for p in prompts:
                out.append(f"> {p}")
            out.append("")
    return "\n".join(out).strip()


__all__ = ["PedagogyAgent", "PEDAGOGY_OUTPUT_SCHEMA"]
