"""Course service (2026-06-21 plan).

Public surface:

  - :class:`Course` (Pydantic model)
  - :class:`CourseService` (high-level operations)
  - :class:`CourseStore` (SQLite-backed persistence)
  - :func:`get_course_store`, :func:`get_course_service`
  - :func:`seed_default_courses`
"""

from tutor.services.courses.schema import Course
from tutor.services.courses.service import (
    CourseService,
    seed_default_courses,
)
from tutor.services.courses.store import (
    CourseStore,
    get_course_store,
    reset_course_store,
)


_service: CourseService | None = None


def get_course_service() -> CourseService:
    """Return the process-wide :class:`CourseService` (lazy)."""
    global _service
    if _service is None:
        _service = CourseService()
    return _service


def reset_course_service() -> None:
    """Drop the singleton (tests)."""
    global _service
    _service = None


__all__ = [
    "Course",
    "CourseService",
    "CourseStore",
    "get_course_service",
    "get_course_store",
    "reset_course_service",
    "reset_course_store",
    "seed_default_courses",
]
