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
import py_compile
import re
import tempfile
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.redaction import public_failure
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
    # Manim codegen produces long scripts (network viz, formulas,
    # multi-scene transitions).  4096 is too tight once JSON wrapping
    # is accounted for — the code gets truncated mid-statement.
    _codegen_max_tokens: int = 8192

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
        resp, data, _attempts = await self.call_llm_with_retry(
            messages=messages,
            stream=None,
            source=self.agent_name,
            temperature=0.6,
            response_format={"type": "json_object"},
        )
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
    ) -> tuple[str, str | None]:
        """Stage 2: translate storyboard → Manim Python code.

        Returns ``(code, finish_reason)``.

        **2026-06-22 fix (Task 6):** raised ``max_tokens`` from the
        default 4096 to ``_codegen_max_tokens=8192``. Multi-scene Manim
        scripts (e.g. backpropagation with forward + loss + backprop
        passes) easily exceed 4k tokens once the JSON wrapper is
        included — truncated code passes the surface ``class``/``def
        construct`` check but breaks Manim with a ``SyntaxError`` at
        the first unterminated line.  ``finish_reason`` is surfaced so
        the caller can refuse truncated code (``"length"``) even when
        the structural sniff test happens to pass.
        """
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data, section="coder", field="system")
        user_msg = self.get_user_prompt(prompt_data, section="coder", field="user").format(
            topic=topic,
            storyboard=json.dumps(storyboard, ensure_ascii=False, indent=2),
        )
        messages = self.build_messages(system=system, user=user_msg)
        resp, data, _attempts = await self.call_llm_with_retry(
            messages=messages,
            stream=None,
            source=self.agent_name,
            temperature=0.3,  # code gen wants determinism
            max_tokens=self._codegen_max_tokens,
            response_format={"type": "json_object"},
        )
        raw = getattr(resp, "content", "") or ""
        # data already parsed by retry wrapper; only fall back here if it's empty.
        if not isinstance(data, dict):
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
        # **2026-06-22 fix (Task 7):** the LLM sometimes returns the
        # code as a single-line string with literal ``\n`` (two chars)
        # instead of real newlines — particularly when ``response_format``
        # was bypassed or when the LLM chose to inline the whole script
        # to fit under its token budget.  ``_normalize_code_newlines``
        # detects this and converts escape sequences back to real
        # newlines so the chat viewer shows a properly-indented script.
        code = _normalize_code_newlines(_strip_code_fences(str(code)))
        return code, getattr(resp, "finish_reason", None)

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
                    "将剧本翻译为 Manim Python 代码 (stage 2/2)...",
                    source=self.agent_name,
                    stage="video_code_generation",
                )
                code, finish_reason = await self._stage_codegen(context, topic, storyboard)
        else:
            code, finish_reason = await self._stage_codegen(context, topic, storyboard)

        # **2026-06-22 fix (Task 6):** detect token-truncated code.
        # When the LLM hits ``max_tokens`` it stops mid-line, the JSON
        # wrapper is also cut, and ``parse_json_response`` either fails
        # or returns a half-complete code string.  This happens BEFORE
        # we waste a job on a doomed render.
        if finish_reason == "length" and (not code or len(code) < 200):
            logger.warning(
                f"manim_video: LLM hit max_tokens (finish_reason=length); "
                f"code_len={len(code)}, topic={topic!r}; returning failed"
            )
            return build_resource(
                type=ResourceType.VIDEO,
                title=f"{topic} — 视频生成失败",
                content=(
                    f"# {topic} — 视频生成失败\n\n"
                    f"**诊断**：代码生成阶段因 ``max_tokens`` 限制被截断（finish_reason=length）。\n\n"
                    f"**建议**：简化主题描述或拆分为多个子主题。\n"
                ),
                format_specific=_failed_video_payload(VideoResource(
                    manim_code="",
                    scene_class="",
                    render_status="failed",
                    render_error="codegen_truncated_by_max_tokens",
                ), "VIDEO_CODEGEN_TRUNCATED", "Video code generation was truncated"),
                difficulty=1,
                estimated_minutes=0,
                prerequisites=[],
                generated_by=[self.agent_name],
                confidence_score=0.0,
                topic=topic,
                tags=["video", "failed", "truncated"],
            )

        if not code or "class " not in code or "def construct" not in code:
            # **2026-06-22 fix (Task 3):** LLM returned empty/non-Python
            # or the salvaged code has no ``construct()`` entry point.
            # DO NOT replace this with a placeholder fallback scene.
            # Instead, surface the failure as a typed failed artifact so
            # the frontend can show "视频生成失败" with a retry action.
            logger.warning(
                f"manim_video: empty/invalid code from LLM for topic={topic!r}; "
                f"storyboard_keys={list(storyboard.keys()) if isinstance(storyboard, dict) else 'N/A'}; "
                "returning VIDEO_CODEGEN_FAILED"
            )
            # Build a failed resource — the quality reviewer will
            # classify this as an explicit failure.
            return build_resource(
                type=ResourceType.VIDEO,
                title=f"{topic} — 视频生成失败",
                content=(
                    f"# {topic} — 视频生成失败\n\n"
                    f"LLM 未能为此主题生成有效的 Manim 代码。\n\n"
                    f"**诊断**：{storyboard.get('_error', '代码生成阶段未返回有效 Python') if isinstance(storyboard, dict) else '故事板为空'}\n\n"
                    f"**建议**：重新提交请求或简化主题描述。\n"
                ),
                format_specific=_failed_video_payload(VideoResource(
                    manim_code="",
                    scene_class="",
                    render_status="failed",
                    render_error=(
                        storyboard.get("_error")
                        if isinstance(storyboard, dict)
                        else "LLM codegen returned empty/invalid code"
                    ),
                ), "VIDEO_CODEGEN_FAILED", "Video code generation failed"),
                difficulty=1,
                estimated_minutes=0,
                prerequisites=[],
                generated_by=[self.agent_name],
                confidence_score=0.0,
                topic=topic,
                tags=["video", "failed", "codegen_error"],
            )

        # **2026-06-22 fix (Task 3):** reject code containing
        # placeholder markers. The fallback scene is identifiable by
        # the strings ``Fallback scene`` or ``动画生成中`` in the
        # code itself. If we see these, the code IS the fallback
        # (from _stage_codegen's failed parse path) and should not
        # be published as a valid resource.
        if "Fallback scene" in code or "动画生成中" in code:
            logger.warning("manim_video: code still contains fallback markers")
            return build_resource(
                type=ResourceType.VIDEO,
                title=f"{topic} — 视频生成失败",
                content=f"# {topic} — 视频生成失败\n\nLLM 输出无效，生成了占位代码。\n",
                format_specific=_failed_video_payload(VideoResource(
                    manim_code="",
                    scene_class="MainScene",
                    render_status="failed",
                    render_error="LLM output contains fallback/placeholder markers",
                ), "VIDEO_CODEGEN_INVALID", "Video code generation failed"),
                difficulty=1,
                estimated_minutes=0,
                prerequisites=[],
                generated_by=[self.agent_name],
                confidence_score=0.0,
                topic=topic,
                tags=["video", "failed", "fallback_detected"],
            )

        # **2026-06-22 fix (Task 6):** py_compile the generated code.
        # Before this guard, truncated LLM output (e.g. code ending in
        # ``w2_lines = VGroup``) silently passed the surface ``class``/
        # ``def construct`` sniff test and was queued for rendering,
        # where ``manim`` would crash with a SyntaxError 30+ seconds
        # into the render.  We refuse it up front so the user sees a
        # typed failed artifact instead of waiting for a doomed job.
        syntax_error = _compile_check(code)
        if syntax_error:
            logger.warning(
                f"manim_video: generated code has SyntaxError "
                f"(topic={topic!r}, len={len(code)})"
            )
            return build_resource(
                type=ResourceType.VIDEO,
                title=f"{topic} — 视频生成失败",
                content=(
                    f"# {topic} — 视频生成失败\n\n"
                    f"**诊断**：LLM 生成的代码存在语法错误，无法被 Manim 渲染。\n\n"
                    f"**建议**：重新提交请求。\n"
                ),
                format_specific=_failed_video_payload(VideoResource(
                    manim_code="",
                    scene_class=_extract_scene_class(code) or "MainScene",
                    render_status="failed",
                    render_error="syntax_error: generated code is invalid",
                ), "VIDEO_CODE_SYNTAX_INVALID", "Generated video code is invalid"),
                difficulty=1,
                estimated_minutes=0,
                prerequisites=[],
                generated_by=[self.agent_name],
                confidence_score=0.0,
                topic=topic,
                tags=["video", "failed", "syntax_error"],
            )

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

        resource = build_resource(
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

        # **2026-07-08 fix (585f367d trace):** emit the VIDEO
        # ``RESOURCE`` event from INSIDE the agent so it lands in
        # the bus before we return. The capability layer
        # (``_generate_parallel``) also emits one, but that emit is
        # reached only after ``as_completed`` yields our task — and
        # if a 600s timeout fires while ``as_completed`` is blocked
        # on a slower sibling task, our emit never runs and the
        # video card silently disappears from the right pane even
        # though the manim code was already generated. Emitting
        # here guarantees the resource is on the wire the moment
        # the agent finishes, regardless of caller cancellation.
        if stream is not None:
            try:
                await stream.resource(
                    resource,
                    source=self.agent_name,
                    stage="video_code_generation",
                )
            except Exception:  # noqa: BLE001
                logger.debug("VIDEO_RESOURCE_EVENT_FAILED")

        return resource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_CODE_FENCE_RE = re.compile(r"^```(?:python)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _compile_check(code: str) -> str | None:
    """Best-effort syntax check via ``py_compile``.  Returns the
    error string on failure, ``None`` on success.

    **2026-06-22 fix (Task 6):** the structural sniff test
    (``class`` + ``def construct``) was insufficient — truncated LLM
    output often satisfies both markers (because ``class MainScene`` is
    near the top) while still failing at a downstream statement.  We
    now compile the full code in a sandbox file so unterminated
    brackets, bad indentation, and ``VGroup`` half-expressions get
    caught before the render job is queued.
    """
    if not code or len(code.strip()) < 10:
        return "code empty or too short"
    # py_compile writes a .pyc next to the source file by default; use
    # a tempfile so we don't litter the working dir.
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name
        try:
            py_compile.compile(tmp_path, doraise=True)
        finally:
            try:
                import os
                os.unlink(tmp_path)
            except OSError:
                pass
        return None
    except py_compile.PyCompileError:
        return "VIDEO_CODE_SYNTAX_INVALID"
    except Exception:  # noqa: BLE001
        return "VIDEO_CODE_COMPILE_CHECK_FAILED"


def _failed_video_payload(
    payload: VideoResource,
    code: str,
    message: str,
) -> dict[str, Any]:
    """Attach the common public failure contract to a failed video."""
    data = payload.model_dump()
    data["failure"] = public_failure(code, message, retryable=True)
    return data


def _extract_first_python_block(text: str) -> str:
    """Best-effort fallback: pull usable Python from raw LLM output.

    Tries, in order:
      1. `` ```python ... ``` `` fenced block.
      2. Manim-flavored raw text starting from ``from manim import *``
         or ``import manim`` (the canonical Manim CE preamble).
      3. ``class ... Scene`` line — used by Manim as the scene class.
      4. **General Python** fallback: look for ``def ``, ``class ``,
         or top-level ``import X`` (not ``import manim``) — this is
         the CodeSandbox salvage path. Without it, snippets like
         ``import numpy as np`` or ``def sigmoid(z):`` were silently
         dropped on the floor and the user saw "代码生成失败".

    All paths trim trailing JSON punctuation that the LLM sometimes
    leaks past the closing delimiter (e.g. ``"}``).
    """
    if not text:
        return ""
    # 1. Fenced block
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Helper: trim trailing JSON punctuation that the LLM leaked.
    def _trim_json_tail(s: str) -> str:
        s = s.rstrip()
        # Repeatedly strip " then } (the JSON value closes with both).
        for _ in range(3):
            if s.endswith('"') or s.endswith('}'):
                s = s[:-1].rstrip()
            else:
                break
        return s

    def _take_to_blank_or_json_tail(start_pos: int) -> str:
        rest = text[start_pos:]
        # Cut at the first blank line followed by JSON punctuation, or
        # any JSON-tail marker on its own line.
        rest = re.split(r'\n\s*\n\s*[}\]"\']', rest)[0]
        return _trim_json_tail(rest)

    # 2. Raw from manim import * — look for the canonical import
    #    and take everything after it until two blank lines or EOF.
    for marker in (r"from\s+manim\s+import\s+\*", r"import\s+manim"):
        m = re.search(marker, text)
        if m:
            rest = _take_to_blank_or_json_tail(m.start())
            if len(rest) > 40:
                return rest
    # 3. Look for class ... (Scene)
    m = re.search(r"class\s+\w+\s*\(\s*Scene\s*\)", text)
    if m:
        # Search backwards for the start of the code block
        before = text[:m.start()]
        import_pos = before.rfind("from manim")
        if import_pos < 0:
            import_pos = before.rfind("import manim")
        start = import_pos if import_pos >= 0 else m.start()
        rest = _take_to_blank_or_json_tail(start)
        if len(rest) > 40:
            return rest

    # 4. General Python fallback. The LLM often embeds the snippet
    #    inside a JSON string value (e.g. ``"code": "import numpy\n..."``)
    #    so the code is NOT at column 0 of any line — it follows a
    #    JSON quote. We therefore look for the pattern after any
    #    run of whitespace + optional JSON punctuation, NOT anchored
    #    to start-of-line.
    #
    #    We pick the EARLIEST match across all patterns (not the
    #    first pattern that hits) so a snippet like
    #    ``"class Sigmoid:\n    def __call__(...)\n"`` starts at
    #    ``class`` rather than at the inner ``def``.
    general_patterns = (
        r"def\s+\w+\s*\(",
        r"class\s+\w+\b",
        r"(?:from\s+\w[\w.]*\s+import|import\s+\w[\w.]*)",
    )
    earliest: tuple[int, int] | None = None
    for pat in general_patterns:
        m = re.search(pat, text)
        if m and (earliest is None or m.start() < earliest[0]):
            earliest = (m.start(), len(m.group(0)))
    if earliest is not None:
        match_start = earliest[0]
        # Walk back over JSON quote/colon/whitespace so we don't
        # start the recovered code mid-line. Cap the walkback at
        # the previous newline so we never merge two unrelated
        # lines.
        #
        # **2026-07-07 fix:** when the input is a single-line
        # ``{"code": "import math\\n..."}`` payload there is no
        # real newline before the code — the ``\n`` characters are
        # JSON-escaped sequences. Falling back to ``lookback_from=0``
        # returned the JSON wrapper as a code prefix (e.g.
        # ``{ "code": "import math\n...``), which then crashed in
        # the sandbox with a SyntaxError on the leading ``{``.
        # We now also look for the JSON string-opening ``"`` and
        # start the recovered snippet after it.
        prev_nl = text.rfind("\n", 0, match_start)
        if prev_nl >= 0:
            lookback_from = prev_nl + 1
        else:
            lookback_from = 0
            quote = text.rfind('"', 0, match_start)
            if quote >= 0:
                # ``quote`` is the JSON string-opening quote; the
                # code starts right after it. Allow ``quote ==
                # match_start - 1`` (the typical case).
                lookback_from = quote + 1
        stripped = text[lookback_from:match_start]
        cleaned = re.sub(r'^[\s":]+', '', stripped)
        real_start = lookback_from + len(stripped) - len(cleaned)
        rest = _take_to_blank_or_json_tail(real_start)
        # Lowered from 40 → 15 so single-line JSON-wrapped snippets
        # like ``{"code":"def f():\\n  pass"}`` (≈30 chars including
        # the JSON escape sequences) still pass the gate. The
        # keyword check below catches accidental garbage.
        if len(rest) > 15:
            # Guard against the walkback ever returning JSON punctuation
            # alone (e.g. ``: ``). Require the recovered snippet to
            # actually look like Python.
            stripped_start = rest.lstrip()
            if any(
                stripped_start.startswith(kw)
                for kw in ("def ", "class ", "import ", "from ", "if ", "for ", "while ", "with ", "@", "print(", "return ")
            ):
                return rest
    return ""


def _strip_code_fences(text: str) -> str:
    m = _CODE_FENCE_RE.match(text.strip())
    if m:
        return m.group(1).strip()
    return text.strip()


def _normalize_code_newlines(code: str) -> str:
    """**2026-06-22 fix (Task 7+8):** when the LLM returns code as a
    single-line string where newlines are encoded as the two-char
    sequence ``\\n`` (backslash + n) instead of real newline chars,
    the resulting code reads as one continuous line in the chat
    bubble / code viewer.

    This happens when ``response_format=json_object`` was bypassed
    (older prompt path) or when the LLM chose to inline the entire
    code string on one line to fit under its token budget.

    The function detects this case (``no real newlines`` +
    ``at least two literal \\n sequences``) and converts the escape
    sequences into real newlines.  Strings that already contain real
    newlines are returned unchanged.

    Also strips any trailing JSON punctuation (``"`` / ``}``) that
    the salvage path may have leaked past the closing delimiter.

    **Task 8** extends the sanity check from ``Manim``-specific
    keywords to a more general Python keyword set so this helper
    is reusable by ``CodeSandboxAgent``.
    """
    if not code:
        return code
    # Already multi-line — nothing to do.
    if "\n" in code:
        # But still strip trailing JSON punctuation.
        return code.rstrip().rstrip('"').rstrip("}").rstrip().rstrip('"').rstrip()
    # No real newlines at all.  If the code is a long sequence of
    # literal ``\n`` (two chars each), it's almost certainly LLM
    # output with un-escaped newlines.  Replace them.
    if "\\n" in code and code.count("\\n") >= 2:
        decoded = code.replace("\\n", "\n").replace("\\t", "\t").replace("\\\"", '"')
        # Sanity-check: must still look like Python code (either
        # Manim-specific OR general Python).
        has_python_kw = any(
            kw in decoded
            for kw in (
                "from manim",
                "import manim",
                "import ",
                "def ",
                "class ",
                "print(",
                "for ",
                "if ",
                " = ",
            )
        )
        if has_python_kw:
            return decoded.rstrip().rstrip('"').rstrip("}").rstrip()
    # No structural cues — return as-is, but trim obvious JSON tail.
    return code.rstrip().rstrip('"').rstrip("}").rstrip().rstrip('"').rstrip()


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
        '        self.wait(1)\n'
    )


__all__ = ["ManimVideoAgent", "STORYBOARD_SCHEMA"]
