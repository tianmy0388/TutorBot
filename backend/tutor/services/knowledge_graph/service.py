"""KnowledgeGraphService — high-level facade.

Combines the :class:`KnowledgeGraphLoader` (YAML → NetworkX) with the
:class:`KGPathPlanner` (learner + graph → :class:`PlannedPath`).

This is the entry point the rest of Tutor should use:

    from tutor.services.knowledge_graph import get_knowledge_graph_service

    svc = get_knowledge_graph_service()
    planned = svc.plan_for_learner("ai_introduction", profile, path_id="nlp_path")
"""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import Any

import networkx as nx
from loguru import logger

from tutor.services.config.settings import get_settings
from tutor.services.knowledge_graph.loader import KnowledgeGraphLoader
from tutor.services.knowledge_graph.planner import KGPathPlanner
from tutor.services.knowledge_graph.schema import (
    KGNode,
    KnowledgeGraph,
    LearningPath,
    PathNode,
    PlannedPath,
)
from tutor.services.learner_profile.schema import LearnerProfile


class KnowledgeGraphService:
    """Aggregate loader + planner behind one stable API."""

    def __init__(
        self,
        loader: KnowledgeGraphLoader | None = None,
        planner: KGPathPlanner | None = None,
    ) -> None:
        self.loader = loader or KnowledgeGraphLoader()
        self.planner = planner or KGPathPlanner()

    # ------------------------------------------------------------------
    # Course discovery
    # ------------------------------------------------------------------

    def list_courses(self) -> list[str]:
        return self.loader.list_courses()

    def has_course(self, course: str) -> bool:
        return course in self.loader.list_courses()

    def default_course(self) -> str:
        """Return the configured default course (or first available)."""
        settings = get_settings()
        if self.has_course(settings.kb_default):
            return settings.kb_default
        courses = self.list_courses()
        return courses[0] if courses else ""

    # ------------------------------------------------------------------
    # Graph access
    # ------------------------------------------------------------------

    def get_graph(
        self, course: str
    ) -> tuple[KnowledgeGraph, nx.DiGraph]:
        return self.loader.load(course)

    def get_model(self, course: str) -> KnowledgeGraph:
        model, _ = self.loader.load(course)
        return model

    def get_node(self, course: str, node_id: str) -> KGNode | None:
        return self.get_model(course).get_node(node_id)

    def list_paths(self, course: str) -> list[LearningPath]:
        return self.get_model(course).learning_paths

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    def plan_for_learner(
        self,
        course: str,
        profile: LearnerProfile,
        *,
        path_id: str = "",
    ) -> PlannedPath:
        model, graph = self.loader.load(course)
        return self.planner.plan(model, graph, profile, path_id=path_id)

    def locate(
        self,
        course: str,
        profile: LearnerProfile,
    ) -> dict[str, Any]:
        _, graph = self.loader.load(course)
        return self.planner.locate(graph, profile)

    def recommend_next(
        self,
        course: str,
        profile: LearnerProfile,
        *,
        limit: int = 3,
    ) -> list[PathNode]:
        model, graph = self.loader.load(course)
        return self.planner.recommend_next(model, graph, profile, limit=limit)

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def invalidate(self, course: str | None = None) -> None:
        self.loader.invalidate(course)

    def stats(self) -> dict[str, Any]:
        """Return basic stats about loaded courses."""
        out: dict[str, Any] = {
            "kb_dir": str(self.loader.kb_dir),
            "available_courses": self.loader.list_courses(),
            "loaded_courses": list(self.loader._cache.keys()),
        }
        return out


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


_service: KnowledgeGraphService | None = None
_service_lock = threading.Lock()


def get_knowledge_graph_service() -> KnowledgeGraphService:
    """Return the singleton :class:`KnowledgeGraphService`."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = KnowledgeGraphService()
                logger.info(
                    f"KG service ready (kb_dir={_service.loader.kb_dir}, "
                    f"courses={_service.list_courses()})"
                )
    return _service


def reset_knowledge_graph_service() -> None:
    """Clear the singleton. Tests only."""
    global _service
    _service = None


__all__ = [
    "KnowledgeGraphService",
    "get_knowledge_graph_service",
    "reset_knowledge_graph_service",
]
