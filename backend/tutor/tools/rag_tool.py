"""RAGTool — knowledge base retrieval tool (2026-06-21 plan).

The old version was a placeholder that always returned empty
chunks. This implementation delegates to
:class:`tutor.services.retrieval.service.RetrievalService` which
does the real work:

  * parses a scope string (``"all"``, ``"course:ID"``,
    ``"library:ID"``) into a concrete set of libraries
  * uses the runtime embedder for the query vector and the
    documents' stored manifest to refuse mixed-provider searches
  * returns Top-K by cosine similarity with a score threshold

The tool surfaces the structured result (``status``,
``error_code``, ``chunks``) so the LLM agent can decide what to
do with a "no evidence" or "stale" response.
"""

from __future__ import annotations

from typing import Any

from tutor.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter, ToolResult
from tutor.services.retrieval import (
    EvidenceChunk,
    RetrievalResult,
    RetrievalService,
    get_retrieval_service,
)


class RAGTool(BaseTool):
    """Retrieve relevant passages from the active knowledge base."""

    name = "rag"
    description = "从知识库中检索与查询相关的文档片段"

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="检索查询字符串",
                    required=True,
                ),
                ToolParameter(
                    name="kb_name",
                    type="string",
                    description=(
                        "知识库 / 课程 / \"all\" 范围。"
                        "支持 library:<lib_id>、course:<course_id>、all。"
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="top_k",
                    type="number",
                    description="返回结果数量（默认 5）",
                    required=False,
                ),
                ToolParameter(
                    name="user_id",
                    type="string",
                    description="用户标识，用于范围权限校验",
                    required=True,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query", "")
        scope = kwargs.get("kb_name") or "all"
        user_id = kwargs.get("user_id") or ""
        top_k = kwargs.get("top_k")
        if top_k is not None:
            try:
                top_k = int(top_k)
            except (TypeError, ValueError):
                top_k = None
        svc: RetrievalService = get_retrieval_service()
        if top_k is not None:
            svc = RetrievalService(top_k=top_k)
        result: RetrievalResult = await svc.retrieve(
            query=query, scope=scope, user_id=user_id
        )
        chunks_payload = [
            {
                "chunk_id": c.chunk_id,
                "text": c.text,
                "score": round(c.score, 4),
                "knowledge_base_id": c.knowledge_base_id,
                "knowledge_base_name": c.knowledge_base_name,
                "document_id": c.document_id,
                "document_name": c.document_name,
                "anchor": c.anchor,
            }
            for c in result.chunks
        ]
        message = self._result_to_message(result)
        return ToolResult(
            success=(result.status == "ok"),
            data={
                "query": query,
                "scope": scope,
                "status": result.status,
                "error_code": result.error_code,
                "error_message": result.error_message,
                "chunks": chunks_payload,
                "message": message,
            },
        )

    @staticmethod
    def _result_to_message(result: RetrievalResult) -> str:
        """One-line human-readable summary the LLM can echo back."""
        if result.status == "ok":
            return f"找到 {len(result.chunks)} 条相关证据"
        if result.status == "no_evidence":
            return "知识库中没有与查询匹配的证据（请基于通用知识回答或提示用户补充上下文）"
        if result.status == "stale":
            return (
                f"知识库索引需要重建（{result.error_code}）；"
                "本次回答不使用 RAG 检索结果"
            )
        # ``status == "error"`` — keep the original error text so the
        # LLM can show the user a precise reason (e.g. unknown scope
        # vs scope-not-ready).
        return f"检索失败: {result.error_message or result.error_code or 'unknown'}"


__all__ = ["RAGTool"]
