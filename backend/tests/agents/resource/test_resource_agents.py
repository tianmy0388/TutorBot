"""Tests for the 7 resource-generation agents (with mocked LLM)."""

from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import MagicMock

import pytest
from tutor.agents.resource.code_sandbox import CodeSandboxAgent
from tutor.agents.resource.content_expert import ContentExpertAgent
from tutor.agents.resource.exercise_generator import ExerciseGeneratorAgent
from tutor.agents.resource.manim_video import ManimVideoAgent
from tutor.agents.resource.multimedia import MultimediaAgent
from tutor.agents.resource.pedagogy import PedagogyAgent
from tutor.agents.resource.quality_reviewer import QualityReviewerAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.resource_package.schema import (
    Resource,
    ResourceType,
    ReviewVerdict,
)


def _mock_llm(*responses: str, finish_reasons: tuple[str | None, ...] | None = None):
    """Mock LLM provider returning successive responses."""
    from tutor.services.llm.base import LLMResponse

    queue = list(responses)
    reasons = list(finish_reasons) if finish_reasons else []

    llm = MagicMock()
    llm.model = "mock-model"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(req):
        content = queue.pop(0) if queue else "{}"
        finish_reason = reasons.pop(0) if reasons else "stop"
        return LLMResponse(content=content, model="mock-model", finish_reason=finish_reason)

    llm.call = call
    return llm


# ---------------------------------------------------------------------------
# ContentExpert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_expert_generates_document():
    llm = _mock_llm(json.dumps({
        "title": "LSTM 基础",
        "summary": "理解 LSTM 的核心机制",
        "sections": [
            {"title": "什么是 LSTM", "content": "LSTM 是...", "key_points": ["长短期记忆"]},
            {"title": "门控机制", "content": "包含遗忘门、输入门、输出门", "key_points": ["三个门"]},
        ],
        "difficulty": 3,
        "estimated_minutes": 12,
        "prerequisites": ["RNN"],
        "tags": ["deep_learning"],
        "has_math": True,
        "has_diagrams": False,
    }, ensure_ascii=False))
    agent = ContentExpertAgent(llm=llm)
    ctx = UnifiedContext(user_message="讲讲 LSTM")
    resource = await agent.process(ctx, topic="LSTM")
    assert resource.type == ResourceType.DOCUMENT
    assert "LSTM" in resource.title
    assert "门控机制" in resource.content
    assert resource.estimated_minutes == 12
    assert resource.difficulty == 3
    assert "RNN" in resource.prerequisites


@pytest.mark.asyncio
async def test_content_expert_handles_invalid_json():
    llm = _mock_llm("not JSON at all")
    agent = ContentExpertAgent(llm=llm)
    ctx = UnifiedContext(user_message="x")
    resource = await agent.process(ctx, topic="X")
    # Falls back to empty sections but still produces a resource
    assert resource.type == ResourceType.DOCUMENT
    assert resource.title == "X"


# ---------------------------------------------------------------------------
# Pedagogy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pedagogy_improves_document():
    source = Resource(
        type=ResourceType.DOCUMENT,
        title="LSTM",
        content="# LSTM\n\nLSTM 是...",
        topic="LSTM",
        difficulty=3,
        estimated_minutes=10,
        format_specific={"sections": [{"title": "x", "content": "y"}]},
    )
    llm = _mock_llm(json.dumps({
        "title": "LSTM（教学版）",
        "summary": "理解 LSTM 的核心机制",
        "sections": [
            {
                "title": "什么是 LSTM",
                "content": "LSTM 是一种 RNN 变体...",
                "key_points": ["长短期记忆", "门控机制"],
                "examples": ["例子 1", "例子 2"],
                "thinking_prompts": ["🤔 为什么需要门控？"],
            }
        ],
        "difficulty": 3,
        "estimated_minutes": 15,
        "prerequisites": ["RNN"],
        "teaching_notes": "从遗忘门讲起",
    }, ensure_ascii=False))
    agent = PedagogyAgent(llm=llm)
    ctx = UnifiedContext()
    improved = await agent.process(ctx, source_resource=source)
    assert improved.type == ResourceType.DOCUMENT
    assert "教学版" in improved.title
    assert "教学说明" in improved.content or "教学设计说明" in improved.content
    assert "pedagogy" in improved.generated_by
    assert improved.metadata.get("pedagogy_applied") is True


