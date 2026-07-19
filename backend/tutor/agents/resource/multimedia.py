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
    MindMapOutlineItem,
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
        # 2026-06-21 fix: sanitize Mermaid DSL before storing it.
        # LLM-generated text often contains ``---``, ``===``,
        # unbalanced parens / brackets that crash the Mermaid
        # parser. We quote-wrap offending node labels so the
        # frontend renders them correctly.
        outline: list[MindMapOutlineItem] = []
        if mermaid_dsl:
            mermaid_dsl, outline = normalise_mindmap_dsl(mermaid_dsl)
        central_topic = str(data.get("central_topic") or topic)
        branch_count = int(data.get("branch_count") or _count_branches(mermaid_dsl))

        if not mermaid_dsl:
            mermaid_dsl = _build_minimal_mindmap(central_topic)
            mermaid_dsl, outline = normalise_mindmap_dsl(mermaid_dsl)

        payload = MindMapResource(
            mermaid_dsl=mermaid_dsl,
            central_topic=central_topic,
            branch_count=branch_count,
            outline=outline,
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

# Mermaid mindmap nodes: text after the indentation. A node is
# the text that follows the leading whitespace + optional shape
# marker (one of ``(``, ``[``, ``{``, ``)``, ``]``, ``}``).
# Lines containing ``---`` (three dashes) or ``===`` (three
# equals) inside node text break the Mermaid parser because
# those sequences are reserved for horizontal rules and
# separators within certain diagram types. We quote-wrap the
# offending text to preserve the LLM's intended content.
#
# **2026-06-22 fix (Task 8):** the previous regex
# ``[-=]{3,}|[()\[\]{}]|[:;]`` matched *any* parenthesis, so
# ``root((反向传播算法))`` (a perfectly valid Mermaid mindmap
# root) got wrapped to ``"root((反向传播算法))"``. The next line
# `` 基本概念`` (no parens) was NOT wrapped → parser then saw a
# mix of quoted and unquoted siblings at the same indent level,
# threw ``Expecting 'SPACELINE', 'NL', 'EOF', got 'NODE_ID'``.
#
# New strategy: only quote-wrap when the line text itself
# contains ``---`` or ``===`` (the actually-fatal sequences).
# Parens/brackets are allowed as long as they balance; we don't
# need to escape them.
_SANITIZE_BAD_LINE_PATTERN = re.compile(r"^(\s*)(.*)$", re.MULTILINE)
_SANITIZE_DANGEROUS_CHARS = re.compile(r"[-=]{3,}")
_MINDMAP_ID = r"[A-Za-z_][\w-]*"
_LEGAL_SHAPED_NODE_PATTERNS = (
    re.compile(rf"^(?P<id>{_MINDMAP_ID})\(\((?P<label>.*)\)\)$"),
    re.compile(rf"^(?P<id>{_MINDMAP_ID})\)\)(?P<label>.*)\(\($"),
    re.compile(rf"^(?P<id>{_MINDMAP_ID})\)(?P<label>.*)\($"),
    re.compile(rf"^(?P<id>{_MINDMAP_ID})\{{\{{(?P<label>.*)\}}\}}$"),
    re.compile(rf"^(?P<id>{_MINDMAP_ID})\[(?P<label>.*)\]$"),
    re.compile(rf"^(?P<id>{_MINDMAP_ID})\((?P<label>.*)\)$"),
)


def normalise_mindmap_dsl(dsl: str) -> tuple[str, list[MindMapOutlineItem]]:
    """Return valid Mermaid mindmap DSL plus an accessible text outline.

    Mermaid mindmaps accept shaped nodes such as ``root((topic))`` and
    ``node[\"label\"]`` but reject a bare quoted sibling. Every other label is
    emitted as a deterministic shaped node using its original source line.
    """
    lines = dsl.splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if line.strip().lower() == "mindmap"),
        None,
    )
    if header_index is None:
        return _sanitize_mermaid_dsl(dsl), []

    entries: list[tuple[int, int, str, str, tuple[str, str] | None]] = []
    for index, raw_line in enumerate(lines[header_index + 1 :], start=header_index + 2):
        if not raw_line.strip():
            continue
        whitespace = raw_line[: len(raw_line) - len(raw_line.lstrip(" \t"))]
        indent_width = sum(2 if char == "\t" else 1 for char in whitespace)
        text = raw_line[len(whitespace) :].strip()
        legal_node = _parse_legal_mindmap_node(text)
        entries.append((index, indent_width, text, _mindmap_label(text, legal_node), legal_node))

    if not entries:
        return "mindmap", []

    output = ["mindmap"]
    outline: list[MindMapOutlineItem] = []
    used_ids = {legal[0] for _, _, _, _, legal in entries if legal is not None}
    indentation_stack: list[tuple[int, int]] = []
    for line_number, indent_width, text, label, legal_node in entries:
        while indentation_stack and indent_width < indentation_stack[-1][0]:
            indentation_stack.pop()
        if not indentation_stack:
            depth = 0
        elif indent_width == indentation_stack[-1][0]:
            depth = indentation_stack[-1][1]
        else:
            depth = indentation_stack[-1][1] + 1
        if not indentation_stack or indent_width != indentation_stack[-1][0]:
            indentation_stack.append((indent_width, depth))
        indent = "  " * (depth + 1)
        if legal_node:
            node = text
        else:
            escaped = label.replace("\\", "\\\\").replace('"', '\\"')
            node_id = f"node_{line_number}"
            if node_id in used_ids:
                node_id = f"{node_id}_{line_number}"
                suffix = 2
                while node_id in used_ids:
                    node_id = f"node_{line_number}_{line_number}_{suffix}"
                    suffix += 1
            used_ids.add(node_id)
            node = f'{node_id}["{escaped}"]'
        output.append(indent + node)
        outline.append(MindMapOutlineItem(depth=depth, label=label))
    return "\n".join(output), outline


