"""Course HTTP endpoints (2026-06-21 plan, Part D).

Surface:

  GET    /api/v1/courses                      — list all courses
  POST   /api/v1/courses                      — create a new course
  GET    /api/v1/courses/{course_id}          — course detail
  PATCH  /api/v1/courses/{course_id}          — rename / re-describe / re-attach KG
  DELETE /api/v1/courses/{course_id}          — delete (detach libraries first)
  POST   /api/v1/courses/{course_id}/libraries/{lib_id}   — attach KB
  DELETE /api/v1/courses/{course_id}/libraries/{lib_id}   — detach KB
  GET    /api/v1/courses/{course_id}/libraries            — list bound libraries
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from tutor.services.courses import (
    Course,
    CourseService,
    get_course_service,
)
from tutor.services.knowledge_base import KnowledgeBaseRecord

router = APIRouter()


class CreateCourseRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    knowledge_graph_id: str = Field(default="", max_length=200)


class UpdateCourseRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    knowledge_graph_id: str | None = Field(default=None, max_length=200)


def _ensure_seeded() -> None:
    """Create the prebuilt AI 导论 course on first startup."""
    from tutor.services.courses import seed_default_courses

    seed_default_courses(get_course_service())


@router.get("/courses")
async def list_courses() -> dict[str, Any]:
    _ensure_seeded()
    svc = get_course_service()
    items = [c.model_dump(mode="json") for c in svc.list_courses()]
    return {"items": items, "total": len(items)}


@router.post("/courses", status_code=201)
async def create_course(req: CreateCourseRequest) -> dict[str, Any]:
    _ensure_seeded()
    svc = get_course_service()
    course = svc.create_course(
        name=req.name,
        description=req.description,
        knowledge_graph_id=req.knowledge_graph_id,
    )
    return course.model_dump(mode="json")


@router.get("/courses/{course_id}")
async def get_course(course_id: str) -> dict[str, Any]:
    _ensure_seeded()
    svc = get_course_service()
    course = svc.get_course(course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    return course.model_dump(mode="json")


@router.patch("/courses/{course_id}")
async def update_course(course_id: str, req: UpdateCourseRequest) -> dict[str, Any]:
    _ensure_seeded()
    svc = get_course_service()
    course = svc.update_course(
        course_id,
        name=req.name,
        description=req.description,
        knowledge_graph_id=req.knowledge_graph_id,
    )
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    return course.model_dump(mode="json")


@router.delete("/courses/{course_id}")
async def delete_course(course_id: str) -> dict[str, Any]:
    _ensure_seeded()
    svc = get_course_service()
    ok = svc.delete_course(course_id)
    if not ok:
        raise HTTPException(status_code=404, detail="course not found")
    return {"deleted": True, "id": course_id}


# ---- library membership -----------------------------------------------


@router.get("/courses/{course_id}/libraries")
async def list_course_libraries(course_id: str) -> dict[str, Any]:
    """List the libraries bound to a course.

    These are the libraries that participate in the course's RAG
    scope. The "move library between courses" spec is implemented
    by detaching from one and attaching to the other; the library
    row's ``course_id`` is the single source of truth.
    """
    _ensure_seeded()
    svc = get_course_service()
    course = svc.get_course(course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    libs: list[KnowledgeBaseRecord] = []
    for lib in svc.kb_store.list_libraries():
        if lib.course_id == course_id:
            libs.append(lib)
    return {
        "course_id": course_id,
        "items": [lib.model_dump(mode="json") for lib in libs],
        "total": len(libs),
    }


@router.post("/courses/{course_id}/libraries/{lib_id}", status_code=200)
async def attach_library(course_id: str, lib_id: str) -> dict[str, Any]:
    _ensure_seeded()
    svc = get_course_service()
    try:
        course = svc.attach_library(course_id, lib_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    return {
        "course": course.model_dump(mode="json"),
        "library_id": lib_id,
        "attached": True,
    }


@router.delete("/courses/{course_id}/libraries/{lib_id}")
async def detach_library(course_id: str, lib_id: str) -> dict[str, Any]:
    _ensure_seeded()
    svc = get_course_service()
    course = svc.detach_library(course_id, lib_id)
    if course is None:
        raise HTTPException(status_code=404, detail="course not found")
    return {
        "course": course.model_dump(mode="json"),
        "library_id": lib_id,
        "detached": True,
    }


__all__ = ["router"]
