"""Tests for :mod:`tutor.services.knowledge_graph.planner`."""

from __future__ import annotations

import networkx as nx
import pytest

from tutor.services.knowledge_graph.planner import KGPathPlanner
from tutor.services.knowledge_graph.schema import (
    EdgeType,
    KGEdge,
    KGNode,
    KnowledgeGraph,
    LearningPath,
    PathStatus,
)
from tutor.services.learner_profile.schema import LearnerProfile


def _toy_graph():
    """Build a small graph: a → b → c, a → c, with a learning_path."""
    model = KnowledgeGraph(
        course="toy",
        nodes=[
            KGNode(id="a", name="A", difficulty=1, estimated_hours=2.0),
            KGNode(id="b", name="B", difficulty=2, estimated_hours=3.0),
            KGNode(id="c", name="C", difficulty=3, estimated_hours=4.0),
        ],
        edges=[
            KGEdge(**{"from": "a"}, to="b", type=EdgeType.PREREQUISITE),
            KGEdge(**{"from": "a"}, to="c", type=EdgeType.PREREQUISITE),
            KGEdge(**{"from": "b"}, to="c", type=EdgeType.PREREQUISITE),
        ],
        learning_paths=[
            LearningPath(id="p1", name="Linear", sequence=["a", "b", "c"]),
        ],
    )
    graph = nx.DiGraph()
    for n in model.nodes:
        graph.add_node(n.id, difficulty=n.difficulty)
    for e in model.edges:
        graph.add_edge(e.from_, e.to)
    return model, graph


# ---------------------------------------------------------------------------
# plan() — high-level
# ---------------------------------------------------------------------------


def test_plan_cold_start_marks_all_available():
    model, graph = _toy_graph()
    profile = LearnerProfile()  # empty
    plan = KGPathPlanner().plan(model, graph, profile)
    # First path node ('a') has no prereqs → AVAILABLE; b has prereq 'a' not done → LOCKED
    statuses = {n.node_id: n.status for n in plan.nodes}
    assert statuses["a"] == PathStatus.AVAILABLE
    assert statuses["b"] == PathStatus.LOCKED
    assert statuses["c"] == PathStatus.LOCKED


def test_plan_after_completing_a():
    model, graph = _toy_graph()
    profile = LearnerProfile()
    profile.knowledge_map.set("a", 0.95)
    plan = KGPathPlanner().plan(model, graph, profile)
    statuses = {n.node_id: n.status for n in plan.nodes}
    assert statuses["a"] == PathStatus.COMPLETED
    assert statuses["b"] == PathStatus.AVAILABLE
    assert statuses["c"] == PathStatus.LOCKED  # still waiting on 'b'


def test_plan_after_completing_a_and_b():
    model, graph = _toy_graph()
    profile = LearnerProfile()
    profile.knowledge_map.set("a", 0.95)
    profile.knowledge_map.set("b", 0.85)  # above completion threshold
    plan = KGPathPlanner().plan(model, graph, profile)
    statuses = {n.node_id: n.status for n in plan.nodes}
    assert statuses["a"] == PathStatus.COMPLETED
    assert statuses["b"] == PathStatus.SKIPPED  # >= completion threshold
    assert statuses["c"] == PathStatus.AVAILABLE


def test_plan_in_progress_state():
    model, graph = _toy_graph()
    profile = LearnerProfile()
    profile.knowledge_map.set("a", 0.95)
    profile.knowledge_map.set("b", 0.5)  # in-progress
    plan = KGPathPlanner().plan(model, graph, profile)
    statuses = {n.node_id: n.status for n in plan.nodes}
    assert statuses["b"] == PathStatus.IN_PROGRESS
    assert statuses["c"] == PathStatus.LOCKED  # b not yet completed


def test_plan_returns_total_hours():
    model, graph = _toy_graph()
    profile = LearnerProfile()
    plan = KGPathPlanner().plan(model, graph, profile)
    assert plan.total_estimated_hours == pytest.approx(9.0)


