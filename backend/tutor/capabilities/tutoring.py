"""TutoringCapability — instant, multi-modal Q&A tutoring.

Pipeline (5 stages):

    1. question_understanding   — classify + extract concepts
    2. context_retrieval       — RAG search the KB
    3. answer_generation        — 4-layer answer (TutoringAgent)
    4. multi_modal_enrichment   — diagram / code / exercise suggestions
    5. session_recording        — persist to TutorService

Graceful degradation:
- Each stage failure is caught + logged; downstream stages still run.
- If LLM is unavailable, we still emit a structured failure result so
  the frontend can show "tutoring temporarily unavailable".

The capability is wired into the WebSocket via the orchestrator's
keyword router ("问", "为什么", "解释", "不懂", ...) or by explicit
``capability='tutoring'`` from the client.

2026-06-21 plan (D9): the RAG stage now uses the new
:class:`tutor.services.retrieval.service.RetrievalService`
directly. The pre-fix code called
``TutorService.retrieve_context``, which only scanned the prebuilt
Markdown courseware with keyword matching and ignored the
uploaded-document knowledge bases entirely. The new path:

  * reads ``context.metadata['retrieval_scope']`` (set by the
    WebSocket submit handler from the front-end's
    ``retrieval_scope`` field) — ``"all"`` by default
  * embeds the question with the runtime embedder and runs
    cosine top-K + threshold filter on the in-scope libraries
  * returns a structured :class:`RAGContext` with chunks AND
    citations so the LLM agent can cite the source by
    knowledge base / document / anchor
  * on ``no_evidence`` / ``stale`` / ``error`` it surfaces a
    structured message to the stream so the user sees
    "知识库中没有相关证据" rather than a silent fallback

The legacy ``retrieve_context`` method is no longer called here;
it remains in :class:`TutorService` for any third-party code
that imported it directly.
"""

from __future__ import annotations

from typing import Any

from tutor.agents.tutor.multimodal_enrichment import (
    EnrichmentSuggestion,
    MultiModalEnrichmentAgent,
)
from tutor.agents.tutor.question_understanding import (
    QuestionUnderstanding,
    QuestionUnderstandingAgent,
)
from tutor.agents.tutor.tutoring import TutoringAgent, TutoringAnswer
from tutor.capabilities.failure_reporting import log_degraded, report_degraded
from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.capability_result import CapabilityResult
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.learner_profile.builder import (
    ProfileBuilder,
    get_profile_builder,
)
from tutor.services.learning_events.store import (
    LearningEventStore,
    get_learning_event_store,
)
from tutor.services.retrieval import (
    RAGContext,
    RetrievalService,
    get_retrieval_service,
)
from tutor.services.search import SearchExecutor, SearchOutcome
from tutor.services.tutor.service import TutorService, get_tutor_service


