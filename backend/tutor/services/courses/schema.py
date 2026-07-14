"""Course schemas (2026-06-21 plan).

A :class:`Course` is the top-level grouping for knowledge bases:

  Course 1 ── N KnowledgeBase
              (course_id nullable)

A knowledge base belongs to at most one course — when ``course_id``
is set on the library row, the library is in scope for the course's
RAG retrieval. A library with ``course_id = None`` is "standalone":
it still shows up in the KB picker but is not part of any course's
RAG scope.

Deleting a course
-----------------
The spec calls for the default behaviour to be "move libraries out
of the course" rather than "cascade delete documents". The library
itself is NOT deleted by the course — only the course_id pointer
is set to NULL. Library deletion is an explicit, separate action.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Course(BaseModel):
    """A named course row in the database.

    ``knowledge_graph_id`` references a YAML knowledge graph under
    :mod:`tutor.services.knowledge_graph`. When unset, the course
    is a plain grouping with no prerequisite structure.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    knowledge_graph_id: str = ""
    is_seeded: bool = False
    # Aggregate counts cached on the row so the courses list
    # endpoint can return them in one round-trip. Recomputed on
    # any library add/remove/rebind.
    library_count: int = 0
    document_count: int = 0
    ready_count: int = 0
    total_chunks: int = 0
    # Optional operator metadata (free-form; we keep it in a JSON
    # column on disk and expose it as a dict on the wire).
    extra_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


__all__ = ["Course"]