@pytest.mark.asyncio
async def test_pedagogy_passes_through_non_document():
    """Non-document/reading resource passes through unchanged."""
    source = Resource(type=ResourceType.VIDEO, title="v", content="x")
    agent = PedagogyAgent(llm=_mock_llm("{}"))
    ctx = UnifiedContext()
    out = await agent.process(ctx, source_resource=source)
    assert out is source


# ---------------------------------------------------------------------------
# ExerciseGenerator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exercise_generator_produces_tiered_questions():
    llm = _mock_llm(json.dumps({
        "questions": [
            {"id": "q1", "tier": "basic", "type": "single_choice",
             "difficulty": 2, "question": "LSTM 有几个门？",
             "options": [{"label": "A", "text": "1"}, {"label": "B", "text": "3"},
                         {"label": "C", "text": "5"}, {"label": "D", "text": "2"}],
             "answer": "B", "explanation": "遗忘门+输入门+输出门=3",
             "estimated_seconds": 30},
            {"id": "q2", "tier": "advanced", "type": "short_answer",
             "difficulty": 3, "question": "解释 LSTM 的遗忘门",
             "answer": "控制上一时刻信息保留多少",
             "explanation": "f_t = σ(W_f · [h_{t-1}, x_t] + b_f)",
             "estimated_seconds": 120},
            {"id": "q3", "tier": "challenge", "type": "code",
             "difficulty": 4, "question": "实现函数返回 LSTM 的门数量",
             "answer": "def lstm_gate_count(): return 3",
             "explanation": "遗忘门、输入门和输出门共三个门",
             "estimated_seconds": 300,
             "code_spec": {
                 "language": "python",
                 "starter_code": "def lstm_gate_count():\n    pass",
                 "tests": [{
                     "name": "返回三个门",
                     "call": "lstm_gate_count()",
                     "expected_json": 3,
                 }],
                 "time_limit_seconds": 5,
             }},
        ]
    }, ensure_ascii=False))
    agent = ExerciseGeneratorAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="LSTM")
    assert resource.type == ResourceType.EXERCISE
    assert "LSTM" in resource.title
    assert len(resource.format_specific.get("questions", [])) == 3
    breakdown = resource.format_specific["difficulty_breakdown"]
    assert breakdown.get("basic") == 1
    assert breakdown.get("advanced") == 1
    assert breakdown.get("challenge") == 1
    code_question = resource.format_specific["questions"][2]
    assert code_question["code_spec"]["tests"][0]["expected_json"] == 3


@pytest.mark.asyncio
async def test_exercise_generator_fallback_on_empty():
    llm = _mock_llm("{}")
    agent = ExerciseGeneratorAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="X")
    assert resource.type == ResourceType.EXERCISE
    # Should have at least 1 fallback question
    assert resource.format_specific["total_questions"] >= 1


# ---------------------------------------------------------------------------
# Multimedia
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multimedia_generates_mindmap():
    llm = _mock_llm(json.dumps({
        "central_topic": "LSTM",
        "mermaid_dsl": "mindmap\n  root((LSTM))\n    门控机制\n      遗忘门\n      输入门\n      输出门\n    优势",
        "branch_count": 2,
    }, ensure_ascii=False))
    agent = MultimediaAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="LSTM")
    assert resource.type == ResourceType.MINDMAP
    assert "mindmap" in resource.content
    assert "LSTM" in resource.title
    assert resource.format_specific["central_topic"] == "LSTM"


@pytest.mark.asyncio
async def test_multimedia_strips_fences():
    llm = _mock_llm(json.dumps({
        "central_topic": "X",
        "mermaid_dsl": "```mermaid\nmindmap\n  root((X))\n    A\n```",
        "branch_count": 1,
    }, ensure_ascii=False))
    agent = MultimediaAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="X")
    # ```mermaid 围栏应该被去掉
    dsl = resource.format_specific["mermaid_dsl"]
    assert "```" not in dsl
    assert dsl.startswith("mindmap")


