"""End-to-end test for AssessmentCapability."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tutor.agents.assessment.adaptive_strategy import AdaptiveStrategyEngine
from tutor.agents.assessment.assessment_agent import AssessmentAgent
from tutor.capabilities.assessment import AssessmentCapability
from tutor.core.context import UnifiedContext
from tutor.core.stream import StreamEventType
from tutor.core.stream_bus import StreamBus
from tutor.services.exercise_responses.publisher import publish_submission_event
from tutor.services.exercise_responses.schema import ExerciseSubmission
from tutor.services.exercise_responses.store import ExerciseResponseStore
from tutor.services.learner_profile import _close_profile_store_sync
from tutor.services.learner_profile.builder import (
    get_profile_builder,
)
from tutor.services.learner_profile.store import ProfileStore
from tutor.services.learning_events.schema import (
    EventType,
    LearningEvent,
)
from tutor.services.learning_events.store import LearningEventStore
from tutor.services.llm.base import LLMResponse


@pytest.fixture(autouse=True)
def isolated_data_dir():
    yield


@pytest.fixture
async def workdir():
    tmp = Path(tempfile.mkdtemp(prefix="assessment_e2e_"))
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
async def env_setup(monkeypatch, workdir):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(workdir))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()

    # Profile builder
    from tutor.services.learner_profile import reset_profile_builder

    reset_profile_builder()
    _close_profile_store_sync()
    builder = get_profile_builder()
    builder.store = ProfileStore(workdir / "assessment_e2e_profiles.db")
    await builder.initialize()

    # Seed profile
    from tutor.services.learner_profile.schema import LearnerProfile

    profile = LearnerProfile(user_id="alice")
    profile.knowledge_map.set("LSTM", 0.4)
    profile.knowledge_map.set("RNN", 0.9)
    await builder.store.replace(profile, source="seed")

    # Event store
    event_store = LearningEventStore(workdir / "assessment_e2e_events.db")
    await event_store.init()
    # Seed some events
    await event_store.record_many([
        LearningEvent(user_id="alice", event_type=EventType.RESOURCE_VIEWED, target_id="r1", concept_id="LSTM"),
        LearningEvent(user_id="alice", event_type=EventType.RESOURCE_COMPLETED, target_id="r1", concept_id="LSTM"),
        LearningEvent(user_id="alice", event_type=EventType.EXERCISE_COMPLETED, target_id="e1", concept_id="LSTM", score=0.5),
    ])

    yield {"builder": builder, "event_store": event_store}

    await event_store.close()
    reset_profile_builder()
    _close_profile_store_sync()


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
def capability(env_setup):
    llm = _mock_llm(json.dumps({
        "trajectory": "improving",
        "weak_concepts": ["LSTM"],
        "strong_concepts": ["RNN"],
        "recommendations": ["复习 LSTM 门控", "多做 LSTM 练习"],
        "notes": "整体有进步",
    }, ensure_ascii=False))
    return AssessmentCapability(
        builder=env_setup["builder"],
        event_store=env_setup["event_store"],
        assessment_agent=AssessmentAgent(llm=llm),
        strategy_engine=AdaptiveStrategyEngine(),
        window_hours=168,
    )


@pytest.mark.asyncio
async def test_assessment_sees_published_submission_immediately_once(
    capability,
    env_setup,
    workdir,
) -> None:
    response_store = ExerciseResponseStore(workdir / "assessment-responses.db")
    await response_store.init()
    durable = await response_store.save_submission(
        ExerciseSubmission(
            submission_id="assessment-visible",
            user_id="alice",
            session_id="sess-assessment-visible",
            package_id="pkg-assessment",
            resource_id="resource-assessment",
            question_id="q-assessment",
            question_type="single_choice",
            answer_json="B",
            correct=False,
            score=0.0,
            concept_id="LSTM",
        )
    )
    try:
        assert await publish_submission_event(
            durable,
            response_store=response_store,
            workflow=SimpleNamespace(event_store=env_setup["event_store"]),  # type: ignore[arg-type]
            reconcile=False,
        )

        result = await capability.run(
            UnifiedContext(user_id="alice", user_message="评估"),
            StreamBus(),
        )

        assert result.payload["stats_summary"]["events_analyzed"] == 4
        assert "exercise_scored" in result.payload["stats_summary"]["event_types"]
        stats = await env_setup["event_store"].stats("alice")
        assert stats["exercise_score_avg"] == 0.25
        scored = await env_setup["event_store"].query(
            "alice", event_types=[EventType.EXERCISE_SCORED]
        )
        assert [event.event_id for event in scored] == [
            "exercise-response:assessment-visible"
        ]
    finally:
        await response_store.close()


@pytest.mark.asyncio
async def test_full_pipeline_emits_all_stages(capability):
    ctx = UnifiedContext(user_id="alice", user_message="评估", language="zh")
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
    await capability.run(ctx, bus)
    await bus.close()
    await asyncio.wait_for(task, timeout=10)

    stages = [e.stage for e in events if e.type == StreamEventType.STAGE_START]
    assert "event_collection" in stages
    assert "event_aggregation" in stages
    assert "assessment" in stages
    assert "adaptive_strategy" in stages
    assert "persist_and_emit" in stages


@pytest.mark.asyncio
async def test_caught_collection_error_redacts_secret_and_emits_stable_code(
    capability,
    monkeypatch,
    capsys,
):
    secret = "SECRET_TOKEN_ASSESSMENT_123"

    async def fail_query(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError(secret)

    monkeypatch.setattr(capability.event_store, "query", fail_query)
    bus = StreamBus()
    queue = bus.subscribe()
    await capability.run(UnifiedContext(user_id="alice", user_message="评估"), bus)
    await bus.close()
    events = []
    while (event := await queue.get()) is not None:
        events.append(event.to_dict())

    captured = capsys.readouterr()
    public_blob = json.dumps(events, ensure_ascii=False, default=str)
    assert secret not in public_blob + captured.out + captured.err
    assert "ASSESSMENT_EVENT_COLLECTION_FAILED" in public_blob


@pytest.mark.asyncio
async def test_result_includes_report_and_strategy(capability):
    ctx = UnifiedContext(user_id="alice", user_message="评估")
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
    result = await capability.run(ctx, bus)
    await bus.close()
    await asyncio.wait_for(task, timeout=10)

    assert not [e for e in events if e.type in {StreamEventType.RESULT, StreamEventType.DONE}]
    payload = result.payload

    assert "report" in payload
    assert "strategy" in payload
    assert payload["report"]["user_id"] == "alice"
    assert "LSTM" in payload["report"]["weak_concepts"]
    assert "RNN" in payload["report"]["strong_concepts"]
    assert len(payload["strategy"]["actions"]) >= 1
    assert payload["stats_summary"]["events_analyzed"] >= 3


@pytest.mark.asyncio
async def test_strategy_includes_review_for_low_mastery(capability):
    """Alice has mastery=0.4 (LSTM), so strategy should include REVIEW or TUTORING."""
    ctx = UnifiedContext(user_id="alice", user_message="评估")
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
    result = await capability.run(ctx, bus)
    await bus.close()
    await asyncio.wait_for(task, timeout=10)

    payload = result.payload
    actions = payload["strategy"]["actions"]
    action_types = {a["action_type"] for a in actions}
    # Either review (low mastery) or tutoring (weak concept)
    assert "recommend_review" in action_types or "recommend_tutoring" in action_types


@pytest.mark.asyncio
async def test_assessment_persists_as_event(capability, env_setup):
    ctx = UnifiedContext(user_id="alice", user_message="评估")
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
    await capability.run(ctx, bus)
    await bus.done()
    await asyncio.wait_for(task, timeout=10)

    # An assessment event was recorded
    all_events = await env_setup["event_store"].query("alice", limit=100)
    assessment_events = [
        e for e in all_events if e.target_id == "assessment"
    ]
    assert len(assessment_events) == 1
    assert "report" in assessment_events[0].metadata
    assert "strategy" in assessment_events[0].metadata


@pytest.mark.asyncio
async def test_no_events_produces_empty_report(env_setup):
    # Use a different user with no events
    env_setup["event_store"]  # ensure fixture ran
    llm = _mock_llm(json.dumps({
        "trajectory": "insufficient_data",
        "recommendations": ["开始学习"],
    }, ensure_ascii=False))
    cap = AssessmentCapability(
        builder=env_setup["builder"],
        event_store=env_setup["event_store"],
        assessment_agent=AssessmentAgent(llm=llm),
        strategy_engine=AdaptiveStrategyEngine(),
    )

    ctx = UnifiedContext(user_id="new_user", user_message="评估")
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
    assert payload["report"]["events_analyzed"] == 0
    assert payload["report"]["trajectory"] == "insufficient_data"


@pytest.mark.asyncio
async def test_capability_routes_through_orchestrator():
    """Verify capability is registered and discoverable."""
    from tutor.runtime.orchestrator import get_orchestrator

    orch = get_orchestrator()
    cap_names = orch.list_capabilities()
    assert "assessment" in cap_names
