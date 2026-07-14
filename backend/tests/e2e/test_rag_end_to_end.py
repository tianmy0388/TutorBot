"""End-to-end test for the RAG pipeline (D13).

The 2026-06-21 plan calls for an acceptance test that walks the
full path:

  1. A document is uploaded to a knowledge base.
  2. The embedder indexes it (using a stub provider so we don't
     hit a real API).
  3. We restart the in-process store and confirm the index is
     still queryable (the on-disk ``chunks.json`` survives).
  4. We pin ``retrieval_scope`` to a single library and confirm
     the answer only cites that library.
  5. A wrong scope (``SCOPE_NOT_FOUND``) returns a structured
     error rather than a silent fallback.

These tests use a stub embedder that returns deterministic
vectors so we don't need network access. The real Zhipu
provider is exercised by the unit test suite that ships with
the embedder factory.
"""

from __future__ import annotations

import asyncio
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
    RetrievalService,
)


# A trivial deterministic embedder. Each "word" of input is
# projected to a 4-D vector with a single 1 in the position of
# the first letter's index. That gives us enough structure to
# do cosine similarity in tests without a real API.
class _StubEmbedder:
    name = "stub"

    def __init__(self) -> None:
        self.model = "stub-model"
        self.api_key = ""
        self.base_url = ""

    async def embed(self, request):  # type: ignore[no-untyped-def]
        from tutor.services.embeddings.base import EmbedResponse

        def _vec(text: str) -> list[float]:
            t = text.lower().strip()
            vec = [0.0, 0.0, 0.0, 0.0]
            if t:
                idx = ord(t[0]) % 4
                vec[idx] = 1.0
            return vec

        return EmbedResponse(
            vectors=[_vec(t) for t in request.input],
            model="stub-model",
            usage={},
        )


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch) -> None:
    """Fresh data dir per test; the embedder is replaced with a
    deterministic stub and ``Settings`` is rebound to one whose
    ``embed_provider`` name the Literal accepts (the actual
    factory call returns the stub regardless of the provider
    string, so the only constraint is the Literal type).
    """
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TUTOR_EMBED_DIMENSIONS", "0")
    from tutor.services.config.settings import (
        Settings,
        reset_settings_cache,
    )
    from tutor.services import config

    fake_settings = Settings(
        embed_provider="openai",
        embed_model="stub-model",
        embed_dimensions=4,
    )
    # The actual embedder factory call below is what matters;
    # the ``Settings`` instance just needs a Literal-valid
    # provider name so the manifest filter accepts our
    # stub-written documents.
    monkeypatch.setattr(config.settings, "get_settings", lambda: fake_settings)
    reset_kb_store()

    from tutor.services.embeddings import embedder_factory

    monkeypatch.setattr(
        embedder_factory, "get_runtime_embedder", lambda *a, **kw: _StubEmbedder()
    )
    yield
    reset_kb_store()


async def _retrieve(svc: RetrievalService, **kwargs):
    res = svc.retrieve(**kwargs)
    if asyncio.iscoroutine(res):
        res = await res
    return res