# ---------------------------------------------------------------------------
# ManimVideo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manim_video_two_stage_pipeline():
    """Designer stage → Coder stage → VideoResource."""
    designer_json = json.dumps({
        "title": "LSTM 动画",
        "duration_seconds": 30,
        "scenes": [
            {"name": "intro", "narration": "让我们看 LSTM",
             "visuals": ["画标题", "画公式"], "duration_seconds": 15},
        ],
        "key_visual_elements": ["标题", "公式"],
    }, ensure_ascii=False)
    coder_json = json.dumps({
        "manim_code": "from manim import *\n\nclass MainScene(Scene):\n    def construct(self):\n        t = Text('LSTM')\n        self.play(Write(t))\n        self.wait(1)\n",
        "scene_class": "MainScene",
    }, ensure_ascii=False)
    llm = _mock_llm(designer_json, coder_json)
    agent = ManimVideoAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="LSTM")
    assert resource.type == ResourceType.VIDEO
    assert resource.format_specific["scene_class"] == "MainScene"
    assert "MainScene" in resource.format_specific["manim_code"]
    assert resource.format_specific["render_status"] == "pending"


@pytest.mark.asyncio
async def test_manim_video_fallback_when_empty():
    """2026-06-22 fix (Task 3): when LLM returns empty JSON, the
    agent now surfaces a typed failed artifact instead of a
    placeholder scene. The fallback was the root cause of users
    seeing "(动画生成中 — LLM 输出待优化)" instead of a real
    failure message.
    """
    llm = _mock_llm("{}", "{}")
    agent = ManimVideoAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="X")
    # Should return a failed resource, not placeholder code
    assert resource.format_specific["render_status"] == "failed"
    assert resource.format_specific["render_error"]
    assert "video" in resource.tags
    assert "failed" in resource.tags


async def test_manim_video_rejects_fallback_marker_code():
    """2026-06-22 fix (Task 3.4): if the LLM returns code that
    literally contains ``Fallback scene`` or ``动画生成中``, the
    agent must reject it as a failed resource, not publish it as
    a valid video.
    """
    fallback_code = """from manim import *

class MainScene(Scene):
    \"\"\"Fallback scene for: test.\"\"\"
    def construct(self):
        t = Text(\"test\")
        self.play(Write(t))
"""
    # Stage 1 returns valid storyboard; stage 2 returns fallback code
    llm = _mock_llm(
        json.dumps({"title": "test", "scenes": [{"name": "s1", "visuals": ["x"]}]}),
        json.dumps({"manim_code": fallback_code}),
    )
    agent = ManimVideoAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="test")
    assert resource.format_specific["render_status"] == "failed"
    assert "fallback" in resource.format_specific.get("render_error", "").lower() or "fallback" in " ".join(resource.tags)


@pytest.mark.asyncio
async def test_manim_video_rejects_truncated_finish_reason_length():
    """2026-06-22 fix (Task 6): when the LLM hits ``max_tokens`` it
    sets ``finish_reason="length"``. The agent must surface this as a
    typed failed resource rather than publishing a half-finished
    script that Manim can't render.

    **2026-07-07 update:** with the L2 retry wrapper, codegen now
    attempts up to 3 times (max_tokens ×1, ×2, ×4). When ALL attempts
    truncate, the agent still surfaces the typed failure. The test
    provides 3 length-finish reasons to exercise the retry-exhausted
    path.
    """
    # Real-world truncation: code ends mid-line ("VGroup") but the
    # structural sniff test passes because ``class MainScene`` is
    # already in the early lines.
    truncated = """from manim import *

class MainScene(Scene):
    def construct(self):
        t = Text('hello')
        self.play(Write(t))
        w2_lines = VGroup"""
    llm = _mock_llm(
        # attempt 1: storyboard (stop)
        json.dumps({"title": "test", "scenes": [{"name": "s1", "visuals": ["x"]}]}),
        # attempts 2/3/4: codegen ×1 / ×2 / ×4 budgets, all truncate
        truncated,
        truncated,
        truncated,
        finish_reasons=("stop", "length", "length", "length"),
    )
    agent = ManimVideoAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="test")
    assert resource.format_specific["render_status"] == "failed"
    assert resource.format_specific.get("render_error") == "codegen_truncated_by_max_tokens"
    assert "truncated" in resource.tags


