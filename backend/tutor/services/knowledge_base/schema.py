"""Knowledge base ingestion schemas (Task 8).

Two top-level records:

- :class:`KnowledgeBaseRecord` — a named library (e.g. "ai_introduction").
- :class:`KnowledgeDocument` — one uploaded file inside a library, with
  ingestion state machine: ``uploaded → extracting → chunking →
  embedding → ready | failed``.

The state is persisted in SQLite so the UI can poll and show progress.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class IngestionStatus(str, Enum):
    UPLOADED = "uploaded"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".docx", ".pptx", ".md", ".txt"}
)


class KnowledgeBaseRecord(BaseModel):
    """A named library.

    2026-06-21 plan: a library may optionally belong to a single
    course via ``course_id`` (Part D of the RAG-overhaul plan). When
    set, retrieval scoped to a course transparently expands to all
    libraries attached to that course. ``None`` means "standalone"
    — the library shows up in the knowledge-base picker but not in
    the course's RAG scope.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""
    is_seeded: bool = False
    document_count: int = 0
    ready_count: int = 0
    failed_count: int = 0
    total_chunks: int = 0
    embedding_model: str = ""
    # 2026-06-21 plan (Part D): optional course binding. A library
    # may belong to at most one course; the constraint is enforced
    # at the service layer, not the schema, so cross-service
    # migrations can move libraries between courses atomically.
    course_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class KnowledgeDocument(BaseModel):
    """One ingested document.

    2026-06-21 plan: the document carries the *index manifest* on the
    wire — the embedder provider, model and dimension that produced
    its vectors, plus an ``index_version`` integer that the RAG
    pipeline bumps on every config change. The retrieval service
    compares the manifest against the runtime config and flags
    ``reindex_required = True`` when they don't match, so the UI
    can show a "RAG is stale — reindex?" chip without having to
    read ``chunks.json`` directly.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    knowledge_base_id: str
    display_name: str
    source_filename: str
    extension: str
    size_bytes: int = 0
    checksum: str = ""
    status: IngestionStatus = IngestionStatus.UPLOADED
    chunk_count: int = 0
    embedding_model: str = ""
    # ---- 2026-06-21 index manifest ----
    embedder_provider: str = ""
    embedder_model: str = ""
    embedder_dimension: int = 0
    index_version: int = 0
    reindex_required: bool = False
    # Non-fatal embedding warning (e.g. "embedder not configured, using
    # text-only fallback"). The document is still ``ready`` for RAG,
    # but downstream consumers should know they have no vectors to
    # match against. Distinct from ``error_code`` which marks the
    # whole ingestion as failed.
    embedding_warning: str | None = None
    error: str | None = None
    error_code: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


__all__ = [
    "IngestionStatus",
    "KnowledgeBaseRecord",
    "KnowledgeDocument",
    "SUPPORTED_EXTENSIONS",
]
