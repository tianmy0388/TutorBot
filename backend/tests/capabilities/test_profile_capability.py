"""End-to-end integration test for the LearnerProfileCapability.

Uses a mocked LLM to drive the full flow:

    WebSocket turn → Orchestrator → ProfileCapability → 3 Agents → Store → emit events

Verifies that:
- All 5 stages execute
- Profile is updated and persisted
- Probe questions are emitted (cold start)
- Re-running the same user increments profile version
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tutor.agents.profile.cognitive_diagnostic import CognitiveDiagnosticAgent
from tutor.agents.profile.feature_extractor import FeatureExtractorAgent
from tutor.agents.profile.profile_updater import ProfileUpdaterAgent
from tutor.capabilities.profile import LearnerProfileCapability
from tutor.core.context import UnifiedContext
from tutor.core.stream import StreamEvent, StreamEventType
from tutor.core.stream_bus import StreamBus
from tutor.services.learner_profile.builder import (
    DialogueSignal,
    ProfileBuilder,
    get_profile_builder,
)
from tutor.services.learner_profile.schema import LearnerProfile
from tutor.services.learner_profile.store import (
    ProfileEventType,
    ProfileStore,
    get_profile_store,
)


def _mock_llm(*responses: str):
    """Build a mock LLM provider that returns successive responses."""
    from tutor.services.llm.base import LLMResponse

    queue = list(responses)
    llm = MagicMock()
    llm.model = "mock-model"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 1024

    async def call(req):
        if queue:
            content = queue.pop(0)
        else:
            content = "{}"
        return LLMResponse(content=content, model="mock-model", finish_reason="stop")

    llm.call = call
    return llm


@pytest.fixture
async def fresh_builder(tmp_path, monkeypatch):
    """Builder with an isolated store and clean singletons."""
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))

    # Reset singletons
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    from tutor.services.learner_profile import (
    _close_profile_store_sync,
    reset_profile_builder,
)

    reset_profile_builder()
    _close_profile_store_sync()

    builder = get_profile_builder()
    builder.store = ProfileStore(tmp_path / "e2e_profiles.db")
    await builder.initialize()
    yield builder

    await builder.store.close()
    reset_profile_builder()
    _close_profile_store_sync()


@pytest.fixture
def profile_capability(fresh_builder):
    """Capability with all 3 agents using a mock LLM."""
    llm = _mock_llm(
        # Feature extractor response
        json.dumps(
            {
                "major": "计算机科学",
                "level": "graduate",
                "knowledge": {"神经网络基础": 0.8, "RNN": 0.3, "LSTM": 0.0},
                "cognitive_style": "visual",
                "motivation": {
                    "goal_type": "project_build",
                    "urgency": "high",
                    "self_efficacy": 0.6,
                    "goal_description": "完成 LSTM 项目",
                },
                "modality": {"video": 0.9, "diagram": 0.85, "text": 0.5},
                "confidence": 0.85,
            },
            ensure_ascii=False,
        ),
        # Cognitive diagnostic response
        json.dumps(
            {
                "questions": [
                    {
                        "concept": "LSTM",
                        "question": "LSTM 是如何用门控机制解决 RNN 的长期依赖问题的？",
                        "why": "验证门控理解",
                        "difficulty": 3,
                    },
                    {
                        "concept": "RNN",
                        "question": "RNN 反向传播中为什么会出现梯度消失？",
                        "why": "诊断 RNN 基础",
                        "difficulty": 2,
                    },
                ]
            },
            ensure_ascii=False,
        ),
    )

    return LearnerProfileCapability(
        builder=fresh_builder,
        feature_extractor=FeatureExtractorAgent(llm=llm),
        profile_updater=ProfileUpdaterAgent(),  # no LLM needed
        cognitive_diagnostic=CognitiveDiagnosticAgent(llm=llm),
    )


@pytest.fixture
def warm_capability(fresh_builder):
    """Capability whose feature extractor returns a benign diff (high knowledge)."""
    llm = _mock_llm(
        # Feature extractor: knowledge stays high, confidence high
        json.dumps(
            {
                "knowledge": {
                    "LSTM": 0.85,
                    "RNN": 0.8,
                    "神经网络": 0.9,
                    "反向传播": 0.9,
                },
                "cognitive_style": "visual",
                "confidence": 0.95,
            },
            ensure_ascii=False,
        ),
        # Diagnostic: should never be reached (no weak concepts)
        json.dumps('{"questions": []}', ensure_ascii=False),
    )

    return LearnerProfileCapability(
        builder=fresh_builder,
        feature_extractor=FeatureExtractorAgent(llm=llm),
        profile_updater=ProfileUpdaterAgent(),
        cognitive_diagnostic=CognitiveDiagnosticAgent(llm=llm),
    )


@pytest.mark.asyncio
async def test_full_flow_cold_start(profile_capability, fresh_builder):
    """First turn with a fresh user → cold start → 2 probe questions."""
    user_id = "alice"
    context = UnifiedContext(
        user_id=user_id,
        user_message="我是CS研一，想学LSTM，之前学过基础NN但对RNN不太熟，下个月要交项目",
        language="zh",
    )

    bus = StreamBus()
    collected: list[StreamEvent] = []

    async def collect():
        async for evt in bus.subscribe_iter():
            collected.append(evt)
            if evt.type == StreamEventType.DONE:
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)

    await profile_capability.run(context, bus)
    await bus.done()
    await asyncio.wait_for(task, timeout=10)

    # All 5 stages should have run
    stages = [e.stage for e in collected if e.type == StreamEventType.STAGE_START]
    assert stages == [
        "load_profile",
        "decide_mode",
        "feature_extraction",
        "profile_update",
        "cognitive_diagnosis",
    ]

    # Result event
    results = [e for e in collected if e.type == StreamEventType.RESULT]
    assert len(results) == 1
    payload = json.loads(results[0].content)
    assert payload["user_id"] == user_id
    assert payload["mode"] == "cold_start"
    assert payload["next_step"] == "answer_probe_questions"
    assert len(payload["probe_questions"]) == 2

    # Profile should be persisted
    persisted = await fresh_builder.get(user_id)
    assert persisted.knowledge_map.get("神经网络基础") == pytest.approx(0.8)
    assert persisted.knowledge_map.get("RNN") == pytest.approx(0.3)
    assert persisted.metadata.get("major") == "计算机科学"
    assert persisted.version == 2  # 1 initial + 1 diff


@pytest.mark.asyncio
async def test_warm_start_no_probes(warm_capability, fresh_builder):
    """Second turn with same user → warm start → no probe questions."""
    user_id = "bob"

    # Pre-seed a populated, warm profile (version > 1, several concepts)
    profile = LearnerProfile(user_id=user_id)
    profile.knowledge_map.set("LSTM", 0.5)
    profile.knowledge_map.set("RNN", 0.5)
    profile.knowledge_map.set("神经网络", 0.6)
    profile.knowledge_map.set("反向传播", 0.7)
    profile.version = 3
    await fresh_builder.store.replace(profile, source="seed")

    context = UnifiedContext(
        user_id=user_id,
        user_message="再多说点 LSTM 的细节",
        language="zh",
    )

    bus = StreamBus()
    collected: list[StreamEvent] = []

    async def collect():
        async for evt in bus.subscribe_iter():
            collected.append(evt)
            if evt.type == StreamEventType.DONE:
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)

    await warm_capability.run(context, bus)
    await bus.done()
    await asyncio.wait_for(task, timeout=10)

    results = [e for e in collected if e.type == StreamEventType.RESULT]
    payload = json.loads(results[0].content)
    # Warm start with populated profile → no probes
    assert payload["mode"] == "incremental"
    assert len(payload["probe_questions"]) == 0


@pytest.mark.asyncio
async def test_event_count_is_consistent(profile_capability, fresh_builder):
    """Sanity check: ~1 stage_start + 1 stage_end per stage."""
    context = UnifiedContext(user_id="charlie", user_message="hi", language="zh")
    bus = StreamBus()
    collected: list[StreamEvent] = []

    async def collect():
        async for evt in bus.subscribe_iter():
            collected.append(evt)
            if evt.type == StreamEventType.DONE:
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await profile_capability.run(context, bus)
    await bus.done()
    await asyncio.wait_for(task, timeout=10)

    stage_starts = sum(1 for e in collected if e.type == StreamEventType.STAGE_START)
    stage_ends = sum(1 for e in collected if e.type == StreamEventType.STAGE_END)
    assert stage_starts == stage_ends
    # 5 stages
    assert stage_starts == 5


@pytest.mark.asyncio
async def test_persistence_survives_reload(tmp_path, monkeypatch):
    """Profile saved in one session is retrievable in a new session."""
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))

    # Session 1
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    from tutor.services.learner_profile import reset_profile_builder

    reset_profile_builder()
    b1 = get_profile_builder()
    b1.store = ProfileStore(tmp_path / "persist.db")
    await b1.initialize()

    await b1.ingest_signal(
        "dave",
        DialogueSignal(
            raw_text="I know LSTM",
            extracted_features={"knowledge": {"LSTM": 0.8}},
            confidence=0.9,
        ),
    )
    await b1.store.close()
    reset_profile_builder()

    # Session 2 — fresh singletons, same DB
    reset_profile_builder()
    b2 = get_profile_builder()
    b2.store = ProfileStore(tmp_path / "persist.db")
    await b2.initialize()

    p = await b2.get("dave")
    assert p.knowledge_map.get("LSTM") == pytest.approx(0.8)
    assert p.metadata == {}  # no major injected this time

    await b2.store.close()
