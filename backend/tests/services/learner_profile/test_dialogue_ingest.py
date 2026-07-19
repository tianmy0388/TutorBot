"""Dialogue-driven profile ingestion (conversational profile building)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from tutor.agents.profile.feature_extractor import FeatureExtractorAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.learner_profile.builder import ProfileBuilder
from tutor.services.learner_profile.dialogue_ingest import ingest_dialogue_signal
from tutor.services.learner_profile.store import ProfileStore


def _mock_llm(payload: dict):
    from tutor.services.llm.base import LLMResponse

    llm = MagicMock()
    llm.model = "mock-model"
    llm.default_temperature = 0.3
    llm.default_max_tokens = 2048

    async def call(req):
        return LLMResponse(
            content=json.dumps(payload), model="mock-model", finish_reason="stop"
        )

    llm.call = call
    return llm


@pytest.fixture
async def isolated_store(tmp_path, monkeypatch):
    """Isolated ProfileStore backed by tmp data dir + reset singletons."""
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    # Singleton-reset pattern used by
    # backend/tests/capabilities/test_profile_capability.py::fresh_builder
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    from tutor.services.learner_profile import (
        _close_profile_store_sync,
        reset_profile_builder,
    )

    reset_profile_builder()
    _close_profile_store_sync()

    # Lifecycle per backend/tests/services/learner_profile/test_store.py:
    # ProfileStore needs init() to create tables, close() on teardown.
    store = ProfileStore(tmp_path / "profiles.db")
    await store.init()
    monkeypatch.setattr(
        "tutor.services.learner_profile.dialogue_ingest.get_profile_store",
        lambda: store,
        raising=False,
    )
    yield store
    await store.close()


def _context(message: str) -> UnifiedContext:
    return UnifiedContext(
        session_id="sess-1",
        user_id="user-1",
        user_message=message,
        language="zh",
        capability="tutoring",
    )


@pytest.mark.asyncio
async def test_self_intro_ingests_profile_and_schedules_path_rebuild(
    isolated_store,
):
    builder = ProfileBuilder(store=isolated_store)
    extractor = FeatureExtractorAgent(
        llm=_mock_llm(
            {
                "major": "计算机科学",
                "level": "graduate",
                "knowledge": {"neural_networks": 0.6},
                "motivation": {"goal_type": "exam_prep", "goal_description": "期末"},
                "confidence": 0.9,
            }
        )
    )
    bus = StreamBus()
    queue = bus.subscribe()

    ingested, follow_ups = await ingest_dialogue_signal(
        _context("我是CS研一，想学LSTM，之前学过基础NN但对RNN不太熟"),
        bus,
        builder=builder,
        extractor=extractor,
    )

    assert ingested is True
    profile = await isolated_store.get("user-1")
    assert profile is not None
    assert profile.metadata.get("major") == "计算机科学"
    assert profile.version >= 2
    assert len(follow_ups) == 1
    spec = follow_ups[0]
    assert spec.kind == "path_rebuild"
    assert spec.dedupe_key == f"path_rebuild:{profile.version}"
    assert spec.payload["user_id"] == "user-1"
    assert spec.payload["profile_version"] == profile.version
    assert spec.payload["profile"]["metadata"]["major"] == "计算机科学"
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert any(
        (getattr(e, "metadata", None) or {}).get("profile_updated") is True
        for e in events
    )


@pytest.mark.asyncio
async def test_plain_question_skips_extractor(isolated_store):
    extractor = FeatureExtractorAgent(llm=_mock_llm({"major": "不应出现"}))
    ingested, follow_ups = await ingest_dialogue_signal(
        _context("什么是反向传播？"),
        StreamBus(),
        builder=ProfileBuilder(store=isolated_store),
        extractor=extractor,
    )
    assert ingested is False
    assert follow_ups == ()
    assert await isolated_store.get("user-1") is None


@pytest.mark.asyncio
async def test_extractor_failure_degrades_to_noop(isolated_store):
    class _BoomExtractor:
        async def process(self, context, stream=None):
            raise RuntimeError("llm down")

    ingested, follow_ups = await ingest_dialogue_signal(
        _context("我是CS研一"),
        StreamBus(),
        builder=ProfileBuilder(store=isolated_store),
        extractor=_BoomExtractor(),
    )
    assert ingested is False
    assert follow_ups == ()
