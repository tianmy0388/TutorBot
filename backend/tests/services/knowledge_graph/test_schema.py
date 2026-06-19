"""Tests for :mod:`tutor.services.knowledge_graph.schema`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tutor.services.knowledge_graph.schema import (
    EdgeType,
    KGEdge,
    KGNode,
    KnowledgeGraph,
    LearningPath,
    PathNode,
    PathStatus,
    PlannedPath,
)


# ---------------------------------------------------------------------------
# KGNode
# ---------------------------------------------------------------------------


def test_node_basic():
    n = KGNode(id="lstm", name="LSTM")
    assert n.difficulty == 1
    assert n.category == "general"
    assert n.estimated_hours == 0.0
    assert n.prerequisites == []


def test_node_id_required():
    with pytest.raises(ValidationError):
        KGNode(id="", name="x")


def test_node_difficulty_in_range():
    KGNode(id="x", name="x", difficulty=1)
    KGNode(id="x", name="x", difficulty=5)
    with pytest.raises(ValidationError):
        KGNode(id="x", name="x", difficulty=0)
    with pytest.raises(ValidationError):
        KGNode(id="x", name="x", difficulty=6)


def test_node_hours_non_negative():
    with pytest.raises(ValidationError):
        KGNode(id="x", name="x", estimated_hours=-1.0)


# ---------------------------------------------------------------------------
# KGEdge
# ---------------------------------------------------------------------------


def test_edge_basic():
    e = KGEdge(**{"from": "a"}, to="b")
    assert e.type == EdgeType.PREREQUISITE
    assert e.weight == 1.0


def test_edge_self_loop_rejected():
    with pytest.raises(ValidationError):
        KGEdge(**{"from": "a"}, to="a")


def test_edge_endpoints_required():
    with pytest.raises(ValidationError):
        KGEdge(**{"from": ""}, to="b")
    with pytest.raises(ValidationError):
        KGEdge(**{"from": "a"}, to="")


# ---------------------------------------------------------------------------
# KnowledgeGraph integrity
# ---------------------------------------------------------------------------


def test_graph_basic_helpers():
    g = KnowledgeGraph(
        course="c1",
        nodes=[
            KGNode(id="a", name="A"),
            KGNode(id="b", name="B", prerequisites=["a"]),
        ],
        edges=[KGEdge(**{"from": "a"}, to="b")],
    )
    assert g.node_ids() == {"a", "b"}
    assert g.get_node("a").name == "A"
    assert g.get_node("z") is None
    assert g.prerequisites_of("b") == ["a"]
    assert g.successors_of("a") == ["b"]
    assert g.validate_integrity() == []


def test_graph_validate_integrity_unknown_prereq():
    g = KnowledgeGraph(
        course="c1",
        nodes=[KGNode(id="b", name="B", prerequisites=["missing"])],
    )
    warnings = g.validate_integrity()
    assert any("missing" in w for w in warnings)


def test_graph_validate_integrity_unknown_edge_endpoint():
    g = KnowledgeGraph(
        course="c1",
        nodes=[KGNode(id="a", name="A")],
        edges=[KGEdge(**{"from": "ghost"}, to="a")],
    )
    warnings = g.validate_integrity()
    assert any("ghost" in w for w in warnings)


def test_graph_validate_integrity_bad_path_node():
    g = KnowledgeGraph(
        course="c1",
        nodes=[KGNode(id="a", name="A")],
        learning_paths=[LearningPath(id="p1", name="p1", sequence=["ghost"])],
    )
    warnings = g.validate_integrity()
    assert any("ghost" in w for w in warnings)


# ---------------------------------------------------------------------------
# PlannedPath
# ---------------------------------------------------------------------------


def test_planned_path_progress_pct():
    from datetime import datetime, timezone

    p = PlannedPath(
        path_id="p",
        course="c",
        nodes=[
            PathNode(node_id="a", status=PathStatus.COMPLETED),
            PathNode(node_id="b", status=PathStatus.SKIPPED),
            PathNode(node_id="c", status=PathStatus.AVAILABLE),
            PathNode(node_id="d", status=PathStatus.LOCKED),
        ],
    )
    assert p.progress_pct() == pytest.approx(50.0)


def test_planned_path_empty():
    p = PlannedPath(path_id="x", course="c")
    assert p.progress_pct() == 0.0
    assert p.next_available() == []
    assert p.first_available() is None
