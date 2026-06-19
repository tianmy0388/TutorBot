"""Knowledge graph service.

A knowledge graph encodes the *prerequisite structure* of a course:

- Nodes  — concepts (e.g. ``lstm``, ``transformer``)
- Edges  — prerequisite relationships (``rnn → lstm``)
- Paths  — recommended learning sequences (e.g. CV track, NLP track)

The MVP uses **YAML static definition + NetworkX in-memory traversal**.
This keeps the surface area tiny. For production, the same API can be
backed by Neo4j without touching callers.

Modules
-------
- :mod:`tutor.services.knowledge_graph.schema`    — Pydantic models
- :mod:`tutor.services.knowledge_graph.loader`    — YAML → NetworkX
- :mod:`tutor.services.knowledge_graph.planner`   — locate / prune / topo_sort / match / recommend
- :mod:`tutor.services.knowledge_graph.service`   — high-level facade
"""

from tutor.services.knowledge_graph.schema import (
    KGNode,
    KGEdge,
    EdgeType,
    KnowledgeGraph,
    LearningPath,
    PathNode,
    PathStatus,
    PlannedPath,
)
from tutor.services.knowledge_graph.loader import (
    KnowledgeGraphLoader,
    list_available_courses,
)
from tutor.services.knowledge_graph.planner import KGPathPlanner
from tutor.services.knowledge_graph.service import (
    KnowledgeGraphService,
    get_knowledge_graph_service,
    reset_knowledge_graph_service,
)

__all__ = [
    "EdgeType",
    "KGNode",
    "KGEdge",
    "KGPathPlanner",
    "KnowledgeGraph",
    "KnowledgeGraphLoader",
    "KnowledgeGraphService",
    "LearningPath",
    "PathNode",
    "PathStatus",
    "PlannedPath",
    "get_knowledge_graph_service",
    "list_available_courses",
    "reset_knowledge_graph_service",
]
