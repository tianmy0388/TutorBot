"""End-to-end test for :mod:`tutor.services.knowledge_graph.service`.

Uses the AI Introduction course that ships with the project.
"""

from __future__ import annotations

import pytest

from tutor.services.knowledge_graph.service import (
    KnowledgeGraphService,
    get_knowledge_graph_service,
    reset_knowledge_graph_service,
)
from tutor.services.knowledge_graph.schema import PathStatus
from tutor.services.learner_profile.schema import LearnerProfile


@pytest.fixture
def fresh_service():
    """Reset singleton and return a fresh service."""
    reset_knowledge_graph_service()
    svc = KnowledgeGraphService()
    yield svc
    reset_knowledge_graph_service()


def test_list_courses_includes_ai(fresh_service):
    assert "ai_introduction" in fresh_service.list_courses()


def test_default_course(fresh_service):
    course = fresh_service.default_course()
    assert course == "ai_introduction"


def test_get_model_returns_valid_graph(fresh_service):
    model = fresh_service.get_model("ai_introduction")
    assert model.course == "ai_introduction"
    assert len(model.nodes) >= 7


def test_plan_for_learner_with_nlp_path(fresh_service):
    """Student wants to learn NLP — pick nlp_path, plan accordingly."""
    profile = LearnerProfile()
    profile.knowledge_map.set("ai_overview", 0.95)  # completed
    profile.knowledge_map.set("ml_basics", 0.85)  # skipped (above threshold but not 0.95)
    plan = fresh_service.plan_for_learner("ai_introduction", profile, path_id="nlp_path")
    assert plan.path_id == "nlp_path"
    # Sequence: ai_overview → ml_basics → neural_network → rnn → transformer → llm
    seq = [n.node_id for n in plan.nodes]
    assert seq[0] == "ai_overview"
    assert seq[-1] == "llm"
    statuses = {n.node_id: n.status for n in plan.nodes}
    assert statuses["ai_overview"] == PathStatus.COMPLETED
    assert statuses["ml_basics"] == PathStatus.SKIPPED
    assert statuses["neural_network"] == PathStatus.AVAILABLE


def test_plan_for_learner_cv_path(fresh_service):
    profile = LearnerProfile()
    profile.knowledge_map.set("ai_overview", 0.95)
    profile.knowledge_map.set("ml_basics", 0.95)
    plan = fresh_service.plan_for_learner("ai_introduction", profile, path_id="cv_path")
    assert plan.path_id == "cv_path"
    # cv path skips rnn, transformer, llm
    seq = [n.node_id for n in plan.nodes]
    assert "cnn" in seq
    assert "rnn" not in seq
    assert "transformer" not in seq
    assert "llm" not in seq


def test_plan_auto_selection_with_no_mastery(fresh_service):
    profile = LearnerProfile()
    plan = fresh_service.plan_for_learner("ai_introduction", profile)
    # Should pick one of the 3 named paths
    assert plan.path_id in {"cv_path", "nlp_path", "full_path"}


def test_locate_returns_useful_info(fresh_service):
    profile = LearnerProfile()
    profile.knowledge_map.set("ai_overview", 0.95)
    profile.knowledge_map.set("ml_basics", 0.5)
    loc = fresh_service.locate("ai_introduction", profile)
    assert "ai_overview" in loc["mastered"]
    assert "ml_basics" in loc["partial"]
    # neural_network has prereq ml_basics which is partial (> weak_threshold)
    # → should be in next_targets
    assert "neural_network" in loc["next_targets"]


def test_recommend_next_returns_ordered_list(fresh_service):
    profile = LearnerProfile()
    profile.knowledge_map.set("ai_overview", 0.95)
    recs = fresh_service.recommend_next("ai_introduction", profile, limit=2)
    assert len(recs) <= 2
    # First recommendation should be ml_basics (unmastered, no prereqs left)
    assert recs[0].node_id == "ml_basics"


def test_singleton_returns_same_instance():
    svc1 = get_knowledge_graph_service()
    svc2 = get_knowledge_graph_service()
    assert svc1 is svc2


def test_stats(fresh_service):
    stats = fresh_service.stats()
    assert "available_courses" in stats
    assert "ai_introduction" in stats["available_courses"]


def test_ai_intro_no_cycles(fresh_service):
    """The shipped AI course graph should be a DAG (no cycles)."""
    import networkx as nx

    _, graph = fresh_service.get_graph("ai_introduction")
    assert nx.is_directed_acyclic_graph(graph), "AI course KG has cycles!"


def test_ai_intro_all_paths_terminate(fresh_service):
    """Every named learning path should be reachable from its start."""
    model = fresh_service.get_model("ai_introduction")
    for path in model.learning_paths:
        assert len(path.sequence) >= 1
        # No phantom nodes
        for nid in path.sequence:
            assert model.get_node(nid) is not None, f"{path.id} references {nid}"


def test_invalidate_clears_cache(fresh_service):
    fresh_service.get_graph("ai_introduction")  # load
    assert "ai_introduction" in fresh_service.loader._cache
    fresh_service.invalidate("ai_introduction")
    assert "ai_introduction" not in fresh_service.loader._cache
