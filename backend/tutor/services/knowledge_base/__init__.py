"""Knowledge base ingestion service (Task 8)."""

from tutor.services.knowledge_base.loaders import (
    ExtractedChunk,
    LoaderError,
    extract_text,
)
from tutor.services.knowledge_base.schema import (
    IngestionStatus,
    KnowledgeBaseRecord,
    KnowledgeDocument,
    SUPPORTED_EXTENSIONS,
)
from tutor.services.knowledge_base.service import (
    KnowledgeBaseService,
    get_ingestion_queue,
    reset_ingestion_queue,
    seed_default_libraries,
)
from tutor.services.knowledge_base.store import (
    KnowledgeBaseStore,
    get_kb_store,
    reset_kb_store,
)

__all__ = [
    "ExtractedChunk",
    "IngestionStatus",
    "KnowledgeBaseRecord",
    "KnowledgeBaseService",
    "KnowledgeBaseStore",
    "KnowledgeDocument",
    "LoaderError",
    "SUPPORTED_EXTENSIONS",
    "extract_text",
    "get_ingestion_queue",
    "get_kb_store",
    "reset_ingestion_queue",
    "reset_kb_store",
    "seed_default_libraries",
]