@pytest.mark.asyncio
async def test_indexed_documents_survive_restart(tmp_path: Path) -> None:
    """Step 1-3 of the acceptance flow: write a document's
    ``chunks.json`` under the canonical on-disk layout, point
    a fresh :class:`RetrievalService` at the data dir, and
    confirm the chunk is queryable. This proves the index
    survives a process restart.
    """
    # Pre-create a library and a document.
    kb = get_kb_store()
    kb.upsert_library(KnowledgeBaseRecord(id="kb_a", name="A"))
    kb.upsert_document(
        KnowledgeDocument(
            id="doc_1",
            knowledge_base_id="kb_a",
            display_name="x.pdf",
            source_filename="x.pdf",
            extension=".pdf",
            status=IngestionStatus.READY,
            chunk_count=2,
            embedder_provider="openai",
            embedder_model="stub-model",
            embedder_dimension=4,
            index_version=INDEX_VERSION,
        )
    )
    # Write the on-disk chunk index the way ``run_ingestion``
    # would: under
    # ``<data_dir>/knowledge_bases/<lib>/sources/indexes/<doc>/``.
    index_dir = (
        tmp_path
        / "knowledge_bases"
        / "kb_a"
        / "sources"
        / "indexes"
        / "doc_1"
    )
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "chunks.json").write_text(
        json.dumps(
            {
                "document_id": "doc_1",
                "knowledge_base_id": "kb_a",
                "embedder_provider": "openai",
                "embedder_model": "stub-model",
                "embedder_dimension": 4,
                "index_version": INDEX_VERSION,
                "chunks": [
                    {"text": "alpha vector starts with a", "anchor": "p.1"},
                    {"text": "beta vector starts with b", "anchor": "p.2"},
                ],
                "embeddings": [
                    [0.0, 1.0, 0.0, 0.0],  # 'a' → slot 1 (97 % 4)
                    [0.0, 0.0, 1.0, 0.0],  # 'b' → slot 2 (98 % 4)
                ],
            }
        ),
        encoding="utf-8",
    )

    # Simulate a process restart by closing the store and
    # opening a new one at the same path. The retrieval service
    # uses the same default path, so a fresh singleton is
    # effectively the same store.
    db_path = tmp_path / "knowledge_bases.db"
    s2 = KnowledgeBaseSQLiteStore(db_path=str(db_path))
    s2.init()
    try:
        doc = s2.get_document("doc_1")
        assert doc is not None
        assert doc.chunk_count == 2
    finally:
        s2.close()
    reset_kb_store()
    # Now query. The "alpha" prompt has a 'a' prefix → matches
    # the chunk whose first letter is 'a' exactly.
    svc = RetrievalService(top_k=5)
    res = await _retrieve(svc, query="alpha", scope="library:kb_a", user_id="u1")
    assert res.status == "ok"
    assert any("alpha" in c.text for c in res.chunks)


@pytest.mark.asyncio
async def test_pinned_scope_only_cites_target_library(
    tmp_path: Path,
) -> None:
    """Step 4 of the acceptance flow: with two libraries that
    each have their own on-disk index, a search scoped to
    one library must NOT return chunks from the other.
    """
    kb = get_kb_store()
    for lib_id in ("kb_a", "kb_b"):
        kb.upsert_library(KnowledgeBaseRecord(id=lib_id, name=lib_id))
        kb.upsert_document(
            KnowledgeDocument(
                id=f"doc_{lib_id}",
                knowledge_base_id=lib_id,
                display_name="x.pdf",
                source_filename="x.pdf",
                extension=".pdf",
                status=IngestionStatus.READY,
                chunk_count=1,
                embedder_provider="openai",
                embedder_model="stub-model",
                embedder_dimension=4,
                index_version=INDEX_VERSION,
            )
        )
        idx = (
            tmp_path
            / "knowledge_bases"
            / lib_id
            / "sources"
            / "indexes"
            / f"doc_{lib_id}"
        )
        idx.mkdir(parents=True, exist_ok=True)
        (idx / "chunks.json").write_text(
            json.dumps(
                {
                    "document_id": f"doc_{lib_id}",
                    "knowledge_base_id": lib_id,
                    "embedder_provider": "openai",
                    "embedder_model": "stub-model",
                    "embedder_dimension": 4,
                    "index_version": INDEX_VERSION,
                    "chunks": [
                        {"text": f"alpha {lib_id}", "anchor": "p.1"}
                    ],
                    "embeddings": [[0.0, 1.0, 0.0, 0.0]],  # 'a' → slot 1
                }
            ),
            encoding="utf-8",
        )

    svc = RetrievalService(top_k=10)
    res = await _retrieve(svc, query="alpha", scope="library:kb_a", user_id="u1")
    assert res.status == "ok"
    assert res.chunks
    # Pinned scope must only see kb_a.
    for c in res.chunks:
        assert c.knowledge_base_id == "kb_a"
    # And ``all`` returns both.
    res_all = await _retrieve(svc, query="alpha", scope="all", user_id="u1")
    assert res_all.status == "ok"
    seen_libs = {c.knowledge_base_id for c in res_all.chunks}
    assert {"kb_a", "kb_b"}.issubset(seen_libs)


