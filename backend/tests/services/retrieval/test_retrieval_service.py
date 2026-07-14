"""Tests for :class:`tutor.services.retrieval.service.RetrievalService`.

These tests stub the embedder at the factory boundary so the service
can be exercised without an actual embedding API. The focus is on
the 2026-06-21 plan behaviours:

  * scope parsing (all / course / library / invalid)
  * "no evidence" path
  * stale-manifest path (reindex_required)
  * top-k ordering
  * result payload carries knowledge base + document + anchor + score
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from tutor.services.knowledge_base.schema import (
    IngestionStatus,
    KnowledgeBaseRecord,
    KnowledgeDocument,
)
from tutor.services.knowledge_base.sqlite_store import (
    INDEX_VERSION,
    KnowledgeBaseSQLiteStore,
    get_kb_store,
    reset_kb_store,
)
from tutor.services.retrieval import (
    RetrievalScope,
    RetrievalService,
    parse_scope,
)


class _StubEmbedder:
    """Deterministic embedder that maps text to a 2-D vector.

    The vectors are chosen so we can reason about similarity
    without an actual embedding model — "alpha" → (1, 0), "beta"
    → (0, 1), and so on. This lets us test the cosine top-K
    ordering precisely.
    """

    name = "stub"

    def __init__(self) -> None:
        self.model = "stub-model"
        self.api_key = ""
        self.base_url = ""

    async def embed(self, request):  # type: ignore[no-untyped-def]
        from tutor.services.embeddings.base import EmbedResponse

        def _vec(text: str) -> list[float]:
            t = text.lower()
            if "alpha" in t:
                return [1.0, 0.0]
            if "beta" in t:
                return [0.0, 1.0]
            if "gamma" in t:
                return [0.5, 0.5]
            return [0.1, 0.1]

        return EmbedResponse(
            vectors=[_vec(t) for t in request.input],
            model=self.model,
            usage={},
        )


@pytest.fixture
def stub_embedder(monkeypatch) -> None:
    """Stub the embedder factory and align the runtime provider name.

    The retrieval service's manifest match compares the document's
    ``embedder_provider`` against ``Settings.embed_provider``. We
    swap the settings singleton for a real one with ``embed_provider
    = "openai"`` (a string the Literal allows) AND monkeypatch
    ``get_settings`` so the service reads the same one. Then we
    re-label our stub-written documents with ``embedder_provider =
    "openai"`` so the manifest filter passes. The literal "openai"
    in the test is purely a name; the actual embedder is still the
    in-process stub.
    """
    from tutor.services.embeddings import embedder_factory

    def _factory(*args: Any, **kwargs: Any) -> _StubEmbedder:
        return _StubEmbedder()

    monkeypatch.setattr(
        embedder_factory, "get_runtime_embedder", _factory
    )
    # Replace the settings singleton with one whose provider name
    # the Literal accepts AND whose dimensions match the stub
    # vectors (2). We don't write to ``.env``; the replacement is
    # process-local.
    from tutor.services.config.settings import Settings
    from tutor.services import config

    fake_settings = Settings(
        embed_provider="openai", embed_model="stub-model", embed_dimensions=2
    )
    monkeypatch.setattr(config.settings, "get_settings", lambda: fake_settings)


@pytest.fixture
def fresh(tmp_path: Path, monkeypatch) -> KnowledgeBaseSQLiteStore:
    """A fresh KB store under tmp_path, set as the process-wide singleton.

    We use the *default* path (``<data_dir>/knowledge_bases.db``) so
    that the RetrievalService's internal ``get_kb_store()`` call
    resolves to the same store the test populated. Otherwise the
    test would write to one DB and the service would read from a
    fresh, empty one.

    We also force ``TUTOR_EMBED_DIMENSIONS=0`` so the
    project-root ``.env`` (which sets ``2048``) doesn't poison
    the test's stub vectors (which are 2-D).
    """
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TUTOR_EMBED_DIMENSIONS", "0")
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    reset_kb_store()
    s = KnowledgeBaseSQLiteStore()  # default path
    s.init()
    yield s
    s.close()
    reset_kb_store()


def _write_index(
    tmp_path: Path,
    lib_id: str,
    doc_id: str,
    chunks: list[dict[str, str]],
    embeddings: list[list[float]],
    *,
    provider: str = "stub",
    dimension: int = 2,
) -> None:
    """Helper that mirrors the on-disk layout ``run_ingestion``
    would write: a ``chunks.json`` per document under
    ``<data_dir>/knowledge_bases/<lib>/sources/indexes/<doc>/``.
    """
    index_dir = (
        tmp_path
        / "knowledge_bases"
        / lib_id
        / "sources"
        / "indexes"
        / doc_id
    )
    index_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "document_id": doc_id,
        "knowledge_base_id": lib_id,
        "embedder_provider": provider,
        "embedder_dimension": dimension,
        "index_version": INDEX_VERSION,
        "chunks": chunks,
        "embeddings": embeddings,
    }
    (index_dir / "chunks.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_parse_scope_all() -> None:
    assert parse_scope(None).kind == "all"
    assert parse_scope("").kind == "all"
    assert parse_scope("all").kind == "all"


def test_parse_scope_course() -> None:
    s = parse_scope("course:course_x")
    assert s.kind == "course"
    assert s.target_id == "course_x"


def test_parse_scope_library() -> None:
    s = parse_scope("library:kb_x")
    assert s.kind == "library"
    assert s.target_id == "kb_x"


def test_parse_scope_invalid() -> None:
    assert parse_scope("wat").kind == "none"


def _retrieve(svc, **kwargs):
    """Call ``svc.retrieve`` and bridge async→sync for tests.

    The service is now ``async def`` (D7 fix). The test code is
    sync; we drive the coroutine to completion with
    ``asyncio.run`` here so the test bodies stay readable.
    """
    res = svc.retrieve(**kwargs)
    if inspect.iscoroutine(res):
        res = asyncio.run(res)
    return res


def test_retrieve_returns_ok_with_provenance(
    fresh: KnowledgeBaseSQLiteStore, tmp_path: Path, stub_embedder: None
) -> None:
    fresh.upsert_library(
        KnowledgeBaseRecord(
            id="kb_a", name="A", embedding_model="stub-model"
        )
    )
    fresh.upsert_document(
        KnowledgeDocument(
            id="doc_1",
            knowledge_base_id="kb_a",
            display_name="x.pdf",
            source_filename="x.pdf",
            extension=".pdf",
            status=IngestionStatus.READY,
            chunk_count=2,
            embedder_provider="openai",  # must match runtime config
            embedder_dimension=2,
            index_version=INDEX_VERSION,
        )
    )
    _write_index(
        tmp_path,
        "kb_a",
        "doc_1",
        chunks=[
            {"text": "alpha content", "anchor": "p.1"},
            {"text": "beta content", "anchor": "p.2"},
        ],
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
        provider="openai",
    )

    svc = RetrievalService()
    res = _retrieve(svc, query="alpha", scope="library:kb_a", user_id="u1")
    assert res.status == "ok"
    assert len(res.chunks) == 1
    chunk = res.chunks[0]
    assert chunk.knowledge_base_id == "kb_a"
    assert chunk.knowledge_base_name == "A"
    assert chunk.document_id == "doc_1"
    assert chunk.anchor == "p.1"
    assert chunk.score == pytest.approx(1.0)


def test_retrieve_no_evidence(
    fresh: KnowledgeBaseSQLiteStore, tmp_path: Path, stub_embedder: None
) -> None:
    fresh.upsert_library(KnowledgeBaseRecord(id="kb_a", name="A"))
    fresh.upsert_document(
        KnowledgeDocument(
            id="doc_1",
            knowledge_base_id="kb_a",
            display_name="x.pdf",
            source_filename="x.pdf",
            extension=".pdf",
            status=IngestionStatus.READY,
            chunk_count=1,
            embedder_provider="openai",
            embedder_dimension=2,
            index_version=INDEX_VERSION,
        )
    )
    _write_index(
        tmp_path,
        "kb_a",
        "doc_1",
        chunks=[{"text": "beta content", "anchor": "p.1"}],
        embeddings=[[0.0, 1.0]],
        provider="openai",
    )

    svc = RetrievalService(score_threshold=0.99)
    res = _retrieve(svc, query="alpha", scope="library:kb_a", user_id="u1")
    assert res.status == "no_evidence"
    assert res.chunks == []


def test_retrieve_scope_not_found(
    fresh: KnowledgeBaseSQLiteStore, stub_embedder: None
) -> None:
    svc = RetrievalService()
    res = _retrieve(svc, 
        query="alpha", scope="library:kb_does_not_exist", user_id="u1"
    )
    assert res.status == "error"
    assert res.error_code == "SCOPE_EMPTY"


def test_retrieve_stale_when_every_doc_reindex_required(
    fresh: KnowledgeBaseSQLiteStore, tmp_path: Path, stub_embedder: None
) -> None:
    fresh.upsert_library(KnowledgeBaseRecord(id="kb_a", name="A"))
    fresh.upsert_document(
        KnowledgeDocument(
            id="doc_1",
            knowledge_base_id="kb_a",
            display_name="x.pdf",
            source_filename="x.pdf",
            extension=".pdf",
            status=IngestionStatus.READY,
            chunk_count=1,
            embedder_provider="openai",
            embedder_dimension=2,
            index_version=INDEX_VERSION,
            reindex_required=True,
        )
    )
    _write_index(
        tmp_path,
        "kb_a",
        "doc_1",
        chunks=[{"text": "alpha content", "anchor": "p.1"}],
        embeddings=[[1.0, 0.0]],
        provider="openai",
    )
    svc = RetrievalService()
    res = _retrieve(svc, query="alpha", scope="library:kb_a", user_id="u1")
    assert res.status == "stale"
    assert res.error_code == "REINDEX_REQUIRED"


def test_retrieve_top_k_respects_limit(
    fresh: KnowledgeBaseSQLiteStore, tmp_path: Path, stub_embedder: None
) -> None:
    fresh.upsert_library(KnowledgeBaseRecord(id="kb_a", name="A"))
    fresh.upsert_document(
        KnowledgeDocument(
            id="doc_1",
            knowledge_base_id="kb_a",
            display_name="x.pdf",
            source_filename="x.pdf",
            extension=".pdf",
            status=IngestionStatus.READY,
            chunk_count=3,
            embedder_provider="openai",
            embedder_dimension=2,
            index_version=INDEX_VERSION,
        )
    )
    _write_index(
        tmp_path,
        "kb_a",
        "doc_1",
        chunks=[
            {"text": "alpha one", "anchor": "p.1"},
            {"text": "alpha two", "anchor": "p.2"},
            {"text": "alpha three", "anchor": "p.3"},
        ],
        embeddings=[[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
        provider="openai",
    )
    svc = RetrievalService(top_k=2)
    res = _retrieve(svc, query="alpha", scope="library:kb_a", user_id="u1")
    assert res.status == "ok"
    assert len(res.chunks) == 2


def test_retrieve_invalid_scope_expression(
    fresh: KnowledgeBaseSQLiteStore, stub_embedder: None
) -> None:
    svc = RetrievalService()
    res = _retrieve(svc, query="alpha", scope="garbage", user_id="u1")
    assert res.status == "error"
    assert res.error_code == "INVALID_SCOPE"


def test_retrieve_course_scope(
    fresh: KnowledgeBaseSQLiteStore,
    tmp_path: Path,
    stub_embedder: None,
    monkeypatch,
) -> None:
    from tutor.services.courses import (
        CourseService,
        CourseStore,
        reset_course_service,
        reset_course_store,
    )
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    # We don't reset settings here — the stub_embedder fixture has
    # already replaced the cached ``get_settings`` with a lambda, so
    # ``reset_settings_cache()`` is a no-op. We just open a fresh
    # CourseStore on the default path the same way the KB fixture
    # does, so the CourseService sees the library we just created.
    reset_course_store()
    reset_course_service()
    cs = CourseStore()
    cs.init()
    course_svc = CourseService(store=cs, kb_store=fresh)
    from tutor.services.courses.schema import Course

    course_svc.store.upsert_course(Course(id="course_x", name="X"))
    fresh.upsert_library(
        KnowledgeBaseRecord(id="kb_a", name="A", course_id="course_x")
    )
    fresh.upsert_document(
        KnowledgeDocument(
            id="doc_1",
            knowledge_base_id="kb_a",
            display_name="x.pdf",
            source_filename="x.pdf",
            extension=".pdf",
            status=IngestionStatus.READY,
            chunk_count=1,
            embedder_provider="openai",
            embedder_dimension=2,
            index_version=INDEX_VERSION,
        )
    )
    _write_index(
        tmp_path,
        "kb_a",
        "doc_1",
        chunks=[{"text": "alpha", "anchor": "p.1"}],
        embeddings=[[1.0, 0.0]],
        provider="openai",
    )
    svc = RetrievalService()
    res = _retrieve(svc, query="alpha", scope="course:course_x", user_id="u1")
    assert res.status == "ok"
    cs.close()
    reset_course_store()
    reset_course_service()
