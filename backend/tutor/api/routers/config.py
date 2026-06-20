"""Configuration HTTP endpoints (Task 6).

Three sections, all masked on read and safely writable on PATCH:

- ``GET  /api/v1/config``                  — masked snapshot of all sections
- ``PATCH /api/v1/config/llm``             — update LLM section
- ``PATCH /api/v1/config/embedding``       — update embedding section
- ``PATCH /api/v1/config/web-search``      — update web search section
- ``POST /api/v1/config/test/llm``         — connection test for LLM
- ``POST /api/v1/config/test/embedding``   — connection test for embedding
- ``POST /api/v1/config/test/web-search``  — connection test for web search

All endpoints go through :class:`RuntimeConfigService` which is the
single boundary that touches the project-root ``.env`` file.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from tutor.services.config.runtime import (
    EmbeddingSectionPatch,
    LLMSectionPatch,
    RuntimeConfigService,
    WebSearchSectionPatch,
)

router = APIRouter()
_service = RuntimeConfigService()


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """Return the masked configuration snapshot."""
    return _service.read()


@router.patch("/config/llm")
async def patch_llm(patch: LLMSectionPatch) -> dict[str, Any]:
    return _service.apply_llm(patch)


@router.patch("/config/embedding")
async def patch_embedding(patch: EmbeddingSectionPatch) -> dict[str, Any]:
    return _service.apply_embedding(patch)


@router.patch("/config/web-search")
async def patch_web_search(patch: WebSearchSectionPatch) -> dict[str, Any]:
    return _service.apply_web_search(patch)


@router.post("/config/test/llm")
async def test_llm() -> dict[str, Any]:
    return _service.test_llm()


@router.post("/config/test/embedding")
async def test_embedding() -> dict[str, Any]:
    return _service.test_embedding()


@router.post("/config/test/web-search")
async def test_web_search() -> dict[str, Any]:
    return _service.test_web_search()


__all__ = ["router"]
