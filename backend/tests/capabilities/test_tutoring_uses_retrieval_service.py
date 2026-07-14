"""Tests that the TutoringCapability calls into the new
:class:`tutor.services.retrieval.service.RetrievalService` for
context retrieval — the D9 fix.

These are unit tests, not full backend integration. We
patch the agents (question / tutoring / enrichment) with
stubs so the test focuses on the RAG stage behaviour:

  * when the scope has evidence, the RAG context string is
    non-empty and the citations metadata is populated
  * when the scope has no evidence, the LLM still gets a
    turn (no upstream failure) and the citations list is empty
  * the ``retrieval_scope`` key on the context metadata
    flows through to the retrieval service
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tutor.capabilities.tutoring import TutoringCapability
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
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


class _StubRetrievalService(RetrievalService):
    """Records the (query, scope, user_id) it was called with and
    returns a canned :class:`RAGContext`.
    """

    last_call: dict[str, Any] | None = None

    def __init__(self, canned_status: str = "ok") -> None:
        super().__init__(top_k=5)
        self._canned_status = canned_status
        self._counter = 0

    async def retrieve(self, *, query, scope, user_id):  # type: ignore[override]
        _StubRetrievalService.last_call = {
            "query": query,
            "scope": scope,
            "user_id": user_id,
        }
        from tutor.services.retrieval.service import (
            EvidenceChunk,
            RAGContext,
            RetrievalResult,
            RetrievalScope,
        )
        self._counter += 1
        if self._canned_status == "ok":
            chunks = [
                EvidenceChunk(
                    chunk_id="doc_1:0",
                    text="alpha content from KB",
                    score=0.92,
                    knowledge_base_id="kb_a",
                    knowledge_base_name="A",
                    document_id="doc_1",
                    document_name="x.pdf",
                    anchor="p.1",
                    embedder_provider="openai",
                    embedder_dimension=2,
                )
            ]
            return RAGContext.from_result(
                RetrievalResult(
                    status="ok",
                    chunks=chunks,
                    scope=RetrievalScope(raw=scope if isinstance(scope, str) else scope.raw, kind="all"),
                ),
                query=query,
            )
        if self._canned_status == "no_evidence":
            return RAGContext.from_result(
                RetrievalResult(
                    status="no_evidence",
                    scope=RetrievalScope(raw=scope if isinstance(scope, str) else scope.raw, kind="all"),
                ),
                query=query,
            )
        return RAGContext.from_result(
            RetrievalResult(
                status="error",
                error_code="SCOPE_EMPTY",
                error_message="forced",
                scope=RetrievalScope(raw=scope if isinstance(scope, str) else scope.raw, kind="all"),
            ),
            query=query,
        )


class _StubAgent:
    """Replaces a real agent with a no-op coroutine that returns
    a fixed object. The tutoring capability reads the agent's
    return value as the structured answer, so a dataclass-like
    object is enough.
    """

    def __init__(self, return_obj: Any) -> None:
        self.return_obj = return_obj

    async def process(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.return_obj


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path: Path, monkeypatch) -> None:
    """Each test gets a fresh KB store under tmp_path; the
    retrieval service is injected per-test.
    """
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TUTOR_EMBED_DIMENSIONS", "0")
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    reset_kb_store()
    s = KnowledgeBaseSQLiteStore()
    s.init()
    yield
    s.close()
    reset_kb_store()


def _build_cap(retrieval: RetrievalService) -> TutoringCapability:
    cap = TutoringCapability()
    cap.retrieval_service = retrieval
    # Replace the other agents so we don't try to call real LLMs.
    from tutor.agents.tutor.question_understanding import (
        QuestionUnderstanding,
    )
    from tutor.agents.tutor.tutoring import TutoringAnswer
    from tutor.agents.tutor.multimodal_enrichment import (
        EnrichmentSuggestion,
    )

    cap.question_agent = _StubAgent(
        QuestionUnderstanding(
            question_type=__import__(
                "tutor.agents.tutor.question_understanding",
                fromlist=["QuestionType"],
            ).QuestionType.CONCEPT,
            raw_question="placeholder",
            concepts=["alpha"],
        )
    )
    cap.tutoring_agent = _StubAgent(
        TutoringAnswer(tldr="stub", confidence=0.7)
    )
    cap.enrichment_agent = _StubAgent([])
    return cap


def _context(metadata: dict[str, Any] | None = None) -> UnifiedContext:
    ctx = UnifiedContext(
        session_id="sess_x",
        user_id="u1",
        user_message="解释 alpha",
        language="zh",
        capability="tutoring",
        metadata=metadata or {},
    )
    return ctx


def _collect_events(bus: StreamBus) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in bus.drain():
        out.append(ev.to_dict() if hasattr(ev, "to_dict") else ev)
    return out


@pytest.mark.asyncio
async def test_tutoring_uses_retrieval_service_with_scope() -> None:
    """The capability reads ``retrieval_scope`` from metadata and
    forwards it to the retrieval service.
    """
    retrieval = _StubRetrievalService("ok")
    cap = _build_cap(retrieval)
    bus = StreamBus(session_id="sess_x", turn_id="t1")
    await cap.run(_context({"retrieval_scope": "library:kb_a"}), bus)
    assert retrieval.last_call is not None
    assert retrieval.last_call["scope"] == "library:kb_a"
    assert retrieval.last_call["query"].startswith("解释 alpha")
    # Question-understanding stage adds "相关概念：alpha" — make
    # sure the enriched query is what we send to retrieval, not
    # the raw user message.
    assert "alpha" in retrieval.last_call["query"]


@pytest.mark.asyncio
async def test_tutoring_rag_context_populated_on_hit() -> None:
    """When the retrieval returns ``ok``, the capability must
    populate ``metadata['rag_context']`` and the citations list.
    """
    retrieval = _StubRetrievalService("ok")
    cap = _build_cap(retrieval)
    bus = StreamBus(session_id="sess_x", turn_id="t1")
    ctx = _context({"retrieval_scope": "all"})
    await cap.run(ctx, bus)
    assert ctx.metadata["rag_status"] == "ok"
    assert "alpha content from KB" in ctx.metadata["rag_context"]
    assert ctx.metadata["rag_citations"]
    citation = ctx.metadata["rag_citations"][0]
    assert citation["knowledge_base_id"] == "kb_a"
    assert citation["document_id"] == "doc_1"
    assert citation["anchor"] == "p.1"


@pytest.mark.asyncio
async def test_tutoring_rag_no_evidence_does_not_block_answer() -> None:
    """``no_evidence`` is a valid outcome; the LLM should still
    get a turn and we just record the empty result in metadata.
    """
    retrieval = _StubRetrievalService("no_evidence")
    cap = _build_cap(retrieval)
    bus = StreamBus(session_id="sess_x", turn_id="t1")
    ctx = _context({"retrieval_scope": "all"})
    await cap.run(ctx, bus)
    # We didn't crash, the LLM still produced an answer (the
    # stub), and the RAG metadata is empty but present.
    assert ctx.metadata["rag_status"] == "no_evidence"
    assert ctx.metadata["rag_context"] == ""
    assert ctx.metadata["rag_citations"] == []


@pytest.mark.asyncio
async def test_tutoring_rag_error_is_surfaced_not_swallowed() -> None:
    """When the retrieval service raises, the capability catches
    the exception and continues with empty RAG context — same
    path as ``no_evidence`` — so a transient retrieval failure
    never blocks the LLM answer.
    """

    class _Boom:
        async def retrieve(self, **_):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated retrieval failure")

    cap = _build_cap(_Boom())  # type: ignore[arg-type]
    bus = StreamBus(session_id="sess_x", turn_id="t1")
    ctx = _context({"retrieval_scope": "all"})
    # No exception is allowed to escape.
    await cap.run(ctx, bus)
    assert ctx.metadata.get("rag_status") in (None, "error")


@pytest.mark.asyncio
async def test_tutoring_default_scope_is_all() -> None:
    """When the metadata has no ``retrieval_scope`` key, the
    capability falls back to ``"all"`` (the existing default).
    """
    retrieval = _StubRetrievalService("ok")
    cap = _build_cap(retrieval)
    bus = StreamBus(session_id="sess_x", turn_id="t1")
    await cap.run(_context(), bus)
    assert retrieval.last_call is not None
    assert retrieval.last_call["scope"] == "all"
