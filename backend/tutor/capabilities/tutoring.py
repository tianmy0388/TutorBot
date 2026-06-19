"""TutoringCapability — intelligent tutoring (Q&A). Optional bonus.

Placeholder for Phase 3.
"""

from __future__ import annotations

from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus


class TutoringCapability(BaseCapability):
    """即时多模态答疑解惑。"""

    manifest = CapabilityManifest(
        name="tutoring",
        description="即时多模态答疑解惑（文字 + 图解 + 短视频讲解）",
        stages=["understand_question", "retrieve_context", "draft_answer", "multimodal_enrich"],
        tools_used=["rag", "code_execution", "web_search"],
        cli_aliases=["tutor", "ask", "question"],
        tags=["tutoring", "qa"],
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        async with stream.stage("understand_question", source="tutor_capability"):
            await stream.observation("理解学生问题...", source="tutor_capability")
        async with stream.stage("retrieve_context", source="tutor_capability"):
            await stream.observation("从知识库检索相关内容...", source="tutor_capability")
        async with stream.stage("draft_answer", source="tutor_capability"):
            await stream.observation("起草文字解答...", source="tutor_capability")
        async with stream.stage("multimodal_enrich", source="tutor_capability"):
            await stream.observation("生成图解/短视频补充...", source="tutor_capability")
        await stream.observation("(占位) TutoringCapability 完整实现将在 Phase 3", source="tutor_capability")
        await stream.done(source="tutor_capability")


__all__ = ["TutoringCapability"]
