"""Knowledge graph data models (Pydantic v2).

These are the *transport* models (what YAML files look like and what
the planner returns). The runtime graph is a NetworkX ``DiGraph`` built
by :mod:`tutor.services.knowledge_graph.loader`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class EdgeType(str, Enum):
    """Kind of relationship between two concepts."""

    PREREQUISITE = "prerequisite"  # hard: must learn A before B
    RELATED = "related"            # soft: helpful but not required
    EXTENDS = "extends"            # B extends A


class PathStatus(str, Enum):
    """Status of a node within a planned learning path."""

    LOCKED = "locked"          # prerequisites not yet met
    AVAILABLE = "available"    # ready to study
    IN_PROGRESS = "in_progress"  # student started
    COMPLETED = "completed"    # mastery >= completion threshold
    SKIPPED = "skipped"        # student already mastered → skip


# ---------------------------------------------------------------------------
# Graph elements
# ---------------------------------------------------------------------------


class KGNode(BaseModel):
    """A single concept node in the knowledge graph."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    category: str = "general"
    difficulty: int = 1  # 1-5
    prerequisites: list[str] = Field(default_factory=list)
    estimated_hours: float = 0.0
    learning_outcomes: list[str] = Field(default_factory=list)
    source_file: str = ""  # relative path inside the course dir

    @field_validator("id")
    @classmethod
    def _id_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("node id must be non-empty")
        return v.strip()

    @field_validator("difficulty")
    @classmethod
    def _difficulty_in_range(cls, v: int) -> int:
        if v < 1 or v > 5:
            raise ValueError(f"difficulty must be in [1, 5], got {v}")
        return v

    @field_validator("estimated_hours")
    @classmethod
    def _hours_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"estimated_hours must be >= 0, got {v}")
        return v


class KGEdge(BaseModel):
    """A directed edge between two concepts."""

    model_config = ConfigDict(extra="forbid")

    from_: str = Field(alias="from")
    to: str
    type: EdgeType = EdgeType.PREREQUISITE
    weight: float = 1.0  # for weighted topo sort / scheduling

    @field_validator("from_", "to")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("edge endpoints must be non-empty")
        return v.strip()

    def model_post_init(self, __context: Any) -> None:
        if self.from_ == self.to:
            raise ValueError(f"self-loop edge not allowed: {self.from_}")


# ---------------------------------------------------------------------------
# Top-level graph
# ---------------------------------------------------------------------------


class KnowledgeGraph(BaseModel):
    """A complete knowledge graph for one course."""

    model_config = ConfigDict(extra="forbid")

    course: str
    version: str = "1.0.0"
    description: str = ""
    nodes: list[KGNode] = Field(default_factory=list)
    edges: list[KGEdge] = Field(default_factory=list)
    learning_paths: list[LearningPath] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    def node_ids(self) -> set[str]:
        return {n.id for n in self.nodes}

    def get_node(self, node_id: str) -> KGNode | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def prerequisites_of(self, node_id: str) -> list[str]:
        """Return direct prerequisite ids (incoming edges)."""
        return [e.from_ for e in self.edges if e.to == node_id]

    def successors_of(self, node_id: str) -> list[str]:
        """Return direct successors (outgoing edges)."""
        return [e.to for e in self.edges if e.from_ == node_id]

    def validate_integrity(self) -> list[str]:
        """Return a list of integrity warnings (empty = OK)."""
        warnings: list[str] = []
        node_ids = self.node_ids()
        for n in self.nodes:
            for p in n.prerequisites:
                if p not in node_ids:
                    warnings.append(f"node {n.id!r} has unknown prerequisite {p!r}")
        for e in self.edges:
            if e.from_ not in node_ids:
                warnings.append(f"edge {e.from_!r}→{e.to!r} has unknown source")
            if e.to not in node_ids:
                warnings.append(f"edge {e.from_!r}→{e.to!r} has unknown target")
        for p in self.learning_paths:
            for n in p.sequence:
                if n not in node_ids:
                    warnings.append(f"path {p.id!r} references unknown node {n!r}")
        return warnings


# ---------------------------------------------------------------------------
# Recommended paths
# ---------------------------------------------------------------------------


class PathNode(BaseModel):
    """One step in a :class:`PlannedPath`."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    status: PathStatus = PathStatus.LOCKED
    estimated_hours: float = 0.0
    difficulty: int = 1
    name: str = ""
    category: str = ""
    matched_resources: list[str] = Field(default_factory=list)  # resource IDs


class LearningPath(BaseModel):
    """A named recommended sequence of nodes (declared in YAML)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    sequence: list[str] = Field(default_factory=list)


class PlannedPath(BaseModel):
    """The output of the planner: an ordered path adapted to a learner.

    Contains every node the learner should visit (in order), annotated
    with status (locked/available/in_progress/...) and the learner's
    estimated total time investment.
    """

    model_config = ConfigDict(extra="forbid")

    path_id: str = ""
    course: str = ""
    name: str = ""
    description: str = ""
    nodes: list[PathNode] = Field(default_factory=list)
    total_estimated_hours: float = 0.0
    completed_count: int = 0
    available_count: int = 0
    locked_count: int = 0
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def next_available(self) -> list[PathNode]:
        return [n for n in self.nodes if n.status == PathStatus.AVAILABLE]

    def first_available(self) -> PathNode | None:
        for n in self.nodes:
            if n.status == PathStatus.AVAILABLE:
                return n
        return None

    def progress_pct(self) -> float:
        if not self.nodes:
            return 0.0
        done = sum(
            1
            for n in self.nodes
            if n.status in (PathStatus.COMPLETED, PathStatus.SKIPPED)
        )
        return 100.0 * done / len(self.nodes)


__all__ = [
    "EdgeType",
    "KGEdge",
    "KGNode",
    "KnowledgeGraph",
    "LearningPath",
    "PathNode",
    "PathStatus",
    "PlannedPath",
]
