"""Self-introduction → profile built → path rebuilt for the new version.

End-to-end coverage of conversational profile building:

    self-intro message → ingest_dialogue_signal → profile persisted with
    ``metadata.major`` (version ≥ 2, ``to_summary()["major"]`` non-empty)
    → exactly one ``path_rebuild`` follow-up spec →
    ``PathRebuildFollowUpCapability`` consumes ``spec.payload`` →
    ``store.get_path(user, version)`` persisted with matching
    ``profile_version`` → replaying the same version's spec is idempotent
    (returns the existing path, no error, still one path).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from tutor.agents.profile.feature_extractor import FeatureExtractorAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.follow_up import PathRebuildFollowUpCapability
from tutor.services.learner_profile.builder import ProfileBuilder
from tutor.services.learner_profile.dialogue_ingest import ingest_dialogue_signal
from tutor.services.learner_profile.store import ProfileStore


def _mock_llm(payload: dict):
    """Mock LLM returning a fixed JSON payload (test_dialogue_ingest pattern)."""
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
    """Isolated ProfileStore backed by tmp data dir + reset singletons.

    Same pattern as
    backend/tests/services/learner_profile/test_dialogue_ingest.py.
    """
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    from tutor.services.learner_profile import (
        _close_profile_store_sync,
        reset_profile_builder,
    )

    reset_profile_builder()
    _close_profile_store_sync()

    store = ProfileStore(tmp_path / "profiles.db")
    await store.init()
    monkeypatch.setattr(
        "tutor.services.learner_profile.dialogue_ingest.get_profile_store",
        lambda: store,
        raising=False,
    )
    yield store
    await store.close()


@pytest.fixture
def kg_stub():
    """Tiny in-memory KG service double (test_learning_loop.py pattern)."""
    import networkx as nx
    from tutor.services.knowledge_graph.planner import KGPathPlanner
    from tutor.services.knowledge_graph.schema import (
        EdgeType,
        KGEdge,
        KGNode,
        KnowledgeGraph,
    )

    model = KnowledgeGraph(
        course="test-course",
        nodes=[
            KGNode(id="rnn", name="RNN", estimated_hours=1),
            KGNode(
                id="lstm", name="LSTM", prerequisites=["rnn"], estimated_hours=2
            ),
        ],
        edges=[
            KGEdge(**{"from": "rnn", "to": "lstm", "type": EdgeType.PREREQUISITE})
        ],
    )
    graph = nx.DiGraph()
    graph.add_nodes_from(["rnn", "lstm"])
    graph.add_edge("rnn", "lstm")

    class KG:
        def default_course(self):
            return "test-course"

        def has_course(self, course):
            return course == "test-course"

        def get_graph(self, course):
            return model, graph

        def plan_for_learner(self, course, profile):
            return KGPathPlanner().plan(model, graph, profile)

    return KG()


def _context(message: str) -> UnifiedContext:
    return UnifiedContext(
        session_id="sess-1",
        user_id="user-1",
        user_message=message,
        language="zh",
        capability="tutoring",
    )


@pytest.mark.asyncio
async def test_self_intro_builds_profile_and_rebuilds_path(isolated_store, kg_stub):
    builder = ProfileBuilder(store=isolated_store)
    extractor = FeatureExtractorAgent(
        llm=_mock_llm(
            {
                "major": "计算机科学",
                "level": "graduate",
                "knowledge": {"rnn": 0.6},
                "motivation": {"goal_type": "exam_prep", "goal_description": "期末"},
                "confidence": 0.9,
            }
        )
    )

    # 1) Self-intro message through the dialogue ingester (mock LLM returns
    #    major/level/knowledge/motivation).
    ingested, follow_ups = await ingest_dialogue_signal(
        _context("我是CS研一，想学LSTM，之前学过基础NN但对RNN不太熟"),
        StreamBus(),
        builder=builder,
        extractor=extractor,
    )

    # 2) Profile persisted: metadata.major, version ≥ 2, summary carries major.
    assert ingested is True
    profile = await isolated_store.get("user-1")
    assert profile is not None
    assert profile.metadata.get("major") == "计算机科学"
    assert profile.version >= 2
    assert profile.to_summary()["major"] == "计算机科学"

    # 3) Exactly one path_rebuild follow-up spec for the new version.
    assert len(follow_ups) == 1
    spec = follow_ups[0]
    assert spec.kind == "path_rebuild"
    assert spec.dedupe_key == f"path_rebuild:{profile.version}"
    assert spec.payload["user_id"] == "user-1"
    assert spec.payload["profile_version"] == profile.version
    assert spec.payload["profile"]["metadata"]["major"] == "计算机科学"

    # 4) Feed spec.payload into the path-rebuild capability.
    capability = PathRebuildFollowUpCapability(
        profile_store=isolated_store, kg_service=kg_stub
    )
    result = await capability.run(
        UnifiedContext(
            user_id=spec.payload["user_id"],
            capability="path_rebuild",
            metadata=dict(spec.payload),
        ),
        StreamBus(),
    )

    path = await isolated_store.get_path("user-1", profile.version)
    assert path is not None
    assert path.profile_version == profile.version
    assert result.payload["profile_version"] == profile.version
    assert [node["id"] for node in path.nodes] == ["rnn", "lstm"]

    # 5) Replaying the same version's spec is idempotent: returns the
    #    existing path, no error, still one path.
    replay = await capability.run(
        UnifiedContext(
            user_id=spec.payload["user_id"],
            capability="path_rebuild",
            metadata=dict(spec.payload),
        ),
        StreamBus(),
    )
    assert replay.assistant_message == "学习路径已恢复"
    assert replay.payload == result.payload
    still = await isolated_store.get_path("user-1", profile.version)
    assert still is not None
    assert still.profile_version == profile.version
