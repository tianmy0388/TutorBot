"""ManimVideoAgent — generate Manim animations via two-stage AI.

Pipeline (inspired by ManimCat):

1. **Concept Designer** (LLM call 1):
   - Storyboard in a structured intermediate format
   - Defines scenes, key visuals, transitions, narration cues
2. **Code Generator** (LLM call 2):
   - Translates storyboard → Manim Community Edition Python code
   - Output: ``class MainScene(Scene): ...``
3. **StaticGuard** (Phase 5 — placeholder here):
   - ``py_compile`` check; abort if syntax errors
4. **Executor** (Phase 5 — placeholder here):
   - subprocess call to ``manim``; produces MP4

For MVP this agent returns a :class:`VideoResource` with
``render_status="pending"``. A separate background worker (Phase 5)
will pick up pending videos, render them, and patch the resource.

Design inspired by ManimCat's two-stage concept-designer → code-gen pipeline.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.resource_package.schema import (
    Resource,
    ResourceType,
    VideoResource,
    build_resource,
)


STORYBOARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "duration_seconds": {"type": "integer", "minimum": 5, "maximum": 600},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "narration": {"type": "string"},
                    "visuals": {"type": "array", "items": {"type": "string"}},
                    "duration_seconds": {"type": "integer"},
                },
                "required": ["name", "visuals"],
            },
        },
        "key_visual_elements": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "scenes"],
}


class ManimVideoAgent(BaseAgent):
    """Generate Manim Python code via a two-stage AI pipeline."""

    module_name = "resource"
    agent_name = "manim_video"
    default_temperature = 0.5
    default_max_tokens = 4096

    # ------------------------------------------------------------------
    # Two-stage LLM calls
    # ------------------------------------------------------------------

    async def _stage_design(
        self,
        context: UnifiedContext,
        topic: str,
        source_content: str,
    ) -> dict[str, Any]:
        """Stage 1: produce a structured storyboard."""
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data, section="designer", field="system")
        user_msg = self.get_user_prompt(prompt_data, section="designer", field="user").format(
            topic=topic,
            source_content=(source_content or "")[:4000],
        )
        messages = self.build_messages(system=system, user=user_msg)
        resp = await self.call_llm(
            messages=messages,
            stream=None,
            source=self.agent_name,
            temperature=0.6,
            response_format={"type": "json_object"},
        )
        data = self.parse_json_response(resp.content, fallback={})
        if not isinstance(data, dict):
            data = {}
        if not data:
            # Surface WHY the design step failed for the fallback renderer.
            data["_error"] = (
                f"stage1 empty (resp_len={len(getattr(resp, 'content', '') or '')}); "
                "storyboard LLM returned no JSON"
            )
        return data

    async def _stage_codegen(
        self,
        context: UnifiedContext,
        topic: str,
        storyboard: dict[str, Any],
    ) -> str:
        """Stage 2: translate storyboard → Manim Python code."""
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data, section="coder", field="system")
        user_msg = self.get_user_prompt(prompt_data, section="coder", field="user").format(
            topic=topic,
            storyboard=json.dumps(storyboard, ensure_ascii=False, indent=2),
        )
        messages = self.build_messages(system=system, user=user_msg)
        resp = await self.call_llm(
            messages=messages,
            stream=None,
            source=self.agent_name,
            temperature=0.3,  # code gen wants determinism
            response_format={"type": "json_object"},
        )
        raw = getattr(resp, "content", "") or ""
        data = self.parse_json_response(raw, fallback={})
        code = ""
        if isinstance(data, dict):
            code = data.get("manim_code") or data.get("code") or ""
        if not code:
            # LLM likely returned code in a fence without JSON wrapping.
            # Try to salvage by extracting the first ```python block.
            salvaged = _extract_first_python_block(raw)
            if salvaged:
                code = salvaged
                logger.info(
                    f"manim_video: salvaged code from non-JSON LLM output "
                    f"(topic={topic!r}, len={len(code)})"
                )
        return _strip_code_fences(str(code))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        topic: str,
        source_content: str = "",
        profile: dict[str, Any] | None = None,
    ) -> Resource:
        """Run the two-stage pipeline and return a :class:`VideoResource`."""
        if stream is not None:
            async with stream.stage("video_concept_design", source=self.agent_name):
                await stream.thinking(
                    f"为「{topic}」设计动画剧本 (stage 1/2)...",
                    source=self.agent_name,
                    stage="video_concept_design",
                )
                storyboard = await self._stage_design(context, topic, source_content)
        else:
            storyboard = await self._stage_design(context, topic, source_content)

        if stream is not None:
            async with stream.stage("video_code_generation", source=self.agent_name):
                await stream.thinking(
                    f"将剧本翻译为 Manim Python 代码 (stage 2/2)...",
                    source=self.agent_name,
                    stage="video_code_generation",
                )
                code = await self._stage_codegen(context, topic, storyboard)
        else:
            code = await self._stage_codegen(context, topic, storyboard)

        if not code or "class " not in code:
            # LLM returned empty / non-Python; surface a real explanation
            # in the produced scene and log the failure so the user can
            # see why their video is a placeholder.
            logger.warning(
                f"manim_video: empty/invalid code from LLM for topic={topic!r}; "
                f"storyboard_keys={list(storyboard.keys()) if isinstance(storyboard, dict) else 'N/A'}; "
                "using fallback scene"
            )
            code = _fallback_manim_code(topic, reason=storyboard.get("_error") if isinstance(storyboard, dict) else None)

        # Extract scene class name
        scene_class = _extract_scene_class(code) or "MainScene"

        duration = int(storyboard.get("duration_seconds") or 0)
        if duration <= 0:
            for s in storyboard.get("scenes") or []:
                duration += int(s.get("duration_seconds") or 0)
        if duration <= 0:
            duration = 30  # sensible default

        payload = VideoResource(
            manim_code=code,
            scene_class=scene_class,
            render_status="pending",
            duration_seconds=duration,
        )

        markdown = (
            f"# {topic} — 动画视频\n\n"
            f"**时长**：约 {duration} 秒\n"
            f"**状态**：渲染中（任务已排队）\n\n"
            f"## 剧本概要\n\n"
            f"{json.dumps(storyboard, ensure_ascii=False, indent=2)}\n\n"
            f"## Manim 源码（预览）\n\n"
            f"```python\n{code[:2000]}\n```\n"
        )

        if stream is not None:
            await stream.observation(
                f"Manim 视频脚本已生成 (scene={scene_class}, ~{duration}s)",
                source=self.agent_name,
                stage="video_code_generation",
                metadata={"render_status": "pending"},
            )

        return build_resource(
            type=ResourceType.VIDEO,
            title=f"{topic} — 动画视频",
            content=markdown,
            format_specific=payload.model_dump(),
            difficulty=3,
            estimated_minutes=max(1, duration // 60),
            prerequisites=[],
            generated_by=[self.agent_name, "ManimConceptDesigner", "ManimCodeGenerator"],
            confidence_score=0.7,
            topic=topic,
            tags=["video", "manim", "animation"],
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_CODE_FENCE_RE = re.compile(r"^```(?:python)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _extract_first_python_block(text: str) -> str:
    """Best-effort fallback: pull the first ```python ... ``` block from
    raw LLM output when JSON-mode parsing fails. Returns "" if nothing
    matches."""
    if not text:
        return ""
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _strip_code_fences(text: str) -> str:
    m = _CODE_FENCE_RE.match(text.strip())
    if m:
        return m.group(1).strip()
    return text.strip()


_SCENE_CLASS_RE = re.compile(r"class\s+(\w+)\s*\(\s*Scene\s*\)")


def _extract_scene_class(code: str) -> str | None:
    m = _SCENE_CLASS_RE.search(code)
    return m.group(1) if m else None


def _fallback_manim_code(topic: str, reason: str | None = None) -> str:
    """A trivial but meaningful Manim scene — used when the LLM fails
    to produce usable code. Renders the topic name + a brief concept
    card instead of a useless "(内容生成失败)" placeholder.

    The ``reason`` (if provided) is embedded in a tiny side note so the
    developer can debug from the rendered output.
    """
    safe = topic.replace('"', "'")[:40]
    note_line = ""
    if reason:
        short = reason.replace('"', "'")[:120]
        note_line = (
            f'        diag = Text("debug: {short}", font_size=18, color=YELLOW)\n'
            f'        diag.to_edge(DOWN)\n'
            f'        self.play(FadeIn(diag))\n'
        )
    return (
        'from manim import *\n\n'
        f'class MainScene(Scene):\n'
        f'    """Fallback scene for: {safe}."""\n'
        f'    def construct(self):\n'
        f'        title = Text("{safe}", font_size=44)\n'
        f'        subtitle = Text("(动画生成中 — LLM 输出待优化)", font_size=20, color=GREY_B)\n'
        f'        subtitle.next_to(title, DOWN, buff=0.4)\n'
        f'        self.play(Write(title))\n'
        f'        self.play(FadeIn(subtitle, shift=UP*0.2))\n'
        f'        self.wait(1.5)\n'
        f'        self.play(FadeOut(title), FadeOut(subtitle))\n'
        + note_line +
        f'        self.wait(1)\n'
    )


__all__ = ["ManimVideoAgent", "STORYBOARD_SCHEMA"]
