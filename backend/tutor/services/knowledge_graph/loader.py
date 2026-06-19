"""Knowledge-graph YAML loader.

Reads ``<course_dir>/knowledge_graph.yaml`` and constructs:

- a :class:`KnowledgeGraph` Pydantic model (transport / storage)
- a NetworkX ``DiGraph`` for fast in-memory traversal

Discovery
---------
By default the loader scans the configured ``kb_dir`` for sub-directories
that contain a ``knowledge_graph.yaml``. Each sub-directory becomes a
course. The graph is cached by course name after first load.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import networkx as nx
import yaml
from loguru import logger

from tutor.services.config.settings import get_settings
from tutor.services.knowledge_graph.schema import (
    EdgeType,
    KGEdge,
    KGNode,
    KnowledgeGraph,
    LearningPath,
)


class KnowledgeGraphLoader:
    """Loads course knowledge graphs from YAML on disk."""

    def __init__(self, kb_dir: str | Path | None = None) -> None:
        if kb_dir is None:
            kb_dir = get_settings().kb_dir
        self.kb_dir = Path(kb_dir)
        self._cache: dict[str, tuple[KnowledgeGraph, nx.DiGraph]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_courses(self) -> list[str]:
        """Return the names of all courses that have a knowledge_graph.yaml."""
        if not self.kb_dir.exists():
            return []
        out: list[str] = []
        for child in sorted(self.kb_dir.iterdir()):
            if not child.is_dir():
                continue
            if (child / "knowledge_graph.yaml").exists():
                out.append(child.name)
        return out

    def course_dir(self, course: str) -> Path:
        return self.kb_dir / course

    def yaml_path(self, course: str) -> Path:
        return self.course_dir(course) / "knowledge_graph.yaml"

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, course: str, *, use_cache: bool = True) -> tuple[KnowledgeGraph, nx.DiGraph]:
        """Load (and cache) a knowledge graph.

        Returns a tuple of (Pydantic model, NetworkX DiGraph).
        """
        with self._lock:
            cached = self._cache.get(course) if use_cache else None
            if cached is not None:
                return cached
            model = self._parse_yaml(course)
            graph = self._build_graph(model)
            warnings = model.validate_integrity()
            if warnings:
                for w in warnings:
                    logger.warning(f"[KG:{course}] {w}")
            self._cache[course] = (model, graph)
            logger.info(
                f"[KG:{course}] loaded {len(model.nodes)} nodes, "
                f"{len(model.edges)} edges, {len(model.learning_paths)} paths"
            )
            return model, graph

    def invalidate(self, course: str | None = None) -> None:
        with self._lock:
            if course is None:
                self._cache.clear()
            else:
                self._cache.pop(course, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _parse_yaml(self, course: str) -> KnowledgeGraph:
        path = self.yaml_path(course)
        if not path.exists():
            raise FileNotFoundError(
                f"No knowledge_graph.yaml for course {course!r} at {path}"
            )
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return _yaml_to_model(course, raw)

    def _build_graph(self, model: KnowledgeGraph) -> nx.DiGraph:
        g = nx.DiGraph()
        # Add nodes with attributes (for traversal convenience)
        for n in model.nodes:
            g.add_node(
                n.id,
                name=n.name,
                category=n.category,
                difficulty=n.difficulty,
                estimated_hours=n.estimated_hours,
                source_file=n.source_file,
            )
        # Add edges (prerequisites flow as src → dst)
        for e in model.edges:
            g.add_edge(
                e.from_,
                e.to,
                type=e.type.value,
                weight=e.weight,
            )
        return g


# ---------------------------------------------------------------------------
# YAML → model
# ---------------------------------------------------------------------------


def _yaml_to_model(course: str, raw: dict[str, Any]) -> KnowledgeGraph:
    """Translate raw YAML to a :class:`KnowledgeGraph`."""
    nodes_raw = raw.get("nodes") or []
    edges_raw = raw.get("edges") or []
    paths_raw = raw.get("learning_paths") or []

    nodes = [
        KGNode(
            id=n["id"],
            name=n.get("name", n["id"]),
            category=n.get("category", "general"),
            difficulty=int(n.get("difficulty", 1)),
            prerequisites=list(n.get("prerequisites", [])),
            estimated_hours=float(n.get("estimated_hours", 0.0)),
            learning_outcomes=list(n.get("learning_outcomes", [])),
            source_file=str(n.get("source_file", "")),
        )
        for n in nodes_raw
    ]

    edges: list[KGEdge] = []
    for e in edges_raw:
        # YAML uses "from" which collides with Python keyword — accept both
        src = e.get("from") or e.get("from_")
        if src is None:
            logger.warning(f"Skipping edge without 'from': {e}")
            continue
        try:
            edge_type = EdgeType(e.get("type", "prerequisite"))
        except ValueError:
            edge_type = EdgeType.PREREQUISITE
        edges.append(
            KGEdge(
                **{"from": src},
                to=e["to"],
                type=edge_type,
                weight=float(e.get("weight", 1.0)),
            )
        )

    paths = [
        LearningPath(
            id=p["id"],
            name=p.get("name", p["id"]),
            description=p.get("description", ""),
            sequence=list(p.get("sequence", [])),
        )
        for p in paths_raw
    ]

    # Any node-level prerequisites that don't have a corresponding edge →
    # synthesise one so the graph is internally consistent.
    existing_edges: set[tuple[str, str]] = {(e.from_, e.to) for e in edges}
    for n in nodes:
        for p in n.prerequisites:
            if (p, n.id) not in existing_edges:
                edges.append(
                    KGEdge(**{"from": p}, to=n.id, type=EdgeType.PREREQUISITE)
                )
                existing_edges.add((p, n.id))

    return KnowledgeGraph(
        course=course,
        version=str(raw.get("version", "1.0.0")),
        description=str(raw.get("description", "")),
        nodes=nodes,
        edges=edges,
        learning_paths=paths,
        metadata={
            k: v
            for k, v in raw.items()
            if k not in ("course", "version", "nodes", "edges", "learning_paths")
        },
    )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def list_available_courses() -> list[str]:
    """List all courses that have a knowledge graph."""
    return KnowledgeGraphLoader().list_courses()


__all__ = ["KnowledgeGraphLoader", "list_available_courses"]