@pytest.mark.asyncio
async def test_manim_video_rejects_syntax_error_code():
    """2026-06-22 fix (Task 6): even if the LLM finished cleanly
    (``finish_reason=stop``), the code may still have a SyntaxError
    because the structural sniff test (``class`` + ``def construct``)
    only checks the surface. We now ``py_compile`` the code and
    reject it if it doesn't parse.
    """
    # Valid surface markers, but unclosed bracket downstream — py_compile will fail.
    broken = (
        "from manim import *\n"
        "\n"
        "class MainScene(Scene):\n"
        "    def construct(self):\n"
        "        nodes = VGroup(\n"  # unclosed paren → SyntaxError
        "        self.wait(1)\n"
    )
    llm = _mock_llm(
        json.dumps({"title": "test", "scenes": [{"name": "s1", "visuals": ["x"]}]}),
        json.dumps({"manim_code": broken}),
    )
    agent = ManimVideoAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="test")
    assert resource.format_specific["render_status"] == "failed"
    assert "syntax_error" in resource.format_specific.get("render_error", "")
    assert "syntax_error" in resource.tags


@pytest.mark.asyncio
async def test_manim_video_publishes_valid_code():
    """2026-06-22 fix (Task 6): ensure valid code still passes
    through with ``render_status=pending``.  The new compile check
    must not over-reject.
    """
    valid_code = (
        "from manim import *\n"
        "\n"
        "class MainScene(Scene):\n"
        "    def construct(self):\n"
        "        t = Text('hello')\n"
        "        self.play(Write(t))\n"
        "        self.wait(1)\n"
    )
    llm = _mock_llm(
        json.dumps({"title": "test", "scenes": [{"name": "s1", "visuals": ["x"]}]}),
        json.dumps({"manim_code": valid_code}),
    )
    agent = ManimVideoAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="test")
    assert resource.format_specific["render_status"] == "pending"
    assert resource.format_specific["manim_code"].strip() == valid_code.strip()


@pytest.mark.asyncio
async def test_manim_video_normalizes_literal_newlines_in_string():
    """2026-06-22 fix (Task 7): the LLM sometimes returns the
    ``manim_code`` field as a single-line string where newlines are
    encoded as the two-char sequence ``\\n`` instead of real newline
    chars.  Without this normalization the chat viewer renders the
    code as one continuous paragraph.

    Reproduces the user's reported symptom: a one-line string in the
    viewer instead of a properly-indented multi-line script.
    """
    # Constructed by Python string concat so the LLM-style literal
    # ``\\n`` (two chars: backslash + n) is preserved in the JSON value.
    inner = (
        'from manim import *\\n\\n'
        'class MainScene(Scene):\\n'
        '    def construct(self):\\n'
        "        t = Text('hello')\\n"
        '        self.play(Write(t))\\n'
        '        self.wait(1)'
    )
    one_line_json = '{"manim_code": "' + inner + '"}'
    # Stage 1: valid storyboard JSON.  Stage 2: malformed JSON (the
    # literal ``\\n`` is not a valid JSON escape), so parse_json_response
    # falls back to {} and the salvage path runs.
    llm = _mock_llm(
        json.dumps({"title": "test", "scenes": [{"name": "s1", "visuals": ["x"]}]}),
        one_line_json,
    )
    agent = ManimVideoAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="test")
    code = resource.format_specific["manim_code"]
    # After normalization: real newlines must be present.
    assert "\n" in code, f"expected multi-line code, got single-line: {code!r}"
    # The decoded form should contain the construct body indented
    # and a self.play on its own line.
    assert "    def construct(self):" in code
    assert "        self.play(Write(t))" in code
    # Trailing JSON punctuation must NOT leak into the code.
    assert not code.rstrip().endswith('"')
    assert not code.rstrip().endswith('}')


