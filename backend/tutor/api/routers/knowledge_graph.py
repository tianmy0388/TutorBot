"""Knowledge-graph HTTP endpoints.

Exposes:
- ``GET /api/v1/kg/courses``          — list available courses
- ``GET /api/v1/kg/{course}``         — full graph (model + adjacency)
- ``GET /api/v1/kg/{course}/paths``   — named learning paths
- ``POST /api/v1/kg/{course}/plan``   — plan a path for a learner profile
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tutor.services.knowledge_graph.schema import PlannedPath
from tutor.services.knowledge_graph.service import get_knowledge_graph_service
from tutor.services.learner_profile.schema import LearnerProfile

router = APIRouter()


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class PlanRequest(BaseModel):
    """Body for ``POST /api/v1/kg/{course}/plan``."""

    profile: LearnerProfile
    path_id: str = ""
    course: str = ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/kg/courses")
async def list_courses() -> dict[str, Any]:
    svc = get_knowledge_graph_service()
    return {"courses": svc.list_courses()}


@router.get("/kg/{course}")
async def get_graph(course: str) -> dict[str, Any]:
    svc = get_knowledge_graph_service()
    if not svc.has_course(course):
        raise HTTPException(404, f"Unknown course: {course}")
    model, graph = svc.get_graph(course)
    nodes = [
        {
            "id": n.id,
            "name": n.name,
            "category": n.category,
            "difficulty": n.difficulty,
            "estimated_hours": n.estimated_hours,
            "prerequisites": n.prerequisites,
            "source_file": n.source_file,
        }
        for n in model.nodes
    ]
    edges = [
        {"from": e.from_, "to": e.to, "type": e.type.value, "weight": e.weight}
        for e in model.edges
    ]
    return {
        "course": course,
        "version": model.version,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "is_dag": __import__("networkx").is_directed_acyclic_graph(graph),
        },
    }


@router.get("/kg/{course}/paths")
async def list_paths(course: str) -> dict[str, Any]:
    svc = get_knowledge_graph_service()
    if not svc.has_course(course):
        raise HTTPException(404, f"Unknown course: {course}")
    paths = svc.list_paths(course)
    return {
        "course": course,
        "paths": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "sequence": p.sequence,
            }
            for p in paths
        ],
    }


@router.post("/kg/{course}/plan")
async def plan_path(course: str, req: PlanRequest) -> dict[str, Any]:
    """Generate a learning plan for the supplied profile."""
    svc = get_knowledge_graph_service()
    if not svc.has_course(course):
        raise HTTPException(404, f"Unknown course: {course}")
    target_course = req.course or course
    plan: PlannedPath = svc.plan_for_learner(
        target_course, req.profile, path_id=req.path_id
    )
    return plan.model_dump(mode="json")


@router.get("/kg/{course}/recommend-next")
async def recommend_next(
    course: str,
    user_id: str = "anonymous",
    limit: int = 3,
) -> dict[str, Any]:
    """Recommend the next concepts for a stored learner profile."""
    from tutor.services.learner_profile.builder import get_profile_builder

    svc = get_knowledge_graph_service()
    if not svc.has_course(course):
        raise HTTPException(404, f"Unknown course: {course}")
    builder = get_profile_builder()
    profile = await builder.get(user_id)
    recs = svc.recommend_next(course, profile, limit=limit)
    return {
        "course": course,
        "user_id": user_id,
        "recommendations": [n.model_dump(mode="json") for n in recs],
    }


__all__ = ["router"]