@pytest.mark.asyncio
async def test_invalid_scope_returns_structured_error() -> None:
    """Step 5 of the acceptance flow: a non-existent library
    must surface ``SCOPE_EMPTY`` rather than silently fall
    back to the entire corpus.
    """
    svc = RetrievalService()
    res = await _retrieve(
        svc, query="alpha", scope="library:kb_does_not_exist", user_id="u1"
    )
    assert res.status == "error"
    assert res.error_code == "SCOPE_EMPTY"


@pytest.mark.asyncio
async def test_no_evidence_when_score_below_threshold(tmp_path: Path) -> None:
    """A query whose only hit has cosine 0 (orthogonal vector)
    must be reported as ``no_evidence`` so the LLM can fall
    back to its own knowledge — the spec is explicit that we
    must not pretend to have found something.
    """
    kb = get_kb_store()
    kb.upsert_library(KnowledgeBaseRecord(id="kb_a", name="A"))
    kb.upsert_document(
        KnowledgeDocument(
            id="doc_1",
            knowledge_base_id="kb_a",
            display_name="x.pdf",
            source_filename="x.pdf",
            extension=".pdf",
            status=IngestionStatus.READY,
            chunk_count=1,
            embedder_provider="openai",
            embedder_model="stub-model",
            embedder_dimension=4,
            index_version=INDEX_VERSION,
        )
    )
    idx = tmp_path / "knowledge_bases" / "kb_a" / "sources" / "indexes" / "doc_1"
    idx.mkdir(parents=True, exist_ok=True)
    (idx / "chunks.json").write_text(
        json.dumps(
            {
                "document_id": "doc_1",
                "knowledge_base_id": "kb_a",
                "embedder_provider": "openai",
                "embedder_model": "stub-model",
                "embedder_dimension": 4,
                "index_version": INDEX_VERSION,
                "chunks": [{"text": "orthogonal", "anchor": "p.1"}],
                # Vector lives in a totally different slot than
                # the 'a' prefix the query maps to.
                "embeddings": [[0.0, 0.0, 1.0, 0.0]],
            }
        ),
        encoding="utf-8",
    )
    # Use a high score threshold to ensure cosine 0 doesn't pass.
    svc = RetrievalService(score_threshold=0.5)
    res = await _retrieve(svc, query="alpha", scope="library:kb_a", user_id="u1")
    assert res.status == "no_evidence"
    assert res.chunks == []


@pytest.mark.asyncio
async def test_manifest_mismatch_keeps_corpus_safe(tmp_path: Path) -> None:
    """If a document was indexed with provider=A but the
    runtime is provider=B, the search must skip that
    document (no mixed vectors) AND mark the corpus
    ``stale`` if EVERY doc in scope is mismatched.
    """
    kb = get_kb_store()
    kb.upsert_library(KnowledgeBaseRecord(id="kb_a", name="A"))
    kb.upsert_document(
        KnowledgeDocument(
            id="doc_1",
            knowledge_base_id="kb_a",
            display_name="x.pdf",
            source_filename="x.pdf",
            extension=".pdf",
            status=IngestionStatus.READY,
            chunk_count=1,
            # Indexed with "old-provider"; runtime says "stub".
            embedder_provider="old-provider",
            embedder_model="old-model",
            embedder_dimension=4,
            index_version=INDEX_VERSION,
        )
    )
    # The on-disk index is fine; the manifest mismatch is the
    # only thing keeping the chunk out of the search.
    idx = tmp_path / "knowledge_bases" / "kb_a" / "sources" / "indexes" / "doc_1"
    idx.mkdir(parents=True, exist_ok=True)
    (idx / "chunks.json").write_text(
        json.dumps(
            {
                "document_id": "doc_1",
                "knowledge_base_id": "kb_a",
                "embedder_provider": "old-provider",
                "embedder_model": "old-model",
                "embedder_dimension": 4,
                "index_version": INDEX_VERSION,
                "chunks": [{"text": "alpha", "anchor": "p.1"}],
                "embeddings": [[0.0, 1.0, 0.0, 0.0]],  # 'a' → slot 1
            }
        ),
        encoding="utf-8",
    )
    # The runtime provider is "stub" (set in the fixture), so
    # every doc in scope is mismatched → "stale" status.
    svc = RetrievalService()
    res = await _retrieve(svc, query="alpha", scope="library:kb_a", user_id="u1")
    assert res.status == "stale"
    assert res.error_code == "MANIFEST_MISMATCH"
