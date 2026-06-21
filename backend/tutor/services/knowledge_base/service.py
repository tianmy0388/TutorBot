"""Knowledge base ingestion service (Task 8 + 2026-06-21 async fix).

The service orchestrates the state machine:

  uploaded → extracting → chunking → embedding → ready
                                                    ↘ failed

The state transitions live in this module so the API router, the
file-upload handler and the (future) async worker all call the same
``KnowledgeBaseService`` methods.

Async dispatch
--------------
Stage 2 of the 2026-06-21 stability plan decouples upload from
ingestion. The router now calls ``enqueue_ingestion`` instead of
``run_ingestion``; the method schedules an ``asyncio.Task`` on a
bounded queue and returns immediately. The HTTP upload response is
no longer blocked by PDF parsing / embedding latency.
"""

from __future__ import annotations

import asyncio
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


def _sanitize_text(s: str) -> str:
    """Replace lone surrogates (U+D800..U+DFFF) so strict utf-8
    encoders don't crash on bad PDF font tables. Real surrogate pairs
    (mathematical alphanumerics etc.) are kept intact."""
    if not s:
        return s
    try:
        # Fast path: round-trips cleanly.
        s.encode("utf-8").decode("utf-8")
        return s
    except UnicodeEncodeError:
        pass
    # Replace lone surrogates with U+FFFD, the standard replacement
    # character. Encoding with "surrogatepass" lets us pull the bytes
    # in, then drop invalid sequences.
    return s.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def _checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# Default concurrency cap for the in-app ingestion queue. Two
# concurrent parses is enough for a local demo without holding the
# event loop hostage; a real deployment would push this to a
# dedicated worker (Celery / RQ / arq).
DEFAULT_MAX_CONCURRENT_INGESTIONS = 2


class _IngestionQueue:
    """Bounded asyncio task queue for knowledge-base ingestion.

    Public surface is just :meth:`enqueue` and :meth:`shutdown`. The
    class is process-singleton and shared via :func:`get_ingestion_queue`.
    """

    def __init__(self, *, max_concurrent: int = DEFAULT_MAX_CONCURRENT_INGESTIONS) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    def enqueue(self, coro_factory) -> asyncio.Task[Any]:
        """Schedule ``coro_factory()`` (called with no args) on the loop.

        The factory must return a coroutine (typically a bound method
        that does ``self.run_ingestion(doc_id)``). Returns the
        :class:`asyncio.Task` so the caller can attach callbacks or
        keep a reference.
        """
        if self._closed:
            raise RuntimeError("ingestion queue is shut down")
        task = asyncio.create_task(self._guarded(coro_factory()))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _guarded(self, coro) -> Any:
        try:
            async with self._semaphore:
                return await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            # The queue's job is to ensure a runaway ingestion task
            # never escapes — log and swallow so the loop stays alive.
            logger.exception(
                "ingestion task failed outside the state machine: {err}",
                err=e,
            )
            return None

    async def drain(self, timeout: float = 5.0) -> None:
        """Wait for in-flight tasks to complete (best effort)."""
        if not self._tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("ingestion queue drain timed out after {t}s", t=timeout)

    def shutdown(self) -> None:
        """Cancel in-flight tasks. Idempotent."""
        if self._closed:
            return
        self._closed = True
        for t in list(self._tasks):
            t.cancel()


_queue: _IngestionQueue | None = None
_queue_lock: asyncio.Lock | None = None


def get_ingestion_queue() -> _IngestionQueue:
    """Return the process-wide ingestion queue (lazy)."""
    global _queue
    if _queue is None:
        _queue = _IngestionQueue()
    return _queue


async def reset_ingestion_queue() -> None:
    """Cancel and drop the singleton queue (tests)."""
    global _queue
    if _queue is not None:
        await _queue.drain(timeout=1.0)
        _queue.shutdown()
        _queue = None


