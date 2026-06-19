"""Tests for the tutor agent cluster (QuestionUnderstanding + Tutoring + Enrichment)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from tutor.agents.tutor.multimodal_enrichment import (
    EnrichmentSuggestion,
    EnrichmentType,
    MultiModalEnrichmentAgent,
)
from tutor.agents.tutor.question_understanding import (
    QuestionType,
    QuestionUnderstanding,
    QuestionUnderstandingAgent,
)
from tutor.agents.tutor.tutoring import TutoringAgent, TutoringAnswer
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.llm.base import LLMResponse


def _mock_llm(*responses: str):
    queue = list(responses)
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(req):
        content = queue.pop(0) if queue else "{}"
        return LLMResponse(content=content, model="mock", finish_reason="stop")

    llm.call = call
    return llm


# ---------------------------------------------------------------------------
# QuestionUnderstandingAgent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_question_understanding_parses_json():
    llm = _mock_llm(json.dumps({
        "question_type": "concept",
        "concepts": ["LSTM", "门控机制"],
        "difficulty": 3,
        "student_intent": "理解 LSTM 的核心创新",
        "follow_up_questions": ["LSTM 是在哪个任务上提出的？"],
        "confidence": 0.9,
    }, ensure_ascii=False))
    agent = QuestionUnderstandingAgent(llm=llm)
    ctx = UnifiedContext(user_message="什么是 LSTM？")
    und = await agent.process(ctx)
    assert und.question_type == QuestionType.CONCEPT
    assert und.concepts == ["LSTM", "门控机制"]
    assert und.difficulty == 3
    assert und.confidence == 0.9
    assert len(und.follow_up_questions) == 1


@pytest.mark.asyncio
async def test_question_understanding_handles_invalid_json():
    llm = _mock_llm("garbage")
    agent = QuestionUnderstandingAgent(llm=llm)
    ctx = UnifiedContext(user_message="x")
    und = await agent.process(ctx)
    # Falls back to defaults
    assert und.question_type == QuestionType.OTHER
    assert und.concepts == []
    assert und.difficulty == 2


@pytest.mark.asyncio
async def test_question_understanding_invalid_type_falls_back():
    llm = _mock_llm(json.dumps({
        "question_type": "totally_invalid_type",
        "difficulty": 3,
    }))
    agent = QuestionUnderstandingAgent(llm=llm)
    ctx = UnifiedContext(user_message="x")
    und = await agent.process(ctx)
    assert und.question_type == QuestionType.OTHER


@pytest.mark.asyncio
async def test_question_understanding_difficulty_clamped():
    llm = _mock_llm(json.dumps({"difficulty": 99, "question_type": "concept"}))
    agent = QuestionUnderstandingAgent(llm=llm)
    ctx = UnifiedContext(user_message="x")
    und = await agent.process(ctx)
    assert und.difficulty == 5  # clamped to max


@pytest.mark.asyncio
async def test_question_understanding_emits_stream_events():
    llm = _mock_llm(json.dumps({
        "question_type": "method",
        "concepts": ["反向传播"],
        "difficulty": 4,
        "confidence": 0.8,
    }, ensure_ascii=False))
    agent = QuestionUnderstandingAgent(llm=llm)
    ctx = UnifiedContext(user_message="反向传播怎么算？")
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
    await agent.process(ctx, stream=bus)
    await bus.done()
    events = await asyncio.wait_for(task, timeout=2)
    assert "stage_start" in events
    assert "observation" in events


# ---------------------------------------------------------------------------
# TutoringAgent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tutoring_generates_4_layer_answer():
    llm = _mock_llm(json.dumps({
        "tldr": "LSTM 是一种带门控的 RNN 变体，能解决长期依赖问题。",
        "intuition": "LSTM 就像带备忘录的学生，能选择性记住重要信息。",
        "principle": "LSTM 通过遗忘门 $f_t$、输入门 $i_t$、输出门 $o_t$ 控制信息流。",
        "example": "```python\nimport torch.nn as nn\nlstm = nn.LSTM(10, 20)\n```",
        "follow_up_suggestion": "下一步可学习 GRU，它是 LSTM 的简化版本。",
        "related_concepts": ["GRU", "RNN", "反向传播"],
        "confidence": 0.9,
    }, ensure_ascii=False))
    agent = TutoringAgent(llm=llm)
    ctx = UnifiedContext(user_message="什么是 LSTM？")
    und = QuestionUnderstanding(
        question_type=QuestionType.CONCEPT,
        concepts=["LSTM"],
        difficulty=3,
        raw_question=ctx.user_message,
    )
    answer = await agent.process(ctx, understanding=und, rag_context="")
    assert "LSTM" in answer.tldr
    assert "备忘录" in answer.intuition
    assert "遗忘门" in answer.principle
    assert "torch" in answer.example
    assert "GRU" in answer.follow_up_suggestion
    assert "RNN" in answer.related_concepts
    assert answer.confidence == 0.9
    # Markdown rendering should include all sections
    md = answer.render_markdown()
    assert "一句话回答" in md
    assert "直觉理解" in md
    assert "原理详解" in md
    assert "例子" in md


@pytest.mark.asyncio
async def test_tutoring_handles_invalid_json():
    llm = _mock_llm("not json")
    agent = TutoringAgent(llm=llm)
    ctx = UnifiedContext(user_message="x")
    und = QuestionUnderstanding(raw_question="x")
    answer = await agent.process(ctx, understanding=und)
    # Falls back to empty answer (graceful degradation)
    assert answer.tldr == ""
    assert answer.confidence == 0.7  # default


@pytest.mark.asyncio
async def test_tutoring_extracts_source_paths_from_rag():
    llm = _mock_llm(json.dumps({
        "tldr": "x",
        "principle": "y",
        "confidence": 0.8,
    }))
    agent = TutoringAgent(llm=llm)
    ctx = UnifiedContext(user_message="x")
    und = QuestionUnderstanding(raw_question="x")
    rag = "### [path/to/lstm.md]\nsome content\n\n### [path/to/other.md]\nmore"
    answer = await agent.process(ctx, understanding=und, rag_context=rag)
    assert "path/to/lstm.md" in answer.sources
    assert "path/to/other.md" in answer.sources


# ---------------------------------------------------------------------------
# MultiModalEnrichmentAgent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrichment_generates_suggestions():
    llm = _mock_llm(json.dumps({
        "suggestions": [
            {
                "type": "diagram",
                "title": "LSTM 思维导图",
                "content": "mindmap\n  root((LSTM))\n    门控\n    优势",
                "rationale": "帮助理清 LSTM 核心组成",
                "confidence": 0.9,
            },
            {
                "type": "code_example",
                "title": "PyTorch LSTM 示例",
                "content": "import torch.nn as nn\nlstm = nn.LSTM(10, 20)",
                "rationale": "动手实践加深理解",
                "confidence": 0.85,
            },
        ]
    }))
    agent = MultiModalEnrichmentAgent(llm=llm)
    ctx = UnifiedContext(user_message="什么是 LSTM？")
    und = QuestionUnderstanding(
        question_type=QuestionType.CONCEPT,
        concepts=["LSTM"],
        difficulty=3,
        raw_question=ctx.user_message,
    )
    answer = TutoringAnswer(
        tldr="LSTM 是带门控的 RNN",
        principle="三个门...",
    )
    suggestions = await agent.process(ctx, understanding=und, answer=answer)
    assert len(suggestions) == 2
    assert suggestions[0].type == EnrichmentType.DIAGRAM
    assert "LSTM" in suggestions[0].title
    assert suggestions[1].type == EnrichmentType.CODE_EXAMPLE


@pytest.mark.asyncio
async def test_enrichment_caps_at_three():
    items = [
        {"type": "diagram", "title": f"D{i}", "content": "x"}
        for i in range(10)
    ]
    llm = _mock_llm(json.dumps({"suggestions": items}))
    agent = MultiModalEnrichmentAgent(llm=llm)
    ctx = UnifiedContext(user_message="x")
    und = QuestionUnderstanding(raw_question="x")
    answer = TutoringAnswer(tldr="x")
    suggestions = await agent.process(ctx, understanding=und, answer=answer)
    assert len(suggestions) == 3


@pytest.mark.asyncio
async def test_enrichment_handles_empty():
    llm = _mock_llm("{}")
    agent = MultiModalEnrichmentAgent(llm=llm)
    ctx = UnifiedContext(user_message="x")
    und = QuestionUnderstanding(raw_question="x")
    answer = TutoringAnswer(tldr="x")
    suggestions = await agent.process(ctx, understanding=und, answer=answer)
    assert suggestions == []


@pytest.mark.asyncio
async def test_enrichment_invalid_type_falls_back_to_diagram():
    llm = _mock_llm(json.dumps({
        "suggestions": [
            {"type": "totally_invalid", "title": "x", "content": "y"}
        ]
    }))
    agent = MultiModalEnrichmentAgent(llm=llm)
    ctx = UnifiedContext(user_message="x")
    und = QuestionUnderstanding(raw_question="x")
    answer = TutoringAnswer(tldr="x")
    suggestions = await agent.process(ctx, understanding=und, answer=answer)
    assert suggestions[0].type == EnrichmentType.DIAGRAM


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


def test_question_understanding_to_dict():
    und = QuestionUnderstanding(
        question_type=QuestionType.METHOD,
        concepts=["X"],
        difficulty=3,
        student_intent="learn X",
        follow_up_questions=["?"],
        confidence=0.8,
    )
    d = und.to_dict()
    assert d["question_type"] == "method"
    assert d["concepts"] == ["X"]
    assert d["difficulty"] == 3
    assert d["confidence"] == 0.8


def test_tutoring_answer_render_markdown_all_sections():
    answer = TutoringAnswer(
        tldr="TL",
        intuition="INT",
        principle="PRIN",
        example="EX",
        follow_up_suggestion="FU",
        related_concepts=["A", "B"],
    )
    md = answer.render_markdown()
    assert "一句话回答" in md
    assert "直觉理解" in md
    assert "原理详解" in md
    assert "例子" in md
    assert "进一步学习" in md
    assert "`A`" in md and "`B`" in md


def test_tutoring_answer_render_markdown_only_some_sections():
    answer = TutoringAnswer(tldr="TL only")
    md = answer.render_markdown()
    assert "一句话回答" in md
    assert "直觉理解" not in md
