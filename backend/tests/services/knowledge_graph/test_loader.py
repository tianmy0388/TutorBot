"""Tests for :mod:`tutor.services.knowledge_graph.loader`.

Uses the AI Introduction course that ships with the project, and creates
ad-hoc course dirs in tmp_path for edge cases.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from tutor.services.knowledge_graph.loader import KnowledgeGraphLoader
from tutor.services.knowledge_graph.schema import (
    EdgeType,
    KGEdge,
    KGNode,
    KnowledgeGraph,
)


def test_list_courses_finds_ai_intro():
    loader = KnowledgeGraphLoader()
    courses = loader.list_courses()
    assert "ai_introduction" in courses


def test_load_ai_introduction():
    loader = KnowledgeGraphLoader()
    model, graph = loader.load("ai_introduction")
    assert model.course == "ai_introduction"
    assert len(model.nodes) >= 7  # 7 chapters
    assert isinstance(graph, nx.DiGraph)
    assert graph.number_of_nodes() == len(model.nodes)
    assert graph.number_of_edges() == len(model.edges)

    # Sanity-check edges
    assert graph.has_edge("ml_basics", "neural_network")
    assert graph.has_edge("neural_network", "transformer")
    assert graph.has_edge("transformer", "llm")


def test_load_is_cached():
    loader = KnowledgeGraphLoader()
    m1, g1 = loader.load("ai_introduction")
    m2, g2 = loader.load("ai_introduction")
    assert m1 is m2
    assert g1 is g2


def test_invalidate_cache():
    loader = KnowledgeGraphLoader()
    m1, _ = loader.load("ai_introduction")
    loader.invalidate("ai_introduction")
    m2, _ = loader.load("ai_introduction")
    assert m1 is not m2


def test_load_unknown_course_raises():
    loader = KnowledgeGraphLoader()
    with pytest.raises(FileNotFoundError):
        loader.load("does_not_exist")


def test_node_attributes_populated():
    loader = KnowledgeGraphLoader()
    _, graph = loader.load("ai_introduction")
    rnn_node = graph.nodes["rnn"]
    assert rnn_node["difficulty"] >= 3
    assert rnn_node["estimated_hours"] > 0
    assert rnn_node["name"]  # non-empty


def test_synthesized_edges_from_prerequisites():
    """Nodes with prerequisites that lack explicit edges get auto-edges."""
    import yaml

    loader = KnowledgeGraphLoader()
    # Find the AI course dir
    ai_dir = loader.course_dir("ai_introduction")
    raw = yaml.safe_load((ai_dir / "knowledge_graph.yaml").read_text(encoding="utf-8"))
    # All explicit edges as (src, dst)
    explicit = {(e["from"], e["to"]) for e in raw.get("edges", [])}
    # All prerequisites declared on nodes
    declared = set()
    for n in raw.get("nodes", []):
        for p in n.get("prerequisites", []):
            declared.add((p, n["id"]))
    # Every declared prereq should also be present in the model (synthesised or not)
    model, _ = loader.load("ai_introduction")
    model_edges = {(e.from_, e.to) for e in model.edges}
    missing = declared - model_edges
    assert missing == set(), f"synthesised edges should cover declared prereqs: {missing}"


def test_integrity_warnings_logged(caplog):
    """An unknown node reference triggers a warning."""
    loader = KnowledgeGraphLoader()
    # Construct a temp course with bad ref
    import tempfile, textwrap

    with tempfile.TemporaryDirectory() as td:
        course_dir = Path(td) / "bad_course"
        course_dir.mkdir()
        (course_dir / "knowledge_graph.yaml").write_text(
            textwrap.dedent(
                """
                course: bad_course
                nodes:
                  - id: a
                    name: A
                    prerequisites: [ghost]
                """
            ),
            encoding="utf-8",
        )
        loader_local = KnowledgeGraphLoader(td)
        # Should NOT raise; warnings are emitted
        model, _ = loader_local.load("bad_course")
        warnings = model.validate_integrity()
        assert any("ghost" in w for w in warnings)