def _parse_legal_mindmap_node(text: str) -> tuple[str, str] | None:
    """Recognize supported mindmap node forms, not arbitrary Mermaid DSL.

    The supported grammar is ``id`` followed by square, rounded, circle,
    bang, cloud, or hexagon shape delimiters. Edges and directives are plain
    labels by design, so the normalizer never attempts to interpret them.
    """
    for pattern in _LEGAL_SHAPED_NODE_PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            return match.group("id"), match.group("label")
    return None


def _mindmap_label(text: str, legal_node: tuple[str, str] | None = None) -> str:
    """Extract readable label text without altering legal Mermaid nodes."""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if legal_node:
        label = legal_node[1]
        if len(label) >= 2 and label[0] == label[-1] and label[0] in {'"', "'"}:
            label = label[1:-1]
        return label.replace('\\"', '"').replace("\\\\", "\\")
    return text


def _sanitize_mermaid_dsl(dsl: str) -> str:
    """Clean a Mermaid DSL string so it doesn't crash the parser.

    For each line in the DSL:
      1. Skip the ``mindmap`` / ``graph`` header.
      2. If the line's text content contains ``---`` or ``===``,
         quote-wrap the entire text so Mermaid treats it as a
         literal label. (Parens/brackets are left alone — the
         ``mindmap`` parser is happy with ``((round))`` /
         ``[rect]`` shapes, and over-wrapping them causes
         sibling-mix parse errors.)
      3. **2026-06-22 fix (Task 8):** also normalize whitespace
         at the start of each line to consistent 2-space steps.
         LLMs frequently mix tabs, single spaces, and odd
         indentation, which Mermaid interprets as different
         hierarchy levels.
    """
    lines = dsl.splitlines()
    out: list[str] = []
    in_mindmap = False
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            out.append(raw_line)
            continue
        if stripped.lower().startswith("mindmap"):
            in_mindmap = True
            out.append("mindmap")
            continue
        if stripped.startswith("graph") or stripped.startswith("flowchart"):
            out.append(raw_line)
            continue
        # Compute indent depth in 2-space units (round to nearest).
        leading_ws = len(raw_line) - len(raw_line.lstrip(" \t"))
        # Count indent as number of leading spaces (treat tabs as 2 spaces).
        space_count = 0
        for ch in raw_line[:leading_ws]:
            space_count += 2 if ch == "\t" else 1
        if in_mindmap:
            depth = max(0, round(space_count / 2))
            indent = "  " * depth
        else:
            indent = raw_line[:leading_ws]
        text = raw_line[leading_ws:]
        # Already quoted — leave as-is.
        if (text.startswith('"') and text.endswith('"')) or (
            text.startswith("'") and text.endswith("'")
        ):
            out.append(indent + text)
            continue
        # If the text contains dangerous chars, wrap it in quotes.
        if _SANITIZE_DANGEROUS_CHARS.search(text):
            safe = text.replace('"', '\\"')
            out.append(f'{indent}"{safe}"')
            continue
        out.append(indent + text)
    return "\n".join(out)


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


__all__ = ["MultimediaAgent", "MINDMAP_OUTPUT_SCHEMA", "normalise_mindmap_dsl"]