class TutoringCapability(BaseCapability):
    """End-to-end intelligent tutoring."""

    manifest = CapabilityManifest(
        name="tutoring",
        description="即时多模态答疑解惑（文字 + 图解 + 例子 + 练习）",
        stages=[
            "question_understanding",
            "context_retrieval",
            "web_search",
            "answer_generation",
            "multi_modal_enrichment",
            "session_recording",
        ],
        tools_used=["rag", "web_search"],
        cli_aliases=["tutor", "ask", "question"],
        tags=["tutoring", "qa"],
    )

    def __init__(
        self,
        *,
        builder: ProfileBuilder | None = None,
        tutor_service: TutorService | None = None,
        question_agent: QuestionUnderstandingAgent | None = None,
        tutoring_agent: TutoringAgent | None = None,
        enrichment_agent: MultiModalEnrichmentAgent | None = None,
        retrieval_service: RetrievalService | None = None,
        search_executor: SearchExecutor | None = None,
        event_store: LearningEventStore | None = None,
    ) -> None:
        super().__init__()
        self.builder = builder
        self._owns_builder = builder is None
        self.tutor_service = tutor_service or get_tutor_service()
        self.question_agent = question_agent or QuestionUnderstandingAgent()
        self.tutoring_agent = tutoring_agent or TutoringAgent()
        self.enrichment_agent = enrichment_agent or MultiModalEnrichmentAgent()
        self.retrieval_service = retrieval_service  # set by tests
        self.search_executor = search_executor
        self.event_store = event_store or get_learning_event_store()

    @property
    def _builder(self) -> ProfileBuilder:
        if self.builder is None:
            self.builder = get_profile_builder()
        return self.builder

    @property
    def _retrieval(self) -> RetrievalService:
        return self.retrieval_service or get_retrieval_service()

    @property
    def _search(self) -> SearchExecutor:
        if self.search_executor is None:
            self.search_executor = SearchExecutor()
        return self.search_executor

    async def _emit_retrieval_observation(
        self, stream: StreamBus, rag: RAGContext | None
    ) -> None:
        """Push a human-readable status line to the stream.

        The 2026-06-21 plan calls for the UI to show *why* a
        retrieval came back empty (no scope, no ready docs, stale
        index) instead of pretending the search succeeded. We
        surface the status to the WS as an observation so the
        chat-surface can render a "知识库没有相关证据" hint when
        ``no_evidence`` fires.
        """
        if rag is None:
            await stream.observation(
                "RAG 未执行 (内部错误)",
                source="tutoring_capability",
                stage="context_retrieval",
            )
            return
        if rag.status == "ok":
            await stream.observation(
                f"已检索 {len(rag.chunks)} 条证据，"
                f"来源 {len({c.knowledge_base_id for c in rag.chunks})} 个知识库",
                source="tutoring_capability",
                stage="context_retrieval",
            )
            return
        if rag.status == "no_evidence":
            await stream.observation(
                "未检索到相关证据，将依赖 LLM 自身知识回答",
                source="tutoring_capability",
                stage="context_retrieval",
            )
            return
        if rag.status == "stale":
            await stream.observation(
                f"知识库索引需要重建 ({rag.error_code})，"
                "本次回答不使用 RAG 检索结果",
                source="tutoring_capability",
                stage="context_retrieval",
            )
            return
        # error
        await stream.observation(
            f"检索失败 ({rag.error_code or 'RETRIEVAL_FAILED'})",
            source="tutoring_capability",
            stage="context_retrieval",
            metadata={"code": rag.error_code or "RETRIEVAL_FAILED"},
        )

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self, context: UnifiedContext, stream: StreamBus) -> CapabilityResult:
        understanding: QuestionUnderstanding | None = None
        answer: TutoringAnswer | None = None
        enrichments: list[EnrichmentSuggestion] = []

        # ------------------------------------------------------------------
        # Stage 1: question understanding
        # ------------------------------------------------------------------
        async with stream.stage("question_understanding", source="tutoring_capability"):
            try:
                understanding = await self.question_agent.process(context, stream=stream)
                context.metadata["tutor_understanding"] = understanding
            except Exception:
                await report_degraded(
                    stream,
                    code="TUTORING_QUESTION_UNDERSTANDING_FAILED",
                    summary="问题理解失败，已使用通用问题类型",
                    source="tutoring_capability",
                    stage="question_understanding",
                )
                understanding = QuestionUnderstanding(
                    question_type=__import__(
                        "tutor.agents.tutor.question_understanding",
                        fromlist=["QuestionType"],
                    ).QuestionType.OTHER,
                    raw_question=context.user_message,
                )

        # ------------------------------------------------------------------
        # Stage 2: context retrieval (RAG)
        # ------------------------------------------------------------------
        # 2026-06-21 plan: the retrieval scope is carried on the
        # context metadata as ``retrieval_scope`` (a string like
        # ``"all"`` / ``"course:ID"`` / ``"library:ID"``). The WS
        # submit handler reads it from the job metadata and sets
        # it here. The pre-fix code used the in-memory
        # ``TutorService.retrieve_context`` which only scanned the
        # prebuilt Markdown — that was the root cause of "RAG is
        # not actually using uploaded documents".
        rag_context: RAGContext | None = None
        search_query = context.user_message
        scope = (context.metadata or {}).get("retrieval_scope") or "all"
        async with stream.stage("context_retrieval", source="tutoring_capability"):
            try:
                enriched_q = context.user_message
                if understanding and understanding.concepts:
                    enriched_q += "\n\n相关概念：" + "、".join(understanding.concepts)
                search_query = enriched_q
                rag_context = await self._retrieval.retrieve(
                    query=enriched_q,
                    scope=scope,
                    user_id=context.user_id,
                )
                await self._emit_retrieval_observation(stream, rag_context)
            except Exception:
                await report_degraded(
                    stream,
                    code="TUTORING_RETRIEVAL_FAILED",
                    summary="RAG 检索失败，本次回答不使用检索结果",
                    source="tutoring_capability",
                    stage="context_retrieval",
                )

        # Serialise to the legacy ``rag_context: str`` field the
        # TutoringAgent expects. The structured ``RAGContext`` is
        # stashed on the context metadata for the resource
        # generation capability to reuse (D9 fix).
        rag_text = ""
        citations: list[dict[str, Any]] = []
        if rag_context is not None and rag_context.chunks:
            rag_text = RAGContext.to_plain_text(rag_context)
            citations = [c.to_dict() for c in rag_context.chunks]
        context.metadata["rag_context"] = rag_text
        context.metadata["rag_citations"] = citations
        context.metadata["rag_status"] = (
            rag_context.status if rag_context else "error"
        )

        web_outcome = SearchOutcome()
        async with stream.stage("web_search", source="tutoring_capability"):
            try:
                web_outcome = await self._search.execute(
                    search_query,
                    conversation_enabled=context.web_search_enabled,
                )
            except Exception:  # noqa: BLE001 - report only stable public details
                web_outcome = SearchOutcome(
                    unavailable=True,
                    degradation_code="WEB_SEARCH_UNAVAILABLE",
                )
            if web_outcome.unavailable:
                await report_degraded(
                    stream,
                    code="WEB_SEARCH_UNAVAILABLE",
                    summary="联网搜索暂不可用，将使用知识库和模型知识继续回答",
                    source="tutoring_capability",
                    stage="web_search",
                )

        web_sources = [source.to_dict() for source in web_outcome.sources]
        web_text = "\n\n".join(
            f"[Web: {source.title}]({source.url})\n{source.excerpt}"
            for source in web_outcome.sources
        )
        answer_context = "\n\n--- Web evidence ---\n\n".join(
            part for part in (rag_text, web_text) if part
        )
        context.metadata["search_used"] = web_outcome.search_used
        context.metadata["web_search_sources"] = web_sources
        context.metadata["answer_context"] = answer_context

        # ------------------------------------------------------------------
        # Stage 3: answer generation
        # ------------------------------------------------------------------
        profile_snapshot: dict[str, Any] = {}
        async with stream.stage("answer_generation", source="tutoring_capability"):
            try:
                profile = await self._builder.get(context.user_id)
                profile_snapshot = dict(profile.to_summary()) if profile else {}
                context.metadata["learner_profile"] = profile
            except Exception:
                await report_degraded(
                    stream,
                    code="TUTORING_PROFILE_LOAD_FAILED",
                    summary="画像加载失败，将使用默认教学策略",
                    source="tutoring_capability",
                    stage="answer_generation",
                )

            profile_snapshot["recent_exercises"] = []
            try:
                profile_snapshot["recent_exercises"] = (
                    await self.event_store.recent_exercise_evidence(
                        context.user_id,
                        limit=10,
                    )
                )
            except Exception:
                await report_degraded(
                    stream,
                    code="TUTORING_EXERCISE_EVIDENCE_LOAD_FAILED",
                    summary="近期练习记录加载失败，将使用画像继续回答",
                    source="tutoring_capability",
                    stage="answer_generation",
                )

            if understanding is not None:
                try:
                    answer = await self.tutoring_agent.process(
                        context,
                        stream=stream,
                        understanding=understanding,
                        rag_context=answer_context,
                        profile=profile_snapshot,
                    )
                    context.metadata["tutor_answer"] = answer
                except Exception:
                    await report_degraded(
                        stream,
                        code="TUTORING_ANSWER_GENERATION_FAILED",
                        summary="答案生成失败，请稍后重试",
                        source="tutoring_capability",
                        stage="answer_generation",
                    )
                    answer = TutoringAnswer(
                        tldr="（暂时无法生成完整解答，请稍后重试）",
                        confidence=0.0,
                    )

        # ------------------------------------------------------------------
        # Stage 4: multi-modal enrichment
        # ------------------------------------------------------------------
        async with stream.stage("multi_modal_enrichment", source="tutoring_capability"):
            if understanding is not None and answer is not None:
                try:
                    enrichments = await self.enrichment_agent.process(
                        context,
                        stream=stream,
                        understanding=understanding,
                        answer=answer,
                    )
                except Exception:
                    await report_degraded(
                        stream,
                        code="TUTORING_ENRICHMENT_FAILED",
                        summary="多模态补充生成失败",
                        source="tutoring_capability",
                        stage="multi_modal_enrichment",
                    )

        # ------------------------------------------------------------------
        # Stage 5: session recording
        # ------------------------------------------------------------------
        async with stream.stage("session_recording", source="tutoring_capability"):
            try:
                if understanding is not None and answer is not None:
                    self.tutor_service.record_interaction(
                        user_id=context.user_id,
                        question=context.user_message,
                        understanding=understanding,
                        answer=answer,
                        enrichments=[s.to_dict() for s in enrichments],
                    )
            except Exception:
                log_degraded(
                    code="TUTORING_SESSION_RECORD_FAILED",
                    source="tutoring_capability",
                    stage="session_recording",
                )

        # ------------------------------------------------------------------
        # Emit final result
        # ------------------------------------------------------------------
        payload = {
                "understanding": (
                    understanding.to_dict() if understanding else {}
                ),
                "answer": answer.to_dict() if answer else {},
                "enrichments": [s.to_dict() for s in enrichments],
                "history_count": len(
                    self.tutor_service.get_history(context.user_id)
                ),
                "next_step": (
                    "follow_up"
                    if (understanding and understanding.follow_up_questions)
                    else "ask_another"
                ),
                "search_used": web_outcome.search_used,
                "sources": web_sources,
            }
        return CapabilityResult(
            assistant_message=(answer.tldr if answer and answer.tldr else "答疑已完成"),
            payload=payload,
        )


__all__ = ["TutoringCapability"]
