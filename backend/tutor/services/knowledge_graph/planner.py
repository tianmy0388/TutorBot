"""KGPathPlanner — turn a learner's profile + a knowledge graph into a path.

Implements the 5-stage flow from idea.md:

1. **locate** — find where the student currently is in the graph
2. **prune** — mark mastered concepts as ``SKIPPED``
3. **topo_sort** — order the rest by prerequisite dependency
4. **match** — annotate each step with matched resources (deferred to caller)
5. **push** — produce the final :class:`PlannedPath` with status annotations

The planner is **pure**: same inputs → same outputs. No I/O, no LLM.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

import networkx as nx

from tutor.services.knowledge_graph.schema import (
    KGNode,
    KnowledgeGraph,
    LearningPath,
    PathNode,
    PathStatus,
    PlannedPath,
)
from tutor.services.learner_profile.schema import (
    CognitiveStyle,
    LearnerProfile,
)


# ---------------------------------------------------------------------------
# Tunables (can be overridden via constructor)
# ---------------------------------------------------------------------------


DEFAULT_COMPLETION_THRESHOLD = 0.8  # mastery >= this → COMPLETED
DEFAULT_AVAILABILITY_THRESHOLD = 0.4  # below this counts as weak


class KGPathPlanner:
    """Generate a learning path adapted to a learner profile."""

    def __init__(
        self,
        *,
        completion_threshold: float = DEFAULT_COMPLETION_THRESHOLD,
        weak_threshold: float = DEFAULT_AVAILABILITY_THRESHOLD,
    ) -> None:
        self.completion_threshold = max(0.0, min(1.0, completion_threshold))
        self.weak_threshold = max(0.0, min(1.0, weak_threshold))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(
        self,
        graph_model: KnowledgeGraph,
        graph: nx.DiGraph,
        profile: LearnerProfile,
        *,
        path_id: str = "",
    ) -> PlannedPath:
        """Plan a learning path for the given learner.

        Strategy:
        - Use the named ``learning_path`` from the graph if ``path_id`` matches.
        - Otherwise pick the path whose first unlocked node matches the
          learner's stated goal (best-effort heuristic).
        - Fall back to a topological traversal of the entire graph from
          leaves / unmastered nodes.
        """
        target_path = self._select_path(graph_model, profile, path_id)
        if target_path is not None:
            sequence = list(target_path.sequence)
            name = target_path.name
            description = target_path.description
            pid = target_path.id
        else:
            sequence = self._sequence_from_graph(graph, profile)
            name = f"{graph_model.course} — 自动路径"
            description = "根据画像自动生成的拓扑顺序"
            pid = "auto"

        planned = self._annotate_steps(graph_model, graph, sequence, profile)

        return PlannedPath(
            path_id=pid,
            course=graph_model.course,
            name=name,
            description=description,
            nodes=planned,
            total_estimated_hours=sum(n.estimated_hours for n in planned),
            completed_count=sum(
                1 for n in planned if n.status == PathStatus.COMPLETED
            ),
            available_count=sum(
                1 for n in planned if n.status == PathStatus.AVAILABLE
            ),
            locked_count=sum(1 for n in planned if n.status == PathStatus.LOCKED),
        )

    def locate(
        self,
        graph: nx.DiGraph,
        profile: LearnerProfile,
    ) -> dict[str, Any]:
        """Identify the student's current position in the graph.

        Returns a dict with:
        - ``mastered`` : nodes at/above completion threshold
        - ``partial`` : nodes between weak and completion thresholds
        - ``unmastered`` : nodes below weak threshold OR not in profile
        - ``next_targets`` : nodes whose prerequisites are satisfied but that
          are not yet mastered (sorted by topological order)
        """
        mastered: list[str] = []
        partial: list[str] = []
        unmastered: list[str] = []

        for nid in graph.nodes:
            score = profile.knowledge_map.get(nid)
            if score >= self.completion_threshold:
                mastered.append(nid)
            elif score >= self.weak_threshold:
                partial.append(nid)
            else:
                unmastered.append(nid)

        next_targets: list[str] = []
        for nid in unmastered:
            if self._prerequisites_satisfied(graph, nid, profile):
                next_targets.append(nid)

        # Topological order on next_targets so the student sees a sane order
        if next_targets:
            subgraph = graph.subgraph(set(next_targets))
            try:
                next_targets = list(nx.topological_sort(subgraph))
            except nx.NetworkXUnfeasible:
                # Cycle — fall back to insertion order
                pass

        return {
            "mastered": sorted(mastered),
            "partial": sorted(partial),
            "unmastered": sorted(unmastered),
            "next_targets": next_targets,
        }

    def recommend_next(
        self,
        graph_model: KnowledgeGraph,
        graph: nx.DiGraph,
        profile: LearnerProfile,
        *,
        limit: int = 3,
    ) -> list[PathNode]:
        """Pick the next ``limit`` concepts the learner should study."""
        location = self.locate(graph, profile)
        candidates = location["next_targets"][:limit]
        out: list[PathNode] = []
        for nid in candidates:
            n = graph_model.get_node(nid)
            if n is None:
                continue
            out.append(
                PathNode(
                    node_id=n.id,
                    status=PathStatus.AVAILABLE,
                    estimated_hours=n.estimated_hours,
                    difficulty=n.difficulty,
                    name=n.name,
                    category=n.category,
                )
            )
        return out

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _select_path(
        self,
        model: KnowledgeGraph,
        profile: LearnerProfile,
        path_id: str,
    ) -> LearningPath | None:
        candidates = list(model.learning_paths)
        if not candidates:
            return None
        if path_id:
            for p in candidates:
                if p.id == path_id:
                    return p
            return None
        # Heuristic: pick the path whose first node is unmastered and
        # whose sequence is most aligned with the learner's current mastery.
        scored: list[tuple[float, LearningPath]] = []
        for p in candidates:
            score = self._score_path(model, p, profile)
            scored.append((score, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1] if scored else None

    def _score_path(
        self,
        model: KnowledgeGraph,
        path: LearningPath,
        profile: LearnerProfile,
    ) -> float:
        # Prefer paths where the first node is unmastered (real progress)
        # and many later nodes are unmastered too.
        seq = path.sequence
        if not seq:
            return 0.0
        first = seq[0]
        first_score = profile.knowledge_map.get(first)
        unmastered_ratio = sum(
            1
            for n in seq
            if profile.knowledge_map.get(n) < self.completion_threshold
        ) / len(seq)
        # Reward low first_score, high unmastered_ratio
        return (1.0 - first_score) * 0.6 + unmastered_ratio * 0.4

    def _sequence_from_graph(
        self,
        graph: nx.DiGraph,
        profile: LearnerProfile,
    ) -> list[str]:
        """Return a topological sequence covering all unmastered nodes plus
        the unmastered prerequisites they depend on.
        """
        unmastered = {
            n
            for n in graph.nodes
            if profile.knowledge_map.get(n) < self.completion_threshold
        }
        # Expand to include all transitive prerequisites
        full = set(unmastered)
        for n in list(unmastered):
            full.update(nx.ancestors(graph, n))
        sub = graph.subgraph(full)
        try:
            return list(nx.topological_sort(sub))
        except nx.NetworkXUnfeasible:
            # Cycle in graph — return any consistent order
            return sorted(full)

    def _annotate_steps(
        self,
        model: KnowledgeGraph,
        graph: nx.DiGraph,
        sequence: Iterable[str],
        profile: LearnerProfile,
    ) -> list[PathNode]:
        """Walk the sequence, annotate each step with status + metadata.

        Status rules (per node):
        - ``SKIPPED``    if mastery >= completion_threshold
        - ``COMPLETED``  if mastery >= 0.95 (effectively done)
        - ``IN_PROGRESS``if 0.4 <= mastery < completion_threshold
        - ``AVAILABLE``  if all prereqs are satisfied (skipped or completed)
        - ``LOCKED``     otherwise
        """
        completed_ids: set[str] = set()
        out: list[PathNode] = []
        for nid in sequence:
            n = model.get_node(nid)
            if n is None:
                # Unknown node — skip but record nothing
                continue
            score = profile.knowledge_map.get(nid)
            if score >= 0.95:
                status = PathStatus.COMPLETED
                completed_ids.add(nid)
            elif score >= self.completion_threshold:
                status = PathStatus.SKIPPED
                completed_ids.add(nid)
            elif score >= self.weak_threshold:
                status = PathStatus.IN_PROGRESS
            else:
                prereqs = list(graph.predecessors(nid))
                if all(p in completed_ids for p in prereqs):
                    status = PathStatus.AVAILABLE
                else:
                    status = PathStatus.LOCKED

            out.append(
                PathNode(
                    node_id=nid,
                    status=status,
                    estimated_hours=n.estimated_hours,
                    difficulty=n.difficulty,
                    name=n.name,
                    category=n.category,
                )
            )

        # Second pass: if a node is COMPLETED/SKIPPED but a later node is
        # still LOCKED because it depended on a peer (not this node), the
        # peer should be IN_PROGRESS or COMPLETED for the dep to unlock.
        # We don't auto-fix those — they're diagnostic info.
        return out

    def _prerequisites_satisfied(
        self,
        graph: nx.DiGraph,
        node_id: str,
        profile: LearnerProfile,
    ) -> bool:
        for pred in graph.predecessors(node_id):
            mastery = profile.knowledge_map.get(pred)
            if mastery < self.weak_threshold:
                # Missing prerequisite unless also low — but treat as not satisfied
                return False
        return True


__all__ = ["KGPathPlanner"]
