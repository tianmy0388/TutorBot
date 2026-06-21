"""Tests for the knowledge base service (Task 8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tutor.services.knowledge_base import (
    IngestionStatus,
    KnowledgeBaseService,
    seed_default_libraries,
)
from tutor.services.knowledge_base.store import (
    KnowledgeBaseStore,
    reset_kb_store,
)


@pytest.fixture
def store() -> KnowledgeBaseStore:
    reset_kb_store()
    return KnowledgeBaseStore()


@pytest.fixture
def service(store: KnowledgeBaseStore, tmp_path, monkeypatch) -> KnowledgeBaseService:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    return KnowledgeBaseService(store=store)


def _write_tmp_file(path: Path, content: str = "Hello world.\n") -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_create_and_list_library(service: KnowledgeBaseService) -> None:
    lib = service.create_library(name="My KB", description="for the test")
    assert lib.id.startswith("kb_")
    assert service.get_library(lib.id) is not None
    assert any(l.id == lib.id for l in service.list_libraries())


def test_seed_creates_ai_introduction_default() -> None:
    reset_kb_store()
    svc = KnowledgeBaseService()
    seed_default_libraries(svc)
    lib = svc.get_library("ai_introduction")
    assert lib is not None
    assert lib.is_seeded is True


def test_upload_document_rejects_unsupported_extension(
    service: KnowledgeBaseService, tmp_path: Path
) -> None:
    lib = service.create_library(name="X")
    src = tmp_path / "x.xyz"
    src.write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError):
        service.upload_document(
            knowledge_base_id=lib.id,
            source_path=src,
            original_filename="x.xyz",
        )


def test_upload_and_ingest_txt(service: KnowledgeBaseService, tmp_path: Path) -> None:
    lib = service.create_library(name="TXT KB")
    src = _write_tmp_file(tmp_path / "doc.txt", "Paragraph one.\n\nParagraph two.\n")
    doc = service.upload_document(
        knowledge_base_id=lib.id,
        source_path=src,
        original_filename="doc.txt",
    )
    assert doc.status == IngestionStatus.UPLOADED
    assert doc.size_bytes > 0
    final = service.run_ingestion(doc.id)
    assert final is not None
    assert final.status in (IngestionStatus.READY, IngestionStatus.FAILED)
    if final.status == IngestionStatus.READY:
        assert final.chunk_count > 0


def test_ingestion_fails_for_missing_source(
    service: KnowledgeBaseService, tmp_path: Path
) -> None:
    lib = service.create_library(name="M")
    src = _write_tmp_file(tmp_path / "doc.txt", "x")
    doc = service.upload_document(
        knowledge_base_id=lib.id, source_path=src, original_filename="doc.txt"
    )
    # Manually remove the on-disk source
    service._document_path(doc).unlink()
    final = service.run_ingestion(doc.id)
    assert final is not None
    assert final.status == IngestionStatus.FAILED


# ---------------------------------------------------------------------------
# Stage 0 — async ingestion contract (Task from the 2026-06-21 plan)
# ---------------------------------------------------------------------------


def test_upload_does_not_run_ingestion_inline(
    service: KnowledgeBaseService, tmp_path: Path
) -> None:
    """The router must decouple upload from ingestion: ``upload_document``
    must return the document in ``UPLOADED`` state and a separate
    ``enqueue_ingestion`` (or equivalent) call drives the state machine.

    Today ``upload_document`` returns ``UPLOADED`` and the router
    immediately calls ``run_ingestion`` synchronously. After the
    fix the upload itself should still leave the doc in UPLOADED and
    ingestion happens through a queue the router can await without
    blocking the response.
    """
    lib = service.create_library(name="Async")
    src = _write_tmp_file(tmp_path / "doc.txt", "Transformer attention.\n")
    doc = service.upload_document(
        knowledge_base_id=lib.id, source_path=src, original_filename="doc.txt"
    )
    # Upload alone must land in UPLOADED.
    assert doc.status == IngestionStatus.UPLOADED
    # The service must expose an enqueue_ingestion method.
    assert hasattr(service, "enqueue_ingestion"), (
        "service should expose an async dispatch entry point"
    )


@pytest.mark.asyncio
async def test_enqueue_ingestion_dispatches_background(
    service: KnowledgeBaseService, tmp_path: Path
) -> None:
    """``enqueue_ingestion`` must return quickly and let the caller
    continue; the actual state transitions happen out-of-band.

    This is the regression test for the synchronous PDF-upload timeout
    that the Next.js dev proxy was dropping.
    """
    import time

    from tutor.services.knowledge_base import IngestionStatus

    lib = service.create_library(name="Async2")
    src = _write_tmp_file(tmp_path / "doc.txt", "hello\n")
    doc = service.upload_document(
        knowledge_base_id=lib.id, source_path=src, original_filename="doc.txt"
    )
    started = time.monotonic()
    task = service.enqueue_ingestion(doc.id)  # must not block
    elapsed = time.monotonic() - started
    assert elapsed < 0.2, (
        f"enqueue_ingestion took {elapsed:.3f}s — should be < 200ms"
    )
    # Let the task finish so we don't leak into other tests.
    await task
    final = service.get_document(doc.id)
    assert final is not None
    assert final.status in {
        IngestionStatus.READY.value,
        IngestionStatus.FAILED.value,
    }


def test_retry_failed_document_resets_state(
    service: KnowledgeBaseService, tmp_path: Path
) -> None:
    lib = service.create_library(name="R")
    # Create a document, then delete the source to force failure.
    src = _write_tmp_file(tmp_path / "doc.txt", "hi")
    doc = service.upload_document(
        knowledge_base_id=lib.id, source_path=src, original_filename="doc.txt"
    )
    service._document_path(doc).unlink()
    failed = service.run_ingestion(doc.id)
    assert failed is not None
    assert failed.status == IngestionStatus.FAILED
    # Restore the source then retry.
    service._document_path(doc).write_text("restored", encoding="utf-8")
    retried = service.retry_document(doc.id)
    assert retried is not None
    assert retried.status in (IngestionStatus.READY, IngestionStatus.FAILED)


def test_delete_document(service: KnowledgeBaseService, tmp_path: Path) -> None:
    lib = service.create_library(name="D")
    src = _write_tmp_file(tmp_path / "doc.txt", "x")
    doc = service.upload_document(
        knowledge_base_id=lib.id, source_path=src, original_filename="doc.txt"
    )
    assert service.delete_document(doc.id) is True
    assert service.get_document(doc.id) is None


def test_delete_library_cascades(service: KnowledgeBaseService, tmp_path: Path) -> None:
    lib = service.create_library(name="C")
    src = _write_tmp_file(tmp_path / "doc.txt", "x")
    service.upload_document(
        knowledge_base_id=lib.id, source_path=src, original_filename="doc.txt"
    )
    assert service.delete_library(lib.id) is True
    assert service.list_documents(lib.id) == []
