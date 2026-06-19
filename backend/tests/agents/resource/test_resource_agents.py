"""Tests for the 7 resource-generation agents (with mocked LLM)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

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


def _mock_llm(*responses: str):
    """Mock LLM provider returning successive responses."""
    from tutor.services.llm.base import LLMResponse

    queue = list(responses)

    llm = MagicMock()
    llm.model = "mock-model"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(req):
        content = queue.pop(0) if queue else "{}"
        return LLMResponse(content=content, model="mock-model", finish_reason="stop")

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
             "difficulty": 4, "question": "用 PyTorch 实现一个 LSTM 层",
             "answer": "nn.LSTM(input_size, hidden_size)",
             "explanation": "PyTorch 提供 nn.LSTM",
             "estimated_seconds": 300},
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
    llm = _mock_llm("{}", "{}")
    agent = ManimVideoAgent(llm=llm)
    ctx = UnifiedContext()
    resource = await agent.process(ctx, topic="X")
    # Falls back to a minimal Manim scene
    assert "class MainScene" in resource.format_specific["manim_code"]


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
