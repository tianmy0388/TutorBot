"""Knowledge base ingestion service (Task 8).

The service orchestrates the state machine:

  uploaded → extracting → chunking → embedding → ready
                                                    ↘ failed

The state transitions live in this module so the API router, the
file-upload handler and the (future) async worker all call the same
``KnowledgeBaseService`` methods.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from tutor.services.config.settings import Settings, get_settings
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
from tutor.services.knowledge_base.store import (
    KnowledgeBaseStore,
    get_kb_store,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class KnowledgeBaseService:
    """High-level orchestrator for ingestion."""

    def __init__(
        self,
        *,
        store: KnowledgeBaseStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.store = store or get_kb_store()
        self.settings = settings or get_settings()

    # ---- library CRUD ----------------------------------------------------

    def list_libraries(self) -> list[KnowledgeBaseRecord]:
        return sorted(self.store.list_libraries(), key=lambda r: r.created_at)

    def get_library(self, lib_id: str) -> KnowledgeBaseRecord | None:
        return self.store.get_library(lib_id)

    def create_library(self, *, name: str, description: str = "") -> KnowledgeBaseRecord:
        lib_id = _new_id("kb")
        rec = KnowledgeBaseRecord(
            id=lib_id, name=name, description=description,
        )
        self.store.upsert_library(rec)
        return rec

    def delete_library(self, lib_id: str) -> bool:
        return self.store.delete_library(lib_id)

    # ---- document upload -------------------------------------------------

    def upload_document(
        self,
        *,
        knowledge_base_id: str,
        source_path: Path,
        original_filename: str,
    ) -> KnowledgeDocument:
        """Validate, copy into the library directory, and create the doc
        record. The actual extraction / chunking / embedding happen in
        :meth:`run_ingestion` (sync, for the demo)."""
        if self.store.get_library(knowledge_base_id) is None:
            raise ValueError(f"library not found: {knowledge_base_id}")
        ext = source_path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported extension {ext!r}")
        if not source_path.exists():
            raise ValueError("uploaded file is missing")

        # Copy the bytes into the library directory.
        lib_dir = self._library_source_dir(knowledge_base_id)
        doc_id = _new_id("doc")
        target = lib_dir / f"{doc_id}{ext}"
        shutil.copy2(source_path, target)

        doc = KnowledgeDocument(
            id=doc_id,
            knowledge_base_id=knowledge_base_id,
            display_name=original_filename,
            source_filename=original_filename,
            extension=ext,
            size_bytes=target.stat().st_size,
            checksum=_checksum(target),
            status=IngestionStatus.UPLOADED,
        )
        self.store.upsert_document(doc)
        return doc

    def list_documents(self, lib_id: str) -> list[KnowledgeDocument]:
        return self.store.list_documents(lib_id)

    def get_document(self, doc_id: str) -> KnowledgeDocument | None:
        return self.store.get_document(doc_id)

    def delete_document(self, doc_id: str) -> bool:
        doc = self.store.get_document(doc_id)
        if doc is None:
            return False
        # Best-effort: also remove the on-disk file.
        try:
            path = self._document_path(doc)
            if path.exists():
                path.unlink()
        except OSError:  # noqa: BLE001
            pass
        return self.store.delete_document(doc_id)

    def retry_document(self, doc_id: str) -> KnowledgeDocument | None:
        """Reset a failed document to ``uploaded`` and re-run ingestion."""
        doc = self.store.get_document(doc_id)
        if doc is None:
            return None
        if doc.status != IngestionStatus.FAILED:
            raise ValueError(f"only failed documents can be retried (got {doc.status})")
        self.store.set_document_status(
            doc_id,
            status=IngestionStatus.UPLOADED,
            error=None,
            error_code=None,
        )
        return self.run_ingestion(doc_id)

    # ---- ingestion --------------------------------------------------------

    def run_ingestion(self, doc_id: str) -> KnowledgeDocument | None:
        """Run the full extract → chunk → embed pipeline for one document.

        This is synchronous for the demo; the state machine is
        preserved so it can be moved to a background worker later.
        """
        doc = self.store.get_document(doc_id)
        if doc is None:
            return None
        path = self._document_path(doc)
        if not path.exists():
            self.store.set_document_status(
                doc_id,
                status=IngestionStatus.FAILED,
                error=f"missing source file: {path}",
                error_code="MISSING_SOURCE",
            )
            return self.store.get_document(doc_id)

        # Extract
        self.store.set_document_status(doc_id, status=IngestionStatus.EXTRACTING)
        try:
            chunks = extract_text(path)
        except LoaderError as e:
            self.store.set_document_status(
                doc_id,
                status=IngestionStatus.FAILED,
                error=e.message,
                error_code=e.code,
            )
            return self.store.get_document(doc_id)
        except Exception as e:  # noqa: BLE001
            self.store.set_document_status(
                doc_id,
                status=IngestionStatus.FAILED,
                error=f"{type(e).__name__}: {e}",
                error_code="EXTRACTION_FAILED",
            )
            return self.store.get_document(doc_id)

        # Chunk (re-aggregate by char count for stable counts)
        self.store.set_document_status(doc_id, status=IngestionStatus.CHUNKING)
        chunk_records = self._chunk(chunks)
        if not chunk_records:
            self.store.set_document_status(
                doc_id,
                status=IngestionStatus.FAILED,
                error="chunking produced no chunks",
                error_code="EMPTY_DOCUMENT",
            )
            return self.store.get_document(doc_id)

        # Embed (best-effort: if no embedder configured, we still mark
        # the document ready as long as the text is present).
        self.store.set_document_status(doc_id, status=IngestionStatus.EMBEDDING)
        try:
            embedding_model, embedded = self._embed(chunk_records)
        except Exception as e:  # noqa: BLE001
            self.store.set_document_status(
                doc_id,
                status=IngestionStatus.FAILED,
                error=f"embed failed: {e}",
                error_code="EMBED_FAILED",
            )
            return self.store.get_document(doc_id)

        # Persist the chunk store to disk.
        try:
            self._write_chunk_index(doc, chunk_records, embedded)
        except OSError as e:
            self.store.set_document_status(
                doc_id,
                status=IngestionStatus.FAILED,
                error=f"index write failed: {e}",
                error_code="INDEX_WRITE_FAILED",
            )
            return self.store.get_document(doc_id)

        self.store.set_document_status(
            doc_id,
            status=IngestionStatus.READY,
            chunk_count=len(chunk_records),
            embedding_model=embedding_model,
        )
        return self.store.get_document(doc_id)

    # ---- chunking / embedding helpers ------------------------------------

    def _chunk(
        self, chunks: list[ExtractedChunk], *, max_chars: int | None = None
    ) -> list[dict[str, str]]:
        max_chars = max_chars or max(200, self.settings.rag_chunk_size * 4)
        overlap = max(0, self.settings.rag_chunk_overlap)
        out: list[dict[str, str]] = []
        for chunk in chunks:
            text = chunk.text.strip()
            if not text:
                continue
            if len(text) <= max_chars:
                out.append({"text": text, "anchor": chunk.anchor})
                continue
            # Naive sliding window.
            start = 0
            while start < len(text):
                end = min(len(text), start + max_chars)
                out.append(
                    {"text": text[start:end], "anchor": chunk.anchor}
                )
                if end == len(text):
                    break
                start = max(end - overlap, start + 1)
        return out

    def _embed(self, chunks: list[dict[str, str]]) -> tuple[str, list[list[float]]]:
        """Embed chunks using the runtime embedder. Returns ("", [])
        if the embedder isn't configured — the document still gets
        marked ready for text-only RAG fallback."""
        model = self.settings.embed_model or ""
        try:
            from tutor.services.embeddings.embedder_factory import (
                get_runtime_embedder,
            )
            from tutor.services.embeddings.base import EmbedRequest

            embedder = get_runtime_embedder(self.settings)
            resp = embedder.embed(EmbedRequest(texts=[c["text"] for c in chunks]))
            return model, resp.embeddings
        except Exception:  # noqa: BLE001
            logger.warning("Embedder unavailable; storing chunks without vectors")
            return "", []

    def _write_chunk_index(
        self,
        doc: KnowledgeDocument,
        chunks: list[dict[str, str]],
        embeddings: list[list[float]],
    ) -> None:
        index_dir = self._library_source_dir(doc.knowledge_base_id) / "indexes" / doc.id
        index_dir.mkdir(parents=True, exist_ok=True)
        import json

        payload = {
            "document_id": doc.id,
            "knowledge_base_id": doc.knowledge_base_id,
            "embedding_model": doc.embedding_model or "",
            "chunks": chunks,
            "embeddings": embeddings,
        }
        (index_dir / "chunks.json").write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    # ---- paths ------------------------------------------------------------

    def _library_source_dir(self, lib_id: str) -> Path:
        base = Path(self.settings.data_dir) / "knowledge_bases" / lib_id / "sources"
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _document_path(self, doc: KnowledgeDocument) -> Path:
        return (
            self._library_source_dir(doc.knowledge_base_id)
            / f"{doc.id}{doc.extension}"
        )


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def seed_default_libraries(service: KnowledgeBaseService) -> None:
    """Create the prebuilt ``ai_introduction`` library on first startup."""
    if service.get_library("ai_introduction") is not None:
        return
    lib = KnowledgeBaseRecord(
        id="ai_introduction",
        name="人工智能导论（预置）",
        description="系统级预置课程资料，PDF/DOCX/PPTX 教材与讲义。",
        is_seeded=True,
    )
    service.store.upsert_library(lib)


__all__ = ["KnowledgeBaseService", "seed_default_libraries"]
