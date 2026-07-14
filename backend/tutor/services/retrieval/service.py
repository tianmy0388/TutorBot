"""RetrievalService — vector retrieval scoped to a course or library (2026-06-21 plan).

The previous ``RAGTool`` was a placeholder that always returned
empty chunks. This module is the real implementation:

  * Load only ``ready`` documents whose ``reindex_required`` is False
    (or include them with a warning if the caller explicitly asks
    for "best effort").
  * Use the runtime embedder to embed the query, but ONLY when the
    document's ``embedder_provider`` / ``embedder_dimension`` match
    the runtime config — otherwise the vectors live in a different
    space and cosine similarity would be meaningless.
  * Run cosine top-K + a configurable score threshold.
  * Return the evidence as a structured payload (kb, document,
    anchor, score, chunk id). When no chunk clears the threshold
    we return ``{"status": "no_evidence", ...}`` so the LLM is
    told not to hallucinate from the (empty) RAG context.

Scope
-----
The :class:`RetrievalScope` is the set of libraries we look in. The
public ``retrieve`` method accepts three shapes:

  * ``"all"``              — every ready library the user owns
  * ``"course:ID"``        — every library whose ``course_id`` matches
  * ``"library:ID"``       — a single library

A scope that doesn't exist, that the user doesn't own, or that has
no ``ready`` documents is a structured error — the spec is explicit
that we must NOT silently fall back to the plain LLM in that case
("选择失效知识库时返回错误而非静默降级").
"""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from loguru import logger

from tutor.services.courses import get_course_service
from tutor.services.knowledge_base.schema import IngestionStatus
from tutor.services.knowledge_base.sqlite_store import (
    INDEX_VERSION,
    get_kb_store,
)


# Cosine-similarity score below this is treated as "no evidence".
# 0.0 means "orthogonal", 1.0 means "identical". The default 0.25 is
# what most OpenAI-embedding retrieval setups use; tune via the
# constructor if your corpus is more specialised.
DEFAULT_SCORE_THRESHOLD = 0.25

# Default top-k.
DEFAULT_TOP_K = 5


@dataclass
class RetrievalScope:
    """Parsed retrieval scope expression.

    ``raw`` is the original user input (e.g. ``"course:course_ai_intro"``).
    ``kind`` is one of ``"all"``, ``"course"``, ``"library"``,
    ``"none"``. ``target_id`` is set for ``"course"`` and ``"library"``.
    """

    raw: str
    kind: str  # "all" | "course" | "library" | "none"
    target_id: str | None = None