class KnowledgeBaseService:
    """High-level orchestrator for ingestion."""

    def __init__(
        self,
        *,
        store: KnowledgeBaseStore | None = None,
        settings: Settings | None = None,
        queue: _IngestionQueue | None = None,
    ) -> None:
        self.store = store or get_kb_store()
        self.settings = settings or get_settings()
        self._queue = queue

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

    def enqueue_ingestion(self, doc_id: str) -> asyncio.Task[Any]:
        """Schedule a background ingestion run for ``doc_id``.

        Returns the :class:`asyncio.Task` so callers (the router) can
        keep a reference, but the HTTP response should not wait on it.
        The task is concurrency-capped and exceptions are caught at
        the queue level so a single bad document cannot crash the
        event loop.
        """
        queue = self._queue or get_ingestion_queue()

        async def _runner() -> KnowledgeDocument | None:
            return self.run_ingestion(doc_id)

        return queue.enqueue(_runner)

    def run_ingestion(self, doc_id: str) -> KnowledgeDocument | None:
        """Run the full extract → chunk → embed pipeline for one document.

        Synchronous but the state machine is fully isolated, so it
        can be called from a background ``asyncio.Task`` (see
        :meth:`enqueue_ingestion`) without blocking the request
        thread. The document record is the source of truth — the
        function reads its current status before deciding to run.
        """
        import time

        doc = self.store.get_document(doc_id)
        if doc is None:
            return None
        lib_id = doc.knowledge_base_id
        started = time.monotonic()
        logger.info(
            "ingestion.start lib_id={lib_id} doc_id={doc_id} filename={filename}",
            lib_id=lib_id,
            doc_id=doc_id,
            filename=doc.source_filename,
        )
        path = self._document_path(doc)
        if not path.exists():
            self.store.set_document_status(
                doc_id,
                status=IngestionStatus.FAILED,
                error=f"missing source file: {path}",
                error_code="MISSING_SOURCE",
            )
            self._log_outcome(doc_id, lib_id, "MISSING_SOURCE", started)
            return self.store.get_document(doc_id)

        # Extract
        stage_started = time.monotonic()
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
            self._log_outcome(doc_id, lib_id, e.code, started)
            return self.store.get_document(doc_id)
        except Exception as e:  # noqa: BLE001
            self.store.set_document_status(
                doc_id,
                status=IngestionStatus.FAILED,
                error=f"{type(e).__name__}: {e}",
                error_code="EXTRACTION_FAILED",
            )
            self._log_outcome(doc_id, lib_id, "EXTRACTION_FAILED", started)
            return self.store.get_document(doc_id)
        logger.info(
            "ingestion.stage lib_id={lib_id} doc_id={doc_id} stage={stage} duration_ms={ms}",
            lib_id=lib_id,
            doc_id=doc_id,
            stage="extract",
            ms=int((time.monotonic() - stage_started) * 1000),
        )

        # Chunk (re-aggregate by char count for stable counts)
        stage_started = time.monotonic()
        self.store.set_document_status(doc_id, status=IngestionStatus.CHUNKING)
        chunk_records = self._chunk(chunks)
        if not chunk_records:
            self.store.set_document_status(
                doc_id,
                status=IngestionStatus.FAILED,
                error="chunking produced no chunks",
                error_code="EMPTY_DOCUMENT",
            )
            self._log_outcome(doc_id, lib_id, "EMPTY_DOCUMENT", started)
            return self.store.get_document(doc_id)
        logger.info(
            "ingestion.stage lib_id={lib_id} doc_id={doc_id} stage={stage} duration_ms={ms}",
            lib_id=lib_id,
            doc_id=doc_id,
            stage="chunk",
            ms=int((time.monotonic() - stage_started) * 1000),
        )

        # Embed (best-effort: if no embedder configured, we still mark
        # the document ready as long as the text is present).
        stage_started = time.monotonic()
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
            self._log_outcome(doc_id, lib_id, "EMBED_FAILED", started)
            return self.store.get_document(doc_id)
        logger.info(
            "ingestion.stage lib_id={lib_id} doc_id={doc_id} stage={stage} duration_ms={ms}",
            lib_id=lib_id,
            doc_id=doc_id,
            stage="embed",
            ms=int((time.monotonic() - stage_started) * 1000),
        )

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
            self._log_outcome(doc_id, lib_id, "INDEX_WRITE_FAILED", started)
            return self.store.get_document(doc_id)

        self.store.set_document_status(
            doc_id,
            status=IngestionStatus.READY,
            chunk_count=len(chunk_records),
            embedding_model=embedding_model,
            # Surface the "no vectors" case as a non-fatal warning so
            # the UI can show a chip and the operator knows their
            # runtime config needs an embedder for real RAG. The doc
            # is still ``ready`` and text-only retrieval still works.
            embedding_warning=(
                "embedder_unavailable: storing chunks without vectors; "
                "RAG will use text-only matching"
            )
            if not embedding_model
            else None,
        )
        self._log_outcome(doc_id, lib_id, "READY", started)
        return self.store.get_document(doc_id)

    def _log_outcome(self, doc_id: str, lib_id: str, code: str, started: float) -> None:
        import time

        logger.info(
            "ingestion.outcome lib_id={lib_id} doc_id={doc_id} error_code={code} duration_ms={ms}",
            lib_id=lib_id,
            doc_id=doc_id,
            code=code,
            ms=int((time.monotonic() - started) * 1000),
        )

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
        marked ready for text-only RAG fallback.

        ``OpenAICompatEmbedder.embed`` is an ``async def`` coroutine;
        ``run_ingestion`` is sync (it's scheduled on the asyncio loop
        via ``enqueue_ingestion`` → ``_runner`` but the function body
        itself runs synchronously in the worker thread). We bridge
        the two with a private event loop. The previous version
        forgot the ``await`` and returned a coroutine object whose
        ``.vectors`` attribute didn't exist — the soft fallback masked
        it as a no-embedder error.
        """
        model = self.settings.embed_model or ""
        if not chunks:
            return model, []
        try:
            import asyncio
            from tutor.services.embeddings.embedder_factory import (
                get_runtime_embedder,
            )
            from tutor.services.embeddings.base import EmbedRequest

            embedder = get_runtime_embedder(self.settings)
            req = EmbedRequest(input=[c["text"] for c in chunks])

            async def _call() -> list[list[float]]:
                resp = await embedder.embed(req)
                return list(resp.vectors)

            try:
                # Fast path: a loop is already running on this thread
                # (the worker that drained the ingestion queue).
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not None and not running.is_closed():
                # We're inside a worker that already owns the loop.
                # Schedule the coroutine and wait synchronously via a
                # future, then collect the result.
                future = asyncio.run_coroutine_threadsafe(_call(), running)
                vectors = future.result()
            else:
                vectors = asyncio.run(_call())
            return model, vectors
        except Exception as e:  # noqa: BLE001
            logger.warning("Embedder unavailable: {err}", err=e)
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

        # Sanitize chunks so any lone surrogates (U+D800..U+DFFF) from
        # bad PDF font tables don't break the strict utf-8 encoder.
        # We use errors="replace" rather than stripping so the source
        # still has useful text where possible.
        clean_chunks = [
            {"text": _sanitize_text(c.get("text", "")), "anchor": _sanitize_text(c.get("anchor", ""))}
            for c in chunks
        ]
        payload = {
            "document_id": doc.id,
            "knowledge_base_id": doc.knowledge_base_id,
            "embedding_model": doc.embedding_model or "",
            "chunks": clean_chunks,
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
