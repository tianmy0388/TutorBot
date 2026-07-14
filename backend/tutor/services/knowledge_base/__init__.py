"""Knowledge base ingestion service (Task 8 + 2026-06-21 plan).

The metadata store was upgraded from an in-memory dict to a
SQLite-backed implementation in the 2026-06-21 stability plan; see
:mod:`tutor.services.knowledge_base.sqlite_store` for the new
implementation. The legacy in-memory class is preserved as a
deprecated alias for any external code that imported it.
"""

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
from tutor.services.knowledge_base.sqlite_store import (
    KnowledgeBaseSQLiteStore,
    get_kb_store,
    reset_kb_store,
)

# Back-compat shim: external code may still import
# ``KnowledgeBaseStore`` (the in-memory class). It is now an alias
# for the SQLite-backed store, which has the same public surface.
KnowledgeBaseStore = KnowledgeBaseSQLiteStore

__all__ = [
    "ExtractedChunk",
    "IngestionStatus",
    "KnowledgeBaseRecord",
    "KnowledgeBaseService",
    "KnowledgeBaseSQLiteStore",
    "KnowledgeBaseStore",  # deprecated alias
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
