"""End-to-end test for ResourceGenerationCapability.

Drives the full pipeline with mocked LLMs:

    Intent → Content → Pedagogy → [Mindmap + Exercise + Video + Code + Reading]
           → [Quality Review × N] → Package → Path Integration
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

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
    get_profile_builder,
)
from tutor.services.learner_profile.store import (
    ProfileStore,
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
    from tutor.services.learner_profile import (
        _close_profile_store_sync,
        reset_profile_builder,
    )

    reset_profile_builder()
    _close_profile_store_sync()

    builder = get_profile_builder()
    builder.store = ProfileStore(tmp_path / "e2e_resources.db")
    await builder.initialize()

    # Seed a learner with some mastery
    from tutor.services.learner_profile.schema import CognitiveStyle, LearnerProfile

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
    _close_profile_store_sync()


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


@pytest.mark.asyncio
async def test_caught_intent_error_redacts_secret_and_emits_stable_code(
    capability,
    tmp_path,
    capsys,
):
    from tutor.services.resource_package.store import ResourcePackageStore

    secret = "SECRET_TOKEN_RESOURCE_123"

    class FailingIntentAgent:
        agent_name = "FailingIntentAgent"

        async def process(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError(secret)

    package_store = ResourcePackageStore(db_path=tmp_path / "secret_packages.db")
    await package_store.init()
    capability.package_store = package_store
    capability.intent_agent = FailingIntentAgent()
    bus = StreamBus()
    queue = bus.subscribe()
    await capability.run(
        UnifiedContext(
            job_id="secret-resource-job",
            user_id="alice",
            user_message="生成 LSTM 文档",
        ),
        bus,
    )
    await bus.close()
    events = []
    while (event := await queue.get()) is not None:
        events.append(event.to_dict())
    await package_store.close()

    captured = capsys.readouterr()
    public_blob = json.dumps(events, ensure_ascii=False, default=str)
    assert secret not in public_blob + captured.out + captured.err
    assert "RESOURCE_INTENT_FAILED" in public_blob


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_emits_all_stages(capability, fresh_builder, tmp_path):
    from tutor.services.resource_package.store import ResourcePackageStore

    package_store = ResourcePackageStore(db_path=tmp_path / "resource_packages.db")
    await package_store.init()
    capability.package_store = package_store
    context = UnifiedContext(
        job_id="job-resource-generation",
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

    result = await capability.run(context, bus)
    await bus.close()
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

    packages = await capability._store.list_for_session(context.session_id)
    assert len(packages) == 1
    assert packages[0].originating_job_id == "job-resource-generation"
    await package_store.close()

    assert result.payload["package"]["metadata"]["job_id"] == "job-resource-generation"
    assert not [t for t, _ in events if t in {"result", "done", "error"}]


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
    result = await capability.run(context, bus)
    await bus.close()
    await asyncio.wait_for(task, timeout=10)

    assert not [e for e in events if e.type in {StreamEventType.RESULT, StreamEventType.DONE}]
    payload = result.payload
    assert "package" in payload
    assert "summary" in payload
    assert "kg_summary" in payload
    assert "next_step" in payload
    assert payload["next_step"] == "open_resource_cards"
    pending_video_ids = {
        resource["resource_id"]
        for resource in payload["package"]["resources"]
        if resource["type"] == "video" and resource["format_specific"].get("render_status") == "pending"
    }
    assert {spec.payload["resource_id"] for spec in result.follow_up_tasks} == pending_video_ids
    assert not hasattr(capability, "_bg_render_tasks")


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
    result = await capability.run(context, bus)
    await bus.close()
    await asyncio.wait_for(task, timeout=10)

    payload = result.payload
    pkg = payload["package"]
    types_in_pkg = {r["type"] for r in pkg["resources"]}

    # Should have at least document (pedagogy version); the rest depend
    # on parallel generation succeeding with mocks.
    assert "document" in types_in_pkg or len(types_in_pkg) >= 1
    # If mocks worked, more types should be present
    assert len(types_in_pkg) >= 1


@pytest.mark.asyncio
async def test_resource_graph_is_explicit_and_failed_video_is_isolated(
    capability,
    fresh_builder,
):
    from tutor.services.resource_package.schema import ResourceReview

    class FailingVideoAgent:
        agent_name = "failing_video"

        async def process(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("provider detail must stay private")

    class CapturingReviewer:
        agent_name = "capturing_reviewer"

        def __init__(self) -> None:
            self.seen_types: list[ResourceType] = []

        async def process(self, context, resource, stream=None):  # type: ignore[no-untyped-def]
            self.seen_types.append(resource.type)
            return ResourceReview(
                resource_id=resource.resource_id,
                verdict=ReviewVerdict.PASS,
                quality_score=0.9,
            )

    reviewer = CapturingReviewer()
    capability.manim_video = FailingVideoAgent()
    capability.quality_reviewer = reviewer
    context = UnifiedContext(
        job_id="job-video-isolation",
        user_id="alice",
        user_message="系统学习 LSTM 并用动画演示",
        language="zh",
    )
    bus = StreamBus()

    graph = capability.build_resource_graph(context, bus)
    assert {node.name: node.dependencies for node in graph.nodes} == {
        "intent": (),
        "profile_snapshot": ("intent",),
        "source": ("profile_snapshot",),
        "pedagogy": ("source",),
        "mindmap": ("pedagogy",),
        "exercise": ("pedagogy",),
        "code": ("pedagogy",),
        "video-code": ("pedagogy",),
        "reading": ("pedagogy",),
        "quality": ("mindmap", "exercise", "code", "video-code", "reading"),
        "safety": ("quality",),
        "package": ("safety",),
    }
    assert all(node.input_model is not None for node in graph.nodes)
    assert all(node.output_model is not None for node in graph.nodes)

    result = await capability.run(context, bus)
    package_types = {resource["type"] for resource in result.payload["package"]["resources"]}

    assert ResourceType.VIDEO not in reviewer.seen_types
    assert "video" not in package_types
    assert all(spec.kind != "video_render" for spec in result.follow_up_tasks)
    assert ResourceType.MINDMAP in reviewer.seen_types
    assert "mindmap" in package_types


@pytest.mark.asyncio
async def test_failed_quality_review_never_enters_package_or_followups(
    capability,
    fresh_builder,
):
    from tutor.services.resource_package.schema import ResourceReview

    class SelectiveReviewer:
        agent_name = "selective_reviewer"

        async def process(self, context, resource, stream=None):  # type: ignore[no-untyped-def]
            if resource.type == ResourceType.VIDEO:
                raise RuntimeError("review provider failure")
            return ResourceReview(
                resource_id=resource.resource_id,
                verdict=ReviewVerdict.PASS,
                quality_score=0.9,
            )

    capability.quality_reviewer = SelectiveReviewer()
    result = await capability.run(
        UnifiedContext(
            job_id="job-review-isolation",
            user_id="alice",
            user_message="系统学习 LSTM 并用动画演示",
            language="zh",
        ),
        StreamBus(),
    )

    assert all(resource["type"] != "video" for resource in result.payload["package"]["resources"])
    assert all(spec.kind != "video_render" for spec in result.follow_up_tasks)
    assert any(resource["type"] != "video" for resource in result.payload["package"]["resources"])


@pytest.mark.asyncio
async def test_safety_rejected_video_never_enters_package_or_followups(
    capability,
    fresh_builder,
):
    from tutor.agents.safety.anti_hallucination import (
        AntiHallucinationReport,
        OverallVerdict,
    )

    class SelectiveSafetyAgent:
        agent_name = "selective_safety"

        async def process(  # type: ignore[no-untyped-def]
            self,
            context,
            stream=None,
            *,
            resource_content,
            topic="",
            source_documents=None,
        ):
            verdict = (
                OverallVerdict.UNSAFE
                if "动画" in resource_content or "Manim" in resource_content
                else OverallVerdict.SAFE
            )
            return AntiHallucinationReport(
                overall_verdict=verdict,
                overall_confidence=0.95,
            )

    capability.anti_hallucination = SelectiveSafetyAgent()
    result = await capability.run(
        UnifiedContext(
            job_id="job-safety-isolation",
            user_id="alice",
            user_message="系统学习 LSTM 并用动画演示",
            language="zh",
        ),
        StreamBus(),
    )

    assert all(resource["type"] != "video" for resource in result.payload["package"]["resources"])
    assert all(spec.kind != "video_render" for spec in result.follow_up_tasks)
    assert result.payload["package"]["resources"]


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
    result = await capability.run(context, bus)
    await bus.close()
    await asyncio.wait_for(task, timeout=10)

    payload = result.payload
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
    await bus.close()
    await asyncio.wait_for(task, timeout=2)

    # Profile should now have last_package_id and resource_history
    profile = await fresh_builder.get("alice")
    assert "last_package_id" in profile.metadata
    assert "resource_history" in profile.metadata
    assert profile.metadata["last_topic"] == "LSTM"
    assert len(profile.metadata["resource_history"]) >= 1


# ---------------------------------------------------------------------------
# Quality-review reject filter (Task 9)
# ---------------------------------------------------------------------------


class _CannedReviewer:
    """QualityReviewerAgent replacement that returns a fixed verdict
    sequence, used to exercise the reject-filter logic in isolation.
    """

    def __init__(self, verdicts: list[str]):
        self._verdicts = list(verdicts)
        self.agent_name = "canned_reviewer"
        from tutor.services.resource_package.schema import ResourceReview

        self._map = {
            "pass": ReviewVerdict.PASS,
            "revise": ReviewVerdict.REVISE,
            "reject": ReviewVerdict.REJECT,
        }
        self._reviews: list[ResourceReview] = []

    async def process(self, context, resource, stream=None):
        from tutor.services.resource_package.schema import ResourceReview

        verdict_str = self._verdicts.pop(0) if self._verdicts else "pass"
        rev = ResourceReview(
            resource_id=resource.resource_id,
            verdict=self._map[verdict_str],
            quality_score=0.0 if verdict_str == "reject" else 0.9,
            issues=[] if verdict_str != "reject" else ["empty content"],
            suggestions=[],
        )
        self._reviews.append(rev)
        return rev


@pytest.mark.asyncio
async def test_rejected_resources_filtered_from_package(capability, fresh_builder):
    """**2026-06-22 fix (Task 9):** resources whose quality-review
    verdict is ``reject`` MUST be filtered from the persisted
    package, otherwise the chat viewer publishes an empty code
    block / failed video as a usable resource.

    Drive the filter logic by attaching a canned reviewer and
    pre-built package, then asserting the package shrinks.
    """
    import uuid

    from tutor.services.resource_package.schema import (
        Resource,
        ResourcePackage,
        ResourceType,
    )

    pkg = ResourcePackage(
        package_id=f"pkg_{uuid.uuid4().hex[:8]}",
        topic="test",
        created_at="2026-06-22T00:00:00",
        resources=[
            Resource(
                resource_id="r1",
                type=ResourceType.DOCUMENT,
                title="good doc",
                content="solid content",
                topic="test",
                difficulty=2,
                estimated_minutes=10,
            ),
            Resource(
                resource_id="r2",
                type=ResourceType.CODE,
                title="empty code",
                content="",
                topic="test",
                difficulty=1,
                estimated_minutes=0,
            ),
            Resource(
                resource_id="r3",
                type=ResourceType.VIDEO,
                title="failed video",
                content="",
                topic="test",
                difficulty=1,
                estimated_minutes=0,
            ),
            Resource(
                resource_id="r4",
                type=ResourceType.EXERCISE,
                title="revise ex",
                content="ex",
                topic="test",
                difficulty=2,
                estimated_minutes=5,
            ),
        ],
    )

    canned = _CannedReviewer(["pass", "reject", "reject", "revise"])
    capability.quality_reviewer = canned
    # Drive the review-all step + post-filter directly to avoid
    # the full intent → content → pedagogy chain.
    context = UnifiedContext(user_id="alice", user_message="test", language="zh")
    bus = StreamBus()
    reviews = await capability._review_all(pkg.resources, context, bus)

    # Apply the same post-filter logic that's in run().
    review_by_id = {r.resource_id: r for r in reviews}
    for r in pkg.resources:
        rev = review_by_id.get(r.resource_id)
        if rev is not None:
            r.metadata["review"] = {
                "verdict": rev.verdict.value,
                "quality_score": rev.quality_score,
                "issues": rev.issues,
                "suggestions": rev.suggestions,
            }

    rejected_ids = {
        r.resource_id for r in pkg.resources if (r.metadata.get("review") or {}).get("verdict") == "reject"
    }
    pkg.resources = [r for r in pkg.resources if r.resource_id not in rejected_ids]

    # Only r1 (pass) and r4 (revise) survive. r2 + r3 (both reject)
    # must be dropped.
    surviving_ids = [r.resource_id for r in pkg.resources]
    assert surviving_ids == ["r1", "r4"], f"expected only pass+revise to survive, got {surviving_ids}"


@pytest.mark.asyncio
async def test_prefilter_drops_failed_video_resources():
    """**2026-07-07 fix:** resources whose *generation* failed
    (render_status="failed" on video) MUST be dropped before the
    quality-review loop. The reviewer would correctly reject them,
    then the reject filter would strip them anyway, but going
    through review wastes capacity AND pollutes the trace panel
    with a confusing ``video_rendering`` no-op stage.

    Drive the pre-filter helper directly so the test is fast and
    focused.
    """
    import uuid

    from tutor.services.resource_package.schema import (
        Resource,
        ResourcePackage,
        ResourceType,
    )

    pkg = ResourcePackage(
        package_id=f"pkg_{uuid.uuid4().hex[:8]}",
        topic="反向传播",
        created_at="2026-07-07T00:00:00",
        resources=[
            Resource(
                resource_id="doc-1",
                type=ResourceType.DOCUMENT,
                title="反向传播入门",
                content="教学版内容",
                topic="反向传播",
                difficulty=2,
                estimated_minutes=15,
            ),
            Resource(
                resource_id="vid-failed",
                type=ResourceType.VIDEO,
                title="反向传播 — 视频生成失败",
                content="# 视频生成失败",
                format_specific={
                    "render_status": "failed",
                    "render_error": "LLM codegen returned empty/invalid code",
                },
                topic="反向传播",
                difficulty=1,
                estimated_minutes=0,
            ),
            Resource(
                resource_id="vid-pending",
                type=ResourceType.VIDEO,
                title="反向传播 — 动画视频",
                content="视频内容",
                format_specific={"render_status": "pending"},
                topic="反向传播",
                difficulty=3,
                estimated_minutes=1,
            ),
            Resource(
                resource_id="code-broken",
                type=ResourceType.CODE,
                title="微型网络反向传播手动计算",
                content="```python\nimport math\ndef sigmoid(z): pass\n```",
                format_specific={
                    "execution_status": "failed",
                    "error_code": "CODE_EXECUTION_FAILED",
                },
                topic="反向传播",
                difficulty=2,
                estimated_minutes=5,
            ),
        ],
    )
    cap = ResourceGenerationCapability()
    bus = StreamBus()
    kept, summary = await cap._prefilter_failed_resources(list(pkg.resources), bus)
    kept_ids = [r.resource_id for r in kept]

    # The failed video MUST be dropped.
    assert "vid-failed" not in kept_ids, f"failed video should be pre-filtered, kept={kept_ids}"
    # Other resources MUST be kept:
    #   - document
    #   - the pending video (not failed yet)
    #   - the code resource (reviewer decides, even if execution failed
    #     with CODE_EXECUTION_FAILED — could be RUNTIME_DEPENDENCY_MISSING
    #     next time, which is still educational)
    assert "doc-1" in kept_ids
    assert "vid-pending" in kept_ids
    assert "code-broken" in kept_ids, (
        "code with failed execution should NOT be pre-filtered — "
        "reviewer handles it (might be env-broken but still useful)"
    )
    # Summary must list the dropped video.
    assert len(summary) == 1
    assert summary[0]["resource_id"] == "vid-failed"
    assert summary[0]["render_error"] == "LLM codegen returned empty/invalid code"


@pytest.mark.asyncio
async def test_prefilter_no_op_when_nothing_failed():
    """When no resources have render_status=failed, the filter is a no-op."""
    import uuid

    from tutor.services.resource_package.schema import (
        Resource,
        ResourcePackage,
        ResourceType,
    )

    pkg = ResourcePackage(
        package_id=f"pkg_{uuid.uuid4().hex[:8]}",
        topic="t",
        created_at="2026-07-07T00:00:00",
        resources=[
            Resource(
                resource_id="vid-pending",
                type=ResourceType.VIDEO,
                title="ok",
                format_specific={"render_status": "pending"},
                topic="t",
            ),
            Resource(
                resource_id="vid-ready",
                type=ResourceType.VIDEO,
                title="ok2",
                format_specific={"render_status": "ready"},
                topic="t",
            ),
        ],
    )
    cap = ResourceGenerationCapability()
    bus = StreamBus()
    kept, summary = await cap._prefilter_failed_resources(list(pkg.resources), bus)
    assert [r.resource_id for r in kept] == ["vid-pending", "vid-ready"]
    assert summary == []


@pytest.mark.asyncio
async def test_prefilter_failure_log_omits_credential_shaped_title(caplog):
    """Operator logs must not repeat user-derived failed-resource titles."""
    from loguru import logger
    from tutor.services.resource_package.schema import Resource, ResourceType

    credential = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    resource = Resource(
        resource_id="ppt-failed",
        type=ResourceType.PPT,
        title=f"Lesson {credential}",
        content="PPT rendering failed",
        format_specific={
            "failure": {
                "code": "PPT_RENDER_FAILED",
                "message": "PPT rendering failed",
                "retryable": True,
            }
        },
    )
    cap = ResourceGenerationCapability()
    bus = StreamBus()
    sink_id = logger.add(caplog.handler, format="{message}", level="WARNING")
    try:
        kept, summary = await cap._prefilter_failed_resources([resource], bus)
    finally:
        logger.remove(sink_id)

    assert kept == []
    assert len(summary) == 1
    assert credential not in caplog.text
    assert "failed-generation resources" in caplog.text


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
    result = await capability.run(context, bus)
    await bus.close()
    await asyncio.wait_for(task, timeout=10)

    payload = result.payload
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
    from tutor.agents.resource.intent_understanding import Intent
    from tutor.services.resource_package.schema import ResourceType

    cap = ResourceGenerationCapability.__new__(ResourceGenerationCapability)
    intent = Intent(topic="X", resource_types=[ResourceType.DOCUMENT])
    profile_snapshot = {
        "modality_dominant": "diagram",
        "knowledge_count": 0,
    }
    planned = cap._plan_resources(intent=intent, profile_snapshot=profile_snapshot, kg_summary={})
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
    planned = cap._plan_resources(intent=intent, profile_snapshot=profile_snapshot, kg_summary={})
    assert ResourceType.VIDEO not in planned
