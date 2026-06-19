"""MultimediaAgent — produce mind maps and tables.

Pipeline role:
    Pedagogy output → MultimediaAgent → MindMapResource (Mermaid DSL)

The agent decides which concepts benefit from visual representation and
emits a Mermaid ``mindmap`` DSL (or ``graph TD``) ready for frontend
rendering. We also use Mermaid for comparison tables when useful.

Future expansion: SVG diagrams, interactive flowcharts, knowledge-graph
visualisations — kept simple for MVP.
"""

from __future__ import annotations

import json
import re
from typing import Any

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.resource_package.schema import (
    MindMapResource,
    Resource,
    ResourceType,
    build_resource,
)


MINDMAP_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "central_topic": {"type": "string"},
        "mermaid_dsl": {"type": "string"},
        "branch_count": {"type": "integer", "minimum": 1},
    },
    "required": ["central_topic", "mermaid_dsl"],
}


class MultimediaAgent(BaseAgent):
    """Generate Mermaid mind maps + comparison tables."""

    module_name = "resource"
    agent_name = "multimedia"
    default_temperature = 0.3
    default_max_tokens = 2048

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        topic: str,
        source_content: str = "",
        profile: dict[str, Any] | None = None,
    ) -> Resource:
        """Return a Mermaid mind map for ``topic``."""
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)
        user_msg = self.get_user_prompt(prompt_data).format(
            topic=topic,
            source_content=(source_content or "")[:4000],
            profile=json.dumps(profile or {}, ensure_ascii=False, indent=2),
        )
        messages = self.build_messages(system=system, user=user_msg)

        if stream is not None:
            async with stream.stage("mindmap_generation", source=self.agent_name):
                await stream.thinking(
                    f"为「{topic}」生成思维导图...",
                    source=self.agent_name,
                    stage="mindmap_generation",
                )
                resp = await self.call_llm(
                    messages=messages,
                    stream=stream,
                    source=self.agent_name,
                    stage="mindmap_generation",
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

        mermaid_dsl = str(data.get("mermaid_dsl") or "").strip()
        # Strip wrapping ```mermaid fences if present
        mermaid_dsl = _strip_mermaid_fences(mermaid_dsl)
        central_topic = str(data.get("central_topic") or topic)
        branch_count = int(data.get("branch_count") or _count_branches(mermaid_dsl))

        if not mermaid_dsl:
            mermaid_dsl = _build_minimal_mindmap(central_topic)

        payload = MindMapResource(
            mermaid_dsl=mermaid_dsl,
            central_topic=central_topic,
            branch_count=branch_count,
        )

        markdown = (
            f"# {central_topic} — 思维导图\n\n"
            f"```{_mermaid_block_type(mermaid_dsl)}\n"
            f"{mermaid_dsl}\n"
            f"```\n\n"
            f"共 **{branch_count}** 个分支。"
        )

        return build_resource(
            type=ResourceType.MINDMAP,
            title=f"{central_topic} — 思维导图",
            content=markdown,
            format_specific=payload.model_dump(),
            difficulty=2,
            estimated_minutes=2,
            prerequisites=[],
            generated_by=[self.agent_name],
            confidence_score=0.8,
            topic=topic,
            tags=["mindmap", "diagram", "mermaid"],
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_FENCE_RE = re.compile(r"^```(?:mermaid)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _strip_mermaid_fences(text: str) -> str:
    """Remove ```mermaid ... ``` wrappers."""
    m = _FENCE_RE.match(text.strip())
    if m:
        return m.group(1).strip()
    return text.strip()


def _count_branches(dsl: str) -> int:
    """Count top-level children in a Mermaid mindmap (rough heuristic)."""
    # Each non-indented line under "mindmap" is a top-level branch
    lines = [ln for ln in dsl.splitlines() if ln.strip()]
    in_mindmap = False
    count = 0
    for ln in lines:
        s = ln.strip()
        if s.lower().startswith("mindmap"):
            in_mindmap = True
            continue
        if in_mindmap and not ln.startswith((" ", "\t")):
            count += 1
    return count


def _mermaid_block_type(dsl: str) -> str:
    """Infer whether DSL is a ``mindmap`` or ``graph`` etc."""
    first = dsl.strip().splitlines()[0].lower() if dsl.strip() else ""
    if first.startswith("mindmap"):
        return "mermaid"
    if first.startswith("graph") or first.startswith("flowchart"):
        return "mermaid"
    return "mermaid"


def _build_minimal_mindmap(central: str) -> str:
    """Fallback mind map when LLM fails."""
    return (
        "mindmap\n"
        f"  ({central})\n"
        "    概述\n"
        "    核心概念\n"
        "    应用场景\n"
        "    学习路径\n"
    )


__all__ = ["MultimediaAgent", "MINDMAP_OUTPUT_SCHEMA"]
