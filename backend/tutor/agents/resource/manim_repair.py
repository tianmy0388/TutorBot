"""Full-source Manim regeneration requested by the resource owner."""

from __future__ import annotations

import ast
from typing import Any

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.manim_render.executor import (
    RenderFailure,
    safe_failure_summary,
    sanitize_public_diagnostic,
    tail_lines,
)


class ManimRepairAgent(BaseAgent):
    module_name = "resource"
    agent_name = "manim_repair"
    default_temperature = 0.2
    default_max_tokens = 8192

    async def regenerate(
        self,
        context: UnifiedContext,
        failed_code: str,
        failure: RenderFailure,
        runtime: dict[str, str],
    ) -> str:
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)
        template = self.get_user_prompt(prompt_data)
        traceback_text = "\n".join(
            tail_lines("\n".join(failure.traceback_tail), limit=40)
        )[:6000]
        runtime_text = "\n".join(
            f"{sanitize_public_diagnostic(str(key))[:80]}="
            f"{sanitize_public_diagnostic(str(value))[:160]}"
            for key, value in sorted(runtime.items())
        )[:2000]
        user = template.format(
            failed_code=failed_code,
            error_code=sanitize_public_diagnostic(failure.error_code)[:120],
            failure_summary=safe_failure_summary(
                failure.summary,
                fallback="Manim rendering failed",
            ),
            traceback_tail=traceback_text,
            runtime_versions=runtime_text,
        )
        response = await self.call_llm(
            messages=self.build_messages(system=system, user=user),
            source=self.agent_name,
            stage="video_repair_generation",
            response_format={"type": "json_object"},
        )
        data = self.parse_json_response(response.content, strict=True)
        if not isinstance(data, dict) or set(data) != {"manim_code"}:
            raise ValueError("Manim repair must return one manim_code JSON field")
        code = data.get("manim_code")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("Manim repair returned empty manim_code")
        code = code.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
        if not _has_complete_main_scene(code):
            raise ValueError("Manim repair must return a complete MainScene")
        return code

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        **kwargs: Any,
    ) -> str:
        return await self.regenerate(context, **kwargs)


__all__ = ["ManimRepairAgent"]


def _has_complete_main_scene(code: str) -> bool:
    try:
        tree = ast.parse(code)
        compile(code, "<manim-repair>", "exec")
    except (SyntaxError, ValueError, TypeError):
        return False
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "MainScene":
            continue
        if not any(
            (
                isinstance(base, ast.Name) and base.id == "Scene"
            ) or (
                isinstance(base, ast.Attribute) and base.attr == "Scene"
            )
            for base in node.bases
        ):
            return False
        return any(
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child.name == "construct"
            for child in node.body
        )
    return False
