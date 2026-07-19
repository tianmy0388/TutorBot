from __future__ import annotations

import networkx as nx
import pytest

from tutor.capabilities.path_planning import PathPlanningCapability
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.knowledge_graph.planner import KGPathPlanner
from tutor.services.knowledge_graph.schema import KGNode, KnowledgeGraph
from tutor.services.knowledge_graph.schema import PlannedPath as KGPlannedPath
from tutor.services.learner_profile.schema import LearnerProfile
from tutor.services.learner_profile.store import ProfileStore


@pytest.mark.asyncio
async def test_path_capability_returns_and_persists_real_version_bound_payload(tmp_path):
    store = ProfileStore(tmp_path / "profiles.db")
    await store.init()
    profile = LearnerProfile(user_id="local-user", version=3, event_watermark=5)
    profile.knowledge_map.set("attention", 0.5)
    await store.replace(profile, source="test")
    model = KnowledgeGraph(
        course="course",
        nodes=[
            KGNode(id="attention", name="Attention"),
            KGNode(id="transformer", name="Transformer", prerequisites=["attention"]),
        ],
    )
    graph = nx.DiGraph([("attention", "transformer")])

    class KG:
        def default_course(self):
            return "course"

        def has_course(self, course):
            return course == "course"

        def get_graph(self, course):
            return model, graph

        def plan_for_learner(self, course, learner, *, path_id=""):
            return KGPathPlanner().plan(model, graph, learner, path_id=path_id)

    capability = PathPlanningCapability(profile_store=store, kg_service=KG())
    result = await capability.run(
        UnifiedContext(user_id="local-user", metadata={"course": "course"}),
        StreamBus(),
    )

    assert result.payload["profile_version"] == 3
    assert [node["id"] for node in result.payload["nodes"]] == [
        "attention",
        "transformer",
    ]
    assert (await store.get_latest_path("local-user")).profile_version == 3
    await store.close()


@pytest.mark.asyncio
async def test_path_capability_preserves_named_path_selection(tmp_path):
    store = ProfileStore(tmp_path / "profiles.db")
    await store.init()
    await store.replace(LearnerProfile(user_id="local-user"), source="test")
    requested: list[str] = []
    model = KnowledgeGraph(course="course", nodes=[KGNode(id="one", name="One")])
    graph = nx.DiGraph()
    graph.add_node("one")

    class KG:
        def default_course(self):
            return "course"

        def has_course(self, course):
            return True

        def get_graph(self, course):
            return model, graph

        def plan_for_learner(self, course, learner, *, path_id=""):
            requested.append(path_id)
            return KGPathPlanner().plan(model, graph, learner, path_id=path_id)

    await PathPlanningCapability(profile_store=store, kg_service=KG()).run(
        UnifiedContext(user_id="local-user", metadata={"path_id": "guided"}),
        StreamBus(),
    )
    assert requested == ["guided"]
    await store.close()


@pytest.mark.asyncio
async def test_path_capability_without_profile_is_typed_empty(tmp_path):
    store = ProfileStore(tmp_path / "profiles.db")
    await store.init()
    result = await PathPlanningCapability(profile_store=store).run(
        UnifiedContext(user_id="missing"), StreamBus()
    )
    assert result.payload == {
        "status": "empty",
        "code": "LEARNING_PROFILE_NOT_FOUND",
        "nodes": [],
        "edges": [],
    }
    assert await store.get_latest_path("missing") is None
    await store.close()


@pytest.mark.asyncio
async def test_path_business_write_is_rejected_after_claim_loss(tmp_path):
    store = ProfileStore(tmp_path / "profiles.db")
    await store.init()
    await store.replace(LearnerProfile(user_id="local-user", version=2), source="test")

    class EmptyKG:
        def default_course(self):
            return ""

    async def reject(operation):
        return False

    with pytest.raises(PermissionError, match="claim"):
        await PathPlanningCapability(profile_store=store, kg_service=EmptyKG()).run(
            UnifiedContext(
                user_id="local-user",
                metadata={"_claim_guard": reject},
            ),
            StreamBus(),
        )
    assert await store.get_latest_path("local-user") is None
    await store.close()


@pytest.mark.asyncio
async def test_nonempty_kg_falls_back_when_declared_plan_is_empty(tmp_path):
    store = ProfileStore(tmp_path / "profiles.db")
    await store.init()
    await store.replace(LearnerProfile(user_id="local-user", version=2), source="test")
    model = KnowledgeGraph(
        course="course",
        nodes=[
            KGNode(id="foundation", name="Foundation"),
            KGNode(id="advanced", name="Advanced", prerequisites=["foundation"]),
        ],
    )
    graph = nx.DiGraph([("foundation", "advanced")])

    class EmptyDeclaredPathKG:
        def default_course(self):
            return "course"

        def has_course(self, course):
            return course == "course"

        def get_graph(self, course):
            return model, graph

        def plan_for_learner(self, course, learner):
            return KGPlannedPath(path_id="declared-empty", course=course)

    result = await PathPlanningCapability(
        profile_store=store,
        kg_service=EmptyDeclaredPathKG(),
    ).run(
        UnifiedContext(user_id="local-user", metadata={"course": "course"}),
        StreamBus(),
    )

    assert [node["id"] for node in result.payload["nodes"]] == [
        "foundation",
        "advanced",
    ]
    assert all(node["id"] in model.node_ids() for node in result.payload["nodes"])
    await store.close()
