"""ContentExpertAgent — generate initial knowledge-accurate content.

Pipeline role (per idea.md):
    UserRequest + Profile → ContentExpert → Pedagogy → ... → QualityReview

The agent pulls (optional) RAG snippets from the active knowledge base,
then asks the LLM to draft a Markdown document with a stable section
structure. Output is wrapped in a :class:`Resource` (type=DOCUMENT) and
returned to the caller.

The caller (ResourceGenerationCapability) feeds this resource into the
PedagogyAgent for restructuring, then routes it through type-specific
agents for non-document resource types.
"""

from __future__ import annotations

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


CONTENT_OUTPUT_SCHEMA: dict[str, Any] = {
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
                },
                "required": ["title", "content"],
            },
        },
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
        "estimated_minutes": {"type": "integer", "minimum": 1},
        "prerequisites": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "has_math": {"type": "boolean"},
        "has_diagrams": {"type": "boolean"},
    },
    "required": ["title", "sections"],
}


class ContentExpertAgent(BaseAgent):
    """Generate knowledge-accurate initial content for a topic."""

    module_name = "resource"
    agent_name = "content_expert"
    default_temperature = 0.4
    default_max_tokens = 4096

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        topic: str,
        profile: dict[str, Any] | None = None,
        rag_snippets: list[str] | None = None,
    ) -> Resource:
        """Generate a ``type=DOCUMENT`` resource for ``topic``."""
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)
        user_msg = self.get_user_prompt(prompt_data).format(
            topic=topic,
            profile=json.dumps(profile or {}, ensure_ascii=False, indent=2),
            rag_context="\n\n---\n\n".join(rag_snippets or []) or "(无 RAG 上下文)",
        )
        messages = self.build_messages(system=system, user=user_msg)

        if stream is not None:
            async with stream.stage("content_generation", source=self.agent_name):
                await stream.thinking(
                    f"正在为「{topic}」生成初版内容...",
                    source=self.agent_name,
                    stage="content_generation",
                )
                resp, data, _attempts = await self.call_llm_with_retry(
                    messages=messages,
                    stream=stream,
                    source=self.agent_name,
                    stage="content_generation",
                    temperature=self.default_temperature,
                    response_format={"type": "json_object"},
                )
        else:
            resp, data, _attempts = await self.call_llm_with_retry(
                messages=messages,
                stream=None,
                source=self.agent_name,
                temperature=self.default_temperature,
                response_format={"type": "json_object"},
            )

        if not isinstance(data, dict):
            data = {}

        sections = data.get("sections") or []
        if not isinstance(sections, list):
            sections = []
        # Normalise sections
        norm_sections: list[dict[str, Any]] = []
        for s in sections:
            if not isinstance(s, dict):
                continue
            norm_sections.append(
                {
                    "title": str(s.get("title", "")),
                    "content": str(s.get("content", "")),
                    "key_points": [str(p) for p in (s.get("key_points") or [])],
                }
            )

        markdown = _sections_to_markdown(
            title=str(data.get("title") or topic),
            summary=str(data.get("summary") or ""),
            sections=norm_sections,
        )

        difficulty = int(data.get("difficulty") or 3)
        estimated_minutes = int(data.get("estimated_minutes") or 10)
        prerequisites = [str(p) for p in (data.get("prerequisites") or [])]
        tags = [str(t) for t in (data.get("tags") or [])]

        # Validate per-type model (raises if bad — but we control the data)
        doc_payload = DocumentResource(
            sections=norm_sections,
            has_math=bool(data.get("has_math", False)),
            has_diagrams=bool(data.get("has_diagrams", False)),
        )

        resource = build_resource(
            type=ResourceType.DOCUMENT,
            title=str(data.get("title") or topic),
            content=markdown,
            format_specific=doc_payload.model_dump(),
            difficulty=max(1, min(5, difficulty)),
            estimated_minutes=max(1, estimated_minutes),
            prerequisites=prerequisites,
            generated_by=[self.agent_name],
            confidence_score=0.75,
            topic=topic,
            tags=tags,
            metadata={
                "summary": str(data.get("summary") or ""),
                "raw_response_chars": len(resp.content),
            },
        )
        if stream is not None:
            await stream.observation(
                f"初版内容已生成 ({len(norm_sections)} 章节, "
                f"{resource.estimated_minutes} 分钟)",
                source=self.agent_name,
                stage="content_generation",
                metadata={"resource_id": resource.resource_id},
            )
        return resource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sections_to_markdown(*, title: str, summary: str, sections: list[dict[str, Any]]) -> str:
    """Render a structured sections dict to Markdown."""
    out: list[str] = []
    out.append(f"# {title}\n")
    if summary:
        out.append(f"> {summary}\n")
    for s in sections:
        st = s.get("title", "")
        sc = s.get("content", "")
        if st:
            out.append(f"\n## {st}\n")
        if sc:
            out.append(f"{sc}\n")
        key_points = s.get("key_points") or []
        if key_points:
            out.append("\n**关键点：**\n")
            for p in key_points:
                out.append(f"- {p}")
            out.append("")
    return "\n".join(out).strip()


# Import json here to avoid unused-import warning at module top
import json  # noqa: E402

__all__ = ["ContentExpertAgent", "CONTENT_OUTPUT_SCHEMA"]