def test_normalize_code_newlines_handles_real_newlines():
    """The normalizer must leave strings that already contain real
    newlines untouched (apart from trimming JSON-tail punctuation
    and trailing whitespace).
    """
    from tutor.agents.resource.manim_video import _normalize_code_newlines

    code = "from manim import *\n\nclass X:\n    pass\n"
    # Trailing whitespace gets stripped — that's the intended
    # behavior so the code doesn't have a phantom trailing blank line
    # when embedded in a markdown ```python ... ``` block.
    assert _normalize_code_newlines(code) == code.rstrip()


def test_normalize_code_newlines_handles_empty():
    from tutor.agents.resource.manim_video import _normalize_code_newlines

    assert _normalize_code_newlines("") == ""
    assert _normalize_code_newlines(None or "") == ""


def test_extract_first_python_block_strips_json_tail():
    """When the LLM inlines ``manim_code`` without fences and adds a
    trailing JSON wrapper (``"`` / ``}``), the extractor must strip
    that punctuation before returning.
    """
    from tutor.agents.resource.manim_video import _extract_first_python_block

    raw = '{"manim_code": "from manim import *\\nclass MainScene(Scene):\\n    def construct(self):\\n        t = Text(\\"hi\\")"}'
    out = _extract_first_python_block(raw)
    # Even without newline normalization, the JSON tail must be trimmed.
    assert not out.rstrip().endswith('"')
    assert not out.rstrip().endswith('}')


# ---------------------------------------------------------------------------
# CodeSandbox — Task 8 (empty-code salvage + newline normalization)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_sandbox_salvages_code_from_real_newline_json():
    """2026-06-22 fix (Task 8): when the LLM returns the code JSON
    with embedded real newlines (``json.loads`` rejects unescaped
    control chars and returns ``{}``), the agent must salvage the
    code from the raw response and normalize it into a multi-line
    string.
    """
    # Constructed by Python string concat so the LLM-style literal
    # ``\\n`` (two chars) is preserved in the JSON value AND the
    # raw content also has real newlines (the common failure mode
    # the user reported).
    code_inner = (
        'import numpy as np\\n\\n'
        'def sigmoid(z):\\n'
        '    return 1 / (1 + np.exp(-z))\\n'
        '\\n'
        'print("hello")'
    )
    # Raw content: JSON with literal \\n (two chars) — this should
    # parse fine and yield real-newline code.
    raw = '{"title": "x", "language": "python", "code": "' + code_inner + '"}'
    llm = _mock_llm(raw)
    agent = CodeSandboxAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="x")
    code = resource.format_specific["code"]
    # After normalization: real newlines must be present.
    assert "\n" in code, f"expected multi-line code, got single-line: {code!r}"
    assert "import numpy as np" in code
    assert "def sigmoid" in code
    assert "    return 1 / (1 + np.exp(-z))" in code


@pytest.mark.asyncio
async def test_code_sandbox_salvages_code_from_unparseable_json():
    """2026-06-22 fix (Task 8): when ``json.loads`` fails entirely
    (e.g. real newlines embedded directly in the JSON string — not
    allowed in strict mode), the salvage path extracts a Python
    block from the raw content and normalizes it.
    """
    # Embed real newlines INSIDE the JSON string value — invalid JSON.
    code_inner_with_real_newlines = (
        'import numpy as np\n\n'
        'def sigmoid(z):\n'
        '    return 1 / (1 + np.exp(-z))\n\n'
        'print("hello")'
    )
    raw = (
        '{"title": "x", "language": "python", "code": "'
        + code_inner_with_real_newlines
        + '"}'
    )
    llm = _mock_llm(raw)
    agent = CodeSandboxAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="x")
    code = resource.format_specific["code"]
    # Salvage succeeded — code is non-empty and multi-line.
    assert code.strip(), "salvage path should have recovered some code"
    assert "\n" in code
    assert "import numpy as np" in code


