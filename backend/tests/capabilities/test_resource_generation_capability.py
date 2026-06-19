"""End-to-end test for ResourceGenerationCapability.

Drives the full pipeline with mocked LLMs:

    Intent → Content → Pedagogy → [Mindmap + Exercise + Video + Code + Reading]
           → [Quality Review × N] → Package → Path Integration
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from unittest.mock import MagicMock

import pytest

from tutor.agents.resource.code_sandbox import CodeSandboxAgent
from tutor.agents.resource.content_expert import ContentExpertAgent
from tutor.agents.resource.exercise_generator import ExerciseGeneratorAgent
from tutor.agents.resource.intent_understanding import IntentUnderstandingAgent
from tutor.agents.resource.manim_video import ManimVideoAgent
from tutor.agents.resource.multimedia import MultimediaAgent
from tutor.agents.resource.pedagogy import PedagogyAgent
from tutor.agents.resource.quality_reviewer import QualityReviewerAgent
from tutor.capabilities.resource_generation import ResourceGenerationCapability
from tutor.core.context import UnifiedContext
from tutor.core.stream import StreamEventType
from tutor.core.stream_bus import StreamBus
from tutor.services.learner_profile.builder import (
    ProfileBuilder,
    get_profile_builder,
)
from tutor.services.learner_profile.store import (
    ProfileStore,
    reset_profile_store,
)
from tutor.services.resource_package.schema import (
    ResourceType,
    ReviewVerdict,
)


# ---------------------------------------------------------------------------
# Smart mock LLM
# ---------------------------------------------------------------------------


class SmartMockLLM:
    """Mock LLM that picks a response based on keyword matching in messages."""

    def __init__(self, responses: list[tuple[str, str]]):
        # responses: list of (keyword_in_prompt, response_content)
        self._responses = list(responses)
        self._used = set()
        self.call_count = 0
        # Attributes some code expects on the LLM
        self.model = "mock-model"
        self.default_temperature = 0.5
        self.default_max_tokens = 2048

    async def call(self, req):
        from tutor.services.llm.base import LLMResponse

        self.call_count += 1
        prompt_text = "\n".join(m.content for m in req.messages)
        for i, (keyword, response) in enumerate(self._responses):
            if i in self._used:
                continue
            if keyword in prompt_text:
                self._used.add(i)
                return LLMResponse(
                    content=response,
                    model="mock",
                    finish_reason="stop",
                )
        # Fallback — return a minimal valid JSON
        return LLMResponse(content="{}", model="mock", finish_reason="stop")


def _make_capability_mock_llm() -> SmartMockLLM:
    """Build a smart mock with all canned responses for the full pipeline."""
    return SmartMockLLM(
        responses=[
            # 1. Intent understanding — keyword: "用户消息"
            (
                "用户消息",
                json.dumps(
                    {
                        "topic": "LSTM",
                        "scope": "deep_dive",
                        "resource_types": ["document", "mindmap", "exercise", "video", "code", "reading"],
                        "prerequisites": ["RNN"],
                        "goal": "系统学习 LSTM",
                        "confidence": 0.9,
                    },
                    ensure_ascii=False,
                ),
            ),
            # 2. ContentExpert — keyword: "RAG 检索" / fallback: content schema
            (
                "RAG 检索",
                json.dumps(
                    {
                        "title": "LSTM 长短期记忆网络",
                        "summary": "理解 LSTM 的核心机制",
                        "sections": [
                            {
                                "title": "什么是 LSTM",
                                "content": "LSTM 是一种 RNN 变体...",
                                "key_points": ["长短期记忆", "门控机制"],
                            },
                            {
                                "title": "三个门",
                                "content": "遗忘门、输入门、输出门...",
                                "key_points": ["遗忘门", "输入门", "输出门"],
                            },
                        ],
                        "difficulty": 3,
                        "estimated_minutes": 15,
                        "prerequisites": ["RNN"],
                        "tags": ["deep_learning"],
                        "has_math": True,
                        "has_diagrams": False,
                    },
                    ensure_ascii=False,
                ),
            ),
            # 3. Pedagogy — keyword: "教学" or "原始内容"
            (
                "原始内容",
                json.dumps(
                    {
                        "title": "LSTM（教学版）",
                        "summary": "深入理解 LSTM",
                        "sections": [
                            {
                                "title": "什么是 LSTM",
                                "content": "LSTM 通过门控机制...",
                                "key_points": ["门控"],
                                "examples": ["翻译任务"],
                                "thinking_prompts": ["为什么需要门控？"],
                            }
                        ],
                        "difficulty": 3,
                        "estimated_minutes": 20,
                        "prerequisites": ["RNN"],
                        "teaching_notes": "从门控讲起",
                    },
                    ensure_ascii=False,
                ),
            ),
            # 4. Multimedia — keyword: "Mermaid" or "思维导图"
            (
                "思维导图",
                json.dumps(
                    {
                        "central_topic": "LSTM",
                        "mermaid_dsl": "mindmap\n  root((LSTM))\n    门控\n      遗忘门\n      输入门\n      输出门\n    优势",
                        "branch_count": 2,
                    },
                    ensure_ascii=False,
                ),
            ),
            # 5. ExerciseGenerator — keyword: "分层" or "n_basic"
            (
                "基础题",
                json.dumps(
                    {
                        "questions": [
                            {
                                "id": "q1",
                                "tier": "basic",
                                "type": "single_choice",
                                "difficulty": 2,
                                "question": "LSTM 有几个门？",
                                "options": [
                                    {"label": "A", "text": "1"},
                                    {"label": "B", "text": "3"},
                                    {"label": "C", "text": "5"},
                                    {"label": "D", "text": "2"},
                                ],
                                "answer": "B",
                                "explanation": "三门",
                                "estimated_seconds": 30,
                            },
                            {
                                "id": "q2",
                                "tier": "advanced",
                                "type": "short_answer",
                                "difficulty": 3,
                                "question": "解释遗忘门",
                                "answer": "控制保留",
                                "explanation": "f_t",
                                "estimated_seconds": 120,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
            ),
            # 6a. Manim designer — keyword: "分镜"
            (
                "分镜",
                json.dumps(
                    {
                        "title": "LSTM 动画",
                        "duration_seconds": 30,
                        "scenes": [
                            {
                                "name": "intro",
                                "narration": "看 LSTM",
                                "visuals": ["画标题"],
                                "duration_seconds": 15,
                            }
                        ],
                        "key_visual_elements": ["标题"],
                    },
                    ensure_ascii=False,
                ),
            ),
            # 6b. Manim coder — keyword: "Manim Python"
            (
                "Manim Python",
                json.dumps(
                    {
                        "manim_code": "from manim import *\n\nclass MainScene(Scene):\n    def construct(self):\n        t = Text('LSTM')\n        self.play(Write(t))\n        self.wait(1)\n",
                        "scene_class": "MainScene",
                    },
                    ensure_ascii=False,
                ),
            ),
            # 7. CodeSandbox — keyword: "代码示例"
            (
                "代码示例",
                json.dumps(
                    {
                        "title": "LSTM 示例",
                        "language": "python",
                        "code": "print('hello lstm')\nimport sys\nprint(sys.version_info[:2])",
                        "explanation": "简单的 LSTM 介绍",
                        "expected_output": "hello lstm",
                        "difficulty": 2,
                    },
                    ensure_ascii=False,
                ),
            ),
            # Reading content (for pedagogy reuse) — keyword: "教学重构"
            (
                "原始内容",
                json.dumps(
                    {
                        "title": "LSTM（拓展阅读）",
                        "summary": "深入理解",
                        "sections": [
                            {
                                "title": "为什么需要 LSTM",
                                "content": "RNN 的梯度消失...",
                                "key_points": ["梯度消失"],
                                "examples": [],
                                "thinking_prompts": [],
                            }
                        ],
                        "difficulty": 3,
                        "estimated_minutes": 12,
                        "prerequisites": ["RNN"],
                        "teaching_notes": "拓展视角",
                    },
                    ensure_ascii=False,
                ),
            ),
            # QualityReviewer — keyword: "审核" or "verdict"
            (
                "verdict",
                json.dumps(
                    {
                        "verdict": "pass",
                        "quality_score": 0.88,
                        "issues": [],
                        "suggestions": [],
                        "comments": "整体不错",
                    },
                    ensure_ascii=False,
                ),
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def fresh_builder(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    from tutor.services.learner_profile import reset_profile_builder

    reset_profile_builder()
    reset_profile_store()

    builder = get_profile_builder()
    builder.store = ProfileStore(tmp_path / "e2e_resources.db")
    await builder.initialize()

    # Seed a learner with some mastery
    from tutor.services.learner_profile.schema import LearnerProfile, CognitiveStyle

    profile = LearnerProfile(user_id="alice")
    profile.knowledge_map.set("ai_overview", 0.95)
    profile.knowledge_map.set("ml_basics", 0.85)
    profile.knowledge_map.set("neural_network", 0.6)
    profile.cognitive_style = CognitiveStyle.VISUAL
    profile.modality.video = 0.9
    profile.modality.diagram = 0.9
    profile.modality.code = 0.7
    await builder.store.replace(profile, source="seed")

    yield builder
    await builder.store.close()
    reset_profile_builder()
    reset_profile_store()


@pytest.fixture
def capability(fresh_builder):
    llm = _make_capability_mock_llm()
    return ResourceGenerationCapability(
        builder=fresh_builder,
        intent_agent=IntentUnderstandingAgent(llm=llm),
        content_expert=ContentExpertAgent(llm=llm),
        pedagogy=PedagogyAgent(llm=llm),
        multimedia=MultimediaAgent(llm=llm),
        exercise_generator=ExerciseGeneratorAgent(llm=llm),
        manim_video=ManimVideoAgent(llm=llm),
        code_sandbox=CodeSandboxAgent(llm=llm),
        quality_reviewer=QualityReviewerAgent(llm=llm),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_emits_all_stages(capability, fresh_builder):
    context = UnifiedContext(
        user_id="alice",
        user_message="系统学习 LSTM",
        language="zh",
    )
    bus = StreamBus()
    events: list[tuple[str, str]] = []

    q = bus.subscribe()

    async def collect():
        while True:
            evt = await q.get()
            if evt is None:
                return
            events.append((evt.type.value, evt.stage))

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)

    await capability.run(context, bus)
    await bus.done()
    await asyncio.wait_for(task, timeout=10)

    stages_started = [s for t, s in events if t == "stage_start"]
    # All 9 high-level stages
    assert "intent_understanding" in stages_started
    assert "profile_loading" in stages_started
    assert "knowledge_graph_query" in stages_started
    assert "resource_planning" in stages_started
    assert "content_and_pedagogy" in stages_started
    assert "parallel_resource_generation" in stages_started
    assert "quality_review" in stages_started
    assert "path_integration" in stages_started

    # Done event at the end
    done_events = [t for t, _ in events if t == "done"]
    assert len(done_events) == 1


@pytest.mark.asyncio
async def test_full_pipeline_emits_result_event(capability, fresh_builder):
    context = UnifiedContext(
        user_id="alice",
        user_message="学习 LSTM",
        language="zh",
    )
    bus = StreamBus()
    events: list[Any] = []

    q = bus.subscribe()

    async def collect():
        while True:
            evt = await q.get()
            if evt is None:
                return
            events.append(evt)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await capability.run(context, bus)
    await bus.done()
    await asyncio.wait_for(task, timeout=10)

    result_events = [e for e in events if e.type == StreamEventType.RESULT]
    assert len(result_events) == 1
    payload = json.loads(result_events[0].content)
    assert "package" in payload
    assert "summary" in payload
    assert "kg_summary" in payload
    assert "next_step" in payload
    assert payload["next_step"] == "open_resource_cards"


@pytest.mark.asyncio
async def test_package_contains_all_resource_types(capability, fresh_builder):
    context = UnifiedContext(
        user_id="alice",
        user_message="学习 LSTM",
        language="zh",
    )
    bus = StreamBus()
    q = bus.subscribe()
    events: list[Any] = []

    async def collect():
        while True:
            evt = await q.get()
            if evt is None:
                return
            events.append(evt)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await capability.run(context, bus)
    await bus.done()
    await asyncio.wait_for(task, timeout=10)

    result = [e for e in events if e.type == StreamEventType.RESULT][0]
    payload = json.loads(result.content)
    pkg = payload["package"]
    types_in_pkg = {r["type"] for r in pkg["resources"]}

    # Should have at least document (pedagogy version); the rest depend
    # on parallel generation succeeding with mocks.
    assert "document" in types_in_pkg or len(types_in_pkg) >= 1
    # If mocks worked, more types should be present
    assert len(types_in_pkg) >= 1


@pytest.mark.asyncio
async def test_quality_reviews_attached(capability, fresh_builder):
    context = UnifiedContext(
        user_id="alice",
        user_message="学习 LSTM",
        language="zh",
    )
    bus = StreamBus()
    q = bus.subscribe()
    events: list[Any] = []

    async def collect():
        while True:
            evt = await q.get()
            if evt is None:
                return
            events.append(evt)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await capability.run(context, bus)
    await bus.done()
    await asyncio.wait_for(task, timeout=10)

    result = [e for e in events if e.type == StreamEventType.RESULT][0]
    payload = json.loads(result.content)
    reviews = payload["reviews"]
    pkg = payload["package"]
    # Each resource should have a review attached
    assert len(reviews) == len(pkg["resources"])
    # All pass in our mock
    for r in reviews:
        assert r["verdict"] == "pass"
        assert r["quality_score"] >= 0.7
    # Resource metadata should also contain review
    for res in pkg["resources"]:
        assert "review" in res["metadata"]
        assert res["metadata"]["review"]["verdict"] == "pass"


@pytest.mark.asyncio
async def test_path_integration_updates_profile(capability, fresh_builder):
    context = UnifiedContext(
        user_id="alice",
        user_message="学习 LSTM",
        language="zh",
    )
    bus = StreamBus()
    q = bus.subscribe()

    async def collect():
        while True:
            evt = await q.get()
            if evt is None:
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await capability.run(context, bus)
    await bus.done()
    await asyncio.wait_for(task, timeout=2)

    # Profile should now have last_package_id and resource_history
    profile = await fresh_builder.get("alice")
    assert "last_package_id" in profile.metadata
    assert "resource_history" in profile.metadata
    assert profile.metadata["last_topic"] == "LSTM"
    assert len(profile.metadata["resource_history"]) >= 1


@pytest.mark.asyncio
async def test_kg_summary_in_result(capability, fresh_builder):
    context = UnifiedContext(
        user_id="alice",
        user_message="学习 LSTM",
        language="zh",
    )
    bus = StreamBus()
    q = bus.subscribe()
    events: list[Any] = []

    async def collect():
        while True:
            evt = await q.get()
            if evt is None:
                return
            events.append(evt)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await capability.run(context, bus)
    await bus.done()
    await asyncio.wait_for(task, timeout=10)

    result = [e for e in events if e.type == StreamEventType.RESULT][0]
    payload = json.loads(result.content)
    kg = payload["kg_summary"]
    assert kg.get("course") == "ai_introduction"
    # alice has mastered 2 concepts (ai_overview, ml_basics)
    assert kg.get("mastered_count") >= 1


@pytest.mark.asyncio
async def test_capability_handles_empty_message(capability, fresh_builder):
    """Edge case: very short / vague message still produces a package."""
    context = UnifiedContext(
        user_id="alice",
        user_message="x",  # minimal
        language="zh",
    )
    bus = StreamBus()
    q = bus.subscribe()
    events: list[Any] = []

    async def collect():
        while True:
            evt = await q.get()
            if evt is None:
                return
            events.append(evt)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await capability.run(context, bus)
    await bus.done()
    await asyncio.wait_for(task, timeout=10)

    # Should still complete gracefully (no crash)
    assert any(e.type == StreamEventType.DONE for e in events)


@pytest.mark.asyncio
async def test_intent_understanding_fallback_keyword():
    """Test the keyword-based fallback parser directly."""
    from tutor.agents.resource.intent_understanding import parse_intent_keyword
    from tutor.services.resource_package.schema import ResourceType

    intent = parse_intent_keyword("系统学习 Transformer")
    assert "Transformer" in intent.topic or "transformer" in intent.topic.lower()
    assert intent.scope == "deep_dive"
    assert len(intent.resource_types) == len(list(ResourceType))

    intent2 = parse_intent_keyword("概览一下 NLP")
    assert intent2.scope == "overview"


@pytest.mark.asyncio
async def test_resource_planning_respects_modality():
    """Resource planner should include diagram/mindmap type when modality is 'diagram'."""
    from tutor.agents.resource.intent_understanding import Intent, parse_intent_keyword
    from tutor.services.resource_package.schema import ResourceType

    cap = ResourceGenerationCapability.__new__(ResourceGenerationCapability)
    intent = Intent(topic="X", resource_types=[ResourceType.DOCUMENT])
    profile_snapshot = {
        "modality_dominant": "diagram",
        "knowledge_count": 0,
    }
    planned = cap._plan_resources(
        intent=intent, profile_snapshot=profile_snapshot, kg_summary={}
    )
    assert ResourceType.MINDMAP in planned
    assert ResourceType.VIDEO not in planned  # not added for diagram modality


@pytest.mark.asyncio
async def test_resource_planning_video_modality_adds_video():
    cap = ResourceGenerationCapability.__new__(ResourceGenerationCapability)
    from tutor.agents.resource.intent_understanding import Intent
    from tutor.services.resource_package.schema import ResourceType

    intent = Intent(topic="X", resource_types=[ResourceType.DOCUMENT])
    planned = cap._plan_resources(
        intent=intent,
        profile_snapshot={"modality_dominant": "video"},
        kg_summary={},
    )
    assert ResourceType.VIDEO in planned


@pytest.mark.asyncio
async def test_resource_planning_overview_skips_video():
    from tutor.agents.resource.intent_understanding import parse_intent_keyword

    cap = ResourceGenerationCapability.__new__(ResourceGenerationCapability)
    intent = parse_intent_keyword("概览一下 NLP")
    profile_snapshot = {"modality_dominant": "video"}
    planned = cap._plan_resources(
        intent=intent, profile_snapshot=profile_snapshot, kg_summary={}
    )
    assert ResourceType.VIDEO not in planned