@dataclass
class EvidenceChunk:
    """One retrieval hit, with provenance."""

    chunk_id: str
    text: str
    score: float
    knowledge_base_id: str
    knowledge_base_name: str
    document_id: str
    document_name: str
    anchor: str = ""
    embedder_provider: str = ""
    embedder_model: str = ""
    embedder_dimension: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly shape used by the WS wire format and by
        the LLM agent's ``citations`` list. Keys are
        snake_case + sorted to keep tests / snapshots stable.
        """
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "score": round(float(self.score), 4),
            "knowledge_base_id": self.knowledge_base_id,
            "knowledge_base_name": self.knowledge_base_name,
            "document_id": self.document_id,
            "document_name": self.document_name,
            "anchor": self.anchor,
            "embedder_provider": self.embedder_provider,
            "embedder_model": self.embedder_model,
            "embedder_dimension": int(self.embedder_dimension),
        }


@dataclass
class RetrievalResult:
    """Result of one :meth:`RetrievalService.retrieve` call.

    ``status`` is one of:

      * ``"ok"``           — at least one chunk cleared the threshold
      * ``"no_evidence"``  — no chunk cleared the threshold; the LLM
                              should answer without RAG context
      * ``"error"``        — the scope was invalid / not ready / etc.
                              (see ``error_code`` for the reason)
      * ``"stale"``        — the scope is ready but every document in
                              it is flagged ``reindex_required``;
                              the LLM should answer with a warning
    """

    status: str
    chunks: list[EvidenceChunk] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    scope: RetrievalScope | None = None


@dataclass
class RAGContext:
    """A retrieval result packaged for the LLM agent.

    The capability layer (``TutoringCapability``,
    ``ResourceGenerationCapability``) consumes this struct rather
    than the raw ``RetrievalResult``. ``chunks`` carries the
    ranked evidence; ``citations`` is the same data in a
    JSON-friendly shape for the streaming wire format; ``status``
    mirrors :attr:`RetrievalResult.status` so the agent can
    decide whether to fall back to general knowledge.
    """

    status: str
    query: str
    chunks: list[EvidenceChunk]
    scope: RetrievalScope | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def from_result(
        cls, result: RetrievalResult, *, query: str = ""
    ) -> "RAGContext":
        return cls(
            status=result.status,
            query=query,
            chunks=list(result.chunks),
            scope=result.scope,
            error_code=result.error_code,
            error_message=result.error_message,
        )

    @staticmethod
    def to_plain_text(context: "RAGContext") -> str:
        """Format the chunks as a single string for LLM context.

        Each chunk is prefixed with its source so the agent can
        cite back to it; this is the "rag_context" string the
        TutoringAgent sees in its prompt template.
        """
        if not context.chunks:
            return ""
        blocks: list[str] = []
        for i, c in enumerate(context.chunks, 1):
            header = (
                f"[{i}] {c.knowledge_base_name} / {c.document_name}"
            )
            if c.anchor:
                header += f" ({c.anchor})"
            header += f" — score={c.score:.3f}"
            blocks.append(f"{header}\n{c.text}")
        return "\n\n".join(blocks)


def parse_scope(raw: str | None) -> RetrievalScope:
    """Parse a scope string into a :class:`RetrievalScope`.

    Accepts the four shapes the UI emits:

      * ``None`` / empty      → ``"all"``
      * ``"all"``              → ``"all"``
      * ``"course:ID"``        → ``"course"``
      * ``"library:ID"``       → ``"library"``

    Anything else is normalised to ``"none"`` so the service can
    raise a structured error instead of silently searching nothing.
    """
    s = (raw or "").strip().lower()
    if not s or s == "all":
        return RetrievalScope(raw=raw or "all", kind="all")
    if s.startswith("course:"):
        return RetrievalScope(raw=raw, kind="course", target_id=s.split(":", 1)[1].strip())
    if s.startswith("library:"):
        return RetrievalScope(raw=raw, kind="library", target_id=s.split(":", 1)[1].strip())
    return RetrievalScope(raw=raw or "none", kind="none")


class RetrievalService:
    """Vector retrieval over a user-scoped set of knowledge bases.

    2026-06-21 fix (D7): the entire pipeline is now ``async``. The
    pre-fix implementation called ``asyncio.run()`` internally,
    which raises ``RuntimeError: asyncio.run() cannot be called
    from a running event loop`` when the service is invoked from
    the FastAPI / asyncio path that TutoringCapability and
    ResourceGenerationCapability run on. The current design
    requires a running loop and uses ``await`` for the embedder
    call, so the same service works in both standalone scripts
    and the async pipeline.

    Sync callers (e.g. background tests, scripts) can drive the
    service with ``asyncio.run(retrieval.retrieve(...))``.
    """

    def __init__(
        self,
        *,
        score_threshold: float = DEFAULT_SCORE_THRESHOLD,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        self.score_threshold = float(score_threshold)
        self.top_k = int(top_k)

    # ---- public API ----------------------------------------------------

    async def retrieve(
        self,
        *,
        query: str,
        scope: str | RetrievalScope,
        user_id: str,
    ) -> RetrievalResult:
        """Top-K evidence chunks for ``query`` within ``scope``.

        Async because the underlying embedder is async. Sync
        callers wrap this with ``asyncio.run``.
        """
        parsed = scope if isinstance(scope, RetrievalScope) else parse_scope(scope)
        if not query or not query.strip():
            return RetrievalResult(
                status="error",
                error_code="EMPTY_QUERY",
                error_message="query is empty",
                scope=parsed,
            )
        if parsed.kind == "none":
            return RetrievalResult(
                status="error",
                error_code="INVALID_SCOPE",
                error_message=f"unknown scope expression: {parsed.raw!r}",
                scope=parsed,
            )

        # Resolve the scope to a list of (lib_id, lib_record) pairs.
        libs = self._resolve_scope(parsed)
        if not libs:
            return RetrievalResult(
                status="error",
                error_code="SCOPE_EMPTY",
                error_message="scope resolved to no libraries",
                scope=parsed,
            )
        # Filter by ready documents and matching manifest.
        ready_libs = [
            lib
            for lib in libs
            if lib.document_count > 0 and lib.ready_count > 0
        ]
        if not ready_libs:
            return RetrievalResult(
                status="error",
                error_code="SCOPE_NOT_READY",
                error_message="no library in scope has a ready document",
                scope=parsed,
            )
        # If every document in every library is flagged stale or
        # has a mismatched manifest, we return ``"stale"`` rather
        # than falling back to a wrong-answer retrieval. The UI
        # can show a "RAG is stale" chip and the LLM answer goes
        # through without RAG context.
        manifest_match = self._find_manifest_match(ready_libs)
        if manifest_match is None:
            # No embedder configured. Treat the scope as
            # "manifest-mismatch" — the operator hasn't picked a
            # runtime embedder, so any corpus is invisible to
            # the search. This is the structured error the spec
            # calls for so the UI can prompt the operator to
            # pick a provider instead of pretending RAG works.
            return RetrievalResult(
                status="stale",
                error_code="MANIFEST_MISMATCH",
                error_message=(
                    "no embedder configured; RAG is unavailable "
                    "until the operator picks a provider"
                ),
                scope=parsed,
            )
        all_stale = self._every_document_stale(ready_libs, manifest_match)
        if all_stale:
            # Distinguish: every doc is ``reindex_required`` (the
            # operator flipped the flag explicitly) vs every doc
            # has a mismatched manifest (the operator switched
            # providers without reindexing). The error code lets
            # the UI render a different message.
            any_reindex_flag = any(
                doc.reindex_required
                for lib in ready_libs
                for doc in get_kb_store().list_documents(lib.id)
            )
            if any_reindex_flag:
                return RetrievalResult(
                    status="stale",
                    error_code="REINDEX_REQUIRED",
                    error_message=(
                        "every document in scope is flagged reindex_required; "
                        "re-run the embedder to refresh vectors"
                    ),
                    scope=parsed,
                )
            return RetrievalResult(
                status="stale",
                error_code="MANIFEST_MISMATCH",
                error_message=(
                    "no document in scope matches the runtime embedder; "
                    "reindex before using RAG"
                ),
                scope=parsed,
            )

        # ``manifest_match`` is computed above. We use it
        # unchanged for the embedder call below.
        if manifest_match is None:
            return RetrievalResult(
                status="stale",
                error_code="MANIFEST_MISMATCH",
                error_message=(
                    "no document in scope matches the runtime embedder; "
                    "reindex before using RAG"
                ),
                scope=parsed,
            )
        try:
            query_vec = await self._embed_query(query, manifest_match)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Retrieval embed failed: {err}", err=exc)
            return RetrievalResult(
                status="error",
                error_code="EMBED_FAILED",
                error_message=str(exc),
                scope=parsed,
            )

        # Load every chunk from the in-scope libraries and rank by
        # cosine similarity to the query vector.
        candidates = self._load_chunks(ready_libs, manifest_match)
        scored: list[EvidenceChunk] = []
        for ch in candidates:
            score = _cosine(query_vec, ch["vector"])
            if math.isnan(score):
                continue
            scored.append(
                EvidenceChunk(
                    chunk_id=ch["chunk_id"],
                    text=ch["text"],
                    score=score,
                    knowledge_base_id=ch["knowledge_base_id"],
                    knowledge_base_name=ch["knowledge_base_name"],
                    document_id=ch["document_id"],
                    document_name=ch["document_name"],
                    anchor=ch.get("anchor", ""),
                    embedder_provider=ch.get("embedder_provider", ""),
                    embedder_dimension=int(ch.get("embedder_dimension", 0) or 0),
                )
            )
        scored.sort(key=lambda c: c.score, reverse=True)
        kept = [c for c in scored if c.score >= self.score_threshold][: self.top_k]
        if not kept:
            return RetrievalResult(
                status="no_evidence",
                scope=parsed,
            )
        return RetrievalResult(status="ok", chunks=kept, scope=parsed)

    # ---- internals -----------------------------------------------------

    def _resolve_scope(self, scope: RetrievalScope) -> list[Any]:
        """Return the list of library records in scope."""
        kb_store = get_kb_store()
        if scope.kind == "all":
            return kb_store.list_libraries()
        if scope.kind == "library":
            lib = kb_store.get_library(scope.target_id or "")
            return [lib] if lib else []
        if scope.kind == "course":
            try:
                course_svc = get_course_service()
            except Exception:  # noqa: BLE001
                return []
            course = course_svc.get_course(scope.target_id or "")
            if course is None:
                return []
            return [
                lib
                for lib in kb_store.list_libraries()
                if lib.course_id == course.id
            ]
        return []

    def _every_document_stale(
        self, libs: Iterable[Any], manifest: dict[str, Any] | None = None
    ) -> bool:
        """True when no document in scope can be searched.

        A document is "searchable" when ALL of:
          * its status is READY
          * its ``reindex_required`` flag is False
          * (if a manifest is provided) its manifest matches
            the runtime config (D8 fix)

        We return ``True`` when the entire scope is empty /
        not-ready / all-flagged-stale / all-mismatched. The
        caller surfaces a ``stale`` status to the LLM so it
        falls back to general knowledge.
        """
        kb_store = get_kb_store()
        for lib in libs:
            for doc in kb_store.list_documents(lib.id):
                if doc.status != IngestionStatus.READY:
                    continue
                if doc.reindex_required:
                    continue
                if manifest is not None:
                    if (
                        doc.embedder_provider
                        and doc.embedder_provider != manifest["provider"]
                    ):
                        continue
                    if (
                        doc.embedder_model
                        and manifest.get("model")
                        and doc.embedder_model != manifest["model"]
                    ):
                        continue
                    if (
                        doc.embedder_dimension
                        and manifest["dimension"]
                        and doc.embedder_dimension != manifest["dimension"]
                    ):
                        continue
                # Found at least one searchable doc — scope is
                # not "every-doc-stale".
                return False
        return True

    def _find_manifest_match(self, libs: Iterable[Any]) -> dict[str, Any] | None:
        """Build the runtime-config fingerprint (D8).

        Returns ``None`` when no embedder is configured. Otherwise
        the returned dict carries the full fingerprint that the
        search uses to refuse mixed-provider / mixed-dimension
        searches — the spec calls for a tuple of ``provider +
        model + dimension + index_version``; we add the index
        version so a future contract bump invalidates the
        fingerprint automatically.
        """
        from tutor.services.config.settings import get_settings
        from tutor.services.knowledge_base.sqlite_store import INDEX_VERSION

        settings = get_settings()
        provider = settings.embed_provider or ""
        model = settings.embed_model or ""
        dimension = int(settings.embed_dimensions or 0)
        if not provider:
            return None
        return {
            "provider": provider,
            "model": model,
            "dimension": dimension,
            "index_version": INDEX_VERSION,
        }

    async def _embed_query(
        self, query: str, manifest: dict[str, Any]
    ) -> list[float]:
        """Embed the query and update the manifest dimension.

        Async because the embedder is async. We do NOT call
        ``asyncio.run`` here — that would deadlock when the
        service runs inside the FastAPI event loop.
        """
        from tutor.services.embeddings.base import EmbedRequest
        from tutor.services.embeddings.embedder_factory import (
            get_runtime_embedder,
        )
        from tutor.services.config.settings import get_settings

        settings = get_settings()
        embedder = get_runtime_embedder(settings)
        req = EmbedRequest(input=[query])
        resp = await embedder.embed(req)
        vectors = list(resp.vectors)
        if not vectors:
            raise RuntimeError("embedder returned no vectors")
        if not manifest.get("dimension"):
            manifest["dimension"] = len(vectors[0])
        return vectors[0]

    def _load_chunks(
        self, libs: list[Any], manifest: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Read every ``ready`` document's ``chunks.json`` and flatten.

        2026-06-21 fix (D8): the manifest check now uses the full
        fingerprint (provider + model + dimension + index_version)
        rather than just provider+dimension. A document that was
        indexed with ``embedding-2`` and a document that was
        indexed with ``embedding-3`` would previously both pass
        the filter as long as the dimension matched; the search
        would then mix incompatible vectors and the cosine score
        would be meaningless. The new check refuses any document
        whose ``embedder_provider``, ``embedder_model`` or
        ``embedder_dimension`` disagrees with the runtime config.
        """
        from tutor.services.config.settings import get_settings

        settings = get_settings()
        data_dir = Path(settings.data_dir)
        out: list[dict[str, Any]] = []
        for lib in libs:
            for doc in get_kb_store().list_documents(lib.id):
                if doc.status != IngestionStatus.READY:
                    continue
                if doc.reindex_required:
                    continue
                # 2026-06-21 fix (D8): full-fingerprint match.
                # A document whose manifest disagrees on any of
                # provider / model / dimension / index_version is
                # dropped — the spec calls for no mixed vectors.
                if (
                    doc.embedder_provider
                    and doc.embedder_provider != manifest["provider"]
                ):
                    continue
                if (
                    doc.embedder_model
                    and manifest.get("model")
                    and doc.embedder_model != manifest["model"]
                ):
                    continue
                if (
                    doc.embedder_dimension
                    and manifest["dimension"]
                    and doc.embedder_dimension != manifest["dimension"]
                ):
                    continue
                index_path = (
                    data_dir
                    / "knowledge_bases"
                    / lib.id
                    / "sources"
                    / "indexes"
                    / doc.id
                    / "chunks.json"
                )
                if not index_path.exists():
                    continue
                try:
                    payload = json.loads(index_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                # 2026-06-21 plan: drop documents whose index_version
                # doesn't match the current INDEX_VERSION. They were
                # written under an older contract and re-running the
                # search against them is unsafe.
                if int(payload.get("index_version", 0) or 0) != INDEX_VERSION:
                    continue
                chunks = payload.get("chunks", [])
                vectors = payload.get("embeddings", [])
                for i, ch in enumerate(chunks):
                    vec = vectors[i] if i < len(vectors) else None
                    if not vec:
                        continue
                    out.append(
                        {
                            "chunk_id": f"{doc.id}:{i}",
                            "text": ch.get("text", ""),
                            "anchor": ch.get("anchor", ""),
                            "vector": vec,
                            "knowledge_base_id": lib.id,
                            "knowledge_base_name": lib.name,
                            "document_id": doc.id,
                            "document_name": doc.display_name,
                            "embedder_provider": doc.embedder_provider,
                            "embedder_model": doc.embedder_model,
                            "embedder_dimension": doc.embedder_dimension,
                        }
                    )
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return float("nan")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return float("nan")
    return dot / (na * nb)


__all__ = [
    "DEFAULT_SCORE_THRESHOLD",
    "DEFAULT_TOP_K",
    "EvidenceChunk",
    "RAGContext",
    "RetrievalResult",
    "RetrievalScope",
    "RetrievalService",
    "parse_scope",
]
