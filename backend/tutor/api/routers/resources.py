"""Resource-generation HTTP endpoints (Phase 2 stub).

Real implementation lands in Phase 5 with proper async jobs + persistence.
For Phase 2 the entry point is the WebSocket at ``/api/v1/ws``.

Endpoints:
- ``GET /api/v1/resources/info`` — agent manifest + sample supported types
- ``GET /api/v1/resources/types`` — ResourceType enum values
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from tutor.services.resource_package.schema import ResourceType

router = APIRouter()


@router.get("/resources/info")
async def resources_info() -> dict[str, Any]:
    """Information about the resource generation subsystem."""
    return {
        "name": "resource_generation",
        "version": "0.1.0",
        "supported_types": [t.value for t in ResourceType],
        "entry_point": "WebSocket /api/v1/ws (set capability='resource_generation')",
        "pipeline_stages": [
            "intent_understanding",
            "profile_loading",
            "knowledge_graph_query",
            "resource_planning",
            "content_and_pedagogy",
            "parallel_resource_generation",
            "quality_review",
            "package_assembly",
            "path_integration",
        ],
        "agents": [
            "IntentUnderstandingAgent",
            "ContentExpertAgent",
            "PedagogyAgent",
            "MultimediaAgent",
            "ExerciseGeneratorAgent",
            "ManimVideoAgent",
            "CodeSandboxAgent",
            "QualityReviewerAgent",
        ],
    }


@router.get("/resources/types")
async def resource_types() -> dict[str, Any]:
    """List all supported resource types."""
    return {
        "types": [
            {
                "id": t.value,
                "name": {
                    ResourceType.DOCUMENT: "课程讲解文档",
                    ResourceType.MINDMAP: "知识点思维导图",
                    ResourceType.EXERCISE: "练习题/题库",
                    ResourceType.READING: "拓展阅读材料",
                    ResourceType.VIDEO: "多模态视频/动画",
                    ResourceType.CODE: "代码实操案例",
                    ResourceType.PPT: "PPT 教案",
                }.get(t, t.value),
                "agent": {
                    ResourceType.DOCUMENT: "ContentExpertAgent + PedagogyAgent",
                    ResourceType.MINDMAP: "MultimediaAgent",
                    ResourceType.EXERCISE: "ExerciseGeneratorAgent",
                    ResourceType.READING: "PedagogyAgent (reading mode)",
                    ResourceType.VIDEO: "ManimVideoAgent (two-stage)",
                    ResourceType.CODE: "CodeSandboxAgent",
                    ResourceType.PPT: "(Phase 5)",
                }.get(t, "TBD"),
            }
            for t in ResourceType
        ],
    }


__all__ = ["router"]
