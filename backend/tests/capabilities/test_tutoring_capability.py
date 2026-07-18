"""End-to-end test for TutoringCapability."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from tutor.agents.tutor.multimodal_enrichment import MultiModalEnrichmentAgent
from tutor.agents.tutor.question_understanding import (
    QuestionUnderstandingAgent,
)
from tutor.agents.tutor.tutoring import TutoringAgent
from tutor.capabilities.tutoring import TutoringCapability
from tutor.core.context import UnifiedContext
from tutor.core.stream import StreamEventType
from tutor.core.stream_bus import StreamBus
from tutor.services.learner_profile import _close_profile_store_sync
from tutor.services.learner_profile.builder import (
    get_profile_builder,
)
from tutor.services.learner_profile.store import ProfileStore
from tutor.services.llm.base import LLMResponse
from tutor.services.search import SearchOutcome, SearchSource
from tutor.services.tutor.service import TutorService, reset_tutor_service


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


@pytest.fixture
async def fresh_builder(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    from tutor.services.learner_profile import reset_profile_builder

    reset_profile_builder()
    _close_profile_store_sync()

    builder = get_profile_builder()
    builder.store = ProfileStore(tmp_path / "tutor_e2e.db")
    await builder.initialize()
    yield builder
    await builder.store.close()
    reset_profile_builder()
    _close_profile_store_sync()
    reset_tutor_service()


@pytest.fixture
def tutor_capability(fresh_builder):
    import pathlib

    from tutor.services.config.settings import get_settings

    settings = get_settings()
    llm = _mock_llm(
        # Question understanding
        json.dumps({
            "question_type": "concept",
            "concepts": ["LSTM"],
            "difficulty": 3,
            "student_intent": "理解 LSTM",
            "follow_up_questions": ["LSTM 在哪些任务上效果好？"],
            "confidence": 0.9,
        }, ensure_ascii=False),
        # Tutoring answer
        json.dumps({
            "tldr": "LSTM 是带门控的 RNN。",
            "intuition": "像带备忘录的学生。",
            "principle": "三个门控制信息流。",
            "example": "nn.LSTM(10, 20)",
            "follow_up_suggestion": "下一步学 GRU。",
            "related_concepts": ["GRU", "RNN"],
            "confidence": 0.9,
        }, ensure_ascii=False),
        # Enrichment
        json.dumps({
            "suggestions": [
                {"type": "diagram", "title": "LSTM 思维导图", "content": "mindmap\n  root((LSTM))", "confidence": 0.9}
            ]
        }),
    )
    return TutoringCapability(
        builder=fresh_builder,
        tutor_service=TutorService(kb_dir=pathlib.Path(settings.kb_dir)),
        question_agent=QuestionUnderstandingAgent(llm=llm),
        tutoring_agent=TutoringAgent(llm=llm),
        enrichment_agent=MultiModalEnrichmentAgent(llm=llm),
    )


class _SearchExecutor:
    def __init__(self, outcome: SearchOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, bool]] = []

    async def execute(self, query: str, *, conversation_enabled: bool):
        self.calls.append((query, conversation_enabled))
        return self.outcome


@pytest.mark.asyncio
async def test_web_sources_are_added_to_answer_context_and_result(tutor_capability) -> None:
    source = SearchSource(
        title="Current LSTM source",
        url="https://example.com/lstm",
        excerpt="Current evidence",
        provider="fake",
        retrieved_at=datetime.now(UTC),
    )
    search = _SearchExecutor(SearchOutcome(search_used=True, sources=(source,)))
    tutor_capability.search_executor = search
    context = UnifiedContext(
        user_id="web-user",
        user_message="LSTM 最近有什么进展？",
        web_search_enabled=True,
    )

    result = await tutor_capability.run(context, StreamBus())

    assert search.calls == [("LSTM 最近有什么进展？\n\n相关概念：LSTM", True)]
    assert context.metadata["search_used"] is True
    assert "Current evidence" in context.metadata["answer_context"]
    assert result.payload["search_used"] is True
    assert result.payload["sources"][0]["url"] == "https://example.com/lstm"


@pytest.mark.asyncio
async def test_web_search_unavailable_emits_one_degradation_and_continues(
    tutor_capability,
) -> None:
    tutor_capability.search_executor = _SearchExecutor(
        SearchOutcome(
            unavailable=True,
            degradation_code="WEB_SEARCH_UNAVAILABLE",
        )
    )
    bus = StreamBus()
    queue = bus.subscribe()
    result = await tutor_capability.run(
        UnifiedContext(
            user_id="web-user",
            user_message="current question",
            web_search_enabled=True,
        ),
        bus,
    )
    await bus.close()
    events = []
    while (event := await queue.get()) is not None:
        events.append(event)
    codes = [
        event.metadata.get("code")
        for event in events
        if event.metadata.get("code") == "WEB_SEARCH_UNAVAILABLE"
    ]

    assert codes == ["WEB_SEARCH_UNAVAILABLE"]
    assert result.assistant_message
    assert result.payload["search_used"] is False
    assert result.payload["sources"] == []


@pytest.mark.asyncio
async def test_caught_understanding_error_redacts_secret_and_emits_stable_code(
    tutor_capability,
    capsys,
):
    secret = "SECRET_TOKEN_TUTORING_123"

    class FailingQuestionAgent:
        async def process(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError(secret)

    tutor_capability.question_agent = FailingQuestionAgent()
    bus = StreamBus()
    queue = bus.subscribe()
    await tutor_capability.run(
        UnifiedContext(user_id="secret-tutor", user_message="解释 LSTM"),
        bus,
    )
    await bus.close()
    events = []
    while (event := await queue.get()) is not None:
        events.append(event.to_dict())

    captured = capsys.readouterr()
    public_blob = json.dumps(events, ensure_ascii=False, default=str)
    assert secret not in public_blob + captured.out + captured.err
    assert "TUTORING_QUESTION_UNDERSTANDING_FAILED" in public_blob


@pytest.mark.asyncio
async def test_full_pipeline_emits_all_5_stages(tutor_capability, fresh_builder):
    ctx = UnifiedContext(
        user_id="alice",
        user_message="什么是 LSTM？",
        language="zh",
    )
    bus = StreamBus()
    q = bus.subscribe()
    events: list = []

    async def collect():
        while True:
            evt = await q.get()
            if evt is None:
                return
            events.append(evt)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    result = await tutor_capability.run(ctx, bus)
    await bus.close()
    await asyncio.wait_for(task, timeout=10)

    stages = [e.stage for e in events if e.type == StreamEventType.STAGE_START]
    assert "question_understanding" in stages
    assert "context_retrieval" in stages
    assert "answer_generation" in stages
    assert "multi_modal_enrichment" in stages
    assert "session_recording" in stages

    assert result.payload["answer"]["tldr"]
    assert not [e for e in events if e.type in {StreamEventType.RESULT, StreamEventType.DONE}]


@pytest.mark.asyncio
async def test_result_event_contains_all_layers(tutor_capability, fresh_builder):
    ctx = UnifiedContext(
        user_id="alice",
        user_message="什么是 LSTM？",
        language="zh",
    )
    bus = StreamBus()
    q = bus.subscribe()
    events: list = []

    async def collect():
        while True:
            evt = await q.get()
            if evt is None:
                return
            events.append(evt)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    result = await tutor_capability.run(ctx, bus)
    await bus.close()
    await asyncio.wait_for(task, timeout=10)

    payload = result.payload
    assert "understanding" in payload
    assert "answer" in payload
    assert "enrichments" in payload
    assert payload["answer"]["tldr"]  # has content
    assert len(payload["enrichments"]) >= 1


@pytest.mark.asyncio
async def test_session_recording_persists_history(tutor_capability, fresh_builder):
    ctx = UnifiedContext(
        user_id="bob",
        user_message="什么是 RNN？",
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
    await tutor_capability.run(ctx, bus)
    await bus.done()
    await asyncio.wait_for(task, timeout=10)

    # bob should have 1 turn in history
    history = tutor_capability.tutor_service.get_history("bob")
    assert len(history) == 1
    assert history[0].question == "什么是 RNN？"


@pytest.mark.asyncio
async def test_handles_llm_failure_gracefully(tmp_path, fresh_builder):
    """If all LLM calls fail, capability still completes with fallback answer."""
    failing_llm = MagicMock()
    failing_llm.model = "mock"
    failing_llm.default_temperature = 0.5
    failing_llm.default_max_tokens = 2048

    async def call(req):
        raise RuntimeError("LLM down")

    failing_llm.call = call

    from tutor.agents.tutor.question_understanding import (
        QuestionUnderstandingAgent,
    )

    cap = TutoringCapability(
        builder=fresh_builder,
        question_agent=QuestionUnderstandingAgent(llm=failing_llm),
        tutoring_agent=TutoringAgent(llm=failing_llm),
        enrichment_agent=MultiModalEnrichmentAgent(llm=failing_llm),
    )

    ctx = UnifiedContext(user_id="x", user_message="x", language="zh")
    bus = StreamBus()
    q = bus.subscribe()
    events: list = []

    async def collect():
        while True:
            evt = await q.get()
            if evt is None:
                return
            events.append(evt)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    result = await cap.run(ctx, bus)
    await bus.close()
    await asyncio.wait_for(task, timeout=10)

    assert result.payload["answer"]["tldr"]
    assert not [e for e in events if e.type in {StreamEventType.RESULT, StreamEventType.DONE}]


@pytest.mark.asyncio
async def test_capability_routes_through_orchestrator():
    """Verify capability is registered and discoverable."""
    from tutor.runtime.orchestrator import get_orchestrator

    orch = get_orchestrator()
    cap_names = orch.list_capabilities()
    assert "tutoring" in cap_names


@pytest.mark.asyncio
async def test_follow_up_suggestion_next_step(fresh_builder):
    """When understanding has follow_up_questions, next_step = 'follow_up'."""
    llm = _mock_llm(
        json.dumps({
            "question_type": "concept",
            "concepts": ["X"],
            "difficulty": 2,
            "follow_up_questions": ["Q?"],
            "confidence": 0.8,
        }),
        json.dumps({"tldr": "x", "principle": "y", "confidence": 0.7}),
        json.dumps({"suggestions": []}),
    )
    cap = TutoringCapability(
        builder=fresh_builder,
        question_agent=QuestionUnderstandingAgent(llm=llm),
        tutoring_agent=TutoringAgent(llm=llm),
        enrichment_agent=MultiModalEnrichmentAgent(llm=llm),
    )
    ctx = UnifiedContext(user_id="u", user_message="x", language="zh")
    bus = StreamBus()
    q = bus.subscribe()
    events: list = []

    async def collect():
        while True:
            evt = await q.get()
            if evt is None:
                return
            events.append(evt)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    result = await cap.run(ctx, bus)
    await bus.close()
    await asyncio.wait_for(task, timeout=10)

    payload = result.payload
    assert payload["next_step"] == "follow_up"
