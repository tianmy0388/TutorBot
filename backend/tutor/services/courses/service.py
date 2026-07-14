"""Course service (2026-06-21 plan).

The service is the only place that knows about both the course
store and the knowledge-base store. Aggregate counts on the
``Course`` row are kept consistent with the actual library
membership here, not in either store individually.

Course operations that need library fan-out
-------------------------------------------

* ``attach_library``  — set ``kb.course_id = course.id``
* ``detach_library``  — set ``kb.course_id = NULL`` (no delete)
* ``delete_course``    — detach all libraries first, then delete
                         the course row

After every fan-out the service recomputes the course's cached
``library_count``, ``document_count``, ``ready_count`` and
``total_chunks`` so the courses list endpoint returns the right
numbers in one round-trip.
"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger

from tutor.services.courses.schema import Course
from tutor.services.courses.store import CourseStore, get_course_store
from tutor.services.knowledge_base.schema import IngestionStatus
from tutor.services.knowledge_base.sqlite_store import (
    KnowledgeBaseSQLiteStore,
    get_kb_store,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class CourseService:
    """High-level course operations."""

    def __init__(
        self,
        *,
        store: CourseStore | None = None,
        kb_store: KnowledgeBaseSQLiteStore | None = None,
    ) -> None:
        self.store = store or get_course_store()
        self.kb_store = kb_store or get_kb_store()

    # ---- CRUD -----------------------------------------------------------

    def list_courses(self) -> list[Course]:
        return self.store.list_courses()

    def get_course(self, course_id: str) -> Course | None:
        return self.store.get_course(course_id)

    def create_course(
        self,
        *,
        name: str,
        description: str = "",
        knowledge_graph_id: str = "",
        extra_metadata: dict[str, Any] | None = None,
    ) -> Course:
        course = Course(
            id=_new_id("course"),
            name=name,
            description=description,
            knowledge_graph_id=knowledge_graph_id,
            extra_metadata=extra_metadata or {},
        )
        return self.store.upsert_course(course)

    def update_course(
        self,
        course_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        knowledge_graph_id: str | None = None,
    ) -> Course | None:
        existing = self.store.get_course(course_id)
        if existing is None:
            return None
        if name is not None:
            existing.name = name
        if description is not None:
            existing.description = description
        if knowledge_graph_id is not None:
            existing.knowledge_graph_id = knowledge_graph_id
        return self.store.upsert_course(existing)

    # ---- library membership --------------------------------------------

    def attach_library(self, course_id: str, lib_id: str) -> Course | None:
        """Bind a knowledge base to this course.

        A library can belong to at most one course; attaching it
        implicitly detaches it from any previous course. The
        library row's ``course_id`` is updated; the course's
        cached aggregates are recomputed.
        """
        course = self.store.get_course(course_id)
        if course is None:
            return None
        lib = self.kb_store.get_library(lib_id)
        if lib is None:
            raise ValueError(f"library not found: {lib_id}")
        # Detach from previous course, if any, then attach to the
        # new one. We use the store's API so the library aggregate
        # counts on both courses stay consistent.
        previous_course_id = lib.course_id
        self.kb_store.set_library_course(lib_id, course_id)
        if previous_course_id and previous_course_id != course_id:
            self._recompute_aggregates(previous_course_id)
        self._recompute_aggregates(course_id)
        return self.store.get_course(course_id)

    def detach_library(self, course_id: str, lib_id: str) -> Course | None:
        """Remove a library from a course (does not delete the library).

        After this call the library's ``course_id`` is ``NULL`` —
        the library still exists, it just isn't part of any
        course's RAG scope.
        """
        course = self.store.get_course(course_id)
        if course is None:
            return None
        lib = self.kb_store.get_library(lib_id)
        if lib is None or lib.course_id != course_id:
            return course
        self.kb_store.set_library_course(lib_id, None)
        self._recompute_aggregates(course_id)
        return self.store.get_course(course_id)

    def delete_course(self, course_id: str) -> bool:
        """Delete a course, detaching its libraries first.

        Per the 2026-06-21 plan: "deleting a course defaults to
        moving its libraries out" — we do exactly that. Library
        rows are NOT deleted; their ``course_id`` is set to NULL.
        """
        course = self.store.get_course(course_id)
        if course is None:
            return False
        # Detach every library currently bound to this course.
        for lib in self.kb_store.list_libraries():
            if lib.course_id == course_id:
                self.kb_store.set_library_course(lib.id, None)
        return self.store.delete_course(course_id)

    # ---- helpers --------------------------------------------------------

    def _recompute_aggregates(self, course_id: str) -> None:
        """Recompute the cached aggregate counts on a course row.

        The KB store is the source of truth for the library list;
        we walk it once and SUM the document counts up. ``kb_count``
        is the number of libraries currently bound to the course.
        """
        libraries = [
            lib for lib in self.kb_store.list_libraries() if lib.course_id == course_id
        ]
        self.store.update_aggregates(
            course_id,
            library_count=len(libraries),
            document_count=sum(lib.document_count for lib in libraries),
            ready_count=sum(lib.ready_count for lib in libraries),
            total_chunks=sum(lib.total_chunks for lib in libraries),
        )


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def seed_default_courses(service: CourseService) -> None:
    """Create the prebuilt ``AI 导论`` course on first startup.

    The 2026-06-21 plan says: "the prebuilt AI 导论 is created
    through a seed migration as ordinary Course / KnowledgeBase
    data, no longer hardcoded in the front-end store". This
    function is the seed entry-point: it does nothing if the
    course already exists.
    """
    existing = service.get_course("course_ai_intro")
    if existing is not None:
        return
    course = service.create_course(
        name="人工智能导论",
        description="面向零基础学生的 AI 入门课程，覆盖机器学习、深度学习与典型应用。",
        knowledge_graph_id="ai_introduction",
    )
    # Re-key to the stable id the rest of the system uses.
    if course.id != "course_ai_intro":
        service.store.delete_course(course.id)
        seeded = Course(
            id="course_ai_intro",
            name=course.name,
            description=course.description,
            knowledge_graph_id=course.knowledge_graph_id,
            is_seeded=True,
        )
        service.store.upsert_course(seeded)
    # Bind the prebuilt KB (if it exists already) to the course.
    lib = service.kb_store.get_library("ai_introduction")
    if lib is not None and lib.course_id is None:
        service.attach_library("course_ai_intro", "ai_introduction")
    logger.info("seeded default course 'course_ai_intro'")


__all__ = ["CourseService", "seed_default_courses"]