@pytest.mark.asyncio
async def test_code_sandbox_returns_failed_resource_when_empty():
    """2026-06-22 fix (Task 8): if even the salvage path produces
    no code, the resource is a typed failed artifact (not an empty
    code block in the viewer).
    """
    llm = _mock_llm("not JSON at all, no python here")
    agent = CodeSandboxAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="x")
    assert resource.format_specific["execution_status"] == "failed"
    assert resource.format_specific.get("error_code") == "CODE_EMPTY_LLM_OUTPUT"
    assert "failed" in resource.tags
    assert "codegen_empty" in resource.tags


# ---------------------------------------------------------------------------
# Multimedia — Task 8 (Mermaid DSL sanitize)
# ---------------------------------------------------------------------------


def test_mindmap_normaliser_rewrites_bare_quoted_siblings():
    """Bare quoted labels are invalid mindmap siblings in Mermaid 11."""
    from tutor.agents.resource.multimedia import normalise_mindmap_dsl

    reported_dsl = (
        "mindmap\n"
        "  root((反向传播))\n"
        "    前向传播\n"
        '    "激活函数 a=σ(z)"\n'
        '    "计算损失 C"\n'
    )

    fixed, outline = normalise_mindmap_dsl(reported_dsl)

    assert "root((反向传播))" in fixed
    assert 'node_4["激活函数 a=σ(z)"]' in fixed
    assert not re.search(r'^\s*"', fixed, re.MULTILINE)
    assert [item.label for item in outline][-2:] == ["激活函数 a=σ(z)", "计算损失 C"]


def test_sanitize_mermaid_dsl_does_not_wrap_parens():
    """2026-06-22 fix (Task 8): the previous sanitizer over-wrapped
    any line containing parens — including valid mindmap root nodes
    like ``((反向传播算法))`` — which then broke sibling line parsing.

    The new sanitizer only quote-wraps when the line contains
    ``---`` or ``===`` (the actually-fatal sequences).
    """
    from tutor.agents.resource.multimedia import _sanitize_mermaid_dsl

    dsl = (
        "mindmap\n"
        "  root((反向传播算法))\n"
        "    基本概念\n"
        "      神经网络学习\n"
        "    优化问题\n"
    )
    out = _sanitize_mermaid_dsl(dsl)
    # Root node must NOT be wrapped in extra quotes — it's already a
    # valid ``((...))`` shape.
    assert 'root((反向传播算法))' in out, f"root node was over-wrapped: {out!r}"
    # And the unwrapped ``基本概念`` line must remain unquoted so
    # Mermaid sees consistent siblings.
    assert "    基本概念" in out


def test_sanitize_mermaid_dsl_wraps_dangerous_separators():
    """Lines containing ``---`` or ``===`` MUST be wrapped because
    those sequences are reserved as separators in Mermaid diagrams
    and crash the parser otherwise.
    """
    from tutor.agents.resource.multimedia import _sanitize_mermaid_dsl

    dsl = (
        "mindmap\n"
        "  root((X))\n"
        "    有破折号---的内容\n"
    )
    out = _sanitize_mermaid_dsl(dsl)
    # The dangerous line should now be quoted.
    assert '"有破折号---的内容"' in out


def test_sanitize_mermaid_dsl_normalizes_indentation():
    """LLMs frequently mix tabs / 1-space / 3-space indentation. We
    normalize mindmap lines to consistent 2-space increments so the
    parser sees a stable hierarchy.
    """
    from tutor.agents.resource.multimedia import _sanitize_mermaid_dsl

    # Mixed indent: 1 space, 3 spaces, 5 spaces (originally). After
    # normalization: 2-space increments.
    dsl = (
        "mindmap\n"
        " root((X))\n"           # 1 space → 0 depth (root sibling)
        "   A\n"                  # 3 spaces → 1 depth
        "      B\n"               # 6 spaces → 3 depths (we round)
    )
    out = _sanitize_mermaid_dsl(dsl)
    # All lines under mindmap should use 2-space increments.
    for line in out.splitlines()[1:]:  # skip "mindmap" header
        if line.strip():
            stripped = line.lstrip()
            leading = len(line) - len(stripped)
            # Indent should be a multiple of 2.
            assert leading % 2 == 0, f"non-2-space indent in: {line!r}"


