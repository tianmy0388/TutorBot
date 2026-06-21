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
    """A named library."""

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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class KnowledgeDocument(BaseModel):
    """One ingested document."""

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
