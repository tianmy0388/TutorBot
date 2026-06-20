"""Tests for :mod:`tutor.agents.profile.*` (without an LLM).

We mock the LLM so the agents can be exercised end-to-end without hitting
any real provider. Real LLM behaviour is verified by the live e2e test.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tutor.agents.profile.cognitive_diagnostic import CognitiveDiagnosticAgent
from tutor.agents.profile.feature_extractor import FeatureExtractorAgent
from tutor.agents.profile.profile_updater import ProfileUpdaterAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.learner_profile.builder import (
    DialogueSignal,
    ExerciseResult,
)
from tutor.services.learner_profile.schema import (
    CognitiveStyle,
    LearnerProfile,
    ProfileDiff,
    apply_diff,
    empty_profile,
)


@pytest.fixture(autouse=True)
def _reset_profile_singleton():
    """Reset profile builder singleton between tests."""
    from tutor.services.learner_profile import (
        reset_profile_builder,
        reset_profile_store,
    )

    reset_profile_builder()
    reset_profile_store()
    yield
    reset_profile_builder()
    reset_profile_store()


def _mock_llm(response_content: str):
    """Build a mock LLM provider that returns a fixed response."""
    from tutor.services.llm.base import LLMResponse

    llm = MagicMock()
    llm.model = "mock-model"
    llm.call = AsyncMock(
        return_value=LLMResponse(
            content=response_content, model="mock-model", finish_reason="stop"
        )
    )
    return llm


# ---------------------------------------------------------------------------
# FeatureExtractorAgent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feature_extractor_parses_json(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))

    agent = FeatureExtractorAgent(llm=_mock_llm(
        '{"major": "CS", "level": "graduate", '
        '"knowledge": {"LSTM": 0.3, "反向传播": 0.8}, '
        '"cognitive_style": "visual", '
        '"motivation": {"goal_type": "project_build", "urgency": "high", '
        '"self_efficacy": 0.6, "goal_description": "完成项目"}, '
        '"modality": {"video": 0.9}, "confidence": 0.85}'
    ))
    ctx = UnifiedContext(user_id="u1", user_message="我想学LSTM")
    signal = await agent.process(ctx)
    assert isinstance(signal, DialogueSignal)
    assert signal.confidence == pytest.approx(0.85)
    assert signal.extracted_features["major"] == "CS"
    assert signal.extracted_features["knowledge"]["LSTM"] == 0.3


@pytest.mark.asyncio
async def test_feature_extractor_handles_invalid_json(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    agent = FeatureExtractorAgent(llm=_mock_llm("not valid JSON at all"))
    ctx = UnifiedContext(user_id="u2", user_message="x")
    signal = await agent.process(ctx)
    # Falls back to empty features (only metadata key may be auto-injected)
    assert signal.confidence == 0.5  # default
    assert signal.extracted_features.get("knowledge") in (None, {})


@pytest.mark.asyncio
async def test_feature_extractor_emits_through_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    agent = FeatureExtractorAgent(llm=_mock_llm('{"confidence": 0.7, "knowledge": {}}'))

    ctx = UnifiedContext(user_id="u3", user_message="hello")
    bus = StreamBus()
    events_received: list[str] = []

    # Subscribe BEFORE running the agent to avoid race condition
    q = bus.subscribe()

    async def collect():
        while True:
            evt = await q.get()
            if evt is None:
                return
            events_received.append(evt.type.value)

    task = asyncio.create_task(collect())
    # Yield control so the consumer can register before events arrive
    await asyncio.sleep(0)
    signal = await agent.process(ctx, stream=bus)
    await bus.done()
    await asyncio.wait_for(task, timeout=2)

    assert "stage_start" in events_received
    assert "thinking" in events_received
    assert "stage_end" in events_received


# ---------------------------------------------------------------------------
# ProfileUpdaterAgent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_updater_applies_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    agent = ProfileUpdaterAgent()

    # Seed a profile
    from tutor.services.learner_profile.builder import get_profile_builder
    builder = get_profile_builder()
    await builder.initialize()
    await builder.create_blank("u4")

    ctx = UnifiedContext(user_id="u4", user_message="x")
    signal = DialogueSignal(
        raw_text="x",
        extracted_features={
            "knowledge": {"LSTM": 0.4},
            "cognitive_style": "active",
        },
        confidence=0.8,
    )
    ctx.metadata["profile_signal"] = signal

    profile = await agent.process(ctx)
    assert profile.knowledge_map.get("LSTM") == pytest.approx(0.4)
    assert profile.cognitive_style == CognitiveStyle.ACTIVE
    assert ctx.metadata["learner_profile"] is profile


@pytest.mark.asyncio
async def test_profile_updater_applies_exercise_results(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    agent = ProfileUpdaterAgent()
    from tutor.services.learner_profile.builder import get_profile_builder
    await get_profile_builder().initialize()
    await get_profile_builder().create_blank("u5")

    ctx = UnifiedContext(user_id="u5", user_message="x")
    ctx.metadata["exercise_results"] = [
        ExerciseResult(concept="X", correct=True, difficulty=5),
        ExerciseResult(concept="Y", correct=False, mistake_type="misread"),
    ]
    profile = await agent.process(ctx)
    assert profile.knowledge_map.get("X") > 0
    assert profile.knowledge_map.get("Y") == pytest.approx(-0.05) or profile.knowledge_map.get("Y") == 0.0
    assert any(p.mistake_type == "misread" for p in profile.error_patterns)


@pytest.mark.asyncio
async def test_profile_updater_no_signals_returns_current(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    agent = ProfileUpdaterAgent()
    from tutor.services.learner_profile.builder import get_profile_builder
    await get_profile_builder().initialize()
    p = await get_profile_builder().create_blank("u6")

    ctx = UnifiedContext(user_id="u6", user_message="x")
    out = await agent.process(ctx)
    assert out.user_id == p.user_id
    assert out.version == p.version


# ---------------------------------------------------------------------------
# CognitiveDiagnosticAgent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cognitive_diagnostic_returns_questions(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    agent = CognitiveDiagnosticAgent(llm=_mock_llm(
        '{"questions": ['
        '{"concept": "LSTM", "question": "LSTM 是如何解决长期依赖的？", '
        '"why": "验证门控理解", "difficulty": 3}'
        ']}'
    ))

    ctx = UnifiedContext(user_id="u7", user_message="讲讲 LSTM")
    ctx.metadata["learner_profile"] = LearnerProfile(
        user_id="u7",
        knowledge_map=LearnerProfile.model_fields["knowledge_map"].default_factory(),
    )
    # Manually set weak concepts
    profile = ctx.metadata["learner_profile"]
    profile.knowledge_map.set("LSTM", 0.2)

    questions = await agent.process(ctx)
    assert len(questions) == 1
    assert questions[0]["concept"] == "LSTM"
    assert "?" in questions[0]["question"] or "？" in questions[0]["question"]


@pytest.mark.asyncio
async def test_cognitive_diagnostic_fallback_when_invalid_json(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    agent = CognitiveDiagnosticAgent(llm=_mock_llm("garbage"))

    ctx = UnifiedContext(user_id="u8", user_message="x")
    profile = LearnerProfile(user_id="u8")
    profile.knowledge_map.set("foo", 0.2)
    ctx.metadata["learner_profile"] = profile

    questions = await agent.process(ctx)
    # Falls back to template questions
    assert len(questions) >= 1
    assert all("question" in q for q in questions)