# ---------------------------------------------------------------------------
# CodeSandbox
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_sandbox_runs_simple_code():
    llm = _mock_llm(json.dumps({
        "title": "Hello LSTM",
        "language": "python",
        "code": "print('hello lstm')",
        "explanation": "最简单的例子",
        "expected_output": "hello lstm",
        "difficulty": 1,
    }, ensure_ascii=False))
    agent = CodeSandboxAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="LSTM")
    assert resource.type == ResourceType.CODE
    # Code was executed (since short)
    assert resource.format_specific["execution_status"] in ("success", "failed")
    assert "print" in resource.format_specific["code"]


@pytest.mark.asyncio
async def test_code_sandbox_no_run_for_long_code():
    llm = _mock_llm(json.dumps({
        "title": "long",
        "code": "x = 1\n" * 300,  # 300 lines
        "explanation": "x",
        "language": "python",
    }, ensure_ascii=False))
    agent = CodeSandboxAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="X")
    # Long code should not be executed
    assert resource.format_specific["execution_status"] == "not_run"


# ---------------------------------------------------------------------------
# QualityReviewer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quality_reviewer_pass():
    llm = _mock_llm(json.dumps({
        "verdict": "pass",
        "quality_score": 0.92,
        "issues": [],
        "suggestions": ["可以补充更多例子"],
        "comments": "整体内容准确、结构清晰",
    }, ensure_ascii=False))
    agent = QualityReviewerAgent(llm=llm)
    ctx = UnifiedContext()
    resource = Resource(
        type=ResourceType.DOCUMENT, title="t", content="body"
    )
    review = await agent.process(ctx, resource=resource)
    assert review.verdict == ReviewVerdict.PASS
    assert review.quality_score == pytest.approx(0.92)
    assert len(review.suggestions) == 1


@pytest.mark.asyncio
async def test_quality_reviewer_revise():
    llm = _mock_llm(json.dumps({
        "verdict": "revise",
        "quality_score": 0.5,
        "issues": ["缺少例子", "难度跳跃"],
        "suggestions": ["补一个具体例子", "加过渡段"],
    }, ensure_ascii=False))
    agent = QualityReviewerAgent(llm=llm)
    ctx = UnifiedContext()
    resource = Resource(type=ResourceType.DOCUMENT, title="t", content="body")
    review = await agent.process(ctx, resource=resource)
    assert review.verdict == ReviewVerdict.REVISE
    assert review.quality_score < 0.7
    assert len(review.issues) == 2


@pytest.mark.asyncio
async def test_quality_reviewer_invalid_verdict_falls_back_to_pass():
    llm = _mock_llm(json.dumps({
        "verdict": "i_am_unsure",
        "quality_score": 0.8,
    }, ensure_ascii=False))
    agent = QualityReviewerAgent(llm=llm)
    ctx = UnifiedContext()
    resource = Resource(type=ResourceType.DOCUMENT, title="t", content="body")
    review = await agent.process(ctx, resource=resource)
    # Invalid verdict → falls back to PASS
    assert review.verdict == ReviewVerdict.PASS


# ---------------------------------------------------------------------------
# StreamBus integration smoke test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_expert_emits_through_stream():
    llm = _mock_llm(json.dumps({
        "title": "X",
        "sections": [{"title": "A", "content": "..."}],
        "difficulty": 2,
        "estimated_minutes": 5,
    }, ensure_ascii=False))
    agent = ContentExpertAgent(llm=llm)
    ctx = UnifiedContext()
    bus = StreamBus()
    q = bus.subscribe()

    async def collect():
        events = []
        while True:
            evt = await q.get()
            if evt is None:
                return events
            events.append(evt.type.value)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await agent.process(ctx, topic="X", stream=bus)
    await bus.done()
    events = await asyncio.wait_for(task, timeout=2)
    assert "stage_start" in events
    assert "stage_end" in events
