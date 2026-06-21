"""In-memory store for knowledge bases (Task 8).

We deliberately use an in-memory dict (not SQLite) for the metadata.
The chunked text + embeddings live on disk under
``data/knowledge_bases/{kb_id}/``. This keeps the metadata simple
while still being crash-recoverable: the on-disk state and the in-memory
state are reconstructed on startup from the directory contents.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from tutor.services.knowledge_base.schema import (
    IngestionStatus,
    KnowledgeBaseRecord,
    KnowledgeDocument,
)


class KnowledgeBaseStore:
    """Singleton in-memory store for libraries + documents."""

    def __init__(self) -> None:
        self._libs: dict[str, KnowledgeBaseRecord] = {}
        self._docs: dict[str, KnowledgeDocument] = {}
        self._docs_by_kb: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    # ---- libraries -------------------------------------------------------

    def upsert_library(self, lib: KnowledgeBaseRecord) -> KnowledgeBaseRecord:
        with self._lock:
            self._libs[lib.id] = lib
        return lib

    def get_library(self, lib_id: str) -> KnowledgeBaseRecord | None:
        return self._libs.get(lib_id)

    def list_libraries(self) -> list[KnowledgeBaseRecord]:
        return list(self._libs.values())

    def delete_library(self, lib_id: str) -> bool:
        with self._lock:
            if lib_id not in self._libs:
                return False
            self._libs.pop(lib_id, None)
            # also drop its documents
            doc_ids = self._docs_by_kb.pop(lib_id, [])
            for d in doc_ids:
                self._docs.pop(d, None)
        return True

    # ---- documents -------------------------------------------------------

    def upsert_document(self, doc: KnowledgeDocument) -> KnowledgeDocument:
        with self._lock:
            self._docs[doc.id] = doc
            ids = self._docs_by_kb.setdefault(doc.knowledge_base_id, [])
            if doc.id not in ids:
                ids.append(doc.id)
            self._recompute_library_counts(doc.knowledge_base_id)
        return doc

    def get_document(self, doc_id: str) -> KnowledgeDocument | None:
        return self._docs.get(doc_id)

    def list_documents(self, lib_id: str) -> list[KnowledgeDocument]:
        ids = self._docs_by_kb.get(lib_id, [])
        return [self._docs[i] for i in ids if i in self._docs]

    def delete_document(self, doc_id: str) -> bool:
        with self._lock:
            doc = self._docs.pop(doc_id, None)
            if doc is None:
                return False
            ids = self._docs_by_kb.get(doc.knowledge_base_id, [])
            if doc_id in ids:
                ids.remove(doc_id)
            self._recompute_library_counts(doc.knowledge_base_id)
        return True

    def set_document_status(
        self,
        doc_id: str,
        *,
        status: IngestionStatus,
        chunk_count: int | None = None,
        embedding_model: str | None = None,
        embedding_warning: str | None = None,
        error: str | None = None,
        error_code: str | None = None,
    ) -> KnowledgeDocument | None:
        with self._lock:
            doc = self._docs.get(doc_id)
            if doc is None:
                return None
            doc.status = status
            if chunk_count is not None:
                doc.chunk_count = chunk_count
            if embedding_model is not None:
                doc.embedding_model = embedding_model
            if embedding_warning is not None:
                doc.embedding_warning = embedding_warning
            if error is not None:
                doc.error = error
            if error_code is not None:
                doc.error_code = error_code
            from datetime import datetime, timezone
            doc.updated_at = datetime.now(timezone.utc)
            self._recompute_library_counts(doc.knowledge_base_id)
        return doc

    def _recompute_library_counts(self, lib_id: str) -> None:
        lib = self._libs.get(lib_id)
        if lib is None:
            return
        docs = [self._docs[i] for i in self._docs_by_kb.get(lib_id, []) if i in self._docs]
        lib.document_count = len(docs)
        lib.ready_count = sum(1 for d in docs if d.status == IngestionStatus.READY)
        lib.failed_count = sum(1 for d in docs if d.status == IngestionStatus.FAILED)
        lib.total_chunks = sum(d.chunk_count for d in docs)
        from datetime import datetime, timezone
        lib.updated_at = datetime.now(timezone.utc)


_store: KnowledgeBaseStore | None = None
_store_lock = threading.Lock()


def get_kb_store() -> KnowledgeBaseStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = KnowledgeBaseStore()
    return _store


def reset_kb_store() -> None:
    global _store
    _store = None


__all__ = ["KnowledgeBaseStore", "get_kb_store", "reset_kb_store"]