def test_plan_progress_pct():
    model, graph = _toy_graph()
    profile = LearnerProfile()
    profile.knowledge_map.set("a", 0.95)
    profile.knowledge_map.set("b", 0.85)
    plan = KGPathPlanner().plan(model, graph, profile)
    # 2 of 3 completed
    assert plan.progress_pct() == pytest.approx(66.666, rel=1e-2)


# ---------------------------------------------------------------------------
# locate()
# ---------------------------------------------------------------------------


def test_locate_classifies_nodes():
    model, graph = _toy_graph()
    profile = LearnerProfile()
    profile.knowledge_map.set("a", 0.95)  # mastered
    profile.knowledge_map.set("b", 0.5)  # partial
    # c: not set → unmastered
    loc = KGPathPlanner().locate(graph, profile)
    assert "a" in loc["mastered"]
    assert "b" in loc["partial"]
    assert "c" in loc["unmastered"]
    assert loc["next_targets"] == ["c"]  # prereqs (a,b) satisfied


def test_locate_next_targets_locked_when_prereq_missing():
    model, graph = _toy_graph()
    profile = LearnerProfile()
    # No knowledge — b needs 'a' first
    loc = KGPathPlanner().locate(graph, profile)
    assert "b" in loc["unmastered"]
    # 'b' is not in next_targets because prereq 'a' not satisfied
    assert "b" not in loc["next_targets"]


# ---------------------------------------------------------------------------
# recommend_next()
# ---------------------------------------------------------------------------


def test_recommend_next_returns_first_n():
    model, graph = _toy_graph()
    profile = LearnerProfile()
    recs = KGPathPlanner().recommend_next(model, graph, profile, limit=2)
    # Only 'a' is truly next-available; 'b' and 'c' locked
    assert len(recs) == 1
    assert recs[0].node_id == "a"


def test_recommend_next_populates_metadata():
    model, graph = _toy_graph()
    profile = LearnerProfile()
    recs = KGPathPlanner().recommend_next(model, graph, profile)
    a = recs[0]
    assert a.name == "A"
    assert a.difficulty == 1
    assert a.estimated_hours == 2.0


# ---------------------------------------------------------------------------
# Path selection heuristic
# ---------------------------------------------------------------------------


def test_path_selection_picks_path_aligned_with_mastery():
    """If two paths exist, the planner picks the one whose first node is
    least mastered."""
    model = KnowledgeGraph(
        course="dual",
        nodes=[
            KGNode(id="x", name="X"),
            KGNode(id="y", name="Y"),
        ],
        edges=[],
        learning_paths=[
            LearningPath(id="p_x", name="X-first", sequence=["x"]),
            LearningPath(id="p_y", name="Y-first", sequence=["y"]),
        ],
    )
    graph = nx.DiGraph()
    graph.add_node("x")
    graph.add_node("y")

    profile = LearnerProfile()
    profile.knowledge_map.set("x", 0.95)  # already know x
    plan = KGPathPlanner().plan(model, graph, profile)
    # p_y should be preferred since y is unmastered
    assert plan.path_id == "p_y"


def test_explicit_path_id_honoured():
    model, graph = _toy_graph()
    profile = LearnerProfile()
    plan = KGPathPlanner().plan(model, graph, profile, path_id="p1")
    assert plan.path_id == "p1"
    assert [n.node_id for n in plan.nodes] == ["a", "b", "c"]


def test_unknown_path_id_falls_back():
    model, graph = _toy_graph()
    profile = LearnerProfile()
    plan = KGPathPlanner().plan(model, graph, profile, path_id="does_not_exist")
    # Falls back to auto topological traversal
    assert plan.path_id == "auto"
    # Sequence still respects prerequisites
    seq = [n.node_id for n in plan.nodes]
    assert seq.index("a") < seq.index("b")
    assert seq.index("b") < seq.index("c")
